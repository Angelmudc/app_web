# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import io
import re
from urllib.parse import urlsplit

from flask import current_app, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from flask_login import current_user
from flask_wtf.csrf import generate_csrf
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import defer, load_only

from config_app import db
from decorators import staff_required
from models import Cliente, ContratoDigital, ContratoEvento, Solicitud
from utils.admin_async import payload as shared_admin_async_payload, wants_json as shared_admin_async_wants_json
from utils.audit_logger import log_action
from utils.timezone import utc_now_naive

from . import contratos_bp
from .services import (
    ContractValidationError,
    EDITABLE_STATES,
    REENVIABLE_STATES,
    TokenResolution,
    build_contract_public_context,
    emitir_nuevo_link,
    evento_contrato,
    format_signed_at_rd_human,
    firmar_contrato_atomico,
    registrar_vista_publica,
    resolver_token_publico,
    snapshot_desde_solicitud,
)

_SOLICITUD_DETAIL_NEXT_RE = re.compile(r"^/admin/clientes/(?P<cliente_id>\d+)/solicitudes/(?P<solicitud_id>\d+)/?$")
_CONTRATO_DETAIL_NEXT_RE = re.compile(r"^/admin/contratos/(?P<contrato_id>\d+)/detalle/?$")
_CLIENTE_DETAIL_NEXT_RE = re.compile(r"^/admin/clientes/(?P<cliente_id>\d+)/?$")


def _request_ip() -> str:
    return (
        (request.headers.get("CF-Connecting-IP") or "").strip()
        or (request.headers.get("X-Real-IP") or "").strip()
        or (request.remote_addr or "").strip()
    )[:64]


def _request_ua() -> str:
    return (request.headers.get("User-Agent") or "").strip()[:512]


def _contract_link(token: str) -> str:
    return url_for("contratos.public_contrato", token=token, _external=True)


def _safe_next_url(default_endpoint: str = "contratos.listado_contratos_admin") -> str:
    nxt = (request.form.get("next") or request.args.get("next") or "").strip()
    if nxt.startswith("/") and not nxt.startswith("//"):
        return nxt
    return url_for(default_endpoint)


def _admin_async_wants_json() -> bool:
    # Wrapper local para mantener compatibilidad sin tocar callsites.
    return shared_admin_async_wants_json(request)


def _admin_async_payload(
    *,
    success: bool,
    message: str = "",
    category: str = "info",
    redirect_url: str | None = None,
    replace_html: str | None = None,
    update_target: str | None = None,
    update_targets: list | None = None,
    invalidate_targets: list | None = None,
    errors: list | None = None,
    error_code: str | None = None,
    extra: dict | None = None,
) -> dict:
    # Wrapper local para mantener compatibilidad sin tocar callsites.
    return shared_admin_async_payload(
        success=success,
        message=message,
        category=category,
        redirect_url=redirect_url,
        replace_html=replace_html,
        update_target=update_target,
        update_targets=update_targets,
        invalidate_targets=invalidate_targets,
        errors=errors,
        error_code=error_code,
        extra=extra,
    )


def _is_contract_expired(contract: ContratoDigital | None) -> bool:
    if contract is None:
        return False
    if getattr(contract, "firmado_at", None) is not None:
        return False
    if getattr(contract, "anulado_at", None) is not None:
        return False
    exp_at = getattr(contract, "token_expira_at", None)
    if exp_at is None:
        return False
    return utc_now_naive() > exp_at


def _contract_effective_state(contract: ContratoDigital | None, *, contrato_expirado: bool | None = None) -> str:
    if contract is None:
        return "sin_contrato"
    base = str(getattr(contract, "estado", "") or "").strip().lower()
    if (contrato_expirado is True) or ((contrato_expirado is None) and _is_contract_expired(contract)):
        if base in {"enviado", "visto", "expirado"}:
            return "expirado"
    return base or "sin_contrato"


def _contract_snapshot_summary(snapshot_raw) -> str | None:
    if not isinstance(snapshot_raw, dict):
        return None
    parts = []
    tipo = str(snapshot_raw.get("tipo_servicio") or "").strip()
    modalidad = str(snapshot_raw.get("modalidad_trabajo") or "").strip()
    ciudad_sector = str(snapshot_raw.get("ciudad_sector") or "").strip()
    if tipo:
        parts.append(f"Tipo: {tipo}")
    if modalidad:
        parts.append(f"Modalidad: {modalidad}")
    if ciudad_sector:
        parts.append(f"Zona: {ciudad_sector}")
    if not parts:
        return f"Snapshot con {len(snapshot_raw.keys())} campos."
    return " | ".join(parts)


def _contract_state_badge_class(state: str) -> str:
    st = str(state or "").strip().lower()
    if st == "borrador":
        return "bg-secondary"
    if st in {"enviado", "visto"}:
        return "bg-primary"
    if st == "firmado":
        return "bg-success"
    if st == "expirado":
        return "bg-warning text-dark"
    if st == "anulado":
        return "bg-danger"
    return "bg-light text-dark"


def _contract_ui_error_message(error_code: str) -> str:
    code = str(error_code or "").strip().lower()
    if code == "active_contract_exists":
        return "Ya existe un contrato activo para esta solicitud. Anula o cierra el actual antes de crear otro."
    if code == "another_active_contract_exists":
        return "No se pudo enviar este contrato porque existe otra versión activa para la solicitud."
    if code == "already_signed":
        return "El contrato ya está firmado y no se puede reenviar."
    if code == "contract_annulled":
        return "El contrato está anulado y no se puede reenviar."
    if code == "invalid_state_for_send":
        return "El estado actual del contrato no permite enviarlo o reemitirlo."
    if code == "signed_cannot_be_annulled":
        return "No se puede anular un contrato ya firmado."
    return "No se pudo completar la acción sobre el contrato. Intenta nuevamente."


def _infer_contract_ui_context() -> tuple[str, int | None]:
    nxt = (request.form.get("next") or request.args.get("next") or "").strip()
    normalized = nxt
    try:
        split = urlsplit(nxt)
        normalized = (split.path or "").strip()
    except Exception:
        normalized = nxt.split("#", 1)[0].split("?", 1)[0].strip()
    m_sol = _SOLICITUD_DETAIL_NEXT_RE.match(nxt)
    if not m_sol:
        m_sol = _SOLICITUD_DETAIL_NEXT_RE.match(normalized)
    if m_sol:
        try:
            return "solicitud_detail", int(m_sol.group("solicitud_id"))
        except Exception:
            return "solicitud_detail", None
    m_contrato = _CONTRATO_DETAIL_NEXT_RE.match(nxt)
    if not m_contrato:
        m_contrato = _CONTRATO_DETAIL_NEXT_RE.match(normalized)
    if m_contrato:
        try:
            return "contrato_detail", int(m_contrato.group("contrato_id"))
        except Exception:
            return "contrato_detail", None
    m_cliente = _CLIENTE_DETAIL_NEXT_RE.match(nxt)
    if not m_cliente:
        m_cliente = _CLIENTE_DETAIL_NEXT_RE.match(normalized)
    if m_cliente:
        try:
            return "cliente_detail", int(m_cliente.group("cliente_id"))
        except Exception:
            return "cliente_detail", None
    return "contratos_list", None


def _render_contratos_list_region(*, estado: str, q: str, page: int, per_page: int):
    schema_ready = True
    rows = []
    total = 0
    has_more = False
    try:
        query = _contract_admin_base_query()
        if estado in {"borrador", "enviado", "visto", "firmado", "expirado", "anulado"}:
            query = query.filter(ContratoDigital.estado == estado)
        if q:
            if q.isdigit():
                query = query.filter(
                    (ContratoDigital.id == int(q))
                    | (ContratoDigital.solicitud_id == int(q))
                    | (ContratoDigital.cliente_id == int(q))
                )
            else:
                query = query.filter(ContratoDigital.estado.ilike(f"%{q}%"))
        total = query.count()
        rows = (
            query.order_by(ContratoDigital.created_at.desc(), ContratoDigital.id.desc())
            .offset((page - 1) * per_page)
            .limit(per_page + 1)
            .all()
        )
        has_more = len(rows) > per_page
        if has_more:
            rows = rows[:per_page]
    except OperationalError as exc:
        db.session.rollback()
        if _is_missing_contract_table_error(exc):
            schema_ready = False
        else:
            raise
    html = render_template(
        "admin/_contratos_list_results.html",
        contratos=rows,
        estado=estado,
        q=q,
        page=page,
        per_page=per_page,
        total=total,
        has_more=has_more,
        schema_ready=schema_ready,
        now=utc_now_naive(),
    )
    return html, total, has_more, schema_ready


def _render_contrato_detail_region(contrato_id: int) -> str:
    contrato = _contract_admin_base_query().filter_by(id=contrato_id).first_or_404()
    contrato_expirado = _is_contract_expired(contrato)
    contrato_effective_state = _contract_effective_state(contrato, contrato_expirado=contrato_expirado)
    eventos = (
        ContratoEvento.query
        .options(
            load_only(
                ContratoEvento.id,
                ContratoEvento.contrato_id,
                ContratoEvento.evento_tipo,
                ContratoEvento.estado_anterior,
                ContratoEvento.estado_nuevo,
                ContratoEvento.actor_tipo,
                ContratoEvento.actor_staff_id,
                ContratoEvento.success,
                ContratoEvento.error_code,
                ContratoEvento.metadata_json,
                ContratoEvento.created_at,
            )
        )
        .filter_by(contrato_id=contrato_id)
        .order_by(ContratoEvento.created_at.desc(), ContratoEvento.id.desc())
        .limit(50)
        .all()
    )
    session_links = session.get("contract_links")
    last_link = session_links.get(str(contrato_id)) if isinstance(session_links, dict) else None
    return render_template(
        "admin/_contratos_detail_region.html",
        contrato=contrato,
        contrato_effective_state=contrato_effective_state,
        contrato_state_badge_class=_contract_state_badge_class(contrato_effective_state),
        eventos=eventos,
        last_link=last_link,
        now=utc_now_naive(),
    )


def _render_solicitud_contract_region(solicitud_id: int) -> str:
    solicitud = Solicitud.query.get_or_404(solicitud_id)
    contract_rows = (
        _contract_admin_base_query()
        .filter_by(solicitud_id=solicitud.id)
        .order_by(ContratoDigital.version.desc(), ContratoDigital.id.desc())
        .all()
    )
    latest_contract = contract_rows[0] if contract_rows else None
    latest_signed_contract = next(
        (
            row for row in contract_rows
            if (row.firmado_at is not None) or (str(row.estado or "").strip().lower() == "firmado")
        ),
        None,
    )
    links = session.get("contract_links")
    links = links if isinstance(links, dict) else {}
    contract_history = []
    for idx, row in enumerate(contract_rows):
        is_expired = _is_contract_expired(row)
        effective_state = _contract_effective_state(row, contrato_expirado=is_expired)
        contract_history.append({
            "contract": row,
            "effective_state": effective_state,
            "is_expired": is_expired,
            "has_pdf": bool(getattr(row, "pdf_final_size_bytes", 0)),
            "is_current": idx == 0,
            "is_latest_signed": bool(latest_signed_contract and latest_signed_contract.id == row.id),
            "is_active": effective_state in {"borrador", "enviado", "visto"},
            "session_link": links.get(str(row.id), ""),
        })

    contrato_expirado = _is_contract_expired(latest_contract)
    latest_contract_link = links.get(str(latest_contract.id)) if latest_contract is not None else None
    return render_template(
        "admin/_solicitud_contract_block.html",
        solicitud=solicitud,
        latest_contract=latest_contract,
        contract_history=contract_history,
        contracts_schema_ready=True,
        contrato_expirado=contrato_expirado,
        contract_effective_state=_contract_effective_state(latest_contract, contrato_expirado=contrato_expirado),
        contract_snapshot_summary=_contract_snapshot_summary(
            getattr(latest_contract, "contenido_snapshot_json", None) if latest_contract else None
        ),
        latest_contract_link=latest_contract_link,
    )


def _contract_ui_async_response(
    *,
    ok: bool,
    message: str,
    category: str,
    contrato_id: int | None = None,
    solicitud_id: int | None = None,
    http_status: int = 200,
    error_code: str | None = None,
):
    context_name, inferred_solicitud_id = _infer_contract_ui_context()
    replace_html = None
    update_target = None
    safe_next = _safe_next_url(default_endpoint="contratos.listado_contratos_admin")

    if context_name == "solicitud_detail" and (solicitud_id or inferred_solicitud_id):
        sid = int(solicitud_id or inferred_solicitud_id or 0)
        replace_html = _render_solicitud_contract_region(sid)
        update_target = "#solicitudContratoAsyncRegion"
    elif context_name == "contrato_detail" and contrato_id:
        replace_html = _render_contrato_detail_region(int(contrato_id))
        update_target = "#contratoDetailAsyncRegion"
    elif context_name == "cliente_detail":
        return jsonify(_admin_async_payload(
            success=ok,
            message=message,
            category=category,
            redirect_url=safe_next,
            update_target="#clienteSolicitudesAsyncRegion",
            error_code=error_code,
        )), http_status
    else:
        estado = (request.args.get("estado") or request.form.get("estado") or "").strip().lower()
        q = (request.args.get("q") or request.form.get("q") or "").strip()
        page = max(1, int(request.args.get("page") or request.form.get("page") or 1))
        per_page = min(100, max(10, int(request.args.get("per_page") or request.form.get("per_page") or 25)))
        replace_html, total, has_more, _schema_ready = _render_contratos_list_region(
            estado=estado,
            q=q,
            page=page,
            per_page=per_page,
        )
        update_target = "#contratosAsyncRegion"
        return jsonify(_admin_async_payload(
            success=ok,
            message=message,
            category=category,
            replace_html=replace_html,
            update_target=update_target,
            error_code=error_code,
            extra={"page": page, "per_page": per_page, "total": total, "has_more": has_more},
        )), http_status

    return jsonify(_admin_async_payload(
        success=ok,
        message=message,
        category=category,
        replace_html=replace_html,
        update_target=update_target,
        error_code=error_code,
    )), http_status


def _contract_admin_base_query():
    return (
        ContratoDigital.query.options(
            load_only(
                ContratoDigital.id,
                ContratoDigital.solicitud_id,
                ContratoDigital.cliente_id,
                ContratoDigital.version,
                ContratoDigital.estado,
                ContratoDigital.contenido_snapshot_json,
                ContratoDigital.snapshot_fijado_at,
                ContratoDigital.token_version,
                ContratoDigital.token_expira_at,
                ContratoDigital.token_generado_at,
                ContratoDigital.enviado_at,
                ContratoDigital.primer_visto_at,
                ContratoDigital.ultimo_visto_at,
                ContratoDigital.firmado_at,
                ContratoDigital.firma_nombre,
                ContratoDigital.pdf_final_size_bytes,
                ContratoDigital.pdf_generado_at,
                ContratoDigital.pdf_final_sha256,
                ContratoDigital.anulado_at,
                ContratoDigital.anulado_motivo,
                ContratoDigital.created_at,
                ContratoDigital.updated_at,
            ),
            defer(ContratoDigital.firma_png),
            defer(ContratoDigital.pdf_final_bytea),
        )
    )


def _locked_contrato_for_signing_query(contrato_id: int):
    return (
        ContratoDigital.query
        .enable_eagerloads(False)
        .filter(ContratoDigital.id == int(contrato_id))
        .with_for_update(of=ContratoDigital)
    )


def _is_missing_contract_table_error(exc: OperationalError) -> bool:
    text = str(getattr(exc, "orig", exc) or "").lower()
    has_table_name = ("contratos_digitales" in text) or ("contratos_eventos" in text)
    has_missing_hint = ("no such table" in text) or ("does not exist" in text) or ("undefined table" in text)
    return has_table_name and has_missing_hint


def _create_or_refresh_draft_for_solicitud(solicitud: Solicitud, *, actor_staff_id: int | None):
    latest = (
        _contract_admin_base_query()
        .filter_by(solicitud_id=solicitud.id)
        .order_by(ContratoDigital.version.desc(), ContratoDigital.id.desc())
        .first()
    )
    now = utc_now_naive()

    active_candidate = (
        _contract_admin_base_query()
        .filter_by(solicitud_id=solicitud.id)
        .filter(ContratoDigital.estado.in_(("borrador", "enviado", "visto")))
        .order_by(ContratoDigital.version.desc(), ContratoDigital.id.desc())
        .first()
    )
    if active_candidate is not None:
        is_not_expired = (active_candidate.token_expira_at is None) or (now <= active_candidate.token_expira_at)
        if (
            active_candidate.firmado_at is None
            and active_candidate.anulado_at is None
            and (
                str(active_candidate.estado or "") == "borrador"
                or (str(active_candidate.estado or "") in {"enviado", "visto"} and is_not_expired)
            )
        ):
            return active_candidate, False, "active_contract_exists"

    next_version = int((latest.version if latest else 0) or 0) + 1
    nuevo = ContratoDigital(
        solicitud_id=solicitud.id,
        cliente_id=solicitud.cliente_id,
        version=next_version,
        contrato_padre_id=int(latest.id) if latest else None,
        estado="borrador",
        contenido_snapshot_json=snapshot_desde_solicitud(solicitud),
        created_at=now,
        updated_at=now,
    )
    db.session.add(nuevo)
    db.session.flush()
    evento_contrato(
        nuevo,
        evento_tipo="CONTRATO_CREADO",
        actor_tipo="staff",
        actor_staff_id=actor_staff_id,
        estado_anterior=None,
        estado_nuevo="borrador",
        metadata={"solicitud_id": int(solicitud.id)},
    )
    db.session.commit()
    return nuevo, True, ""


def _send_or_reissue_contract(contrato: ContratoDigital, *, actor_staff_id: int | None) -> dict:
    now = utc_now_naive()
    old_state = str(contrato.estado or "")
    if old_state == "firmado":
        return {"ok": False, "error": "already_signed"}
    if old_state == "anulado" or contrato.anulado_at is not None:
        return {"ok": False, "error": "contract_annulled"}

    if old_state == "borrador" and contrato.snapshot_fijado_at is None:
        contrato.snapshot_fijado_at = now
    elif old_state not in REENVIABLE_STATES:
        return {"ok": False, "error": "invalid_state_for_send"}

    other_active = (
        _contract_admin_base_query()
        .filter_by(solicitud_id=contrato.solicitud_id)
        .filter(ContratoDigital.id != contrato.id)
        .filter(ContratoDigital.estado.in_(("borrador", "enviado", "visto")))
        .order_by(ContratoDigital.version.desc(), ContratoDigital.id.desc())
        .first()
    )
    if other_active is not None:
        is_not_expired = (other_active.token_expira_at is None) or (now <= other_active.token_expira_at)
        if (
            other_active.firmado_at is None
            and other_active.anulado_at is None
            and (
                str(other_active.estado or "") == "borrador"
                or (str(other_active.estado or "") in {"enviado", "visto"} and is_not_expired)
            )
        ):
            return {
                "ok": False,
                "error": "another_active_contract_exists",
                "active_contract_id": int(other_active.id),
            }

    token = emitir_nuevo_link(contrato)
    contrato.estado = "enviado"
    contrato.enviado_at = now
    contrato.updated_at = now

    evento_contrato(
        contrato,
        evento_tipo="CONTRATO_REEMITIDO" if old_state in REENVIABLE_STATES else "CONTRATO_ENVIADO",
        actor_tipo="staff",
        actor_staff_id=actor_staff_id,
        estado_anterior=old_state,
        estado_nuevo="enviado",
        metadata={"token_version": int(contrato.token_version)},
    )
    db.session.commit()

    log_action(
        action_type="CONTRATO_SEND",
        entity_type="ContratoDigital",
        entity_id=contrato.id,
        summary=f"Contrato {contrato.id} enviado",
        metadata={"solicitud_id": contrato.solicitud_id, "estado_anterior": old_state, "estado_nuevo": "enviado"},
        success=True,
    )
    return {"ok": True, "contrato": _contract_payload(contrato), "link": _contract_link(token)}


def _contract_payload(c: ContratoDigital, *, include_pdf: bool = False) -> dict:
    payload = {
        "id": int(c.id),
        "solicitud_id": int(c.solicitud_id),
        "cliente_id": int(c.cliente_id),
        "version": int(c.version or 1),
        "estado": str(c.estado or ""),
        "snapshot_fijado_at": c.snapshot_fijado_at.isoformat() if c.snapshot_fijado_at else None,
        "token_version": int(c.token_version or 0),
        "token_expira_at": c.token_expira_at.isoformat() if c.token_expira_at else None,
        "firmado_at": c.firmado_at.isoformat() if c.firmado_at else None,
        "anulado_at": c.anulado_at.isoformat() if c.anulado_at else None,
        "pdf_size": int(c.pdf_final_size_bytes or 0),
    }
    if include_pdf:
        payload["pdf_sha256"] = c.pdf_final_sha256
    return payload


@contratos_bp.route("/admin/contratos", methods=["GET"])
@staff_required
def listado_contratos_admin():
    estado = (request.args.get("estado") or "").strip().lower()
    q = (request.args.get("q") or "").strip()
    page = max(1, int(request.args.get("page", 1) or 1))
    per_page = min(100, max(10, int(request.args.get("per_page", 25) or 25)))
    region_html, total, has_more, schema_ready = _render_contratos_list_region(
        estado=estado,
        q=q,
        page=page,
        per_page=per_page,
    )

    if _admin_async_wants_json():
        return jsonify(_admin_async_payload(
            success=True,
            message="Listado actualizado.",
            category="info",
            replace_html=region_html,
            update_target="#contratosAsyncRegion",
            extra={
                "page": page,
                "per_page": per_page,
                "total": total,
                "query": q,
                "estado": estado,
                "has_more": has_more,
            },
        )), 200

    return render_template(
        "admin/contratos_list.html",
        contratos=[],
        estado=estado,
        q=q,
        page=page,
        per_page=per_page,
        total=total,
        has_more=has_more,
        schema_ready=schema_ready,
        list_region_html=region_html,
        now=utc_now_naive(),
    )


@contratos_bp.route("/admin/contratos/<int:contrato_id>/detalle", methods=["GET"])
@staff_required
def detalle_contrato_admin_view(contrato_id: int):
    contrato = _contract_admin_base_query().filter_by(id=contrato_id).first_or_404()
    contrato_expirado = _is_contract_expired(contrato)
    contrato_effective_state = _contract_effective_state(contrato, contrato_expirado=contrato_expirado)
    eventos = (
        ContratoEvento.query
        .options(
            load_only(
                ContratoEvento.id,
                ContratoEvento.contrato_id,
                ContratoEvento.evento_tipo,
                ContratoEvento.estado_anterior,
                ContratoEvento.estado_nuevo,
                ContratoEvento.actor_tipo,
                ContratoEvento.actor_staff_id,
                ContratoEvento.success,
                ContratoEvento.error_code,
                ContratoEvento.metadata_json,
                ContratoEvento.created_at,
            )
        )
        .filter_by(contrato_id=contrato_id)
        .order_by(ContratoEvento.created_at.desc(), ContratoEvento.id.desc())
        .limit(50)
        .all()
    )
    session_links = session.get("contract_links")
    last_link = session_links.get(str(contrato_id)) if isinstance(session_links, dict) else None
    return render_template(
        "admin/contratos_detail.html",
        contrato=contrato,
        contrato_effective_state=contrato_effective_state,
        contrato_state_badge_class=_contract_state_badge_class(contrato_effective_state),
        eventos=eventos,
        last_link=last_link,
        now=utc_now_naive(),
    )


@contratos_bp.route("/admin/contratos/solicitudes/<int:solicitud_id>/borrador", methods=["POST"])
@staff_required
def crear_o_refrescar_borrador(solicitud_id: int):
    solicitud = Solicitud.query.get_or_404(solicitud_id)
    _ = Cliente.query.get_or_404(solicitud.cliente_id)
    actor_staff_id = int(getattr(current_user, "id", 0) or 0) or None
    contrato, created, error = _create_or_refresh_draft_for_solicitud(solicitud, actor_staff_id=actor_staff_id)
    if error == "active_contract_exists":
        return jsonify({
            "ok": False,
            "error": error,
            "active_contract_id": int(contrato.id),
            "contrato": _contract_payload(contrato),
        }), 409
    return jsonify({"ok": True, "created": created, "contrato": _contract_payload(contrato)}), (201 if created else 200)


@contratos_bp.route("/admin/contratos/solicitudes/<int:solicitud_id>/borrador/ui", methods=["POST"])
@staff_required
def crear_o_refrescar_borrador_ui(solicitud_id: int):
    solicitud = Solicitud.query.get_or_404(solicitud_id)
    actor_staff_id = int(getattr(current_user, "id", 0) or 0) or None
    contrato, created, error = _create_or_refresh_draft_for_solicitud(solicitud, actor_staff_id=actor_staff_id)
    if _admin_async_wants_json():
        if error == "active_contract_exists":
            return _contract_ui_async_response(
                ok=False,
                message=_contract_ui_error_message(error),
                category="warning",
                solicitud_id=solicitud.id,
                contrato_id=int(contrato.id),
                http_status=200,
                error_code="active_contract_exists",
            )
        return _contract_ui_async_response(
            ok=True,
            message="Nueva versión de contrato borrador creada." if created else "Contrato actualizado.",
            category="success",
            solicitud_id=solicitud.id,
            contrato_id=int(contrato.id),
        )
    if error == "active_contract_exists":
        flash(
            f"No se puede crear un nuevo contrato mientras exista uno activo (#{int(contrato.id)}). "
            "Anula o cierra el activo primero.",
            "warning",
        )
    else:
        flash("Nueva versión de contrato borrador creada." if created else "Contrato actualizado.", "success")
    return redirect(_safe_next_url(default_endpoint="contratos.listado_contratos_admin"))


@contratos_bp.route("/admin/contratos/<int:contrato_id>/enviar", methods=["POST"])
@staff_required
def enviar_o_reemitir_contrato(contrato_id: int):
    contrato = ContratoDigital.query.get_or_404(contrato_id)
    actor_staff_id = int(getattr(current_user, "id", 0) or 0) or None
    result = _send_or_reissue_contract(contrato, actor_staff_id=actor_staff_id)
    if not result.get("ok"):
        return jsonify(result), 409
    return jsonify(result), 200


@contratos_bp.route("/admin/contratos/<int:contrato_id>/enviar/ui", methods=["POST"])
@staff_required
def enviar_o_reemitir_contrato_ui(contrato_id: int):
    contrato = ContratoDigital.query.get_or_404(contrato_id)
    actor_staff_id = int(getattr(current_user, "id", 0) or 0) or None
    result = _send_or_reissue_contract(contrato, actor_staff_id=actor_staff_id)
    if _admin_async_wants_json():
        if not result.get("ok"):
            error_code = str(result.get("error") or "conflict")
            return _contract_ui_async_response(
                ok=False,
                message=_contract_ui_error_message(error_code),
                category="danger",
                contrato_id=int(contrato.id),
                solicitud_id=int(contrato.solicitud_id),
                http_status=200,
                error_code=error_code,
            )
        session_links = session.get("contract_links")
        if not isinstance(session_links, dict):
            session_links = {}
        session_links[str(contrato.id)] = result["link"]
        session["contract_links"] = session_links
        session.modified = True
        context_name, _ = _infer_contract_ui_context()
        success_message = "Link del contrato generado."
        if context_name == "solicitud_detail":
            success_message = "Link del contrato generado. Puedes copiarlo en este bloque."
        elif context_name == "contrato_detail":
            success_message = "Link del contrato generado. Puedes copiarlo en este detalle."
        return _contract_ui_async_response(
            ok=True,
            message=success_message,
            category="success",
            contrato_id=int(contrato.id),
            solicitud_id=int(contrato.solicitud_id),
        )
    if not result.get("ok"):
        flash(_contract_ui_error_message(str(result.get("error") or "conflict")), "danger")
    else:
        session_links = session.get("contract_links")
        if not isinstance(session_links, dict):
            session_links = {}
        session_links[str(contrato.id)] = result["link"]
        session["contract_links"] = session_links
        session.modified = True
        flash("Link del contrato generado. Puedes copiarlo desde el detalle.", "success")
    return redirect(_safe_next_url(default_endpoint="contratos.listado_contratos_admin"))


@contratos_bp.route("/admin/contratos/<int:contrato_id>/anular", methods=["POST"])
@staff_required
def anular_contrato(contrato_id: int):
    contrato = ContratoDigital.query.get_or_404(contrato_id)
    if str(contrato.estado or "") == "firmado":
        return jsonify({"ok": False, "error": "signed_cannot_be_annulled"}), 409

    motivo = ""
    if request.is_json:
        body = request.get_json(silent=True) or {}
        motivo = str(body.get("motivo") or "")
    else:
        motivo = str(request.form.get("motivo") or "")
    motivo = (motivo or "").strip()[:255]
    if not motivo:
        motivo = "Anulado por staff"

    old_state = str(contrato.estado or "")
    now = utc_now_naive()
    contrato.estado = "anulado"
    contrato.anulado_at = now
    contrato.anulado_motivo = motivo
    contrato.anulado_por_staff_id = int(getattr(current_user, "id", 0) or 0) or None
    contrato.updated_at = now

    evento_contrato(
        contrato,
        evento_tipo="CONTRATO_ANULADO",
        actor_tipo="staff",
        actor_staff_id=contrato.anulado_por_staff_id,
        estado_anterior=old_state,
        estado_nuevo="anulado",
        metadata={"motivo": motivo},
    )
    db.session.commit()
    return jsonify({"ok": True, "contrato": _contract_payload(contrato)}), 200


@contratos_bp.route("/admin/contratos/<int:contrato_id>/anular/ui", methods=["POST"])
@staff_required
def anular_contrato_ui(contrato_id: int):
    contrato = ContratoDigital.query.get_or_404(contrato_id)
    if str(contrato.estado or "") == "firmado":
        if _admin_async_wants_json():
            return _contract_ui_async_response(
                ok=False,
                message=_contract_ui_error_message("signed_cannot_be_annulled"),
                category="danger",
                contrato_id=int(contrato.id),
                solicitud_id=int(contrato.solicitud_id),
                http_status=200,
                error_code="signed_cannot_be_annulled",
            )
        flash("No se puede anular un contrato ya firmado.", "danger")
        return redirect(_safe_next_url(default_endpoint="contratos.listado_contratos_admin"))

    motivo = (request.form.get("motivo") or "Anulado por staff").strip()[:255]
    if not motivo:
        motivo = "Anulado por staff"
    old_state = str(contrato.estado or "")
    now = utc_now_naive()
    contrato.estado = "anulado"
    contrato.anulado_at = now
    contrato.anulado_motivo = motivo
    contrato.anulado_por_staff_id = int(getattr(current_user, "id", 0) or 0) or None
    contrato.updated_at = now

    evento_contrato(
        contrato,
        evento_tipo="CONTRATO_ANULADO",
        actor_tipo="staff",
        actor_staff_id=contrato.anulado_por_staff_id,
        estado_anterior=old_state,
        estado_nuevo="anulado",
        metadata={"motivo": motivo},
    )
    db.session.commit()
    if _admin_async_wants_json():
        return _contract_ui_async_response(
            ok=True,
            message="Contrato anulado correctamente.",
            category="success",
            contrato_id=int(contrato.id),
            solicitud_id=int(contrato.solicitud_id),
        )
    flash("Contrato anulado correctamente.", "success")
    return redirect(_safe_next_url(default_endpoint="contratos.listado_contratos_admin"))


@contratos_bp.route("/admin/contratos/<int:contrato_id>", methods=["GET"])
@staff_required
def detalle_contrato_admin(contrato_id: int):
    contrato = ContratoDigital.query.get_or_404(contrato_id)
    return jsonify({"ok": True, "contrato": _contract_payload(contrato, include_pdf=True)}), 200


@contratos_bp.route("/admin/contratos/<int:contrato_id>/pdf", methods=["GET"])
@staff_required
def descargar_pdf_admin(contrato_id: int):
    contrato = ContratoDigital.query.get_or_404(contrato_id)
    if contrato.firmado_at is None or not contrato.pdf_final_bytea:
        return jsonify({"ok": False, "error": "pdf_not_available"}), 404

    filename = f"contrato_{contrato.id}_v{contrato.version}.pdf"
    data = bytes(contrato.pdf_final_bytea)
    return send_file(
        io.BytesIO(data),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


def _render_public_token_error(status: int, reason: str) -> tuple[str, int]:
    html = render_template("contratos/public_invalid.html", reason_key=reason)
    return html, status


@contratos_bp.route("/contratos/f/<token>", methods=["GET"])
def public_contrato(token: str):
    resolved: TokenResolution = resolver_token_publico(token)
    if not resolved.ok:
        if resolved.reason in {"expired", "expired_signature"}:
            return render_template("contratos/public_expired.html", reason_key=resolved.reason), 410
        if resolved.reason == "contract_annulled":
            return render_template("contratos/public_invalid.html", reason_key="annulled"), 410
        return _render_public_token_error(400, resolved.reason or "invalid")

    contrato = resolved.contrato
    assert contrato is not None
    contract_ctx = build_contract_public_context(contrato)

    if contrato.firmado_at is not None or str(contrato.estado or "") == "firmado":
        signature_data_url = ""
        firma_png = getattr(contrato, "firma_png", None)
        if firma_png:
            signature_data_url = "data:image/png;base64," + base64.b64encode(bytes(firma_png)).decode("ascii")
        evento_contrato(
            contrato,
            evento_tipo="ACCESO_SOLO_LECTURA",
            actor_tipo="cliente_publico",
            estado_anterior="firmado",
            estado_nuevo="firmado",
            ip=_request_ip(),
            user_agent=_request_ua(),
            metadata={"source": "public_get"},
        )
        db.session.commit()
        return render_template(
            "contratos/public_readonly.html",
            contrato=contrato,
            token=token,
            contract_ctx=contract_ctx,
            signature_data_url=signature_data_url,
            signed_at_local_text=format_signed_at_rd_human(getattr(contrato, "firmado_at", None)),
        ), 200

    if str(contrato.estado or "") not in EDITABLE_STATES:
        return _render_public_token_error(409, "invalid_state")

    registrar_vista_publica(contrato, ip=_request_ip(), user_agent=_request_ua())
    return render_template(
        "contratos/public_sign.html",
        contrato=contrato,
        contract_ctx=contract_ctx,
        token=token,
        csrf_token_value=generate_csrf(),
    ), 200


@contratos_bp.route("/contratos/f/<token>/firmar", methods=["POST"])
def public_firmar_contrato(token: str):
    resolved: TokenResolution = resolver_token_publico(token)
    if not resolved.ok:
        if resolved.reason in {"expired", "expired_signature"}:
            return render_template("contratos/public_expired.html", reason_key=resolved.reason), 410
        if resolved.reason == "contract_annulled":
            return render_template("contratos/public_invalid.html", reason_key="annulled"), 410
        return _render_public_token_error(400, resolved.reason or "invalid")

    contrato_ref = resolved.contrato
    assert contrato_ref is not None
    contrato = _locked_contrato_for_signing_query(int(contrato_ref.id)).first()
    if contrato is None:
        return _render_public_token_error(404, "contract_not_found")

    signature_data_url = (request.form.get("signature_data") or "").strip()
    signer_name = (request.form.get("signer_name") or "").strip()

    try:
        firmar_contrato_atomico(
            contrato,
            signature_data_url=signature_data_url,
            signer_name=signer_name,
            ip=_request_ip(),
            user_agent=_request_ua(),
        )
    except ContractValidationError as exc:
        reason = str(exc) or "signature_invalid"
        if reason == "already_signed":
            return redirect(url_for("contratos.public_contrato", token=token, estado="firmado"))
        if reason in {"invalid_state_for_sign", "contract_annulled"}:
            return _render_public_token_error(409, reason)
        return render_template(
            "contratos/public_sign.html",
            contrato=contrato,
            contract_ctx=build_contract_public_context(contrato),
            token=token,
            csrf_token_value=generate_csrf(),
            error_key=reason,
        ), 400
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Error inesperado firmando contrato digital",
            extra={"contract_id": int(contrato.id), "token_prefix": (token or "")[:8]},
        )
        return render_template(
            "contratos/public_sign.html",
            contrato=contrato,
            contract_ctx=build_contract_public_context(contrato),
            token=token,
            csrf_token_value=generate_csrf(),
            error_key="signature_failed_retry",
            error_message="No se pudo completar la firma en este momento. Intenta nuevamente.",
        ), 503

    return redirect(url_for("contratos.public_contrato", token=token, estado="firmado"))


@contratos_bp.route("/contratos/f/<token>/pdf", methods=["GET"])
def public_descargar_pdf(token: str):
    resolved: TokenResolution = resolver_token_publico(token)
    if not resolved.ok:
        return _render_public_token_error(400, resolved.reason or "invalid")

    contrato = resolved.contrato
    assert contrato is not None
    if contrato.firmado_at is None or not contrato.pdf_final_bytea:
        return _render_public_token_error(404, "pdf_not_available")

    evento_contrato(
        contrato,
        evento_tipo="PDF_DESCARGADO",
        actor_tipo="cliente_publico",
        estado_anterior="firmado",
        estado_nuevo="firmado",
        ip=_request_ip(),
        user_agent=_request_ua(),
    )
    db.session.commit()

    filename = f"contrato_{contrato.id}_v{contrato.version}.pdf"
    data = bytes(contrato.pdf_final_bytea)
    return send_file(
        io.BytesIO(data),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )
