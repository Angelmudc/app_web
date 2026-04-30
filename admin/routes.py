# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import os
import time
import json
import hashlib
import secrets
import ipaddress
from types import SimpleNamespace
from contextlib import contextmanager
from urllib.parse import parse_qs, urlparse
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation

from flask import render_template, redirect, url_for, flash, request, jsonify, abort, session, current_app, Response, stream_with_context, make_response, has_request_context
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash

from sqlalchemy import and_, or_, func, cast, desc, case, inspect as sa_inspect, Table, MetaData, select as sa_select, event
from sqlalchemy.sql import table as sa_table, column as sa_column
from sqlalchemy.engine import Engine
from sqlalchemy.types import Numeric
from sqlalchemy.orm import joinedload, load_only, selectinload  # ➜ para evitar N+1 en copiar_solicitudes
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm.exc import StaleDataError

from functools import wraps  # si otros decoradores locales lo usan

from config_app import db, cache, csrf
from models import (
    Cliente,
    Solicitud,
    Candidata,
    Reemplazo,
    TareaCliente,
    StaffUser,
    SolicitudCandidata,
    ClienteNotificacion,
    ContratoDigital,
    Entrevista,
    StaffAuditLog,
    StaffPresenceState,
    TrustedDevice,
    PublicSolicitudTokenUso,
    PublicSolicitudClienteNuevoTokenUso,
    SolicitudRecommendationRun,
    SolicitudRecommendationItem,
    SolicitudRecommendationSelection,
    RequestIdempotencyKey,
    DomainOutbox,
    SeguimientoCandidataCaso,
    SeguimientoCandidataContacto,
    SeguimientoCandidataEvento,
)
try:
    from models import ChatConversation, ChatMessage
except Exception:
    # Mantiene el arranque aunque el modelo de chat no esté disponible en el build actual.
    ChatConversation = None
    ChatMessage = None
from admin.forms import (
    StaffUserCreateForm,
    StaffUserEditForm,
    AdminClienteForm,
    AdminSolicitudForm,
    AdminPagoForm,
    AdminReemplazoForm,
    AdminGestionPlanForm,
    AdminReemplazoFinForm,  # 🔹 NUEVO FORM PARA FINALIZAR REEMPLAZO
)
from utils.codigo_solicitud import compose_codigo_solicitud
from utils.compat_engine import compute_match, format_compat_result
from utils.guards import (
    assert_candidata_no_descalificada,
    candidata_esta_descalificada,
    candidatas_activas_filter,
)
from utils.candidata_readiness import candidata_is_ready_to_send
from utils.candidata_completitud_audit import (
    entrevista_ok,
    binario_ok,
    referencias_ok,
    candidata_tiene_codigo_valido,
    faltantes_desde_flags,
    es_incompleta,
    solo_criticos,
    solo_sin_documentos,
    solo_sin_referencias,
)
from utils.matching_service import rank_candidates
from utils.funciones_formatter import format_funciones
from utils.audit_labels import (
    humanize_audit_field,
    humanize_audit_value,
    humanize_change,
    summarize_changed_fields,
)
from utils.staff_auth import (
    breakglass_allowed_ip,
    get_request_ip,
    breakglass_username,
    build_breakglass_user,
    check_breakglass_password,
    is_breakglass_enabled,
    log_breakglass_attempt,
    set_breakglass_session,
    is_breakglass_user_obj,
    is_breakglass_session_valid,
    clear_breakglass_session,
)
from utils.audit_logger import log_action, log_admin_action, log_auth_event
from utils.business_guard import enforce_business_limit, enforce_min_human_interval
from utils.distributed_backplane import bp_get, bp_healthcheck, bp_set
from utils.enterprise_layer import (
    touch_staff_session,
    list_active_sessions,
    close_user_sessions,
    lock_ping,
    lock_takeover,
    list_active_locks,
    get_alert_items,
    resolve_alert,
    health_payload,
    metrics_dashboard,
    metrics_secretarias,
    metrics_solicitudes,
    intelligent_suggestions_for_solicitud,
    register_decision_feedback,
    log_error_event,
    emit_critical_alert,
    emit_warning_alert,
    telegram_channel_config,
    save_telegram_channel_config,
    send_telegram_test_message,
    operational_semaphore_payload,
    operational_trends_payload,
    ingest_live_observability_event,
    bump_operational_counter,
)
from utils.client_contact_norm import (
    norm_email as _shared_norm_email,
    norm_phone_rd as _shared_norm_phone_rd,
    nullable_norm_email as _shared_nullable_norm_email,
    nullable_norm_phone_rd as _shared_nullable_norm_phone_rd,
)
from utils.audit_entity import (
    candidata_entity_meta,
    log_candidata_action,
)
from utils.robust_save import execute_robust_save
from utils.rbac import (
    can as rbac_can,
    has_admin_access,
    log_permission_denied,
    normalize_role as normalize_staff_role,
    permission_required_for_path,
    role_for_user,
)
from utils.pasaje_mode import (
    apply_pasaje_to_solicitud,
    normalize_pasaje_mode_text,
    read_pasaje_mode_text,
    strip_pasaje_marker_from_note,
)
from utils.modalidad import (
    canonicalize_modalidad_trabajo,
    split_modalidad_for_ui,
    should_preserve_existing_modalidad_on_edit,
)
from utils.admin_async import payload as shared_admin_async_payload, wants_json as shared_admin_async_wants_json
from utils.outbox_relay import OUTBOX_RELAY_ALLOWED_EVENT_TYPES, _redis_client as relay_redis_client, _redis_stream_key as relay_redis_stream_key
from services.candidata_invariants import (
    InvariantConflictError,
    change_candidate_state as invariant_change_candidate_state,
    release_solicitud_candidatas_on_cancel as invariant_release_solicitud_candidatas_on_cancel,
    sync_solicitud_candidatas_after_assignment as invariant_sync_solicitud_candidatas_after_assignment,
)
from services.solicitud_estado import (
    days_in_state,
    priority_band_for_days,
    priority_band_rank,
    priority_message_for_solicitud,
    resolve_solicitud_estado_priority_anchor,
    set_solicitud_estado,
)
from services.solicitud_recommendation_service import SolicitudRecommendationService
from services.solicitud_recommendation_snapshot import build_candidate_guard, build_solicitud_fingerprint
from utils.timezone import (
    format_rd_datetime,
    iso_utc_z,
    now_rd,
    parse_iso_utc,
    rd_day_range_utc_naive,
    rd_today,
    to_rd,
    utc_now_naive,
    utc_timestamp,
)
try:
    from utils.chat_e2e_guard import (
        E2EChatGuardError,
        chat_e2e_enabled,
        chat_e2e_scope_prefix,
        chat_e2e_scope_key,
        chat_e2e_run_id,
        chat_e2e_subject,
        chat_e2e_tag,
        enforce_e2e_cliente_id,
        enforce_e2e_conversation,
        enforce_e2e_conversation_id,
        enforce_e2e_solicitud_id,
        e2e_message_meta,
    )
except Exception:
    class E2EChatGuardError(Exception):
        reason = "disabled"

    def chat_e2e_enabled() -> bool:
        return False

    def chat_e2e_scope_prefix() -> str:
        return ""

    def chat_e2e_scope_key(*, cliente_id: int, solicitud_id: int | None):
        if int(solicitud_id or 0) > 0:
            return f"solicitud:{int(solicitud_id)}"
        return f"general:{int(cliente_id)}"

    def chat_e2e_run_id() -> str:
        return ""

    def chat_e2e_subject(base: str) -> str:
        return str(base or "")

    def chat_e2e_tag() -> str:
        return ""

    def enforce_e2e_cliente_id(_cliente_id: int) -> None:
        return

    def enforce_e2e_conversation(_conversation) -> None:
        return

    def enforce_e2e_conversation_id(_conversation_id: int) -> None:
        return

    def enforce_e2e_solicitud_id(_solicitud_id: int | None) -> None:
        return

    def e2e_message_meta(meta):
        return meta if isinstance(meta, dict) else {}
from utils.staff_presence import (
    build_presence_snapshot,
    list_recent_staff_presence_states,
    upsert_staff_presence_snapshot,
)
from utils.sqlite_pk import maybe_assign_sqlite_pk as _shared_maybe_assign_sqlite_pk
from utils.staff_mfa import (
    MFA_PENDING_SESSION_KEY,
    MFA_SETUP_SECRET_SESSION_KEY,
    generate_mfa_secret,
    generate_qr_png_data_uri,
    mfa_enforced_for_staff,
    mfa_issuer_name,
    provisioning_uri,
    session_begin_mfa_pending,
    session_clear_mfa_pending,
    session_get_mfa_pending,
    staff_role_requires_mfa,
    verify_totp_code,
)
from core.services.search import search_candidatas_limited

from . import admin_bp
from .decorators import admin_required, staff_required

from clientes.routes import (
    generar_link_publico_compartible_cliente,
    generar_link_publico_compartible_cliente_nuevo,
)

def _is_true_env(value: str, default: bool = False) -> bool:
    raw = (value or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _login_debug_enabled() -> bool:
    return _is_true_env(os.getenv("LOGIN_DEBUG", "0"), default=False)


def _staff_password_min_len() -> int:
    try:
        return max(8, int((os.getenv("STAFF_PASSWORD_MIN_LEN") or "8").strip()))
    except Exception:
        return 8


def _operational_rate_limits_enabled() -> bool:
    raw = os.getenv("ENABLE_OPERATIONAL_RATE_LIMITS")
    if raw is not None and str(raw).strip() != "":
        return _is_true_env(raw, default=False)
    run_env = (os.getenv("APP_ENV", os.getenv("FLASK_ENV", "development")) or "").strip().lower()
    return run_env in ("prod", "production")


def _live_invalidation_stream_enabled() -> bool:
    cfg = current_app.config.get("ADMIN_LIVE_SSE_ENABLED")
    if cfg is not None:
        if isinstance(cfg, bool):
            return cfg
        return _is_true_env(str(cfg), default=True)

    # Compat legado por variable env.
    raw_new = os.getenv("ADMIN_LIVE_SSE_ENABLED")
    if raw_new is not None and str(raw_new).strip() != "":
        return _is_true_env(str(raw_new), default=False)

    raw = os.getenv("ENABLE_ADMIN_LIVE_INVALIDATION_STREAM")
    if raw is not None and str(raw).strip() != "":
        return _is_true_env(str(raw), default=False)
    run_env = (os.getenv("APP_ENV", os.getenv("FLASK_ENV", "development")) or "").strip().lower()
    return run_env not in ("prod", "production")


def _admin_auto_presence_touch_enabled() -> bool:
    raw = os.getenv("ADMIN_AUTO_PRESENCE_TOUCH_ENABLED")
    if raw is None or str(raw).strip() == "":
        return True
    return _is_true_env(str(raw), default=True)


def _should_auto_touch_presence() -> bool:
    if request.method not in {"GET", "HEAD"}:
        return False
    if not _admin_auto_presence_touch_enabled():
        return False

    endpoint = (request.endpoint or "").strip().lower()
    path = (request.path or "").strip().lower()
    if endpoint in {
        "admin.monitoreo_presence_ping",
        "admin.monitoreo_presence_json",
        "admin.monitoreo_presence_stream",
        "admin.seguridad_locks_ping",
        "admin.live_invalidation_poll",
        "admin.live_invalidation_stream",
        "admin.chat_staff_badge_json",
        "admin.live_observability_ingest",
    }:
        return False
    if path.startswith("/admin/live/"):
        return False
    if path in {
        "/admin/chat/badge.json",
        "/admin/monitoreo/presence.json",
        "/admin/monitoreo/presence/stream",
    }:
        return False
    if path.endswith(".json"):
        return False
    if _admin_async_wants_json():
        return False
    return True


def _admin_global_action_guard_enabled() -> bool:
    """Guard global admin para POST/PUT/PATCH/DELETE.

    Por defecto queda desactivado para evitar falsos positivos operativos.
    Se puede reactivar explícitamente con ENABLE_ADMIN_GLOBAL_ACTION_GUARD=1.
    """
    raw = os.getenv("ENABLE_ADMIN_GLOBAL_ACTION_GUARD")
    if raw is None or str(raw).strip() == "":
        return False
    return _is_true_env(str(raw), default=False)


def _critical_concurrency_guards_enabled() -> bool:
    raw = os.getenv("ENABLE_CRITICAL_CONCURRENCY_GUARDS")
    if raw is None or str(raw).strip() == "":
        return True
    return _is_true_env(str(raw), default=True)


def _idempotency_enabled() -> bool:
    raw = os.getenv("ENABLE_CRITICAL_IDEMPOTENCY")
    if raw is None or str(raw).strip() == "":
        return True
    return _is_true_env(str(raw), default=True)


def _request_actor_id() -> str:
    try:
        uid = int(getattr(current_user, "id", 0) or 0)
        if uid > 0:
            return str(uid)
    except Exception:
        pass
    return str(session.get("usuario") or "anon")


def _parse_if_match_version(raw: str | None) -> int | None:
    txt = (raw or "").strip()
    if not txt:
        return None
    # Soporta formatos: W/"12", "12", 12
    txt = txt.replace("W/", "").replace('"', "").strip()
    if txt.isdigit():
        return int(txt)
    return None


def _expected_row_version() -> int | None:
    candidates = (
        _parse_if_match_version(request.headers.get("If-Match")),
        request.headers.get("X-Entity-Version"),
        request.form.get("row_version"),
        request.args.get("row_version"),
    )
    for raw in candidates:
        if raw is None:
            continue
        val = _parse_if_match_version(str(raw))
        if val is not None:
            return val
    return None


def _build_request_hash() -> str:
    body = ""
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        if request.is_json:
            try:
                payload = request.get_json(silent=True) or {}
                body = json.dumps(payload, sort_keys=True, ensure_ascii=True)
            except Exception:
                body = ""
        else:
            try:
                pairs = sorted((k, v) for k, v in request.form.items())
                body = "&".join(f"{k}={v}" for k, v in pairs)
            except Exception:
                body = ""
    base = f"{request.method}|{request.path}|{body}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def _incoming_idempotency_key() -> str:
    for raw in (
        request.headers.get("Idempotency-Key"),
        request.form.get("idempotency_key"),
        request.args.get("idempotency_key"),
    ):
        value = (str(raw or "")).strip()
        if value:
            return value[:128]
    return ""


def _fallback_idempotency_key(scope: str, entity_id: int | str, action: str) -> str:
    # Fallback corto para flujos legacy sin header/hidden-field.
    stamp = f"{_request_actor_id()}|{scope}|{entity_id}|{action}|{_expected_row_version() or 0}|{_build_request_hash()}"
    return hashlib.sha256(stamp.encode("utf-8")).hexdigest()[:64]


def _claim_idempotency(*, scope: str, entity_type: str, entity_id: int | str, action: str):
    if not _idempotency_enabled():
        return None, False

    key = _incoming_idempotency_key() or _fallback_idempotency_key(scope, entity_id, action)
    req_hash = _build_request_hash()
    actor_id = _request_actor_id()

    row = RequestIdempotencyKey(
        scope=scope,
        idempotency_key=key,
        actor_id=actor_id,
        entity_type=(entity_type or "")[:50],
        entity_id=str(entity_id)[:64],
        request_hash=req_hash,
    )
    try:
        db.session.add(row)
        db.session.flush()
        setattr(row, "request_hash_conflict", False)
        return row, False
    except IntegrityError:
        db.session.rollback()
        existing = (
            RequestIdempotencyKey.query
            .filter_by(scope=scope, idempotency_key=key)
            .first()
        )
        if existing is None:
            return None, True
        if str(getattr(existing, "request_hash", "") or "") != req_hash:
            setattr(existing, "request_hash_conflict", True)
            existing.last_seen_at = utc_now_naive()
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
            return existing, True
        setattr(existing, "request_hash_conflict", False)
        existing.last_seen_at = utc_now_naive()
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
        return existing, True
    except SQLAlchemyError:
        # Fail-open en esta fase para no bloquear operación si falta tabla/migración.
        db.session.rollback()
        return None, False


def _set_idempotency_response(row: RequestIdempotencyKey | None, *, status: int, code: str | None = None):
    if row is None:
        return
    try:
        row.response_status = int(status)
        row.response_code = (code or "")[:80] or None
        row.last_seen_at = utc_now_naive()
        db.session.add(row)
    except Exception:
        return


def _idempotency_request_conflict(row: RequestIdempotencyKey | None) -> bool:
    return bool(getattr(row, "request_hash_conflict", False))


def _idempotency_conflict_message() -> str:
    return 'La misma Idempotency-Key fue reutilizada con datos distintos. Genera una nueva acción e intenta nuevamente.'


def _new_form_idempotency_key() -> str:
    return secrets.token_urlsafe(24)


def _incoming_correlation_id() -> str:
    for raw in (
        request.headers.get("X-Correlation-ID"),
        request.headers.get("X-Request-ID"),
    ):
        value = (str(raw or "")).strip()
        if value:
            return value[:64]
    return ""


def _maybe_assign_sqlite_pk(model_obj, model_cls) -> None:
    _shared_maybe_assign_sqlite_pk(session=db.session, model_obj=model_obj, model_cls=model_cls)


_DOMAIN_OUTBOX_TABLE_READY: bool | None = None


def _domain_outbox_table_ready() -> bool:
    global _DOMAIN_OUTBOX_TABLE_READY
    if _DOMAIN_OUTBOX_TABLE_READY is not None:
        return bool(_DOMAIN_OUTBOX_TABLE_READY)
    try:
        bind = db.session.get_bind()
        inspector = sa_inspect(bind)
        table_name = str(getattr(DomainOutbox, "__tablename__", "domain_outbox") or "domain_outbox")
        _DOMAIN_OUTBOX_TABLE_READY = bool(inspector.has_table(table_name))
    except Exception:
        _DOMAIN_OUTBOX_TABLE_READY = False
    return bool(_DOMAIN_OUTBOX_TABLE_READY)


def _emit_domain_outbox_event(
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: int | str,
    aggregate_version: int | None,
    payload: dict | None = None,
):
    if not _domain_outbox_table_ready():
        return
    event_id = secrets.token_hex(16)
    payload_data = dict(payload or {})
    if chat_e2e_enabled():
        payload_data.setdefault("e2e_tag", chat_e2e_tag())
        payload_data.setdefault("e2e_run_id", chat_e2e_run_id())
    row = DomainOutbox(
        event_id=event_id,
        event_type=(event_type or "")[:80],
        aggregate_type=(aggregate_type or "")[:80],
        aggregate_id=str(aggregate_id)[:64],
        aggregate_version=aggregate_version,
        occurred_at=utc_now_naive(),
        actor_id=_request_actor_id(),
        region="admin",
        payload=payload_data,
        schema_version=1,
        correlation_id=_incoming_correlation_id() or None,
        idempotency_key=_incoming_idempotency_key() or None,
    )
    _maybe_assign_sqlite_pk(row, DomainOutbox)
    db.session.add(row)


def _publish_fast_path_stream_event(
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: int | str,
    payload: dict | None = None,
    aggregate_version: int | None = None,
    actor_id: str | None = None,
    region: str = "admin",
) -> bool:
    ev = str(event_type or "").strip().upper()
    if not ev:
        return False
    try:
        redis_client = relay_redis_client()
        stream_key = relay_redis_stream_key()
    except Exception:
        return False
    if not redis_client or not stream_key:
        return False

    now = utc_now_naive()
    envelope = {
        "schema_version": 1,
        "event_id": secrets.token_hex(16),
        "event_type": ev[:80],
        "occurred_at": iso_utc_z(now),
        "recorded_at": iso_utc_z(now),
        "actor_id": str(actor_id or _request_actor_id() or "") or None,
        "correlation_id": _incoming_correlation_id() or None,
        "idempotency_key": _incoming_idempotency_key() or None,
        "region": str(region or "admin")[:30],
        "aggregate": {
            "type": str(aggregate_type or "")[:80],
            "id": str(aggregate_id or "")[:64],
            "version": aggregate_version,
        },
        "payload": dict(payload or {}),
    }
    try:
        redis_client.xadd(
            stream_key,
            {"event": json.dumps(envelope, ensure_ascii=True, separators=(",", ":"))},
        )
        return True
    except Exception:
        return False


def _emit_cliente_live_solicitud_events(
    *,
    event_type: str,
    solicitud: Solicitud | None,
    cliente_id: int | None = None,
    include_dashboard: bool = False,
):
    sid = _safe_int(getattr(solicitud, "id", 0), default=0)
    cid = _safe_int(cliente_id or getattr(solicitud, "cliente_id", 0), default=0)
    if sid <= 0 or cid <= 0:
        return

    _emit_domain_outbox_event(
        event_type=event_type,
        aggregate_type="Solicitud",
        aggregate_id=int(sid),
        aggregate_version=int(getattr(solicitud, "row_version", 0) or 0) + 1,
        payload={
            "cliente_id": int(cid),
            "solicitud_id": int(sid),
            "codigo_solicitud": str(getattr(solicitud, "codigo_solicitud", "") or "")[:40],
            "estado": str(getattr(solicitud, "estado", "") or ""),
        },
    )
    if include_dashboard:
        reason = "solicitud_creada" if str(event_type or "").upper() == "CLIENTE_SOLICITUD_CREATED" else "solicitud_actualizada"
        _emit_domain_outbox_event(
            event_type="CLIENTE_DASHBOARD_UPDATED",
            aggregate_type="Cliente",
            aggregate_id=int(cid),
            aggregate_version=None,
            payload={
                "cliente_id": int(cid),
                "solicitud_id": int(sid),
                "reason": reason,
            },
        )


def _set_solicitud_estado(solicitud: Solicitud, nuevo_estado: str, *, now_dt: datetime | None = None):
    return set_solicitud_estado(solicitud, nuevo_estado, now_dt=now_dt)


def _set_solicitud_estado_with_outbox(
    solicitud: Solicitud,
    nuevo_estado: str,
    *,
    now_dt: datetime | None = None,
    payload_extra: dict | None = None,
):
    transition = _set_solicitud_estado(solicitud, nuevo_estado, now_dt=now_dt)
    if not bool(transition.get("changed")):
        return transition
    _emit_domain_outbox_event(
        event_type="SOLICITUD_ESTADO_CAMBIADO",
        aggregate_type="Solicitud",
        aggregate_id=solicitud.id,
        aggregate_version=(int(getattr(solicitud, "row_version", 0) or 0) + 1),
        payload={
            "solicitud_id": int(solicitud.id),
            "from": transition.get("from"),
            "to": transition.get("to"),
            **(payload_extra or {}),
        },
    )
    try:
        _notify_cliente_status_change(solicitud, transition)
    except Exception:
        # Fail-open: en entornos parciales (tests/migraciones incompletas) no bloquear transición de estado.
        pass
    return transition


_F4_LIVE_ALLOWED_EVENT_TYPES = {str(ev or "").strip().upper() for ev in OUTBOX_RELAY_ALLOWED_EVENT_TYPES}
_F4_LIVE_POLL_APPROVED_VIEWS = {
    "solicitud_detail",
    "cliente_detail",
    "solicitudes_summary",
    "solicitudes_prioridad_summary",
    "chat_inbox",
}
_LIVE_INVALIDATION_MODE_RELAY = "relay_published"
_LIVE_INVALIDATION_MODE_DEGRADED = "degraded_outbox_fallback"
_LIVE_STREAM_CONCURRENCY_KEY_PREFIX = "admin_live_stream_concurrency"


def _live_endpoint_roles(capability: str) -> set[str]:
    cap = (capability or "").strip().lower()
    policy = {
        "invalidation_poll": {"owner", "admin", "secretaria"},
        "invalidation_stream": {"owner", "admin", "secretaria"},
        "presence_ping": {"owner", "admin", "secretaria"},
        "locks_ping": {"owner", "admin", "secretaria"},
    }
    return set(policy.get(cap, {"owner", "admin"}))


def _live_access_allowed(capability: str) -> bool:
    role = role_for_user(current_user)
    return role in _live_endpoint_roles(capability)


def _live_rate_limits(capability: str) -> dict[str, int]:
    cap = (capability or "").strip().lower()

    defaults = {
        "poll": {"window": 60, "max_user": 24, "max_ip": 60, "max_session": 24, "block": 60},
        # presence ping is high-frequency internal telemetry; keep abuse protection but tolerate real multi-tab usage.
        "ping": {"window": 60, "max_user": 480, "max_ip": 6000, "max_session": 480, "block": 20},
        # entity lock heartbeat runs in edit forms; allow sustained normal editing across office IPs.
        "locks_ping": {"window": 60, "max_user": 240, "max_ip": 4000, "max_session": 240, "block": 20},
        "stream_open": {"window": 60, "max_user": 20, "max_ip": 40, "max_session": 20, "block": 45},
    }
    cfg = dict(defaults.get(cap, defaults["poll"]))

    env_prefix = f"LIVE_{cap.upper()}"
    for k in ("window", "max_user", "max_ip", "max_session", "block"):
        raw = os.getenv(f"{env_prefix}_{k.upper()}")
        if raw is None or str(raw).strip() == "":
            continue
        try:
            cfg[k] = int(raw)
        except Exception:
            continue

    cfg["window"] = max(5, min(int(cfg.get("window", 60) or 60), 3600))
    cfg["max_user"] = max(1, min(int(cfg.get("max_user", 24) or 24), 5000))
    cfg["max_ip"] = max(1, min(int(cfg.get("max_ip", 60) or 60), 10000))
    cfg["max_session"] = max(1, min(int(cfg.get("max_session", 24) or 24), 5000))
    cfg["block"] = max(5, min(int(cfg.get("block", cfg["window"]) or cfg["window"]), 3600))
    return cfg


def _live_stream_concurrency_limits() -> dict[str, int]:
    limits = {
        "max_user": 2,
        "max_ip": 8,
        "max_session": 2,
        "ttl_sec": 90,
    }
    for key, env_name in (
        ("max_user", "LIVE_STREAM_MAX_CONCURRENT_USER"),
        ("max_ip", "LIVE_STREAM_MAX_CONCURRENT_IP"),
        ("max_session", "LIVE_STREAM_MAX_CONCURRENT_SESSION"),
        ("ttl_sec", "LIVE_STREAM_CONCURRENCY_TTL_SEC"),
    ):
        raw = os.getenv(env_name)
        if raw is None or str(raw).strip() == "":
            continue
        try:
            limits[key] = int(raw)
        except Exception:
            continue
    limits["max_user"] = max(1, min(int(limits["max_user"]), 30))
    limits["max_ip"] = max(1, min(int(limits["max_ip"]), 100))
    limits["max_session"] = max(1, min(int(limits["max_session"]), 30))
    limits["ttl_sec"] = max(20, min(int(limits["ttl_sec"]), 600))
    return limits


def _live_session_key() -> str:
    token = (session.get("staff_session_token") or "").strip()
    if token:
        return token[:80]
    actor = ""
    try:
        actor = str(current_user.get_id() or "").strip().lower()
    except Exception:
        actor = str(session.get("usuario") or "anon").strip().lower()
    ua = (request.headers.get("User-Agent") or "")[:120]
    return hashlib.sha1(f"{actor}|{ua}".encode("utf-8", errors="ignore")).hexdigest()[:40]


def _live_rate_identity() -> dict[str, str]:
    uid = "anon"
    try:
        if isinstance(current_user, StaffUser):
            uid = str(int(current_user.id))
        else:
            raw = str(current_user.get_id() or "").strip()
            uid = raw or "anon"
    except Exception:
        uid = "anon"

    return {
        "user": uid[:64],
        "ip": (_client_ip() or "0.0.0.0").strip()[:64],
        "session": _live_session_key(),
    }


def _live_security_audit_dedupe(action_type: str, reason: str, dedupe_seconds: int = 20) -> bool:
    key = f"live_audit_dedupe:{(action_type or '')[:40]}:{(_live_session_key() or '')[:40]}:{(reason or '')[:64]}:{(request.path or '')[:120]}"
    if bp_get(key, default=0, context="live_security_audit_dedupe_get"):
        return False
    bp_set(key, 1, timeout=max(5, int(dedupe_seconds)), context="live_security_audit_dedupe_set")
    return True


def _audit_live_security_block(*, action_type: str, summary: str, reason: str, metadata: dict | None = None) -> None:
    if not _live_security_audit_dedupe(action_type=action_type, reason=reason, dedupe_seconds=20):
        return
    log_action(
        action_type=(action_type or "LIVE_SECURITY_BLOCKED")[:80],
        entity_type="security",
        entity_id=(str(getattr(current_user, "id", ""))[:64] or None),
        summary=(summary or "Seguridad live")[:255],
        metadata=dict(metadata or {}),
        success=False,
        error=(reason or "blocked")[:255],
    )


def _live_rate_store_get(key: str):
    if _cache_ok():
        try:
            return cache.get(key)
        except Exception:
            pass
    return session.get(key)


def _live_rate_store_set(key: str, value: dict, timeout: int) -> None:
    if _cache_ok():
        try:
            cache.set(key, value, timeout=timeout)
            return
        except Exception:
            pass
    session[key] = value
    session.modified = True


def _enforce_live_rate_limit(capability: str):
    if not _operational_rate_limits_enabled():
        return None

    limits = _live_rate_limits(capability)
    identities = _live_rate_identity()
    now_ts = utc_timestamp()
    blocked_scopes: list[tuple[str, int]] = []

    for scope, raw_ident in (("user", identities["user"]), ("ip", identities["ip"]), ("session", identities["session"])):
        ident = (raw_ident or "").strip().lower()[:80] or "anon"
        key = f"live_rl:{capability}:{scope}:{ident}"
        data = _live_rate_store_get(key) or {}

        try:
            window_start = float(data.get("window_start") or 0.0)
        except Exception:
            window_start = 0.0
        if not window_start or (now_ts - window_start) > int(limits["window"]):
            data = {"window_start": now_ts, "count": 0}

        locked_until = data.get("locked_until")
        if locked_until:
            try:
                left = int(float(locked_until) - now_ts)
            except Exception:
                left = int(limits["block"])
            if left > 0:
                blocked_scopes.append((scope, max(1, left)))
                continue
            data.pop("locked_until", None)

        data["count"] = int(data.get("count") or 0) + 1
        max_allowed = int(limits.get(f"max_{scope}", limits["max_user"]))
        if int(data["count"]) > max_allowed:
            data["locked_until"] = now_ts + int(limits["block"])
            blocked_scopes.append((scope, int(limits["block"])))

        ttl = max(int(limits["window"]), int(limits["block"])) + 5
        _live_rate_store_set(key, data, timeout=ttl)

    if not blocked_scopes:
        return None

    scope_txt, retry_after = blocked_scopes[0]
    return {
        "blocked": True,
        "scope": scope_txt,
        "retry_after_sec": int(max(1, retry_after)),
    }


def _presence_ping_state_hash(payload: dict | None, current_path: str) -> str:
    try:
        snapshot = build_presence_snapshot(payload or {}, fallback_route=(current_path or ""))
        return str(snapshot.get("state_hash") or "").strip()[:64]
    except Exception:
        return ""


def _presence_ping_should_count_rate_limit(
    *,
    user_id: int,
    session_id: str,
    state_hash: str,
    dedupe_seconds: int = 2,
) -> bool:
    uid = int(user_id or 0)
    sid = str(session_id or "").strip()[:120]
    shash = str(state_hash or "").strip()[:64]
    if uid <= 0 or not sid or not shash:
        return True

    key = f"live_presence_ping_dedupe:{uid}:{sid}"
    now_ts = float(utc_timestamp())
    prior = bp_get(key, default=None, context="live_presence_ping_dedupe_get")
    if isinstance(prior, dict):
        if str(prior.get("state_hash") or "") == shash:
            try:
                prior_ts = float(prior.get("ts") or 0.0)
            except Exception:
                prior_ts = 0.0
            if (now_ts - prior_ts) < max(1, int(dedupe_seconds or 2)):
                return False

    bp_set(
        key,
        {"state_hash": shash, "ts": now_ts},
        timeout=max(10, int(dedupe_seconds or 2) * 8),
        context="live_presence_ping_dedupe_set",
    )
    return True


def _stream_registry_key(scope: str, identity: str) -> str:
    return f"{_LIVE_STREAM_CONCURRENCY_KEY_PREFIX}:{(scope or '')[:12]}:{(identity or 'anon')[:120]}"


def _stream_registry_load(scope: str, identity: str) -> dict[str, float]:
    key = _stream_registry_key(scope, identity)
    data = bp_get(key, default={}, context="live_stream_registry_get") or {}
    if not isinstance(data, dict):
        data = {}
    now_ts = float(time.time())
    cleaned: dict[str, float] = {}
    for sid, exp in data.items():
        try:
            expf = float(exp)
        except Exception:
            continue
        if expf > now_ts:
            cleaned[str(sid)[:64]] = expf
    return cleaned


def _stream_registry_store(scope: str, identity: str, data: dict[str, float], ttl_sec: int) -> bool:
    key = _stream_registry_key(scope, identity)
    return bool(bp_set(key, data, timeout=max(20, int(ttl_sec) * 3), context="live_stream_registry_set"))


def _stream_registry_make_room_for_new_connection(
    reg: dict[str, float],
    *,
    max_allowed: int,
) -> dict[str, float]:
    """
    Evita auto-bloqueos por conexiones huérfanas/reconexiones rápidas:
    conserva los streams más recientes y deja un slot para el nuevo.
    """
    if int(max_allowed) <= 1 or len(reg) < int(max_allowed):
        return reg
    keep = max(0, int(max_allowed) - 1)
    if keep <= 0:
        return {}
    ordered = sorted(reg.items(), key=lambda item: float(item[1] or 0.0), reverse=True)
    return {str(sid): float(exp) for sid, exp in ordered[:keep]}


def _live_stream_register(stream_id: str):
    limits = _live_stream_concurrency_limits()
    identities = _live_rate_identity()
    now_ts = float(time.time())
    expires_at = now_ts + float(limits["ttl_sec"])
    added: list[tuple[str, str]] = []

    for scope, raw_ident, max_allowed in (
        ("user", identities["user"], int(limits["max_user"])),
        ("session", identities["session"], int(limits["max_session"])),
        ("ip", identities["ip"], int(limits["max_ip"])),
    ):
        ident = (raw_ident or "").strip().lower()[:80] or "anon"
        reg = _stream_registry_load(scope, ident)
        if scope in {"user", "session"} and len(reg) >= max_allowed:
            reg = _stream_registry_make_room_for_new_connection(reg, max_allowed=max_allowed)
        if len(reg) >= max_allowed:
            for prev_scope, prev_ident in added:
                prev_reg = _stream_registry_load(prev_scope, prev_ident)
                prev_reg.pop(stream_id, None)
                _stream_registry_store(prev_scope, prev_ident, prev_reg, ttl_sec=int(limits["ttl_sec"]))
            return {
                "ok": False,
                "scope": scope,
                "limit": int(max_allowed),
                "active": int(len(reg)),
            }
        reg[stream_id] = expires_at
        if not _stream_registry_store(scope, ident, reg, ttl_sec=int(limits["ttl_sec"])):
            return {"ok": False, "scope": scope, "limit": int(max_allowed), "active": int(len(reg))}
        added.append((scope, ident))

    return {"ok": True}


def _live_stream_refresh(stream_id: str) -> None:
    limits = _live_stream_concurrency_limits()
    identities = _live_rate_identity()
    expires_at = float(time.time()) + float(limits["ttl_sec"])
    for scope, raw_ident in (("user", identities["user"]), ("session", identities["session"]), ("ip", identities["ip"])):
        ident = (raw_ident or "").strip().lower()[:80] or "anon"
        reg = _stream_registry_load(scope, ident)
        if stream_id in reg:
            reg[stream_id] = expires_at
            _stream_registry_store(scope, ident, reg, ttl_sec=int(limits["ttl_sec"]))


def _live_stream_release(stream_id: str) -> None:
    limits = _live_stream_concurrency_limits()
    identities = _live_rate_identity()
    for scope, raw_ident in (("user", identities["user"]), ("session", identities["session"]), ("ip", identities["ip"])):
        ident = (raw_ident or "").strip().lower()[:80] or "anon"
        reg = _stream_registry_load(scope, ident)
        if stream_id in reg:
            reg.pop(stream_id, None)
            _stream_registry_store(scope, ident, reg, ttl_sec=int(limits["ttl_sec"]))


def _live_poll_outbox_fallback_enabled() -> bool:
    raw = os.getenv("LIVE_POLL_OUTBOX_FALLBACK_ENABLED")
    if raw is None or str(raw).strip() == "":
        return True
    return _is_true_env(str(raw), default=False)


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


_P1C1_PERF_HEADER_PREFIX = "X-P1C1-Perf-"


@event.listens_for(Engine, "before_cursor_execute")
def _p1c1_perf_before_cursor_execute(conn, _cursor, _statement, _parameters, _context, _executemany):
    if not has_request_context():
        return
    try:
        env = request.environ
    except Exception:
        return
    if not bool(env.get("_p1c1_perf_enabled")):
        return
    stack = conn.info.setdefault("_p1c1_perf_stack", [])
    stack.append(time.perf_counter())


@event.listens_for(Engine, "after_cursor_execute")
def _p1c1_perf_after_cursor_execute(conn, _cursor, _statement, _parameters, _context, _executemany):
    if not has_request_context():
        return
    try:
        env = request.environ
    except Exception:
        return
    if not bool(env.get("_p1c1_perf_enabled")):
        return
    stack = conn.info.get("_p1c1_perf_stack") or []
    started = stack.pop() if stack else None
    if started is None:
        return
    elapsed_ms = max(0.0, (time.perf_counter() - float(started)) * 1000.0)
    env["_p1c1_perf_db_ms"] = float(env.get("_p1c1_perf_db_ms", 0.0)) + elapsed_ms
    env["_p1c1_perf_db_queries"] = int(env.get("_p1c1_perf_db_queries", 0)) + 1


@contextmanager
def _p1c1_perf_scope(scope_name: str, *, enabled: bool = True):
    started = time.perf_counter()
    env = request.environ if has_request_context() else {}
    if enabled:
        env["_p1c1_perf_enabled"] = True
        env["_p1c1_perf_db_ms"] = 0.0
        env["_p1c1_perf_db_queries"] = 0

    def _finalize(response_like, *, html_bytes: int | None = None, extra: dict | None = None):
        response = make_response(response_like)
        if not enabled:
            return response
        elapsed_ms = max(0.0, (time.perf_counter() - started) * 1000.0)
        db_ms = float(env.get("_p1c1_perf_db_ms", 0.0))
        db_queries = int(env.get("_p1c1_perf_db_queries", 0))
        response.headers[f"{_P1C1_PERF_HEADER_PREFIX}Scope"] = str(scope_name or "")
        response.headers[f"{_P1C1_PERF_HEADER_PREFIX}Latency-Ms"] = f"{elapsed_ms:.2f}"
        response.headers[f"{_P1C1_PERF_HEADER_PREFIX}DB-Queries"] = str(db_queries)
        response.headers[f"{_P1C1_PERF_HEADER_PREFIX}DB-Time-Ms"] = f"{db_ms:.2f}"
        if html_bytes is not None and int(html_bytes) >= 0:
            response.headers[f"{_P1C1_PERF_HEADER_PREFIX}HTML-Bytes"] = str(int(html_bytes))

        payload = {
            "scope": str(scope_name or ""),
            "path": str(request.path or ""),
            "latency_ms": round(elapsed_ms, 2),
            "db_queries": db_queries,
            "db_time_ms": round(db_ms, 2),
            "html_bytes": int(html_bytes) if html_bytes is not None else None,
        }
        if isinstance(extra, dict) and extra:
            payload.update(extra)
        try:
            current_app.logger.info("[p1-c1-baseline] %s", json.dumps(payload, ensure_ascii=True, sort_keys=True))
        except Exception:
            pass
        return response

    try:
        yield _finalize
    finally:
        if enabled:
            env.pop("_p1c1_perf_enabled", None)
            env.pop("_p1c1_perf_db_ms", None)
            env.pop("_p1c1_perf_db_queries", None)


def _normalize_live_invalidation_event(event: dict, *, stream_id: str | None = None) -> dict | None:
    event_type = str((event or {}).get("event_type") or "").strip().upper()
    if not event_type or event_type not in _F4_LIVE_ALLOWED_EVENT_TYPES:
        return None

    aggregate = dict((event or {}).get("aggregate") or {})
    payload = dict((event or {}).get("payload") or {})
    aggregate_type = str(aggregate.get("type") or "").strip() or "Solicitud"
    aggregate_id = str(aggregate.get("id") or "").strip()

    if event_type.startswith("CHAT_"):
        conversation_id = _safe_int(payload.get("conversation_id") or aggregate_id, default=0)
        if conversation_id <= 0:
            return None
        cliente_id = _safe_int(payload.get("cliente_id"), default=0)
        if cliente_id <= 0:
            cliente_id = None
        solicitud_id = _safe_int(payload.get("solicitud_id"), default=0)
        if solicitud_id <= 0:
            solicitud_id = None
        return {
            "event_id": str((event or {}).get("event_id") or ""),
            "event_type": event_type,
            "occurred_at": (event or {}).get("occurred_at"),
            "recorded_at": (event or {}).get("recorded_at"),
            "stream_id": str(stream_id or "") or None,
            "aggregate": {
                "type": "ChatConversation",
                "id": str(conversation_id),
                "version": aggregate.get("version"),
            },
            "target": {
                "entity_type": "chat_conversation",
                "conversation_id": int(conversation_id),
                "solicitud_id": int(solicitud_id) if solicitud_id else None,
                "cliente_id": cliente_id,
            },
            "payload": {
                "conversation_id": int(conversation_id),
                "cliente_id": cliente_id,
                "solicitud_id": int(solicitud_id) if solicitud_id else None,
                "status": payload.get("status") or payload.get("to"),
                "from": payload.get("from"),
                "message_id": payload.get("message_id"),
                "sender_type": payload.get("sender_type"),
                "preview": payload.get("preview"),
                "cliente_unread_count": payload.get("cliente_unread_count"),
                "staff_unread_count": payload.get("staff_unread_count"),
                "assigned_staff_user_id": payload.get("assigned_staff_user_id"),
                "assigned_staff_username": payload.get("assigned_staff_username"),
                "actor_type": payload.get("actor_type"),
                "is_typing": payload.get("is_typing"),
                "typing_expires_in": payload.get("typing_expires_in"),
                "typing_expires_at": payload.get("typing_expires_at"),
                "message": payload.get("message") if isinstance(payload.get("message"), dict) else None,
            },
        }

    if event_type.startswith("STAFF.CASE_TRACKING."):
        case_id = _safe_int(payload.get("case_id") or aggregate.get("id"), default=0)
        if case_id <= 0:
            return None
        return {
            "event_id": str((event or {}).get("event_id") or ""),
            "event_type": event_type,
            "occurred_at": (event or {}).get("occurred_at"),
            "recorded_at": (event or {}).get("recorded_at"),
            "stream_id": str(stream_id or "") or None,
            "aggregate": {
                "type": "SeguimientoCandidataCaso",
                "id": str(case_id),
                "version": aggregate.get("version"),
            },
            "target": {
                "entity_type": "seguimiento_candidata_caso",
                "case_id": int(case_id),
            },
            "payload": {
                "case_id": int(case_id),
                "public_id": payload.get("public_id"),
                "estado": payload.get("estado"),
                "owner_staff_user_id": payload.get("owner_staff_user_id"),
                "due_at": payload.get("due_at"),
            },
        }

    solicitud_id_raw = payload.get("solicitud_id") or aggregate_id
    solicitud_id = _safe_int(solicitud_id_raw, default=0)
    if solicitud_id <= 0:
        return None

    cliente_id_raw = payload.get("cliente_id")
    cliente_id = _safe_int(cliente_id_raw, default=0)
    if cliente_id <= 0:
        cliente_id = None

    return {
        "event_id": str((event or {}).get("event_id") or ""),
        "event_type": event_type,
        "occurred_at": (event or {}).get("occurred_at"),
        "recorded_at": (event or {}).get("recorded_at"),
        "stream_id": str(stream_id or "") or None,
        "aggregate": {
            "type": aggregate_type,
            "id": str(solicitud_id),
            "version": aggregate.get("version"),
        },
        "target": {
            "entity_type": "solicitud",
            "solicitud_id": int(solicitud_id),
            "cliente_id": cliente_id,
        },
    }


def _normalize_live_invalidation_from_outbox(row: DomainOutbox) -> dict | None:
    event = {
        "event_id": str(getattr(row, "event_id", "") or ""),
        "event_type": str(getattr(row, "event_type", "") or ""),
        "occurred_at": iso_utc_z(getattr(row, "occurred_at", None)),
        "recorded_at": iso_utc_z(getattr(row, "created_at", None)),
        "aggregate": {
            "type": str(getattr(row, "aggregate_type", "") or ""),
            "id": str(getattr(row, "aggregate_id", "") or ""),
            "version": getattr(row, "aggregate_version", None),
        },
        "payload": dict(getattr(row, "payload", None) or {}),
    }
    return _normalize_live_invalidation_event(event)


@admin_bp.route('/live/invalidation/poll', methods=['GET'])
@login_required
@staff_required
def live_invalidation_poll():
    if not _live_access_allowed("invalidation_poll"):
        _audit_live_security_block(
            action_type="LIVE_ACCESS_DENIED",
            summary="Acceso denegado a live invalidation poll",
            reason="live_poll_role_forbidden",
            metadata={"path": request.path, "role": role_for_user(current_user), "capability": "invalidation_poll"},
        )
        abort(403)
    rl = _enforce_live_rate_limit("poll")
    if rl:
        _audit_live_security_block(
            action_type="LIVE_RATE_LIMITED",
            summary="Rate limit bloqueó live invalidation poll",
            reason="live_poll_rate_limited",
            metadata={"path": request.path, "scope": rl.get("scope"), "retry_after_sec": rl.get("retry_after_sec")},
        )
        response = jsonify({
            "ok": False,
            "error": "rate_limited",
            "scope": rl.get("scope"),
            "retry_after_sec": int(rl.get("retry_after_sec") or 1),
        })
        response.status_code = 429
        response.headers["Retry-After"] = str(int(rl.get("retry_after_sec") or 1))
        return response

    requested_view = str(request.args.get("view") or "").strip().lower()
    fallback_view_allowed = requested_view in _F4_LIVE_POLL_APPROVED_VIEWS
    perf_enabled = requested_view in {"solicitud_detail", "solicitudes_prioridad_summary", "solicitudes_summary"}
    with _p1c1_perf_scope("live_invalidation_poll", enabled=perf_enabled) as perf_done:
        after_id = max(0, _safe_int(request.args.get("after_id"), default=0))
        limit = min(80, max(1, _safe_int(request.args.get("limit"), default=25)))
        mode = "poll_only" if not _live_invalidation_stream_enabled() else _LIVE_INVALIDATION_MODE_RELAY

        rows = (
            DomainOutbox.query
            .filter(DomainOutbox.id > after_id)
            .filter(DomainOutbox.published_at.isnot(None))
            .filter(DomainOutbox.event_type.in_(sorted(_F4_LIVE_ALLOWED_EVENT_TYPES)))
            .order_by(DomainOutbox.id.asc())
            .limit(limit)
            .all()
        )

        if (not rows) and fallback_view_allowed and _live_poll_outbox_fallback_enabled():
            degraded_rows = (
                DomainOutbox.query
                .filter(DomainOutbox.id > after_id)
                .filter(DomainOutbox.published_at.is_(None))
                .filter(DomainOutbox.event_type.in_(sorted(_F4_LIVE_ALLOWED_EVENT_TYPES)))
                .order_by(DomainOutbox.id.asc())
                .limit(limit)
                .all()
            )
            if degraded_rows:
                rows = degraded_rows
                mode = _LIVE_INVALIDATION_MODE_DEGRADED
                bump_operational_counter("live:poll:degraded_outbox_fallback_count")

        items = []
        cursor = int(after_id)
        for row in (rows or []):
            rid = int(getattr(row, "id", 0) or 0)
            if rid > cursor:
                cursor = rid
            normalized = _normalize_live_invalidation_from_outbox(row)
            if normalized is not None:
                items.append(normalized)

        response = jsonify({
            "ok": True,
            "items": items,
            "next_after_id": int(cursor),
            "count": len(items),
            "mode": mode,
            "ts": iso_utc_z(),
        })
        response.headers["X-Live-Invalidation-Mode"] = mode
        return perf_done(
            response,
            extra={"count": len(items), "view": requested_view or None, "mode": mode},
        )


@admin_bp.route('/live/invalidation/stream', methods=['GET'])
@login_required
@staff_required
def live_invalidation_stream():
    if not _live_access_allowed("invalidation_stream"):
        _audit_live_security_block(
            action_type="LIVE_ACCESS_DENIED",
            summary="Acceso denegado a live invalidation stream",
            reason="live_stream_role_forbidden",
            metadata={"path": request.path, "role": role_for_user(current_user), "capability": "invalidation_stream"},
        )
        abort(403)
    if current_app.config.get("TESTING") and str(request.args.get("once") or "").strip() == "1":
        payload = {"ts": iso_utc_z()}
        body = f"event: heartbeat\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        headers = {
            "Content-Type": "text/event-stream; charset=utf-8",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
        return Response(body, headers=headers)
    rl = _enforce_live_rate_limit("stream_open")
    if rl:
        _audit_live_security_block(
            action_type="LIVE_RATE_LIMITED",
            summary="Rate limit bloqueó apertura de live stream",
            reason="live_stream_open_rate_limited",
            metadata={"path": request.path, "scope": rl.get("scope"), "retry_after_sec": rl.get("retry_after_sec")},
        )
        response = jsonify({
            "ok": False,
            "error": "rate_limited",
            "scope": rl.get("scope"),
            "retry_after_sec": int(rl.get("retry_after_sec") or 1),
        })
        response.status_code = 429
        response.headers["Retry-After"] = str(int(rl.get("retry_after_sec") or 1))
        return response

    if not _live_invalidation_stream_enabled():
        wants_json_probe = str(request.args.get("probe") or "").strip() == "1"
        accept = str(request.headers.get("Accept") or "").lower()
        xrw = str(request.headers.get("X-Requested-With") or "").lower()
        if ("application/json" in accept) or (xrw == "xmlhttprequest"):
            wants_json_probe = True
        if wants_json_probe:
            response = jsonify(
                {
                    "ok": False,
                    "error": "stream_disabled",
                    "mode": "poll_only",
                    "replaced_by": {"poll_url": url_for("admin.live_invalidation_poll")},
                    "ts": iso_utc_z(),
                }
            )
            response.status_code = 503
            response.headers["X-Live-Invalidation-Mode"] = "poll_only"
            return response

        payload = {"mode": "poll_only", "reason": "sse_disabled", "ts": iso_utc_z()}
        body = f"event: poll_only\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        headers = {
            "Content-Type": "text/event-stream; charset=utf-8",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Live-Invalidation-Mode": "poll_only",
        }
        return Response(body, headers=headers)

    stream_slot_id = secrets.token_hex(12)
    stream_slot = _live_stream_register(stream_slot_id)
    if not bool(stream_slot.get("ok")):
        _audit_live_security_block(
            action_type="LIVE_STREAM_CONCURRENCY_BLOCKED",
            summary="Bloqueo por concurrencia en live stream",
            reason="live_stream_concurrency_limit",
            metadata={
                "path": request.path,
                "scope": stream_slot.get("scope"),
                "limit": stream_slot.get("limit"),
                "active": stream_slot.get("active"),
            },
        )
        return jsonify({
            "ok": False,
            "error": "concurrency_limit",
            "scope": stream_slot.get("scope"),
            "limit": int(stream_slot.get("limit") or 1),
            "active": int(stream_slot.get("active") or 0),
        }), 429

    def _sse(event: str, payload: dict) -> str:
        return f"event: {event}\\ndata: {json.dumps(payload, ensure_ascii=False)}\\n\\n"

    @stream_with_context
    def generate():
        try:
            if current_app.config.get("TESTING") and str(request.args.get("once") or "").strip() == "1":
                yield _sse("heartbeat", {"ts": iso_utc_z()})
                return

            last_stream_id = (request.args.get("last_stream_id") or "").strip() or "$"
            heartbeat_every_sec = 15.0
            last_heartbeat_at = 0.0

            try:
                redis_client = relay_redis_client()
                stream_key = relay_redis_stream_key()
            except Exception as exc:
                redis_client = None
                stream_key = ""
                current_app.logger.warning(
                    "[f4-live] redis stream unavailable; using heartbeat-only mode (%s: %s)",
                    type(exc).__name__,
                    str(exc),
                )

            while True:
                now_ts = time.time()
                emitted = False

                if redis_client is not None and stream_key:
                    try:
                        # block_ms debe ser menor que socket_timeout del cliente Redis
                        # para evitar falsos degradados a heartbeat-only.
                        messages = redis_client.xread({stream_key: last_stream_id}, count=25, block=1500) or []
                        for _stream_name, entries in (messages or []):
                            for entry_id, fields in (entries or []):
                                last_stream_id = str(entry_id or last_stream_id)
                                raw = None
                                if isinstance(fields, dict):
                                    raw = fields.get("event")
                                if not raw:
                                    continue
                                try:
                                    envelope = json.loads(raw)
                                except Exception:
                                    continue
                                normalized = _normalize_live_invalidation_event(envelope, stream_id=last_stream_id)
                                if normalized is None:
                                    continue
                                yield _sse("invalidation", normalized)
                                emitted = True
                    except Exception as exc:
                        if type(exc).__name__ == "TimeoutError":
                            # Timeout de lectura bloqueante: fallback normal, no desactivar stream.
                            pass
                            continue
                        redis_client = None
                        current_app.logger.warning(
                            "[f4-live] stream read failed; switching to heartbeat-only mode (%s: %s)",
                            type(exc).__name__,
                            str(exc),
                        )

                if (now_ts - last_heartbeat_at) >= heartbeat_every_sec:
                    _live_stream_refresh(stream_slot_id)
                    yield _sse(
                        "heartbeat",
                        {
                            "ts": iso_utc_z(),
                            "last_stream_id": last_stream_id,
                            "mode": "streaming" if redis_client is not None else "heartbeat_only",
                        },
                    )
                    last_heartbeat_at = now_ts
                    emitted = True

                if not emitted:
                    time.sleep(0.2)
        except (GeneratorExit, ConnectionError, OSError):
            return
        finally:
            _live_stream_release(stream_slot_id)

    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return Response(generate(), mimetype="text/event-stream", headers=headers)


def _admin_default_role() -> str:
    role = (os.getenv("ADMIN_DEFAULT_ROLE") or "secretaria").strip().lower()
    return role if role in ("owner", "admin", "secretaria") else "secretaria"


def _normalize_staff_role_loose(role_raw) -> str:
    role = (str(role_raw or "").strip().lower())
    if role in ("owner", "admin", "secretaria"):
        return role
    if role in ("secretary", "secre", "secretaría"):
        return "secretaria"
    return ""


def _emergency_hide_prefix() -> str:
    return (os.getenv("EMERGENCY_ADMIN_HIDE_PREFIX") or "emergency_").strip().lower()


def _emergency_hide_username() -> str:
    return (os.getenv("EMERGENCY_ADMIN_USERNAME") or "").strip().lower()


def _is_hidden_emergency_username(username: str) -> bool:
    uname = (username or "").strip().lower()
    if not uname:
        return False
    env_user = _emergency_hide_username()
    if env_user and uname == env_user:
        return True
    pref = _emergency_hide_prefix()
    return bool(pref and uname.startswith(pref))


def _try_breakglass_login(usuario_norm: str, clave: str):
    if not is_breakglass_enabled():
        return None

    ip = get_request_ip()
    ua = request.headers.get("User-Agent") or ""

    if usuario_norm != breakglass_username().strip().lower():
        return None

    if not breakglass_allowed_ip(ip):
        log_breakglass_attempt(False, ip, ua)
        return False

    ok = check_breakglass_password(clave)
    log_breakglass_attempt(ok, ip, ua)
    return bool(ok)


def _audit_log(
    action_type: str,
    entity_type: str | None = None,
    entity_id=None,
    summary: str | None = None,
    metadata: dict | None = None,
    changes: dict | None = None,
    success: bool = True,
    error: str | None = None,
) -> None:
    try:
        log_action(
            action_type=action_type,
            entity_type=entity_type,
            entity_id=entity_id,
            summary=summary,
            metadata=metadata,
            changes=changes,
            success=success,
            error=error,
        )
    except Exception:
        return


def _form_snapshot_payload() -> dict[str, object]:
    payload: dict[str, object] = {}
    sensitive_tokens = {
        "password",
        "password_confirm",
        "clave",
        "token",
        "csrf",
        "secret",
        "telefono",
        "phone",
        "whatsapp",
        "cedula",
        "documento",
        "direccion",
        "email",
        "correo",
    }
    try:
        form_keys = sorted((request.form or {}).keys())
        for idx, key in enumerate(form_keys):
            key_txt = str(key)
            key_low = key_txt.lower()
            if any(token in key_low for token in sensitive_tokens):
                payload[key_txt] = "<redacted>"
                continue
            vals = request.form.getlist(key)
            if len(vals) > 1:
                payload[key_txt] = [str(v)[:120] for v in vals[:10]]
            else:
                payload[key_txt] = str((vals[0] if vals else ""))[:120]
            if idx >= 60:
                payload["_truncated"] = True
                break
    except Exception:
        return {}
    return payload


def _verify_solicitud_saved(
    solicitud_id: int,
    *,
    expected_cliente_id: int | None = None,
    expected_codigo: str | None = None,
    expected_estado: str | None = None,
) -> bool:
    if int(solicitud_id or 0) <= 0:
        return False
    row = Solicitud.query.filter_by(id=int(solicitud_id)).first()
    if not row:
        return False
    if expected_cliente_id is not None and int(getattr(row, "cliente_id", 0) or 0) != int(expected_cliente_id):
        return False
    if expected_codigo is not None and str(getattr(row, "codigo_solicitud", "") or "") != str(expected_codigo):
        return False
    if expected_estado is not None and str(getattr(row, "estado", "") or "") != str(expected_estado):
        return False
    return True


def _cliente_detail_solicitudes_redirect_url(*, cliente_id: int, solicitud_id: int | None = None) -> str:
    base = url_for("admin.detalle_cliente", cliente_id=int(cliente_id))
    sid = int(solicitud_id or 0)
    if sid > 0:
        return f"{base}#sol-{sid}"
    return f"{base}#clienteSolicitudesAsyncScope"


def _execute_form_save(
    *,
    persist_fn,
    verify_fn,
    entity_type: str,
    entity_id,
    summary: str,
    metadata: dict | None = None,
):
    result = execute_robust_save(
        session=db.session,
        persist_fn=persist_fn,
        verify_fn=verify_fn,
        max_retries=2,
        retryable_exceptions=(OperationalError,),
    )
    base_metadata = {
        "fields_sent": _form_snapshot_payload(),
        "attempt_count": int(result.attempts),
    }
    if metadata:
        base_metadata.update(metadata)
    if result.ok:
        _audit_log(
            action_type="FORM_SAVE_OK",
            entity_type=entity_type,
            entity_id=entity_id,
            summary=summary,
            metadata=base_metadata,
            success=True,
        )
        return result
    _audit_log(
        action_type="FORM_SAVE_FAIL",
        entity_type=entity_type,
        entity_id=entity_id,
        summary=summary,
        metadata={**base_metadata, "error_message": (result.error_message or "")[:400]},
        success=False,
        error=result.error_message or "No se pudo guardar correctamente.",
    )
    return result


def _current_staff_role() -> str:
    if not isinstance(current_user, StaffUser):
        return normalize_staff_role(session.get("role"))
    return role_for_user(current_user)


def _owner_only() -> None:
    role = _current_staff_role()
    if not rbac_can(role, "admin:roles"):
        log_permission_denied(user=current_user if isinstance(current_user, StaffUser) else None, required_permission="admin:roles")
        abort(403)


def _ensure_testing_staff_defaults() -> None:
    if not bool(current_app.config.get("TESTING")):
        return
    seed = [
        ("Owner", "owner", "admin123"),
        ("Cruz", "admin", "8998"),
        ("Karla", "secretaria", "9989"),
        ("Anyi", "secretaria", "0931"),
    ]
    changed = False
    try:
        for username, role, password in seed:
            user = StaffUser.query.filter(func.lower(StaffUser.username) == username.lower()).first()
            if user is None:
                user = StaffUser(username=username, role=role, is_active=True)
                user.set_password(password)
                db.session.add(user)
                changed = True
                continue
            if (user.role or "").strip().lower() != role:
                user.role = role
                changed = True
            if not bool(user.is_active):
                user.is_active = True
                changed = True
            # Evita escrituras en cada login: solo rehace hash si la clave cambió.
            if not user.check_password(password):
                user.set_password(password)
                changed = True
        if changed:
            db.session.commit()
    except Exception:
        db.session.rollback()



# ─────────────────────────────────────────────────────────────
# 🔒 Aislamiento real de sesión ADMIN vs CLIENTE + Rate-limit admin
# ─────────────────────────────────────────────────────────────
# Marcador de sesión: si no existe, NO se permite navegar en /admin/*
_ADMIN_SESSION_MARKER = "is_admin_session"
_MFA_VERIFIED_SESSION_KEY = "mfa_verified"
_TRUSTED_DEVICE_COOKIE_NAME = "trusted_device_token"
_TRUSTED_DEVICE_SESSION_REASON = "_trusted_device_reason"


def _staff_mfa_is_enforced() -> bool:
    return mfa_enforced_for_staff(testing=bool(current_app.config.get("TESTING")))


def _staff_user_needs_mfa(user: StaffUser | None) -> bool:
    if not isinstance(user, StaffUser):
        return False
    if not bool(getattr(user, "is_active", False)):
        return False
    if not _staff_mfa_is_enforced():
        return False
    role = (getattr(user, "role", "") or "").strip().lower()
    return staff_role_requires_mfa(role)


def _session_redirect_after_mfa(pending: dict | None = None) -> str:
    default_url = url_for("admin.listar_solicitudes")
    if not isinstance(pending, dict):
        return default_url
    candidate = str(pending.get("next_url") or "").strip()
    if candidate and _is_safe_next(candidate):
        return candidate
    return default_url


def _get_pending_staff_user() -> StaffUser | None:
    pending = session_get_mfa_pending(session)
    raw = pending.get("staff_user_id")
    try:
        sid = int(raw)
    except Exception:
        return None
    if sid <= 0:
        return None
    return StaffUser.query.get(sid)


def _begin_pending_staff_mfa(*, staff_user: StaffUser, source: str, next_url: str) -> str:
    role = _normalize_staff_role_loose(getattr(staff_user, "role", "") or "")
    session.clear()
    session.permanent = True
    session_begin_mfa_pending(
        session,
        staff_user_id=int(staff_user.id),
        username=(staff_user.username or ""),
        role=role or "secretaria",
        next_url=next_url,
        source=source,
    )
    mfa_path, mfa_reason = _staff_user_mfa_bootstrap_path(staff_user)
    if mfa_path == "verify":
        return url_for("admin.mfa_verify")
    _log_staff_mfa_setup_required(
        staff_user,
        reason=mfa_reason,
        source=source,
        path_hint="/admin/mfa/setup",
    )
    session[MFA_SETUP_SECRET_SESSION_KEY] = generate_mfa_secret()
    session.modified = True
    return url_for("admin.mfa_setup")


def _staff_user_mfa_bootstrap_path(staff_user: StaffUser | None) -> tuple[str, str]:
    if not isinstance(staff_user, StaffUser):
        return "setup", "invalid_staff_user"
    if not bool(getattr(staff_user, "mfa_enabled", False)):
        return "setup", "mfa_enabled_false"
    raw_secret = str(getattr(staff_user, "mfa_secret", "") or "").strip()
    if not raw_secret:
        return "setup", "mfa_secret_missing"
    try:
        readable_secret = str(staff_user.get_mfa_secret() or "").strip()
    except Exception:
        readable_secret = ""
    if not readable_secret:
        return "setup", "mfa_secret_unreadable"
    return "verify", "ok"


def _log_staff_mfa_setup_required(
    staff_user: StaffUser | None,
    *,
    reason: str,
    source: str,
    path_hint: str,
) -> None:
    user_id = int(getattr(staff_user, "id", 0) or 0) or None
    username = str(getattr(staff_user, "username", "") or "").strip() or None
    role = str(getattr(staff_user, "role", "") or "").strip().lower() or None
    try:
        current_app.logger.warning(
            "MFA_SETUP_REQUIRED user_id=%s username=%s role=%s reason=%s source=%s path=%s ip=%s",
            user_id,
            username or "",
            role or "",
            reason,
            source,
            path_hint,
            _client_ip(),
        )
    except Exception:
        pass
    try:
        log_auth_event(
            event="MFA_SETUP_REQUIRED",
            status="success",
            user_id=user_id,
            user_identifier=username,
            reason=(reason or "unknown"),
            metadata={
                "path": path_hint,
                "source": source,
                "role": role,
            },
        )
    except Exception:
        pass


def evaluate_staff_trusted_device_decision(
    *,
    staff_user: StaffUser,
    previous_fail_count: int,
    is_testing: bool,
) -> dict:
    trusted_device_allowed = False
    trusted_device_token = _trusted_device_current_token()
    trust_reason = "new_device"
    force_mfa_by_failures = (not is_testing) and (int(previous_fail_count or 0) >= _trusted_device_fail_threshold())
    if force_mfa_by_failures:
        trust_reason = "failed_attempts"
    elif trusted_device_token:
        trusted_device_hash = _trusted_device_token_hash(trusted_device_token)
        if trusted_device_hash and is_trusted_device(staff_user, trusted_device_hash):
            trusted_device_allowed = True
        else:
            trust_reason = _trusted_device_reason_from_session(default="new_device")
    else:
        trust_reason = "missing_cookie"
    return {
        "trusted_device_allowed": bool(trusted_device_allowed),
        "trusted_device_token": (trusted_device_token or "").strip(),
        "trust_reason": trust_reason,
    }


def _trusted_device_cookie_ttl_seconds() -> int:
    days_raw = os.getenv("TRUSTED_DEVICE_COOKIE_DAYS", "30")
    try:
        days = max(1, int(str(days_raw).strip()))
    except Exception:
        days = 30
    return days * 24 * 60 * 60


def _trusted_device_fail_threshold() -> int:
    raw = os.getenv("TRUSTED_DEVICE_FAIL_COUNT_FORCE_2FA", "3")
    try:
        return max(1, int(str(raw).strip()))
    except Exception:
        return 3


def _trusted_device_current_token() -> str:
    return (request.cookies.get(_TRUSTED_DEVICE_COOKIE_NAME) or "").strip()[:255]


def _trusted_device_issue_token() -> str:
    return secrets.token_urlsafe(32)


def _trusted_device_set_cookie(resp, token: str) -> None:
    value = (token or "").strip()
    if not value:
        return
    secure_cookie = not bool(current_app.config.get("TESTING"))
    resp.set_cookie(
        _TRUSTED_DEVICE_COOKIE_NAME,
        value,
        max_age=_trusted_device_cookie_ttl_seconds(),
        httponly=True,
        secure=secure_cookie,
        samesite="Lax",
        path="/",
    )


def _trusted_device_user_agent() -> str:
    return (request.headers.get("User-Agent") or "").strip()[:512]


def _trusted_device_legacy_ip_bucket(ip_raw: str) -> str:
    txt = (ip_raw or "").strip()
    if not txt:
        return "none"
    try:
        parsed = ipaddress.ip_address(txt)
        if parsed.version == 4:
            network = ipaddress.ip_network(f"{parsed}/24", strict=False)
            return str(network.network_address)
        network = ipaddress.ip_network(f"{parsed}/48", strict=False)
        return str(network.network_address)
    except Exception:
        return txt[:64]


def _trusted_device_token_hash(token: str) -> str:
    raw_token = (token or "").strip()
    if not raw_token:
        return ""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _trusted_device_legacy_fingerprint(user: StaffUser, token: str) -> str:
    uid = int(getattr(user, "id", 0) or 0)
    if uid <= 0:
        return ""
    raw_token = (token or "").strip()
    if not raw_token:
        return ""
    ua = _trusted_device_user_agent().lower()
    ip_bucket = _trusted_device_legacy_ip_bucket(_client_ip())
    payload = f"v1|{uid}|{raw_token}|{ua}|{ip_bucket}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _trusted_device_ip_changed_significantly(old_ip: str, new_ip: str) -> bool:
    old_txt = (old_ip or "").strip()
    new_txt = (new_ip or "").strip()
    if not old_txt or not new_txt:
        return True
    if old_txt == new_txt:
        return False
    try:
        old_parsed = ipaddress.ip_address(old_txt)
        new_parsed = ipaddress.ip_address(new_txt)
        if old_parsed.version != new_parsed.version:
            return True
        if old_parsed.version == 4:
            old_net = ipaddress.ip_network(f"{old_parsed}/24", strict=False)
            new_net = ipaddress.ip_network(f"{new_parsed}/24", strict=False)
            return old_net.network_address != new_net.network_address
        old_net = ipaddress.ip_network(f"{old_parsed}/48", strict=False)
        new_net = ipaddress.ip_network(f"{new_parsed}/48", strict=False)
        return old_net.network_address != new_net.network_address
    except Exception:
        return True


def _trusted_device_reason_from_session(default: str = "new_device") -> str:
    reason = str(session.pop(_TRUSTED_DEVICE_SESSION_REASON, default) or "").strip().lower()
    if not reason:
        return default
    return reason[:60]


def is_trusted_device(user: StaffUser, device_token_hash: str) -> bool:
    if not isinstance(user, StaffUser):
        return False
    token_hash = (device_token_hash or "").strip().lower()
    if not token_hash:
        return False
    device = TrustedDevice.query.filter_by(
        user_id=int(user.id),
        device_token_hash=token_hash,
        is_trusted=True,
    ).first()
    if not isinstance(device, TrustedDevice):
        legacy_fp = _trusted_device_legacy_fingerprint(user, _trusted_device_current_token())
        if legacy_fp:
            device = TrustedDevice.query.filter_by(
                user_id=int(user.id),
                device_fingerprint=legacy_fp,
                is_trusted=True,
            ).first()
            if isinstance(device, TrustedDevice):
                device.device_token_hash = token_hash
    if not isinstance(device, TrustedDevice):
        return False
    current_ip = _client_ip()
    if _trusted_device_ip_changed_significantly(device.ip_address or "", current_ip):
        session[_TRUSTED_DEVICE_SESSION_REASON] = "ip_change"
        session.modified = True
        return False
    device.last_used_at = utc_now_naive()
    device.ip_address = current_ip
    device.user_agent = _trusted_device_user_agent()
    if not (device.device_token_hash or "").strip():
        device.device_token_hash = token_hash
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
    return True


def register_trusted_device(user: StaffUser, device_token_hash: str) -> TrustedDevice | None:
    if not isinstance(user, StaffUser):
        return None
    token_hash = (device_token_hash or "").strip().lower()
    if not token_hash:
        return None
    device = TrustedDevice.query.filter_by(
        user_id=int(user.id),
        device_token_hash=token_hash,
    ).first()
    now = utc_now_naive()
    if not isinstance(device, TrustedDevice):
        fallback_fp = _trusted_device_legacy_fingerprint(user, _trusted_device_current_token())
        if fallback_fp:
            device = TrustedDevice.query.filter_by(
                user_id=int(user.id),
                device_fingerprint=fallback_fp,
            ).first()
    if not isinstance(device, TrustedDevice):
        device = TrustedDevice(
            user_id=int(user.id),
            device_fingerprint=token_hash,
            device_token_hash=token_hash,
            created_at=now,
        )
        db.session.add(device)
    else:
        device.device_token_hash = token_hash
        if not (device.device_fingerprint or "").strip():
            device.device_fingerprint = token_hash
    device.is_trusted = True
    device.last_used_at = now
    device.ip_address = _client_ip()
    device.user_agent = _trusted_device_user_agent()
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return None
    return device


def _complete_staff_login_session(
    user_obj,
    *,
    username_for_session: str,
    breakglass_ok: bool,
) -> None:
    session.clear()
    session.permanent = True
    login_user(user_obj, remember=False)
    session[_ADMIN_SESSION_MARKER] = True
    session["usuario"] = str(username_for_session)
    normalized_role = _normalize_staff_role_loose(getattr(user_obj, "role", "") or "")
    session["role"] = normalized_role or (str(getattr(user_obj, "role", "") or "").strip().lower())
    session[_MFA_VERIFIED_SESSION_KEY] = True
    if breakglass_ok:
        set_breakglass_session(session)
    else:
        clear_breakglass_session(session)
    session.modified = True

# Rate-limit global para acciones ADMIN (POST/PUT/PATCH/DELETE)
# Configurable por env:
#   ADMIN_ACTION_MAX=80   (acciones por ventana)
#   ADMIN_ACTION_WINDOW=60 (segundos)
#   ADMIN_ACTION_LOCK=120  (segundos bloqueado si se pasa)
_ADMIN_ACTION_MAX = int((os.getenv("ADMIN_ACTION_MAX") or "80").strip() or 80)
_ADMIN_ACTION_WINDOW = int((os.getenv("ADMIN_ACTION_WINDOW") or "60").strip() or 60)
_ADMIN_ACTION_LOCK = int((os.getenv("ADMIN_ACTION_LOCK") or "120").strip() or 120)
_ADMIN_ACTION_KEY_PREFIX = "admin_action"


def _admin_action_limits(bucket: str = "default"):
    """Devuelve (max, window, lock) por bucket."""
    b = (bucket or "default").strip().lower()

    mx = _ADMIN_ACTION_MAX
    win = _ADMIN_ACTION_WINDOW
    lock = _ADMIN_ACTION_LOCK

    try:
        if b == "pagos":
            mx = int((os.getenv("ADMIN_ACTION_MAX_PAGOS") or str(mx)).strip())
            win = int((os.getenv("ADMIN_ACTION_WINDOW_PAGOS") or str(win)).strip())
        elif b == "solicitudes":
            mx = int((os.getenv("ADMIN_ACTION_MAX_SOL") or str(mx)).strip())
            win = int((os.getenv("ADMIN_ACTION_WINDOW_SOL") or str(win)).strip())
        elif b == "reemplazos":
            mx = int((os.getenv("ADMIN_ACTION_MAX_REEMP") or str(mx)).strip())
            win = int((os.getenv("ADMIN_ACTION_WINDOW_REEMP") or str(win)).strip())
        elif b == "delete":
            mx = int((os.getenv("ADMIN_ACTION_MAX_DEL") or str(mx)).strip())
            win = int((os.getenv("ADMIN_ACTION_WINDOW_DEL") or str(win)).strip())
    except Exception:
        pass

    try:
        lock = int((os.getenv("ADMIN_ACTION_LOCK") or str(lock)).strip())
    except Exception:
        lock = _ADMIN_ACTION_LOCK

    return mx, win, lock


def _admin_action_keys(usuario_norm: str, bucket: str = "default"):
    ip = _client_ip()
    u = (usuario_norm or "").strip().lower()[:64]
    b = (bucket or "default").strip().lower()[:32]
    base = f"{_ADMIN_ACTION_KEY_PREFIX}:{ip}:{u}:{b}"
    return {
        "count": f"{base}:count",
        "lock": f"{base}:lock",
    }


def _sess_action_key(usuario_norm: str, bucket: str = "default") -> str:
    ip = _client_ip()
    u = (usuario_norm or "").strip().lower()[:64]
    b = (bucket or "default").strip().lower()[:32]
    return f"admin_action:{ip}:{u}:{b}"


def _session_action_is_locked(usuario_norm: str, bucket: str = "default") -> bool:
    data = session.get(_sess_action_key(usuario_norm, bucket)) or {}
    locked_until = data.get("locked_until")
    if not locked_until:
        return False
    try:
        return utc_timestamp() < float(locked_until)
    except Exception:
        return False


def _session_action_register(usuario_norm: str, bucket: str, mx: int, win: int, lock: int) -> int:
    key = _sess_action_key(usuario_norm, bucket)
    data = session.get(key) or {}

    now_ts = utc_timestamp()
    window_start = float(data.get("window_start") or 0.0)

    if not window_start or (now_ts - window_start) > win:
        data["window_start"] = now_ts
        data["count"] = 0
        data.pop("locked_until", None)

    if data.get("locked_until"):
        session[key] = data
        return int(data.get("count") or 0)

    data["count"] = int(data.get("count") or 0) + 1

    if int(data["count"]) > mx:
        data["locked_until"] = now_ts + lock

    session[key] = data
    return int(data.get("count") or 0)


def _session_action_lock_left_seconds(usuario_norm: str, bucket: str = "default") -> int:
    data = session.get(_sess_action_key(usuario_norm, bucket)) or {}
    locked_until = data.get("locked_until")
    if not locked_until:
        return 0
    try:
        left = int(float(locked_until) - utc_timestamp())
        return max(0, left)
    except Exception:
        return 0


def _admin_action_is_locked(usuario_norm: str, bucket: str = "default") -> bool:
    if not _operational_rate_limits_enabled():
        return False
    if _cache_ok():
        keys = _admin_action_keys(usuario_norm, bucket)
        try:
            return bool(cache.get(keys["lock"]))
        except Exception:
            return _session_action_is_locked(usuario_norm, bucket)
    return _session_action_is_locked(usuario_norm, bucket)


def _admin_action_lock_left_seconds(usuario_norm: str, bucket: str = "default") -> int:
    mx, win, lock = _admin_action_limits(bucket)

    if _cache_ok():
        keys = _admin_action_keys(usuario_norm, bucket)
        try:
            left = cache.get(keys["lock"])
            try:
                return max(0, int(left or 0))
            except Exception:
                return lock
        except Exception:
            return _session_action_lock_left_seconds(usuario_norm, bucket)

    return _session_action_lock_left_seconds(usuario_norm, bucket)


def _admin_action_register(usuario_norm: str, bucket: str = "default") -> int:
    if not _operational_rate_limits_enabled():
        return 0
    mx, win, lock = _admin_action_limits(bucket)

    if _cache_ok():
        keys = _admin_action_keys(usuario_norm, bucket)
        try:
            if cache.get(keys["lock"]):
                return int(cache.get(keys["count"]) or 0)

            n = int(cache.get(keys["count"]) or 0) + 1
            cache.set(keys["count"], n, timeout=win)

            if n > mx:
                cache.set(keys["lock"], lock, timeout=lock)
            return n
        except Exception:
            return _session_action_register(usuario_norm, bucket, mx=mx, win=win, lock=lock)

    return _session_action_register(usuario_norm, bucket, mx=mx, win=win, lock=lock)


#
# ─────────────────────────────────────────────────────────────
# ✅ Decorador CANÓNICO: rate-limit por ruta (admin_action_limit)
# IMPORTANTE: debe existir ANTES de cualquier uso @admin_action_limit(...)
# ─────────────────────────────────────────────────────────────

def admin_action_limit(bucket: str = "default", max_actions: int | None = None, window_sec: int | None = None):
    """Rate-limit por IP + usuario para rutas ADMIN.

    - bucket: agrupa acciones (ej: 'pagos', 'solicitudes', 'delete', 'tareas')
    - max_actions: override del máximo en la ventana
    - window_sec: override del tamaño de la ventana (segundos)

    Usa cache (Flask-Caching) si está disponible; si no, usa sesión como fallback.
    """
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not _operational_rate_limits_enabled():
                return fn(*args, **kwargs)
            try:
                # usuario normalizado
                try:
                    uname = (current_user.get_id() if current_user else "") or ""
                except Exception:
                    uname = getattr(current_user, "id", "") or ""

                usuario_norm = str(uname).strip().lower()[:64]
                b = (bucket or "default").strip().lower()[:32]

                # límites base por bucket
                mx, win, lock = _admin_action_limits(b)

                # overrides por ruta
                if max_actions is not None:
                    try:
                        mx = int(max_actions)
                    except Exception:
                        pass
                if window_sec is not None:
                    try:
                        win = int(window_sec)
                    except Exception:
                        pass

                # nunca permitir valores absurdos
                try:
                    mx = max(1, min(int(mx), 5000))
                except Exception:
                    mx = 80
                try:
                    win = max(1, min(int(win), 3600))
                except Exception:
                    win = 60
                try:
                    lock = max(5, min(int(lock), 3600))
                except Exception:
                    lock = 120

                # --- con CACHE (preferido) ---
                if _cache_ok():
                    keys = _admin_action_keys(usuario_norm, bucket=b)
                    try:
                        # locked?
                        left = cache.get(keys["lock"])
                        if left:
                            try:
                                left_i = int(left)
                            except Exception:
                                left_i = lock

                            wants_json = False
                            try:
                                wants_json = (request.is_json or ("application/json" in (request.headers.get("Accept") or "")))
                            except Exception:
                                wants_json = False

                            if wants_json:
                                return jsonify({
                                    "ok": False,
                                    "error": "rate_limited",
                                    "bucket": b,
                                    "retry_after_sec": max(10, left_i),
                                }), 429

                            flash(f"Demasiadas acciones seguidas ({b}). Intenta de nuevo en {max(10, left_i)} segundos.", "warning")
                            return redirect(url_for("admin.listar_clientes"))

                        # count
                        n = int(cache.get(keys["count"]) or 0) + 1
                        cache.set(keys["count"], n, timeout=win)

                        if n > mx:
                            cache.set(keys["lock"], lock, timeout=lock)

                            wants_json = False
                            try:
                                wants_json = (request.is_json or ("application/json" in (request.headers.get("Accept") or "")))
                            except Exception:
                                wants_json = False

                            if wants_json:
                                return jsonify({
                                    "ok": False,
                                    "error": "rate_limited",
                                    "bucket": b,
                                    "retry_after_sec": max(10, lock),
                                }), 429

                            flash(f"Demasiadas acciones seguidas ({b}). Intenta de nuevo en {max(10, lock)} segundos.", "warning")
                            return redirect(url_for("admin.listar_clientes"))

                        return fn(*args, **kwargs)
                    except Exception:
                        # si cache falla, cae a sesión
                        pass

                # --- fallback por SESIÓN ---
                try:
                    key = _sess_action_key(usuario_norm, bucket=b)
                    data = session.get(key) or {}

                    now_ts = utc_timestamp()
                    window_start = float(data.get("window_start") or 0.0)

                    if not window_start or (now_ts - window_start) > win:
                        data["window_start"] = now_ts
                        data["count"] = 0
                        data.pop("locked_until", None)

                    locked_until = data.get("locked_until")
                    if locked_until:
                        try:
                            left = int(float(locked_until) - now_ts)
                        except Exception:
                            left = lock
                        left = max(0, left)

                        wants_json = False
                        try:
                            wants_json = (request.is_json or ("application/json" in (request.headers.get("Accept") or "")))
                        except Exception:
                            wants_json = False

                        if wants_json:
                            return jsonify({
                                "ok": False,
                                "error": "rate_limited",
                                "bucket": b,
                                "retry_after_sec": max(10, left or 10),
                            }), 429

                        flash(f"Demasiadas acciones seguidas ({b}). Intenta de nuevo en {max(10, left or 10)} segundos.", "warning")
                        return redirect(url_for("admin.listar_clientes"))

                    data["count"] = int(data.get("count") or 0) + 1

                    if int(data["count"]) > mx:
                        data["locked_until"] = now_ts + lock
                        session[key] = data

                        wants_json = False
                        try:
                            wants_json = (request.is_json or ("application/json" in (request.headers.get("Accept") or "")))
                        except Exception:
                            wants_json = False

                        if wants_json:
                            return jsonify({
                                "ok": False,
                                "error": "rate_limited",
                                "bucket": b,
                                "retry_after_sec": max(10, lock),
                            }), 429

                        flash(f"Demasiadas acciones seguidas ({b}). Intenta de nuevo en {max(10, lock)} segundos.", "warning")
                        return redirect(url_for("admin.listar_clientes"))

                    session[key] = data
                except Exception:
                    # si todo falla, no rompemos el flujo
                    pass

            except Exception:
                # si algo raro pasa, no rompemos la ruta
                pass

            return fn(*args, **kwargs)
        return wrapper
    return deco

@admin_bp.before_request
def _admin_guard_and_rate_limit():
    """
    1) Aislamiento real de sesión Admin:
       - Solo permite navegar por /admin/* si:
         - current_user es AdminUser
         - session[_ADMIN_SESSION_MARKER] == True
       - Si no, logout y manda a /admin/login

    2) Rate-limit global para acciones sensibles:
       - Solo aplica a métodos mutadores: POST/PUT/PATCH/DELETE
       - Excluye: login/logout/MFA
    """
    try:
        # Endpoint actual
        ep = (request.endpoint or "").strip()

        # Permitir siempre rutas públicas del admin blueprint (login)
        # y utilidades (logout, ping, live)
        allow_eps = {
            "admin.login",
            "admin.logout",
            "admin.mfa_setup",
            "admin.mfa_verify",
            "admin.mfa_cancel",
        }
        if ep in allow_eps:
            return None

        pending = session_get_mfa_pending(session)
        if pending:
            pending_user = _get_pending_staff_user()
            if pending_user is None or not bool(getattr(pending_user, "is_active", False)):
                session_clear_mfa_pending(session)
                return redirect(url_for("admin.login"))
            mfa_path, mfa_reason = _staff_user_mfa_bootstrap_path(pending_user)
            if mfa_path == "verify":
                return redirect(url_for("admin.mfa_verify"))
            _log_staff_mfa_setup_required(
                pending_user,
                reason=mfa_reason,
                source="admin_guard",
                path_hint="/admin/mfa/setup",
            )
            return redirect(url_for("admin.mfa_setup"))

        # Si NO hay usuario logueado, que flask-login maneje @login_required más abajo
        # (pero aquí evitamos que un cliente autenticado con otra sesión se cuele).
        if not current_user or not getattr(current_user, "is_authenticated", False):
            return None

        # Debe ser identidad ADMIN válida + sesión marcada como admin.
        # OJO: NO usamos isinstance(AdminUser) porque flask-login reconstruye el usuario
        # vía user_loader y puede no devolver la misma clase.
        def _is_admin_identity() -> bool:
            try:
                role = role_for_user(current_user)
                if is_breakglass_user_obj(current_user):
                    return (role == "admin") and is_breakglass_session_valid(session)

                if isinstance(current_user, StaffUser):
                    return bool(current_user.is_active) and role in ("owner", "admin", "secretaria")
                return False
            except Exception:
                return False

        is_admin_user = _is_admin_identity()
        is_admin_session = bool(session.get(_ADMIN_SESSION_MARKER))

        if (not is_admin_user) or (not is_admin_session):
            try:
                logout_user()
            except Exception:
                pass
            try:
                session.clear()
            except Exception:
                pass
            return redirect(url_for("admin.login"))

        sess_state = touch_staff_session(
            user=current_user,
            flask_session=session,
            path=(request.full_path or request.path or ""),
            endpoint=(request.endpoint or ""),
        )
        if not bool(sess_state.get("ok")) and sess_state.get("reason") in {"revoked", "backplane_unavailable"}:
            reason = str(sess_state.get("reason") or "").strip().lower()
            if reason == "backplane_unavailable" and not bool(current_app.config.get("DISTRIBUTED_BACKPLANE_REQUIRED", False)):
                try:
                    current_app.logger.warning(
                        "[admin-session] backplane unavailable; fail-open in degraded mode for endpoint=%s path=%s",
                        request.endpoint,
                        request.path,
                    )
                except Exception:
                    pass
            else:
                try:
                    logout_user()
                except Exception:
                    pass
                try:
                    session.clear()
                except Exception:
                    pass
                if reason == "backplane_unavailable":
                    flash("Control de sesión no disponible por degradación de infraestructura. Reintenta en unos segundos.", "danger")
                    return redirect(url_for("admin.login")), 503
                flash("Tu sesión fue cerrada por administración.", "warning")
                return redirect(url_for("admin.login"))

        if isinstance(current_user, StaffUser) and _staff_user_needs_mfa(current_user):
            if _MFA_VERIFIED_SESSION_KEY not in session:
                # Compatibilidad: sesiones previas al despliegue MFA quedan válidas hasta logout.
                session[_MFA_VERIFIED_SESSION_KEY] = True
                session.modified = True
            if not bool(session.get(_MFA_VERIFIED_SESSION_KEY)):
                try:
                    logout_user()
                except Exception:
                    pass
                try:
                    session.clear()
                except Exception:
                    pass
                flash("Debes completar MFA para acceder al panel.", "warning")
                return redirect(url_for("admin.login"))

        if _should_auto_touch_presence():
            _touch_staff_presence(current_path=request.path, page_title=(request.endpoint or request.path))

        required_permission = permission_required_for_path(request.path or "")
        # Live pings de presencia/locks tienen política propia por capability y deben permitir staff operativo.
        if request.endpoint in {"admin.monitoreo_presence_ping", "admin.seguridad_locks_ping"}:
            required_permission = None
        if required_permission:
            role = role_for_user(current_user)
            if not rbac_can(role, required_permission):
                log_permission_denied(user=current_user, required_permission=required_permission)
                if _admin_async_wants_json():
                    login_url = url_for("admin.login", next=(request.full_path or request.path))
                    return jsonify(_admin_async_payload(
                        success=False,
                        message="No tienes permisos para esta acción.",
                        category="danger",
                        redirect_url=login_url,
                        error_code="forbidden",
                    )), 403
                abort(403)

        # Rate-limit solo para acciones que cambian cosas
        if (
            _operational_rate_limits_enabled()
            and _admin_global_action_guard_enabled()
            and request.method in ("POST", "PUT", "PATCH", "DELETE")
        ):
            # Endpoints internos de telemetría/live ya tienen su propio rate-limit
            # y no deben contaminar el bucket global de acciones administrativas.
            ep_l = (ep or "").strip().lower()
            path_l = (request.path or "").strip().lower()
            if ep_l in {
                "admin.monitoreo_presence_ping",
                "admin.seguridad_locks_ping",
                "admin.live_observability_ingest",
            }:
                return None
            if path_l in {
                "/admin/monitoreo/presence/ping",
                "/admin/seguridad/locks/ping",
                "/admin/live/observability",
            }:
                return None

            usuario_norm = ""
            try:
                usuario_norm = (current_user.get_id() or "").strip().lower()
            except Exception:
                usuario_norm = ""

            # Bucket automático según endpoint/path/método
            if request.method == "DELETE" or "eliminar" in ep_l or "/eliminar" in path_l:
                bucket = "delete"
            elif "pago" in ep_l or "/pago" in path_l or "abono" in ep_l or "/abono" in path_l:
                bucket = "pagos"
            elif "reemplazo" in ep_l or "/reemplazo" in path_l:
                bucket = "reemplazos"
            elif "solicitud" in ep_l or "/solicitud" in path_l:
                bucket = "solicitudes"
            elif "tarea" in ep_l or "/tarea" in path_l:
                bucket = "tareas"
            else:
                bucket = "default"

            if _admin_action_is_locked(usuario_norm, bucket=bucket):
                left = _admin_action_lock_left_seconds(usuario_norm, bucket=bucket)
                # Mensaje corto y claro
                flash(f"Demasiadas acciones seguidas. Intenta de nuevo en {max(10, left)} segundos.", "danger")
                return redirect(url_for("admin.listar_clientes"))

            _admin_action_register(usuario_norm, bucket=bucket)

    except Exception:
        # Nunca rompemos el request por seguridad
        return None

    return None



#
# —— Anti fuerza-bruta (cache) por IP + usuario ——
# Nota: usamos `cache` (Flask-Caching) para que el lock sea real incluso si el usuario cambia de navegador.
# Fallback seguro: si `cache` no está disponible o falla, usamos sesión (NO rompe el login).
# Configurable por env: ADMIN_LOGIN_MAX_INTENTOS y ADMIN_LOGIN_LOCK_MINUTOS.
_ADMIN_LOGIN_MAX_INTENTOS = int((os.getenv("ADMIN_LOGIN_MAX_INTENTOS") or "10").strip() or 10)
_ADMIN_LOGIN_LOCK_MINUTOS = int((os.getenv("ADMIN_LOGIN_LOCK_MINUTOS") or "10").strip() or 10)
_ADMIN_LOGIN_KEY_PREFIX   = "admin_login"


def _client_ip() -> str:
    """Obtiene la IP del cliente.
    - En local: NO confía en X-Forwarded-For.
    - En producción detrás de proxy: solo confía si TRUST_XFF=1.
    """
    trust_xff = (os.getenv("TRUST_XFF", "0").strip() == "1")
    if trust_xff:
        xff = (request.headers.get("X-Forwarded-For") or "").strip()
        if xff:
            return xff.split(",")[0].strip()[:64]
    return (request.remote_addr or "0.0.0.0").strip()[:64]


def _admin_login_max_intentos() -> int:
    # permite cambiar en runtime si hace falta
    try:
        return int((os.getenv("ADMIN_LOGIN_MAX_INTENTOS") or str(_ADMIN_LOGIN_MAX_INTENTOS)).strip())
    except Exception:
        return _ADMIN_LOGIN_MAX_INTENTOS


def _admin_login_lock_minutos() -> int:
    try:
        return int((os.getenv("ADMIN_LOGIN_LOCK_MINUTOS") or str(_ADMIN_LOGIN_LOCK_MINUTOS)).strip())
    except Exception:
        return _ADMIN_LOGIN_LOCK_MINUTOS


def _admin_login_keys(usuario_norm: str):
    ip = _client_ip()
    u = (usuario_norm or "").strip().lower()[:64]
    base = f"{_ADMIN_LOGIN_KEY_PREFIX}:{ip}:{u}"
    return {
        "fail": f"{base}:fail",
        "lock": f"{base}:lock",
    }


def _cache_ok() -> bool:
    """Retorna True si el cache está disponible y operativo."""
    try:
        return bool(bp_healthcheck(strict=False))
    except Exception:
        return False


def _sess_key(usuario_norm: str) -> str:
    ip = _client_ip()
    u = (usuario_norm or "").strip().lower()[:64]
    return f"admin_login_fail:{ip}:{u}"


def _session_is_locked(usuario_norm: str) -> bool:
    data = session.get(_sess_key(usuario_norm)) or {}
    locked_until = data.get("locked_until")
    if not locked_until:
        return False
    try:
        return utc_timestamp() < float(locked_until)
    except Exception:
        return False


def _session_fail_count(usuario_norm: str) -> int:
    data = session.get(_sess_key(usuario_norm)) or {}
    try:
        return int(data.get("tries") or 0)
    except Exception:
        return 0


def _session_lock(usuario_norm: str):
    key = _sess_key(usuario_norm)
    data = session.get(key) or {}
    data["locked_until"] = utc_timestamp() + (_admin_login_lock_minutos() * 60)
    session[key] = data


def _session_register_fail(usuario_norm: str) -> int:
    key = _sess_key(usuario_norm)
    data = session.get(key) or {}
    tries = int(data.get("tries") or 0) + 1
    data["tries"] = tries
    # lock cuando llega al máximo
    if tries >= _admin_login_max_intentos():
        data["locked_until"] = utc_timestamp() + (_admin_login_lock_minutos() * 60)
    session[key] = data
    return tries


def _session_reset_fail(usuario_norm: str):
    try:
        session.pop(_sess_key(usuario_norm), None)
    except Exception:
        pass


def _admin_is_locked(usuario_norm: str) -> bool:
    """Chequea lock (cache si sirve, si no sesión)."""
    if not _operational_rate_limits_enabled():
        return False
    if _cache_ok():
        keys = _admin_login_keys(usuario_norm)
        try:
            return bool(cache.get(keys["lock"]))
        except Exception:
            # si falla cache en runtime, cae a sesión
            return _session_is_locked(usuario_norm)
    return _session_is_locked(usuario_norm)


def _admin_lock(usuario_norm: str):
    """Activa lock (cache si sirve, si no sesión)."""
    if _cache_ok():
        keys = _admin_login_keys(usuario_norm)
        try:
            cache.set(keys["lock"], True, timeout=_admin_login_lock_minutos() * 60)
            return
        except Exception:
            pass
    _session_lock(usuario_norm)


def _admin_fail_count(usuario_norm: str) -> int:
    """Conteo de fallos (cache si sirve, si no sesión)."""
    if _cache_ok():
        keys = _admin_login_keys(usuario_norm)
        try:
            return int(cache.get(keys["fail"]) or 0)
        except Exception:
            return _session_fail_count(usuario_norm)
    return _session_fail_count(usuario_norm)


def _admin_register_fail(usuario_norm: str) -> int:
    """Registra intento fallido y bloquea al llegar al máximo."""
    if not _operational_rate_limits_enabled():
        return 0
    if _cache_ok():
        keys = _admin_login_keys(usuario_norm)
        n = _admin_fail_count(usuario_norm) + 1
        try:
            cache.set(keys["fail"], n, timeout=_admin_login_lock_minutos() * 60)
        except Exception:
            # cae a sesión si cache falla
            return _session_register_fail(usuario_norm)

        if n >= _admin_login_max_intentos():
            _admin_lock(usuario_norm)
        return n

    return _session_register_fail(usuario_norm)


def _admin_reset_fail(usuario_norm: str):
    """Limpia contadores y locks."""
    if _cache_ok():
        keys = _admin_login_keys(usuario_norm)
        try:
            cache.delete(keys["fail"])
            cache.delete(keys["lock"])
        except Exception:
            pass
    _session_reset_fail(usuario_norm)


def _clear_security_layer_lock_admin(endpoint: str = "/admin/login", usuario: str = ""):
    """Limpia el lock global (utils/security_layer.py) si está registrado.
    Soporta limpiar por IP + endpoint + usuario.
    """
    try:
        clear_fn = current_app.extensions.get("clear_login_attempts")
        if callable(clear_fn):
            ip = _client_ip()
            ep = (endpoint or "/admin/login").strip() or "/admin/login"
            uname = (usuario or "").strip()
            try:
                if uname:
                    clear_fn(ip, ep, uname)
                else:
                    clear_fn(ip, ep)
            except TypeError:
                clear_fn(ip)
    except Exception:
        pass


def _clear_security_layer_login_counters_admin(
    *,
    endpoint: str = "/admin/login",
    input_identifier: str = "",
    staff_user: StaffUser | None = None,
):
    """
    Limpia contadores de login para todos los identificadores válidos del intento:
    - identificador ingresado (username/email)
    - username canónico del usuario autenticado
    - email canónico del usuario autenticado
    """
    candidates: list[str] = []
    input_norm = (input_identifier or "").strip().lower()
    if input_norm:
        candidates.append(input_norm)

    if isinstance(staff_user, StaffUser):
        username_norm = str(getattr(staff_user, "username", "") or "").strip().lower()
        email_norm = str(getattr(staff_user, "email", "") or "").strip().lower()
        if username_norm:
            candidates.append(username_norm)
        if email_norm:
            candidates.append(email_norm)

    seen: set[str] = set()
    for ident in candidates:
        if not ident or ident in seen:
            continue
        seen.add(ident)
        _clear_security_layer_lock_admin(endpoint=endpoint, usuario=ident)


def _is_safe_next(target: str) -> bool:
    """Permite solo redirects internos (sin dominio externo)."""
    if not target:
        return False
    try:
        from urllib.parse import urlparse
        ref = urlparse(request.host_url)
        test = urlparse(target)
        if not test.netloc and test.path.startswith("/"):
            return True
        return (test.scheme, test.netloc) == (ref.scheme, ref.netloc)
    except Exception:
        return False


def _safe_next_url(fallback: str) -> str:
    nxt = (request.args.get("next") or request.form.get("next") or "").strip()
    return nxt if _is_safe_redirect_url(nxt) else fallback


def _reset_inicio_seguimiento_si_reactiva(s, now: datetime):
    """Si una solicitud se (re)activa para seguimiento, reinicia `fecha_inicio_seguimiento`.

    Esto evita que solicitudes viejas “arrastren” días viejos al reactivarlas.

    Nota: esta función es defensiva (solo actúa si el modelo tiene el atributo).
    """
    if not hasattr(s, 'fecha_inicio_seguimiento'):
        return

    estado = (getattr(s, 'estado', '') or '').strip().lower()

    # Estados que cuentan como "seguimiento"
    tracking_states = {'proceso', 'activa', 'reemplazo'}

    if estado in tracking_states:
        # Si se llama al (re)activar, queremos resetear el inicio del seguimiento.
        s.fecha_inicio_seguimiento = now


def _pasaje_copy_phrase_from_solicitud(s: Solicitud) -> str:
    """Frase de pasaje para textos de copiado: legado booleano + texto libre."""
    mode, other_text = read_pasaje_mode_text(
        pasaje_aporte=getattr(s, "pasaje_aporte", False),
        detalles_servicio=getattr(s, "detalles_servicio", None),
        nota_cliente=getattr(s, "nota_cliente", ""),
    )

    custom = str(other_text or "").strip()
    if mode == "otro" and custom:
        return custom
    if mode == "aparte":
        return "incluye ayuda de pasaje"
    return "no incluye ayuda de pasaje"


def _pasaje_operativo_phrase_from_solicitud(s: Solicitud) -> str:
    """
    Frase de pasaje para copiado operativo interno/publicar.
    Regla unificada:
    - aparte  -> "Más ayuda del pasaje"
    - incluido -> "Pasaje incluido"
    - otro + texto -> texto libre exacto
    """
    mode, other_text = read_pasaje_mode_text(
        pasaje_aporte=getattr(s, "pasaje_aporte", False),
        detalles_servicio=getattr(s, "detalles_servicio", None),
        nota_cliente=getattr(s, "nota_cliente", ""),
    )
    custom = str(other_text or "").strip()
    if mode == "otro" and custom:
        return custom
    if mode == "aparte":
        return "Más ayuda del pasaje"
    return "Pasaje incluido"


def build_resumen_cliente_solicitud(s: Solicitud) -> str:
    """
    Arma un resumen limpio y entendible de la solicitud para compartir con el cliente.
    Formato pensado para WhatsApp / correo: con emojis, espacios y todo organizado.
    """
    # Para mapear funciones (códigos -> etiquetas legibles)
    try:
        form_tmp = AdminSolicitudForm()
        FUNCIONES_LABELS = {code: label for code, label in (getattr(form_tmp, "funciones", None).choices or [])}
    except Exception:
        FUNCIONES_LABELS = {}

    # Campos base
    codigo        = _s(getattr(s, 'codigo_solicitud', None))
    ciudad_sector = _s(getattr(s, 'ciudad_sector', None))
    rutas         = _s(getattr(s, 'rutas_cercanas', None))
    modalidad_raw = _s(getattr(s, 'modalidad_trabajo', None))
    modalidad     = canonicalize_modalidad_trabajo(modalidad_raw) if modalidad_raw else ""
    edad_req_raw  = getattr(s, 'edad_requerida', None)
    experiencia   = _s(getattr(s, 'experiencia', None))
    horario       = _s(getattr(s, 'horario', None))
    nota_cli      = _s(getattr(s, 'nota_cliente', None))

    # Edad requerida (suele estar como lista de labels)
    edad_list = _as_list(edad_req_raw)
    edad_txt  = ", ".join(edad_list) if edad_list else ""

    # Funciones (códigos -> etiquetas)
    raw_fun_codes = _unique_keep_order(_as_list(getattr(s, 'funciones', None)))
    fun_labels = []
    for code in raw_fun_codes:
        if code == 'otro':
            continue
        label = FUNCIONES_LABELS.get(code, code)
        if label:
            fun_labels.append(label)

    otros_fun = _s(getattr(s, 'funciones_otro', None))
    if otros_fun:
        fun_labels.append(otros_fun)

    funciones_txt = format_funciones(fun_labels, otros_fun)

    # Hogar
    tipo_lugar   = _s(getattr(s, 'tipo_lugar', None))
    habitaciones = _s(getattr(s, 'habitaciones', None))
    banos_txt    = _fmt_banos(getattr(s, 'banos', None))

    # Áreas comunes
    areas_raw   = _as_list(getattr(s, 'areas_comunes', None))
    area_otro   = _s(getattr(s, 'area_otro', None))
    if area_otro:
        areas_raw.append(area_otro)
    areas_txt = ", ".join(_unique_keep_order([x for x in (_norm_area(a) for a in areas_raw) if x])) if areas_raw else ""

    # Familia
    adultos    = _s(getattr(s, 'adultos', None))
    ninos_val  = _s(getattr(s, 'ninos', None))
    edades_n   = _s(getattr(s, 'edades_ninos', None))
    mascota    = _s(getattr(s, 'mascota', None))

    # Dinero
    sueldo_raw    = getattr(s, 'sueldo', None)
    sueldo_txt    = _format_money_usd(sueldo_raw)
    pasaje_texto = _pasaje_copy_phrase_from_solicitud(s)

    lineas = []

    # Encabezado
    if codigo:
        lineas.append(f"🧾 Resumen de su solicitud ({codigo})")
    else:
        lineas.append("🧾 Resumen de su solicitud")
    lineas.append("")

    # Ubicación / modalidad
    if ciudad_sector:
        lineas.append(f"📍 Ciudad / Sector: {ciudad_sector}")
    if rutas:
        lineas.append(f"🚌 Ruta más cercana: {rutas}")
    if modalidad:
        lineas.append(f"💼 Modalidad: {modalidad}")
    if edad_txt:
        lineas.append(f"👤 Edad requerida: {edad_txt}")
    if horario:
        lineas.append(f"⏰ Horario: {horario}")
    if experiencia:
        lineas.append(f"⭐ Experiencia solicitada: {experiencia}")
    lineas.append("")

    # Hogar
    lineas.append("🏠 Detalles del hogar:")
    hogar_sub = []
    if tipo_lugar:
        hogar_sub.append(f"• Tipo de lugar: {tipo_lugar}")
    if habitaciones:
        hogar_sub.append(f"• Habitaciones: {habitaciones}")
    if banos_txt:
        hogar_sub.append(f"• Baños: {banos_txt}")
    if areas_txt:
        hogar_sub.append(f"• Áreas comunes: {areas_txt}")

    if hogar_sub:
        lineas.extend(hogar_sub)
    else:
        lineas.append("• (No se especificaron detalles del hogar)")
    lineas.append("")

    # Familia
    lineas.append("👨‍👩‍👧‍👦 Composición del hogar:")
    fam_sub = []
    if adultos:
        fam_sub.append(f"• Adultos en casa: {adultos}")
    if ninos_val:
        if edades_n:
            fam_sub.append(f"• Niños: {ninos_val} (edades: {edades_n})")
        else:
            fam_sub.append(f"• Niños: {ninos_val}")
    if mascota:
        fam_sub.append(f"• Mascotas: {mascota}")

    if fam_sub:
        lineas.extend(fam_sub)
    else:
        lineas.append("• (No se especificó información de adultos/niños/mascotas)")
    lineas.append("")

    # Funciones
    lineas.append("🧹 Funciones principales:")
    if funciones_txt:
        lineas.append(f"• {funciones_txt}")
    else:
        lineas.append("• (No se especificaron funciones en detalle)")
    lineas.append("")

    # Dinero
    lineas.append("💰 Oferta económica:")
    if sueldo_txt:
        lineas.append(f"• Sueldo: {sueldo_txt} mensual, {pasaje_texto}")
    else:
        lineas.append("• (No se especificó sueldo)")

    lineas.append("")

    # Nota del cliente
    if nota_cli:
        lineas.append("📝 Nota adicional del cliente:")
        lineas.append(f"{nota_cli}")
        lineas.append("")

    return "\n".join(lineas).rstrip()


@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Login admin: StaffUser en BD + breakglass."""
    error = None
    is_testing = bool(current_app.config.get("TESTING"))

    if request.method == 'POST':
        if is_testing:
            try:
                _ensure_testing_staff_defaults()
            except Exception:
                pass

        # Honeypot (opcional). Si el template no lo tiene, no afecta.
        if (request.form.get('website') or '').strip():
            return "", 400

        usuario_raw = (request.form.get('usuario') or '').strip()[:64]
        clave       = (request.form.get('clave') or '').strip()[:128]
        usuario_norm = (usuario_raw or '').strip().lower()

        # Si está bloqueado por IP+usuario
        if (not is_testing) and _admin_is_locked(usuario_norm):
            log_auth_event(
                event="STAFF_LOGIN_BLOCKED",
                status="fail",
                user_identifier=usuario_norm or None,
                reason="admin_login_lock_active",
                metadata={"path": "/admin/login"},
            )
            mins = _admin_login_lock_minutos()
            error = f'Has excedido el máximo de intentos. Intenta de nuevo en {mins} minutos.'
            return render_template('admin/login.html', error=error), 429

        auth_ok = False
        authenticated_user = None
        authenticated_username = usuario_raw

        # 1) Intento principal: staff_users (BD) por username o email
        staff_user = None
        staff_lookup_error = False
        try:
            staff_user = StaffUser.query.filter(
                or_(
                    func.lower(StaffUser.username) == usuario_norm,
                    func.lower(StaffUser.email) == usuario_norm,
                )
            ).first()
        except Exception:
            staff_lookup_error = True
            try:
                current_app.logger.exception("LOGIN_DB_LOOKUP_ERROR /admin/login usuario=%s", usuario_norm)
            except Exception:
                pass
            staff_user = None

        staff_password_ok = False
        auth_reject_reason = "staff_not_found"
        if staff_user and staff_user.is_active:
            try:
                staff_password_ok = bool(staff_user.check_password(clave))
            except Exception:
                staff_password_ok = False
        if staff_user:
            if not bool(getattr(staff_user, "is_active", False)):
                auth_reject_reason = "staff_inactive"
            elif not bool(staff_password_ok):
                auth_reject_reason = "password_mismatch"
        if staff_user and staff_user.is_active and staff_password_ok:
            auth_ok = True
            authenticated_user = staff_user
            authenticated_username = staff_user.username
            auth_reject_reason = ""

        # 2) Breakglass por ENV (emergencia)
        breakglass_ok = False
        if not auth_ok:
            bg = _try_breakglass_login(usuario_norm, clave)
            breakglass_ok = bool(bg is True)
            if breakglass_ok:
                auth_ok = True
                authenticated_user = build_breakglass_user()
                authenticated_username = breakglass_username()

        if _login_debug_enabled():
            try:
                current_app.logger.warning(
                    "LOGIN_DEBUG_ADMIN %s",
                    json.dumps(
                        {
                            "route": "/admin/login",
                            "usuario_input": usuario_raw,
                            "usuario_norm": usuario_norm,
                            "locked": bool((not is_testing) and _admin_is_locked(usuario_norm)),
                            "staff_lookup_error": bool(staff_lookup_error),
                            "staff_found": bool(staff_user),
                            "staff_id": int(staff_user.id) if isinstance(staff_user, StaffUser) else None,
                            "staff_role": (getattr(staff_user, "role", None) if staff_user else None),
                            "staff_active": bool(getattr(staff_user, "is_active", False)) if staff_user else False,
                            "staff_password_ok": bool(staff_password_ok),
                            "breakglass_ok": bool(breakglass_ok),
                            "auth_ok": bool(auth_ok),
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                )
            except Exception:
                pass

        if staff_lookup_error:
            error = "Error temporal validando credenciales. Intenta de nuevo."
            return render_template('admin/login.html', error=error), 503

        if auth_ok and authenticated_user is not None:
            previous_fail_count = 0
            if not is_testing:
                try:
                    previous_fail_count = int(_admin_fail_count(usuario_norm) or 0)
                except Exception:
                    previous_fail_count = 0
            # Reset locks después de validar credenciales correctas.
            _admin_reset_fail(usuario_norm)
            _clear_security_layer_login_counters_admin(
                endpoint="/admin/login",
                input_identifier=usuario_norm,
                staff_user=(authenticated_user if isinstance(authenticated_user, StaffUser) else None),
            )

            if isinstance(authenticated_user, StaffUser) and _staff_user_needs_mfa(authenticated_user):
                trust_eval = evaluate_staff_trusted_device_decision(
                    staff_user=authenticated_user,
                    previous_fail_count=previous_fail_count,
                    is_testing=is_testing,
                )
                trusted_device_allowed = bool(trust_eval.get("trusted_device_allowed"))
                trusted_device_token = str(trust_eval.get("trusted_device_token") or "").strip()
                trust_reason = str(trust_eval.get("trust_reason") or "new_device")

                if trusted_device_allowed:
                    _complete_staff_login_session(
                        authenticated_user,
                        username_for_session=authenticated_username,
                        breakglass_ok=False,
                    )
                    try:
                        authenticated_user.last_login_at = utc_now_naive()
                        authenticated_user.last_login_ip = _client_ip()
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
                    log_auth_event(
                        event="STAFF_LOGIN_TRUSTED_DEVICE",
                        status="success",
                        user_id=authenticated_user.id,
                        user_identifier=authenticated_user.username,
                        metadata={"path": "/admin/login", "trusted": True},
                    )
                    try:
                        current_app.logger.info(
                            "STAFF_LOGIN_TRUSTED user=%s ip=%s",
                            authenticated_user.username,
                            _client_ip(),
                        )
                    except Exception:
                        pass
                    fallback = url_for("admin.listar_solicitudes")
                    resp = redirect(_safe_next_url(fallback))
                    _trusted_device_set_cookie(resp, trusted_device_token)
                    return resp

                session[_TRUSTED_DEVICE_SESSION_REASON] = trust_reason
                session.modified = True
                log_auth_event(
                    event="STAFF_2FA_REQUIRED",
                    status="success",
                    user_id=authenticated_user.id,
                    user_identifier=authenticated_user.username,
                    metadata={"path": "/admin/login", "reason": trust_reason},
                )
                try:
                    current_app.logger.info(
                        "STAFF_LOGIN_NEW_DEVICE user=%s reason=%s ip=%s",
                        authenticated_user.username,
                        trust_reason,
                        _client_ip(),
                    )
                except Exception:
                    pass
                fallback = url_for("admin.listar_solicitudes")
                next_url = _safe_next_url(fallback)
                mfa_url = _begin_pending_staff_mfa(
                    staff_user=authenticated_user,
                    source="admin",
                    next_url=next_url,
                )
                resp = redirect(mfa_url)
                if not trusted_device_token:
                    trusted_device_token = _trusted_device_issue_token()
                _trusted_device_set_cookie(resp, trusted_device_token)
                return resp

            _complete_staff_login_session(
                authenticated_user,
                username_for_session=authenticated_username,
                breakglass_ok=bool(breakglass_ok),
            )

            # Auditoría de último login para staff en BD
            if isinstance(authenticated_user, StaffUser):
                try:
                    authenticated_user.last_login_at = utc_now_naive()
                    authenticated_user.last_login_ip = _client_ip()
                    db.session.commit()
                    _audit_log(
                        action_type="STAFF_LOGIN_SUCCESS",
                        entity_type="StaffUser",
                        entity_id=authenticated_user.id,
                        summary=f"Login staff exitoso: {authenticated_user.username}",
                    )
                    log_auth_event(
                        event="STAFF_LOGIN_SUCCESS",
                        status="success",
                        user_id=authenticated_user.id,
                        user_identifier=authenticated_user.username,
                        metadata={"path": "/admin/login"},
                    )
                except Exception:
                    db.session.rollback()
            else:
                log_auth_event(
                    event="BREAKGLASS_LOGIN_SUCCESS",
                    status="success",
                    user_identifier=authenticated_username,
                    metadata={"path": "/admin/login"},
                )

            fallback = url_for('admin.listar_solicitudes')
            if _login_debug_enabled():
                try:
                    current_app.logger.warning(
                        "LOGIN_DEBUG_ADMIN_SESSION %s",
                        json.dumps(
                            {
                                "route": "/admin/login",
                                "usuario_session": session.get("usuario"),
                                "role_session": session.get("role"),
                                "is_admin_session": bool(session.get("is_admin_session")),
                                "mfa_verified": bool(session.get(_MFA_VERIFIED_SESSION_KEY)),
                            },
                            ensure_ascii=False,
                            default=str,
                        ),
                    )
                except Exception:
                    pass
            return redirect(_safe_next_url(fallback))

        # ❌ Login incorrecto
        if not is_testing:
            _admin_register_fail(usuario_norm)
        _audit_log(
            action_type="STAFF_LOGIN_FAIL",
            entity_type="StaffUser",
            entity_id=usuario_norm or None,
            summary=f"Intento fallido de login staff: {usuario_norm or 'sin_usuario'}",
            success=False,
            error="Credenciales inválidas",
        )
        log_auth_event(
            event="STAFF_LOGIN_FAIL",
            status="fail",
            user_identifier=usuario_norm or None,
            reason="invalid_credentials",
            metadata={
                "path": "/admin/login",
                "reason_code": auth_reject_reason or "invalid_credentials",
                "staff_found": bool(staff_user),
                "staff_active": bool(getattr(staff_user, "is_active", False)) if staff_user else False,
            },
        )
        try:
            current_app.logger.warning(
                "STAFF_LOGIN_REJECT route=/admin/login user=%s reason=%s found=%s active=%s",
                usuario_norm,
                auth_reject_reason or "invalid_credentials",
                bool(staff_user),
                bool(getattr(staff_user, "is_active", False)) if staff_user else False,
            )
        except Exception:
            pass
        error = 'Credenciales incorrectas.'

    return render_template('admin/login.html', error=error)


@admin_bp.route("/mfa/setup", methods=["GET", "POST"])
def mfa_setup():
    pending = session_get_mfa_pending(session)
    staff_user = _get_pending_staff_user()
    if not pending or not isinstance(staff_user, StaffUser):
        session_clear_mfa_pending(session)
        return redirect(url_for("admin.login"))
    if not _staff_user_needs_mfa(staff_user):
        _complete_staff_login_session(
            staff_user,
            username_for_session=(staff_user.username or ""),
            breakglass_ok=False,
        )
        return redirect(_session_redirect_after_mfa(pending))
    mfa_path, _ = _staff_user_mfa_bootstrap_path(staff_user)
    if mfa_path == "verify":
        return redirect(url_for("admin.mfa_verify"))

    setup_secret = (session.get(MFA_SETUP_SECRET_SESSION_KEY) or "").strip().upper()
    if not setup_secret:
        setup_secret = generate_mfa_secret()
        session[MFA_SETUP_SECRET_SESSION_KEY] = setup_secret
        session.modified = True

    issuer = mfa_issuer_name()
    otp_uri = provisioning_uri(setup_secret, username=(staff_user.username or ""), issuer=issuer)
    qr_data_uri = generate_qr_png_data_uri(otp_uri)
    error = None

    if request.method == "POST":
        code = (request.form.get("code") or "").strip()
        ok, matched_counter, reason = verify_totp_code(
            setup_secret,
            code,
            last_used_counter=None,
        )
        if ok and matched_counter is not None:
            try:
                staff_user.set_mfa_secret(setup_secret)
                staff_user.mfa_enabled = True
                staff_user.mfa_last_timestep = int(matched_counter)
                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                error = "No fue posible activar MFA. Intenta de nuevo."
                exc_type = type(exc).__name__
                exc_msg = str(exc).strip()
                if len(exc_msg) > 240:
                    exc_msg = exc_msg[:240]
                try:
                    current_app.logger.exception(
                        "MFA_SETUP_FAIL db_error user_id=%s username=%s exc_type=%s exc=%s",
                        int(getattr(staff_user, "id", 0) or 0),
                        str(getattr(staff_user, "username", "") or "").strip(),
                        exc_type,
                        exc_msg,
                    )
                except Exception:
                    pass
                log_auth_event(
                    event="MFA_SETUP_FAIL",
                    status="fail",
                    user_id=staff_user.id,
                    user_identifier=staff_user.username,
                    reason="mfa_setup_db_error",
                    metadata={
                        "path": "/admin/mfa/setup",
                        "exc_type": exc_type,
                        "exc": exc_msg,
                    },
                )
            else:
                _complete_staff_login_session(
                    staff_user,
                    username_for_session=(staff_user.username or ""),
                    breakglass_ok=False,
                )
                try:
                    staff_user.last_login_at = utc_now_naive()
                    staff_user.last_login_ip = _client_ip()
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                trusted_token = _trusted_device_current_token()
                if not trusted_token:
                    trusted_token = _trusted_device_issue_token()
                trusted_hash = _trusted_device_token_hash(trusted_token)
                if trusted_hash:
                    _ = register_trusted_device(staff_user, trusted_hash)
                reason = _trusted_device_reason_from_session(default="new_device")
                log_auth_event(
                    event="STAFF_LOGIN_NEW_DEVICE",
                    status="success",
                    user_id=staff_user.id,
                    user_identifier=staff_user.username,
                    metadata={"path": "/admin/mfa/setup", "reason": reason},
                )
                log_auth_event(
                    event="MFA_SETUP_SUCCESS",
                    status="success",
                    user_id=staff_user.id,
                    user_identifier=staff_user.username,
                    metadata={"path": "/admin/mfa/setup"},
                )
                session_clear_mfa_pending(session)
                resp = redirect(_session_redirect_after_mfa(pending))
                _trusted_device_set_cookie(resp, trusted_token)
                return resp
        else:
            if reason == "reused":
                error = "Código ya utilizado. Espera el siguiente código."
            else:
                error = "Código de verificación inválido."
            log_auth_event(
                event="MFA_SETUP_FAIL",
                status="fail",
                user_id=staff_user.id,
                user_identifier=staff_user.username,
                reason=(reason or "invalid_code"),
                metadata={"path": "/admin/mfa/setup"},
            )

    return render_template(
        "admin/mfa_setup.html",
        error=error,
        qr_data_uri=qr_data_uri,
        secret=setup_secret,
        issuer=issuer,
        username=(staff_user.username or ""),
    )


@admin_bp.route("/mfa/verify", methods=["GET", "POST"])
def mfa_verify():
    pending = session_get_mfa_pending(session)
    staff_user = _get_pending_staff_user()
    if not pending or not isinstance(staff_user, StaffUser):
        session_clear_mfa_pending(session)
        return redirect(url_for("admin.login"))
    if not _staff_user_needs_mfa(staff_user):
        _complete_staff_login_session(
            staff_user,
            username_for_session=(staff_user.username or ""),
            breakglass_ok=False,
        )
        return redirect(_session_redirect_after_mfa(pending))

    mfa_path, mfa_reason = _staff_user_mfa_bootstrap_path(staff_user)
    if mfa_path != "verify":
        _log_staff_mfa_setup_required(
            staff_user,
            reason=mfa_reason,
            source="admin_verify",
            path_hint="/admin/mfa/setup",
        )
        return redirect(url_for("admin.mfa_setup"))
    secret = staff_user.get_mfa_secret()

    error = None
    if request.method == "POST":
        code = (request.form.get("code") or "").strip()
        ok, matched_counter, reason = verify_totp_code(
            secret,
            code,
            last_used_counter=staff_user.mfa_last_timestep,
        )
        if ok and matched_counter is not None:
            try:
                staff_user.mfa_last_timestep = int(matched_counter)
                staff_user.last_login_at = utc_now_naive()
                staff_user.last_login_ip = _client_ip()
                db.session.commit()
            except Exception:
                db.session.rollback()
                error = "No fue posible validar MFA. Intenta de nuevo."
                log_auth_event(
                    event="MFA_VERIFY_FAIL",
                    status="fail",
                    user_id=staff_user.id,
                    user_identifier=staff_user.username,
                    reason="mfa_verify_db_error",
                    metadata={"path": "/admin/mfa/verify"},
                )
            else:
                _complete_staff_login_session(
                    staff_user,
                    username_for_session=(staff_user.username or ""),
                    breakglass_ok=False,
                )
                trusted_token = _trusted_device_current_token()
                if not trusted_token:
                    trusted_token = _trusted_device_issue_token()
                trusted_hash = _trusted_device_token_hash(trusted_token)
                if trusted_hash:
                    _ = register_trusted_device(staff_user, trusted_hash)
                reason = _trusted_device_reason_from_session(default="new_device")
                log_auth_event(
                    event="STAFF_LOGIN_NEW_DEVICE",
                    status="success",
                    user_id=staff_user.id,
                    user_identifier=staff_user.username,
                    metadata={"path": "/admin/mfa/verify", "reason": reason},
                )
                log_auth_event(
                    event="MFA_VERIFY_SUCCESS",
                    status="success",
                    user_id=staff_user.id,
                    user_identifier=staff_user.username,
                    metadata={"path": "/admin/mfa/verify"},
                )
                session_clear_mfa_pending(session)
                resp = redirect(_session_redirect_after_mfa(pending))
                _trusted_device_set_cookie(resp, trusted_token)
                return resp
        else:
            if reason == "reused":
                error = "Código ya utilizado. Espera el siguiente código."
            else:
                error = "Código de verificación inválido."
            log_auth_event(
                event="MFA_VERIFY_FAIL",
                status="fail",
                user_id=staff_user.id,
                user_identifier=staff_user.username,
                reason=(reason or "invalid_code"),
                metadata={"path": "/admin/mfa/verify"},
            )

    return render_template(
        "admin/mfa_verify.html",
        error=error,
        username=(staff_user.username or ""),
    )


@admin_bp.route("/mfa/cancel", methods=["POST"])
def mfa_cancel():
    session_clear_mfa_pending(session)
    try:
        logout_user()
    except Exception:
        pass
    return redirect(url_for("admin.login"))


@admin_bp.route('/logout', methods=['POST'])
@login_required
def logout():
    try:
        # captura usuario antes de salir
        uname = None
        try:
            uname = (current_user.get_id() if current_user else None)
        except Exception:
            uname = None

        # ✅ bajar marcador de sesión admin (por si session.clear falla)
        try:
            session.pop(_ADMIN_SESSION_MARKER, None)
            session.pop(_MFA_VERIFIED_SESSION_KEY, None)
            session_clear_mfa_pending(session)
        except Exception:
            pass

        logout_user()

        # limpiar locks (si se puede)
        if uname:
            usuario_norm = str(uname).strip().lower()

            # 🔐 reset de bruteforce login
            try:
                _admin_reset_fail(usuario_norm)
            except Exception:
                pass

            # 🔐 limpiar capa global (si existe)
            try:
                _clear_security_layer_lock_admin(endpoint="/admin/login", usuario=str(uname))
            except Exception:
                pass

            # 🟡 reset de rate-limit admin (acciones)
            try:
                # limpiamos buckets comunes para que al salir quede limpio
                buckets = ["default", "pagos", "solicitudes", "reemplazos", "delete", "tareas"]

                if _cache_ok():
                    for b in buckets:
                        try:
                            keys = _admin_action_keys(usuario_norm, bucket=b)
                            cache.delete(keys["count"])
                            cache.delete(keys["lock"])
                        except Exception:
                            pass

                for b in buckets:
                    try:
                        session.pop(_sess_action_key(usuario_norm, bucket=b), None)
                    except Exception:
                        pass
            except Exception:
                pass

        # ✅ limpieza total de sesión
        try:
            session.clear()
        except Exception:
            pass

    except Exception:
        try:
            # por si algo explotó, igual nos aseguramos de salir
            try:
                session.pop(_ADMIN_SESSION_MARKER, None)
            except Exception:
                pass
            logout_user()
        except Exception:
            pass
        try:
            session.clear()
        except Exception:
            pass

    return redirect(url_for('admin.login'))


@admin_bp.route('/usuarios', methods=['GET'])
@admin_required
def listar_usuarios():
    _owner_only()
    q = (request.args.get('q') or '').strip()
    page = max(1, request.args.get('page', default=1, type=int) or 1)
    per_page = request.args.get('per_page', default=20, type=int) or 20
    per_page = max(10, min(per_page, 100))
    usuarios, total, last_page = _usuarios_list_page_data(
        q=q,
        page=page,
        per_page=per_page,
    )
    list_next = _usuarios_list_next_url(q=q, page=page, per_page=per_page)

    if _admin_async_wants_json():
        html = render_template(
            'admin/_usuarios_list_results.html',
            usuarios=usuarios,
            q=q,
            page=page,
            per_page=per_page,
            total=total,
            last_page=last_page,
            list_next=list_next,
            min_password_len=_staff_password_min_len(),
        )
        return jsonify(_admin_async_payload(
            success=True,
            message='',
            category='info',
            replace_html=html,
            update_target='#usuariosAsyncRegion',
            extra={
                "query": q,
                "page": page,
                "per_page": per_page,
                "total": total,
                "last_page": last_page,
            },
        )), 200

    return render_template(
        'admin/usuarios_list.html',
        usuarios=usuarios,
        q=q,
        page=page,
        per_page=per_page,
        total=total,
        last_page=last_page,
        list_next=list_next,
        min_password_len=_staff_password_min_len(),
    )


@admin_bp.route('/usuarios/nuevo', methods=['GET', 'POST'])
@admin_required
def crear_usuario():
    _owner_only()
    form = StaffUserCreateForm()
    form.role.data = form.role.data or _admin_default_role()
    min_password_len = _staff_password_min_len()

    if form.validate_on_submit():
        username = (form.username.data or '').strip()
        email = (form.email.data or '').strip().lower() or None
        role = (form.role.data or '').strip().lower()
        password = StaffUser.normalize_password(form.password.data or "")

        if role not in ('owner', 'admin', 'secretaria'):
            flash('Rol inválido.', 'danger')
            return render_template('admin/usuario_form.html', form=form, nuevo=True, min_password_len=min_password_len)

        if len(password) < min_password_len:
            flash(f'La contraseña debe tener al menos {min_password_len} caracteres.', 'danger')
            return render_template('admin/usuario_form.html', form=form, nuevo=True, min_password_len=min_password_len)

        exists_username = StaffUser.query.filter(func.lower(StaffUser.username) == username.lower()).first()
        if exists_username:
            flash('El username ya existe.', 'danger')
            return render_template('admin/usuario_form.html', form=form, nuevo=True, min_password_len=min_password_len)

        if email:
            exists_email = StaffUser.query.filter(func.lower(StaffUser.email) == email).first()
            if exists_email:
                flash('El email ya existe.', 'danger')
                return render_template('admin/usuario_form.html', form=form, nuevo=True, min_password_len=min_password_len)

        try:
            u = StaffUser(username=username, email=email, role=role, is_active=True)
            u.set_password(password)
            db.session.add(u)
            db.session.commit()
            log_admin_action(
                event="STAFF_USER_CREATE",
                status="success",
                entity_type="staff_user",
                entity_id=u.id,
                summary=f"Usuario staff creado: {u.username}",
                metadata={"role": role},
            )
            flash('Usuario creado correctamente.', 'success')
            return redirect(url_for('admin.listar_usuarios'))
        except IntegrityError:
            db.session.rollback()
            log_admin_action(
                event="STAFF_USER_CREATE",
                status="fail",
                entity_type="staff_user",
                entity_id=username.lower(),
                reason="integrity_error",
                metadata={"role": role},
            )
            flash('No se pudo crear el usuario: username o email duplicado.', 'danger')
        except SQLAlchemyError:
            db.session.rollback()
            log_admin_action(
                event="STAFF_USER_CREATE",
                status="fail",
                entity_type="staff_user",
                entity_id=username.lower(),
                reason="db_error",
                metadata={"role": role},
            )
            flash('No se pudo crear el usuario por un error de base de datos.', 'danger')

    return render_template('admin/usuario_form.html', form=form, nuevo=True, min_password_len=min_password_len)


@admin_bp.route('/usuarios/<int:user_id>/editar', methods=['GET', 'POST'])
@admin_required
def editar_usuario(user_id: int):
    _owner_only()
    user = StaffUser.query.get_or_404(user_id)
    form = StaffUserEditForm(obj=user)
    min_password_len = _staff_password_min_len()
    wants_async = _admin_async_wants_json()

    def _flatten_form_errors() -> list[str]:
        out: list[str] = []
        try:
            for field_errors in (form.errors or {}).values():
                for msg in (field_errors or []):
                    text = str(msg or '').strip()
                    if text:
                        out.append(text)
        except Exception:
            return []
        return out

    def _render_edit_region(async_feedback=None) -> str:
        return render_template(
            'admin/_editar_usuario_form_region.html',
            form=form,
            user=user,
            min_password_len=min_password_len,
            async_feedback=async_feedback,
        )

    def _render_edit_page(async_feedback=None):
        return render_template(
            'admin/usuario_form.html',
            form=form,
            user=user,
            nuevo=False,
            min_password_len=min_password_len,
            async_feedback=async_feedback,
        )

    def _async_edit_response(
        *,
        ok: bool,
        message: str,
        category: str,
        http_status: int = 200,
        error_code: str | None = None,
        include_region: bool = True,
        async_feedback=None,
    ):
        payload = _admin_async_payload(
            success=bool(ok),
            message=message,
            category=category,
            replace_html=_render_edit_region(async_feedback=async_feedback) if include_region else None,
            update_target="#editarUsuarioAsyncRegion",
            errors=_flatten_form_errors(),
            error_code=error_code,
        )
        return jsonify(payload), http_status

    if request.method == 'POST' and not form.validate_on_submit():
        if wants_async:
            return _async_edit_response(
                ok=False,
                message='No se guardó. Revisa los campos marcados y corrige los errores.',
                category='warning',
                http_status=200,
                error_code='invalid_input',
                async_feedback={"message": "No se guardó. Revisa los campos marcados y corrige los errores.", "category": "warning"},
            )
        flash('No se guardó. Revisa los campos marcados y corrige los errores.', 'danger')
        return _render_edit_page()

    if form.validate_on_submit():
        email = (form.email.data or '').strip().lower() or None
        change_password_requested = bool(getattr(form, "change_password", None) and form.change_password.data)
        new_password = StaffUser.normalize_password(form.new_password.data or "") if change_password_requested else ""
        new_password_confirm = StaffUser.normalize_password(form.new_password_confirm.data or "") if change_password_requested else ""
        old_role = (user.role or "").strip().lower()
        role_from_form = (form.role.data or '').strip().lower()
        role_to_apply = old_role if wants_async else role_from_form

        if role_to_apply not in ('owner', 'admin', 'secretaria'):
            if wants_async:
                return _async_edit_response(
                    ok=False,
                    message='No se pudo guardar la edición del usuario.',
                    category='danger',
                    http_status=400,
                    error_code='invalid_input',
                    async_feedback={"message": "No se pudo guardar la edición del usuario.", "category": "danger"},
                )
            flash('Rol inválido.', 'danger')
            return _render_edit_page()

        if email:
            dup_email = StaffUser.query.filter(
                func.lower(StaffUser.email) == email,
                StaffUser.id != user.id
            ).first()
            if dup_email:
                form.email.errors.append('El email ya está en uso por otro usuario.')
                if wants_async:
                    return _async_edit_response(
                        ok=False,
                        message='Este email ya está en uso por otro usuario.',
                        category='danger',
                        http_status=409,
                        error_code='conflict',
                        async_feedback={"message": "Este email ya está en uso por otro usuario.", "category": "danger"},
                    )
                flash('El email ya está en uso por otro usuario.', 'danger')
                return _render_edit_page()

        if new_password and len(new_password) < min_password_len:
            form.new_password.errors.append(f'La nueva contraseña debe tener al menos {min_password_len} caracteres.')
            if wants_async:
                return _async_edit_response(
                    ok=False,
                    message=f'La nueva contraseña debe tener al menos {min_password_len} caracteres.',
                    category='danger',
                    http_status=200,
                    error_code='invalid_input',
                    async_feedback={"message": f"La nueva contraseña debe tener al menos {min_password_len} caracteres.", "category": "danger"},
                )
            flash(f'La nueva contraseña debe tener al menos {min_password_len} caracteres.', 'danger')
            return _render_edit_page()

        if new_password and not new_password_confirm:
            form.new_password_confirm.errors.append('Confirma la nueva contraseña.')
            if wants_async:
                return _async_edit_response(
                    ok=False,
                    message='No se guardó. Revisa los campos marcados y corrige los errores.',
                    category='warning',
                    http_status=200,
                    error_code='invalid_input',
                    async_feedback={"message": "No se guardó. Revisa los campos marcados y corrige los errores.", "category": "warning"},
                )
            flash('No se guardó. Revisa los campos marcados y corrige los errores.', 'danger')
            return _render_edit_page()

        if new_password_confirm and not new_password:
            form.new_password.errors.append('Debes escribir la nueva contraseña primero.')
            if wants_async:
                return _async_edit_response(
                    ok=False,
                    message='No se guardó. Revisa los campos marcados y corrige los errores.',
                    category='warning',
                    http_status=200,
                    error_code='invalid_input',
                    async_feedback={"message": "No se guardó. Revisa los campos marcados y corrige los errores.", "category": "warning"},
                )
            flash('No se guardó. Revisa los campos marcados y corrige los errores.', 'danger')
            return _render_edit_page()

        if new_password and new_password_confirm and new_password != new_password_confirm:
            form.new_password_confirm.errors.append('Las contraseñas no coinciden.')
            if wants_async:
                return _async_edit_response(
                    ok=False,
                    message='No se guardó. Revisa los campos marcados y corrige los errores.',
                    category='warning',
                    http_status=200,
                    error_code='invalid_input',
                    async_feedback={"message": "No se guardó. Revisa los campos marcados y corrige los errores.", "category": "warning"},
                )
            flash('No se guardó. Revisa los campos marcados y corrige los errores.', 'danger')
            return _render_edit_page()

        try:
            user.email = email
            if not wants_async:
                user.role = role_to_apply
            if new_password:
                user.set_password(new_password)
            db.session.commit()
            new_role_effective = (user.role or "").strip().lower()
            log_admin_action(
                event="STAFF_USER_UPDATE",
                status="success",
                entity_type="staff_user",
                entity_id=user.id,
                summary=f"Usuario actualizado: {user.username}",
                metadata={"old_role": old_role, "new_role": new_role_effective, "password_changed": bool(new_password)},
            )
            if new_password:
                log_admin_action(
                    event="STAFF_PASSWORD_CHANGED",
                    status="success",
                    entity_type="staff_user",
                    entity_id=user.id,
                    summary=f"Contraseña staff actualizada: {user.username}",
                    metadata={"trigger": "admin_edit_user"},
                )
            success_msg = 'Usuario actualizado correctamente.'
            if wants_async:
                form = StaffUserEditForm(formdata=None, obj=user)
                return _async_edit_response(
                    ok=True,
                    message=success_msg,
                    category='success',
                    http_status=200,
                    async_feedback={"message": success_msg, "category": "success"},
                )
            flash(success_msg, 'success')
            return redirect(url_for('admin.listar_usuarios'))
        except IntegrityError:
            db.session.rollback()
            form.email.errors.append('Este email ya está en uso por otro usuario.')
            log_admin_action(
                event="STAFF_USER_UPDATE",
                status="fail",
                entity_type="staff_user",
                entity_id=user.id,
                reason="integrity_error",
            )
            msg = 'No se pudo guardar porque el email ya está en uso.'
            if wants_async:
                return _async_edit_response(
                    ok=False,
                    message=msg,
                    category='danger',
                    http_status=409,
                    error_code='conflict',
                    async_feedback={"message": msg, "category": "danger"},
                )
            flash(msg, 'danger')
        except SQLAlchemyError:
            db.session.rollback()
            log_admin_action(
                event="STAFF_USER_UPDATE",
                status="fail",
                entity_type="staff_user",
                entity_id=user.id,
                reason="db_error",
            )
            msg = 'No se pudo guardar el usuario. Intenta nuevamente.'
            if wants_async:
                return _async_edit_response(
                    ok=False,
                    message=msg,
                    category='danger',
                    http_status=500,
                    error_code='server_error',
                    async_feedback={"message": msg, "category": "danger"},
                )
            flash(msg, 'danger')

    return _render_edit_page()


@admin_bp.route('/usuarios/<int:user_id>/toggle-estado', methods=['POST'])
@admin_required
def toggle_usuario_estado(user_id: int):
    _owner_only()
    user = StaffUser.query.get_or_404(user_id)
    next_url = (request.form.get("next") or request.args.get("next") or request.referrer or "").strip()
    fallback = url_for('admin.listar_usuarios')
    try:
        if isinstance(current_user, StaffUser) and int(current_user.id) == int(user.id):
            return _usuarios_list_action_response(
                ok=False,
                message='No puedes desactivar tu propio usuario.',
                category='warning',
                next_url=next_url,
                fallback=fallback,
                http_status=409,
                error_code='conflict',
            )
    except Exception:
        pass

    try:
        user.is_active = not bool(user.is_active)
        db.session.commit()
        log_admin_action(
            event="STAFF_USER_STATUS_CHANGED",
            status="success",
            entity_type="staff_user",
            entity_id=user.id,
            summary=f"Estado de usuario staff actualizado: {user.username}",
            metadata={"is_active": bool(user.is_active)},
        )
        estado = "activado" if user.is_active else "desactivado"
        return _usuarios_list_action_response(
            ok=True,
            message=f'Usuario {estado} correctamente.',
            category='success',
            next_url=next_url,
            fallback=fallback,
        )
    except SQLAlchemyError:
        db.session.rollback()
        log_admin_action(
            event="STAFF_USER_STATUS_CHANGED",
            status="fail",
            entity_type="staff_user",
            entity_id=user.id,
            reason="db_error",
        )
        return _usuarios_list_action_response(
            ok=False,
            message='No se pudo actualizar el estado del usuario. Intenta nuevamente.',
            category='danger',
            next_url=next_url,
            fallback=fallback,
            http_status=500,
            error_code='server_error',
        )


@admin_bp.route('/roles', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_roles():
    _owner_only()
    next_url = (request.form.get("next") or request.args.get("next") or request.referrer or "").strip()
    fallback = url_for("admin.listar_usuarios")

    if request.method == 'POST':
        raw_user_id = (request.form.get("user_id") or "").strip()
        new_role = normalize_staff_role(request.form.get("role"))
        if not raw_user_id.isdigit():
            return _usuarios_list_action_response(
                ok=False,
                message="Usuario inválido.",
                category="danger",
                next_url=next_url,
                fallback=fallback,
                http_status=400,
                error_code="invalid_input",
            )
        if new_role not in {"owner", "admin", "secretaria"}:
            return _usuarios_list_action_response(
                ok=False,
                message="Rol inválido.",
                category="danger",
                next_url=next_url,
                fallback=fallback,
                http_status=400,
                error_code="invalid_input",
            )

        user = StaffUser.query.filter_by(id=int(raw_user_id)).first_or_404()
        old_role = normalize_staff_role(user.role)
        if old_role == new_role:
            return _usuarios_list_action_response(
                ok=False,
                message="Ese usuario ya tiene ese rol.",
                category="info",
                next_url=next_url,
                fallback=fallback,
                http_status=409,
                error_code="conflict",
            )

        if old_role == "owner" and new_role != "owner":
            owners = StaffUser.query.filter(func.lower(StaffUser.role) == "owner", StaffUser.is_active.is_(True)).count()
            if int(owners or 0) <= 1:
                return _usuarios_list_action_response(
                    ok=False,
                    message="Debe existir al menos un Owner activo.",
                    category="warning",
                    next_url=next_url,
                    fallback=fallback,
                    http_status=409,
                    error_code="conflict",
                )

        user.role = new_role
        try:
            db.session.commit()
            _audit_log(
                action_type="ROLE_CHANGED",
                entity_type="StaffUser",
                entity_id=user.id,
                summary=f"Rol actualizado para {user.username}",
                metadata={"old_role": old_role, "new_role": new_role},
                success=True,
            )
            log_admin_action(
                event="ROLE_CHANGED",
                status="success",
                entity_type="staff_user",
                entity_id=user.id,
                summary=f"Rol actualizado para {user.username}",
                metadata={"old_role": old_role, "new_role": new_role},
            )
            return _usuarios_list_action_response(
                ok=True,
                message=f"Rol de {user.username} actualizado correctamente.",
                category="success",
                next_url=next_url,
                fallback=fallback,
            )
        except Exception:
            db.session.rollback()
            log_admin_action(
                event="ROLE_CHANGED",
                status="fail",
                entity_type="staff_user",
                entity_id=user.id,
                reason="db_error",
                metadata={"old_role": old_role, "new_role": new_role},
            )
            return _usuarios_list_action_response(
                ok=False,
                message="No se pudo actualizar el rol. Intenta nuevamente.",
                category="danger",
                next_url=next_url,
                fallback=fallback,
                http_status=500,
                error_code="server_error",
            )

    return redirect(fallback)


@admin_bp.route('/usuarios/<int:user_id>/eliminar', methods=['POST'])
@admin_required
def eliminar_usuario(user_id: int):
    _owner_only()
    user = StaffUser.query.get_or_404(user_id)
    next_url = (request.form.get("next") or request.args.get("next") or request.referrer or "").strip()
    fallback = url_for('admin.listar_usuarios')
    try:
        if isinstance(current_user, StaffUser) and int(current_user.id) == int(user.id):
            return _usuarios_list_action_response(
                ok=False,
                message='No puedes eliminar tu propio usuario.',
                category='warning',
                next_url=next_url,
                fallback=fallback,
                http_status=409,
                error_code='conflict',
            )
    except Exception:
        pass

    def _has_linked_history(staff_user: StaffUser) -> bool:
        try:
            has_audit = db.session.query(StaffAuditLog.id).filter(
                StaffAuditLog.actor_user_id == int(staff_user.id)
            ).first() is not None
            if has_audit:
                return True
        except Exception:
            return True

        try:
            username_norm = (getattr(staff_user, "username", "") or "").strip().lower()
            if username_norm:
                has_matching_activity = db.session.query(SolicitudCandidata.id).filter(
                    func.lower(SolicitudCandidata.created_by) == username_norm
                ).first() is not None
                if has_matching_activity:
                    return True
        except Exception:
            # Si la verificación secundaria falla, no bloqueamos; la fuente canónica
            # para impedir borrado es la auditoría ligada por actor_user_id.
            return False

        return False

    try:
        if _has_linked_history(user):
            user.is_active = False
            db.session.commit()
            log_admin_action(
                event="STAFF_USER_DELETE_BLOCKED",
                status="fail",
                entity_type="staff_user",
                entity_id=user.id,
                reason="linked_history",
                metadata={"username": user.username},
            )
            return _usuarios_list_action_response(
                ok=False,
                message='Este usuario tiene actividad registrada y no puede eliminarse. Solo puede desactivarse.',
                category='warning',
                next_url=next_url,
                fallback=fallback,
                http_status=409,
                error_code='conflict',
            )

        db.session.delete(user)
        db.session.commit()
        log_admin_action(
            event="STAFF_USER_DELETE",
            status="success",
            entity_type="staff_user",
            entity_id=user_id,
            summary=f"Usuario staff eliminado: {user.username}",
        )
        return _usuarios_list_action_response(
            ok=True,
            message='Usuario eliminado correctamente.',
            category='success',
            next_url=next_url,
            fallback=fallback,
        )
    except SQLAlchemyError:
        db.session.rollback()
        log_admin_action(
            event="STAFF_USER_DELETE",
            status="fail",
            entity_type="staff_user",
            entity_id=user_id,
            reason="db_error",
        )
        return _usuarios_list_action_response(
            ok=False,
            message='No se pudo eliminar el usuario. Intenta nuevamente.',
            category='danger',
            next_url=next_url,
            fallback=fallback,
            http_status=500,
            error_code='server_error',
        )


def _parse_monitoreo_date(raw: str, end_of_day: bool = False):
    txt = (raw or "").strip()
    if not txt:
        return None
    try:
        d = datetime.strptime(txt, "%Y-%m-%d").date()
        if end_of_day:
            next_day = d + timedelta(days=1)
            next_start, _ = rd_day_range_utc_naive(next_day)
            return next_start
        day_start, _ = rd_day_range_utc_naive(d)
        return day_start
    except Exception:
        return None


_PRESENCE_TTL_SECONDS = 65
_PRESENCE_ACTIVE_SECONDS = 30
_PRESENCE_INTERACTION_ACTIVE_SECONDS = 60
_PRESENCE_INDEX_KEY = "staff_presence:index"
_PRODUCTIVITY_ACTIONS = (
    "CANDIDATA_EDIT",
    "CANDIDATA_INTERVIEW_NEW_CREATE",
    "CANDIDATA_INTERVIEW_LEGACY_SAVE",
    "MATCHING_SEND",
    "CANDIDATA_UPLOAD_DOCS",
    "CANDIDATA_MARK_LISTA",
    "CANDIDATA_MARK_TRABAJANDO",
)
_LIVE_EVENT_PREFIX = "staff_live_event"
_HUMAN_ACTION_MAP = {
    "STAFF_POST": "Actualizo datos",
    "CANDIDATA_EDIT": "Editando candidata",
    "CANDIDATA_INTERVIEW_NEW_CREATE": "Guardo entrevista",
    "CANDIDATA_INTERVIEW_LEGACY_SAVE": "Guardo entrevista",
    "CANDIDATA_UPLOAD_DOCS": "Guardo documentos",
    "CANDIDATA_MARK_LISTA": "Marco candidata lista para trabajar",
    "CANDIDATA_MARK_TRABAJANDO": "Marco candidata trabajando",
    "MATCHING_SEND": "Envio candidata a solicitud",
    "SOLICITUD_CREATE": "Creo solicitud",
    "SOLICITUD_UPDATE": "Actualizo solicitud",
    "SOLICITUD_PUBLICAR": "Publico solicitud",
    "LIVE_PAGE_LOAD": "Abrio pantalla",
    "LIVE_HEARTBEAT": "Activo",
    "LIVE_TAB_FOCUS": "Volvio a la app",
    "LIVE_OPEN_ENTITY": "Abrio entidad",
    "LIVE_SUBMIT": "Envio formulario",
    "LIVE_INTENT_CHANGE": "Cambio de actividad",
}


def _normalize_entity_type(value: str | None) -> str:
    txt = (value or "").strip().lower()
    if txt in {"candidata", "candidatas", "candidate"}:
        return "candidata"
    if txt in {"solicitud", "solicitudes", "request"}:
        return "solicitud"
    if txt in {"cliente", "clientes", "client"}:
        return "cliente"
    if txt in {"chat", "chat_conversation", "conversation", "conversacion", "chatconversation"}:
        return "chat_conversation"
    return txt


def _human_entity_display(name: str | None, code: str | None) -> str | None:
    nm = (name or "").strip()
    if not nm:
        return None
    cd = (code or "").strip()
    return f"{nm} - {cd}" if cd else f"{nm} - (sin codigo)"


def _infer_action_hint_from_path(path: str | None) -> str:
    p = (path or "").strip().lower()
    if not p:
        return "browsing"
    if "matching" in p:
        return "matching"
    if "pago" in p:
        return "pagos"
    if "entrevistas/" in p or "/entrevistas" in p:
        return "interview"
    if "entrevista" in p:
        return "editing_interview"
    if "referencias/" in p or "/referencias" in p:
        return "references"
    if "referencia" in p:
        return "editing_references"
    if "solicitudes/" in p or "/solicitudes" in p:
        return "solicitudes"
    if "solicitud" in p:
        return "editing_request"
    if "editar" in p or "edit" in p:
        return "editing"
    if "buscar" in p:
        return "searching"
    return "browsing"


def _humanize_action(
    action_type: str | None,
    summary: str | None = None,
    metadata: dict | None = None,
    route: str | None = None,
    action_hint: str | None = None,
) -> str:
    at = (action_type or "").strip().upper()
    if at == "STAFF_POST":
        route_h = _humanize_route(route)
        return f"Actualizo datos en {route_h}"
    if at in _HUMAN_ACTION_MAP:
        return _HUMAN_ACTION_MAP[at]

    hint = (action_hint or "").strip().lower() or _infer_action_hint_from_path(route)
    hint_map = {
        "editing": "Editando",
        "editing_interview": "Editando entrevista",
        "editing_references": "Editando referencias",
        "editing_candidate": "Editando candidata",
        "editing_request": "Editando solicitud",
        "viewing_client": "Viendo cliente",
        "matching": "En Matching",
        "searching": "Buscando",
        "interview": "En Entrevista",
        "references": "En Referencias",
        "solicitudes": "En Solicitudes",
        "pagos": "En Pagos",
        "browsing": "Navegando en la app",
    }
    if hint in hint_map:
        return hint_map[hint]

    txt = (summary or "").strip()
    if txt and not txt.isupper():
        return txt[:120]
    return "Actividad en la app"


def _extract_entity_context(payload: dict | None, current_path: str | None = None) -> dict:
    src = dict(payload or {})
    ctx = {
        "entity_type": _normalize_entity_type(src.get("entity_type")),
        "entity_id": (src.get("entity_id") or "").strip(),
        "entity_name": (src.get("entity_name") or "").strip(),
        "entity_code": (src.get("entity_code") or "").strip(),
        "entity_label": (src.get("entity_label") or "").strip(),
    }
    if not ctx["entity_id"]:
        for key, etype in (
            ("candidata_id", "candidata"),
            ("solicitud_id", "solicitud"),
            ("cliente_id", "cliente"),
            ("conversation_id", "chat_conversation"),
        ):
            value = str(src.get(key) or "").strip()
            if value:
                ctx["entity_type"] = etype
                ctx["entity_id"] = value
                break

    path = (current_path or "").strip()
    if not path:
        return ctx

    parsed = urlparse(path)
    q = parse_qs(parsed.query)
    if not ctx["entity_id"]:
        for key, etype in (
            ("candidata_id", "candidata"),
            ("solicitud_id", "solicitud"),
            ("cliente_id", "cliente"),
            ("conversation_id", "chat_conversation"),
        ):
            value = (q.get(key) or [None])[0]
            value = str(value or "").strip()
            if value:
                ctx["entity_type"] = etype
                ctx["entity_id"] = value
                break

    path_only = (parsed.path or "").strip().lower()
    if (not ctx["entity_id"]) and path_only:
        m = re.search(r"/candidatas?/([a-z0-9_-]+)", path_only)
        if m:
            ctx["entity_type"] = "candidata"
            ctx["entity_id"] = m.group(1)
        m = re.search(r"/solicitudes?/([a-z0-9_-]+)", path_only)
        if m and not ctx["entity_id"]:
            ctx["entity_type"] = "solicitud"
            ctx["entity_id"] = m.group(1)
        m = re.search(r"/clientes?/([a-z0-9_-]+)", path_only)
        if m and not ctx["entity_id"]:
            ctx["entity_type"] = "cliente"
            ctx["entity_id"] = m.group(1)
        m = re.search(r"/chat/conversations?/([a-z0-9_-]+)", path_only)
        if m and not ctx["entity_id"]:
            ctx["entity_type"] = "chat_conversation"
            ctx["entity_id"] = m.group(1)

    if (not ctx["entity_id"]) and path_only:
        m = re.search(r"/clientes/\d+/solicitudes/([a-z0-9_-]+)", path_only)
        if m:
            ctx["entity_type"] = "solicitud"
            ctx["entity_id"] = m.group(1)
        m = re.search(r"/matching/solicitudes/([a-z0-9_-]+)", path_only)
        if m and not ctx["entity_id"]:
            ctx["entity_type"] = "solicitud"
            ctx["entity_id"] = m.group(1)

    return ctx


def _humanize_presence_action(
    base_action: str | None,
    action_hint: str | None,
    entity_type: str | None,
    entity_display: str | None,
) -> str:
    base = (base_action or "").strip() or "Actividad en la app"
    hint = (action_hint or "").strip().lower()
    etype = _normalize_entity_type(entity_type)
    display = (entity_display or "").strip()

    if not display and etype:
        display = {
            "candidata": "candidata",
            "solicitud": "solicitud",
            "cliente": "cliente",
        }.get(etype, etype)

    if not display:
        return base[:120]

    if hint in {"editing_candidate", "editing", "editing_interview", "editing_references", "interview", "references"} and etype == "candidata":
        if hint in {"editing_interview", "interview"}:
            return f"Editando entrevista de {display}"[:120]
        if hint in {"editing_references", "references"}:
            return f"Editando referencias de {display}"[:120]
        return f"Editando candidata {display}"[:120]

    if hint in {"editing_request", "solicitudes"} and etype == "solicitud":
        return f"Revisando solicitud {display}"[:120]

    if hint in {"matching"} and etype == "solicitud":
        return f"Trabajando en matching de solicitud {display}"[:120]

    if hint in {"viewing_client"} and etype == "cliente":
        return f"Viendo cliente {display}"[:120]

    if "editar" in base.lower() and etype == "candidata":
        return f"Editando candidata {display}"[:120]
    if "solicitud" in base.lower() and etype == "solicitud":
        return f"Revisando solicitud {display}"[:120]
    if "cliente" in base.lower() and etype == "cliente":
        return f"Viendo cliente {display}"[:120]

    return f"{base} - {display}"[:120]


def _format_candidata_display(cand: Candidata | None) -> str | None:
    if cand is None:
        return None
    return _human_entity_display(cand.nombre_completo, cand.codigo)


def _format_cliente_display(cli: Cliente | None) -> str | None:
    if cli is None:
        return None
    return _human_entity_display(cli.nombre_completo, cli.codigo)


def _format_solicitud_display(sol: Solicitud | None) -> str | None:
    if sol is None:
        return None
    base = (sol.codigo_solicitud or f"Solicitud #{sol.id}")
    cli = _format_cliente_display(getattr(sol, "cliente", None))
    return f"{base} - {cli}" if cli else base


def _humanize_route(path: str | None) -> str:
    raw = (path or "").strip()
    if not raw:
        return "-"
    parsed = urlparse(raw)
    p = (parsed.path or "").strip().lower()

    route_map = (
        ("/entrevistas/buscar", "Entrevistas: buscar"),
        ("/admin/matching", "Matching"),
        ("/admin/solicitudes", "Solicitudes"),
        ("/referencias", "Referencias"),
        ("/admin/entrevista", "Entrevista"),
        ("/admin/monitoreo", "Control Room"),
        ("/admin/pagos", "Pagos"),
    )
    for prefix, label in route_map:
        if p.startswith(prefix):
            return label

    chunks = [c for c in p.split("/") if c]
    if not chunks:
        return "Inicio"
    tail = chunks[-1].replace("-", " ").replace("_", " ").strip()
    if tail:
        return tail[:1].upper() + tail[1:]
    return raw[:80]


def _humanize_datetime(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    now = now_rd()
    dt_rd = to_rd(dt)
    if dt_rd is None:
        return "-"
    delta = max(0, int((now - dt_rd).total_seconds()))
    if delta < 60:
        return f"Hace {delta} segundos"
    if delta < 3600:
        mins = max(1, delta // 60)
        return f"Hace {mins} minutos"
    if dt_rd.date() == now.date():
        return f"Hoy {dt_rd.strftime('%H:%M')}"
    return dt_rd.strftime("%d/%m %H:%M")


def _action_icon(action_type: str | None, success: bool = True) -> tuple[str, str]:
    at = (action_type or "").strip().upper()
    if not success:
        return ("bi-exclamation-triangle", "text-danger")
    if "CREATE" in at or "NEW" in at:
        return ("bi-plus-circle", "text-success")
    if "EDIT" in at or "UPDATE" in at or "INTERVIEW" in at:
        return ("bi-pencil-square", "text-primary")
    if "DELETE" in at or "ELIM" in at:
        return ("bi-trash", "text-danger")
    if "OPEN" in at or "VIEW" in at or "PAGE_LOAD" in at:
        return ("bi-eye", "text-info")
    if "MATCH" in at or "SEND" in at:
        return ("bi-send", "text-warning")
    return ("bi-activity", "text-secondary")


def _humanize_field_name(name: str | None) -> str:
    return humanize_audit_field(name)


def _humanize_value(value) -> str:
    return humanize_audit_value(value)


def _changes_to_human(changes) -> list[dict]:
    if not isinstance(changes, dict):
        return []
    rows: list[dict] = []
    for key, value in changes.items():
        before = after = None
        if isinstance(value, dict) and ("from" in value or "to" in value):
            before = value.get("from")
            after = value.get("to")
        elif isinstance(value, (list, tuple)) and len(value) >= 2:
            before, after = value[0], value[1]
        else:
            before, after = None, value
        human = humanize_change(str(key), before, after)
        rows.append(
            {
                "field": str(key),
                "label": human.get("label") or _humanize_field_name(key),
                "from": human.get("from") or _humanize_value(before),
                "to": human.get("to") or _humanize_value(after),
                "sentence": human.get("sentence") or f"{_humanize_field_name(key)}: {_humanize_value(before)} -> {_humanize_value(after)}",
                "sensitive": bool(human.get("sensitive")),
            }
        )
    return rows[:30]


def _metadata_human(meta: dict | None) -> list[dict]:
    src = dict(meta or {})
    skip = {
        "ip", "user_agent", "event_type", "scope",
        "entity_display", "entity_name", "entity_code",
        "action_hint", "action_label", "route_label", "status_code",
    }
    out = []
    for k, v in src.items():
        key = str(k or "").strip()
        if not key or key in skip:
            continue
        if key.endswith("_id") and str(v or "").strip().isdigit():
            continue
        out.append({"label": _humanize_field_name(key), "value": _humanize_value(v)})
    return out[:12]


def _humanize_summary(action_human: str, summary: str | None, route: str | None) -> str:
    txt = (summary or "").strip()
    if not txt:
        return action_human
    if txt.isupper() or "HTTP " in txt or txt.startswith("POST ") or txt.startswith("GET "):
        return f"{action_human} en {_humanize_route(route)}"
    return txt[:180]


def _build_entity_display_map(logs: list[StaffAuditLog] | None) -> dict[tuple[str, str], str]:
    logs = logs or []
    cand_ids: set[int] = set()
    cand_codes: set[str] = set()
    sol_ids: set[int] = set()
    sol_codes: set[str] = set()
    cli_ids: set[int] = set()
    cli_codes: set[str] = set()

    for log in logs:
        et = _normalize_entity_type(getattr(log, "entity_type", None))
        eid = str(getattr(log, "entity_id", "") or "").strip()
        if not et or not eid:
            continue
        if et == "candidata":
            if eid.isdigit():
                cand_ids.add(int(eid))
            else:
                cand_codes.add(eid)
        elif et == "solicitud":
            if eid.isdigit():
                sol_ids.add(int(eid))
            else:
                sol_codes.add(eid)
        elif et == "cliente":
            if eid.isdigit():
                cli_ids.add(int(eid))
            else:
                cli_codes.add(eid)

    out: dict[tuple[str, str], str] = {}
    if cand_ids or cand_codes:
        try:
            cand_q = Candidata.query
            filters = []
            if cand_ids:
                filters.append(Candidata.fila.in_(cand_ids))
            if cand_codes:
                filters.append(Candidata.codigo.in_(cand_codes))
            for cand in cand_q.filter(or_(*filters)).all():
                label = _format_candidata_display(cand)
                if not label:
                    continue
                out[("candidata", str(cand.fila))] = label
                if (cand.codigo or "").strip():
                    out[("candidata", str(cand.codigo).strip())] = label
        except Exception:
            pass

    if sol_ids or sol_codes:
        try:
            sol_q = Solicitud.query.options(joinedload(Solicitud.cliente))
            filters = []
            if sol_ids:
                filters.append(Solicitud.id.in_(sol_ids))
            if sol_codes:
                filters.append(Solicitud.codigo_solicitud.in_(sol_codes))
            for sol in sol_q.filter(or_(*filters)).all():
                label = _format_solicitud_display(sol)
                if not label:
                    continue
                out[("solicitud", str(sol.id))] = label
                if (sol.codigo_solicitud or "").strip():
                    out[("solicitud", str(sol.codigo_solicitud).strip())] = label
        except Exception:
            pass

    if cli_ids or cli_codes:
        try:
            cli_q = Cliente.query
            filters = []
            if cli_ids:
                filters.append(Cliente.id.in_(cli_ids))
            if cli_codes:
                filters.append(Cliente.codigo.in_(cli_codes))
            for cli in cli_q.filter(or_(*filters)).all():
                label = _format_cliente_display(cli)
                if not label:
                    continue
                out[("cliente", str(cli.id))] = label
                if (cli.codigo or "").strip():
                    out[("cliente", str(cli.codigo).strip())] = label
        except Exception:
            pass
    return out


def _entity_display_from_metadata(metadata: dict | None, entity_type: str | None, entity_id: str | None) -> str | None:
    meta = dict(metadata or {})
    direct = (meta.get("entity_display") or "").strip()
    if direct:
        return direct
    name = (meta.get("entity_name") or meta.get("nombre") or "").strip()
    code = (meta.get("entity_code") or meta.get("codigo") or "").strip()
    if name:
        return _human_entity_display(name, code)
    et = _normalize_entity_type(entity_type)
    eid = (entity_id or "").strip()
    if et and eid:
        return f"{et[:1].upper() + et[1:]} ID {eid}"
    return None


def _is_valid_live_action_type(raw: str) -> bool:
    txt = (raw or "").strip().upper()
    if not txt or len(txt) > 80:
        return False
    return re.fullmatch(r"[A-Z0-9_]+", txt) is not None


def _map_event_to_action_type(event_type: str | None) -> str:
    ev = (event_type or "").strip().lower()
    if ev == "page_load":
        return "LIVE_PAGE_LOAD"
    if ev == "tab_focus":
        return "LIVE_TAB_FOCUS"
    if ev == "open_entity":
        return "LIVE_OPEN_ENTITY"
    if ev == "submit":
        return "LIVE_SUBMIT"
    if ev == "intent_change":
        return "LIVE_INTENT_CHANGE"
    return "LIVE_HEARTBEAT"


def _should_log_live_event(user_id: int, event_type: str, path: str, action_hint: str, entity_id: str) -> bool:
    ev = (event_type or "").strip().lower() or "heartbeat"
    base = f"{_LIVE_EVENT_PREFIX}:{int(user_id)}:{ev}:{(path or '')[:120]}:{(action_hint or '')[:60]}:{(entity_id or '')[:40]}"
    timeout = 25 if ev == "heartbeat" else 2
    if bp_get(base, default=0, context="admin_live_event_dedupe_get"):
        return False
    if not bp_set(base, 1, timeout=timeout, context="admin_live_event_dedupe_set"):
        return ev != "heartbeat"
    return True


def _presence_key(user_id: int) -> str:
    return f"staff_presence:{int(user_id)}"


def _resolve_presence_session_id(raw_session_id: str | None = None) -> str:
    val = str(raw_session_id or "").strip()
    if val:
        return val[:120]

    token = str(session.get("staff_session_token") or "").strip()
    if token:
        return token[:120]

    fallback = str(session.get("_presence_session_id") or "").strip()
    if not fallback:
        fallback = f"legacy-{secrets.token_hex(16)}"
        session["_presence_session_id"] = fallback
        session.modified = True
    return fallback[:120]


def _parse_iso_utc(raw: str | None):
    dt = parse_iso_utc(raw)
    if dt is None:
        return None
    return dt.replace(tzinfo=None)


def _normalize_client_status(raw: str | None) -> str:
    txt = (raw or "").strip().lower()
    if txt in {"active", "idle", "hidden"}:
        return txt
    return "active"


def _resolve_presence_status(last_seen_seconds: int, client_status: str) -> str:
    if int(last_seen_seconds or 0) >= _PRESENCE_ACTIVE_SECONDS:
        return "inactive"
    return _normalize_client_status(client_status)


def _touch_staff_presence(
    current_path: str | None = None,
    page_title: str | None = None,
    last_action_hint: str | None = None,
    event_type: str | None = None,
    action_type: str | None = None,
    action_hint: str | None = None,
    action_human: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    entity_name: str | None = None,
    entity_code: str | None = None,
    last_interaction_at: str | None = None,
    client_status: str | None = None,
    route_label: str | None = None,
    action_label: str | None = None,
    session_id: str | None = None,
    tab_visible: bool | None = None,
    is_idle: bool | None = None,
    is_typing: bool | None = None,
    has_unsaved_changes: bool | None = None,
    modal_open: bool | None = None,
    lock_owner: str | None = None,
    preserve_entity_when_missing: bool = True,
    log_event: bool = False,
    load_previous_state: bool = True,
) -> None:
    try:
        if not bool(session.get("is_admin_session")):
            return
        if not current_user or not getattr(current_user, "is_authenticated", False):
            return
        if not isinstance(current_user, StaffUser):
            return

        uid = int(current_user.id)
        sid = _resolve_presence_session_id(session_id)
        prev = None
        if load_previous_state:
            prev = (
                StaffPresenceState.query
                .filter(
                    StaffPresenceState.user_id == uid,
                    StaffPresenceState.session_id == sid,
                )
                .first()
            )
        effective_path = (current_path or (getattr(prev, "route", "") or "") or request.path or "")[:255]
        effective_hint = (
            action_hint
            or last_action_hint
            or (getattr(prev, "current_action", "") or "")
            or _infer_action_hint_from_path(effective_path)
        )[:80]
        effective_action_type = (action_type or "LIVE_HEARTBEAT")[:80]
        incoming_entity_type = _normalize_entity_type(entity_type)
        incoming_entity_id = (entity_id or "")[:64]
        if preserve_entity_when_missing:
            effective_entity_type = _normalize_entity_type(incoming_entity_type or getattr(prev, "entity_type", ""))
            effective_entity_id = (incoming_entity_id or getattr(prev, "entity_id", "") or "")[:64]
        else:
            effective_entity_type = _normalize_entity_type(incoming_entity_type)
            effective_entity_id = incoming_entity_id

        effective_entity_display = _human_entity_display(entity_name, entity_code)
        if not effective_entity_display and preserve_entity_when_missing:
            effective_entity_display = _human_entity_display(getattr(prev, "entity_name", ""), getattr(prev, "entity_code", ""))
        if not effective_entity_id:
            effective_entity_display = ""
        if not effective_entity_display and effective_entity_type and effective_entity_id:
            fake_log = StaffAuditLog(entity_type=effective_entity_type, entity_id=effective_entity_id)
            mapped = _build_entity_display_map([fake_log])
            effective_entity_display = mapped.get((effective_entity_type, effective_entity_id))
        base_action_human = (
            action_label
            or action_human
            or (getattr(prev, "action_label", "") if preserve_entity_when_missing else "")
            or _humanize_action(effective_action_type, route=effective_path, action_hint=effective_hint)
        )[:120]
        effective_action_human = _humanize_presence_action(
            base_action_human,
            effective_hint,
            effective_entity_type,
            effective_entity_display,
        )[:120]
        has_client_status = bool((client_status or "").strip())
        now = utc_now_naive()
        prev_interaction = getattr(prev, "last_interaction_at", None)
        interaction_dt = _parse_iso_utc(last_interaction_at) or (now if not has_client_status else (prev_interaction or now))
        normalized_client_status = _normalize_client_status(client_status) if has_client_status else "active"
        payload = build_presence_snapshot(
            {
                "route": effective_path,
                "page_title": (page_title or request.endpoint or request.path or "")[:160],
                "route_label": (route_label or getattr(prev, "route_label", "") or _humanize_route(effective_path))[:120],
                "entity_type": effective_entity_type,
                "entity_id": effective_entity_id,
                "entity_name": (entity_name or getattr(prev, "entity_name", "") or "")[:160],
                "entity_code": (entity_code or getattr(prev, "entity_code", "") or "")[:64],
                "current_action": effective_hint,
                "action_label": effective_action_human,
                "client_status": normalized_client_status,
                "last_interaction_at": iso_utc_z(interaction_dt),
                "tab_visible": tab_visible,
                "is_idle": is_idle,
                "is_typing": is_typing,
                "has_unsaved_changes": has_unsaved_changes,
                "modal_open": modal_open,
                "lock_owner": lock_owner,
                "ip": (_client_ip() or "")[:64],
                "user_agent": (request.headers.get("User-Agent") or "")[:255],
            },
            fallback_route=effective_path,
        )
        if load_previous_state:
            upsert_staff_presence_snapshot(
                user_id=uid,
                session_id=sid,
                snapshot=payload,
                now=now,
                existing_row=prev,
            )
        else:
            upsert_staff_presence_snapshot(
                user_id=uid,
                session_id=sid,
                snapshot=payload,
                now=now,
            )

        if log_event and _should_log_live_event(
            user_id=uid,
            event_type=(event_type or "heartbeat"),
            path=effective_path,
            action_hint=effective_hint,
            entity_id=effective_entity_id,
        ):
            meta = {
                "event_type": (event_type or "heartbeat"),
                "action_hint": effective_hint,
                "entity_display": effective_entity_display,
                "entity_name": (entity_name or "")[:120],
                "entity_code": (entity_code or "")[:60],
                "route_label": (route_label or "")[:120],
                "action_label": (action_label or "")[:120],
            }
            _audit_log(
                action_type=effective_action_type,
                entity_type=effective_entity_type or None,
                entity_id=effective_entity_id or None,
                summary=effective_action_human,
                metadata=meta,
                success=True,
            )
    except Exception:
        return


def _presence_rows() -> list[dict]:
    now = utc_now_naive()
    raw_rows = list_recent_staff_presence_states(max_age_seconds=(_PRESENCE_TTL_SECONDS * 3))
    if not raw_rows:
        return []

    grouped: dict[int, list[StaffPresenceState]] = {}
    for row in raw_rows:
        grouped.setdefault(int(row.user_id), []).append(row)

    user_ids = sorted(grouped.keys())
    users_map = {}
    if user_ids:
        users = StaffUser.query.filter(StaffUser.id.in_(user_ids)).all()
        users_map = {int(u.id): u for u in users}

    last_action_map = {}
    latest_subq = (
        db.session.query(
            StaffAuditLog.actor_user_id.label("uid"),
            func.max(StaffAuditLog.id).label("max_id"),
        )
        .filter(StaffAuditLog.actor_user_id.in_(user_ids))
        .group_by(StaffAuditLog.actor_user_id)
        .subquery()
    )
    action_rows = (
        db.session.query(StaffAuditLog)
        .join(latest_subq, StaffAuditLog.id == latest_subq.c.max_id)
        .all()
    )
    for item in action_rows:
        last_action_map[int(item.actor_user_id)] = item

    last_serialized_map: dict[int, dict] = {}
    if last_action_map:
        logs = list(last_action_map.values())
        entity_map = _build_entity_display_map(logs)
        for item in logs:
            if item.actor_user_id is None:
                continue
            last_serialized_map[int(item.actor_user_id)] = _serialize_log_item(item, entity_display_map=entity_map)

    status_priority = {"active": 0, "idle": 1, "hidden": 2, "inactive": 3}
    status_human_map = {
        "active": "Activa",
        "idle": "Inactiva",
        "hidden": "Oculta",
        "inactive": "Desconectada",
    }
    out: list[dict] = []
    for uid, sessions in grouped.items():
        per_session: list[dict] = []
        last = last_action_map.get(uid)
        last_serialized = last_serialized_map.get(uid) or {}
        for row in sessions:
            seen_at = row.last_seen_at
            if seen_at is None:
                continue
            interaction_at = row.last_interaction_at or seen_at
            delta = max(0, int((now - seen_at).total_seconds()))
            interaction_delta = max(0, int((now - interaction_at).total_seconds()))
            client_status = _normalize_client_status(row.client_status)
            if client_status == "active" and interaction_delta > _PRESENCE_INTERACTION_ACTIVE_SECONDS:
                client_status = "idle"
            status = _resolve_presence_status(delta, client_status)
            entity_display = _human_entity_display(row.entity_name, row.entity_code) or (last_serialized.get("entity_display") or "").strip()
            current_action_human = (row.action_label or last_serialized.get("action_human") or _humanize_action(
                getattr(last, "action_type", None),
                getattr(last, "summary", None),
                getattr(last, "metadata_json", {}),
                route=row.route,
                action_hint=row.current_action,
            ))[:120]
            current_action_human = _humanize_presence_action(
                current_action_human,
                row.current_action,
                row.entity_type,
                entity_display,
            )[:120]
            per_session.append(
                {
                    "session_id": row.session_id,
                    "status": status,
                    "status_human": status_human_map.get(status, "Sin estado"),
                    "client_status": client_status,
                    "last_seen_seconds": delta,
                    "last_interaction_seconds": interaction_delta,
                    "last_interaction_at": iso_utc_z(interaction_at),
                    "last_interaction_human": (
                        f"Hace {interaction_delta}s"
                        if interaction_delta < 60
                        else f"Hace {max(1, int(interaction_delta / 60))}m"
                    ),
                    "route": row.route,
                    "route_label": row.route_label or _humanize_route(row.route),
                    "page_title": row.page_title,
                    "current_action": row.current_action,
                    "action_label": row.action_label,
                    "current_action_human": current_action_human,
                    "entity_type": row.entity_type,
                    "entity_id": row.entity_id,
                    "entity_display": entity_display,
                    "tab_visible": bool(row.tab_visible),
                    "is_idle": bool(row.is_idle),
                    "is_typing": bool(row.is_typing),
                    "has_unsaved_changes": bool(row.has_unsaved_changes),
                    "modal_open": bool(row.modal_open),
                    "lock_owner": row.lock_owner or "",
                    "updated_at": iso_utc_z(row.updated_at) if row.updated_at else None,
                    "_updated_epoch": int(row.updated_at.timestamp()) if row.updated_at else 0,
                    "_seen_epoch": float(row.last_seen_at.timestamp()) if row.last_seen_at else 0.0,
                    "_row_id": int(row.id or 0),
                }
            )

        if not per_session:
            continue
        per_session.sort(
            key=lambda x: (
                status_priority.get(str(x.get("status") or ""), 99),
                -1
                if (
                    str(x.get("entity_id") or "").strip()
                    and str(x.get("current_action") or "").strip().lower() not in {"", "browsing", "dashboard"}
                )
                else 0,
                -float(x.get("_seen_epoch") or 0.0),
                -int(x.get("_updated_epoch") or 0),
                -int(x.get("_row_id") or 0),
            )
        )
        primary = per_session[0]
        for item in per_session:
            item.pop("_seen_epoch", None)
            item.pop("_updated_epoch", None)
            item.pop("_row_id", None)
        usr = users_map.get(uid)
        username = getattr(usr, "username", None) or str(uid)
        role = (getattr(usr, "role", None) or "").strip().lower()
        out.append(
            {
                "user_id": uid,
                "username": username,
                "role": role,
                "status": primary.get("status"),
                "status_human": status_human_map.get(str(primary.get("status") or ""), "Sin estado"),
                "current_path": primary.get("route"),
                "route_human": _humanize_route(primary.get("route")),
                "route_label": primary.get("route_label") or _humanize_route(primary.get("route")),
                "page_title": primary.get("page_title"),
                "last_seen_seconds": primary.get("last_seen_seconds"),
                "last_interaction_at": primary.get("last_interaction_at"),
                "last_interaction_seconds": primary.get("last_interaction_seconds"),
                "last_interaction_human": (
                    f"Hace {int(primary.get('last_interaction_seconds') or 0)}s"
                    if int(primary.get("last_interaction_seconds") or 0) < 60
                    else f"Hace {max(1, int((int(primary.get('last_interaction_seconds') or 0)) / 60))}m"
                ),
                "client_status": primary.get("client_status"),
                "last_action_hint": primary.get("current_action"),
                "action_type": primary.get("current_action"),
                "action_hint": primary.get("current_action"),
                "action_label": primary.get("action_label"),
                "current_action_human": primary.get("current_action_human"),
                "supervision_human": (
                    f"{primary.get('current_action_human')}"
                    if str(primary.get("current_action_human") or "").strip()
                    else "Sin actividad reciente"
                ),
                "entity_type": primary.get("entity_type"),
                "entity_id": primary.get("entity_id"),
                "entity_display": primary.get("entity_display"),
                "tab_visible": bool(primary.get("tab_visible")),
                "is_idle": bool(primary.get("is_idle")),
                "is_typing": bool(primary.get("is_typing")),
                "has_unsaved_changes": bool(primary.get("has_unsaved_changes")),
                "modal_open": bool(primary.get("modal_open")),
                "lock_owner": primary.get("lock_owner") or "",
                "session_count": len(per_session),
                "sessions": per_session,
                "last_action_type": getattr(last, "action_type", None),
                "last_action_summary": getattr(last, "summary", None),
                "last_action_at": iso_utc_z(getattr(last, "created_at", None)) if getattr(last, "created_at", None) else None,
            }
        )

    out.sort(key=lambda x: (status_priority.get(str(x.get("status") or "").lower(), 99), x.get("last_seen_seconds", 999999)))
    return out


def _presence_active_rows(rows: list[dict] | None = None) -> list[dict]:
    src = rows if rows is not None else _presence_rows()
    return [r for r in (src or []) if (r.get("status") == "active")]


def _build_presence_conflicts(active_rows: list[dict] | None = None) -> list[dict]:
    active_rows = active_rows if active_rows is not None else _presence_active_rows()
    by_entity: dict[str, list[dict]] = {}
    for row in active_rows:
        if (row.get("entity_type") or "") != "candidata":
            continue
        hint = (row.get("action_hint") or "").strip().lower()
        if "edit" not in hint and hint not in {"interview", "references"}:
            continue
        key = str(row.get("entity_id") or "").strip()
        if not key:
            continue
        by_entity.setdefault(key, []).append(row)

    out: list[dict] = []
    for entity_id, rows in by_entity.items():
        usernames = sorted({str(r.get("username") or "") for r in rows if r.get("username")})
        if len(usernames) < 2:
            continue
        human_name = rows[0].get("entity_display") or f"Candidata ID {entity_id}"
        emit_critical_alert(
            rule="editing_conflict",
            summary=f"Conflicto: {' y '.join(usernames[:2])} estan editando {human_name}",
            entity_type="candidata",
            entity_id=str(entity_id),
            metadata={
                "users": usernames,
                "entity_display": human_name,
                "source": "control_room",
            },
            dedupe_seconds=180,
            telegram=True,
        )
        out.append(
            {
                "entity_type": "candidata",
                "entity_id": entity_id,
                "entity_display": human_name,
                "users": usernames,
                "message": "Dos usuarias editando la misma candidata",
            }
        )
    return out


def _build_operations_metrics_payload(active_rows: list[dict] | None = None) -> dict:
    active_rows = active_rows if active_rows is not None else _presence_active_rows()
    now = utc_now_naive()
    day_start, _ = rd_day_range_utc_naive()
    active_secretarias = len([r for r in active_rows if (r.get("role") or "").lower() == "secretaria"])
    candidatas_editing = len(
        [
            r for r in active_rows
            if (r.get("entity_type") == "candidata")
            and ("edit" in (r.get("action_hint") or "").lower() or (r.get("action_hint") in {"interview", "references"}))
        ]
    )
    entrevistas_hoy = (
        StaffAuditLog.query
        .filter(StaffAuditLog.created_at >= day_start)
        .filter(StaffAuditLog.action_type.in_(["CANDIDATA_INTERVIEW_NEW_CREATE", "CANDIDATA_INTERVIEW_LEGACY_SAVE"]))
        .count()
    )
    matching_hoy = (
        StaffAuditLog.query
        .filter(StaffAuditLog.created_at >= day_start, StaffAuditLog.action_type == "MATCHING_SEND")
        .count()
    )
    try:
        solicitudes_en_proceso = Solicitud.query.filter(Solicitud.estado == "proceso").count()
    except Exception:
        solicitudes_en_proceso = 0
    return {
        "active_secretarias": int(active_secretarias),
        "candidatas_editing_now": int(candidatas_editing),
        "solicitudes_en_proceso": int(solicitudes_en_proceso),
        "entrevistas_hoy": int(entrevistas_hoy),
        "matching_hoy": int(matching_hoy),
    }


def _build_activity_stream_payload(limit: int = 20) -> list[dict]:
    logs = (
        StaffAuditLog.query
        .order_by(StaffAuditLog.id.desc())
        .limit(min(100, max(5, int(limit))))
        .all()
    )
    if not logs:
        return []
    logs = list(reversed(logs))
    actor_ids = sorted({int(l.actor_user_id) for l in logs if l.actor_user_id is not None})
    username_map = {}
    if actor_ids:
        users = StaffUser.query.filter(StaffUser.id.in_(actor_ids)).all()
        username_map = {int(u.id): u.username for u in users}
    entity_display_map = _build_entity_display_map(logs)
    items = [_serialize_log_item(log, username_map=username_map, entity_display_map=entity_display_map) for log in logs]
    return items[-limit:]


def _serialize_log_item(
    log: StaffAuditLog,
    username_map: dict[int, str] | None = None,
    entity_display_map: dict[tuple[str, str], str] | None = None,
) -> dict:
    username_map = username_map or {}
    entity_display_map = entity_display_map or {}
    metadata = dict(getattr(log, "metadata_json", {}) or {})
    changes = getattr(log, "changes_json", None)
    for key in ("telefono", "numero_telefono", "phone", "phone_number", "whatsapp"):
        metadata.pop(key, None)
    entity_type = _normalize_entity_type(log.entity_type)
    entity_id = str(log.entity_id or "").strip()
    entity_display = None
    if entity_type and entity_id:
        entity_display = entity_display_map.get((entity_type, entity_id))
    if not entity_display:
        entity_display = _entity_display_from_metadata(metadata, entity_type, entity_id)

    action_hint = (metadata.get("action_hint") or "").strip().lower()
    action_human = _humanize_action(
        log.action_type,
        summary=log.summary,
        metadata=metadata,
        route=log.route,
        action_hint=action_hint,
    )
    icon_name, icon_class = _action_icon(log.action_type, success=bool(log.success))
    changes_human = _changes_to_human(changes)
    metadata_human = _metadata_human(metadata)
    created_human = _humanize_datetime(log.created_at)
    summary_human = _humanize_summary(action_human, log.summary, log.route)
    if str(log.action_type or "").upper() == "CANDIDATA_EDIT" and changes_human:
        fields_summary = summarize_changed_fields(changes_human, max_items=4)
        action_human = f"Edito candidata ({fields_summary})"
        summary_human = f"Edito: {fields_summary}"
    return {
        "id": int(log.id),
        "created_at": iso_utc_z(log.created_at) if log.created_at else None,
        "created_at_human": created_human,
        "actor_user_id": log.actor_user_id,
        "actor_username": username_map.get(int(log.actor_user_id)) if log.actor_user_id else None,
        "actor_role": log.actor_role,
        "action_type": log.action_type,
        "action_icon": icon_name,
        "action_icon_class": icon_class,
        "entity_type": entity_type or log.entity_type,
        "entity_id": entity_id or log.entity_id,
        "entity_display": entity_display,
        "action_human": action_human,
        "summary": log.summary,
        "summary_human": summary_human,
        "route": log.route,
        "route_human": _humanize_route(log.route),
        "method": log.method,
        "success": bool(log.success),
        "metadata_json": metadata,
        "metadata_human": metadata_human,
        "changes_json": changes,
        "changes_human": changes_human,
    }


def _logs_filtered_query(args=None):
    args = args or request.args
    q = StaffAuditLog.query

    user_id = args.get("actor_user_id", type=int) or args.get("user_id", type=int)
    action_type = (args.get("action_type") or "").strip()
    entity_type = (args.get("entity_type") or "").strip()
    date_from = _parse_monitoreo_date(args.get("date_from"))
    date_to = _parse_monitoreo_date(args.get("date_to"), end_of_day=True)
    search = (args.get("search") or "").strip()[:100]
    success_raw = (args.get("success") or "").strip().lower()
    since_id = args.get("since_id", type=int)

    if user_id:
        q = q.filter(StaffAuditLog.actor_user_id == user_id)
    if action_type:
        q = q.filter(StaffAuditLog.action_type == action_type)
    if entity_type:
        if entity_type.lower() == "candidata":
            q = q.filter(func.lower(StaffAuditLog.entity_type).in_(["candidata"]))
        else:
            q = q.filter(StaffAuditLog.entity_type == entity_type)
    if date_from:
        q = q.filter(StaffAuditLog.created_at >= date_from)
    if date_to:
        q = q.filter(StaffAuditLog.created_at < date_to)
    if since_id and since_id > 0:
        q = q.filter(StaffAuditLog.id > since_id)
    if success_raw in {"1", "true", "yes", "ok"}:
        q = q.filter(StaffAuditLog.success.is_(True))
    elif success_raw in {"0", "false", "no", "error"}:
        q = q.filter(StaffAuditLog.success.is_(False))
    if search:
        like = f"%{search}%"
        q = q.filter(or_(StaffAuditLog.entity_id.ilike(like), StaffAuditLog.summary.ilike(like)))

    return q


def _activity_ranking(since_dt: datetime, until_dt: datetime | None = None, only_secretarias: bool = False):
    rows = (
        db.session.query(
            StaffAuditLog.actor_user_id,
            StaffUser.username,
            StaffUser.role,
            func.count(StaffAuditLog.id).label("total"),
        )
        .join(StaffUser, StaffUser.id == StaffAuditLog.actor_user_id)
        .filter(StaffAuditLog.created_at >= since_dt)
    )
    if until_dt:
        rows = rows.filter(StaffAuditLog.created_at < until_dt)
    if only_secretarias:
        rows = rows.filter(func.lower(StaffUser.role) == "secretaria")
    rows = (
        rows.group_by(StaffAuditLog.actor_user_id, StaffUser.username, StaffUser.role)
        .order_by(desc("total"), StaffUser.username.asc())
        .all()
    )
    return rows


def _window_metrics_payload(start_dt: datetime, end_dt: datetime | None = None) -> dict:
    query = db.session.query(
        func.count(StaffAuditLog.id).label("total_actions"),
        func.sum(case((StaffAuditLog.action_type == "SOLICITUD_CREATE", 1), else_=0)).label("solicitudes_creadas"),
        func.sum(case((StaffAuditLog.action_type == "SOLICITUD_PUBLICAR", 1), else_=0)).label("solicitudes_publicadas"),
        func.sum(case((StaffAuditLog.action_type == "CANDIDATA_EDIT", 1), else_=0)).label("candidatas_editadas"),
        func.sum(case((StaffAuditLog.action_type == "MATCHING_SEND", 1), else_=0)).label("candidatas_enviadas"),
    ).filter(StaffAuditLog.created_at >= start_dt)
    if end_dt:
        query = query.filter(StaffAuditLog.created_at < end_dt)
    row = query.one()
    return {
        "total_actions": int(row.total_actions or 0),
        "solicitudes_creadas": int(row.solicitudes_creadas or 0),
        "solicitudes_publicadas": int(row.solicitudes_publicadas or 0),
        "candidatas_editadas": int(row.candidatas_editadas or 0),
        "candidatas_enviadas": int(row.candidatas_enviadas or 0),
    }


def _build_productivity_today_payload() -> dict:
    now = utc_now_naive()
    day_start, _ = rd_day_range_utc_naive()
    interview_actions = ("CANDIDATA_INTERVIEW_NEW_CREATE", "CANDIDATA_INTERVIEW_LEGACY_SAVE")

    rows = (
        db.session.query(
            StaffAuditLog.actor_user_id.label("user_id"),
            StaffUser.username.label("username"),
            StaffUser.role.label("role"),
            func.sum(case((StaffAuditLog.action_type == "CANDIDATA_EDIT", 1), else_=0)).label("edits"),
            func.sum(case((StaffAuditLog.action_type.in_(interview_actions), 1), else_=0)).label("interviews"),
            func.sum(case((StaffAuditLog.action_type == "MATCHING_SEND", 1), else_=0)).label("sent"),
            func.count(StaffAuditLog.id).label("total"),
        )
        .join(StaffUser, StaffUser.id == StaffAuditLog.actor_user_id)
        .filter(StaffAuditLog.created_at >= day_start)
        .filter(StaffAuditLog.actor_user_id.isnot(None))
        .filter(StaffAuditLog.action_type.in_(_PRODUCTIVITY_ACTIONS))
        .group_by(StaffAuditLog.actor_user_id, StaffUser.username, StaffUser.role)
        .order_by(desc("total"), StaffUser.username.asc())
        .all()
    )

    users = []
    for row in rows:
        users.append(
            {
                "user_id": int(row.user_id),
                "username": row.username,
                "role": row.role,
                "edits": int(row.edits or 0),
                "interviews": int(row.interviews or 0),
                "sent": int(row.sent or 0),
                "total": int(row.total or 0),
            }
        )
    return {"users": users}


def _build_monitoreo_summary_payload(
    *,
    include_presence: bool = True,
    include_presence_conflicts: bool = True,
    include_operations: bool = True,
    include_activity_stream: bool = True,
) -> dict:
    now = utc_now_naive()
    day_start, _ = rd_day_range_utc_naive()
    week_start = now - timedelta(days=7)
    month_start = now - timedelta(days=30)
    top = _activity_ranking(month_start, only_secretarias=True)
    needs_presence_rows = bool(include_presence or include_presence_conflicts or include_operations)
    presence = _presence_rows() if needs_presence_rows else []
    active_presence = _presence_active_rows(presence) if needs_presence_rows else []
    conflicts = _build_presence_conflicts(active_presence) if include_presence_conflicts else []
    alerts = get_alert_items(limit=10, scope="critical", include_resolved=False)
    payload = {
        "generated_at": iso_utc_z(now),
        "today": _window_metrics_payload(day_start),
        "week": _window_metrics_payload(week_start),
        "month": _window_metrics_payload(month_start),
        "top": [
            {
                "user_id": int(r.actor_user_id),
                "username": r.username,
                "role": r.role,
                "total_actions": int(r.total or 0),
            }
            for r in top[:10]
        ],
        "presence_active_count": len(active_presence),
        "presence_conflicts": conflicts,
        "productivity": _build_productivity_today_payload(),
        "alerts": alerts,
        "critical_alerts": alerts,
    }
    if include_presence:
        payload["presence"] = presence
    if include_operations:
        payload["operations"] = _build_operations_metrics_payload(active_presence)
    if include_activity_stream:
        payload["activity_stream"] = _build_activity_stream_payload(limit=20)
    return payload


def _stream_active_presence_for_operations(latest_active_presence: list[dict] | None) -> list[dict]:
    if latest_active_presence is not None:
        return latest_active_presence
    return _presence_active_rows()


def _resolve_candidata_from_entity_id(entity_id: str):
    val = (entity_id or "").strip()
    if not val:
        return None
    cand = None
    if val.isdigit():
        cand = Candidata.query.filter_by(fila=int(val)).first()
    if cand is None:
        cand = Candidata.query.filter(Candidata.codigo == val).first()
    return cand


def _sanitize_monitoreo_metadata(meta: dict | None) -> dict:
    out = dict(meta or {})
    for key in (
        "telefono",
        "numero_telefono",
        "phone",
        "phone_number",
        "whatsapp",
        "cedula",
        "dni",
        "documento",
        "direccion",
        "address",
        "email",
        "correo",
    ):
        out.pop(key, None)
    return out


def _candidata_logs_query(candidata_entity_id: str, filter_tag: str = ""):
    q = (
        StaffAuditLog.query
        .filter(func.lower(StaffAuditLog.entity_type) == "candidata")
        .filter(StaffAuditLog.entity_id == str(candidata_entity_id))
    )

    tag = (filter_tag or "").strip().lower()
    if tag == "edits":
        q = q.filter(StaffAuditLog.action_type.in_(["CANDIDATA_EDIT"]))
    elif tag == "entrevistas":
        q = q.filter(StaffAuditLog.action_type.in_(["CANDIDATA_INTERVIEW_NEW_CREATE", "CANDIDATA_INTERVIEW_LEGACY_SAVE"]))
    elif tag == "docs":
        q = q.filter(StaffAuditLog.action_type.in_(["CANDIDATA_UPLOAD_DOCS"]))
    elif tag == "matching":
        q = q.filter(StaffAuditLog.action_type.in_(["MATCHING_SEND"]))
    elif tag == "fallos":
        q = q.filter(StaffAuditLog.success.is_(False))
    return q


@admin_bp.route('/monitoreo', methods=['GET'])
@login_required
@admin_required
def monitoreo_staff():
    if _admin_async_wants_json():
        return jsonify(_admin_async_payload(
            success=True,
            message="Shell del dashboard actualizada.",
            category="success",
            replace_html=render_template("admin/_monitoreo_dashboard_shell_region.html"),
            update_target="#monitoreoDashboardShellAsyncRegion",
            redirect_url=url_for("admin.monitoreo_staff"),
        ))

    summary = _build_monitoreo_summary_payload()

    latest_logs = (
        _logs_filtered_query()
        .order_by(StaffAuditLog.created_at.desc())
        .limit(30)
        .all()
    )
    actor_ids = sorted({int(l.actor_user_id) for l in latest_logs if l.actor_user_id is not None})
    username_map = {}
    if actor_ids:
        users = StaffUser.query.filter(StaffUser.id.in_(actor_ids)).all()
        username_map = {int(u.id): u.username for u in users}
    latest_entity_map = _build_entity_display_map(latest_logs)
    latest_logs_items = [
        _serialize_log_item(log, username_map=username_map, entity_display_map=latest_entity_map)
        for log in latest_logs
    ]

    monitoreo_alerts = list(summary.get("critical_alerts") or summary.get("alerts") or [])

    return render_template(
        "admin/monitoreo.html",
        latest_logs=latest_logs_items,
        monitoreo_alerts=monitoreo_alerts,
        summary_payload=summary,
        initial_last_id=int(latest_logs[0].id) if latest_logs else 0,
    )


@admin_bp.route('/monitoreo/logs', methods=['GET'])
@login_required
@admin_required
def monitoreo_logs():
    page = max(1, request.args.get("page", default=1, type=int))
    per_page = min(100, max(10, request.args.get("per_page", default=25, type=int)))

    try:
        pagination = _logs_filtered_query().order_by(StaffAuditLog.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
        users = StaffUser.query.order_by(StaffUser.username.asc()).all()
        action_types = [r[0] for r in db.session.query(StaffAuditLog.action_type).distinct().order_by(StaffAuditLog.action_type.asc()).all()]
        entity_types = [r[0] for r in db.session.query(StaffAuditLog.entity_type).filter(StaffAuditLog.entity_type.isnot(None)).distinct().order_by(StaffAuditLog.entity_type.asc()).all()]
        user_map = {u.id: u for u in users}
        username_map = {int(u.id): u.username for u in users}
        entity_display_map = _build_entity_display_map(list(pagination.items))
        logs_items = [
            _serialize_log_item(log, username_map=username_map, entity_display_map=entity_display_map)
            for log in pagination.items
        ]
    except Exception:
        if _admin_async_wants_json():
            current_app.logger.exception("monitoreo_logs_async_error")
            return jsonify(_admin_async_payload(
                success=False,
                message="No se pudo actualizar el listado de logs. Intenta de nuevo.",
                category="danger",
                error_code="internal_error",
            )), 500
        raise

    list_ctx = dict(
        logs=logs_items,
        pagination=pagination,
        users=users,
        user_map=user_map,
        action_types=action_types,
        entity_types=entity_types,
        initial_last_id=int(pagination.items[0].id) if pagination.items else 0,
        has_active_filters=bool(
            request.args.get("user_id")
            or request.args.get("actor_user_id")
            or request.args.get("action_type")
            or request.args.get("entity_type")
            or request.args.get("date_from")
            or request.args.get("date_to")
            or request.args.get("search")
            or request.args.get("success")
        ),
    )

    if _admin_async_wants_json():
        html = render_template("admin/_monitoreo_logs_results.html", **list_ctx)
        return jsonify(_admin_async_payload(
            success=True,
            message="Listado actualizado.",
            category="info",
            replace_html=html,
            update_target="#monitoreoLogsAsyncRegion",
            extra={
                "page": pagination.page,
                "per_page": per_page,
                "total": pagination.total,
            },
        )), 200

    return render_template("admin/monitoreo_logs.html", **list_ctx)


@admin_bp.route('/monitoreo/candidatas', methods=['GET'])
@login_required
@admin_required
def monitoreo_candidatas_search():
    q = (request.args.get("q") or "").strip()[:128]
    limit = min(50, max(1, request.args.get("limit", default=20, type=int)))
    rows = []
    if q:
        like = f"%{q}%"
        digits = re.sub(r"\D+", "", q)
        filters = [
            Candidata.nombre_completo.ilike(like),
            Candidata.cedula.ilike(like),
            Candidata.codigo.ilike(like),
            cast(Candidata.fila, db.String).ilike(like),
        ]
        if digits:
            filters.append(Candidata.cedula_norm_digits.ilike(f"%{digits}%"))
        rows = (
            Candidata.query
            .filter(or_(*filters))
            .order_by(Candidata.fila.desc())
            .limit(limit)
            .all()
        )
    list_ctx = dict(
        q=q,
        limit=limit,
        rows=rows,
    )
    if _admin_async_wants_json():
        redirect_url = url_for(
            "admin.monitoreo_candidatas_search",
            q=(q or None),
            limit=limit,
        )
        html = render_template("admin/_monitoreo_candidatas_search_results.html", **list_ctx)
        return jsonify(_admin_async_payload(
            success=True,
            message="Listado actualizado.",
            category="info",
            redirect_url=redirect_url,
            replace_html=html,
            update_target="#monitoreoCandidatasAsyncRegion",
            extra={
                "query": q,
                "limit": limit,
                "count": len(rows),
            },
        )), 200
    return render_template(
        "admin/monitoreo_candidatas_search.html",
        **list_ctx,
    )


def _render_monitoreo_alertas_region(*, alerts=None, next_url: str | None = None) -> str:
    return render_template(
        "admin/_monitoreo_alertas_region.html",
        alerts=list(alerts or []),
        next_url=(next_url or url_for("admin.monitoreo_staff")),
    )


def _monitoreo_dashboard_alerts() -> list[dict]:
    return list(get_alert_items(limit=10, scope="critical", include_resolved=False) or [])


@admin_bp.route('/monitoreo/candidatas/<candidata_entity_id>', methods=['GET'])
@login_required
@admin_required
def monitoreo_candidata_historial(candidata_entity_id: str):
    filter_tag = (request.args.get("filter") or "").strip().lower()
    cand = _resolve_candidata_from_entity_id(candidata_entity_id)
    logs = (
        _candidata_logs_query(candidata_entity_id, filter_tag)
        .order_by(StaffAuditLog.created_at.desc())
        .limit(300)
        .all()
    )
    actor_ids = sorted({int(l.actor_user_id) for l in logs if l.actor_user_id is not None})
    username_map = {}
    if actor_ids:
        users = StaffUser.query.filter(StaffUser.id.in_(actor_ids)).all()
        username_map = {int(u.id): u.username for u in users}
    entity_display_map = _build_entity_display_map(logs)
    items = [_serialize_log_item(log, username_map=username_map, entity_display_map=entity_display_map) for log in logs]
    for item in items:
        item["metadata_json"] = _sanitize_monitoreo_metadata(item.get("metadata_json"))
    list_ctx = dict(
        candidata_entity_id=str(candidata_entity_id),
        candidata=cand,
        candidata_meta=(candidata_entity_meta(cand) if cand else {}),
        logs=items,
        active_filter=filter_tag,
        initial_last_id=max([i["id"] for i in items], default=0),
    )
    if _admin_async_wants_json():
        redirect_url = url_for(
            "admin.monitoreo_candidata_historial",
            candidata_entity_id=candidata_entity_id,
            filter=(filter_tag or None),
        )
        html = render_template("admin/_monitoreo_candidata_historial_region.html", **list_ctx)
        return jsonify(_admin_async_payload(
            success=True,
            message="Historial actualizado.",
            category="info",
            redirect_url=redirect_url,
            replace_html=html,
            update_target="#monitoreoCandidataHistorialAsyncRegion",
        )), 200
    return render_template(
        "admin/monitoreo_candidata_historial.html",
        **list_ctx,
    )


@admin_bp.route('/monitoreo/secretarias/<int:user_id>', methods=['GET'])
@login_required
@admin_required
def monitoreo_secretaria(user_id: int):
    user = StaffUser.query.get_or_404(user_id)
    page = max(1, request.args.get("page", default=1, type=int))
    per_page = min(100, max(10, request.args.get("per_page", default=25, type=int)))

    date_from = _parse_monitoreo_date(request.args.get("date_from"))
    date_to = _parse_monitoreo_date(request.args.get("date_to"), end_of_day=True)

    q = StaffAuditLog.query.filter(StaffAuditLog.actor_user_id == user.id)
    if date_from:
        q = q.filter(StaffAuditLog.created_at >= date_from)
    if date_to:
        q = q.filter(StaffAuditLog.created_at < date_to)
    q = q.order_by(StaffAuditLog.created_at.desc())

    pagination = q.paginate(page=page, per_page=per_page, error_out=False)
    username_map = {int(user.id): user.username}
    entity_display_map = _build_entity_display_map(list(pagination.items))
    logs_items = [
        _serialize_log_item(log, username_map=username_map, entity_display_map=entity_display_map)
        for log in pagination.items
    ]

    now = utc_now_naive()
    since = now - timedelta(days=30)
    per_day_rows = (
        db.session.query(
            func.date(StaffAuditLog.created_at).label("day"),
            func.count(StaffAuditLog.id).label("total"),
        )
        .filter(StaffAuditLog.actor_user_id == user.id, StaffAuditLog.created_at >= since)
        .group_by(func.date(StaffAuditLog.created_at))
        .order_by(func.date(StaffAuditLog.created_at).asc())
        .all()
    )

    date_from_raw = (request.args.get("date_from") or "").strip()
    date_to_raw = (request.args.get("date_to") or "").strip()
    list_ctx = dict(
        target_user=user,
        logs=logs_items,
        pagination=pagination,
        per_day_rows=per_day_rows,
        date_from_q=date_from_raw,
        date_to_q=date_to_raw,
    )
    if _admin_async_wants_json():
        redirect_url = url_for(
            "admin.monitoreo_secretaria",
            user_id=user.id,
            page=pagination.page,
            date_from=(date_from_raw or None),
            date_to=(date_to_raw or None),
        )
        html = render_template("admin/_monitoreo_secretaria_region.html", **list_ctx)
        return jsonify(_admin_async_payload(
            success=True,
            message="Listado actualizado.",
            category="info",
            redirect_url=redirect_url,
            replace_html=html,
            update_target="#monitoreoSecretariaAsyncRegion",
            extra={
                "page": pagination.page,
                "total": pagination.total,
                "pages": pagination.pages or 1,
            },
        )), 200

    return render_template("admin/monitoreo_secretaria.html", **list_ctx)


@admin_bp.route('/monitoreo/logs.json', methods=['GET'])
@login_required
@admin_required
def monitoreo_logs_json():
    limit = min(200, max(1, request.args.get("limit", default=50, type=int)))
    query = _logs_filtered_query()
    since_id = request.args.get("since_id", type=int) or 0

    if since_id > 0:
        logs = (
            query
            .filter(StaffAuditLog.id > since_id)
            .order_by(StaffAuditLog.id.asc())
            .limit(limit)
            .all()
        )
    else:
        logs = query.order_by(StaffAuditLog.id.desc()).limit(limit).all()
        logs = list(reversed(logs))

    actor_ids = sorted({int(l.actor_user_id) for l in logs if l.actor_user_id is not None})
    username_map = {}
    if actor_ids:
        rows = StaffUser.query.filter(StaffUser.id.in_(actor_ids)).all()
        username_map = {int(u.id): u.username for u in rows}

    entity_display_map = _build_entity_display_map(logs)
    items = [_serialize_log_item(log, username_map=username_map, entity_display_map=entity_display_map) for log in logs]
    last_id = max([i["id"] for i in items], default=(since_id or 0))
    return jsonify({"items": items, "last_id": int(last_id)})


@admin_bp.route('/monitoreo/summary.json', methods=['GET'])
@login_required
@admin_required
def monitoreo_summary_json():
    return jsonify(_build_monitoreo_summary_payload())


@admin_bp.route('/monitoreo/productividad.json', methods=['GET'])
@login_required
@admin_required
def monitoreo_productividad_json():
    if not bool(session.get("is_admin_session")):
        abort(403)
    return jsonify(_build_productivity_today_payload())


@admin_bp.route('/monitoreo/presence.json', methods=['GET'])
@login_required
@admin_required
def monitoreo_presence_json():
    return jsonify({"items": _presence_rows()})


@admin_bp.route('/monitoreo/candidatas/<candidata_entity_id>/logs.json', methods=['GET'])
@login_required
@admin_required
def monitoreo_candidata_logs_json(candidata_entity_id: str):
    since_id = request.args.get("since_id", type=int) or 0
    limit = min(300, max(1, request.args.get("limit", default=50, type=int)))
    filter_tag = (request.args.get("filter") or "").strip().lower()
    query = _candidata_logs_query(candidata_entity_id, filter_tag)
    if since_id > 0:
        query = query.filter(StaffAuditLog.id > since_id)
        logs = query.order_by(StaffAuditLog.id.asc()).limit(limit).all()
    else:
        logs = query.order_by(StaffAuditLog.id.desc()).limit(limit).all()
        logs = list(reversed(logs))

    actor_ids = sorted({int(l.actor_user_id) for l in logs if l.actor_user_id is not None})
    username_map = {}
    if actor_ids:
        users = StaffUser.query.filter(StaffUser.id.in_(actor_ids)).all()
        username_map = {int(u.id): u.username for u in users}
    entity_display_map = _build_entity_display_map(logs)
    items = [_serialize_log_item(log, username_map=username_map, entity_display_map=entity_display_map) for log in logs]
    for item in items:
        item["metadata_json"] = _sanitize_monitoreo_metadata(item.get("metadata_json"))
    last_id = max([i["id"] for i in items], default=(since_id or 0))
    return jsonify({"items": items, "last_id": int(last_id)})


@admin_bp.route('/monitoreo/stream', methods=['GET'])
@login_required
@admin_required
def monitoreo_stream():
    def _sse(event: str, payload: dict) -> str:
        return f"event: {event}\\ndata: {json.dumps(payload, ensure_ascii=False)}\\n\\n"

    @stream_with_context
    def generate():
        try:
            if current_app.config.get("TESTING") and str(request.args.get("once") or "").strip() == "1":
                snapshot = _presence_rows()
                active = _presence_active_rows(snapshot)
                conflicts = _build_presence_conflicts(active)
                yield _sse(
                    "active_snapshot",
                    {
                        "items": snapshot,
                        "active_count": len(active),
                        "conflicts": conflicts,
                        "interval_sec": 1,
                    },
                )
                yield _sse(
                    "presence",
                    {
                        "items": snapshot,
                        "active_count": len(active),
                        "conflicts": conflicts,
                        "interval_sec": 1,
                    },
                )
                yield _sse("heartbeat", {"ts": iso_utc_z()})
                return

            last_id = request.args.get("last_id", type=int) or 0
            if last_id <= 0:
                max_id = db.session.query(func.max(StaffAuditLog.id)).scalar()
                last_id = int(max_id or 0)

            last_summary_at = 0.0
            last_presence_at = 0.0
            last_operations_at = 0.0
            last_activity_at = 0.0
            last_heartbeat_at = 0.0
            latest_active_presence = None
            while True:
                now_ts = time.time()

                new_logs = (
                    StaffAuditLog.query
                    .filter(StaffAuditLog.id > last_id)
                    .order_by(StaffAuditLog.id.asc())
                    .limit(100)
                    .all()
                )
                if new_logs:
                    actor_ids = sorted({int(l.actor_user_id) for l in new_logs if l.actor_user_id is not None})
                    username_map = {}
                    if actor_ids:
                        users = StaffUser.query.filter(StaffUser.id.in_(actor_ids)).all()
                        username_map = {int(u.id): u.username for u in users}
                    entity_display_map = _build_entity_display_map(new_logs)
                    for log in new_logs:
                        item = _serialize_log_item(log, username_map=username_map, entity_display_map=entity_display_map)
                        yield _sse("log", item)
                        last_id = max(last_id, int(log.id))

                if (now_ts - last_presence_at) >= 1.0:
                    presence = _presence_rows()
                    active = _presence_active_rows(presence)
                    latest_active_presence = active
                    conflicts = _build_presence_conflicts(active)
                    payload = {
                        "items": presence,
                        "active_count": len(active),
                        "conflicts": conflicts,
                        "interval_sec": 1,
                    }
                    # Emit both event names for backwards-compatible listeners.
                    yield _sse("active_snapshot", payload)
                    yield _sse("presence", payload)
                    last_presence_at = now_ts

                if (now_ts - last_summary_at) >= 5.0:
                    summary = _build_monitoreo_summary_payload(
                        include_presence=False,
                        include_activity_stream=False,
                    )
                    summary.pop("presence", None)
                    summary.pop("activity_stream", None)
                    yield _sse("summary", summary)
                    last_summary_at = now_ts

                if (now_ts - last_operations_at) >= 2.0:
                    active_for_operations = _stream_active_presence_for_operations(latest_active_presence)
                    yield _sse("operations", {"metrics": _build_operations_metrics_payload(active_for_operations)})
                    last_operations_at = now_ts

                if (now_ts - last_activity_at) >= 2.0:
                    yield _sse("activity", {"items": _build_activity_stream_payload(limit=20)})
                    last_activity_at = now_ts

                if (now_ts - last_heartbeat_at) >= 15.0:
                    yield _sse("heartbeat", {"ts": iso_utc_z()})
                    last_heartbeat_at = now_ts

                time.sleep(1.0)
        except (GeneratorExit, ConnectionError, OSError):
            return

    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return Response(generate(), mimetype="text/event-stream", headers=headers)


@admin_bp.route('/monitoreo/candidatas/<candidata_entity_id>/stream', methods=['GET'])
@login_required
@admin_required
def monitoreo_candidata_stream(candidata_entity_id: str):
    def _sse(event: str, payload: dict) -> str:
        return f"event: {event}\\ndata: {json.dumps(payload, ensure_ascii=False)}\\n\\n"

    @stream_with_context
    def generate():
        if current_app.config.get("TESTING") and str(request.args.get("once") or "").strip() == "1":
            yield _sse("heartbeat", {"ts": iso_utc_z()})
            return

        last_id = request.args.get("last_id", type=int) or 0
        if last_id <= 0:
            max_id = (
                _candidata_logs_query(candidata_entity_id)
                .with_entities(func.max(StaffAuditLog.id))
                .scalar()
            )
            last_id = int(max_id or 0)

        last_heartbeat_at = 0.0
        while True:
            now_ts = time.time()
            new_logs = (
                _candidata_logs_query(candidata_entity_id)
                .filter(StaffAuditLog.id > last_id)
                .order_by(StaffAuditLog.id.asc())
                .limit(100)
                .all()
            )
            if new_logs:
                actor_ids = sorted({int(l.actor_user_id) for l in new_logs if l.actor_user_id is not None})
                username_map = {}
                if actor_ids:
                    users = StaffUser.query.filter(StaffUser.id.in_(actor_ids)).all()
                    username_map = {int(u.id): u.username for u in users}
                entity_display_map = _build_entity_display_map(new_logs)
                for log in new_logs:
                    item = _serialize_log_item(log, username_map=username_map, entity_display_map=entity_display_map)
                    item["metadata_json"] = _sanitize_monitoreo_metadata(item.get("metadata_json"))
                    yield _sse("candidatelog", item)
                    last_id = max(last_id, int(log.id))

            if (now_ts - last_heartbeat_at) >= 15.0:
                yield _sse("heartbeat", {"ts": iso_utc_z()})
                last_heartbeat_at = now_ts
            time.sleep(2.0)

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return Response(generate(), mimetype="text/event-stream", headers=headers)


@admin_bp.route('/monitoreo/presence/ping', methods=['POST'])
@login_required
def monitoreo_presence_ping():
    if not bool(session.get("is_admin_session")):
        abort(403)
    if not isinstance(current_user, StaffUser):
        abort(403)
    if not bool(getattr(current_user, "is_active", False)):
        abort(403)
    role = role_for_user(current_user)
    if role not in ("owner", "admin", "secretaria"):
        abort(403)
    if not _live_access_allowed("presence_ping"):
        _audit_live_security_block(
            action_type="LIVE_ACCESS_DENIED",
            summary="Acceso denegado a presence ping",
            reason="presence_ping_role_forbidden",
            metadata={"path": request.path, "role": role, "capability": "presence_ping"},
        )
        abort(403)

    payload = request.get_json(silent=True) or {}
    current_path = (payload.get("current_path") or request.path or "").strip()[:255]
    page_title = (payload.get("page_title") or request.endpoint or "").strip()[:160]
    session_id = _resolve_presence_session_id((payload.get("session_id") or "").strip()[:120])
    dedupe_seconds = max(1, min(10, int(os.getenv("LIVE_PING_DEDUPE_SECONDS", "2") or 2)))
    should_count_rl = _presence_ping_should_count_rate_limit(
        user_id=int(getattr(current_user, "id", 0) or 0),
        session_id=session_id,
        state_hash=_presence_ping_state_hash(payload, current_path=current_path),
        dedupe_seconds=dedupe_seconds,
    )
    if should_count_rl:
        rl = _enforce_live_rate_limit("ping")
        if rl:
            _audit_live_security_block(
                action_type="LIVE_RATE_LIMITED",
                summary="Rate limit bloqueó presence ping",
                reason="presence_ping_rate_limited",
                metadata={"path": request.path, "scope": rl.get("scope"), "retry_after_sec": rl.get("retry_after_sec")},
            )
            response = jsonify({
                "ok": False,
                "error": "rate_limited",
                "scope": rl.get("scope"),
                "retry_after_sec": int(rl.get("retry_after_sec") or 1),
            })
            response.status_code = 429
            response.headers["Retry-After"] = str(int(rl.get("retry_after_sec") or 1))
            return response

    event_type = (payload.get("event_type") or "heartbeat").strip().lower()[:32]
    if event_type not in {
        "page_load",
        "heartbeat",
        "tab_focus",
        "open_entity",
        "submit",
        "intent_change",
        "typing_start",
        "typing_stop",
        "dirty_on",
        "dirty_off",
        "modal_open",
        "modal_close",
        "tab_hidden",
        "idle",
        "resume",
    }:
        event_type = "heartbeat"

    raw_action_type = (payload.get("action_type") or "").strip().upper()
    action_type = raw_action_type if _is_valid_live_action_type(raw_action_type) else _map_event_to_action_type(event_type)
    action_hint = (payload.get("action_hint") or payload.get("last_action_hint") or "").strip().lower()[:80]
    action_label = (payload.get("action_label") or "").strip()[:120]
    route_label = (payload.get("route_label") or "").strip()[:120]
    client_status = _normalize_client_status(payload.get("client_status"))
    last_interaction_at = (payload.get("last_interaction_at") or "").strip()[:40]
    tab_visible = payload.get("tab_visible")
    is_idle = payload.get("is_idle")
    is_typing = payload.get("is_typing")
    has_unsaved_changes = payload.get("has_unsaved_changes")
    modal_open = payload.get("modal_open")
    lock_owner = (payload.get("lock_owner") or "").strip()[:120]
    if not action_hint:
        action_hint = _infer_action_hint_from_path(current_path)[:80]
    ctx = _extract_entity_context(payload, current_path=current_path)
    action_human = _humanize_action(action_type, route=current_path, action_hint=action_hint)
    if not action_label:
        action_label = action_human

    _touch_staff_presence(
        current_path=current_path,
        page_title=page_title,
        route_label=route_label,
        last_action_hint=action_hint,
        event_type=event_type,
        action_type=action_type,
        action_hint=action_hint,
        action_human=action_human,
        action_label=action_label,
        entity_type=ctx.get("entity_type"),
        entity_id=ctx.get("entity_id"),
        entity_name=ctx.get("entity_name"),
        entity_code=ctx.get("entity_code"),
        last_interaction_at=last_interaction_at,
        client_status=client_status,
        session_id=session_id,
        tab_visible=tab_visible,
        is_idle=is_idle,
        is_typing=is_typing,
        has_unsaved_changes=has_unsaved_changes,
        modal_open=modal_open,
        lock_owner=lock_owner,
        preserve_entity_when_missing=False,
        log_event=True,
        load_previous_state=False,
    )
    return jsonify({"ok": True})


@admin_bp.route('/seguridad/locks', methods=['GET'])
@login_required
@admin_required
def seguridad_locks():
    rows = list_active_locks()
    list_ctx = dict(locks=rows, now=utc_now_naive())
    if _admin_async_wants_json():
        redirect_url = url_for("admin.seguridad_locks")
        html = render_template("admin/_seguridad_locks_region.html", **list_ctx)
        return jsonify(_admin_async_payload(
            success=True,
            message="Locks actualizados.",
            category="info",
            redirect_url=redirect_url,
            replace_html=html,
            update_target="#seguridadLocksAsyncRegion",
        )), 200
    return render_template("admin/seguridad_locks.html", **list_ctx)


@admin_bp.route('/seguridad/locks/ping', methods=['POST'])
@login_required
@staff_required
def seguridad_locks_ping():
    if not _live_access_allowed("locks_ping"):
        _audit_live_security_block(
            action_type="LIVE_ACCESS_DENIED",
            summary="Acceso denegado a locks ping",
            reason="locks_ping_role_forbidden",
            metadata={"path": request.path, "role": role_for_user(current_user), "capability": "locks_ping"},
        )
        abort(403)
    rl = _enforce_live_rate_limit("locks_ping")
    if rl:
        _audit_live_security_block(
            action_type="LIVE_RATE_LIMITED",
            summary="Rate limit bloqueó locks ping",
            reason="locks_ping_rate_limited",
            metadata={"path": request.path, "scope": rl.get("scope"), "retry_after_sec": rl.get("retry_after_sec")},
        )
        response = jsonify({
            "ok": False,
            "error": "rate_limited",
            "scope": rl.get("scope"),
            "retry_after_sec": int(rl.get("retry_after_sec") or 1),
        })
        response.status_code = 429
        response.headers["Retry-After"] = str(int(rl.get("retry_after_sec") or 1))
        return response
    payload = request.get_json(silent=True) or {}
    entity_type = (payload.get("entity_type") or "").strip().lower()
    entity_id = str(payload.get("entity_id") or "").strip()
    current_path = (payload.get("current_path") or request.path or "").strip()[:255]
    if not entity_type or not entity_id:
        lock_path = (current_path or request.referrer or "").strip()
        lock_path_l = lock_path.lower()
        m_sol = re.search(r"/clientes/\d+/solicitudes/(\d+)(?:/editar)?/?", lock_path_l)
        if not m_sol:
            m_sol = re.search(r"/solicitudes/(\d+)(?:/editar)?/?", lock_path_l)
        if m_sol and m_sol.group(1):
            entity_type = entity_type or "solicitud"
            entity_id = entity_id or str(m_sol.group(1))
        if (not entity_type or not entity_id) and lock_path:
            q = parse_qs(urlparse(lock_path).query)
            solicitud_id_qs = str(((q.get("solicitud_id") or [""])[0]) or "").strip()
            candidata_id_qs = str(((q.get("candidata_id") or q.get("fila") or [""])[0]) or "").strip()
            if solicitud_id_qs:
                entity_type = entity_type or "solicitud"
                entity_id = entity_id or solicitud_id_qs
            elif candidata_id_qs:
                entity_type = entity_type or "candidata"
                entity_id = entity_id or candidata_id_qs
    if not isinstance(current_user, StaffUser):
        return jsonify({"ok": False, "error": "Sesión inválida."}), 403
    if entity_type not in {"candidata", "solicitud"} or not entity_id:
        missing_fields: list[str] = []
        if not entity_type:
            missing_fields.append("entity_type")
        if not entity_id:
            missing_fields.append("entity_id")
        return jsonify({
            "ok": False,
            "error": "Entidad inválida.",
            "error_code": "invalid_entity_payload",
            "missing_fields": missing_fields,
            "received": {
                "entity_type": entity_type,
                "entity_id": entity_id,
            },
        }), 400
    data = lock_ping(user=current_user, entity_type=entity_type, entity_id=entity_id, current_path=current_path)
    if not data.get("ok"):
        if data.get("error") == "distributed_backplane_unavailable":
            if not bool(current_app.config.get("DISTRIBUTED_BACKPLANE_REQUIRED", False)):
                return jsonify({
                    "ok": True,
                    "state": "degraded",
                    "degraded": True,
                    "coordination": "local_only",
                    "lock": {
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "owner_user_id": int(getattr(current_user, "id", 0) or 0),
                        "owner_username": str(getattr(current_user, "username", "") or ""),
                        "owner_role": str(getattr(current_user, "role", "") or ""),
                        "current_path": current_path,
                    },
                    "message": "Backplane distribuido no disponible; lock en modo degradado local.",
                }), 200
            return jsonify(data), 503
        return jsonify(data), 400
    return jsonify(data)


@admin_bp.route('/seguridad/locks/takeover', methods=['POST'])
@login_required
@staff_required
def seguridad_locks_takeover():
    payload = request.get_json(silent=True) or {}
    entity_type = (payload.get("entity_type") or "").strip().lower()
    entity_id = str(payload.get("entity_id") or "").strip()
    reason = (payload.get("reason") or "").strip()
    if not isinstance(current_user, StaffUser):
        return jsonify({"ok": False, "error": "Sesión inválida."}), 403
    data = lock_takeover(user=current_user, entity_type=entity_type, entity_id=entity_id, reason=reason)
    if not data.get("ok"):
        if data.get("error") == "distributed_backplane_unavailable":
            if not bool(current_app.config.get("DISTRIBUTED_BACKPLANE_REQUIRED", False)):
                return jsonify({
                    "ok": True,
                    "state": "degraded",
                    "degraded": True,
                    "coordination": "local_only",
                    "message": "Takeover aplicado en modo degradado local (sin backplane distribuido).",
                }), 200
            return jsonify(data), 503
        return jsonify(data), 403
    return jsonify(data)


@admin_bp.route('/seguridad/sesiones', methods=['GET'])
@login_required
@admin_required
def seguridad_sesiones():
    sessions_rows = list_active_sessions()
    return render_template("admin/seguridad_sesiones.html", sessions_rows=sessions_rows, now=utc_now_naive())


def _render_seguridad_sesiones_region(*, sessions_rows) -> str:
    return render_template("admin/_seguridad_sesiones_region.html", sessions_rows=sessions_rows)


def _render_seguridad_alertas_region(*, alerts) -> str:
    return render_template("admin/_seguridad_alertas_region.html", alerts=alerts)


def _render_alertas_canales_region(*, cfg) -> str:
    return render_template("admin/_alertas_canales_region.html", cfg=cfg)


@admin_bp.route('/seguridad/sesiones/cerrar', methods=['POST'])
@login_required
@admin_required
def seguridad_sesiones_cerrar():
    raw_user_id = request.form.get("user_id") or (request.get_json(silent=True) or {}).get("user_id")
    reason = request.form.get("reason") or (request.get_json(silent=True) or {}).get("reason") or ""
    fallback = url_for("admin.seguridad_sesiones")
    try:
        user_id = int(raw_user_id)
    except Exception:
        return _security_admin_action_response(
            ok=False,
            message="Usuario inválido para cerrar sesión.",
            category="danger",
            fallback=fallback,
            http_status=200,
            error_code="invalid_input",
        )
    try:
        close_user_sessions(actor=current_user, user_id=user_id, reason=reason)
    except Exception:
        return _security_admin_action_response(
            ok=False,
            message="No se pudo cerrar la sesión. Intente nuevamente.",
            category="danger",
            fallback=fallback,
            http_status=500,
            error_code="server_error",
        )
    return _security_admin_action_response(
        ok=True,
        message="Sesiones cerradas correctamente.",
        category="success",
        fallback=fallback,
        replace_html=_render_seguridad_sesiones_region(sessions_rows=list_active_sessions()),
        update_target="#seguridadSesionesAsyncRegion",
    )


@admin_bp.route('/seguridad/alertas', methods=['GET'])
@login_required
@admin_required
def seguridad_alertas():
    alerts = get_alert_items(limit=200, scope="security", include_resolved=True)
    return render_template("admin/seguridad_alertas.html", alerts=alerts)


@admin_bp.route('/alertas/<int:alert_id>/resolver', methods=['POST'])
@login_required
@admin_required
def resolver_alerta(alert_id: int):
    fallback = url_for("admin.monitoreo_staff")
    dynamic_target = (request.form.get("_async_target") or request.args.get("_async_target") or "").strip()
    is_dashboard_target = dynamic_target == "#monitoreoAlertsAsyncRegion"
    update_target = dynamic_target if is_dashboard_target else "#seguridadAlertasAsyncRegion"

    def _target_region_html() -> str:
        if is_dashboard_target:
            return _render_monitoreo_alertas_region(
                alerts=_monitoreo_dashboard_alerts(),
                next_url=url_for("admin.monitoreo_staff"),
            )
        return _render_seguridad_alertas_region(
            alerts=get_alert_items(limit=200, scope="security", include_resolved=True)
        )

    def _dashboard_update_targets(html: str, *, include_shell_invalidate: bool) -> list[dict]:
        # Contrato async v2: refresca región principal y marca región shell para refresh por URL.
        targets = [
            {
                "target": "#monitoreoAlertsAsyncRegion",
                "replace_html": html,
            },
        ]
        if include_shell_invalidate:
            targets.append({
                "target": "#monitoreoDashboardShellAsyncRegion",
                "invalidate": True,
            })
        return targets

    try:
        resolve_alert(alert_id, actor=current_user if isinstance(current_user, StaffUser) else None)
    except Exception:
        region_html = _target_region_html()
        # En error evitamos invalidar shell para no disparar refresh extra/flicker innecesario.
        update_targets = _dashboard_update_targets(region_html, include_shell_invalidate=False) if is_dashboard_target else None
        return _security_admin_action_response(
            ok=False,
            message="No se pudo resolver la alerta. Intente nuevamente.",
            category="danger",
            fallback=fallback,
            http_status=500,
            error_code="server_error",
            replace_html=region_html,
            update_target=update_target,
            update_targets=update_targets,
        )
    region_html = _target_region_html()
    update_targets = _dashboard_update_targets(region_html, include_shell_invalidate=True) if is_dashboard_target else None
    return _security_admin_action_response(
        ok=True,
        message="Alerta marcada como resuelta.",
        category="success",
        fallback=fallback,
        replace_html=region_html,
        update_target=update_target,
        update_targets=update_targets,
    )


@admin_bp.route('/alertas/canales', methods=['GET', 'POST'])
@login_required
@admin_required
def alertas_canales():
    _owner_only()
    current_cfg = telegram_channel_config()
    if request.method == "POST":
        fallback = url_for("admin.alertas_canales")
        token_input = (request.form.get("telegram_bot_token") or "").strip()
        chat_id_input = (request.form.get("telegram_chat_id") or "").strip()
        token = token_input or str(current_cfg.get("token") or "").strip()
        chat_id = chat_id_input or str(current_cfg.get("chat_id") or "").strip()
        enabled = str(request.form.get("telegram_enabled") or "").strip().lower() in {"1", "true", "on", "yes"}
        if enabled and (not token or not chat_id):
            return _security_admin_action_response(
                ok=False,
                message="Para activar Telegram debes configurar token y chat_id.",
                category="warning",
                fallback=fallback,
                http_status=200,
                error_code="invalid_input",
                replace_html=_render_alertas_canales_region(cfg=current_cfg),
                update_target="#alertasCanalesAsyncRegion",
            )
        try:
            save_telegram_channel_config(
                token=token,
                chat_id=chat_id,
                enabled=enabled,
                actor_username=getattr(current_user, "username", None),
            )
        except Exception:
            return _security_admin_action_response(
                ok=False,
                message="No se pudo actualizar el canal de alertas.",
                category="danger",
                fallback=fallback,
                http_status=500,
                error_code="server_error",
            )
        return _security_admin_action_response(
            ok=True,
            message="Canal de Telegram actualizado.",
            category="success",
            fallback=fallback,
            replace_html=_render_alertas_canales_region(cfg=telegram_channel_config()),
            update_target="#alertasCanalesAsyncRegion",
        )

    return render_template("admin/alertas_canales.html", cfg=current_cfg)


@admin_bp.route('/alertas/canales/probar', methods=['POST'])
@login_required
@admin_required
def alertas_canales_probar():
    _owner_only()
    fallback = url_for("admin.alertas_canales")
    try:
        ok, detail = send_telegram_test_message(actor_username=getattr(current_user, "username", None))
    except Exception:
        return _security_admin_action_response(
            ok=False,
            message="No se pudo enviar el mensaje de prueba.",
            category="danger",
            fallback=fallback,
            http_status=500,
            error_code="server_error",
        )
    if ok:
        return _security_admin_action_response(
            ok=True,
            message="Mensaje de prueba enviado por Telegram.",
            category="success",
            fallback=fallback,
        )
    return _security_admin_action_response(
        ok=False,
        message=f"No se pudo enviar el mensaje de prueba: {detail}",
        category="danger",
        fallback=fallback,
        http_status=409,
        error_code="telegram_test_failed",
    )


@admin_bp.route('/errores', methods=['GET'])
@login_required
@admin_required
def errores_lista():
    q = (request.args.get("q") or "").strip().lower()
    status = (request.args.get("status") or "all").strip().lower()
    if status not in {"all", "pending", "resolved"}:
        status = "all"
    page = max(1, request.args.get("page", default=1, type=int))
    per_page = min(100, max(10, request.args.get("per_page", default=25, type=int)))

    rows = list(get_alert_items(limit=500, scope="error", include_resolved=True) or [])
    if status == "pending":
        rows = [r for r in rows if not bool(r.get("is_resolved"))]
    elif status == "resolved":
        rows = [r for r in rows if bool(r.get("is_resolved"))]
    if q:
        rows = [
            r for r in rows
            if q in str(r.get("summary") or "").lower()
            or q in str(r.get("route") or "").lower()
            or q in str(r.get("error_type") or "").lower()
        ]

    total = len(rows)
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, pages)
    start = (page - 1) * per_page
    end = start + per_page
    errors = rows[start:end]
    has_prev = page > 1
    has_next = page < pages

    list_ctx = dict(
        errors=errors,
        q=q,
        status=status,
        page=page,
        pages=pages,
        per_page=per_page,
        total=total,
        has_prev=has_prev,
        has_next=has_next,
    )
    if _admin_async_wants_json():
        redirect_url = url_for(
            "admin.errores_lista",
            q=(q or None),
            status=(status if status != "all" else None),
            page=page,
            per_page=per_page,
        )
        html = render_template("admin/_errores_lista_region.html", **list_ctx)
        return jsonify(_admin_async_payload(
            success=True,
            message="Listado actualizado.",
            category="info",
            redirect_url=redirect_url,
            replace_html=html,
            update_target="#erroresListaAsyncRegion",
            extra={
                "page": page,
                "total": total,
                "pages": pages,
            },
        )), 200

    return render_template("admin/errores_lista.html", **list_ctx)


@admin_bp.route('/errores/<int:error_id>', methods=['GET'])
@login_required
@admin_required
def errores_detalle(error_id: int):
    row = StaffAuditLog.query.filter(StaffAuditLog.id == int(error_id), StaffAuditLog.action_type == "ERROR_EVENT").first_or_404()
    actor = None
    if row.actor_user_id:
        actor = StaffUser.query.filter_by(id=int(row.actor_user_id)).first()
    fallback = url_for("admin.errores_lista")
    next_url = (request.args.get("next") or request.form.get("next") or request.referrer or "").strip()
    safe_next = next_url if _is_safe_redirect_url(next_url) else fallback
    return render_template("admin/errores_detalle.html", error_row=row, actor=actor, next_url=safe_next)


@admin_bp.route('/health', methods=['GET'])
@login_required
@admin_required
def admin_health():
    payload = health_payload()
    if (request.args.get("format") or "").strip().lower() == "json":
        return jsonify(payload)
    return render_template("admin/health.html", health=payload)


@admin_bp.route('/health/operational', methods=['GET'])
@login_required
@admin_required
def admin_health_operational():
    payload = operational_semaphore_payload(window_minutes=15)
    if (request.args.get("format") or "").strip().lower() == "json":
        return jsonify(payload)
    return render_template("admin/health_operational.html", payload=payload)


@admin_bp.route('/health/operational/trends', methods=['GET'])
@login_required
@admin_required
def admin_health_operational_trends():
    payload = operational_trends_payload()
    return jsonify(payload)


@admin_bp.route('/live/observability', methods=['POST'])
@csrf.exempt
@login_required
@staff_required
def live_observability_ingest():
    data = request.get_json(silent=True) or {}
    uid = None
    try:
        if current_user and getattr(current_user, "is_authenticated", False):
            uid = int(getattr(current_user, "id", 0) or 0)
    except Exception:
        uid = None
    out = ingest_live_observability_event(data, user_id=uid if uid and uid > 0 else None)
    return jsonify(out), 200


@admin_bp.route('/metricas', methods=['GET'])
@login_required
@admin_required
def metricas_dashboard():
    period = (request.args.get("period") or "7d").strip().lower()
    payload = metrics_dashboard(period)
    list_ctx = dict(period=period, payload=payload)
    if _admin_async_wants_json():
        redirect_url = url_for("admin.metricas_dashboard", period=(period or None))
        html = render_template("admin/_metricas_dashboard_region.html", **list_ctx)
        return jsonify(_admin_async_payload(
            success=True,
            message="Métricas actualizadas.",
            category="info",
            redirect_url=redirect_url,
            replace_html=html,
            update_target="#metricasDashboardAsyncRegion",
        )), 200
    return render_template("admin/metricas_dashboard.html", **list_ctx)


@admin_bp.route('/metricas/secretarias', methods=['GET'])
@login_required
@admin_required
def metricas_secretarias_view():
    period = (request.args.get("period") or "7d").strip().lower()
    payload = metrics_secretarias(period)
    list_ctx = dict(period=period, payload=payload)
    if _admin_async_wants_json():
        redirect_url = url_for("admin.metricas_secretarias_view", period=(period or None))
        html = render_template("admin/_metricas_secretarias_region.html", **list_ctx)
        return jsonify(_admin_async_payload(
            success=True,
            message="Métricas actualizadas.",
            category="info",
            redirect_url=redirect_url,
            replace_html=html,
            update_target="#metricasSecretariasAsyncRegion",
        )), 200
    return render_template("admin/metricas_secretarias.html", **list_ctx)


@admin_bp.route('/metricas/solicitudes', methods=['GET'])
@login_required
@admin_required
def metricas_solicitudes_view():
    period = (request.args.get("period") or "7d").strip().lower()
    payload = metrics_solicitudes(period)
    list_ctx = dict(period=period, payload=payload)
    if _admin_async_wants_json():
        redirect_url = url_for("admin.metricas_solicitudes_view", period=(period or None))
        html = render_template("admin/_metricas_solicitudes_region.html", **list_ctx)
        return jsonify(_admin_async_payload(
            success=True,
            message="Métricas actualizadas.",
            category="info",
            redirect_url=redirect_url,
            replace_html=html,
            update_target="#metricasSolicitudesAsyncRegion",
        )), 200
    return render_template("admin/metricas_solicitudes.html", **list_ctx)


@admin_bp.route('/solicitudes/<int:solicitud_id>/sugerencias', methods=['GET'])
@login_required
@staff_required
def sugerencias_solicitud(solicitud_id: int):
    solicitud = (
        Solicitud.query
        .options(joinedload(Solicitud.cliente))
        .filter_by(id=solicitud_id)
        .first_or_404()
    )
    items = intelligent_suggestions_for_solicitud(solicitud, top_k=10)
    return render_template("admin/sugerencias_solicitud.html", solicitud=solicitud, items=items)


@admin_bp.route('/solicitudes/<int:solicitud_id>/sugerencias/feedback', methods=['POST'])
@login_required
@staff_required
def sugerencias_feedback(solicitud_id: int):
    solicitud = Solicitud.query.filter_by(id=solicitud_id).first_or_404()
    try:
        candidata_id = int(request.form.get("candidata_id") or "0")
    except Exception:
        candidata_id = 0
    feedback = (request.form.get("feedback") or "").strip().lower()
    reason_key = (request.form.get("reason_key") or "").strip().lower()[:40]
    reason_text = (request.form.get("reason_text") or "").strip()[:200]
    good = feedback in {"good", "buena", "si", "sí", "1"}
    if candidata_id <= 0:
        flash("Selecciona una candidata válida para guardar feedback.", "warning")
        return redirect(url_for("admin.sugerencias_solicitud", solicitud_id=solicitud.id))
    register_decision_feedback(
        actor=current_user,
        solicitud_id=solicitud.id,
        candidata_id=candidata_id,
        good=good,
        reason_key=reason_key or "experiencia",
        reason_text=reason_text,
    )
    flash("Feedback guardado. El motor ajustó sus pesos de reglas.", "success")
    return redirect(url_for("admin.sugerencias_solicitud", solicitud_id=solicitud.id))


@admin_bp.route('/matching/inteligente', methods=['GET'])
@login_required
@staff_required
def matching_inteligente():
    solicitudes = (
        Solicitud.query
        .options(joinedload(Solicitud.cliente))
        .filter(Solicitud.estado.in_(("activa", "reemplazo", "proceso")))
        .order_by(Solicitud.fecha_solicitud.desc(), Solicitud.id.desc())
        .limit(80)
        .all()
    )
    return render_template("admin/matching_inteligente.html", solicitudes=solicitudes)

# =============================================================================
#                 GUARD GLOBAL ADMIN (aislamiento real)
# =============================================================================

def _is_admin_identity_LEGACY() -> bool:
    return False


@admin_bp.before_request
def _admin_guard_before_request_LEGACY():
    """Se ejecuta antes de cualquier endpoint del blueprint admin.
    Si la sesión no es de admin real -> logout y pa' fuera.
    """
    return None
    try:
        # Permitir login sin estar autenticado
        if request.endpoint in ("admin.login", "admin.static"):
            return None

        # Si no está logueado, pa' login
        if not current_user or not getattr(current_user, "is_authenticated", False):
            return redirect(url_for("admin.login"))

        # ✅ Aislamiento real: si NO es usuario admin/staff/secretaria => sacar
        if not _is_admin_identity_LEGACY():
            try:
                logout_user()
            except Exception:
                pass
            try:
                session.clear()
            except Exception:
                pass
            return redirect(url_for("admin.login"))

        return None
    except Exception:
        # fallback ultra seguro
        try:
            logout_user()
        except Exception:
            pass
        try:
            session.clear()
        except Exception:
            pass
        return redirect(url_for("admin.login"))

# =============================================================================
#                 RATE-LIMIT ADMIN (acciones sensibles)
# =============================================================================

_ADMIN_ACTION_KEY_PREFIX_LEGACY = "admin_act"

def _admin_action_max_LEGACY() -> int:
    # acciones permitidas por ventana
    try:
        return int((os.getenv("ADMIN_ACTION_MAX") or "40").strip())
    except Exception:
        return 40

def _admin_action_window_sec_LEGACY() -> int:
    # ventana en segundos
    try:
        return int((os.getenv("ADMIN_ACTION_WINDOW_SEC") or "60").strip())
    except Exception:
        return 60

def _admin_action_lock_min_LEGACY() -> int:
    # lock en minutos si se pasa
    try:
        return int((os.getenv("ADMIN_ACTION_LOCK_MIN") or "5").strip())
    except Exception:
        return 5

def _admin_action_keys_LEGACY(usuario_norm: str, bucket: str = "default"):
    ip = _client_ip()
    u = (usuario_norm or "").strip().lower()[:64]
    b = (bucket or "default").strip().lower()[:32]
    base = f"{_ADMIN_ACTION_KEY_PREFIX_LEGACY}:{ip}:{u}:{b}"
    return {
        "count": f"{base}:count",
        "lock":  f"{base}:lock",
    }

def _sess_action_key_LEGACY(usuario_norm: str, bucket: str = "default") -> str:
    ip = _client_ip()
    u = (usuario_norm or "").strip().lower()[:64]
    b = (bucket or "default").strip().lower()[:32]
    return f"admin_act:{ip}:{u}:{b}"

def _session_action_get_LEGACY(usuario_norm: str, bucket: str):
    return session.get(_sess_action_key_LEGACY(usuario_norm, bucket)) or {}

def _session_action_is_locked_LEGACY(usuario_norm: str, bucket: str) -> bool:
    data = _session_action_get_LEGACY(usuario_norm, bucket)
    until = data.get("locked_until")
    if not until:
        return False
    try:
        return utc_timestamp() < float(until)
    except Exception:
        return False

def _session_action_register_LEGACY(usuario_norm: str, bucket: str, max_actions: int, window_sec: int) -> int:
    key = _sess_action_key_LEGACY(usuario_norm, bucket)
    now = utc_timestamp()
    data = session.get(key) or {}

    start = float(data.get("start_ts") or now)
    count = int(data.get("count") or 0)

    # si se venció la ventana, resetea
    if (now - start) > window_sec:
        start = now
        count = 0

    count += 1
    data["start_ts"] = start
    data["count"] = count

    if count >= max_actions:
        data["locked_until"] = now + (_admin_action_lock_min_LEGACY() * 60)

    session[key] = data
    return count

def _admin_action_is_locked_LEGACY(usuario_norm: str, bucket: str) -> bool:
    if _cache_ok():
        keys = _admin_action_keys_LEGACY(usuario_norm, bucket=bucket)
        try:
            return bool(cache.get(keys["lock"]))
        except Exception:
            return _session_action_is_locked_LEGACY(usuario_norm, bucket)
    return _session_action_is_locked_LEGACY(usuario_norm, bucket)

def _admin_action_register_LEGACY(usuario_norm: str, bucket: str, max_actions: int, window_sec: int) -> int:
    if _cache_ok():
        keys = _admin_action_keys_LEGACY(usuario_norm, bucket=bucket)
        try:
            # ventana: count expira con la ventana
            n = int(cache.get(keys["count"]) or 0) + 1
            cache.set(keys["count"], n, timeout=window_sec)

            if n >= max_actions:
                cache.set(keys["lock"], True, timeout=_admin_action_lock_min_LEGACY() * 60)
            return n
        except Exception:
            return _session_action_register_LEGACY(usuario_norm, bucket, max_actions, window_sec)

    return _session_action_register_LEGACY(usuario_norm, bucket, max_actions, window_sec)

def admin_action_limit_LEGACY(bucket: str = "default", max_actions: int | None = None, window_sec: int | None = None):
    """Decorador para limitar acciones admin por IP+usuario.
    bucket: agrupa acciones (ej: 'delete', 'edit', 'pay', 'reemplazo')
    """
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                uname = ""
                try:
                    uname = current_user.get_id()
                except Exception:
                    uname = getattr(current_user, "id", "") or ""
                usuario_norm = str(uname).strip().lower()

                lim = int(max_actions) if max_actions is not None else _admin_action_max_LEGACY()
                win = int(window_sec) if window_sec is not None else _admin_action_window_sec_LEGACY()

                if _admin_action_is_locked_LEGACY(usuario_norm, bucket=bucket):
                    mins = _admin_action_lock_min_LEGACY()
                    flash(f"Demasiadas acciones seguidas. Intenta de nuevo en {mins} minutos.", "warning")
                    return redirect(url_for("admin.listar_clientes"))

                _admin_action_register_LEGACY(usuario_norm, bucket=bucket, max_actions=lim, window_sec=win)

            except Exception:
                # si falla el rate-limit, NO rompemos la app
                pass

            return fn(*args, **kwargs)
        return wrapper
    return deco

# =============================================================================
#                            CLIENTES (CRUD BÁSICO)
# =============================================================================
@admin_bp.route('/clientes')
@login_required
@staff_required
def listar_clientes():
    """
    Lista de clientes con búsqueda básica.
    - Evita escaneos completos si la query de texto es de 1 carácter (excepto ID numérica).
    """
    q = (request.args.get('q') or '').strip()
    try:
        page = int(request.args.get('page', 1) or 1)
    except Exception:
        page = 1
    page = max(1, page)

    try:
        per_page = int(request.args.get('per_page', 25) or 25)
    except Exception:
        per_page = 25
    per_page = max(10, min(per_page, 100))

    query = Cliente.query

    if q:
        filtros = []
        q_lower = q.lower()
        q_digits = _only_digits(q)

        # 1) Si es un ID exacto (entero), permite búsqueda directa por ID
        if q.isdigit():
            try:
                filtros.append(Cliente.id == int(q))
            except Exception:
                pass

        # 2) Búsqueda por CÓDIGO (exacto + parcial)
        #    - Exacto: rápido y preciso
        #    - Parcial: útil para fragmentos (ej: "ADC" o "ADC-" o "-A")
        try:
            filtros.append(Cliente.codigo == q)
        except Exception:
            pass
        if len(q) >= 2:
            filtros.append(Cliente.codigo.ilike(f"%{q}%"))

        # 3) Búsqueda por EMAIL (case-insensitive) — soporta "gmail" o el email completo
        #    - Si el query incluye '@' o '.' o la palabra 'gmail', asumimos que es email/fragmento
        looks_like_email = ('@' in q) or ('.' in q) or ('gmail' in q_lower)
        if looks_like_email:
            try:
                filtros.append(func.lower(Cliente.email).like(f"%{q_lower}%"))
            except Exception:
                # fallback si el motor no soporta func.lower
                filtros.append(Cliente.email.ilike(f"%{q}%"))
        else:
            # Si no parece email, solo lo incluimos cuando q tenga mínimo 2 chars (evita full scan por 1 char)
            if len(q) >= 2:
                filtros.append(Cliente.email.ilike(f"%{q}%"))

        # 4) Teléfono:
        #    - búsqueda textual (como antes)
        #    - búsqueda por solo dígitos para soportar históricos con formato "809-555-0001"
        looks_like_phone = bool(
            q_digits
            and len(q_digits) >= 6
            and re.fullmatch(r"[\d\s\-\+\(\)\.\/]+", q or "")
        )
        if looks_like_phone:
            try:
                phone_digits_expr = func.replace(
                    func.replace(
                        func.replace(
                            func.replace(
                                func.replace(
                                    func.replace(
                                        func.replace(func.coalesce(Cliente.telefono, ''), ' ', ''),
                                        '-',
                                        '',
                                    ),
                                    '(',
                                    '',
                                ),
                                ')',
                                '',
                            ),
                            '+',
                            '',
                        ),
                        '.',
                        '',
                    ),
                    '/',
                    '',
                )
                filtros.append(phone_digits_expr.like(f"%{q_digits}%"))
            except Exception:
                pass

        # 5) Campos extra (solo cuando hay suficiente texto para evitar escaneo completo)
        if len(q) >= 2:
            filtros.extend([
                Cliente.nombre_completo.ilike(f"%{q}%"),
                Cliente.telefono.ilike(f"%{q}%"),
            ])

        if filtros:
            query = query.filter(or_(*filtros))

    list_attrs = []
    for attr in (
        "id",
        "codigo",
        "nombre_completo",
        "telefono",
        "total_solicitudes",
        "fecha_registro",
    ):
        if hasattr(Cliente, attr):
            list_attrs.append(getattr(Cliente, attr))

    if list_attrs and hasattr(query, "options"):
        try:
            query = query.options(load_only(*list_attrs))
        except Exception:
            # Compatibilidad defensiva con stubs de tests u ORMs parciales.
            pass

    ordered_query = query.order_by(Cliente.fecha_registro.desc())
    if all(hasattr(ordered_query, attr) for attr in ("count", "offset", "limit", "all")):
        total = ordered_query.count()
        clientes = (
            ordered_query
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )
    else:
        # Fallback para stubs legacy de tests que solo exponen .all()
        all_rows = ordered_query.all()
        total = len(all_rows)
        clientes = all_rows[(page - 1) * per_page: page * per_page]
    last_page = ((total - 1) // per_page) + 1 if total > 0 else 1

    if _admin_async_wants_json():
        html = render_template(
            'admin/_clientes_list_results.html',
            clientes=clientes,
            q=q,
            page=page,
            per_page=per_page,
            total=total,
            last_page=last_page,
            server_paginated=True,
        )
        return jsonify(_admin_async_payload(
            success=True,
            message='',
            category='info',
            replace_html=html,
            update_target='#clientesAsyncRegion',
            extra={
                "query": q,
                "page": page,
                "per_page": per_page,
                "total": total,
                "last_page": last_page,
            },
        )), 200

    return render_template(
        'admin/clientes_list.html',
        clientes=clientes,
        q=q,
        page=page,
        per_page=per_page,
        total=total,
        last_page=last_page,
        server_paginated=True,
    )


# ─────────────────────────────────────────────────────────────
# Helpers de fecha (UTC) para listados/filtrado
# ─────────────────────────────────────────────────────────────

def _today_utc_bounds():
    """Devuelve (start_utc, end_utc) del día actual en UTC como datetimes NAIVE.

    Se usa para filtros por rango diario sin depender de timezone-aware datetimes,
    manteniendo consistencia con columnas típicamente naive en Postgres.
    """
    now_utc = utc_now_naive()
    start = datetime(now_utc.year, now_utc.month, now_utc.day)
    end = start + timedelta(days=1)
    return start, end


@admin_bp.route('/ping')
@login_required
def admin_ping():
    """Ping simple para saber si la sesión sigue viva (útil para UI)."""
    resp = jsonify({"ok": True, "utc": iso_utc_z()})
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    return resp

# =============================================================================
#                       HELPERS DE LIMPIEZA / NORMALIZACIÓN
# =============================================================================

def _only_digits(text: str) -> str:
    """Retorna solo dígitos de un texto (para teléfonos, etc.)."""
    return re.sub(r"\D+", "", text or "")


# Nuevo helper: normalizar strings numéricos (para sueldo, etc.)
def _norm_numeric_str(value) -> str | None:
    """Normaliza strings numéricos para campos como sueldo.

    - Acepta: "30000", "RD$ 30,000", "30.000", "30 000"
    - Retorna SOLO dígitos (sin decimales) o None si queda vacío.

    Importante: esto NO toca lo ya guardado en BD; solo normaliza lo que entra por formularios.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    s = _only_digits(s)
    return s or None

def _normalize_email(value: str) -> str:
    """Email normalizado (lower + strip)."""
    return _shared_norm_email(value)

def _normalize_phone(value: str) -> str:
    """
    Normaliza teléfono manteniendo dígitos. Si quieres guardar con formato,
    hazlo en la vista; persiste solo dígitos en la BD si tu modelo lo permite.
    """
    return _shared_norm_phone_rd(value)

def _strip_if_str(x):
    return x.strip() if isinstance(x, str) else x

def _norm_cliente_form(form: AdminClienteForm) -> None:
    """
    Normaliza/limpia entradas de texto del formulario de cliente.
    """
    if hasattr(form, 'codigo') and form.codigo.data:
        form.codigo.data = _strip_if_str(form.codigo.data)

    if hasattr(form, 'nombre_completo') and form.nombre_completo.data:
        form.nombre_completo.data = _strip_if_str(form.nombre_completo.data)

    if hasattr(form, 'email') and form.email.data:
        form.email.data = _normalize_email(form.email.data)

    if hasattr(form, 'telefono') and form.telefono.data:
        # guarda limpio; si prefieres mantener guiones para UI, renderízalos en plantilla
        form.telefono.data = _normalize_phone(form.telefono.data)

    if hasattr(form, 'ciudad') and form.ciudad.data:
        form.ciudad.data = _strip_if_str(form.ciudad.data)

    if hasattr(form, 'sector') and form.sector.data:
        form.sector.data = _strip_if_str(form.sector.data)

    if hasattr(form, 'notas_admin') and form.notas_admin.data:
        form.notas_admin.data = _strip_if_str(form.notas_admin.data)


def parse_integrity_error(err: IntegrityError) -> str:
    """
    Intenta detectar qué constraint única falló.
    Retorna 'codigo', 'email', 'username' o '' si no se pudo identificar.
    Funciona para SQLite, MySQL y PostgreSQL en la mayoría de casos.
    """
    msg = ""
    try:
        msg = str(getattr(err, "orig", err))
    except Exception:
        msg = str(err)

    m = msg.lower()

    # PostgreSQL: nombre del constraint si está disponible
    try:
        cstr = getattr(getattr(err, "orig", None), "diag", None)
        if cstr and getattr(cstr, "constraint_name", None):
            cname = cstr.constraint_name.lower()
            if "codigo" in cname:
                return "codigo"
            if "email" in cname or "correo" in cname:
                return "email"
            if "username" in cname or "usuario" in cname:
                return "username"
    except Exception:
        pass

    # Heurísticas por mensaje (MySQL/SQLite)
    if "codigo" in m:
        return "codigo"
    if "email" in m or "correo" in m:
        return "email"
    if "username" in m or "usuario" in m:
        return "username"

    if "for key" in m and "email" in m:
        return "email"
    if "for key" in m and "codigo" in m:
        return "codigo"
    if "for key" in m and ("username" in m or "usuario" in m):
        return "username"

    return ""


# =============================================================================
#                 HELPERS CONSISTENTES PARA EDAD Y LISTAS (ADMIN)
#            (VERSIÓN CANÓNICA — ELIMINAR CUALQUIER DUPLICADO LUEGO)
# =============================================================================
def _as_list(value):
    """Devuelve siempre una lista (acepta None, str o list/tuple/set)."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(',') if p.strip()]
        return parts if parts else ([value.strip()] if value.strip() else [])
    return [value]

def _clean_list(seq):
    """Lista sin vacíos/guiones, preservando orden y quitando duplicados."""
    bad = {"-", "–", "—"}
    out, seen = [], set()
    for v in (seq or []):
        s = str(v).strip()
        if not s or s in bad:
            continue
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out

def _choices_maps(choices):
    """Mapeos code<->label a partir de choices [(code, label), ...]."""
    code_to_label, label_to_code = {}, {}
    for code, label in (choices or []):
        c = str(code).strip()
        l = str(label).strip()
        if not c or not l:
            continue
        code_to_label[c] = l
        label_to_code[l] = c
    return code_to_label, label_to_code

def _map_edad_choices(codes_selected, edad_choices, otro_text):
    """
    Recibe lista de CÓDIGOS marcados en el form, choices y el texto de 'otro'.
    Devuelve lista final de LABELS legibles (lo que se guarda en BD).
    """
    codes_selected = _clean_list([str(x) for x in (codes_selected or [])])
    code_to_label, _ = _choices_maps(edad_choices)

    result = []
    for code in codes_selected:
        if code == "otro":
            continue
        label = code_to_label.get(code)
        if label:
            result.append(label)

    if "otro" in codes_selected:
        extra = (otro_text or "").strip()
        if extra:
            result.extend([x.strip() for x in extra.split(',') if x.strip()])

    return _clean_list(result)

def _split_edad_for_form(stored_list, edad_choices):
    """
    Convierte lo guardado en BD (LABELS legibles) a (CÓDIGOS seleccionados, texto_otro)
    para precargar el formulario.
    """
    stored_list = _clean_list(stored_list)
    code_to_label, label_to_code = _choices_maps(edad_choices)

    selected_codes, otros = [], []
    for label in stored_list:
        code = label_to_code.get(label)
        if code:
            selected_codes.append(code)
        else:
            otros.append(label)

    otro_text = ", ".join(otros) if otros else ""
    if otros:
        selected_codes = _clean_list(selected_codes + ["otro"])
    return selected_codes, otro_text


# =============================================================================
#                      CONSTANTES / CHOICES PARA FORMULARIOS
# =============================================================================
AREAS_COMUNES_CHOICES = [
    ('sala', 'Sala'), ('comedor', 'Comedor'),
    ('cocina', 'Cocina'), ('salon_juegos', 'Salón de juegos'),
    ('terraza', 'Terraza'), ('jardin', 'Jardín'),
    ('estudio', 'Estudio'), ('patio', 'Patio'),
    ('piscina', 'Piscina'), ('marquesina', 'Marquesina'),
    ('todas_anteriores', 'Todas las anteriores'),
    ('otro', 'Otro'),
]


# =============================================================================
#                              HELPERS NUEVOS (HOGAR)
# =============================================================================
def _norm_area(text: str) -> str:
    """Reemplaza guiones bajos por espacios y colapsa espacios múltiples."""
    if not text:
        return ""
    s = str(text)
    s = s.replace("_", " ")
    s = re.sub(r"\s+", " ", s).strip()
    if s.lower() in {"otro", "otro...", "otro…"}:
        return ""
    return s

def _fmt_banos(value) -> str:
    """Devuelve baños sin .0 si es entero; si no, muestra el decimal tal cual."""
    if value is None or value == "":
        return ""
    try:
        f = float(value)
        return str(int(f)) if f.is_integer() else str(f)
    except Exception:
        return str(value)


def _normalize_banos_value(raw_value):
    """Normaliza baños desde request/form sin forzar formato decimal."""
    if raw_value is None:
        return None
    txt = str(raw_value).strip()
    if not txt:
        return None
    txt = txt.replace(",", ".")
    try:
        dec = Decimal(txt)
    except (InvalidOperation, ValueError, TypeError):
        return None
    if dec < 0:
        return None
    return float(dec)


def _apply_banos_from_request(solicitud_obj, form_obj):
    if not hasattr(solicitud_obj, "banos"):
        return
    raw_post = None
    try:
        raw_post = request.form.get("banos")
    except Exception:
        raw_post = None
    parsed = _normalize_banos_value(raw_post)
    if parsed is None and hasattr(form_obj, "banos"):
        parsed = _normalize_banos_value(getattr(getattr(form_obj, "banos", None), "data", None))
    solicitud_obj.banos = parsed

def _map_funciones(vals, extra_text):
    """
    Combina funciones seleccionadas con valores personalizados de 'otro',
    eliminando duplicados y vacíos.
    """
    vals = _clean_list(vals)
    if 'otro' in vals:
        vals = [v for v in vals if v != 'otro']
        extra = (extra_text or '').strip()
        if extra:
            vals.extend([x.strip() for x in extra.split(',') if x.strip()])
    return _clean_list(vals)

def _map_tipo_lugar(value, extra):
    """
    Si el valor es 'otro', usa el texto extra; en otro caso retorna el valor tal cual.
    """
    value = (value or '').strip()
    if value == 'otro':
        return (extra or '').strip() or value
    return value


def _has_limpieza_funcion(funciones_selected):
    vals = _clean_list(funciones_selected)
    for raw in vals:
        if str(raw or '').strip().lower() == 'limpieza':
            return True
    return False


def _strip_pisos_marker_from_note(note_text):
    txt = str(note_text or '')
    lines = txt.split('\n')
    keep = []
    for raw in lines:
        line = str(raw or '')
        if line.strip().startswith('Pisos reportados:'):
            continue
        keep.append(line)
    return '\n'.join(keep).strip()


def _clear_house_structure_if_not_limpieza(solicitud_obj, funciones_selected):
    if _has_limpieza_funcion(funciones_selected):
        return
    if hasattr(solicitud_obj, 'tipo_lugar'):
        solicitud_obj.tipo_lugar = None
    if hasattr(solicitud_obj, 'habitaciones'):
        solicitud_obj.habitaciones = None
    if hasattr(solicitud_obj, 'banos'):
        solicitud_obj.banos = None
    if hasattr(solicitud_obj, 'dos_pisos'):
        solicitud_obj.dos_pisos = False
    if hasattr(solicitud_obj, 'areas_comunes'):
        solicitud_obj.areas_comunes = []
    if hasattr(solicitud_obj, 'area_otro'):
        solicitud_obj.area_otro = None
    if hasattr(solicitud_obj, 'nota_cliente'):
        solicitud_obj.nota_cliente = _strip_pisos_marker_from_note(getattr(solicitud_obj, 'nota_cliente', ''))


def _normalize_modalidad_on_solicitud(solicitud_obj) -> None:
    try:
        if hasattr(solicitud_obj, "modalidad_trabajo"):
            txt = canonicalize_modalidad_trabajo(getattr(solicitud_obj, "modalidad_trabajo", ""))
            solicitud_obj.modalidad_trabajo = txt or None
    except Exception:
        return


def _resolve_modalidad_ui_context_from_request(form_obj, *, prefer_post: bool = False) -> tuple[str, str, str]:
    """Compone estado guiado de modalidad para re-render estable en GET/POST."""
    fallback_group = ""
    fallback_specific = ""
    fallback_other = ""
    try:
        modalidad_raw = form_obj.modalidad_trabajo.data if hasattr(form_obj, "modalidad_trabajo") else ""
        modalidad_ui = split_modalidad_for_ui(modalidad_raw)
        fallback_group = (modalidad_ui.get("group") or "").strip()
        fallback_specific = (modalidad_ui.get("specific") or "").strip()
        fallback_other = (modalidad_ui.get("other") or "").strip()
    except Exception:
        pass

    if not prefer_post:
        return fallback_group, fallback_specific, fallback_other

    post_group = (request.form.get("modalidad_grupo") or "").strip()
    post_specific = (request.form.get("modalidad_especifica") or "").strip()
    post_other = (request.form.get("modalidad_otro_text") or "").strip()
    return (
        post_group or fallback_group,
        post_specific or fallback_specific,
        post_other or fallback_other,
    )


# ─────────────────────────────────────────────────────────────
# Helpers internos específicos de Solicitud
# ─────────────────────────────────────────────────────────────
def _allowed_codes_from_choices(choices):
    """Devuelve el set de códigos válidos a partir de choices [(code,label), ...]."""
    try:
        return {str(v).strip() for v, _ in (choices or []) if str(v).strip()}
    except Exception:
        return set()

def _normalize_areas_comunes_selected(selected_vals, choices):
    """Normaliza áreas comunes y expande 'todas_anteriores' a todas las opciones reales."""
    vals = _clean_list(selected_vals)
    allowed = _allowed_codes_from_choices(choices)
    vals = [v for v in vals if v in allowed]

    if 'todas_anteriores' in vals:
        all_codes = [
            str(code).strip()
            for code, _ in (choices or [])
            if str(code).strip() and str(code).strip() not in {'todas_anteriores', 'otro'}
        ]
        vals = [v for v in vals if v != 'todas_anteriores']
        vals = _clean_list(vals + all_codes)

    return [v for v in vals if v not in {'todas_anteriores', 'otro'}]

def _next_codigo_solicitud(cliente: Cliente) -> str:
    """
    Genera un código único del tipo:
      - primera:  <CODCLI>
      - siguientes: <CODCLI> - <LETRA>  (B, C, ...)
    Usa un loop defensivo para evitar colisiones si hubo borrados o concurrencia.
    """
    prefix = (cliente.codigo or str(cliente.id)).strip()
    base_count = Solicitud.query.filter_by(cliente_id=cliente.id).count()
    intento = 0
    while True:
        code = compose_codigo_solicitud(prefix, base_count + intento)
        exists = Solicitud.query.filter(Solicitud.codigo_solicitud == code).first()
        if not exists:
            return code
        intento += 1

# =============================================================================
#                         CLIENTES – CREAR / EDITAR / ELIMINAR / DETALLE
# =============================================================================

@admin_bp.route('/clientes/nuevo', methods=['GET', 'POST'])
@login_required
@staff_required
@admin_action_limit(bucket="create_cliente", max_actions=25, window_sec=60)
def nuevo_cliente():
    """🟢 Crear un nuevo cliente desde el panel de administración (sin credenciales de login)."""
    form = AdminClienteForm()

    if form.validate_on_submit():
        _norm_cliente_form(form)

        # --- Validación de código único (case-sensitive) ---
        try:
            if Cliente.query.filter(Cliente.codigo == form.codigo.data).first():
                form.codigo.errors.append("Este código ya está en uso.")
                flash("El código ya está en uso.", "danger")
                return render_template('admin/cliente_form.html', cliente_form=form, nuevo=True)
        except Exception:
            flash("No se pudo validar el código del cliente.", "danger")
            return render_template('admin/cliente_form.html', cliente_form=form, nuevo=True)

        # --- Validación de email único (case-insensitive) ---
        email_norm = (form.email.data or "").lower().strip()
        phone_norm = _shared_norm_phone_rd(form.telefono.data or "")
        try:
            if Cliente.query.filter(Cliente.email_norm == email_norm).first():
                form.email.errors.append("Este email ya está registrado.")
                flash("El email ya está registrado.", "danger")
                return render_template('admin/cliente_form.html', cliente_form=form, nuevo=True)
            if phone_norm and Cliente.query.filter(Cliente.telefono_norm == phone_norm).first():
                form.telefono.errors.append("Este teléfono ya está registrado.")
                flash("El teléfono ya está registrado.", "danger")
                return render_template('admin/cliente_form.html', cliente_form=form, nuevo=True)
        except Exception:
            flash("No se pudo validar el email del cliente.", "danger")
            return render_template('admin/cliente_form.html', cliente_form=form, nuevo=True)

        # --- Validación de USERNAME único (opcional, si existe en el modelo) ---
        username_norm = None
        if hasattr(Cliente, 'username'):
            # Preferimos el campo del form si existe; si no, intentamos leerlo del POST directo
            raw_username = None
            if hasattr(form, 'username'):
                raw_username = form.username.data
            if not raw_username:
                raw_username = request.form.get('username')

            username_norm = (raw_username or '').strip().lower()
            if not username_norm:
                # Si no envían username, usamos el email como username por defecto
                username_norm = email_norm

            try:
                if Cliente.query.filter(func.lower(Cliente.username) == username_norm).first():
                    if hasattr(form, 'username'):
                        form.username.errors.append("Este usuario ya está registrado.")
                    flash("Este usuario ya está registrado.", "danger")
                    return render_template('admin/cliente_form.html', cliente_form=form, nuevo=True)
            except Exception:
                flash("No se pudo validar el usuario del cliente.", "danger")
                return render_template('admin/cliente_form.html', cliente_form=form, nuevo=True)

        # --- Creación del cliente (con username/password si existen) ---
        try:
            ahora = utc_now_naive()
            c = Cliente()
            form.populate_obj(c)

            # Normalizamos email y fechas clave
            c.email = email_norm
            if hasattr(c, "email_norm"):
                c.email_norm = _shared_nullable_norm_email(email_norm)
            if hasattr(c, "telefono_norm"):
                c.telefono_norm = _shared_nullable_norm_phone_rd(getattr(c, "telefono", ""))

            # Username (si existe en el modelo)
            if hasattr(c, 'username'):
                # Si ya calculamos username_norm arriba úsalo, si no, usa el email
                c.username = (username_norm or email_norm)

            # Password (si existe en el modelo)
            if hasattr(c, 'password_hash'):
                raw_pw = None
                if hasattr(form, 'password'):
                    raw_pw = form.password.data
                if not raw_pw:
                    raw_pw = request.form.get('password')

                raw_pw = (raw_pw or '').strip()
                # Si mandan contraseña, la seteamos. Si no, dejamos el server_default (DISABLED_RESET_REQUIRED)
                if raw_pw:
                    if len(raw_pw) < 8:
                        if hasattr(form, 'password'):
                            form.password.errors.append("La contraseña debe tener al menos 8 caracteres.")
                        flash("La contraseña debe tener al menos 8 caracteres.", "danger")
                        return render_template('admin/cliente_form.html', cliente_form=form, nuevo=True)
                    # Fuerza PBKDF2 (compatibilidad): algunos Python/macOS pueden no traer hashlib.scrypt
                    c.password_hash = generate_password_hash(raw_pw, method="pbkdf2:sha256")

            if not c.fecha_registro:
                c.fecha_registro = ahora
            if not c.created_at:
                c.created_at = ahora
            c.updated_at = ahora

            db.session.add(c)
            db.session.commit()

            flash('Cliente creado correctamente ✅', 'success')
            return redirect(url_for('admin.listar_clientes'))

        except IntegrityError as e:
            db.session.rollback()
            which = parse_integrity_error(e)
            if which == "codigo":
                form.codigo.errors.append("Este código ya está en uso.")
                flash("El código ya está en uso.", "danger")
            elif which == "email":
                form.email.errors.append("Este email ya está registrado.")
                flash("Este email ya está registrado.", "danger")
            else:
                flash("Conflicto con datos únicos. Verifica código y/o email.", "danger")

        except Exception:
            db.session.rollback()
            flash('Ocurrió un error al crear el cliente. Intenta de nuevo.', 'danger')

    elif request.method == 'POST':
        flash('Revisa los campos marcados en rojo.', 'danger')

    return render_template('admin/cliente_form.html', cliente_form=form, nuevo=True)


# ─────────────────────────────────────────────────────────────
# 🔵 Editar cliente
# ─────────────────────────────────────────────────────────────
@admin_bp.route('/clientes/<int:cliente_id>/editar', methods=['GET', 'POST'])
@login_required
@staff_required
@admin_action_limit(bucket="edit_cliente", max_actions=35, window_sec=60)
def editar_cliente(cliente_id):
    """✏️ Editar la información de un cliente existente.

    Fix:
    - El bug solo ocurría cuando se tocaba username/password.
    - Evitamos `form.populate_obj(c)` para que WTForms no intente setear atributos no mapeados.
    - Actualizamos username/password SOLO si el usuario escribió algo.
    - Logueamos el error real en terminal para depurar rápido.
    """
    c = Cliente.query.get_or_404(cliente_id)
    form = AdminClienteForm(obj=c)
    wants_async = _admin_async_wants_json()

    def _flatten_form_errors() -> list[str]:
        out = []
        try:
            for field_errors in (form.errors or {}).values():
                for msg in (field_errors or []):
                    text = str(msg or "").strip()
                    if text:
                        out.append(text)
        except Exception:
            return []
        return out

    def _render_edit_region(async_feedback=None) -> str:
        return render_template(
            'admin/_editar_cliente_form_region.html',
            cliente_form=form,
            cliente=c,
            async_feedback=async_feedback,
        )

    def _render_edit_page(async_feedback=None):
        return render_template(
            'admin/cliente_form.html',
            cliente_form=form,
            nuevo=False,
            cliente=c,
            async_feedback=async_feedback,
        )

    def _async_edit_response(
        *,
        ok: bool,
        message: str,
        category: str,
        http_status: int = 200,
        error_code: str | None = None,
        include_region: bool = True,
        async_feedback=None,
    ):
        payload = _admin_async_payload(
            success=bool(ok),
            message=message,
            category=category,
            replace_html=_render_edit_region(async_feedback=async_feedback) if include_region else None,
            update_target="#editarClienteAsyncRegion",
            errors=_flatten_form_errors(),
            error_code=error_code,
        )
        return jsonify(payload), http_status

    required_fields = ("codigo", "nombre_completo", "email", "telefono")
    missing_fields = [name for name in required_fields if not hasattr(form, name)]
    if request.method == 'POST' and missing_fields:
        msg = 'No se pudo procesar el formulario de edición. Recarga e intenta nuevamente.'
        if wants_async:
            return _async_edit_response(
                ok=False,
                message=msg,
                category='danger',
                http_status=400,
                error_code='invalid_input',
                include_region=False,
            )
        flash(msg, 'danger')
        return _render_edit_page()

    if request.method == 'POST' and not form.validate_on_submit():
        if wants_async:
            return _async_edit_response(
                ok=False,
                message='No se guardó. Revisa los campos marcados y corrige los errores.',
                category='warning',
                http_status=200,
                error_code='invalid_input',
                async_feedback={"message": "No se guardó. Revisa los campos marcados y corrige los errores.", "category": "warning"},
            )
        # Si llegó POST pero no pasó validación, NO debe “parecer” que guardó.
        flash('No se guardó. Revisa los campos marcados y corrige los errores.', 'danger')
        try:
            current_app.logger.warning('editar_cliente validate_on_submit=False | cliente_id=%s | errors=%s', cliente_id, form.errors)
        except Exception:
            pass
        try:
            print('editar_cliente validate_on_submit=False', 'cliente_id=', cliente_id, 'errors=', form.errors)
        except Exception:
            pass
        return _render_edit_page()

    if form.validate_on_submit():
        _norm_cliente_form(form)

        # --- Validar código si se modifica ---
        if hasattr(c, 'codigo') and hasattr(form, 'codigo'):
            new_codigo = (form.codigo.data or '').strip()
            old_codigo = (c.codigo or '').strip()
            if new_codigo != old_codigo:
                try:
                    if Cliente.query.filter(Cliente.codigo == new_codigo).first():
                        form.codigo.errors.append("Este código ya está en uso.")
                        if wants_async:
                            return _async_edit_response(
                                ok=False,
                                message='Este código ya está en uso.',
                                category='danger',
                                http_status=200,
                                error_code='conflict',
                                async_feedback={"message": "Este código ya está en uso.", "category": "danger"},
                            )
                        flash("El código ya está en uso.", "danger")
                        return _render_edit_page()
                except Exception:
                    msg = "No se pudo validar el código del cliente."
                    if wants_async:
                        return _async_edit_response(
                            ok=False,
                            message=msg,
                            category='danger',
                            http_status=500,
                            error_code='server_error',
                            include_region=False,
                        )
                    flash(msg, "danger")
                    return _render_edit_page()

        # --- Validar email si se modifica ---
        email_norm = (getattr(form, 'email', type('x', (), {'data': ''})) .data or '').lower().strip()
        phone_norm = _shared_norm_phone_rd(getattr(form, 'telefono', type('x', (), {'data': ''})).data or '')
        email_actual = (getattr(c, 'email', '') or '').lower().strip()
        if email_norm != email_actual:
            try:
                if Cliente.query.filter(Cliente.email_norm == email_norm).first():
                    if hasattr(form, 'email'):
                        form.email.errors.append("Este email ya está registrado.")
                    if wants_async:
                        return _async_edit_response(
                            ok=False,
                            message='Este email ya está registrado.',
                            category='danger',
                            http_status=200,
                            error_code='conflict',
                            async_feedback={"message": "Este email ya está registrado.", "category": "danger"},
                        )
                    flash("Este email ya está registrado.", "danger")
                    return _render_edit_page()
            except Exception:
                msg = "No se pudo validar el email del cliente."
                if wants_async:
                    return _async_edit_response(
                        ok=False,
                        message=msg,
                        category='danger',
                        http_status=500,
                        error_code='server_error',
                        include_region=False,
                    )
                    flash(msg, "danger")
                    return _render_edit_page()
        if phone_norm and phone_norm != (_shared_norm_phone_rd(getattr(c, "telefono", "") or "")):
            try:
                dup_phone = Cliente.query.filter(
                    Cliente.telefono_norm == phone_norm,
                    Cliente.id != c.id,
                ).first()
                if dup_phone:
                    if hasattr(form, 'telefono'):
                        form.telefono.errors.append("Este teléfono ya está registrado.")
                    if wants_async:
                        return _async_edit_response(
                            ok=False,
                            message='Este teléfono ya está registrado.',
                            category='danger',
                            http_status=200,
                            error_code='conflict',
                            async_feedback={"message": "Este teléfono ya está registrado.", "category": "danger"},
                        )
                    flash("Este teléfono ya está registrado.", "danger")
                    return _render_edit_page()
            except Exception:
                msg = "No se pudo validar el teléfono del cliente."
                if wants_async:
                    return _async_edit_response(
                        ok=False,
                        message=msg,
                        category='danger',
                        http_status=500,
                        error_code='server_error',
                        include_region=False,
                    )
                flash(msg, "danger")
                return _render_edit_page()

        # --- Username: validar solo si el usuario escribió uno ---
        username_to_set = None
        if hasattr(c, 'username'):
            raw_username = None
            if hasattr(form, 'username'):
                raw_username = form.username.data
            if raw_username is None:
                raw_username = request.form.get('username')

            raw_username = (raw_username or '').strip()
            if raw_username:
                username_norm = raw_username.lower()
                username_actual = (getattr(c, 'username', '') or '').strip().lower()

                # Solo validar si realmente cambia
                if username_norm != username_actual:
                    try:
                        # Excluir el mismo cliente
                        exists = Cliente.query.filter(
                            func.lower(Cliente.username) == username_norm,
                            Cliente.id != c.id
                        ).first()
                        if exists:
                            if hasattr(form, 'username'):
                                form.username.errors.append("Este usuario ya está registrado.")
                            if wants_async:
                                return _async_edit_response(
                                    ok=False,
                                    message='Este usuario ya está registrado.',
                                    category='danger',
                                    http_status=200,
                                    error_code='conflict',
                                    async_feedback={"message": "Este usuario ya está registrado.", "category": "danger"},
                                )
                            flash("Este usuario ya está registrado.", "danger")
                            return _render_edit_page()
                    except Exception:
                        msg = "No se pudo validar el usuario del cliente."
                        if wants_async:
                            return _async_edit_response(
                                ok=False,
                                message=msg,
                                category='danger',
                                http_status=500,
                                error_code='server_error',
                                include_region=False,
                            )
                        flash(msg, "danger")
                        return _render_edit_page()

                username_to_set = username_norm

        # --- Password: solo si escriben una nueva ---
        password_to_set = None
        if hasattr(c, 'password_hash'):
            raw_pw = None
            if hasattr(form, 'password'):
                raw_pw = form.password.data
            if raw_pw is None:
                raw_pw = request.form.get('password')
            raw_pw = (raw_pw or '').strip()
            if raw_pw:
                if len(raw_pw) < 8:
                    if hasattr(form, 'password'):
                        form.password.errors.append("La contraseña debe tener al menos 8 caracteres.")
                    if wants_async:
                        return _async_edit_response(
                            ok=False,
                            message='La contraseña debe tener al menos 8 caracteres.',
                            category='danger',
                            http_status=200,
                            error_code='invalid_input',
                            async_feedback={"message": "La contraseña debe tener al menos 8 caracteres.", "category": "danger"},
                        )
                    flash("La contraseña debe tener al menos 8 caracteres.", "danger")
                    return _render_edit_page()
                password_to_set = raw_pw

        # --- Guardar cambios (sin populate_obj) ---
        try:
            # Campos base (solo si existen)
            if hasattr(c, 'codigo') and hasattr(form, 'codigo'):
                c.codigo = (form.codigo.data or '').strip()

            if hasattr(c, 'nombre_completo') and hasattr(form, 'nombre_completo'):
                c.nombre_completo = (form.nombre_completo.data or '').strip()

            if hasattr(c, 'email'):
                c.email = email_norm
                if hasattr(c, "email_norm"):
                    c.email_norm = _shared_nullable_norm_email(email_norm)

            if hasattr(c, 'telefono') and hasattr(form, 'telefono'):
                c.telefono = _normalize_phone(form.telefono.data or '')
                if hasattr(c, "telefono_norm"):
                    c.telefono_norm = _shared_nullable_norm_phone_rd(c.telefono)

            if hasattr(c, 'ciudad') and hasattr(form, 'ciudad'):
                c.ciudad = (form.ciudad.data or '').strip()

            if hasattr(c, 'sector') and hasattr(form, 'sector'):
                c.sector = (form.sector.data or '').strip()

            if hasattr(c, 'notas_admin') and hasattr(form, 'notas_admin'):
                c.notas_admin = (form.notas_admin.data or '').strip()

            # Username (solo si el usuario escribió uno)
            if username_to_set is not None and hasattr(c, 'username'):
                c.username = username_to_set

            # Password (solo si escribió una nueva)
            if password_to_set is not None and hasattr(c, 'password_hash'):
                # Fuerza PBKDF2 (compatibilidad): algunos Python/macOS pueden no traer hashlib.scrypt
                c.password_hash = generate_password_hash(password_to_set, method="pbkdf2:sha256")

            if hasattr(c, 'fecha_ultima_actividad'):
                c.fecha_ultima_actividad = utc_now_naive()
            if hasattr(c, 'updated_at'):
                c.updated_at = utc_now_naive()

            db.session.commit()
            success_msg = 'Cliente actualizado correctamente.'
            if wants_async:
                form = AdminClienteForm(formdata=None, obj=c)
                return _async_edit_response(
                    ok=True,
                    message=success_msg,
                    category='success',
                    http_status=200,
                    async_feedback={"message": success_msg, "category": "success"},
                )
            flash('Cliente actualizado correctamente ✅', 'success')
            return redirect(url_for('admin.detalle_cliente', cliente_id=cliente_id))

        except IntegrityError as e:
            db.session.rollback()
            which = parse_integrity_error(e)
            if which == "codigo":
                if hasattr(form, 'codigo'):
                    form.codigo.errors.append("Este código ya está en uso.")
                conflict_msg = "Este código ya está en uso."
            elif which == "email":
                if hasattr(form, 'email'):
                    form.email.errors.append("Este email ya está registrado.")
                conflict_msg = "Este email ya está registrado."
            elif which == "username":
                if hasattr(form, 'username'):
                    form.username.errors.append("Este usuario ya está registrado.")
                conflict_msg = "Este usuario ya está registrado."
            else:
                # Puede incluir username unique si el parser no lo detecta
                conflict_msg = 'No se pudo actualizar: conflicto con datos únicos (código, email o usuario).'

            if wants_async:
                return _async_edit_response(
                    ok=False,
                    message=conflict_msg,
                    category='danger',
                    http_status=409,
                    error_code='conflict',
                    async_feedback={"message": conflict_msg, "category": "danger"},
                )
            flash(conflict_msg, "danger")

        except Exception:
            db.session.rollback()
            # Mostrar el error real en terminal
            try:
                import traceback
                print("\n=== ERROR REAL editar_cliente ===")
                traceback.print_exc()
                print("=== FIN ERROR ===\n")
            except Exception:
                pass
            error_msg = 'Ocurrió un error al actualizar el cliente. Intenta de nuevo.'
            if wants_async:
                return _async_edit_response(
                    ok=False,
                    message=error_msg,
                    category='danger',
                    http_status=500,
                    error_code='server_error',
                    include_region=False,
                )
            flash(error_msg, 'danger')

    return _render_edit_page()


# ─────────────────────────────────────────────────────────────
# 🔴 Eliminar cliente
# ─────────────────────────────────────────────────────────────
_TABLE_EXISTS_CACHE: dict[str, bool] = {}


def _table_exists(table_name: str) -> bool:
    name = (table_name or "").strip()
    if not name:
        return False
    cached = _TABLE_EXISTS_CACHE.get(name)
    if cached is not None:
        return bool(cached)
    try:
        exists = bool(sa_inspect(db.engine).has_table(name))
    except Exception:
        exists = False
    _TABLE_EXISTS_CACHE[name] = exists
    return exists


def _safe_count(query) -> int:
    try:
        return int(query.scalar() or 0)
    except Exception:
        # Evita que un fallo en una sola consulta deje la sesión en estado abortado
        # y contamine el resto de conteos del plan con errores en cascada.
        try:
            db.session.rollback()
        except Exception:
            pass
        return -1


def _collect_cliente_delete_plan(cliente_id: int) -> dict[str, object]:
    cid = int(cliente_id or 0)
    solicitud_ids: list[int] = []
    chat_conv_ids: list[int] = []
    summary: dict[str, int] = {
        "solicitudes": 0,
        "solicitudes_criticas": 0,
        "solicitudes_candidatas": 0,
        "reemplazos": 0,
        "notificaciones_cliente": 0,
        "notificaciones_solicitud": 0,
        "tokens_publicos_cliente": 0,
        "tokens_publicos_solicitud": 0,
        "tokens_cliente_nuevo_cliente": 0,
        "tokens_cliente_nuevo_solicitud": 0,
        "recommendation_runs": 0,
        "recommendation_items": 0,
        "recommendation_selections": 0,
        "chat_conversations": 0,
        "chat_messages": 0,
        "tareas": 0,
    }
    warnings: list[str] = []

    if _table_exists("solicitudes"):
        try:
            rows = (
                db.session.query(Solicitud.id)
                .filter(Solicitud.cliente_id == cid)
                .all()
            )
            solicitud_ids = [int(r[0]) for r in rows]
            summary["solicitudes"] = len(solicitud_ids)
            protected_states = ("pagada", "activa", "reemplazo", "espera_pago")
            summary["solicitudes_criticas"] = _safe_count(
                db.session.query(func.count(Solicitud.id))
                .filter(
                    Solicitud.cliente_id == cid,
                    func.lower(cast(Solicitud.estado, db.String)).in_(protected_states),
                )
            )
        except SQLAlchemyError:
            warnings.append("No se pudo leer solicitudes del cliente.")
            summary["solicitudes"] = -1
            summary["solicitudes_criticas"] = -1

    if _table_exists("solicitudes_candidatas") and solicitud_ids:
        summary["solicitudes_candidatas"] = _safe_count(
            db.session.query(func.count(SolicitudCandidata.id))
            .filter(SolicitudCandidata.solicitud_id.in_(solicitud_ids))
        )

    if _table_exists("reemplazos") and solicitud_ids:
        summary["reemplazos"] = _safe_count(
            db.session.query(func.count(Reemplazo.id))
            .filter(Reemplazo.solicitud_id.in_(solicitud_ids))
        )

    if _table_exists("clientes_notificaciones"):
        summary["notificaciones_cliente"] = _safe_count(
            db.session.query(func.count(ClienteNotificacion.id))
            .filter(ClienteNotificacion.cliente_id == cid)
        )
        if solicitud_ids:
            summary["notificaciones_solicitud"] = _safe_count(
                db.session.query(func.count(ClienteNotificacion.id))
                .filter(ClienteNotificacion.solicitud_id.in_(solicitud_ids))
            )

    if _table_exists("public_solicitud_tokens_usados"):
        summary["tokens_publicos_cliente"] = _safe_count(
            db.session.query(func.count(PublicSolicitudTokenUso.id))
            .filter(PublicSolicitudTokenUso.cliente_id == cid)
        )
        if solicitud_ids:
            summary["tokens_publicos_solicitud"] = _safe_count(
                db.session.query(func.count(PublicSolicitudTokenUso.id))
                .filter(PublicSolicitudTokenUso.solicitud_id.in_(solicitud_ids))
            )

    if _table_exists("public_solicitud_cliente_nuevo_tokens_usados"):
        summary["tokens_cliente_nuevo_cliente"] = _safe_count(
            db.session.query(func.count(PublicSolicitudClienteNuevoTokenUso.id))
            .filter(PublicSolicitudClienteNuevoTokenUso.cliente_id == cid)
        )
        if solicitud_ids:
            summary["tokens_cliente_nuevo_solicitud"] = _safe_count(
                db.session.query(func.count(PublicSolicitudClienteNuevoTokenUso.id))
                .filter(PublicSolicitudClienteNuevoTokenUso.solicitud_id.in_(solicitud_ids))
            )

    if _table_exists("tareas_clientes"):
        summary["tareas"] = _safe_count(
            db.session.query(func.count(TareaCliente.id))
            .filter(TareaCliente.cliente_id == cid)
        )

    if _table_exists("solicitud_recommendation_runs") and solicitud_ids:
        summary["recommendation_runs"] = _safe_count(
            db.session.query(func.count(SolicitudRecommendationRun.id))
            .filter(SolicitudRecommendationRun.solicitud_id.in_(solicitud_ids))
        )

    if _table_exists("solicitud_recommendation_items") and solicitud_ids:
        summary["recommendation_items"] = _safe_count(
            db.session.query(func.count(SolicitudRecommendationItem.id))
            .filter(SolicitudRecommendationItem.solicitud_id.in_(solicitud_ids))
        )

    if _table_exists("solicitud_recommendation_selections") and solicitud_ids:
        summary["recommendation_selections"] = _safe_count(
            db.session.query(func.count(SolicitudRecommendationSelection.id))
            .filter(SolicitudRecommendationSelection.solicitud_id.in_(solicitud_ids))
        )

    if ChatConversation is not None and _table_exists("chat_conversations"):
        try:
            chat_rows = (
                db.session.query(ChatConversation.id)
                .filter(ChatConversation.cliente_id == cid)
                .all()
            )
            chat_conv_ids = [int(r[0]) for r in (chat_rows or []) if int(r[0] or 0) > 0]
            summary["chat_conversations"] = len(chat_conv_ids)
        except SQLAlchemyError:
            warnings.append("No se pudo leer conversaciones de chat del cliente.")
            summary["chat_conversations"] = -1

    if ChatMessage is not None and _table_exists("chat_messages"):
        msg_filters = [ChatMessage.sender_cliente_id == cid]
        if chat_conv_ids:
            msg_filters.append(ChatMessage.conversation_id.in_(chat_conv_ids))
        summary["chat_messages"] = _safe_count(
            db.session.query(func.count(ChatMessage.id))
            .filter(or_(*msg_filters))
        )

    blocked_issues: list[str] = []
    if int(summary.get("solicitudes_criticas") or 0) > 0:
        blocked_issues.append(
            "El cliente tiene solicitudes activas/pagadas/reemplazo/espera de pago y no puede eliminarse."
        )
    if solicitud_ids and _table_exists("clientes_notificaciones"):
        mismatch = _safe_count(
            db.session.query(func.count(ClienteNotificacion.id))
            .filter(
                ClienteNotificacion.solicitud_id.in_(solicitud_ids),
                ClienteNotificacion.cliente_id != cid,
            )
        )
        if mismatch > 0:
            blocked_issues.append(
                "Existen notificaciones cruzadas con otro cliente en las solicitudes."
            )

    if solicitud_ids and _table_exists("public_solicitud_tokens_usados"):
        mismatch = _safe_count(
            db.session.query(func.count(PublicSolicitudTokenUso.id))
            .filter(
                PublicSolicitudTokenUso.solicitud_id.in_(solicitud_ids),
                PublicSolicitudTokenUso.cliente_id != cid,
            )
        )
        if mismatch > 0:
            blocked_issues.append(
                "Existen tokens públicos cruzados con otro cliente en las solicitudes."
            )

    if solicitud_ids and _table_exists("public_solicitud_cliente_nuevo_tokens_usados"):
        mismatch = _safe_count(
            db.session.query(func.count(PublicSolicitudClienteNuevoTokenUso.id))
            .filter(
                PublicSolicitudClienteNuevoTokenUso.solicitud_id.in_(solicitud_ids),
                PublicSolicitudClienteNuevoTokenUso.cliente_id.isnot(None),
                PublicSolicitudClienteNuevoTokenUso.cliente_id != cid,
            )
        )
        if mismatch > 0:
            blocked_issues.append(
                "Existen tokens de cliente nuevo cruzados con otro cliente en las solicitudes."
            )

    # Protección defensiva: si hay tablas con FK a clientes/solicitudes no gestionadas,
    # bloquear para evitar borrados parciales inesperados.
    managed_tables = {
        "clientes",
        "solicitudes",
        "solicitudes_candidatas",
        "reemplazos",
        "clientes_notificaciones",
        "public_solicitud_tokens_usados",
        "public_solicitud_cliente_nuevo_tokens_usados",
        "solicitud_recommendation_runs",
        "solicitud_recommendation_items",
        "solicitud_recommendation_selections",
        "chat_conversations",
        "chat_messages",
        "tareas_clientes",
    }
    try:
        inspector = sa_inspect(db.engine)
        for table_name in inspector.get_table_names():
            if table_name in managed_tables:
                continue
            fks = inspector.get_foreign_keys(table_name) or []
            has_ref = any((fk.get("referred_table") or "") in {"clientes", "solicitudes"} for fk in fks)
            if not has_ref:
                continue
            # Evita reflexión pesada por tabla en cada solicitud:
            # se construye una tabla ligera con columnas FK detectadas.
            fk_cols: set[str] = set()
            for fk in fks:
                for col_name in (fk.get("constrained_columns") or []):
                    if col_name:
                        fk_cols.add(str(col_name))
            if not fk_cols:
                continue
            tbl = sa_table(table_name, *[sa_column(col_name) for col_name in sorted(fk_cols)])
            row_hits = 0
            for fk in fks:
                ref_table = (fk.get("referred_table") or "").strip()
                cols = fk.get("constrained_columns") or []
                if not cols:
                    continue
                for col_name in cols:
                    if str(col_name) not in tbl.c:
                        continue
                    col = tbl.c[str(col_name)]
                    if ref_table == "clientes":
                        row_hits += int(
                            db.session.execute(
                                sa_select(func.count()).select_from(tbl).where(col == cid)
                            ).scalar() or 0
                        )
                    elif ref_table == "solicitudes" and solicitud_ids:
                        row_hits += int(
                            db.session.execute(
                                sa_select(func.count()).select_from(tbl).where(col.in_(solicitud_ids))
                            ).scalar() or 0
                        )
            if row_hits > 0:
                blocked_issues.append(
                    f"Dependencia no gestionada detectada en tabla '{table_name}'."
                )
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        warnings.append("No se pudo completar la inspección de dependencias no gestionadas.")

    return {
        "cliente_id": cid,
        "solicitud_ids": solicitud_ids,
        "summary": summary,
        "warnings": warnings,
        "blocked_issues": blocked_issues,
    }


def _delete_plan_has_uncertain_inspection(plan: dict[str, object]) -> bool:
    warnings = list(plan.get("warnings") or [])
    if warnings:
        return True
    summary = dict(plan.get("summary") or {})
    for value in summary.values():
        try:
            if int(value) < 0:
                return True
        except Exception:
            continue
    return False


def _delete_cliente_tree(cliente_id: int, solicitud_ids: list[int]) -> dict[str, int]:
    cid = int(cliente_id or 0)
    sid_list = [int(sid) for sid in (solicitud_ids or []) if int(sid or 0) > 0]
    chat_conv_ids: list[int] = []
    deleted: dict[str, int] = {
        "solicitudes_candidatas": 0,
        "reemplazos": 0,
        "notificaciones_solicitud": 0,
        "tokens_publicos_solicitud": 0,
        "tokens_cliente_nuevo_solicitud": 0,
        "recommendation_selections": 0,
        "recommendation_items": 0,
        "recommendation_runs": 0,
        "chat_messages": 0,
        "chat_conversations": 0,
        "solicitudes": 0,
        "tareas": 0,
        "notificaciones_cliente": 0,
        "tokens_publicos_cliente": 0,
        "tokens_cliente_nuevo_cliente": 0,
        "cliente": 0,
    }
    run_ids: list[int] = []
    item_ids: list[int] = []

    if sid_list and _table_exists("solicitudes_candidatas"):
        deleted["solicitudes_candidatas"] = int(
            SolicitudCandidata.query
            .filter(SolicitudCandidata.solicitud_id.in_(sid_list))
            .delete(synchronize_session=False)
            or 0
        )

    if sid_list and _table_exists("reemplazos"):
        deleted["reemplazos"] = int(
            Reemplazo.query
            .filter(Reemplazo.solicitud_id.in_(sid_list))
            .delete(synchronize_session=False)
            or 0
        )

    if sid_list and _table_exists("clientes_notificaciones"):
        deleted["notificaciones_solicitud"] = int(
            ClienteNotificacion.query
            .filter(
                ClienteNotificacion.solicitud_id.in_(sid_list),
                ClienteNotificacion.cliente_id == cid,
            )
            .delete(synchronize_session=False)
            or 0
        )

    if sid_list and _table_exists("public_solicitud_tokens_usados"):
        deleted["tokens_publicos_solicitud"] = int(
            PublicSolicitudTokenUso.query
            .filter(
                PublicSolicitudTokenUso.solicitud_id.in_(sid_list),
                PublicSolicitudTokenUso.cliente_id == cid,
            )
            .delete(synchronize_session=False)
            or 0
        )

    if sid_list and _table_exists("public_solicitud_cliente_nuevo_tokens_usados"):
        deleted["tokens_cliente_nuevo_solicitud"] = int(
            PublicSolicitudClienteNuevoTokenUso.query
            .filter(
                PublicSolicitudClienteNuevoTokenUso.solicitud_id.in_(sid_list),
                (
                    (PublicSolicitudClienteNuevoTokenUso.cliente_id == cid)
                    | (PublicSolicitudClienteNuevoTokenUso.cliente_id.is_(None))
                ),
            )
            .delete(synchronize_session=False)
            or 0
        )

    if sid_list and _table_exists("solicitud_recommendation_runs"):
        run_rows = (
            db.session.query(SolicitudRecommendationRun.id)
            .filter(SolicitudRecommendationRun.solicitud_id.in_(sid_list))
            .all()
        )
        run_ids = [int(row[0]) for row in (run_rows or []) if int(row[0] or 0) > 0]

    if sid_list and _table_exists("solicitud_recommendation_items"):
        item_filters = [SolicitudRecommendationItem.solicitud_id.in_(sid_list)]
        if run_ids:
            item_filters.append(SolicitudRecommendationItem.run_id.in_(run_ids))
        item_rows = (
            db.session.query(SolicitudRecommendationItem.id)
            .filter(or_(*item_filters))
            .all()
        )
        item_ids = [int(row[0]) for row in (item_rows or []) if int(row[0] or 0) > 0]

    if sid_list and _table_exists("solicitud_recommendation_selections"):
        sel_filters = [SolicitudRecommendationSelection.solicitud_id.in_(sid_list)]
        if run_ids:
            sel_filters.append(SolicitudRecommendationSelection.run_id.in_(run_ids))
        if item_ids:
            sel_filters.append(SolicitudRecommendationSelection.recommendation_item_id.in_(item_ids))
        deleted["recommendation_selections"] = int(
            SolicitudRecommendationSelection.query
            .filter(or_(*sel_filters))
            .delete(synchronize_session=False)
            or 0
        )

    if sid_list and _table_exists("solicitud_recommendation_items"):
        item_filters = [SolicitudRecommendationItem.solicitud_id.in_(sid_list)]
        if run_ids:
            item_filters.append(SolicitudRecommendationItem.run_id.in_(run_ids))
        deleted["recommendation_items"] = int(
            SolicitudRecommendationItem.query
            .filter(or_(*item_filters))
            .delete(synchronize_session=False)
            or 0
        )

    if sid_list and _table_exists("solicitud_recommendation_runs"):
        deleted["recommendation_runs"] = int(
            SolicitudRecommendationRun.query
            .filter(SolicitudRecommendationRun.solicitud_id.in_(sid_list))
            .delete(synchronize_session=False)
            or 0
        )

    if ChatConversation is not None and _table_exists("chat_conversations"):
        chat_rows = (
            db.session.query(ChatConversation.id)
            .filter(ChatConversation.cliente_id == cid)
            .all()
        )
        chat_conv_ids = [int(r[0]) for r in (chat_rows or []) if int(r[0] or 0) > 0]

    if ChatMessage is not None and _table_exists("chat_messages"):
        msg_filters = [ChatMessage.sender_cliente_id == cid]
        if chat_conv_ids:
            msg_filters.append(ChatMessage.conversation_id.in_(chat_conv_ids))
        deleted["chat_messages"] = int(
            ChatMessage.query
            .filter(or_(*msg_filters))
            .delete(synchronize_session=False)
            or 0
        )

    if ChatConversation is not None and _table_exists("chat_conversations"):
        deleted["chat_conversations"] = int(
            ChatConversation.query
            .filter(ChatConversation.cliente_id == cid)
            .delete(synchronize_session=False)
            or 0
        )

    if _table_exists("solicitudes"):
        if sid_list:
            deleted["solicitudes"] = int(
                Solicitud.query
                .filter(Solicitud.id.in_(sid_list), Solicitud.cliente_id == cid)
                .delete(synchronize_session=False)
                or 0
            )
        else:
            deleted["solicitudes"] = int(
                Solicitud.query
                .filter(Solicitud.cliente_id == cid)
                .delete(synchronize_session=False)
                or 0
            )

    if _table_exists("tareas_clientes"):
        deleted["tareas"] = int(
            TareaCliente.query
            .filter(TareaCliente.cliente_id == cid)
            .delete(synchronize_session=False)
            or 0
        )

    if _table_exists("clientes_notificaciones"):
        deleted["notificaciones_cliente"] = int(
            ClienteNotificacion.query
            .filter(ClienteNotificacion.cliente_id == cid)
            .delete(synchronize_session=False)
            or 0
        )

    if _table_exists("public_solicitud_tokens_usados"):
        deleted["tokens_publicos_cliente"] = int(
            PublicSolicitudTokenUso.query
            .filter(PublicSolicitudTokenUso.cliente_id == cid)
            .delete(synchronize_session=False)
            or 0
        )

    if _table_exists("public_solicitud_cliente_nuevo_tokens_usados"):
        deleted["tokens_cliente_nuevo_cliente"] = int(
            PublicSolicitudClienteNuevoTokenUso.query
            .filter(PublicSolicitudClienteNuevoTokenUso.cliente_id == cid)
            .delete(synchronize_session=False)
            or 0
        )

    deleted["cliente"] = int(
        Cliente.query
        .filter(Cliente.id == cid)
        .delete(synchronize_session=False)
        or 0
    )
    return deleted


@admin_bp.route('/clientes/<int:cliente_id>/eliminar', methods=['POST'])
@login_required
@admin_required
@admin_action_limit(bucket="delete_cliente", max_actions=10, window_sec=60)
def eliminar_cliente(cliente_id):
    """🗑️ Eliminar un cliente definitivamente."""
    _owner_only()
    c = Cliente.query.get_or_404(cliente_id)
    cliente_pk = int(getattr(c, "id", 0) or 0)
    cliente_code = str((getattr(c, "codigo", "") or "")).strip() or str(cliente_pk)
    next_url = (request.form.get("next") or request.args.get("next") or request.referrer or "").strip()
    fallback = url_for("admin.listar_clientes")

    blocked_resp = _admin_block_sensitive_action(
        scope="admin_cliente_delete",
        entity_type="Cliente",
        entity_id=cliente_pk,
        limit=10,
        window_seconds=600,
        min_interval_seconds=1,
        summary=f"Bloqueo de eliminación de cliente por patrón de abuso (cliente {cliente_pk})",
        next_url=next_url,
        fallback=fallback,
    )
    if blocked_resp is not None:
        if _admin_async_wants_json():
            return _clientes_list_action_response(
                ok=False,
                message="Demasiadas acciones seguidas. Espera un momento e intenta nuevamente.",
                category="warning",
                next_url=next_url,
                fallback=fallback,
                http_status=429,
                error_code="rate_limit",
            )
        return blocked_resp

    idem_row, duplicate = _claim_idempotency(
        scope="admin_cliente_delete",
        entity_type="Cliente",
        entity_id=cliente_pk,
        action="eliminar_cliente",
    )
    if duplicate:
        if _idempotency_request_conflict(idem_row):
            return _clientes_list_action_response(
                ok=False,
                message=_idempotency_conflict_message(),
                category="warning",
                next_url=next_url,
                fallback=fallback,
                http_status=409,
                error_code="idempotency_conflict",
            )
        prev_status = int(getattr(idem_row, "response_status", 0) or 0)
        if 200 <= prev_status < 300:
            return _clientes_list_action_response(
                ok=True,
                message="Acción ya aplicada previamente.",
                category="info",
                next_url=next_url,
                fallback=fallback,
            )
        return _clientes_list_action_response(
            ok=False,
            message="Solicitud duplicada detectada. Espera y vuelve a intentar.",
            category="warning",
            next_url=next_url,
            fallback=fallback,
            http_status=409,
            error_code="conflict",
        )

    plan = _collect_cliente_delete_plan(cliente_pk)
    blocked_issues = list(plan.get("blocked_issues") or [])
    warnings = list(plan.get("warnings") or [])
    summary = dict(plan.get("summary") or {})
    solicitud_ids = list(plan.get("solicitud_ids") or [])
    inspection_uncertain = _delete_plan_has_uncertain_inspection(plan)

    if blocked_issues:
        msg = "Este cliente no puede eliminarse de forma segura: " + " | ".join(blocked_issues)
        _audit_log(
            action_type="CLIENTE_DELETE_BLOCKED",
            entity_type="Cliente",
            entity_id=str(cliente_pk),
            summary=f"Borrado bloqueado para cliente {cliente_code}",
            metadata={
                "blocked_issues": blocked_issues,
                "warnings": warnings,
                "dependency_summary": summary,
            },
            success=False,
            error=msg,
        )
        return _clientes_list_action_response(
            ok=False,
            message=msg,
            category="warning",
            next_url=next_url,
            fallback=fallback,
            http_status=409,
            error_code="conflict",
        )

    if inspection_uncertain:
        msg = (
            "No se pudo validar de forma confiable todas las dependencias del cliente. "
            "La eliminación fue cancelada y no se aplicaron cambios."
        )
        _audit_log(
            action_type="CLIENTE_DELETE_BLOCKED",
            entity_type="Cliente",
            entity_id=str(cliente_pk),
            summary=f"Borrado cancelado por inspección incompleta para cliente {cliente_code}",
            metadata={
                "warnings": warnings,
                "dependency_summary": summary,
            },
            success=False,
            error=msg,
        )
        return _clientes_list_action_response(
            ok=False,
            message=msg,
            category="warning",
            next_url=next_url,
            fallback=fallback,
            http_status=409,
            error_code="dependency_inspection_failed",
        )

    try:
        deleted_rows: dict[str, int] = {}
        with db.session.begin_nested():
            deleted_rows = _delete_cliente_tree(cliente_pk, solicitud_ids=solicitud_ids)
            if int(deleted_rows.get("cliente") or 0) != 1:
                raise SQLAlchemyError("No se pudo confirmar la eliminación del cliente.")
            db.session.flush()
            _set_idempotency_response(idem_row, status=200, code="ok")
        db.session.commit()
        _audit_log(
            action_type="CLIENTE_DELETE_OK",
            entity_type="Cliente",
            entity_id=str(cliente_pk),
            summary=f"Cliente eliminado {cliente_code}",
            metadata={
                "deleted_rows": deleted_rows,
                "dependency_summary": summary,
                "warnings": warnings,
            },
            success=True,
        )
        return _clientes_list_action_response(
            ok=True,
            message='Cliente eliminado correctamente.',
            category='success',
            next_url=next_url,
            fallback=fallback,
        )
    except IntegrityError:
        db.session.rollback()
        msg = (
            "No se pudo eliminar el cliente por restricciones de integridad. "
            "No se aplicaron cambios."
        )
        _audit_log(
            action_type="CLIENTE_DELETE_FAIL",
            entity_type="Cliente",
            entity_id=str(cliente_pk),
            summary=f"Error de integridad al eliminar cliente {cliente_code}",
            metadata={
                "dependency_summary": summary,
                "warnings": warnings,
            },
            success=False,
            error=msg,
        )
        return _clientes_list_action_response(
            ok=False,
            message=msg,
            category="danger",
            next_url=next_url,
            fallback=fallback,
            http_status=409,
            error_code="conflict",
        )
    except SQLAlchemyError:
        db.session.rollback()
        msg = 'No se pudo eliminar el cliente de forma segura. No se aplicaron cambios.'
        _audit_log(
            action_type="CLIENTE_DELETE_FAIL",
            entity_type="Cliente",
            entity_id=str(cliente_pk),
            summary=f"Fallo técnico al eliminar cliente {cliente_code}",
            metadata={
                "dependency_summary": summary,
                "warnings": warnings,
            },
            success=False,
            error=msg,
        )
        return _clientes_list_action_response(
            ok=False,
            message=msg,
            category='danger',
            next_url=next_url,
            fallback=fallback,
            http_status=500,
            error_code='server_error',
        )


# ─────────────────────────────────────────────────────────────
# 🔍 Detalle de cliente
# ─────────────────────────────────────────────────────────────
def _cliente_detail_regions_context(
    cliente_id: int,
    *,
    include_kpi: bool = True,
    include_timeline: bool = True,
) -> dict:
    cliente = Cliente.query.get_or_404(cliente_id)
    solicitud_attrs = []
    for attr in (
        "id",
        "cliente_id",
        "candidata_id",
        "codigo_solicitud",
        "fecha_solicitud",
        "ciudad_sector",
        "tipo_servicio",
        "modalidad_trabajo",
        "tipo_plan",
        "estado",
        "row_version",
        "monto_pagado",
        "last_copiado_at",
        "fecha_ultima_modificacion",
        "fecha_cancelacion",
        "motivo_cancelacion",
    ):
        if hasattr(Solicitud, attr):
            solicitud_attrs.append(getattr(Solicitud, attr))

    reemplazo_attrs = []
    for attr in (
        "id",
        "solicitud_id",
        "candidata_new_id",
        "fecha_inicio_reemplazo",
        "fecha_fin_reemplazo",
        "created_at",
    ):
        if hasattr(Reemplazo, attr):
            reemplazo_attrs.append(getattr(Reemplazo, attr))

    options_list = []
    if solicitud_attrs:
        options_list.append(load_only(*solicitud_attrs))
    try:
        repl_loader = selectinload(Solicitud.reemplazos)
        if reemplazo_attrs:
            repl_loader = repl_loader.load_only(*reemplazo_attrs)
        if include_timeline:
            candidata_new_attrs = []
            for attr in ("fila", "nombre_completo"):
                if hasattr(Candidata, attr):
                    candidata_new_attrs.append(getattr(Candidata, attr))
            if candidata_new_attrs:
                repl_loader = repl_loader.joinedload(Reemplazo.candidata_new).load_only(*candidata_new_attrs)
            else:
                repl_loader = repl_loader.joinedload(Reemplazo.candidata_new)
        options_list.append(repl_loader)
    except Exception:
        pass

    solicitudes = (
        Solicitud.query
        .options(*options_list)
        .filter_by(cliente_id=cliente_id)
        .order_by(Solicitud.fecha_solicitud.desc())
        .all()
    )
    kpi_cliente = _build_cliente_summary_kpi(cliente=cliente, solicitudes=solicitudes) if include_kpi else None
    reemplazos_activos = {int(s.id): _active_reemplazo_for_solicitud(s) for s in (solicitudes or [])}
    role = (
        str(getattr(current_user, "role", "") or "").strip().lower()
        or str(session.get("role", "") or "").strip().lower()
    )
    is_admin_role = role in ("owner", "admin")
    contracts_schema_ready = True
    latest_contracts_by_solicitud = {}
    contract_links_by_solicitud = {}
    solicitud_ids = [int(s.id) for s in (solicitudes or []) if int(getattr(s, "id", 0) or 0) > 0]
    if solicitud_ids:
        try:
            contract_rows = (
                ContratoDigital.query.options(
                    load_only(
                        ContratoDigital.id,
                        ContratoDigital.solicitud_id,
                        ContratoDigital.version,
                        ContratoDigital.estado,
                        ContratoDigital.enviado_at,
                        ContratoDigital.firmado_at,
                        ContratoDigital.token_expira_at,
                        ContratoDigital.pdf_final_size_bytes,
                        ContratoDigital.anulado_at,
                        ContratoDigital.anulado_motivo,
                    )
                )
                .filter(ContratoDigital.solicitud_id.in_(solicitud_ids))
                .order_by(ContratoDigital.solicitud_id.asc(), ContratoDigital.version.desc(), ContratoDigital.id.desc())
                .all()
            )
            for c in (contract_rows or []):
                sid = int(getattr(c, "solicitud_id", 0) or 0)
                if sid <= 0 or sid in latest_contracts_by_solicitud:
                    continue
                is_expired = _is_contract_expired(c)
                latest_contracts_by_solicitud[sid] = {
                    "contract": c,
                    "effective_state": _contract_effective_state(c, contrato_expirado=is_expired),
                    "is_expired": bool(is_expired),
                    "has_pdf": bool(getattr(c, "pdf_final_size_bytes", 0)),
                    "is_annulled": bool(getattr(c, "anulado_at", None)),
                }
            session_links = session.get("contract_links")
            if isinstance(session_links, dict):
                for sid, payload in latest_contracts_by_solicitud.items():
                    contract_obj = payload.get("contract")
                    cid = int(getattr(contract_obj, "id", 0) or 0) if contract_obj is not None else 0
                    if cid > 0:
                        contract_links_by_solicitud[sid] = str(session_links.get(str(cid)) or "")
        except OperationalError as exc:
            db.session.rollback()
            if _is_missing_contract_table_error(exc):
                contracts_schema_ready = False
            else:
                raise

    return {
        "cliente": cliente,
        "solicitudes": solicitudes,
        "kpi_cliente": kpi_cliente,
        "reemplazos_activos": reemplazos_activos,
        "is_admin_role": is_admin_role,
        "contracts_schema_ready": contracts_schema_ready,
        "latest_contracts_by_solicitud": latest_contracts_by_solicitud,
        "contract_links_by_solicitud": contract_links_by_solicitud,
        "chat_feature_enabled": _chat_enabled(),
    }


def _cliente_detail_summary_context(cliente_id: int) -> dict:
    cliente = Cliente.query.get_or_404(cliente_id)
    solicitudes = (
        Solicitud.query
        .options(
            load_only(
                Solicitud.id,
                Solicitud.estado,
                Solicitud.monto_pagado,
                Solicitud.fecha_solicitud,
            )
        )
        .filter_by(cliente_id=cliente_id)
        .order_by(Solicitud.fecha_solicitud.desc())
        .all()
    )
    return {
        "cliente": cliente,
        "kpi_cliente": _build_cliente_summary_kpi(cliente=cliente, solicitudes=solicitudes),
        "chat_feature_enabled": _chat_enabled(),
    }


@admin_bp.route('/clientes/<int:cliente_id>')
@login_required
@staff_required
def detalle_cliente(cliente_id):
    """
    Vista 360° del cliente:
    - Datos del cliente
    - Resumen de solicitudes (totales, estados, monto pagado)
    - Lista de solicitudes del cliente
    - Línea de tiempo simple de eventos (creación, publicaciones, pagos, cancelaciones, reemplazos)
    - Tareas de seguimiento del cliente
    """

    region_ctx = _cliente_detail_regions_context(cliente_id)
    cliente = region_ctx["cliente"]
    solicitudes = region_ctx["solicitudes"]

    # ------------------------------
    # TIMELINE SIMPLE (HUMANO)
    # ------------------------------
    timeline = []

    for s in solicitudes:
        codigo = s.codigo_solicitud or s.id

        # 1) Creación de la solicitud
        if s.fecha_solicitud:
            timeline.append({
                'fecha': s.fecha_solicitud,
                'tipo': 'Solicitud creada',
                'detalle': f"Se creó la solicitud {codigo} para este cliente."
            })

        # 2) Solicitud activada / en búsqueda (lo más parecido a 'publicada')
        #    Usamos fecha_ultima_modificacion como referencia.
        if s.estado == 'activa' and getattr(s, 'fecha_ultima_modificacion', None):
            timeline.append({
                'fecha': s.fecha_ultima_modificacion,
                'tipo': 'Solicitud activada',
                'detalle': f"La solicitud {codigo} está activa y en búsqueda de candidata."
            })

        # 3) Solicitud copiada para publicar (texto que se copia para redes / grupos)
        if getattr(s, 'last_copiado_at', None):
            timeline.append({
                'fecha': s.last_copiado_at,
                'tipo': 'Solicitud copiada para publicar',
                'detalle': f"Se copió el texto de la solicitud {codigo} para publicarla en redes o grupos."
            })

        # 4) Pago registrado
        if s.estado == 'pagada' and getattr(s, 'fecha_ultima_modificacion', None):
            timeline.append({
                'fecha': s.fecha_ultima_modificacion,
                'tipo': 'Pago registrado',
                'detalle': f"La solicitud {codigo} fue marcada como pagada."
            })

        # 5) Solicitud cancelada
        if s.estado == 'cancelada' and getattr(s, 'fecha_cancelacion', None):
            motivo = (s.motivo_cancelacion or '').strip()
            texto_motivo = motivo or 'Sin motivo especificado por el cliente.'
            timeline.append({
                'fecha': s.fecha_cancelacion,
                'tipo': 'Solicitud cancelada',
                'detalle': f"La solicitud {codigo} fue cancelada. Motivo: {texto_motivo}"
            })

        # 6) Reemplazos activados
        for r in (s.reemplazos or []):
            fecha_r = getattr(r, 'fecha_inicio_reemplazo', None) or getattr(r, 'created_at', None)
            if not fecha_r:
                continue

            nombre_new = getattr(getattr(r, 'candidata_new', None), 'nombre_completo', None)
            if nombre_new:
                detalle_r = f"Se activó un reemplazo en la solicitud {codigo} con la candidata {nombre_new}."
            else:
                detalle_r = f"Se activó un reemplazo en la solicitud {codigo}."

            timeline.append({
                'fecha': fecha_r,
                'tipo': 'Reemplazo activado',
                'detalle': detalle_r
            })

    # Ordenar timeline de más reciente a más viejo
    timeline = sorted(timeline, key=lambda e: e['fecha'], reverse=True)

    # ------------------------------
    # TAREAS DEL CLIENTE
    # ------------------------------
    tareas = (
        TareaCliente.query
        .filter_by(cliente_id=cliente_id)
        .order_by(
            TareaCliente.estado != 'pendiente',             # primero pendientes
            TareaCliente.fecha_vencimiento.is_(None),       # luego las que no tienen fecha
            TareaCliente.fecha_vencimiento.asc(),           # las que vencen antes van arriba
            TareaCliente.fecha_creacion.desc()              # últimas creadas al final dentro del mismo grupo
        )
        .all()
    )
    return render_template(
        'admin/cliente_detail.html',
        timeline=timeline,
        tareas=tareas,
        **region_ctx,
    )


@admin_bp.route('/clientes/<int:cliente_id>/_summary')
@login_required
@staff_required
def cliente_detail_summary_fragment(cliente_id):
    with _p1c1_perf_scope("cliente_detail_summary_fragment") as perf_done:
        region_ctx = _cliente_detail_summary_context(cliente_id)
        html = render_template(
            'admin/_cliente_detail_summary_region.html',
            cliente=region_ctx["cliente"],
            kpi_cliente=region_ctx["kpi_cliente"],
            chat_feature_enabled=region_ctx["chat_feature_enabled"],
        )
        response = make_response(html, 200)
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        response.headers["X-Async-Fragment-Region"] = "clienteSummaryAsyncRegion"
        return perf_done(response, html_bytes=len(html.encode("utf-8")), extra={"mode": "fragment_summary"})


@admin_bp.route('/clientes/<int:cliente_id>/_solicitudes')
@login_required
@staff_required
def cliente_detail_solicitudes_fragment(cliente_id):
    with _p1c1_perf_scope("cliente_detail_solicitudes_fragment") as perf_done:
        region_ctx = _cliente_detail_regions_context(
            cliente_id,
            include_kpi=False,
            include_timeline=False,
        )
        html = render_template(
            'admin/_cliente_detail_solicitudes_region.html',
            cliente=region_ctx["cliente"],
            solicitudes=region_ctx["solicitudes"],
            reemplazos_activos=region_ctx["reemplazos_activos"],
            is_admin_role=region_ctx["is_admin_role"],
            contracts_schema_ready=region_ctx["contracts_schema_ready"],
            latest_contracts_by_solicitud=region_ctx["latest_contracts_by_solicitud"],
            contract_links_by_solicitud=region_ctx["contract_links_by_solicitud"],
            chat_feature_enabled=region_ctx["chat_feature_enabled"],
        )
        response = make_response(html, 200)
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        response.headers["X-Async-Fragment-Region"] = "clienteSolicitudesAsyncRegion"
        return perf_done(response, html_bytes=len(html.encode("utf-8")), extra={"mode": "fragment_solicitudes"})


@admin_bp.route('/tareas/pendientes')
@login_required
@staff_required
def tareas_pendientes():
    """
    Lista todas las tareas que NO están completadas, ordenadas por fecha de vencimiento.
    """
    hoy = rd_today()

    tareas = (
        TareaCliente.query
        .options(joinedload(TareaCliente.cliente))
        .filter(TareaCliente.estado != 'completada')
        .order_by(
            TareaCliente.fecha_vencimiento.is_(None),
            TareaCliente.fecha_vencimiento.asc(),
            TareaCliente.fecha_creacion.desc()
        )
        .all()
    )

    return render_template(
        'admin/tareas_pendientes.html',
        tareas=tareas,
        hoy=hoy
    )


def _tareas_hoy_operational_kpis(*, today_rd: date, trabajo_del_dia_total: int) -> dict:
    now_dt = utc_now_naive()
    allowed_states = ("proceso", "activa", "reemplazo", "espera_pago")
    solicitud_attrs = []
    for attr in (
        "id",
        "estado",
        "estado_actual_desde",
        "fecha_seguimiento_manual",
        "fecha_ultima_actividad",
        "fecha_ultima_modificacion",
        "updated_at",
        "fecha_solicitud",
    ):
        if hasattr(Solicitud, attr):
            solicitud_attrs.append(getattr(Solicitud, attr))

    options_list = []
    if solicitud_attrs:
        options_list.append(load_only(*solicitud_attrs))
    try:
        options_list.append(
            selectinload(Solicitud.reemplazos).load_only(
                Reemplazo.id,
                Reemplazo.fecha_inicio_reemplazo,
                Reemplazo.fecha_fin_reemplazo,
                Reemplazo.created_at,
            )
        )
    except Exception:
        pass

    query = Solicitud.query
    if options_list:
        query = query.options(*options_list)
    rows = (
        query
        .filter(Solicitud.estado.in_(allowed_states))
        .all()
    )

    solicitud_ids = [int(getattr(s, "id", 0) or 0) for s in (rows or []) if int(getattr(s, "id", 0) or 0) > 0]
    actor_by_solicitud = _resolve_solicitud_last_actor_user_ids(solicitud_ids)
    actor_ids = sorted({
        int(uid) for uid in (actor_by_solicitud.values() or [])
        if uid is not None and int(uid or 0) > 0
    })
    username_by_actor = _staff_username_map(actor_ids)

    espera_pago = 0
    vencidas = 0
    atencion_hoy = 0
    reemplazos_activos = 0
    sin_responsable = 0
    sin_movimiento = 0
    espera_pago_prolongada = 0
    reemplazo_sin_seguimiento = 0
    carga_raw: dict[int | None, dict] = {}

    for s in (rows or []):
        sid = int(getattr(s, "id", 0) or 0)
        estado = str(getattr(s, "estado", "") or "").strip().lower()
        if estado == "espera_pago":
            espera_pago += 1

        has_active_reemplazo = bool(_active_reemplazo_for_solicitud(s))
        if estado == "reemplazo" and has_active_reemplazo:
            reemplazos_activos += 1

        manual = _manual_followup_snapshot(
            getattr(s, "fecha_seguimiento_manual", None),
            today_rd=today_rd,
        )
        if manual["state"] == "vencida":
            vencidas += 1
        elif manual["state"] == "hoy":
            atencion_hoy += 1

        _score, priority_label, _is_stagnant, _hours = _solicitud_priority_snapshot(s, now_dt=now_dt)
        _ = _score, _is_stagnant, _hours
        estado_desde, _source, _estimated = resolve_solicitud_estado_priority_anchor(s)
        signal = _solicitud_operativa_signal(
            estado_raw=estado,
            priority_label=priority_label,
            manual_followup_state=manual["state"],
            days_in_state=int(days_in_state(estado_desde, now_dt=now_dt) or 0),
            has_active_reemplazo=has_active_reemplazo,
            last_activity_at=_solicitud_last_activity_at(s),
            now_dt=now_dt,
        )
        signal_code = str(signal.get("code", "") or "").strip().lower()
        if signal_code == "sin_movimiento":
            sin_movimiento += 1
        elif signal_code == "espera_pago_prolongada":
            espera_pago_prolongada += 1
        elif signal_code == "reemplazo_sin_seguimiento":
            reemplazo_sin_seguimiento += 1

        actor_user_id = actor_by_solicitud.get(sid)
        if actor_user_id is not None:
            try:
                actor_user_id = int(actor_user_id)
            except Exception:
                actor_user_id = None
        if actor_user_id is not None and actor_user_id <= 0:
            actor_user_id = None
        if actor_user_id is None:
            sin_responsable += 1

        actor_key = actor_user_id if actor_user_id is not None else None
        if actor_key not in carga_raw:
            carga_raw[actor_key] = {
                "actor_user_id": actor_key,
                "responsable_label": username_by_actor.get(actor_key, f"Staff #{actor_key}") if actor_key is not None else "Sin responsable",
                "total": 0,
                "riesgo": 0,
            }
        carga_raw[actor_key]["total"] += 1
        if (
            manual["state"] in {"vencida", "hoy"}
            or estado in {"espera_pago", "reemplazo"}
            or str(signal.get("code", "") or "").strip().lower() == "sin_movimiento"
        ):
            carga_raw[actor_key]["riesgo"] += 1

    carga_ordenada = sorted(
        (value for key, value in carga_raw.items() if key is not None),
        key=lambda row: (-int(row["total"]), -int(row["riesgo"]), str(row["responsable_label"]).lower()),
    )
    top_responsables = carga_ordenada[:3]
    if None in carga_raw:
        top_responsables.append(carga_raw[None])

    cards = [
        {
            "key": "trabajo_dia",
            "label": "Trabajo del día",
            "value": int(trabajo_del_dia_total or 0),
            "tone": "primary",
            "hint": "Seguimientos y tareas vencidas/hoy para ejecutar ahora.",
            "href": url_for("admin.tareas_hoy"),
            "cta_label": "Ver trabajo",
        },
        {
            "key": "espera_pago",
            "label": "Espera de pago",
            "value": int(espera_pago),
            "tone": "warning",
            "hint": "Solicitudes activas detenidas por pago pendiente.",
            "href": url_for("admin.listar_solicitudes", triage="espera_pago"),
            "cta_label": "Abrir bloque",
        },
        {
            "key": "vencidas",
            "label": "Seguimientos vencidos",
            "value": int(vencidas),
            "tone": "danger",
            "hint": "Solicitudes con fecha manual de seguimiento ya vencida.",
            "href": url_for("admin.listar_solicitudes", triage="vencidas"),
            "cta_label": "Atender vencidas",
        },
        {
            "key": "atencion_hoy",
            "label": "Atención hoy",
            "value": int(atencion_hoy),
            "tone": "info",
            "hint": "Solicitudes con seguimiento manual programado para hoy.",
            "href": url_for("admin.listar_solicitudes", triage="atencion_hoy"),
            "cta_label": "Ver hoy",
        },
        {
            "key": "reemplazos_activos",
            "label": "Reemplazos activos",
            "value": int(reemplazos_activos),
            "tone": "warning",
            "hint": "Solicitudes en reemplazo con proceso abierto.",
            "href": url_for("admin.listar_solicitudes", triage="reemplazo"),
            "cta_label": "Gestionar reemplazos",
        },
        {
            "key": "sin_movimiento",
            "label": "Sin movimiento",
            "value": int(sin_movimiento),
            "tone": "warning",
            "hint": "Solicitudes activas sin avance relevante reciente.",
            "href": url_for("admin.listar_solicitudes", triage="sin_movimiento"),
            "cta_label": "Revisar riesgo",
        },
        {
            "key": "espera_pago_prolongada",
            "label": "Pago prolongado",
            "value": int(espera_pago_prolongada),
            "tone": "danger",
            "hint": "Solicitudes en espera de pago por varios días sin destrabe.",
            "href": url_for("admin.listar_solicitudes", triage="espera_pago_prolongada"),
            "cta_label": "Escalar cobro",
        },
        {
            "key": "reemplazo_sin_seguimiento",
            "label": "Reemplazo sin seguimiento",
            "value": int(reemplazo_sin_seguimiento),
            "tone": "danger",
            "hint": "Reemplazos activos sin fecha manual de seguimiento.",
            "href": url_for("admin.listar_solicitudes", triage="reemplazo_sin_seguimiento"),
            "cta_label": "Asignar seguimiento",
        },
        {
            "key": "sin_responsable",
            "label": "Sin responsable",
            "value": int(sin_responsable),
            "tone": "secondary",
            "hint": "Solicitudes sin último actor operativo asignado.",
            "href": url_for("admin.listar_solicitudes", triage="sin_responsable"),
            "cta_label": "Asignar",
        },
    ]

    return {
        "cards": cards,
        "top_responsables": top_responsables,
        "total_operativo": int(len(rows or [])),
    }


@admin_bp.route('/tareas/hoy')
@login_required
@staff_required
def tareas_hoy():
    """
    Trabajo del día unificado:
    - Seguimientos manuales de solicitud vencidos/hoy.
    - Tareas de cliente vencidas/hoy (no completadas).
    """
    hoy = rd_today()
    task_rows = (
        TareaCliente.query
        .options(joinedload(TareaCliente.cliente))
        .filter(
            TareaCliente.estado != 'completada',
            TareaCliente.fecha_vencimiento.isnot(None),
            TareaCliente.fecha_vencimiento <= hoy,
        )
        .order_by(TareaCliente.fecha_vencimiento.asc(), TareaCliente.fecha_creacion.desc())
        .all()
    )
    followup_rows = (
        Solicitud.query
        .options(joinedload(Solicitud.cliente))
        .filter(
            Solicitud.fecha_seguimiento_manual.isnot(None),
            Solicitud.fecha_seguimiento_manual <= hoy,
            Solicitud.estado.notin_(('pagada', 'cancelada')),
        )
        .order_by(Solicitud.fecha_seguimiento_manual.asc(), Solicitud.id.desc())
        .all()
    )

    items = []
    for s in (followup_rows or []):
        fecha_obj = getattr(s, "fecha_seguimiento_manual", None)
        is_overdue = isinstance(fecha_obj, date) and fecha_obj < hoy
        items.append({
            "kind": "seguimiento_solicitud",
            "id": int(getattr(s, "id", 0) or 0),
            "fecha": fecha_obj,
            "is_overdue": bool(is_overdue),
            "cliente": getattr(s, "cliente", None),
            "solicitud": s,
            "titulo": f"Seguimiento solicitud {(getattr(s, 'codigo_solicitud', None) or f'SOL-{s.id}')}",
            "estado": str(getattr(s, "estado", "") or "").strip().lower(),
            "prioridad": "alta" if is_overdue else "media",
        })

    for t in (task_rows or []):
        fecha_obj = getattr(t, "fecha_vencimiento", None)
        is_overdue = isinstance(fecha_obj, date) and fecha_obj < hoy
        items.append({
            "kind": "tarea_cliente",
            "id": int(getattr(t, "id", 0) or 0),
            "fecha": fecha_obj,
            "is_overdue": bool(is_overdue),
            "cliente": getattr(t, "cliente", None),
            "tarea": t,
            "titulo": str(getattr(t, "titulo", "") or "").strip() or "Tarea",
            "estado": str(getattr(t, "estado", "") or "").strip().lower(),
            "prioridad": str(getattr(t, "prioridad", "") or "").strip().lower() or ("alta" if is_overdue else "media"),
        })

    items.sort(
        key=lambda item: (
            0 if bool(item.get("is_overdue")) else 1,
            item.get("fecha") or hoy,
            0 if str(item.get("kind") or "") == "seguimiento_solicitud" else 1,
            -int(item.get("id") or 0),
        )
    )
    resumen = {
        "total": len(items),
        "vencidas": sum(1 for i in items if bool(i.get("is_overdue"))),
        "hoy": sum(1 for i in items if (not bool(i.get("is_overdue")) and i.get("fecha") == hoy)),
        "seguimientos": sum(1 for i in items if i.get("kind") == "seguimiento_solicitud"),
        "tareas": sum(1 for i in items if i.get("kind") == "tarea_cliente"),
    }
    kpis_operativos = _tareas_hoy_operational_kpis(
        today_rd=hoy,
        trabajo_del_dia_total=int(resumen.get("total") or 0),
    )

    return render_template(
        'admin/tareas_hoy.html',
        tareas=task_rows,
        items=items,
        resumen=resumen,
        kpis_operativos=kpis_operativos,
        hoy=hoy,
    )


@admin_bp.route('/tareas/<int:id>/completar', methods=['POST'])
@login_required
@staff_required
@admin_action_limit(bucket="tareas", max_actions=80, window_sec=60)
def completar_tarea_cliente(id):
    tarea = TareaCliente.query.get_or_404(id)
    next_url = request.form.get("next") or request.referrer or url_for("admin.tareas_hoy")

    if str(getattr(tarea, "estado", "") or "").strip().lower() == "completada":
        flash("La tarea ya estaba completada.", "info")
        return redirect(next_url)

    try:
        tarea.estado = "completada"
        tarea.completada_at = utc_now_naive()
        db.session.commit()
        flash("Tarea marcada como hecha.", "success")
    except Exception:
        db.session.rollback()
        flash("No se pudo completar la tarea.", "danger")
    return redirect(next_url)


@admin_bp.route('/tareas/<int:id>/reprogramar', methods=['POST'])
@login_required
@staff_required
@admin_action_limit(bucket="tareas", max_actions=80, window_sec=60)
def reprogramar_tarea_cliente(id):
    tarea = TareaCliente.query.get_or_404(id)
    next_url = request.form.get("next") or request.referrer or url_for("admin.tareas_hoy")
    raw_date = (request.form.get("fecha_vencimiento") or "").strip()

    if not raw_date:
        flash("Indica una fecha para reprogramar.", "warning")
        return redirect(next_url)

    try:
        parsed_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
    except Exception:
        flash("Fecha inválida para reprogramar la tarea.", "warning")
        return redirect(next_url)

    try:
        tarea.fecha_vencimiento = parsed_date
        if str(getattr(tarea, "estado", "") or "").strip().lower() == "completada":
            tarea.estado = "pendiente"
            tarea.completada_at = None
        db.session.commit()
        flash("Tarea reprogramada.", "success")
    except Exception:
        db.session.rollback()
        flash("No se pudo reprogramar la tarea.", "danger")
    return redirect(next_url)

@admin_bp.route('/clientes/<int:cliente_id>/tareas/rapida', methods=['POST'])
@login_required
@staff_required
@admin_action_limit(bucket="tareas", max_actions=60, window_sec=60)
def crear_tarea_rapida(cliente_id):
    """
    Crea una tarea rápida para hoy asociada al cliente.
    No pide formulario, simplemente genera:
      - Título: "Dar seguimiento a <nombre>"
      - fecha_vencimiento: hoy
      - estado: pendiente
    """
    cliente = Cliente.query.get_or_404(cliente_id)

    titulo = (request.form.get('titulo') or '').strip()
    if not titulo:
        titulo = f"Dar seguimiento a {cliente.nombre_completo}"

    hoy = rd_today()

    try:
        tarea = TareaCliente(
            cliente_id=cliente.id,
            titulo=titulo,
            fecha_creacion=utc_now_naive(),
            fecha_vencimiento=hoy,
            estado='pendiente',
            prioridad='media'
        )
        db.session.add(tarea)
        db.session.commit()
        flash('Tarea rápida creada para hoy.', 'success')
    except Exception:
        db.session.rollback()
        flash('No se pudo crear la tarea rápida.', 'danger')

    return redirect(url_for('admin.detalle_cliente', cliente_id=cliente.id))



# ─────────────────────────────────────────────────────────────
# HELPERS: Detalles por tipo de servicio (JSONB)
# ─────────────────────────────────────────────────────────────

def _build_detalles_servicio_from_form(form) -> dict | None:
    """
    Construye el JSON que se guarda en Solicitud.detalles_servicio
    según el tipo de servicio seleccionado.
    """
    ts = getattr(form, 'tipo_servicio', None).data if hasattr(form, 'tipo_servicio') else None
    if not ts:
        return None

    detalles: dict = {
        "tipo": ts  # siempre guardamos el tipo aquí
    }

    # ─────────────────────────────
    # NIÑERA
    # ─────────────────────────────
    if ts == 'NINERA':
        cant_ninos = form.ninera_cant_ninos.data if hasattr(form, 'ninera_cant_ninos') else None
        edades = (form.ninera_edades.data or '').strip() if hasattr(form, 'ninera_edades') else ''
        tareas = _clean_list(form.ninera_tareas.data) if hasattr(form, 'ninera_tareas') else []
        tareas_otro = (form.ninera_tareas_otro.data or '').strip() if hasattr(form, 'ninera_tareas_otro') else ''
        condicion = (form.ninera_condicion_especial.data or '').strip() if hasattr(form, 'ninera_condicion_especial') else ''
        usa_otro = ('otro' in tareas)
        tareas = [t for t in tareas if t != 'otro']
        if not usa_otro:
            tareas_otro = ''

        detalles.update({
            "cantidad_ninos": cant_ninos,
            "edades_ninos": edades or None,
            "tareas": _clean_list(tareas or []),
            # Clave específica para evitar cruces con ENFERMERA.
            "ninera_tareas_otro": tareas_otro or None,
            "condicion_especial": condicion or None,
        })

    # ─────────────────────────────
    # ENFERMERA / CUIDADORA
    # ─────────────────────────────
    elif ts == 'ENFERMERA':
        a_quien = (form.enf_a_quien_cuida.data or '').strip() if hasattr(form, 'enf_a_quien_cuida') else ''
        condicion = (form.enf_condicion_principal.data or '').strip() if hasattr(form, 'enf_condicion_principal') else ''
        movilidad = form.enf_movilidad.data if hasattr(form, 'enf_movilidad') else ''
        tareas = _clean_list(form.enf_tareas.data) if hasattr(form, 'enf_tareas') else []
        tareas_otro = (form.enf_tareas_otro.data or '').strip() if hasattr(form, 'enf_tareas_otro') else ''
        usa_otro = ('otro' in tareas)
        tareas = [t for t in tareas if t != 'otro']
        if not usa_otro:
            tareas_otro = ''

        detalles.update({
            "a_quien_cuida": a_quien or None,
            "condicion_principal": condicion or None,
            "movilidad": movilidad or None,
            "tareas": _clean_list(tareas or []),
            # Clave específica para evitar cruces con NIÑERA.
            "enf_tareas_otro": tareas_otro or None,
        })

    # ─────────────────────────────
    # CHOFER
    # ─────────────────────────────
    elif ts == 'CHOFER':
        vehiculo = form.chofer_vehiculo.data if hasattr(form, 'chofer_vehiculo') else ''
        tipo_vehiculo = form.chofer_tipo_vehiculo.data if hasattr(form, 'chofer_tipo_vehiculo') else ''
        tipo_vehiculo_otro = (form.chofer_tipo_vehiculo_otro.data or '').strip() if hasattr(form, 'chofer_tipo_vehiculo_otro') else ''
        if tipo_vehiculo != 'otro':
            tipo_vehiculo_otro = ''
        rutas = (form.chofer_rutas.data or '').strip() if hasattr(form, 'chofer_rutas') else ''
        viajes_largos = bool(form.chofer_viajes_largos.data) if hasattr(form, 'chofer_viajes_largos') else None
        licencia = (form.chofer_licencia_detalle.data or '').strip() if hasattr(form, 'chofer_licencia_detalle') else ''

        detalles.update({
            "vehiculo": vehiculo or None,
            "tipo_vehiculo": tipo_vehiculo or None,
            "tipo_vehiculo_otro": tipo_vehiculo_otro or None,
            "rutas": rutas or None,
            "viajes_largos": viajes_largos,
            "licencia_requisitos": licencia or None,
        })

    # ─────────────────────────────
    # DOMÉSTICA DE LIMPIEZA
    # ─────────────────────────────
    elif ts == 'DOMESTICA_LIMPIEZA':
        # No metemos más cosas aquí porque ya usamos columnas normales (funciones, áreas, etc.)
        pass

    # Limpiar claves vacías
    clean = {
        k: v for k, v in detalles.items()
        if v not in (None, '', [], {})
    }
    return clean or None


def _populate_form_detalles_from_solicitud(form, solicitud: Solicitud) -> None:
    """
    Cuando se edita una solicitud, toma solicitud.detalles_servicio (JSON)
    y rellena los campos específicos correspondientes en el form.
    """
    try:
        if not hasattr(solicitud, 'detalles_servicio') or not solicitud.detalles_servicio:
            return

        data = solicitud.detalles_servicio or {}
        ts = data.get("tipo") or getattr(solicitud, 'tipo_servicio', None)

        # Aseguramos que el select tenga el tipo
        if hasattr(form, 'tipo_servicio') and not form.tipo_servicio.data:
            form.tipo_servicio.data = ts

        # ─────────────────────────────
        # NIÑERA
        # ─────────────────────────────
        if ts == 'NINERA':
            if hasattr(form, 'ninera_cant_ninos'):
                form.ninera_cant_ninos.data = data.get("cantidad_ninos")
            if hasattr(form, 'ninera_edades'):
                form.ninera_edades.data = data.get("edades_ninos") or ''
            if hasattr(form, 'ninera_tareas'):
                form.ninera_tareas.data = data.get("tareas") or []
            if hasattr(form, 'ninera_tareas_otro'):
                # Compat retroactiva: lee clave nueva y fallback legado.
                form.ninera_tareas_otro.data = (
                    data.get("ninera_tareas_otro")
                    or data.get("tareas_otro")
                    or ''
                )
            try:
                if hasattr(form, 'ninera_tareas') and hasattr(form, 'ninera_tareas_otro'):
                    if (form.ninera_tareas_otro.data or '').strip():
                        allowed = _allowed_codes_from_choices(form.ninera_tareas.choices)
                        if 'otro' in allowed:
                            vals = set(_clean_list(form.ninera_tareas.data))
                            vals.add('otro')
                            form.ninera_tareas.data = list(vals)
            except Exception:
                pass
            if hasattr(form, 'ninera_condicion_especial'):
                form.ninera_condicion_especial.data = data.get("condicion_especial") or ''

        # ─────────────────────────────
        # ENFERMERA / CUIDADORA
        # ─────────────────────────────
        elif ts == 'ENFERMERA':
            if hasattr(form, 'enf_a_quien_cuida'):
                form.enf_a_quien_cuida.data = data.get("a_quien_cuida") or ''
            if hasattr(form, 'enf_condicion_principal'):
                form.enf_condicion_principal.data = data.get("condicion_principal") or ''
            if hasattr(form, 'enf_movilidad'):
                form.enf_movilidad.data = data.get("movilidad") or ''
            if hasattr(form, 'enf_tareas'):
                form.enf_tareas.data = data.get("tareas") or []
            if hasattr(form, 'enf_tareas_otro'):
                # Compat retroactiva: lee clave nueva y fallback legado.
                form.enf_tareas_otro.data = (
                    data.get("enf_tareas_otro")
                    or data.get("tareas_otro")
                    or ''
                )
            try:
                if hasattr(form, 'enf_tareas') and hasattr(form, 'enf_tareas_otro'):
                    if (form.enf_tareas_otro.data or '').strip():
                        allowed = _allowed_codes_from_choices(form.enf_tareas.choices)
                        if 'otro' in allowed:
                            vals = set(_clean_list(form.enf_tareas.data))
                            vals.add('otro')
                            form.enf_tareas.data = list(vals)
            except Exception:
                pass

        # ─────────────────────────────
        # CHOFER
        # ─────────────────────────────
        elif ts == 'CHOFER':
            if hasattr(form, 'chofer_vehiculo'):
                form.chofer_vehiculo.data = data.get("vehiculo") or None
            if hasattr(form, 'chofer_tipo_vehiculo'):
                form.chofer_tipo_vehiculo.data = data.get("tipo_vehiculo") or ''
            if hasattr(form, 'chofer_tipo_vehiculo_otro'):
                form.chofer_tipo_vehiculo_otro.data = data.get("tipo_vehiculo_otro") or ''
            try:
                if hasattr(form, 'chofer_tipo_vehiculo') and hasattr(form, 'chofer_tipo_vehiculo_otro'):
                    if (form.chofer_tipo_vehiculo_otro.data or '').strip():
                        allowed = _allowed_codes_from_choices(form.chofer_tipo_vehiculo.choices)
                        if 'otro' in allowed:
                            form.chofer_tipo_vehiculo.data = 'otro'
            except Exception:
                pass
            if hasattr(form, 'chofer_rutas'):
                form.chofer_rutas.data = data.get("rutas") or ''
            if hasattr(form, 'chofer_viajes_largos'):
                form.chofer_viajes_largos.data = bool(data.get("viajes_largos")) if "viajes_largos" in data else None
            if hasattr(form, 'chofer_licencia_detalle'):
                form.chofer_licencia_detalle.data = data.get("licencia_requisitos") or ''

        # DOMESTICA_LIMPIEZA no tiene extras en JSON

    except Exception:
        # Si algo falla, no explotamos el render
        return


# ─────────────────────────────────────────────────────────────
# ADMIN: Nueva solicitud
# ─────────────────────────────────────────────────────────────
@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/nueva', methods=['GET', 'POST'])
@login_required
@staff_required
@admin_action_limit(bucket="create_solicitud", max_actions=25, window_sec=60)
def nueva_solicitud_admin(cliente_id):
    c = Cliente.query.get_or_404(cliente_id)
    form = AdminSolicitudForm()
    public_pasaje_mode = "aparte" if bool(getattr(form, "pasaje_aporte", type("x", (object,), {"data": False})).data) else "incluido"
    public_pasaje_otro = ""
    public_modalidad_group = ""
    public_modalidad_specific = ""
    public_modalidad_other = ""

    # Mantener en sync con constantes
    form.areas_comunes.choices = AREAS_COMUNES_CHOICES

    if request.method == 'GET':
        # Valores iniciales
        if hasattr(form, 'tipo_servicio'):
            form.tipo_servicio.data = 'DOMESTICA_LIMPIEZA'

        if hasattr(form, 'funciones'):        form.funciones.data = []
        if hasattr(form, 'funciones_otro'):   form.funciones_otro.data = ''
        if hasattr(form, 'areas_comunes'):    form.areas_comunes.data = []
        if hasattr(form, 'area_otro'):        form.area_otro.data = ''
        if hasattr(form, 'edad_requerida'):   form.edad_requerida.data = []
        if hasattr(form, 'edad_otro'):        form.edad_otro.data = ''
        if hasattr(form, 'tipo_lugar_otro'):  form.tipo_lugar_otro.data = ''
        if hasattr(form, 'mascota'):          form.mascota.data = ''

        # Limpia bloques específicos
        if hasattr(form, 'ninera_cant_ninos'):
            form.ninera_cant_ninos.data = None
            form.ninera_edades.data = ''
            form.ninera_tareas.data = []
            form.ninera_tareas_otro.data = ''
            form.ninera_condicion_especial.data = ''

        if hasattr(form, 'enf_a_quien_cuida'):
            form.enf_a_quien_cuida.data = ''
            form.enf_movilidad.data = ''
            form.enf_condicion_principal.data = ''
            form.enf_tareas.data = []
            form.enf_tareas_otro.data = ''

        if hasattr(form, 'chofer_vehiculo'):
            form.chofer_vehiculo.data = None
            form.chofer_tipo_vehiculo.data = ''
            form.chofer_tipo_vehiculo_otro.data = ''
            form.chofer_rutas.data = ''
            form.chofer_viajes_largos.data = None
            form.chofer_licencia_detalle.data = ''

    if request.method == "POST":
        public_pasaje_mode, public_pasaje_otro = normalize_pasaje_mode_text(
            request.form.get("pasaje_mode"),
            request.form.get("pasaje_otro_text"),
            default_mode=public_pasaje_mode,
        )
        if hasattr(form, "pasaje_aporte"):
            form.pasaje_aporte.data = (public_pasaje_mode == "aparte")
        (
            public_modalidad_group,
            public_modalidad_specific,
            public_modalidad_other,
        ) = _resolve_modalidad_ui_context_from_request(form, prefer_post=True)
    else:
        (
            public_modalidad_group,
            public_modalidad_specific,
            public_modalidad_other,
        ) = _resolve_modalidad_ui_context_from_request(form, prefer_post=False)

    # POST válido
    if form.validate_on_submit():
        state = {"solicitud_id": 0, "tipo_servicio": None}
        try:
            nuevo_codigo = _next_codigo_solicitud(c)
            base_total = int(c.total_solicitudes or 0)

            def _persist_solicitud_create(_attempt: int):
                s = Solicitud(
                    cliente_id=c.id,
                    fecha_solicitud=utc_now_naive(),
                    codigo_solicitud=nuevo_codigo,
                )
                form.populate_obj(s)
                _apply_banos_from_request(s, form)
                _normalize_modalidad_on_solicitud(s)

                if hasattr(form, 'sueldo'):
                    try:
                        s.sueldo = _norm_numeric_str(form.sueldo.data)
                    except Exception:
                        pass
                if hasattr(form, 'tipo_servicio'):
                    s.tipo_servicio = (form.tipo_servicio.data or '').strip() or None
                tipo_lugar_value = getattr(s, 'tipo_lugar', '')
                tipo_lugar_otro_txt = (getattr(form, 'tipo_lugar_otro', None).data or '').strip() if hasattr(form, 'tipo_lugar_otro') else ''
                if tipo_lugar_otro_txt and str(tipo_lugar_value or '').strip() != 'otro':
                    tipo_lugar_value = 'otro'
                s.tipo_lugar = _map_tipo_lugar(
                    tipo_lugar_value,
                    tipo_lugar_otro_txt
                )
                edad_codes_selected = (form.edad_requerida.data if hasattr(form, 'edad_requerida') else [])
                edad_otro_txt = (form.edad_otro.data if hasattr(form, 'edad_otro') else '')
                edad_codes_clean = _clean_list(edad_codes_selected)
                if (edad_otro_txt or '').strip() and 'otro' not in edad_codes_clean:
                    edad_codes_clean.append('otro')
                s.edad_requerida = _map_edad_choices(
                    codes_selected=edad_codes_clean,
                    edad_choices=(form.edad_requerida.choices if hasattr(form, 'edad_requerida') else []),
                    otro_text=edad_otro_txt
                )
                if hasattr(form, 'mascota'):
                    s.mascota = (form.mascota.data or '').strip() or None

                selected_codes = _clean_list(form.funciones.data) if hasattr(form, 'funciones') else []
                extra_text = (form.funciones_otro.data or '').strip() if hasattr(form, 'funciones_otro') else ''
                if extra_text and 'otro' not in selected_codes:
                    selected_codes.append('otro')
                if 'otro' not in selected_codes:
                    extra_text = ''
                if hasattr(form, 'funciones') and hasattr(form.funciones, 'choices'):
                    valid_codes = _allowed_codes_from_choices(form.funciones.choices)
                    s.funciones = [code for code in selected_codes if code in valid_codes and code != 'otro']
                else:
                    s.funciones = [code for code in selected_codes if code != 'otro']
                if hasattr(s, 'funciones_otro'):
                    s.funciones_otro = extra_text or None

                selected_areas = []
                areas_has_otro = False
                if hasattr(form, 'areas_comunes'):
                    areas_selected_raw = _clean_list(
                        getattr(form, 'areas_comunes', type('x', (object,), {'data': []})).data
                    )
                    area_otro_txt = (form.area_otro.data or '').strip() if hasattr(form, 'area_otro') else ''
                    areas_has_otro = ('otro' in areas_selected_raw) or bool(area_otro_txt)
                    selected_areas = _normalize_areas_comunes_selected(
                        selected_vals=areas_selected_raw,
                        choices=form.areas_comunes.choices
                    )
                s.areas_comunes = selected_areas
                if hasattr(s, 'area_otro') and hasattr(form, 'area_otro'):
                    area_otro_txt = (form.area_otro.data or '').strip()
                    s.area_otro = (area_otro_txt if areas_has_otro else '') or None
                _clear_house_structure_if_not_limpieza(s, s.funciones)
                s.detalles_servicio = _build_detalles_servicio_from_form(form)
                if hasattr(s, 'nota_cliente'):
                    s.nota_cliente = strip_pasaje_marker_from_note(getattr(s, 'nota_cliente', ''))
                apply_pasaje_to_solicitud(
                    s,
                    mode_raw=public_pasaje_mode,
                    text_raw=public_pasaje_otro,
                    default_mode="aparte" if bool(getattr(s, "pasaje_aporte", False)) else "incluido",
                )

                db.session.add(s)
                db.session.flush()
                state["solicitud_id"] = int(getattr(s, "id", 0) or 0)
                state["tipo_servicio"] = s.tipo_servicio
                _emit_cliente_live_solicitud_events(
                    event_type="CLIENTE_SOLICITUD_CREATED",
                    solicitud=s,
                    cliente_id=int(c.id),
                    include_dashboard=True,
                )

                c.total_solicitudes = base_total + 1
                c.fecha_ultima_solicitud = utc_now_naive()
                c.fecha_ultima_actividad = utc_now_naive()

            result = _execute_form_save(
                persist_fn=_persist_solicitud_create,
                verify_fn=lambda: _verify_solicitud_saved(
                    int(state.get("solicitud_id") or 0),
                    expected_cliente_id=c.id,
                    expected_codigo=nuevo_codigo,
                ),
                entity_type="Solicitud",
                entity_id=state.get("solicitud_id"),
                summary=f"Guardar nueva solicitud cliente={c.id}",
                metadata={"cliente_id": c.id, "codigo_solicitud": nuevo_codigo},
            )

            if result.ok:
                try:
                    SolicitudRecommendationService().request_generation(
                        int(state.get("solicitud_id") or 0),
                        trigger_source="admin_create",
                        requested_by=str(_staff_actor_name() or "staff"),
                        synchronous=False,
                        best_effort=True,
                        commit=True,
                        dispatch_async=True,
                    )
                except Exception:
                    current_app.logger.exception(
                        "solicitud_recommendation.trigger_failed solicitud_id=%s source=admin_create",
                        int(state.get("solicitud_id") or 0),
                    )
                _audit_log(
                    action_type="SOLICITUD_CREATE",
                    entity_type="Solicitud",
                    entity_id=state.get("solicitud_id"),
                    summary=f"Solicitud creada: {nuevo_codigo}",
                    metadata={"cliente_id": c.id, "tipo_servicio": state.get("tipo_servicio")},
                )
                flash(f'Solicitud {nuevo_codigo} creada correctamente.', 'success')
                return redirect(
                    _cliente_detail_solicitudes_redirect_url(
                        cliente_id=cliente_id,
                        solicitud_id=int(state.get("solicitud_id") or 0),
                    )
                )

            log_error_event(
                error_type="SAVE_ERROR",
                exc=f"Error robusto al crear solicitud: {result.error_message}",
                route=request.path,
                entity_type="solicitud",
                entity_id=state.get("solicitud_id"),
                request_id=request.headers.get("X-Request-ID"),
                status_code=500,
            )
            flash('No se pudo guardar correctamente. Intente nuevamente.', 'danger')
        except Exception:
            db.session.rollback()
            log_error_event(
                error_type="SAVE_ERROR",
                exc="Error inesperado al crear solicitud",
                route=request.path,
                entity_type="solicitud",
                request_id=request.headers.get("X-Request-ID"),
                status_code=500,
            )
            flash('No se pudo guardar correctamente. Intente nuevamente.', 'danger')

    elif request.method == 'POST':
        flash('Revisa los campos marcados en rojo.', 'danger')

    return render_template(
        'admin/solicitud_form.html',
        form=form,
        cliente_id=cliente_id,
        nuevo=True,
        public_pasaje_mode=public_pasaje_mode,
        public_pasaje_otro=public_pasaje_otro,
        public_modalidad_group=public_modalidad_group,
        public_modalidad_specific=public_modalidad_specific,
        public_modalidad_other=public_modalidad_other,
    )


# ─────────────────────────────────────────────────────────────
# ADMIN: Editar solicitud
# ─────────────────────────────────────────────────────────────
@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/<int:id>/editar', methods=['GET', 'POST'])
@login_required
@staff_required
@admin_action_limit(bucket="edit_solicitud", max_actions=35, window_sec=60)
def editar_solicitud_admin(cliente_id, id):
    s = Solicitud.query.filter_by(id=id, cliente_id=cliente_id).first_or_404()
    form = AdminSolicitudForm(obj=s)
    wants_async = _admin_async_wants_json()
    public_pasaje_mode = "aparte" if bool(getattr(s, "pasaje_aporte", False)) else "incluido"
    public_pasaje_otro = ""
    public_modalidad_group = ""
    public_modalidad_specific = ""
    public_modalidad_other = ""
    next_url = request.args.get("next") or request.form.get("next") or request.referrer or ""
    fallback = url_for('admin.detalle_cliente', cliente_id=cliente_id)
    safe_next = next_url if _is_safe_redirect_url(next_url) else fallback
    success_redirect_url = _cliente_detail_solicitudes_redirect_url(cliente_id=cliente_id)

    # Mantener en sync con constantes
    form.areas_comunes.choices = AREAS_COMUNES_CHOICES

    # ─────────────────────────────────────────
    # GET: pre-cargar campos
    # ─────────────────────────────────────────
    if request.method == 'GET':
        # Tipo de servicio
        if hasattr(form, 'tipo_servicio'):
            valid_ts = {code for code, _ in form.tipo_servicio.choices}
            if not s.tipo_servicio:
                if 'DOMESTICA_LIMPIEZA' in valid_ts:
                    form.tipo_servicio.data = 'DOMESTICA_LIMPIEZA'
            else:
                if s.tipo_servicio in valid_ts:
                    form.tipo_servicio.data = s.tipo_servicio

        # Tipo de lugar
        try:
            if hasattr(form, 'tipo_lugar') and hasattr(form, 'tipo_lugar_otro'):
                allowed_tl = _allowed_codes_from_choices(form.tipo_lugar.choices)
                if s.tipo_lugar and s.tipo_lugar in allowed_tl:
                    form.tipo_lugar.data = s.tipo_lugar
                    form.tipo_lugar_otro.data = ''
                else:
                    form.tipo_lugar.data = 'otro'
                    form.tipo_lugar_otro.data = (s.tipo_lugar or '').strip()
        except Exception:
            pass

        # Edad requerida
        if hasattr(form, 'edad_requerida'):
            selected_codes, otro_text = _split_edad_for_form(
                stored_list=s.edad_requerida,
                edad_choices=form.edad_requerida.choices
            )
            try:
                edad_codes = set(selected_codes or [])
                if (otro_text or '').strip():
                    allowed_edad = _allowed_codes_from_choices(form.edad_requerida.choices)
                    if 'otro' in allowed_edad:
                        edad_codes.add('otro')
                form.edad_requerida.data = list(edad_codes)
            except Exception:
                form.edad_requerida.data = selected_codes or []
            if hasattr(form, 'edad_otro'):
                form.edad_otro.data = (otro_text or '').strip()

        # Funciones
        if hasattr(form, 'funciones'):
            allowed_fun_codes = _allowed_codes_from_choices(form.funciones.choices)
            funs_guardadas = _clean_list(s.funciones)
            form.funciones.data = [f for f in funs_guardadas if f in allowed_fun_codes]

            extras = [f for f in funs_guardadas if f not in allowed_fun_codes and f != 'otro']

            base_otro = (getattr(s, 'funciones_otro', '') or '').strip()
            if hasattr(form, 'funciones_otro'):
                form.funciones_otro.data = (", ".join(extras) if extras else base_otro)

            try:
                if (form.funciones_otro.data or '').strip():
                    fun_codes = set(form.funciones.data or [])
                    if 'otro' in allowed_fun_codes:
                        fun_codes.add('otro')
                    form.funciones.data = list(fun_codes)
            except Exception:
                pass

        # Mascota / Áreas / Pasaje
        if hasattr(form, 'mascota'):
            form.mascota.data = (getattr(s, 'mascota', '') or '')
        if hasattr(form, 'areas_comunes'):
            form.areas_comunes.data = _clean_list(s.areas_comunes)
        if hasattr(form, 'area_otro'):
            form.area_otro.data = (getattr(s, 'area_otro', '') or '')
        try:
            if hasattr(form, 'areas_comunes') and hasattr(form, 'area_otro'):
                if (form.area_otro.data or '').strip():
                    allowed_areas = _allowed_codes_from_choices(form.areas_comunes.choices)
                    if 'otro' in allowed_areas:
                        area_codes = set(_clean_list(form.areas_comunes.data))
                        area_codes.add('otro')
                        form.areas_comunes.data = list(area_codes)
        except Exception:
            pass
        if hasattr(form, 'pasaje_aporte'):
            form.pasaje_aporte.data = bool(getattr(s, 'pasaje_aporte', False))
        public_pasaje_mode, public_pasaje_otro = read_pasaje_mode_text(
            pasaje_aporte=getattr(s, "pasaje_aporte", False),
            detalles_servicio=getattr(s, "detalles_servicio", None),
            nota_cliente=getattr(s, "nota_cliente", ""),
        )

        # Detalles específicos (JSONB)
        _populate_form_detalles_from_solicitud(form, s)
        (
            public_modalidad_group,
            public_modalidad_specific,
            public_modalidad_other,
        ) = _resolve_modalidad_ui_context_from_request(form, prefer_post=False)

    if request.method == "POST":
        public_pasaje_mode, public_pasaje_otro = normalize_pasaje_mode_text(
            request.form.get("pasaje_mode"),
            request.form.get("pasaje_otro_text"),
            default_mode=public_pasaje_mode,
        )
        if hasattr(form, "pasaje_aporte"):
            form.pasaje_aporte.data = (public_pasaje_mode == "aparte")
        (
            public_modalidad_group,
            public_modalidad_specific,
            public_modalidad_other,
        ) = _resolve_modalidad_ui_context_from_request(form, prefer_post=True)

    def _flatten_form_errors() -> list[str]:
        out = []
        try:
            for field_errors in (form.errors or {}).values():
                for msg in (field_errors or []):
                    text = str(msg or "").strip()
                    if text:
                        out.append(text)
        except Exception:
            return []
        return out

    def _render_edit_region(async_feedback=None) -> str:
        return render_template(
            'admin/_editar_solicitud_form_region.html',
            form=form,
            cliente_id=cliente_id,
            solicitud=s,
            nuevo=False,
            public_pasaje_mode=public_pasaje_mode,
            public_pasaje_otro=public_pasaje_otro,
            public_modalidad_group=public_modalidad_group,
            public_modalidad_specific=public_modalidad_specific,
            public_modalidad_other=public_modalidad_other,
            next_url=safe_next,
            async_feedback=async_feedback,
        )

    def _async_edit_response(
        *,
        ok: bool,
        message: str,
        category: str,
        redirect_url: str | None = None,
        http_status: int = 200,
        error_code: str | None = None,
        include_region: bool = False,
        include_update_target: bool = True,
        async_feedback=None,
    ):
        payload = _admin_async_payload(
            success=bool(ok),
            message=message,
            category=category,
            redirect_url=redirect_url if redirect_url is not None else safe_next,
            replace_html=_render_edit_region(async_feedback=async_feedback) if include_region else None,
            update_target="#editarSolicitudAsyncRegion" if include_update_target else None,
            errors=_flatten_form_errors(),
            error_code=error_code,
        )
        return jsonify(payload), http_status

    # ─────────────────────────────────────────
    # POST válido
    # ─────────────────────────────────────────
    if form.validate_on_submit():
        try:
            prev_modalidad = (getattr(s, "modalidad_trabajo", "") or "").strip()
            def _persist_solicitud_update(_attempt: int):
                form.populate_obj(s)
                _apply_banos_from_request(s, form)
                _normalize_modalidad_on_solicitud(s)
                submitted_modalidad = (
                    (getattr(form, "modalidad_trabajo", None).data or "").strip()
                    if hasattr(form, "modalidad_trabajo")
                    else ""
                )
                if hasattr(s, "modalidad_trabajo") and should_preserve_existing_modalidad_on_edit(
                    existing_value=prev_modalidad,
                    submitted_value=submitted_modalidad,
                    submitted_group=request.form.get("modalidad_grupo"),
                    submitted_specific=request.form.get("modalidad_especifica"),
                    submitted_other=request.form.get("modalidad_otro_text"),
                ):
                    s.modalidad_trabajo = prev_modalidad or None

                if hasattr(form, 'sueldo'):
                    try:
                        s.sueldo = _norm_numeric_str(form.sueldo.data)
                    except Exception:
                        pass
                if hasattr(form, 'tipo_servicio'):
                    s.tipo_servicio = (form.tipo_servicio.data or '').strip() or None
                tipo_lugar_value = getattr(s, 'tipo_lugar', '')
                tipo_lugar_otro_txt = (getattr(form, 'tipo_lugar_otro', None).data or '').strip() if hasattr(form, 'tipo_lugar_otro') else ''
                if tipo_lugar_otro_txt and str(tipo_lugar_value or '').strip() != 'otro':
                    tipo_lugar_value = 'otro'
                s.tipo_lugar = _map_tipo_lugar(
                    tipo_lugar_value,
                    tipo_lugar_otro_txt
                )
                edad_codes_selected = (form.edad_requerida.data if hasattr(form, 'edad_requerida') else [])
                edad_otro_txt = (form.edad_otro.data if hasattr(form, 'edad_otro') else '')
                edad_codes_clean = _clean_list(edad_codes_selected)
                if (edad_otro_txt or '').strip() and 'otro' not in edad_codes_clean:
                    edad_codes_clean.append('otro')
                s.edad_requerida = _map_edad_choices(
                    codes_selected=edad_codes_clean,
                    edad_choices=(form.edad_requerida.choices if hasattr(form, 'edad_requerida') else []),
                    otro_text=edad_otro_txt
                )
                if hasattr(form, 'mascota'):
                    s.mascota = (form.mascota.data or '').strip() or None

                selected_codes = _clean_list(form.funciones.data) if hasattr(form, 'funciones') else []
                extra_text = (form.funciones_otro.data or '').strip() if hasattr(form, 'funciones_otro') else ''
                if extra_text and 'otro' not in selected_codes:
                    selected_codes.append('otro')
                if 'otro' not in selected_codes:
                    extra_text = ''
                if hasattr(form, 'funciones') and hasattr(form.funciones, 'choices'):
                    valid_codes = _allowed_codes_from_choices(form.funciones.choices)
                    s.funciones = [code for code in selected_codes if code in valid_codes and code != 'otro']
                else:
                    s.funciones = [code for code in selected_codes if code != 'otro']
                if hasattr(s, 'funciones_otro'):
                    s.funciones_otro = extra_text or None

                areas_has_otro = False
                if hasattr(form, 'areas_comunes'):
                    areas_selected_raw = _clean_list(form.areas_comunes.data)
                    area_otro_txt = (form.area_otro.data or '').strip() if hasattr(form, 'area_otro') else ''
                    areas_has_otro = ('otro' in areas_selected_raw) or bool(area_otro_txt)
                    s.areas_comunes = _normalize_areas_comunes_selected(
                        selected_vals=areas_selected_raw,
                        choices=form.areas_comunes.choices
                    )
                if hasattr(s, 'area_otro') and hasattr(form, 'area_otro'):
                    area_otro_txt = (form.area_otro.data or '').strip()
                    s.area_otro = (area_otro_txt if areas_has_otro else '') or None
                _clear_house_structure_if_not_limpieza(s, s.funciones)

                s.fecha_ultima_modificacion = utc_now_naive()
                s.detalles_servicio = _build_detalles_servicio_from_form(form)
                if hasattr(s, 'nota_cliente'):
                    s.nota_cliente = strip_pasaje_marker_from_note(getattr(s, 'nota_cliente', ''))
                apply_pasaje_to_solicitud(
                    s,
                    mode_raw=public_pasaje_mode,
                    text_raw=public_pasaje_otro,
                    default_mode="aparte" if bool(getattr(s, "pasaje_aporte", False)) else "incluido",
                )
                db.session.flush()
                _emit_cliente_live_solicitud_events(
                    event_type="CLIENTE_SOLICITUD_UPDATED",
                    solicitud=s,
                    cliente_id=int(cliente_id),
                    include_dashboard=True,
                )

            result = _execute_form_save(
                persist_fn=_persist_solicitud_update,
                verify_fn=lambda: _verify_solicitud_saved(
                    int(s.id),
                    expected_cliente_id=cliente_id,
                    expected_codigo=str(getattr(s, "codigo_solicitud", "") or ""),
                ),
                entity_type="Solicitud",
                entity_id=s.id,
                summary=f"Editar solicitud {s.id}",
                metadata={"cliente_id": s.cliente_id},
            )

            if result.ok:
                _audit_log(
                    action_type="SOLICITUD_EDIT",
                    entity_type="Solicitud",
                    entity_id=s.id,
                    summary=f"Solicitud editada: {s.codigo_solicitud or s.id}",
                    metadata={"cliente_id": s.cliente_id, "tipo_servicio": s.tipo_servicio},
                )
                flash('Solicitud actualizada correctamente.', 'success')
                if wants_async:
                    return _async_edit_response(
                        ok=True,
                        message='Solicitud actualizada correctamente.',
                        category='success',
                        redirect_url=success_redirect_url,
                        include_update_target=False,
                        http_status=200,
                    )
                return redirect(success_redirect_url)

            log_error_event(
                error_type="SAVE_ERROR",
                exc=f"Error robusto al actualizar solicitud: {result.error_message}",
                route=request.path,
                entity_type="solicitud",
                entity_id=s.id,
                request_id=request.headers.get("X-Request-ID"),
                status_code=500,
            )
            if wants_async:
                flash('No se pudo guardar correctamente. Intente nuevamente.', 'danger')
                return _async_edit_response(
                    ok=False,
                    message='No se pudo guardar correctamente. Intente nuevamente.',
                    category='danger',
                    http_status=500,
                    error_code='server_error',
                )
            flash('No se pudo guardar correctamente. Intente nuevamente.', 'danger')
        except Exception:
            db.session.rollback()
            log_error_event(
                error_type="SAVE_ERROR",
                exc="Error inesperado al actualizar solicitud",
                route=request.path,
                entity_type="solicitud",
                entity_id=s.id,
                request_id=request.headers.get("X-Request-ID"),
                status_code=500,
            )
            if wants_async:
                flash('No se pudo guardar correctamente. Intente nuevamente.', 'danger')
                return _async_edit_response(
                    ok=False,
                    message='No se pudo guardar correctamente. Intente nuevamente.',
                    category='danger',
                    http_status=500,
                    error_code='server_error',
                )
            flash('No se pudo guardar correctamente. Intente nuevamente.', 'danger')

    elif request.method == 'POST':
        if wants_async:
            return _async_edit_response(
                ok=False,
                message='Revisa los campos marcados y corrige los errores.',
                category='warning',
                http_status=200,
                error_code='invalid_input',
                include_region=True,
                async_feedback={"message": "Revisa los campos marcados y corrige los errores.", "category": "warning"},
            )
        flash('Revisa los campos marcados en rojo.', 'danger')

    return render_template(
        'admin/solicitud_form.html',
        form=form,
        cliente_id=cliente_id,
        solicitud=s,
        nuevo=False,
        public_pasaje_mode=public_pasaje_mode,
        public_pasaje_otro=public_pasaje_otro,
        public_modalidad_group=public_modalidad_group,
        public_modalidad_specific=public_modalidad_specific,
        public_modalidad_other=public_modalidad_other,
        next_url=safe_next,
    )



# ─────────────────────────────────────────────────────────────
# Helpers: Autocomplete/Select de candidatas (para reemplazos, pagos, etc.)
# ─────────────────────────────────────────────────────────────

def _load_candidatas_choices(q: str, limit: int = 50):
    """Devuelve lista de tuples (id, label) para WTForms SelectField.

    Se usa en pantallas con barra de búsqueda por querystring `?q=...`.
    Busca por: nombre, cédula, código y teléfono.

    NOTA: Si `q` viene vacío, devolvemos [] para evitar cargar 50 candidatas sin necesidad.
    """
    q = (q or '').strip()
    if not q:
        return []

    like = f"%{q}%"

    candidatas = (
        Candidata.query
        .filter(candidatas_activas_filter(Candidata))
        .filter(
            or_(
                Candidata.nombre_completo.ilike(like),
                Candidata.cedula.ilike(like),
                Candidata.codigo.ilike(like),
                Candidata.numero_telefono.ilike(like),
            )
        )
        .order_by(Candidata.nombre_completo.asc())
        .limit(int(limit))
        .all()
    )

    choices = []
    for c in candidatas:
        nombre = (c.nombre_completo or '').strip()
        ced = (c.cedula or '').strip()
        tel = (c.numero_telefono or '').strip()

        extra = ""
        if ced and tel:
            extra = f" — {ced} — {tel}"
        elif ced:
            extra = f" — {ced}"
        elif tel:
            extra = f" — {tel}"

        label = f"{nombre}{extra}".strip() if nombre else f"ID {c.fila}{extra}".strip()
        choices.append((c.fila, label))

    return choices

# ─────────────────────────────────────────────────────────────
# Helpers de apoyo (dinero, choices)
# ─────────────────────────────────────────────────────────────
def _parse_money_to_decimal_str(raw: str, places: int = 2) -> str:
    """Convierte entradas humanas a string decimal normalizado con punto y N decimales.

    Acepta formatos comunes:
      - "RD$ 1,234.50", "$1200", "1200,50", "  5000  "
      - "1,500" (miles), "1.500" (miles), "1.500,50" (EU), "1,500.50" (US)

    Retorna string canónica: "1234.56".
    Lanza ValueError si no se puede parsear.
    """
    if raw is None:
        raise ValueError("Monto vacío")

    s = str(raw).strip()
    if not s:
        raise ValueError("Monto vacío")

    # quitar símbolos y espacios
    s = s.replace("RD$", "").replace("$", "").replace(" ", "")

    # Caso mixto: tiene punto y coma
    if "." in s and "," in s:
        # Si la última coma está a la derecha del último punto -> coma es decimal (EU)
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            # Punto decimal, coma miles (US)
            s = s.replace(",", "")
    else:
        # Solo comas -> puede ser decimal con coma o miles con coma
        if "," in s:
            parts = s.split(",")
            if len(parts) > 2:
                # 1,234,567 -> miles
                s = "".join(parts)
            else:
                # Ambiguo: si hay 1-2 dígitos al final asumimos decimales
                if len(parts[-1]) in (1, 2):
                    s = s.replace(",", ".")
                else:
                    s = s.replace(",", "")

        # Solo puntos -> puede ser miles con punto o decimal con punto
        elif "." in s:
            parts = s.split(".")
            if len(parts) > 2:
                # 1.234.567,89 o 1.234.567 -> asumimos miles
                s = "".join(parts[:-1]) + "." + parts[-1]

    try:
        val = Decimal(s)
    except InvalidOperation:
        raise ValueError("Monto inválido")

    if val < 0:
        raise ValueError("Monto negativo no permitido")

    q = Decimal(10) ** -int(places)
    val = val.quantize(q)
    return f"{val:.{places}f}"

def _to_decimal_safe(value) -> Decimal:
    """Convierte un valor (None/Decimal/int/float/str) a Decimal(0.01) de forma segura.

    - Si el valor no se puede convertir, devuelve 0.00
    - Limpia strings raros (RD$, comas, espacios) y deja solo dígitos y punto.
    """
    if value is None:
        return Decimal('0.00')

    if isinstance(value, Decimal):
        return value.quantize(Decimal('0.01'))

    # si viene como int/float
    if isinstance(value, (int, float)):
        return Decimal(str(value)).quantize(Decimal('0.01'))

    # si viene string
    txt = str(value).strip()
    if not txt:
        return Decimal('0.00')

    # Dejar solo dígitos y punto (quitamos RD$, comas, letras, etc.)
    cleaned = ''.join(ch for ch in txt if ch.isdigit() or ch == '.')
    if cleaned.count('.') > 1:
        parts = cleaned.split('.')
        cleaned = parts[0] + '.' + ''.join(parts[1:])

    if cleaned in ('', '.'):
        return Decimal('0.00')

    try:
        return Decimal(cleaned).quantize(Decimal('0.01'))
    except InvalidOperation:
        return Decimal('0.00')



def _sum_decimal_fields(current_value, add_value_decimal: Decimal) -> Decimal:
    """Suma segura para campos Numeric/String mezclados.

    - current_value puede venir como None, Decimal, número o string viejo.
    - add_value_decimal debe venir como Decimal ya calculado.
    """
    actual = _to_decimal_safe(current_value)
    total = (actual + add_value_decimal).quantize(Decimal('0.01'))
    return total


def _clamp_decimal(value: Decimal, min_v: Decimal, max_v: Decimal) -> Decimal:
    """Limita un Decimal entre min_v y max_v (ambos inclusive)."""
    v = _to_decimal_safe(value)
    if v < min_v:
        return min_v
    if v > max_v:
        return max_v
    return v


def _percent_paid(monto_total, monto_pagado) -> Decimal:
    """Calcula porcentaje pagado (0–100) a partir de total vs pagado."""
    total = _to_decimal_safe(monto_total)
    pagado = _to_decimal_safe(monto_pagado)

    if total <= Decimal('0.00'):
        return Decimal('0.00')

    pct = (pagado / total) * Decimal('100.00')
    pct = _clamp_decimal(pct, Decimal('0.00'), Decimal('100.00'))
    return pct.quantize(Decimal('0.01'))

def _choice_codes(choices):
    """Devuelve set de códigos válidos de choices [(code,label), ...]."""
    out = set()
    for c in (choices or []):
        try:
            out.add(str(c[0]).strip())
        except Exception:
            try:
                out.add(str(c).strip())
            except Exception:
                pass
    return {x for x in out if x}

# ─────────────────────────────────────────────────────────────
# ADMIN: Eliminar solicitud (seguro)
# ─────────────────────────────────────────────────────────────
def _collect_solicitud_delete_plan(*, solicitud_id: int, cliente_id: int) -> dict[str, object]:
    sid = int(solicitud_id or 0)
    cid = int(cliente_id or 0)
    summary: dict[str, int] = {
        "solicitudes_candidatas": 0,
        "reemplazos": 0,
        "notificaciones_solicitud": 0,
        "tokens_publicos_solicitud": 0,
        "tokens_cliente_nuevo_solicitud": 0,
        "recommendation_runs": 0,
        "recommendation_items": 0,
        "recommendation_selections": 0,
        "contratos_digitales": 0,
        "chat_conversaciones": 0,
    }
    warnings: list[str] = []
    blocked_issues: list[str] = []

    if _table_exists("solicitudes_candidatas"):
        summary["solicitudes_candidatas"] = _safe_count(
            db.session.query(func.count(SolicitudCandidata.id))
            .filter(SolicitudCandidata.solicitud_id == sid)
        )

    if _table_exists("reemplazos"):
        summary["reemplazos"] = _safe_count(
            db.session.query(func.count(Reemplazo.id))
            .filter(Reemplazo.solicitud_id == sid)
        )
        if int(summary.get("reemplazos") or 0) > 0:
            blocked_issues.append("La solicitud tiene reemplazos asociados.")

    if _table_exists("clientes_notificaciones"):
        summary["notificaciones_solicitud"] = _safe_count(
            db.session.query(func.count(ClienteNotificacion.id))
            .filter(ClienteNotificacion.solicitud_id == sid)
        )
        mismatch = _safe_count(
            db.session.query(func.count(ClienteNotificacion.id))
            .filter(
                ClienteNotificacion.solicitud_id == sid,
                ClienteNotificacion.cliente_id != cid,
            )
        )
        if mismatch > 0:
            blocked_issues.append(
                "Existen notificaciones cruzadas con otro cliente para esta solicitud."
            )

    if _table_exists("public_solicitud_tokens_usados"):
        summary["tokens_publicos_solicitud"] = _safe_count(
            db.session.query(func.count(PublicSolicitudTokenUso.id))
            .filter(PublicSolicitudTokenUso.solicitud_id == sid)
        )
        mismatch = _safe_count(
            db.session.query(func.count(PublicSolicitudTokenUso.id))
            .filter(
                PublicSolicitudTokenUso.solicitud_id == sid,
                PublicSolicitudTokenUso.cliente_id != cid,
            )
        )
        if mismatch > 0:
            blocked_issues.append(
                "Existen tokens públicos cruzados con otro cliente para esta solicitud."
            )

    if _table_exists("public_solicitud_cliente_nuevo_tokens_usados"):
        summary["tokens_cliente_nuevo_solicitud"] = _safe_count(
            db.session.query(func.count(PublicSolicitudClienteNuevoTokenUso.id))
            .filter(PublicSolicitudClienteNuevoTokenUso.solicitud_id == sid)
        )
        mismatch = _safe_count(
            db.session.query(func.count(PublicSolicitudClienteNuevoTokenUso.id))
            .filter(
                PublicSolicitudClienteNuevoTokenUso.solicitud_id == sid,
                PublicSolicitudClienteNuevoTokenUso.cliente_id.isnot(None),
                PublicSolicitudClienteNuevoTokenUso.cliente_id != cid,
            )
        )
        if mismatch > 0:
            blocked_issues.append(
                "Existen tokens de cliente nuevo cruzados con otro cliente para esta solicitud."
            )

    if _table_exists("solicitud_recommendation_runs"):
        summary["recommendation_runs"] = _safe_count(
            db.session.query(func.count(SolicitudRecommendationRun.id))
            .filter(SolicitudRecommendationRun.solicitud_id == sid)
        )

    if _table_exists("solicitud_recommendation_items"):
        summary["recommendation_items"] = _safe_count(
            db.session.query(func.count(SolicitudRecommendationItem.id))
            .filter(SolicitudRecommendationItem.solicitud_id == sid)
        )

    if _table_exists("solicitud_recommendation_selections"):
        summary["recommendation_selections"] = _safe_count(
            db.session.query(func.count(SolicitudRecommendationSelection.id))
            .filter(SolicitudRecommendationSelection.solicitud_id == sid)
        )

    if _table_exists("contratos_digitales"):
        summary["contratos_digitales"] = _safe_count(
            db.session.query(func.count(ContratoDigital.id))
            .filter(ContratoDigital.solicitud_id == sid)
        )
        if int(summary.get("contratos_digitales") or 0) > 0:
            blocked_issues.append("La solicitud tiene contratos digitales asociados.")

    if ChatConversation is not None and _table_exists("chat_conversations"):
        summary["chat_conversaciones"] = _safe_count(
            db.session.query(func.count(ChatConversation.id))
            .filter(ChatConversation.solicitud_id == sid)
        )
        if int(summary.get("chat_conversaciones") or 0) > 0:
            blocked_issues.append("La solicitud tiene conversaciones de chat asociadas.")

    managed_tables = {
        "solicitudes",
        "solicitudes_candidatas",
        "reemplazos",
        "clientes_notificaciones",
        "public_solicitud_tokens_usados",
        "public_solicitud_cliente_nuevo_tokens_usados",
        "solicitud_recommendation_runs",
        "solicitud_recommendation_items",
        "solicitud_recommendation_selections",
        "contratos_digitales",
        "chat_conversations",
    }
    try:
        inspector = sa_inspect(db.engine)
        for table_name in inspector.get_table_names():
            if table_name in managed_tables:
                continue
            fks = inspector.get_foreign_keys(table_name) or []
            has_ref = any((fk.get("referred_table") or "") == "solicitudes" for fk in fks)
            if not has_ref:
                continue
            tbl = Table(table_name, MetaData(), autoload_with=db.engine)
            row_hits = 0
            for fk in fks:
                ref_table = (fk.get("referred_table") or "").strip()
                if ref_table != "solicitudes":
                    continue
                cols = fk.get("constrained_columns") or []
                if not cols:
                    continue
                for col_name in cols:
                    if col_name not in tbl.c:
                        continue
                    col = tbl.c[col_name]
                    row_hits += int(
                        db.session.execute(
                            sa_select(func.count()).select_from(tbl).where(col == sid)
                        ).scalar() or 0
                    )
            if row_hits > 0:
                blocked_issues.append(
                    f"Dependencia no gestionada detectada en tabla '{table_name}'."
                )
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        warnings.append("No se pudo completar la inspección de dependencias no gestionadas.")

    return {
        "solicitud_id": sid,
        "cliente_id": cid,
        "summary": summary,
        "warnings": warnings,
        "blocked_issues": blocked_issues,
    }


def _delete_solicitud_tree(*, solicitud_id: int, cliente_id: int) -> dict[str, int]:
    sid = int(solicitud_id or 0)
    cid = int(cliente_id or 0)
    deleted: dict[str, int] = {
        "solicitudes_candidatas": 0,
        "notificaciones_solicitud": 0,
        "tokens_publicos_solicitud": 0,
        "tokens_cliente_nuevo_solicitud": 0,
        "recommendation_selections": 0,
        "recommendation_items": 0,
        "recommendation_runs": 0,
        "solicitud": 0,
    }
    run_ids: list[int] = []
    item_ids: list[int] = []

    if _table_exists("solicitudes_candidatas"):
        deleted["solicitudes_candidatas"] = int(
            SolicitudCandidata.query
            .filter(SolicitudCandidata.solicitud_id == sid)
            .delete(synchronize_session=False)
            or 0
        )

    if _table_exists("clientes_notificaciones"):
        deleted["notificaciones_solicitud"] = int(
            ClienteNotificacion.query
            .filter(
                ClienteNotificacion.solicitud_id == sid,
                ClienteNotificacion.cliente_id == cid,
            )
            .delete(synchronize_session=False)
            or 0
        )

    if _table_exists("public_solicitud_tokens_usados"):
        deleted["tokens_publicos_solicitud"] = int(
            PublicSolicitudTokenUso.query
            .filter(
                PublicSolicitudTokenUso.solicitud_id == sid,
                PublicSolicitudTokenUso.cliente_id == cid,
            )
            .delete(synchronize_session=False)
            or 0
        )

    if _table_exists("public_solicitud_cliente_nuevo_tokens_usados"):
        deleted["tokens_cliente_nuevo_solicitud"] = int(
            PublicSolicitudClienteNuevoTokenUso.query
            .filter(
                PublicSolicitudClienteNuevoTokenUso.solicitud_id == sid,
                (
                    (PublicSolicitudClienteNuevoTokenUso.cliente_id == cid)
                    | (PublicSolicitudClienteNuevoTokenUso.cliente_id.is_(None))
                ),
            )
            .delete(synchronize_session=False)
            or 0
        )

    if _table_exists("solicitud_recommendation_runs"):
        run_rows = (
            db.session.query(SolicitudRecommendationRun.id)
            .filter(SolicitudRecommendationRun.solicitud_id == sid)
            .all()
        )
        run_ids = [int(row[0]) for row in (run_rows or []) if int(row[0] or 0) > 0]

    if _table_exists("solicitud_recommendation_items"):
        item_filters = [SolicitudRecommendationItem.solicitud_id == sid]
        if run_ids:
            item_filters.append(SolicitudRecommendationItem.run_id.in_(run_ids))
        item_rows = (
            db.session.query(SolicitudRecommendationItem.id)
            .filter(or_(*item_filters))
            .all()
        )
        item_ids = [int(row[0]) for row in (item_rows or []) if int(row[0] or 0) > 0]

    # Borrar en orden defensivo para respetar FKs (selections -> items -> runs).
    if _table_exists("solicitud_recommendation_selections"):
        sel_filters = [SolicitudRecommendationSelection.solicitud_id == sid]
        if run_ids:
            sel_filters.append(SolicitudRecommendationSelection.run_id.in_(run_ids))
        if item_ids:
            sel_filters.append(SolicitudRecommendationSelection.recommendation_item_id.in_(item_ids))
        deleted["recommendation_selections"] = int(
            SolicitudRecommendationSelection.query
            .filter(or_(*sel_filters))
            .delete(synchronize_session=False)
            or 0
        )

    if _table_exists("solicitud_recommendation_items"):
        item_filters = [SolicitudRecommendationItem.solicitud_id == sid]
        if run_ids:
            item_filters.append(SolicitudRecommendationItem.run_id.in_(run_ids))
        deleted["recommendation_items"] = int(
            SolicitudRecommendationItem.query
            .filter(or_(*item_filters))
            .delete(synchronize_session=False)
            or 0
        )

    if _table_exists("solicitud_recommendation_runs"):
        deleted["recommendation_runs"] = int(
            SolicitudRecommendationRun.query
            .filter(SolicitudRecommendationRun.solicitud_id == sid)
            .delete(synchronize_session=False)
            or 0
        )

    deleted["solicitud"] = int(
        Solicitud.query
        .filter(Solicitud.id == sid, Solicitud.cliente_id == cid)
        .delete(synchronize_session=False)
        or 0
    )
    return deleted


@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/<int:id>/eliminar', methods=['POST'])
@login_required
@admin_required
@admin_action_limit(bucket="delete_solicitud", max_actions=10, window_sec=60)
def eliminar_solicitud_admin(cliente_id, id):
    _owner_only()
    s = Solicitud.query.filter_by(id=id, cliente_id=cliente_id).first_or_404()
    sid = int(getattr(s, "id", 0) or 0)
    cid = int(getattr(s, "cliente_id", 0) or 0)
    codigo = str((getattr(s, "codigo_solicitud", "") or "")).strip() or str(sid)
    next_url = (request.form.get("next") or request.args.get("next") or request.referrer or "").strip()
    fallback = url_for("admin.detalle_cliente", cliente_id=cliente_id)

    def _action_response(
        *,
        ok: bool,
        message: str,
        category: str,
        http_status: int = 200,
        error_code: str | None = None,
    ):
        return _solicitudes_list_action_response(
            ok=ok,
            message=message,
            category=category,
            next_url=next_url,
            fallback=fallback,
            http_status=http_status,
            error_code=error_code,
        )

    blocked_resp = _admin_block_sensitive_action(
        scope="admin_solicitud_delete",
        entity_type="Solicitud",
        entity_id=sid,
        limit=10,
        window_seconds=600,
        min_interval_seconds=1,
        summary=f"Bloqueo de eliminación de solicitud por patrón de abuso ({sid})",
        next_url=next_url,
        fallback=fallback,
    )
    if blocked_resp is not None:
        if _admin_async_wants_json():
            return _action_response(
                ok=False,
                message="Demasiadas acciones seguidas. Espera un momento e intenta nuevamente.",
                category="warning",
                http_status=429,
                error_code="rate_limit",
            )
        return blocked_resp

    idem_row, duplicate = _claim_idempotency(
        scope="admin_solicitud_delete",
        entity_type="Solicitud",
        entity_id=sid,
        action="eliminar_solicitud_admin",
    )
    if duplicate:
        if _idempotency_request_conflict(idem_row):
            return _action_response(
                ok=False,
                message=_idempotency_conflict_message(),
                category="warning",
                http_status=409,
                error_code="idempotency_conflict",
            )
        prev_status = int(getattr(idem_row, "response_status", 0) or 0)
        if 200 <= prev_status < 300:
            return _action_response(
                ok=True,
                message="Acción ya aplicada previamente.",
                category="info",
            )
        return _action_response(
            ok=False,
            message="Solicitud duplicada detectada. Espera y vuelve a intentar.",
            category="warning",
            http_status=409,
            error_code="conflict",
        )

    expected_version = _expected_row_version()
    if _critical_concurrency_guards_enabled() and expected_version is not None:
        current_version = int(getattr(s, "row_version", 0) or 0)
        if int(expected_version) != current_version:
            return _action_response(
                ok=False,
                message="La solicitud cambió mientras trabajabas. Recarga y vuelve a intentar.",
                category="warning",
                http_status=409,
                error_code="conflict",
            )

    estado = (getattr(s, "estado", "") or "").strip().lower()
    if estado in {"pagada", "activa", "reemplazo", "espera_pago"}:
        msg = (
            "No puedes eliminar una solicitud activa/pagada/reemplazo/espera de pago. "
            "Usa el flujo operativo de cancelación."
        )
        _audit_log(
            action_type="SOLICITUD_DELETE_BLOCKED",
            entity_type="Solicitud",
            entity_id=str(sid),
            summary=f"Borrado bloqueado por estado para solicitud {codigo}",
            metadata={"estado": estado},
            success=False,
            error=msg,
        )
        return _action_response(
            ok=False,
            message=msg,
            category="warning",
            http_status=409,
            error_code="conflict",
        )

    plan = _collect_solicitud_delete_plan(solicitud_id=sid, cliente_id=cid)
    blocked_issues = list(plan.get("blocked_issues") or [])
    warnings = list(plan.get("warnings") or [])
    summary = dict(plan.get("summary") or {})
    if blocked_issues:
        msg = "Esta solicitud no puede eliminarse de forma segura: " + " | ".join(blocked_issues)
        _audit_log(
            action_type="SOLICITUD_DELETE_BLOCKED",
            entity_type="Solicitud",
            entity_id=str(sid),
            summary=f"Borrado bloqueado para solicitud {codigo}",
            metadata={
                "cliente_id": cid,
                "blocked_issues": blocked_issues,
                "warnings": warnings,
                "dependency_summary": summary,
            },
            success=False,
            error=msg,
        )
        return _action_response(
            ok=False,
            message=msg,
            category="warning",
            http_status=409,
            error_code="conflict",
        )

    try:
        deleted_rows: dict[str, int] = {}
        with db.session.begin_nested():
            deleted_rows = _delete_solicitud_tree(solicitud_id=sid, cliente_id=cid)
            if int(deleted_rows.get("solicitud") or 0) != 1:
                raise SQLAlchemyError("No se pudo confirmar la eliminación de la solicitud.")
            db.session.flush()
            cliente = Cliente.query.get(cid)
            if cliente is not None:
                total_restante = int(
                    db.session.query(func.count(Solicitud.id))
                    .filter(Solicitud.cliente_id == cid)
                    .scalar()
                    or 0
                )
                cliente.total_solicitudes = max(total_restante, 0)
                cliente.fecha_ultima_actividad = utc_now_naive()
            _set_idempotency_response(idem_row, status=200, code="ok")
        db.session.commit()
        _audit_log(
            action_type="SOLICITUD_DELETE_OK",
            entity_type="Solicitud",
            entity_id=str(sid),
            summary=f"Solicitud eliminada {codigo}",
            metadata={
                "cliente_id": cid,
                "deleted_rows": deleted_rows,
                "dependency_summary": summary,
                "warnings": warnings,
            },
            success=True,
        )
        return _action_response(
            ok=True,
            message=f"Solicitud {codigo} eliminada correctamente.",
            category="success",
        )
    except IntegrityError:
        db.session.rollback()
        msg = (
            "No se pudo eliminar la solicitud por restricciones de integridad. "
            "No se aplicaron cambios."
        )
        _audit_log(
            action_type="SOLICITUD_DELETE_FAIL",
            entity_type="Solicitud",
            entity_id=str(sid),
            summary=f"Error de integridad al eliminar solicitud {codigo}",
            metadata={"cliente_id": cid},
            success=False,
            error=msg,
        )
        return _action_response(
            ok=False,
            message=msg,
            category="danger",
            http_status=409,
            error_code="conflict",
        )
    except StaleDataError:
        db.session.rollback()
        return _action_response(
            ok=False,
            message="La solicitud cambió por otra sesión. Recarga e intenta nuevamente.",
            category="warning",
            http_status=409,
            error_code="conflict",
        )
    except SQLAlchemyError:
        db.session.rollback()
        msg = "No se pudo eliminar la solicitud de forma segura. No se aplicaron cambios."
        _audit_log(
            action_type="SOLICITUD_DELETE_FAIL",
            entity_type="Solicitud",
            entity_id=str(sid),
            summary=f"Fallo técnico al eliminar solicitud {codigo}",
            metadata={"cliente_id": cid},
            success=False,
            error=msg,
        )
        return _action_response(
            ok=False,
            message=msg,
            category="danger",
            http_status=500,
            error_code="server_error",
        )


# ─────────────────────────────────────────────────────────────
# ADMIN: Gestionar plan (valida choices y abono OBLIGATORIO)
# ─────────────────────────────────────────────────────────────
@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/<int:id>/plan', methods=['GET','POST'])
@login_required
@admin_required
@admin_action_limit(bucket="plan_abono", max_actions=25, window_sec=60)
def gestionar_plan(cliente_id, id):
    s = Solicitud.query.filter_by(id=id, cliente_id=cliente_id).first_or_404()
    form = AdminGestionPlanForm(obj=s)
    next_url = (request.args.get('next') or request.form.get('next') or request.referrer or '').strip()
    fallback_detail = url_for('admin.detalle_cliente', cliente_id=cliente_id)
    safe_next = next_url if _is_safe_redirect_url(next_url) else fallback_detail

    def _flatten_form_errors() -> list[str]:
        out = []
        try:
            for field_errors in (form.errors or {}).values():
                for msg in (field_errors or []):
                    text = str(msg or '').strip()
                    if text:
                        out.append(text)
        except Exception:
            return []
        return out

    def _render_plan_region(async_feedback=None) -> str:
        return render_template(
            'admin/_gestionar_plan_form_region.html',
            form=form,
            cliente_id=cliente_id,
            solicitud=s,
            next_url=safe_next,
            async_feedback=async_feedback,
        )

    def _render_plan_page(async_feedback=None):
        return render_template(
            'admin/gestionar_plan.html',
            form=form,
            cliente_id=cliente_id,
            solicitud=s,
            next_url=safe_next,
            async_feedback=async_feedback,
        )

    def _async_plan_response(
        *,
        ok: bool,
        message: str,
        category: str,
        redirect_url: str | None = None,
        http_status: int = 200,
        error_code: str | None = None,
        include_region: bool = True,
        async_feedback=None,
    ):
        payload = _admin_async_payload(
            success=bool(ok),
            message=message,
            category=category,
            redirect_url=redirect_url,
            replace_html=_render_plan_region(async_feedback=async_feedback) if include_region else None,
            update_target="#gestionarPlanAsyncRegion",
            errors=_flatten_form_errors(),
            error_code=error_code,
        )
        return jsonify(payload), http_status

    if request.method == 'POST' and not form.validate_on_submit():
        if _admin_async_wants_json():
            return _async_plan_response(
                ok=False,
                message='No se guardó. Revisa los campos marcados y corrige los errores.',
                category='warning',
                http_status=200,
                error_code='invalid_input',
                async_feedback={"message": "No se guardó. Revisa los campos marcados y corrige los errores.", "category": "warning"},
            )
        return _render_plan_page()

    if form.validate_on_submit():
        try:
            # --- Validar tipo_plan contra choices si existen ---
            if hasattr(form, 'tipo_plan') and getattr(form.tipo_plan, "choices", None):
                allowed = _choice_codes(form.tipo_plan.choices)
                if str(form.tipo_plan.data) not in allowed:
                    form.tipo_plan.errors.append('Tipo de plan inválido.')
                    if _admin_async_wants_json():
                        return _async_plan_response(
                            ok=False,
                            message='Tipo de plan inválido.',
                            category='danger',
                            http_status=200,
                            error_code='invalid_input',
                            async_feedback={"message": "Corrige el tipo de plan e intenta nuevamente.", "category": "danger"},
                        )
                    flash('Tipo de plan inválido.', 'danger')
                    return _render_plan_page()

            s.tipo_plan = form.tipo_plan.data

            # --- Abono OBLIGATORIO + parseo robusto ---
            if not hasattr(form, 'abono'):
                if _admin_async_wants_json():
                    return _async_plan_response(
                        ok=False,
                        message='No se pudo procesar el formulario de plan.',
                        category='danger',
                        http_status=200,
                        error_code='invalid_input',
                        include_region=True,
                        async_feedback={"message": "No se pudo procesar el formulario de plan.", "category": "danger"},
                    )
                flash('Falta el campo abono en el formulario.', 'danger')
                return _render_plan_page()

            raw_abono = (form.abono.data or '').strip()
            if not raw_abono:
                form.abono.errors.append('El abono es obligatorio.')
                if _admin_async_wants_json():
                    return _async_plan_response(
                        ok=False,
                        message='El abono es obligatorio.',
                        category='danger',
                        http_status=200,
                        error_code='invalid_input',
                        async_feedback={"message": "Completa el campo de abono.", "category": "danger"},
                    )
                flash('El abono es obligatorio.', 'danger')
                return _render_plan_page()

            try:
                s_abono = _parse_money_to_decimal_str(raw_abono)  # '1500.00'
            except ValueError as e:
                form.abono.errors.append(f'Abono inválido: {e}.')
                if _admin_async_wants_json():
                    return _async_plan_response(
                        ok=False,
                        message='El abono no tiene un formato válido.',
                        category='danger',
                        http_status=200,
                        error_code='invalid_input',
                        async_feedback={"message": "El abono no tiene un formato válido.", "category": "danger"},
                    )
                flash(f'Abono inválido: {e}. Formatos válidos: 1500, 1,500, 1.500,50', 'danger')
                return _render_plan_page()

            # Guardar abono
            s.abono = s_abono

            # --- Estado ---
            # Reactivar SIEMPRE, aunque esté pagada o cancelada.
            _set_solicitud_estado_with_outbox(s, 'activa')
            s.fecha_cancelacion = None
            s.motivo_cancelacion = None

            db.session.commit()
            success_message = 'Plan y abono actualizados correctamente.'
            if _admin_async_wants_json():
                form = AdminGestionPlanForm(formdata=None, obj=s)
                return _async_plan_response(
                    ok=True,
                    message=success_message,
                    category='success',
                    redirect_url=safe_next,
                    http_status=200,
                    include_region=False,
                )
            flash('Plan y abono actualizados correctamente.', 'success')
            return redirect(safe_next)

        except IntegrityError:
            db.session.rollback()
            if _admin_async_wants_json():
                return _async_plan_response(
                    ok=False,
                    message='Conflicto al guardar el plan. Verifica si otro cambio ocurrió al mismo tiempo.',
                    category='danger',
                    http_status=409,
                    error_code='conflict',
                    include_region=False,
                )
            flash('Conflicto al guardar el plan (valores únicos/relaciones).', 'danger')
        except SQLAlchemyError:
            db.session.rollback()
            if _admin_async_wants_json():
                return _async_plan_response(
                    ok=False,
                    message='Error de base de datos al guardar el plan.',
                    category='danger',
                    http_status=500,
                    error_code='server_error',
                    include_region=False,
                )
            flash('Error de base de datos al guardar el plan.', 'danger')
        except Exception:
            db.session.rollback()
            if _admin_async_wants_json():
                return _async_plan_response(
                    ok=False,
                    message='Ocurrió un error al guardar el plan.',
                    category='danger',
                    http_status=500,
                    error_code='server_error',
                    include_region=False,
                )
            flash('Ocurrió un error al guardar el plan.', 'danger')

    return _render_plan_page()



# ─────────────────────────────────────────────────────────────
# ADMIN: Registrar pago (robusto y consistente)
# ─────────────────────────────────────────────────────────────
@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/<int:id>/pago', methods=['GET', 'POST'])
@login_required
@admin_required
@admin_action_limit(bucket="pagos", max_actions=20, window_sec=60)
def registrar_pago(cliente_id, id):
    s = Solicitud.query.filter_by(id=id, cliente_id=cliente_id).first_or_404()
    form = AdminPagoForm()
    form_idempotency_key = _new_form_idempotency_key()

    q = (request.args.get('q') or request.form.get('q') or '').strip()
    next_url = (request.args.get('next') or request.form.get('next') or request.referrer or '').strip()
    fallback_detail = url_for('admin.detalle_cliente', cliente_id=cliente_id)
    safe_next = next_url if _is_safe_redirect_url(next_url) else fallback_detail

    def _render_pago_region(async_feedback=None) -> str:
        return render_template(
            'admin/_registrar_pago_form_region.html',
            form=form,
            cliente_id=cliente_id,
            solicitud=s,
            q=q,
            next_url=safe_next,
            form_idempotency_key=form_idempotency_key,
            async_feedback=async_feedback,
        )

    def _render_pago_page(async_feedback=None):
        return render_template(
            'admin/registrar_pago.html',
            form=form,
            cliente_id=cliente_id,
            solicitud=s,
            q=q,
            next_url=safe_next,
            form_idempotency_key=form_idempotency_key,
            async_feedback=async_feedback,
        )

    def _async_pago_response(
        *,
        ok: bool,
        message: str,
        category: str,
        redirect_url: str | None = None,
        http_status: int = 200,
        error_code: str | None = None,
        include_region: bool = True,
    ):
        payload = _admin_async_payload(
            success=bool(ok),
            message=message,
            category=category,
            redirect_url=redirect_url,
            replace_html=_render_pago_region(async_feedback={"message": message, "category": category}) if include_region else None,
            update_target="#registrarPagoAsyncRegion",
            error_code=error_code,
        )
        payload["next"] = redirect_url or ""
        return jsonify(payload), http_status

    def _build_candidata_choices(search_text):
        query = Candidata.query.filter(candidatas_activas_filter(Candidata))
        if search_text:
            like = f"%{search_text}%"
            query = query.filter(
                or_(
                    Candidata.nombre_completo.ilike(like),
                    Candidata.cedula.ilike(like),
                    Candidata.codigo.ilike(like),
                    Candidata.numero_telefono.ilike(like),
                )
            )

        candidatas = query.order_by(Candidata.nombre_completo.asc()).limit(50).all()
        choices = [(c.fila, c.nombre_completo) for c in candidatas]

        if s.candidata_id:
            cand_actual = Candidata.query.get(s.candidata_id)
            if cand_actual and cand_actual.fila not in [x[0] for x in choices]:
                choices.insert(0, (cand_actual.fila, cand_actual.nombre_completo))

        return choices

    form.candidata_id.choices = _build_candidata_choices(q)

    if request.method == 'GET' and s.candidata_id:
        form.candidata_id.data = s.candidata_id

    if request.method == 'GET' and _admin_async_wants_json():
        return jsonify(_admin_async_payload(
            success=True,
            message='Formulario actualizado.',
            category='info',
            replace_html=_render_pago_region(),
            update_target='#registrarPagoAsyncRegion',
        )), 200

    if form.validate_on_submit():
        expected_version = _expected_row_version()
        if _critical_concurrency_guards_enabled() and expected_version is not None:
            current_version = int(getattr(s, "row_version", 0) or 0)
            if int(expected_version) != current_version:
                msg = 'La solicitud cambió mientras trabajabas. Recarga y vuelve a intentar.'
                if _admin_async_wants_json():
                    return _async_pago_response(
                        ok=False,
                        message=msg,
                        category='warning',
                        http_status=409,
                        error_code='conflict',
                    )
                flash(msg, 'warning')
                return _render_pago_page()

        if s.estado in ('cancelada', 'pagada'):
            if _admin_async_wants_json():
                return _async_pago_response(
                    ok=False,
                    message='Esta solicitud no admite pagos.',
                    category='warning',
                    http_status=409,
                    error_code='conflict',
                )
            flash('Esta solicitud no admite pagos.', 'warning')
            return _render_pago_page()

        cand = Candidata.query.get(form.candidata_id.data)
        if not cand:
            if _admin_async_wants_json():
                return _async_pago_response(
                    ok=False,
                    message='Candidata inválida.',
                    category='danger',
                    http_status=404,
                    error_code='not_found',
                )
            flash('Candidata inválida.', 'danger')
            return _render_pago_page()
        blocked = assert_candidata_no_descalificada(
            cand,
            action="asignar a solicitud",
            redirect_endpoint="admin.registrar_pago",
            redirect_kwargs={"cliente_id": cliente_id, "id": id, "q": q, "next": safe_next},
        )
        if blocked is not None:
            return blocked

        idem_row, duplicate = _claim_idempotency(
            scope="solicitud_pago",
            entity_type="Solicitud",
            entity_id=s.id,
            action="registrar_pago",
        )
        if duplicate:
            if _idempotency_request_conflict(idem_row):
                msg = _idempotency_conflict_message()
                if _admin_async_wants_json():
                    return _async_pago_response(
                        ok=False,
                        message=msg,
                        category='warning',
                        http_status=409,
                        error_code='idempotency_conflict',
                    )
                flash(msg, 'warning')
                return _render_pago_page()
            prev_status = int(getattr(idem_row, "response_status", 0) or 0)
            if 200 <= prev_status < 300:
                msg = 'Pago ya procesado previamente. No se duplicó la operación.'
                if _admin_async_wants_json():
                    return _async_pago_response(
                        ok=True,
                        message=msg,
                        category='info',
                        redirect_url=safe_next,
                        include_region=False,
                    )
                flash(msg, 'info')
                return redirect(safe_next)
            msg = 'Solicitud duplicada detectada. Espera y vuelve a intentar.'
            if _admin_async_wants_json():
                return _async_pago_response(
                    ok=False,
                    message=msg,
                    category='warning',
                    http_status=409,
                    error_code='conflict',
                )
            flash(msg, 'warning')
            return _render_pago_page()

        s.candidata_id = cand.fila
        _sync_solicitud_candidatas_after_assignment(s, cand.fila)
        try:
            _mark_candidata_estado(cand, "trabajando")
        except InvariantConflictError as inv_exc:
            msg = str(inv_exc) or "Conflicto de estado de candidata."
            if _admin_async_wants_json():
                return _async_pago_response(
                    ok=False,
                    message=msg,
                    category='warning',
                    http_status=409,
                    error_code=getattr(inv_exc, "code", "conflict"),
                )
            flash(msg, 'warning')
            return _render_pago_page()

        # Monto pagado
        s.monto_pagado = _parse_money_to_decimal_str(form.monto_pagado.data)

        # Siempre calculamos el 25% si hay sueldo en la solicitud.
        # Si una candidata no acepta porcentaje, por requisito queda descalificada antes,
        # así que aquí no validamos esa columna.
        if s.sueldo:
            try:
                sueldo = Decimal(_parse_money_to_decimal_str(s.sueldo))
                monto_25 = (sueldo * Decimal('0.25')).quantize(Decimal('0.01'))

                # Guardamos el total (si existe el campo)
                # ✅ Si ya tenía un monto_total previo, lo acumulamos.
                if hasattr(cand, 'monto_total'):
                    try:
                        cand.monto_total = _sum_decimal_fields(getattr(cand, 'monto_total', None), sueldo)
                    except Exception:
                        cand.monto_total = sueldo

                # ✅ Guardar MONTO del 25% (en dinero), no el número 25.
                # Nota: si tu BD tenía un CHECK que obliga 0–100, ese CHECK debe ajustarse
                # para permitir montos (>= 0). En código, aquí guardamos el monto real.
                if hasattr(cand, 'porciento'):
                    try:
                        cand.porciento = _sum_decimal_fields(getattr(cand, 'porciento', None), monto_25)
                    except Exception:
                        cand.porciento = monto_25

                # Fecha de pago (si existe)
                if hasattr(cand, 'fecha_de_pago') and not getattr(cand, 'fecha_de_pago', None):
                    cand.fecha_de_pago = rd_today()

                db.session.add(cand)
            except Exception:
                # Si el sueldo viene raro, no rompemos el pago
                pass

        _set_solicitud_estado(s, 'pagada')
        _emit_domain_outbox_event(
            event_type="SOLICITUD_PAGO_REGISTRADO",
            aggregate_type="Solicitud",
            aggregate_id=s.id,
            aggregate_version=(int(getattr(s, "row_version", 0) or 0) + 1),
            payload={
                "solicitud_id": int(s.id),
                "cliente_id": int(cliente_id),
                "estado": "pagada",
                "candidata_id": int(getattr(cand, "fila", 0) or 0),
            },
        )
        _set_idempotency_response(idem_row, status=200, code="ok")

        try:
            db.session.commit()
        except StaleDataError:
            db.session.rollback()
            if _admin_async_wants_json():
                return _async_pago_response(
                    ok=False,
                    message='La solicitud cambió por otra sesión. Recarga e intenta nuevamente.',
                    category='warning',
                    http_status=409,
                    error_code='conflict',
                )
            flash('La solicitud cambió por otra sesión. Recarga e intenta nuevamente.', 'warning')
            return _render_pago_page()
        except IntegrityError as e:
            db.session.rollback()
            msg = str(getattr(e, "orig", e))
            # Caso: constraint viejo que obliga porciento entre 0 y 100
            if "chk_porciento" in msg:
                constraint_msg = (
                    "Tu BD tiene un CHECK (chk_porciento) que obliga 'porciento' a estar entre 0 y 100. "
                    "Ahora estás guardando el MONTO del 25% (ej: 16000.00), por eso falla. "
                    "Solución: cambia ese constraint para permitir montos (porciento >= 0) o guarda 25 (porcentaje) en vez del monto."
                )
                if _admin_async_wants_json():
                    return _async_pago_response(
                        ok=False,
                        message=constraint_msg,
                        category='danger',
                        http_status=409,
                        error_code='conflict',
                    )
                flash(
                    constraint_msg,
                    "danger"
                )
            else:
                if _admin_async_wants_json():
                    return _async_pago_response(
                        ok=False,
                        message='No se pudo registrar el pago por un conflicto de datos en la base de datos.',
                        category='danger',
                        http_status=409,
                        error_code='conflict',
                    )
                flash('No se pudo registrar el pago por un conflicto de datos en la base de datos.', 'danger')
            return _render_pago_page()

        if _admin_async_wants_json():
            return _async_pago_response(
                ok=True,
                message='Pago registrado correctamente.',
                category='success',
                redirect_url=safe_next,
                include_region=False,
            )
        flash('Pago registrado correctamente.', 'success')
        return redirect(safe_next)

    if request.method == 'POST' and _admin_async_wants_json():
        return _async_pago_response(
            ok=False,
            message='No se guardó. Revisa los campos marcados y corrige los errores.',
            category='warning',
            http_status=200,
            error_code='invalid_input',
        )

    return _render_pago_page()


@admin_bp.route('/solicitudes/<int:s_id>/reemplazos/nuevo', methods=['GET', 'POST'])
@login_required
@staff_required
@admin_action_limit(bucket="reemplazos", max_actions=15, window_sec=60)
def nuevo_reemplazo(s_id):
    sol = (
        Solicitud.query
        .options(joinedload(Solicitud.reemplazos), joinedload(Solicitud.candidata))
        .get_or_404(s_id)
    )

    form = AdminReemplazoForm()
    form_idempotency_key = (request.form.get("idempotency_key") or "").strip() or _new_form_idempotency_key()
    reemplazo_activo = _active_reemplazo_for_solicitud(sol)
    next_url = (request.form.get("next") or request.args.get("next") or "").strip()
    dynamic_target = (request.form.get("_async_target") or request.args.get("_async_target") or "").strip()
    fallback_detail = url_for('admin.detalle_cliente', cliente_id=sol.cliente_id)
    fallback = (
        url_for('admin.listar_solicitudes')
        if dynamic_target == '#solicitudesAsyncRegion' or dynamic_target.startswith("#solicitudReemplazoActionsAsyncRegion-")
        else fallback_detail
    )

    def _action_response(
        *,
        ok: bool,
        message: str,
        category: str,
        http_status: int = 200,
        error_code: str | None = None,
    ):
        replace_html = None
        update_target = dynamic_target
        if _admin_async_wants_json():
            if ok:
                update_target = _reemplazo_parent_async_target(dynamic_target) or dynamic_target
            else:
                replace_html = _render_reemplazo_actions_region(
                    solicitud_id=int(sol.id),
                    dynamic_target=dynamic_target,
                    next_url=next_url,
                )
        return _solicitudes_list_action_response(
            ok=ok,
            message=message,
            category=category,
            next_url=next_url,
            fallback=fallback,
            http_status=http_status,
            error_code=error_code,
            replace_html=replace_html,
            update_target=update_target,
        )

    blocked_resp = _admin_block_sensitive_action(
        scope="admin_reemplazo_open",
        entity_type="Solicitud",
        entity_id=sol.id,
        limit=20,
        window_seconds=600,
        min_interval_seconds=2,
        summary=f"Bloqueo de apertura de reemplazo por patrón de abuso (solicitud {sol.id})",
        next_url=next_url,
        fallback=fallback_detail,
    )
    if blocked_resp is not None:
        if _admin_async_wants_json():
            return _action_response(
                ok=False,
                message='Demasiadas acciones seguidas. Espera un momento e intenta nuevamente.',
                category='warning',
                http_status=429,
                error_code='rate_limit',
            )
        return blocked_resp

    expected_version = _expected_row_version()
    if _critical_concurrency_guards_enabled() and expected_version is not None:
        current_version = int(getattr(sol, "row_version", 0) or 0)
        if int(expected_version) != current_version:
            return _action_response(
                ok=False,
                message='La solicitud cambió mientras trabajabas. Recarga y vuelve a intentar.',
                category='warning',
                http_status=409,
                error_code='conflict',
            )

    idem_row, duplicate = _claim_idempotency(
        scope="admin_reemplazo_open",
        entity_type="Solicitud",
        entity_id=sol.id,
        action="abrir_reemplazo",
    )
    if duplicate:
        if _idempotency_request_conflict(idem_row):
            return _action_response(
                ok=False,
                message=_idempotency_conflict_message(),
                category='warning',
                http_status=409,
                error_code='idempotency_conflict',
            )
        prev_status = int(getattr(idem_row, "response_status", 0) or 0)
        if 200 <= prev_status < 300:
            return _action_response(
                ok=True,
                message='Acción ya aplicada previamente.',
                category='info',
            )
        return _action_response(
            ok=False,
            message='Solicitud duplicada detectada. Espera y vuelve a intentar.',
            category='warning',
            http_status=409,
            error_code='conflict',
        )

    # ✅ SIEMPRE usar la candidata asignada originalmente a la solicitud (por relación)
    assigned_id = getattr(sol, 'candidata_id', None)

    # Si no hay candidata asignada, no se puede iniciar reemplazo
    if not assigned_id or not getattr(sol, 'candidata', None):
        return _action_response(
            ok=False,
            message='Esta solicitud no tiene candidata asignada. Primero asigna una candidata antes de iniciar un reemplazo.',
            category='warning',
            http_status=409,
            error_code='conflict',
        )
    if reemplazo_activo:
        if _admin_noop_repeat_blocked(
            scope="admin_reemplazo_open",
            entity_type="Solicitud",
            entity_id=sol.id,
            state="reemplazo_activo",
            summary=f"Intento repetido de abrir reemplazo ya activo (solicitud {sol.id})",
        ):
            return _action_response(
                ok=False,
                message='Acción bloqueada temporalmente: ya existe un reemplazo activo para esta solicitud.',
                category='warning',
                http_status=429,
                error_code='rate_limit',
            )
        return _action_response(
            ok=False,
            message='Ya existe un reemplazo activo para esta solicitud.',
            category='warning',
            http_status=409,
            error_code='conflict',
        )

    # Prefill (por si tu form/template muestra campos)
    # No hay búsqueda ni selección manual: todo viene de sol.candidata
    try:
        if hasattr(form, 'candidata_old_id'):
            form.candidata_old_id.data = str(int(assigned_id))
    except Exception:
        pass

    try:
        if hasattr(form, 'candidata_old_name'):
            form.candidata_old_name.data = (sol.candidata.nombre_completo or '').strip()
    except Exception:
        pass

    if form.validate_on_submit():
        try:
            # ✅ Candidata anterior: SIEMPRE la asignada actual
            cand_old = sol.candidata
            if not cand_old:
                flash('No se encontró la candidata asignada a esta solicitud.', 'danger')
                return redirect(next_url if _is_safe_redirect_url(next_url) else fallback_detail)

            descalificar = str(request.form.get('descalificar_candidata_fallida') or '').strip().lower() in ('1', 'true', 'on', 'yes')
            motivo_descalificacion = (request.form.get('motivo_descalificacion') or '').strip()
            if descalificar and not motivo_descalificacion:
                if _admin_async_wants_json():
                    return _action_response(
                        ok=False,
                        message='Debes indicar el motivo de descalificación.',
                        category='warning',
                        http_status=400,
                        error_code='invalid_input',
                    )
                flash('Debes indicar el motivo de descalificación.', 'warning')
                return render_template(
                    'admin/reemplazo_inicio.html',
                    form=form,
                    solicitud=sol,
                    form_idempotency_key=form_idempotency_key,
                )

            r = Reemplazo(
                solicitud_id=sol.id,
                candidata_old_id=cand_old.fila,
                motivo_fallo=(form.motivo_fallo.data or '').strip(),
                estado_previo_solicitud=(sol.estado or '').strip().lower() or None,
            )

            ahora = utc_now_naive()
            r.fecha_fallo = ahora
            r.iniciar_reemplazo()
            if getattr(r, 'fecha_inicio_reemplazo', None) is None:
                r.fecha_inicio_reemplazo = ahora
                r.oportunidad_nueva = True

            _set_solicitud_estado(sol, 'reemplazo', now_dt=ahora)
            mark_lista_from_state = None

            if descalificar:
                _mark_candidata_estado(cand_old, 'descalificada', nota_descalificacion=motivo_descalificacion)
            else:
                ready_ok, reasons = candidata_is_ready_to_send(cand_old)
                blocking = [rr for rr in (reasons or []) if not str(rr).lower().startswith("advertencia:")]
                if ready_ok and not blocking:
                    mark_lista_from_state = (getattr(cand_old, "estado", None) or "").strip().lower()
                    _mark_candidata_estado(cand_old, 'lista_para_trabajar')
                elif blocking:
                    flash(
                        "La candidata que falló no pudo volver a lista para trabajar. Falta: "
                        + "; ".join(blocking[:4]),
                        "warning",
                    )

            db.session.add(r)
            db.session.flush()
            _emit_domain_outbox_event(
                event_type="REEMPLAZO_ABIERTO",
                aggregate_type="Solicitud",
                aggregate_id=sol.id,
                aggregate_version=(int(getattr(sol, "row_version", 0) or 0) + 1),
                payload={
                    "solicitud_id": int(sol.id),
                    "reemplazo_id": int(r.id),
                    "candidata_old_id": int(cand_old.fila),
                    "estado_previo": (getattr(r, "estado_previo_solicitud", None) or "").strip().lower() or None,
                    "descalificar": bool(descalificar),
                },
            )
            _notify_cliente_reemplazo_activado(sol, reemplazo_id=int(getattr(r, "id", 0) or 0))
            _set_idempotency_response(idem_row, status=200, code="ok")
            db.session.commit()
            _audit_log(
                action_type="REEMPLAZO_ABRIR",
                entity_type="Solicitud",
                entity_id=sol.id,
                summary=f"Reemplazo iniciado para solicitud {sol.codigo_solicitud or sol.id}",
                metadata={"reemplazo_id": r.id, "candidata_old_id": cand_old.fila, "descalificar": bool(descalificar)},
            )
            log_candidata_action(
                action_type="REEMPLAZO_OPEN",
                candidata=cand_old,
                summary=f"Reemplazo abierto para candidata en solicitud {sol.codigo_solicitud or sol.id}",
                metadata={
                    "reemplazo_id": r.id,
                    "solicitud_id": sol.id,
                    "cliente_id": sol.cliente_id,
                    "descalificar": bool(descalificar),
                },
                success=True,
            )
            if not descalificar and (getattr(cand_old, "estado", None) or "").strip().lower() == "lista_para_trabajar":
                _log_lista_state_change(
                    cand_old,
                    source="auto",
                    faltantes=[],
                    from_state=mark_lista_from_state,
                )

            return _action_response(
                ok=True,
                message='Reemplazo iniciado correctamente.',
                category='success',
            )

        except InvariantConflictError as inv_exc:
            db.session.rollback()
            return _action_response(
                ok=False,
                message=str(inv_exc) or "Conflicto de estado de candidata.",
                category='warning',
                http_status=409,
                error_code=getattr(inv_exc, "code", "conflict"),
            )
        except StaleDataError:
            db.session.rollback()
            return _action_response(
                ok=False,
                message='La solicitud cambió por otra sesión. Recarga e intenta nuevamente.',
                category='warning',
                http_status=409,
                error_code='conflict',
            )
        except Exception:
            db.session.rollback()
            _audit_log(
                action_type="REEMPLAZO_ABRIR",
                entity_type="Solicitud",
                entity_id=sol.id,
                summary=f"Fallo iniciando reemplazo para solicitud {sol.id}",
                success=False,
                error="Error al iniciar reemplazo.",
            )
            log_candidata_action(
                action_type="REEMPLAZO_OPEN",
                candidata=cand_old if 'cand_old' in locals() else None,
                summary=f"Fallo iniciando reemplazo para solicitud {sol.id}",
                metadata={"solicitud_id": sol.id, "cliente_id": sol.cliente_id},
                success=False,
                error="Error al iniciar reemplazo.",
            )
            return _action_response(
                ok=False,
                message='No se pudo iniciar el reemplazo.',
                category='danger',
                http_status=500,
                error_code='server_error',
            )

    if request.method == 'POST' and _admin_async_wants_json():
        return _action_response(
            ok=False,
            message='No se guardó. Revisa los campos e intenta nuevamente.',
            category='warning',
            http_status=400,
            error_code='invalid_input',
        )

    # 👇 Ya no se manda "q" porque eliminamos búsqueda
    return render_template(
        'admin/reemplazo_inicio.html',
        form=form,
        solicitud=sol,
        form_idempotency_key=form_idempotency_key,
    )


@admin_bp.route(
    '/solicitudes/<int:s_id>/reemplazos/<int:reemplazo_id>/finalizar',
    methods=['GET', 'POST']
)
@login_required
@admin_required
def finalizar_reemplazo(s_id, reemplazo_id):
    s = (
        Solicitud.query
        .options(
            joinedload(Solicitud.reemplazos),
            joinedload(Solicitud.candidata)
        )
        .get_or_404(s_id)
    )

    r = Reemplazo.query.filter_by(id=reemplazo_id, solicitud_id=s_id).first_or_404()
    form = AdminReemplazoFinForm()
    form_idempotency_key = (request.form.get("idempotency_key") or "").strip() or _new_form_idempotency_key()

    # ✅ Igual que PAGO
    q = (request.args.get('q') or request.form.get('q') or '').strip()

    def _render_pick_region() -> str:
        return render_template(
            'admin/_reemplazo_fin_pick_region.html',
            form=form,
            q=q,
            pick_name=pick_name,
            candidatas=candidatas,
        )

    # ✅ Detectar el field real que existe en el form
    if hasattr(form, 'domestica_id'):
        pick_field = form.domestica_id
        pick_name = 'domestica_id'
    elif hasattr(form, 'candidata_new_id'):
        pick_field = form.candidata_new_id
        pick_name = 'candidata_new_id'
    elif hasattr(form, 'candidata_id'):
        pick_field = form.candidata_id
        pick_name = 'candidata_id'
    else:
        flash('Error: el formulario no tiene un campo para seleccionar candidata.', 'danger')
        return redirect(url_for('admin.detalle_cliente', cliente_id=s.cliente_id))

    def _query_candidatas(search_text: str):
        # ✅ Si no hay búsqueda, NO cargamos nada
        if not search_text:
            return []
        return _search_candidatas_reemplazo(search_text, limit=50)

    def _build_choices_from_list(items):
        """✅ Para SelectField(coerce=int): value SIEMPRE int (nunca '' / None)."""
        out = []
        for c in items:
            nombre = (c.nombre_completo or '').strip()
            ced = (c.cedula or '').strip()
            tel = (c.numero_telefono or '').strip()

            extra = ""
            if ced and tel:
                extra = f" — {ced} — {tel}"
            elif ced:
                extra = f" — {ced}"
            elif tel:
                extra = f" — {tel}"

            label = f"{nombre}{extra}".strip() if nombre else f"ID {c.fila}{extra}".strip()

            try:
                out.append((int(c.fila), label))
            except Exception:
                continue

        return out

    def _choice_tuple_for_candidata(c):
        if not c:
            return None
        nombre = (c.nombre_completo or '').strip()
        ced = (c.cedula or '').strip()
        tel = (c.numero_telefono or '').strip()

        extra = ""
        if ced and tel:
            extra = f" — {ced} — {tel}"
        elif ced:
            extra = f" — {ced}"
        elif tel:
            extra = f" — {tel}"

        try:
            cid = int(c.fila)
        except Exception:
            return None
        label = f"{nombre}{extra}".strip() if nombre else f"ID {cid}{extra}".strip()
        return (cid, label)

    # ✅ RESULTADOS (para tabla) + CHOICES (para select)
    candidatas = _query_candidatas(q)
    choices = _build_choices_from_list(candidatas)

    # ✅ Si ya hay candidata guardada en el reemplazo, subirla arriba (aunque no esté en búsqueda)
    cand_actual_id = getattr(r, 'candidata_new_id', None)
    try:
        cand_actual_id_int = int(cand_actual_id) if cand_actual_id else None
    except Exception:
        cand_actual_id_int = None

    if cand_actual_id_int:
        cand_actual = Candidata.query.get(cand_actual_id_int)
        if cand_actual:
            nombre = (cand_actual.nombre_completo or '').strip()
            ced = (cand_actual.cedula or '').strip()
            tel = (cand_actual.numero_telefono or '').strip()

            extra = ""
            if ced and tel:
                extra = f" — {ced} — {tel}"
            elif ced:
                extra = f" — {ced}"
            elif tel:
                extra = f" — {tel}"

            top = (
                int(cand_actual.fila),
                f"{nombre}{extra}".strip() if nombre else f"ID {cand_actual.fila}{extra}".strip()
            )

            ids = [x[0] for x in choices]
            if top[0] in ids:
                choices = [top] + [x for x in choices if x[0] != top[0]]
            else:
                choices.insert(0, top)

    # ✅ Placeholder arriba (OJO: value int=0, NO '')
    pick_field.choices = [(0, '— Selecciona una doméstica —')] + choices

    # ✅ POST robusto: si el submit llega con q vacío/stale, rehidratar choice seleccionada
    # para que WTForms no falle con "Not a valid choice".
    if request.method == 'POST':
        raw_selected = (
            request.form.get(pick_name)
            or request.form.get('candidata_new_id')
            or request.form.get('domestica_id')
            or request.form.get('candidata_id')
            or ''
        )
        try:
            selected_id = int(str(raw_selected).strip() or 0)
        except Exception:
            selected_id = 0

        if selected_id > 0 and all(int(v) != selected_id for v, _ in (pick_field.choices or [])):
            selected_obj = Candidata.query.get(selected_id)
            selected_choice = _choice_tuple_for_candidata(selected_obj)
            if selected_choice is not None:
                pick_field.choices = list(pick_field.choices or []) + [selected_choice]

    # ✅ GET: precargar si ya existe candidata_new_id en el reemplazo
    if request.method == 'GET':
        if cand_actual_id_int:
            try:
                pick_field.data = int(cand_actual_id_int)
            except Exception:
                pick_field.data = 0
        else:
            pick_field.data = 0

    if request.method == 'GET' and _admin_async_wants_json():
        return jsonify(_admin_async_payload(
            success=True,
            message='Resultados actualizados.',
            category='info',
            replace_html=_render_pick_region(),
            update_target='#reemplazoFinPickRegion',
            redirect_url=None,
        )), 200

    if form.validate_on_submit():
        expected_version = _expected_row_version()
        if _critical_concurrency_guards_enabled() and expected_version is not None:
            current_version = int(getattr(s, "row_version", 0) or 0)
            if int(expected_version) != current_version:
                msg = 'La solicitud cambió mientras trabajabas. Recarga y vuelve a intentar.'
                if _admin_async_wants_json():
                    return jsonify(_admin_async_payload(
                        success=False,
                        message=msg,
                        category='warning',
                        redirect_url=None,
                        error_code='conflict',
                    )), 409
                flash(msg, 'warning')
                return redirect(url_for('admin.detalle_cliente', cliente_id=s.cliente_id))

        if (s.estado or "").strip().lower() != "reemplazo":
            msg = 'La solicitud no tiene un reemplazo activo para culminar.'
            if _admin_async_wants_json():
                return jsonify(_admin_async_payload(
                    success=False,
                    message=msg,
                    category='warning',
                    redirect_url=None,
                    error_code='conflict',
                )), 409
            flash(msg, 'warning')
            return redirect(url_for('admin.detalle_cliente', cliente_id=s.cliente_id))

        if getattr(r, "fecha_fin_reemplazo", None):
            msg = 'Este reemplazo ya está cerrado.'
            if _admin_async_wants_json():
                return jsonify(_admin_async_payload(
                    success=False,
                    message=msg,
                    category='warning',
                    redirect_url=None,
                    error_code='conflict',
                )), 409
            flash(msg, 'warning')
            return redirect(url_for('admin.detalle_cliente', cliente_id=s.cliente_id))

        idem_row, duplicate = _claim_idempotency(
            scope="admin_reemplazo_finalize",
            entity_type="Solicitud",
            entity_id=s.id,
            action="finalizar_reemplazo",
        )
        if duplicate:
            if _idempotency_request_conflict(idem_row):
                msg = _idempotency_conflict_message()
                if _admin_async_wants_json():
                    return jsonify(_admin_async_payload(
                        success=False,
                        message=msg,
                        category='warning',
                        redirect_url=None,
                        error_code='idempotency_conflict',
                    )), 409
                flash(msg, 'warning')
                return redirect(url_for('admin.detalle_cliente', cliente_id=s.cliente_id))
            prev_status = int(getattr(idem_row, "response_status", 0) or 0)
            if 200 <= prev_status < 300:
                msg = 'Acción ya aplicada previamente.'
                if _admin_async_wants_json():
                    return jsonify(_admin_async_payload(
                        success=True,
                        message=msg,
                        category='info',
                        redirect_url=url_for('admin.detalle_cliente', cliente_id=s.cliente_id),
                    )), 200
                flash(msg, 'info')
                return redirect(url_for('admin.detalle_cliente', cliente_id=s.cliente_id))
            msg = 'Solicitud duplicada detectada. Espera y vuelve a intentar.'
            if _admin_async_wants_json():
                return jsonify(_admin_async_payload(
                    success=False,
                    message=msg,
                    category='warning',
                    redirect_url=None,
                    error_code='conflict',
                )), 409
            flash(msg, 'warning')
            return redirect(url_for('admin.detalle_cliente', cliente_id=s.cliente_id))

        try:
            # ✅ leer id seleccionado (int)
            try:
                cand_new_id = int(pick_field.data or 0)
            except Exception:
                cand_new_id = 0

            if cand_new_id <= 0:
                flash('Debes seleccionar la nueva candidata.', 'danger')
                return render_template(
                    'admin/reemplazo_fin.html',
                    form=form,
                    solicitud=s,
                    reemplazo=r,
                    q=q,
                    pick_name=pick_name,
                    candidatas=candidatas,
                    form_idempotency_key=form_idempotency_key,
                )

            cand_new = Candidata.query.get(cand_new_id)
            if not cand_new:
                flash('La candidata seleccionada no existe.', 'danger')
                return render_template(
                    'admin/reemplazo_fin.html',
                    form=form,
                    solicitud=s,
                    reemplazo=r,
                    q=q,
                    pick_name=pick_name,
                    candidatas=candidatas,
                    form_idempotency_key=form_idempotency_key,
                )
            blocked = assert_candidata_no_descalificada(
                cand_new,
                action="asignar a solicitud",
                redirect_endpoint="admin.finalizar_reemplazo",
                redirect_kwargs={"s_id": s_id, "reemplazo_id": reemplazo_id, "q": q},
            )
            if blocked is not None:
                return blocked

            ahora = utc_now_naive()

            # Guardar reemplazo
            r.candidata_new_id = cand_new.fila

            if hasattr(form, 'nota_adicional'):
                r.nota_adicional = (form.nota_adicional.data or '').strip() or None

            if hasattr(r, 'fecha_fin_reemplazo'):
                r.fecha_fin_reemplazo = ahora
            elif hasattr(r, 'fecha_fin'):
                r.fecha_fin = ahora

            # Reasignar solicitud
            s.candidata_id = cand_new.fila
            estado_restore = (getattr(r, "estado_previo_solicitud", None) or "activa").strip().lower()
            if estado_restore == "reemplazo":
                estado_restore = "activa"
            _set_solicitud_estado(s, estado_restore, now_dt=ahora)
            _sync_solicitud_candidatas_after_assignment(s, cand_new.fila)
            _mark_candidata_estado(cand_new, "trabajando")

            # Porcentaje (MISMA lógica que PAGO)
            if getattr(s, 'sueldo', None):
                try:
                    sueldo = Decimal(_parse_money_to_decimal_str(s.sueldo))
                    monto_25 = (sueldo * Decimal('0.25')).quantize(Decimal('0.01'))

                    # ✅ Si ya tenía un monto_total previo, lo acumulamos.
                    if hasattr(cand_new, 'monto_total'):
                        try:
                            cand_new.monto_total = _sum_decimal_fields(getattr(cand_new, 'monto_total', None), sueldo)
                        except Exception:
                            cand_new.monto_total = sueldo

                    # ✅ Guardar MONTO del 25% (en dinero), igual que en PAGO.
                    if hasattr(cand_new, 'porciento'):
                        try:
                            cand_new.porciento = _sum_decimal_fields(getattr(cand_new, 'porciento', None), monto_25)
                        except Exception:
                            cand_new.porciento = monto_25

                    # Fecha de pago (si existe)
                    if hasattr(cand_new, 'fecha_de_pago') and not getattr(cand_new, 'fecha_de_pago', None):
                        cand_new.fecha_de_pago = rd_today()

                    if hasattr(cand_new, 'fecha_ultima_modificacion'):
                        cand_new.fecha_ultima_modificacion = ahora

                    db.session.add(cand_new)
                except Exception:
                    # Si el sueldo viene raro, no rompemos el flujo
                    pass

            _emit_domain_outbox_event(
                event_type="REEMPLAZO_FINALIZADO",
                aggregate_type="Solicitud",
                aggregate_id=s.id,
                aggregate_version=(int(getattr(s, "row_version", 0) or 0) + 1),
                payload={
                    "solicitud_id": int(s.id),
                    "reemplazo_id": int(r.id),
                    "candidata_old_id": int(getattr(r, "candidata_old_id", 0) or 0) or None,
                    "candidata_new_id": int(cand_new.fila),
                    "estado_restaurado": estado_restore,
                },
            )
            _set_idempotency_response(idem_row, status=200, code="ok")
            db.session.commit()
            _audit_log(
                action_type="REEMPLAZO_CERRAR",
                entity_type="Solicitud",
                entity_id=s.id,
                summary=f"Reemplazo finalizado para solicitud {s.codigo_solicitud or s.id}",
                metadata={"reemplazo_id": r.id, "candidata_new_id": cand_new.fila},
            )
            cand_old = Candidata.query.filter_by(fila=getattr(r, "candidata_old_id", None)).first()
            if cand_old is not None:
                log_candidata_action(
                    action_type="REEMPLAZO_CLOSE",
                    candidata=cand_old,
                    summary=f"Reemplazo cerrado (sale candidata) en solicitud {s.codigo_solicitud or s.id}",
                    metadata={"reemplazo_id": r.id, "solicitud_id": s.id, "cliente_id": s.cliente_id, "candidata_new_id": cand_new.fila},
                    success=True,
                )
            log_candidata_action(
                action_type="REEMPLAZO_CLOSE",
                candidata=cand_new,
                summary=f"Reemplazo cerrado (entra candidata) en solicitud {s.codigo_solicitud or s.id}",
                metadata={"reemplazo_id": r.id, "solicitud_id": s.id, "cliente_id": s.cliente_id},
                success=True,
            )
            flash('Reemplazo finalizado correctamente.', 'success')
            return redirect(url_for('admin.detalle_cliente', cliente_id=s.cliente_id))

        except InvariantConflictError as inv_exc:
            db.session.rollback()
            if _admin_async_wants_json():
                return jsonify(_admin_async_payload(
                    success=False,
                    message=str(inv_exc) or "Conflicto de estado de candidata.",
                    category='warning',
                    redirect_url=None,
                    error_code=getattr(inv_exc, "code", "conflict"),
                )), 409
            flash(str(inv_exc) or "Conflicto de estado de candidata.", "warning")
            return redirect(url_for('admin.detalle_cliente', cliente_id=s.cliente_id))
        except StaleDataError:
            db.session.rollback()
            msg = 'La solicitud cambió por otra sesión. Recarga e intenta nuevamente.'
            if _admin_async_wants_json():
                return jsonify(_admin_async_payload(
                    success=False,
                    message=msg,
                    category='warning',
                    redirect_url=None,
                    error_code='conflict',
                )), 409
            flash(msg, 'warning')
            return redirect(url_for('admin.detalle_cliente', cliente_id=s.cliente_id))
        except Exception as e:
            db.session.rollback()
            # ✅ Mostrar el error real en terminal para poder corregirlo de una vez
            try:
                import traceback
                print('ERROR finalizar_reemplazo:', repr(e))
                traceback.print_exc()
            except Exception:
                pass
            _audit_log(
                action_type="REEMPLAZO_CERRAR",
                entity_type="Solicitud",
                entity_id=s.id,
                summary=f"Fallo finalizando reemplazo para solicitud {s.id}",
                metadata={"reemplazo_id": r.id},
                success=False,
                error=str(e),
            )
            if 'cand_new' in locals() and cand_new is not None:
                log_candidata_action(
                    action_type="REEMPLAZO_CLOSE",
                    candidata=cand_new,
                    summary=f"Fallo cerrando reemplazo para solicitud {s.id}",
                    metadata={"reemplazo_id": r.id, "solicitud_id": s.id, "cliente_id": s.cliente_id},
                    success=False,
                    error=str(e),
                )
            if _admin_async_wants_json():
                return jsonify(_admin_async_payload(
                    success=False,
                    message='Error al finalizar el reemplazo.',
                    category='danger',
                    redirect_url=None,
                    error_code='server_error',
                )), 500
            flash('Error al finalizar el reemplazo.', 'danger')

    elif request.method == 'POST':
        if _admin_async_wants_json():
            return jsonify(_admin_async_payload(
                success=False,
                message='Revisa los campos marcados en rojo.',
                category='warning',
                redirect_url=None,
                error_code='invalid_input',
            )), 400
        flash('Revisa los campos marcados en rojo.', 'danger')

    return render_template(
        'admin/reemplazo_fin.html',
        form=form,
        solicitud=s,
        reemplazo=r,
        q=q,
        pick_name=pick_name,
        candidatas=candidatas,
        form_idempotency_key=form_idempotency_key,
    )


def _search_candidatas_reemplazo(search_text: str, *, limit: int = 25):
    txt = (search_text or "").strip()
    if not txt:
        return []
    base_query = Candidata.query.filter(candidatas_activas_filter(Candidata))
    return search_candidatas_limited(
        txt,
        limit=limit,
        base_query=base_query,
        minimal_fields=False,
        order_mode="nombre_asc",
        log_label="reemplazo_quick_close",
    )


@admin_bp.route('/candidatas/reemplazo/quick-search', methods=['GET'])
@login_required
@staff_required
def candidatas_reemplazo_quick_search():
    q = (request.args.get("q") or "").strip()
    try:
        limit = int((request.args.get("limit") or 20))
    except Exception:
        limit = 20
    limit = max(5, min(limit, 50))
    if len(q) < 2:
        return jsonify({"success": True, "items": []}), 200

    rows = _search_candidatas_reemplazo(q, limit=limit)
    items = []
    for c in rows:
        cid = int(getattr(c, "fila", 0) or 0)
        if cid <= 0:
            continue
        nombre = (getattr(c, "nombre_completo", "") or "").strip()
        codigo = (getattr(c, "codigo", "") or "").strip()
        cedula = (getattr(c, "cedula", "") or "").strip()
        label_parts = [nombre or f"ID {cid}"]
        extras = []
        if codigo:
            extras.append(f"Código: {codigo}")
        if cedula:
            extras.append(f"Cédula: {cedula}")
        if extras:
            label_parts.append(" · ".join(extras))
        items.append({
            "id": cid,
            "nombre": nombre,
            "codigo": codigo,
            "cedula": cedula,
            "label": " — ".join(label_parts),
        })
    return jsonify({"success": True, "items": items}), 200


@admin_bp.route('/solicitudes/<int:s_id>/reemplazos/<int:reemplazo_id>/cancelar', methods=['POST'])
@login_required
@admin_required
@admin_action_limit(bucket="reemplazos", max_actions=15, window_sec=60)
def cancelar_reemplazo(s_id, reemplazo_id):
    s = Solicitud.query.filter_by(id=s_id).first_or_404()
    r = Reemplazo.query.filter_by(id=reemplazo_id, solicitud_id=s_id).first_or_404()
    next_url = (request.form.get("next") or request.args.get("next") or "").strip()
    dynamic_target = (request.form.get("_async_target") or request.args.get("_async_target") or "").strip()
    fallback = (
        url_for("admin.listar_solicitudes")
        if dynamic_target == "#solicitudesAsyncRegion" or dynamic_target.startswith("#solicitudReemplazoActionsAsyncRegion-")
        else (
            url_for("admin.detalle_cliente", cliente_id=s.cliente_id) + f"#sol-{s.id}"
            if dynamic_target == "#clienteSolicitudesAsyncRegion" or dynamic_target.startswith("#clienteSolicitudReemplazoActionsAsyncRegion-")
            else url_for("admin.detalle_solicitud", cliente_id=s.cliente_id, id=s.id)
        )
    )

    def _action_response(
        *,
        ok: bool,
        message: str,
        category: str,
        http_status: int = 200,
        error_code: str | None = None,
    ):
        replace_html = None
        update_target = dynamic_target
        if _admin_async_wants_json():
            if ok:
                update_target = _reemplazo_parent_async_target(dynamic_target) or dynamic_target
            else:
                replace_html = _render_reemplazo_actions_region(
                    solicitud_id=int(s.id),
                    dynamic_target=dynamic_target,
                    next_url=next_url,
                )
        return _solicitudes_list_action_response(
            ok=ok,
            message=message,
            category=category,
            next_url=next_url,
            fallback=fallback,
            http_status=http_status,
            error_code=error_code,
            replace_html=replace_html,
            update_target=update_target,
        )

    blocked_resp = _admin_block_sensitive_action(
        scope="admin_reemplazo_cancel",
        entity_type="Solicitud",
        entity_id=s.id,
        limit=25,
        window_seconds=600,
        min_interval_seconds=1,
        summary=f"Bloqueo de cancelación de reemplazo por patrón de abuso (solicitud {s.id})",
        next_url=next_url,
        fallback=fallback,
    )
    if blocked_resp is not None:
        if _admin_async_wants_json():
            return _action_response(
                ok=False,
                message="Demasiadas acciones seguidas. Espera un momento e intenta nuevamente.",
                category="warning",
                http_status=429,
                error_code="rate_limit",
            )
        return blocked_resp

    expected_version = _expected_row_version()
    if _critical_concurrency_guards_enabled() and expected_version is not None:
        current_version = int(getattr(s, "row_version", 0) or 0)
        if int(expected_version) != current_version:
            return _action_response(
                ok=False,
                message='La solicitud cambió mientras trabajabas. Recarga y vuelve a intentar.',
                category='warning',
                http_status=409,
                error_code='conflict',
            )

    idem_row, duplicate = _claim_idempotency(
        scope="admin_reemplazo_cancel",
        entity_type="Solicitud",
        entity_id=s.id,
        action="cancelar_reemplazo",
    )
    if duplicate:
        if _idempotency_request_conflict(idem_row):
            return _action_response(
                ok=False,
                message=_idempotency_conflict_message(),
                category='warning',
                http_status=409,
                error_code='idempotency_conflict',
            )
        prev_status = int(getattr(idem_row, "response_status", 0) or 0)
        if 200 <= prev_status < 300:
            return _action_response(
                ok=True,
                message='Acción ya aplicada previamente.',
                category='info',
            )
        return _action_response(
            ok=False,
            message='Solicitud duplicada detectada. Espera y vuelve a intentar.',
            category='warning',
            http_status=409,
            error_code='conflict',
        )

    if (s.estado or "").strip().lower() != "reemplazo":
        return _action_response(
            ok=False,
            message="La solicitud no tiene un reemplazo activo para cancelar.",
            category="warning",
            http_status=409,
            error_code="conflict",
        )

    if getattr(r, "fecha_fin_reemplazo", None):
        if _admin_noop_repeat_blocked(
            scope="admin_reemplazo_cancel",
            entity_type="Reemplazo",
            entity_id=r.id,
            state="cerrado",
            summary=f"Intento repetido de cancelar reemplazo ya cerrado ({r.id})",
        ):
            return _action_response(
                ok=False,
                message="Acción bloqueada temporalmente: este reemplazo ya está cerrado.",
                category="warning",
                http_status=429,
                error_code="rate_limit",
            )
        return _action_response(
            ok=False,
            message="Este reemplazo ya está cerrado.",
            category="warning",
            http_status=409,
            error_code="conflict",
        )

    try:
        r.cerrar_reemplazo()
        if hasattr(r, "oportunidad_nueva"):
            r.oportunidad_nueva = False

        estado_restore = (getattr(r, "estado_previo_solicitud", None) or "").strip().lower()
        if estado_restore not in ("proceso", "activa", "pagada", "cancelada"):
            estado_restore = "activa"
        _set_solicitud_estado(s, estado_restore)

        _emit_domain_outbox_event(
            event_type="REEMPLAZO_CANCELADO",
            aggregate_type="Solicitud",
            aggregate_id=s.id,
            aggregate_version=(int(getattr(s, "row_version", 0) or 0) + 1),
            payload={
                "solicitud_id": int(s.id),
                "reemplazo_id": int(r.id),
                "estado_restaurado": estado_restore,
            },
        )
        _set_idempotency_response(idem_row, status=200, code="ok")
        db.session.commit()
        _audit_log(
            action_type="REEMPLAZO_CANCELAR",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Reemplazo cancelado para solicitud {s.codigo_solicitud or s.id}",
            metadata={"reemplazo_id": r.id},
        )
        return _action_response(
            ok=True,
            message="Reemplazo cancelado correctamente.",
            category="success",
        )
    except StaleDataError:
        db.session.rollback()
        return _action_response(
            ok=False,
            message='La solicitud cambió por otra sesión. Recarga e intenta nuevamente.',
            category='warning',
            http_status=409,
            error_code='conflict',
        )
    except Exception:
        db.session.rollback()
        _audit_log(
            action_type="REEMPLAZO_CANCELAR",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Fallo cancelando reemplazo para solicitud {s.id}",
            metadata={"reemplazo_id": r.id},
            success=False,
            error="No se pudo cancelar el reemplazo.",
        )
        return _action_response(
            ok=False,
            message="No se pudo cancelar el reemplazo.",
            category="danger",
            http_status=500,
            error_code="server_error",
        )


@admin_bp.route('/solicitudes/<int:s_id>/reemplazos/<int:reemplazo_id>/cerrar_asignando', methods=['POST'])
@login_required
@staff_required
@admin_action_limit(bucket="reemplazos", max_actions=20, window_sec=60)
def cerrar_reemplazo_asignando(s_id, reemplazo_id):
    s = Solicitud.query.filter_by(id=s_id).first_or_404()
    r = Reemplazo.query.filter_by(id=reemplazo_id, solicitud_id=s_id).first_or_404()
    next_url = (request.form.get("next") or request.args.get("next") or "").strip()
    dynamic_target = (request.form.get("_async_target") or request.args.get("_async_target") or "").strip()
    fallback = (
        url_for("admin.listar_solicitudes")
        if dynamic_target == "#solicitudesAsyncRegion" or dynamic_target.startswith("#solicitudReemplazoActionsAsyncRegion-")
        else (
            url_for("admin.detalle_cliente", cliente_id=s.cliente_id) + f"#sol-{s.id}"
            if dynamic_target == "#clienteSolicitudesAsyncRegion" or dynamic_target.startswith("#clienteSolicitudReemplazoActionsAsyncRegion-")
            else url_for("admin.detalle_solicitud", cliente_id=s.cliente_id, id=s.id)
        )
    )

    def _action_response(
        *,
        ok: bool,
        message: str,
        category: str,
        http_status: int = 200,
        error_code: str | None = None,
    ):
        replace_html = None
        update_target = dynamic_target
        if _admin_async_wants_json():
            if ok:
                update_target = _reemplazo_parent_async_target(dynamic_target) or dynamic_target
            else:
                replace_html = _render_reemplazo_actions_region(
                    solicitud_id=int(s.id),
                    dynamic_target=dynamic_target,
                    next_url=next_url,
                )
        return _solicitudes_list_action_response(
            ok=ok,
            message=message,
            category=category,
            next_url=next_url,
            fallback=fallback,
            http_status=http_status,
            error_code=error_code,
            replace_html=replace_html,
            update_target=update_target,
        )

    blocked_resp = _admin_block_sensitive_action(
        scope="admin_reemplazo_close_assign",
        entity_type="Solicitud",
        entity_id=s.id,
        limit=30,
        window_seconds=600,
        min_interval_seconds=1,
        summary=f"Bloqueo de cierre de reemplazo por patrón de abuso (solicitud {s.id})",
        next_url=next_url,
        fallback=fallback,
    )
    if blocked_resp is not None:
        if _admin_async_wants_json():
            return _action_response(
                ok=False,
                message="Demasiadas acciones seguidas. Espera un momento e intenta nuevamente.",
                category="warning",
                http_status=429,
                error_code="rate_limit",
            )
        return blocked_resp

    expected_version = _expected_row_version()
    if _critical_concurrency_guards_enabled() and expected_version is not None:
        current_version = int(getattr(s, "row_version", 0) or 0)
        if int(expected_version) != current_version:
            return _action_response(
                ok=False,
                message='La solicitud cambió mientras trabajabas. Recarga y vuelve a intentar.',
                category='warning',
                http_status=409,
                error_code='conflict',
            )

    idem_row, duplicate = _claim_idempotency(
        scope="admin_reemplazo_close_assign",
        entity_type="Solicitud",
        entity_id=s.id,
        action="cerrar_reemplazo_asignando",
    )
    if duplicate:
        if _idempotency_request_conflict(idem_row):
            return _action_response(
                ok=False,
                message=_idempotency_conflict_message(),
                category='warning',
                http_status=409,
                error_code='idempotency_conflict',
            )
        prev_status = int(getattr(idem_row, "response_status", 0) or 0)
        if 200 <= prev_status < 300:
            return _action_response(
                ok=True,
                message='Acción ya aplicada previamente.',
                category='info',
            )
        return _action_response(
            ok=False,
            message='Solicitud duplicada detectada. Espera y vuelve a intentar.',
            category='warning',
            http_status=409,
            error_code='conflict',
        )

    if (s.estado or "").strip().lower() != "reemplazo":
        return _action_response(
            ok=False,
            message="La solicitud no tiene un reemplazo activo para culminar.",
            category="warning",
            http_status=409,
            error_code="conflict",
        )

    if getattr(r, "fecha_fin_reemplazo", None):
        if _admin_noop_repeat_blocked(
            scope="admin_reemplazo_close_assign",
            entity_type="Reemplazo",
            entity_id=r.id,
            state="cerrado",
            summary=f"Intento repetido de cerrar reemplazo ya cerrado ({r.id})",
        ):
            return _action_response(
                ok=False,
                message="Acción bloqueada temporalmente: este reemplazo ya está cerrado.",
                category="warning",
                http_status=429,
                error_code="rate_limit",
            )
        return _action_response(
            ok=False,
            message="Este reemplazo ya está cerrado.",
            category="warning",
            http_status=409,
            error_code="conflict",
        )

    try:
        nueva_id = int((request.form.get("candidata_new_id") or "").strip())
    except Exception:
        nueva_id = 0
    if nueva_id <= 0:
        return _action_response(
            ok=False,
            message="Debes indicar la candidata nueva para cerrar el reemplazo.",
            category="warning",
            http_status=400,
            error_code="invalid_input",
        )

    cand_new = Candidata.query.filter_by(fila=nueva_id).first()
    if not cand_new:
        return _action_response(
            ok=False,
            message="La candidata seleccionada no existe.",
            category="danger",
            http_status=404,
            error_code="not_found",
        )

    if int(getattr(s, "candidata_id", 0) or 0) == int(cand_new.fila):
        return _action_response(
            ok=False,
            message="Esa candidata ya está asignada en la solicitud.",
            category="info",
            http_status=409,
            error_code="conflict",
        )

    blocked = assert_candidata_no_descalificada(
        cand_new,
        action="asignar a solicitud",
        redirect_endpoint="admin.detalle_solicitud",
        redirect_kwargs={"cliente_id": s.cliente_id, "id": s.id},
    )
    if blocked is not None:
        if _admin_async_wants_json():
            return _action_response(
                ok=False,
                message="La candidata seleccionada no está disponible para esta acción.",
                category="warning",
                http_status=409,
                error_code="conflict",
            )
        return blocked

    try:
        r.cerrar_reemplazo(cand_new.fila)

        s.candidata_id = cand_new.fila
        estado_restore = (getattr(r, "estado_previo_solicitud", None) or "").strip().lower()
        if estado_restore in ("", "reemplazo", "cancelada"):
            estado_restore = "activa"
        _set_solicitud_estado(s, estado_restore)

        _sync_solicitud_candidatas_after_assignment(s, cand_new.fila)
        _mark_candidata_estado(cand_new, "trabajando")

        _emit_domain_outbox_event(
            event_type="REEMPLAZO_CERRADO_ASIGNANDO",
            aggregate_type="Solicitud",
            aggregate_id=s.id,
            aggregate_version=(int(getattr(s, "row_version", 0) or 0) + 1),
            payload={
                "solicitud_id": int(s.id),
                "reemplazo_id": int(r.id),
                "candidata_old_id": int(getattr(r, "candidata_old_id", 0) or 0) or None,
                "candidata_new_id": int(cand_new.fila),
                "estado_restaurado": estado_restore,
            },
        )
        _set_idempotency_response(idem_row, status=200, code="ok")
        db.session.commit()
        _audit_log(
            action_type="REEMPLAZO_CERRAR",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Reemplazo cerrado asignando candidata en solicitud {s.codigo_solicitud or s.id}",
            metadata={"reemplazo_id": r.id, "candidata_new_id": cand_new.fila},
        )
        cand_old = Candidata.query.filter_by(fila=getattr(r, "candidata_old_id", None)).first()
        if cand_old is not None:
            log_candidata_action(
                action_type="REEMPLAZO_CLOSE",
                candidata=cand_old,
                summary=f"Reemplazo cerrado (sale candidata) en solicitud {s.codigo_solicitud or s.id}",
                metadata={"reemplazo_id": r.id, "solicitud_id": s.id, "cliente_id": s.cliente_id, "candidata_new_id": cand_new.fila},
                success=True,
            )
        log_candidata_action(
            action_type="REEMPLAZO_CLOSE",
            candidata=cand_new,
            summary=f"Reemplazo cerrado (entra candidata) en solicitud {s.codigo_solicitud or s.id}",
            metadata={"reemplazo_id": r.id, "solicitud_id": s.id, "cliente_id": s.cliente_id},
            success=True,
        )
        return _action_response(
            ok=True,
            message="Reemplazo cerrado y nueva candidata asignada.",
            category="success",
        )
    except InvariantConflictError as inv_exc:
        db.session.rollback()
        return _action_response(
            ok=False,
            message=str(inv_exc) or "Conflicto de estado de candidata.",
            category="warning",
            http_status=409,
            error_code=getattr(inv_exc, "code", "conflict"),
        )
    except StaleDataError:
        db.session.rollback()
        return _action_response(
            ok=False,
            message='La solicitud cambió por otra sesión. Recarga e intenta nuevamente.',
            category='warning',
            http_status=409,
            error_code='conflict',
        )
    except Exception:
        db.session.rollback()
        _audit_log(
            action_type="REEMPLAZO_CERRAR",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Fallo cerrando reemplazo por asignación en solicitud {s.id}",
            metadata={"reemplazo_id": r.id, "candidata_new_id": cand_new.fila if cand_new else None},
            success=False,
            error="No se pudo cerrar el reemplazo.",
        )
        if cand_new is not None:
            log_candidata_action(
                action_type="REEMPLAZO_CLOSE",
                candidata=cand_new,
                summary=f"Fallo cerrando reemplazo por asignación en solicitud {s.id}",
                metadata={"reemplazo_id": r.id, "solicitud_id": s.id, "cliente_id": s.cliente_id},
                success=False,
                error="No se pudo cerrar el reemplazo.",
            )
        return _action_response(
            ok=False,
            message="No se pudo cerrar el reemplazo.",
            category="danger",
            http_status=500,
            error_code="server_error",
        )

def _is_missing_contract_table_error(exc: OperationalError) -> bool:
    text = str(getattr(exc, "orig", exc) or "").lower()
    has_table_name = "contratos_digitales" in text
    has_missing_hint = ("no such table" in text) or ("does not exist" in text) or ("undefined table" in text)
    return has_table_name and has_missing_hint


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


def _solicitud_last_activity_at(solicitud):
    def _to_dt(raw):
        if not raw:
            return None
        if isinstance(raw, datetime):
            return raw
        try:
            return datetime(raw.year, raw.month, raw.day)
        except Exception:
            return None

    for attr in ("fecha_ultima_actividad", "fecha_ultima_modificacion", "updated_at", "fecha_solicitud"):
        val = _to_dt(getattr(solicitud, attr, None))
        if val:
            return val
    return None


def _solicitud_priority_snapshot(solicitud, *, now_dt: datetime):
    estado = str(getattr(solicitud, "estado", "") or "").strip().lower()
    estado_desde, _source, _estimated = resolve_solicitud_estado_priority_anchor(solicitud)
    days = days_in_state(estado_desde, now_dt=now_dt)
    label = priority_band_for_days(days)
    if estado not in ("activa", "reemplazo"):
        label = "normal"
    rank = priority_band_rank(label)
    score = int(rank * 25)
    is_stagnant = bool(label in {"urgente", "critica"})
    return score, label, is_stagnant, int((days or 0) * 24)


_OPERATIVE_STALE_ACTIVITY_HOURS = 72
_OPERATIVE_ESPERA_PAGO_PROLONGADA_DIAS = 5


def _solicitud_needs_followup_today(*, is_stagnant: bool, priority_label: str) -> bool:
    label = str(priority_label or "").strip().lower()
    return bool(is_stagnant or label in {"urgente", "critica"})


def _manual_followup_snapshot(fecha_manual, *, today_rd):
    if not fecha_manual:
        return {
            "state": "normal",
            "label": "Sin fecha",
            "badge_class": "bg-secondary",
            "hint": "No hay seguimiento manual programado.",
            "date_value": "",
        }
    if fecha_manual > today_rd:
        state = "pendiente"
        label = "Pendiente"
        badge_class = "bg-primary"
        hint = "Seguimiento manual programado para una fecha futura."
    elif fecha_manual == today_rd:
        state = "hoy"
        label = "Hoy"
        badge_class = "bg-info text-dark"
        hint = "Seguimiento manual programado para hoy."
    else:
        state = "vencida"
        label = "Vencida"
        badge_class = "bg-danger"
        hint = "La fecha manual de seguimiento ya pasó."
    return {
        "state": state,
        "label": label,
        "badge_class": badge_class,
        "hint": hint,
        "date_value": fecha_manual.isoformat(),
    }


def _resolve_solicitud_last_actor_user_ids(solicitud_ids: list[int]) -> dict[int, int | None]:
    ids = [int(sid) for sid in (solicitud_ids or []) if int(sid or 0) > 0]
    if not ids:
        return {}
    id_strings = [str(sid) for sid in sorted(set(ids))]
    try:
        latest_subq = (
            db.session.query(
                StaffAuditLog.entity_id.label("entity_id"),
                func.max(StaffAuditLog.id).label("max_id"),
            )
            .filter(func.lower(StaffAuditLog.entity_type) == "solicitud")
            .filter(StaffAuditLog.entity_id.in_(id_strings))
            .group_by(StaffAuditLog.entity_id)
            .subquery()
        )
        rows = (
            db.session.query(StaffAuditLog.entity_id, StaffAuditLog.actor_user_id)
            .join(latest_subq, StaffAuditLog.id == latest_subq.c.max_id)
            .all()
        )
    except Exception:
        return {}

    resolved: dict[int, int | None] = {}
    for row in (rows or []):
        raw_entity_id = str(getattr(row, "entity_id", "") or "").strip()
        if not raw_entity_id.isdigit():
            continue
        sid = int(raw_entity_id)
        raw_actor = getattr(row, "actor_user_id", None)
        try:
            resolved[sid] = int(raw_actor) if raw_actor is not None else None
        except Exception:
            resolved[sid] = None
    return resolved


def _staff_username_map(user_ids: list[int]) -> dict[int, str]:
    ids = sorted({int(uid) for uid in (user_ids or []) if int(uid or 0) > 0})
    if not ids:
        return {}
    try:
        rows = (
            StaffUser.query
            .options(load_only(StaffUser.id, StaffUser.username))
            .filter(StaffUser.id.in_(ids))
            .all()
        )
    except Exception:
        return {}
    out: dict[int, str] = {}
    for row in (rows or []):
        rid = int(getattr(row, "id", 0) or 0)
        if rid <= 0:
            continue
        out[rid] = str(getattr(row, "username", "") or f"Staff #{rid}")
    return out


@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/<int:id>')
@login_required
@staff_required
def detalle_solicitud(cliente_id, id):
    with _p1c1_perf_scope("solicitud_detail") as perf_done:
        # Carga completa para evitar N+1 en plantilla
        s = (Solicitud.query
             .options(
                 joinedload(Solicitud.reemplazos).joinedload(Reemplazo.candidata_new),
                 joinedload(Solicitud.candidata)
             )
             .filter_by(id=id, cliente_id=cliente_id)
             .first_or_404())

        # Historial de envíos (inicial + reemplazos válidos)
        envios = []
        if s.candidata:
            envios.append({
                'tipo':     'Envío inicial',
                'candidata': s.candidata,
                'fecha':     s.fecha_solicitud
            })

        reemplazos_ordenados = sorted(list(s.reemplazos or []),
                                      key=lambda r: r.fecha_inicio_reemplazo or r.created_at or datetime.min)
        reemplazo_activo = _active_reemplazo_for_solicitud(s)
        for idx, r in enumerate(reemplazos_ordenados, start=1):
            if r.candidata_new:
                envios.append({
                    'tipo':     f'Reemplazo {idx}',
                    'candidata': r.candidata_new,
                    'fecha':     r.fecha_inicio_reemplazo or r.created_at
                })

        # Cancelaciones
        cancelaciones = []
        if s.estado == 'cancelada' and s.fecha_cancelacion:
            cancelaciones.append({
                'fecha':  s.fecha_cancelacion,
                'motivo': s.motivo_cancelacion
            })

        # 👉 Resumen listo para enviar al cliente (helper que ya te di antes)
        resumen_cliente = build_resumen_cliente_solicitud(s)
        pasaje_copy_text = _pasaje_operativo_phrase_from_solicitud(s)
        pasaje_copy_mode, pasaje_copy_other_text = read_pasaje_mode_text(
            pasaje_aporte=getattr(s, "pasaje_aporte", False),
            detalles_servicio=getattr(s, "detalles_servicio", None),
            nota_cliente=getattr(s, "nota_cliente", ""),
        )
        role = (
            str(getattr(current_user, "role", "") or "").strip().lower()
            or str(session.get("role", "") or "").strip().lower()
        )
        is_admin_role = role in ("owner", "admin")
        contracts_schema_ready = True
        latest_contract = None
        contract_history = []
        latest_signed_contract = None
        try:
            contract_rows = (
                ContratoDigital.query.options(
                    load_only(
                        ContratoDigital.id,
                        ContratoDigital.solicitud_id,
                        ContratoDigital.cliente_id,
                        ContratoDigital.version,
                        ContratoDigital.estado,
                        ContratoDigital.snapshot_fijado_at,
                        ContratoDigital.token_expira_at,
                        ContratoDigital.enviado_at,
                        ContratoDigital.primer_visto_at,
                        ContratoDigital.firmado_at,
                        ContratoDigital.pdf_final_size_bytes,
                        ContratoDigital.contenido_snapshot_json,
                        ContratoDigital.anulado_at,
                        ContratoDigital.created_at,
                        ContratoDigital.updated_at,
                    )
                )
                .filter_by(solicitud_id=s.id)
                .order_by(ContratoDigital.version.desc(), ContratoDigital.id.desc())
                .all()
            )
            if contract_rows:
                latest_contract = contract_rows[0]
                latest_signed_contract = next(
                    (
                        row for row in contract_rows
                        if (row.firmado_at is not None) or (str(row.estado or "").strip().lower() == "firmado")
                    ),
                    None,
                )

                links = session.get("contract_links")
                links = links if isinstance(links, dict) else {}
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
        except OperationalError as exc:
            db.session.rollback()
            if _is_missing_contract_table_error(exc):
                contracts_schema_ready = False
            else:
                raise
        contrato_expirado = _is_contract_expired(latest_contract)
        latest_contract_link = None
        if latest_contract is not None:
            links = session.get("contract_links")
            if isinstance(links, dict):
                latest_contract_link = links.get(str(latest_contract.id))
        now_utc = utc_now_naive()
        _score, priority_label, is_stagnant, _hours = _solicitud_priority_snapshot(s, now_dt=now_utc)
        needs_followup_today = _solicitud_needs_followup_today(
            is_stagnant=is_stagnant,
            priority_label=priority_label,
        )
        manual_followup = _manual_followup_snapshot(
            getattr(s, "fecha_seguimiento_manual", None),
            today_rd=rd_today(),
        )
        solicitud_detail_url = url_for("admin.detalle_solicitud", cliente_id=s.cliente_id, id=s.id)

        html = render_template(
            'admin/solicitud_detail.html',
            solicitud      = s,
            envios         = envios,
            cancelaciones  = cancelaciones,
            reemplazos     = reemplazos_ordenados,
            reemplazo_activo=reemplazo_activo,
            resumen_cliente=resumen_cliente,
            pasaje_copy_text=pasaje_copy_text,
            pasaje_copy_mode=pasaje_copy_mode,
            pasaje_copy_other_text=pasaje_copy_other_text,
            is_admin_role=is_admin_role,
            latest_contract=latest_contract,
            contract_history=contract_history,
            latest_signed_contract=latest_signed_contract,
            contracts_schema_ready=contracts_schema_ready,
            contrato_expirado=contrato_expirado,
            contract_effective_state=_contract_effective_state(latest_contract, contrato_expirado=contrato_expirado),
            contract_snapshot_summary=_contract_snapshot_summary(
                getattr(latest_contract, "contenido_snapshot_json", None) if latest_contract else None
            ),
            latest_contract_link=latest_contract_link,
            now_utc=now_utc,
            priority_label_operativa=priority_label,
            needs_followup_today=needs_followup_today,
            manual_followup=manual_followup,
            solicitud_detail_url=solicitud_detail_url,
            chat_feature_enabled=_chat_enabled(),
        )
        return perf_done(html, html_bytes=len(html.encode("utf-8")), extra={"mode": "full"})


@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/<int:id>/_summary')
@login_required
@staff_required
def solicitud_detail_summary_fragment(cliente_id, id):
    with _p1c1_perf_scope("solicitud_detail_summary_fragment") as perf_done:
        solicitud = (
            Solicitud.query
            .options(joinedload(Solicitud.candidata))
            .filter_by(id=id, cliente_id=cliente_id)
            .first_or_404()
        )
        html = render_template(
            'admin/_solicitud_detail_summary_region.html',
            solicitud=solicitud,
            chat_feature_enabled=_chat_enabled(),
        )
        response = make_response(html, 200)
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        response.headers["X-Async-Fragment-Region"] = "solicitudSummaryAsyncRegion"
        return perf_done(response, html_bytes=len(html.encode("utf-8")), extra={"mode": "fragment_summary"})


@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/<int:id>/_operativa_core')
@login_required
@staff_required
def solicitud_detail_operativa_core_fragment(cliente_id, id):
    with _p1c1_perf_scope("solicitud_detail_operativa_core_fragment") as perf_done:
        solicitud = (
            Solicitud.query
            .options(joinedload(Solicitud.candidata))
            .filter_by(id=id, cliente_id=cliente_id)
            .first_or_404()
        )
        now_utc = utc_now_naive()
        _score, priority_label, is_stagnant, _hours = _solicitud_priority_snapshot(solicitud, now_dt=now_utc)
        manual_followup = _manual_followup_snapshot(
            getattr(solicitud, "fecha_seguimiento_manual", None),
            today_rd=rd_today(),
        )
        solicitud_detail_url = url_for("admin.detalle_solicitud", cliente_id=solicitud.cliente_id, id=solicitud.id)
        html = render_template(
            'admin/_solicitud_operativa_core_region.html',
            solicitud=solicitud,
            async_feedback=None,
            now_utc=now_utc,
            priority_label_operativa=priority_label,
            needs_followup_today=_solicitud_needs_followup_today(
                is_stagnant=is_stagnant,
                priority_label=priority_label,
            ),
            manual_followup=manual_followup,
            solicitud_detail_url=solicitud_detail_url,
        )
        response = make_response(html, 200)
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        response.headers["X-Async-Fragment-Region"] = "solicitudOperativaCoreAsyncRegion"
        return perf_done(response, html_bytes=len(html.encode("utf-8")), extra={"mode": "fragment_operativa_core"})

from datetime import datetime, timedelta
from sqlalchemy.orm import joinedload
from sqlalchemy import func

def _solicitudes_prioridad_next_step(*, estado_raw: str, is_stagnant: bool):
    estado = (estado_raw or '').strip().lower()
    if estado == 'proceso':
        return 'Activar solicitud', 'La solicitud aún no está en operación activa.', True
    if estado == 'activa':
        if is_stagnant:
            return 'Escalar hoy', 'Lleva demasiados días activa sin cierre.', True
        return 'Seguimiento activo', 'Mantener el ritmo para cerrar antes de 7 días.', True
    if estado == 'reemplazo':
        if is_stagnant:
            return 'Destrabar reemplazo', 'El reemplazo ya cruza umbral urgente/crítico.', True
        return 'Gestionar reemplazo', 'Hay un reemplazo abierto en curso.', True
    if estado == 'pagada':
        return 'Cerrada', 'Proceso finalizado sin acción operativa pendiente.', False
    if estado == 'cancelada':
        return 'Sin acción', 'Solicitud cancelada, sin gestión operativa pendiente.', False
    return 'Sin acción', 'Estado sin acción operativa definida en esta vista.', False


def _solicitud_list_primary_cta(
    *,
    solicitud,
    priority_label: str,
    is_stagnant: bool,
    needs_followup_today: bool,
    manual_followup_state: str,
):
    estado = str(getattr(solicitud, "estado", "") or "").strip().lower()
    detail_url = url_for("admin.detalle_solicitud", cliente_id=solicitud.cliente_id, id=solicitud.id)

    if estado == "proceso":
        return {
            "label": "Activar solicitud",
            "kind": "form",
            "form_action": url_for("admin.activar_solicitud_directa", id=solicitud.id),
            "href": "",
            "btn_class": "btn-success",
            "help": "Pasarla a activa para iniciar operación.",
        }
    if estado == "espera_pago":
        return {
            "label": "Gestionar pago",
            "kind": "link",
            "form_action": "",
            "href": url_for(
                "admin.registrar_pago",
                cliente_id=solicitud.cliente_id,
                id=solicitud.id,
                next=detail_url,
            ),
            "btn_class": "btn-warning text-dark",
            "help": "Pago pendiente para destrabar avance.",
        }
    if estado == "reemplazo":
        return {
            "label": "Revisar reemplazo",
            "kind": "link",
            "form_action": "",
            "href": detail_url,
            "btn_class": "btn-warning text-dark",
            "help": "Hay reemplazo abierto; revisar resolución.",
        }
    if estado == "activa":
        requires_followup = bool(
            is_stagnant
            or needs_followup_today
            or manual_followup_state in {"hoy", "vencida"}
            or str(priority_label or "").strip().lower() in {"urgente", "critica"}
        )
        if requires_followup:
            return {
                "label": "Dar seguimiento",
                "kind": "link",
                "form_action": "",
                "href": detail_url,
                "btn_class": "btn-primary",
                "help": "Requiere seguimiento operativo hoy.",
            }
    return {
        "label": "Ver detalle",
        "kind": "link",
        "form_action": "",
        "href": detail_url,
        "btn_class": "btn-outline-primary",
        "help": "Abrir detalle para coordinar el siguiente paso.",
    }


def _solicitud_list_operativa_badge(*, estado_raw: str, priority_label: str, manual_followup_state: str):
    estado = (estado_raw or "").strip().lower()
    label = (priority_label or "").strip().lower()
    manual = (manual_followup_state or "").strip().lower()

    if estado == "espera_pago":
        return "Pago pendiente", "text-bg-warning"
    if estado == "reemplazo":
        return "Reemplazo en curso", "text-bg-warning"
    if estado == "proceso":
        return "Pendiente de activación", "text-bg-info"
    if estado == "pagada":
        return "Cerrada pagada", "text-bg-success"
    if estado == "cancelada":
        return "Cancelada", "text-bg-secondary"
    if manual in {"hoy", "vencida"}:
        return "Seguimiento hoy", "text-bg-danger" if manual == "vencida" else "text-bg-info"
    if label == "critica":
        return "Crítica", "text-bg-danger"
    if label == "urgente":
        return "Urgente", "text-bg-warning"
    if label == "atencion":
        return "Atención", "text-bg-info"
    return "En ritmo", "text-bg-success"


def _solicitud_operativa_signal(
    *,
    estado_raw: str,
    priority_label: str,
    manual_followup_state: str,
    days_in_state: int,
    has_active_reemplazo: bool,
    last_activity_at: datetime | None,
    now_dt: datetime,
):
    estado = (estado_raw or "").strip().lower()
    label = (priority_label or "").strip().lower()
    manual = (manual_followup_state or "").strip().lower()
    days = int(days_in_state or 0)
    stale_by_days = bool(label in {"urgente", "critica"} or days >= 7)

    stale_by_activity = False
    if isinstance(last_activity_at, datetime):
        try:
            stale_by_activity = (now_dt - last_activity_at) >= timedelta(hours=_OPERATIVE_STALE_ACTIVITY_HOURS)
        except Exception:
            stale_by_activity = False
    espera_pago_prolongada = bool(
        estado == "espera_pago"
        and days >= int(_OPERATIVE_ESPERA_PAGO_PROLONGADA_DIAS)
    )
    reemplazo_sin_seguimiento = bool(
        estado == "reemplazo"
        and bool(has_active_reemplazo)
        and manual in {"", "normal"}
    )

    if manual == "vencida":
        return {
            "code": "vencida",
            "label": "Vencida",
            "badge_class": "text-bg-danger",
            "hint": "La fecha manual de seguimiento ya venció.",
            "rank": 100,
        }
    if reemplazo_sin_seguimiento:
        return {
            "code": "reemplazo_sin_seguimiento",
            "label": "Reemplazo sin seguimiento",
            "badge_class": "text-bg-danger",
            "hint": "Hay reemplazo activo sin fecha manual de seguimiento.",
            "rank": 95,
        }
    if manual == "hoy":
        return {
            "code": "atencion_hoy",
            "label": "Atención hoy",
            "badge_class": "text-bg-info",
            "hint": "Tiene seguimiento manual programado para hoy.",
            "rank": 90,
        }
    if espera_pago_prolongada:
        return {
            "code": "espera_pago_prolongada",
            "label": "Espera de pago prolongada",
            "badge_class": "text-bg-danger",
            "hint": f"Lleva {days} días en espera de pago; conviene escalar registro/cobro hoy.",
            "rank": 85,
        }
    if estado in {"activa", "proceso", "reemplazo"} and (stale_by_days or stale_by_activity):
        return {
            "code": "sin_movimiento",
            "label": "Sin movimiento",
            "badge_class": "text-bg-warning",
            "hint": "Acumula tiempo sin avance operativo relevante.",
            "rank": 80,
        }
    if estado == "espera_pago":
        return {
            "code": "esperando_pago",
            "label": "Esperando pago",
            "badge_class": "text-bg-warning",
            "hint": "Pendiente de registro de pago para continuar.",
            "rank": 70,
        }
    if estado == "reemplazo" and bool(has_active_reemplazo):
        return {
            "code": "reemplazo_activo",
            "label": "Reemplazo activo",
            "badge_class": "text-bg-warning",
            "hint": "Existe reemplazo abierto en curso.",
            "rank": 60,
        }
    if estado == "pagada":
        return {
            "code": "cerrada_pagada",
            "label": "Cerrada pagada",
            "badge_class": "text-bg-success",
            "hint": "Solicitud completada y pagada.",
            "rank": 10,
        }
    if estado == "cancelada":
        return {
            "code": "cerrada_cancelada",
            "label": "Cancelada",
            "badge_class": "text-bg-secondary",
            "hint": "Solicitud cancelada sin acción operativa pendiente.",
            "rank": 5,
        }
    return {
        "code": "estable",
        "label": "Estable",
        "badge_class": "text-bg-success",
        "hint": "Sin alertas operativas críticas en este momento.",
        "rank": 40,
    }


def _solicitud_quick_summary(solicitud) -> str:
    parts: list[str] = []
    modalidad = str(getattr(solicitud, "modalidad_trabajo", "") or "").strip()
    horario = str(getattr(solicitud, "horario", "") or "").strip()
    rutas = str(getattr(solicitud, "rutas_cercanas", "") or "").strip()
    ciudad = str(getattr(solicitud, "ciudad_sector", "") or "").strip()
    nota = str(getattr(solicitud, "nota_cliente", "") or "").strip()

    if modalidad:
        parts.append(f"Modalidad: {modalidad}")
    if horario:
        parts.append(f"Horario: {horario}")
    if rutas:
        parts.append(f"Ruta: {rutas}")
    if ciudad:
        parts.append(f"Zona: {ciudad}")
    if nota:
        sanitized = " ".join(nota.split())
        if len(sanitized) > 160:
            sanitized = sanitized[:157].rstrip() + "..."
        parts.append(f"Nota cliente: {sanitized}")

    if not parts:
        return "Sin resumen operativo adicional."
    return " | ".join(parts)


_SOLICITUDES_TRIAGE_DEFS = (
    ("urgentes", "Urgentes"),
    ("vencidas", "Vencidas"),
    ("atencion_hoy", "Atención hoy"),
    ("espera_pago", "Espera de pago"),
    ("espera_pago_prolongada", "Espera pago prolongada"),
    ("reemplazo", "Reemplazo"),
    ("reemplazo_sin_seguimiento", "Reemplazo sin seguimiento"),
    ("sin_movimiento", "Sin movimiento"),
    ("activas_estables", "Activas estables"),
    ("sin_responsable", "Sin responsable"),
)
_SOLICITUDES_TRIAGE_CODES = {code for code, _label in _SOLICITUDES_TRIAGE_DEFS}
_SOLICITUDES_TRIAGE_LABELS = {code: label for code, label in _SOLICITUDES_TRIAGE_DEFS}


def _solicitudes_query_supports_sql_triage(query) -> bool:
    return bool(
        query is not None
        and hasattr(query, "session")
        and hasattr(query, "with_entities")
        and hasattr(query, "limit")
        and hasattr(query, "offset")
    )


def _solicitudes_triage_sql_parts(*, now_dt: datetime, today_rd):
    # PostgreSQL no soporta lower(enum) directamente; primero convertimos a texto.
    estado_norm = func.lower(cast(Solicitud.estado, db.String()))
    estado_activa = estado_norm == "activa"
    estado_reemplazo = estado_norm == "reemplazo"
    estado_espera_pago = estado_norm == "espera_pago"
    estado_proceso = estado_norm == "proceso"

    manual_vencida = Solicitud.fecha_seguimiento_manual.isnot(None) & (Solicitud.fecha_seguimiento_manual < today_rd)
    manual_hoy = Solicitud.fecha_seguimiento_manual.isnot(None) & (Solicitud.fecha_seguimiento_manual == today_rd)
    manual_normal = Solicitud.fecha_seguimiento_manual.is_(None)

    active_reemplazo_exists = (
        db.session.query(Reemplazo.id)
        .filter(
            Reemplazo.solicitud_id == Solicitud.id,
            Reemplazo.fecha_inicio_reemplazo.isnot(None),
            Reemplazo.fecha_fin_reemplazo.is_(None),
        )
        .exists()
    )
    active_reemplazo_started_at = (
        db.session.query(func.max(Reemplazo.fecha_inicio_reemplazo))
        .filter(
            Reemplazo.solicitud_id == Solicitud.id,
            Reemplazo.fecha_inicio_reemplazo.isnot(None),
            Reemplazo.fecha_fin_reemplazo.is_(None),
        )
        .correlate(Solicitud)
        .scalar_subquery()
    )

    state_anchor = case(
        (
            and_(estado_reemplazo, active_reemplazo_started_at.isnot(None)),
            active_reemplazo_started_at,
        ),
        (Solicitud.estado_actual_desde.isnot(None), Solicitud.estado_actual_desde),
        (
            and_(estado_activa, Solicitud.fecha_inicio_seguimiento.isnot(None)),
            Solicitud.fecha_inicio_seguimiento,
        ),
        (
            and_(estado_activa, Solicitud.fecha_ultima_modificacion.isnot(None)),
            Solicitud.fecha_ultima_modificacion,
        ),
        (
            and_(estado_activa, Solicitud.fecha_solicitud.isnot(None)),
            Solicitud.fecha_solicitud,
        ),
        (
            and_(estado_reemplazo, Solicitud.fecha_ultima_modificacion.isnot(None)),
            Solicitud.fecha_ultima_modificacion,
        ),
        (
            and_(estado_reemplazo, Solicitud.fecha_solicitud.isnot(None)),
            Solicitud.fecha_solicitud,
        ),
        else_=None,
    )
    # Hardening: algunos builds no exponen `fecha_ultima_actividad` en Solicitud.
    last_activity_candidates = []
    for attr in ("fecha_ultima_actividad", "fecha_ultima_modificacion", "updated_at", "fecha_solicitud"):
        col = getattr(Solicitud, attr, None)
        if col is not None:
            last_activity_candidates.append(col)
    last_activity_at = func.coalesce(*last_activity_candidates) if last_activity_candidates else None
    stale_by_days = and_(state_anchor.isnot(None), state_anchor <= (now_dt - timedelta(days=7)))
    stale_by_activity = (
        and_(last_activity_at.isnot(None), last_activity_at <= (now_dt - timedelta(hours=_OPERATIVE_STALE_ACTIVITY_HOURS)))
        if last_activity_at is not None
        else False
    )

    espera_pago_prolongada = and_(
        estado_espera_pago,
        state_anchor.isnot(None),
        state_anchor <= (now_dt - timedelta(days=int(_OPERATIVE_ESPERA_PAGO_PROLONGADA_DIAS))),
    )
    reemplazo_sin_seguimiento = and_(
        estado_reemplazo,
        active_reemplazo_exists,
        manual_normal,
    )
    sin_movimiento_base = and_(
        or_(estado_activa, estado_proceso, estado_reemplazo),
        or_(stale_by_days, stale_by_activity),
    )
    sin_movimiento_signal = and_(
        sin_movimiento_base,
        ~manual_vencida,
        ~reemplazo_sin_seguimiento,
        ~manual_hoy,
        ~espera_pago_prolongada,
    )
    activas_estables = and_(
        estado_activa,
        ~manual_vencida,
        ~manual_hoy,
        ~sin_movimiento_base,
    )

    latest_audit_id = (
        db.session.query(func.max(StaffAuditLog.id))
        .filter(
            func.lower(StaffAuditLog.entity_type) == "solicitud",
            StaffAuditLog.entity_id == cast(Solicitud.id, db.String(64)),
        )
        .correlate(Solicitud)
        .scalar_subquery()
    )
    latest_has_actor = (
        db.session.query(StaffAuditLog.id)
        .filter(
            StaffAuditLog.id == latest_audit_id,
            StaffAuditLog.actor_user_id.isnot(None),
        )
        .exists()
    )
    sin_responsable = ~latest_has_actor

    signal_rank = case(
        (manual_vencida, 100),
        (reemplazo_sin_seguimiento, 95),
        (manual_hoy, 90),
        (espera_pago_prolongada, 85),
        (sin_movimiento_signal, 80),
        (estado_espera_pago, 70),
        (and_(estado_reemplazo, active_reemplazo_exists), 60),
        (estado_norm == "pagada", 10),
        (estado_norm == "cancelada", 5),
        else_=40,
    )

    clauses = {
        "urgentes": or_(
            manual_vencida,
            manual_hoy,
            sin_movimiento_signal,
            espera_pago_prolongada,
            reemplazo_sin_seguimiento,
        ),
        "vencidas": manual_vencida,
        "atencion_hoy": manual_hoy,
        "espera_pago": estado_espera_pago,
        "espera_pago_prolongada": and_(
            espera_pago_prolongada,
            ~manual_vencida,
            ~manual_hoy,
        ),
        "reemplazo": estado_reemplazo,
        "reemplazo_sin_seguimiento": reemplazo_sin_seguimiento,
        "sin_movimiento": sin_movimiento_signal,
        "activas_estables": activas_estables,
        "sin_responsable": sin_responsable,
    }
    return {
        "clauses": clauses,
        "signal_rank": signal_rank,
        "state_anchor": state_anchor,
    }


def _query_count_distinct_solicitudes(query) -> int:
    try:
        return int(
            query
            .with_entities(func.count(func.distinct(Solicitud.id)))
            .scalar()
            or 0
        )
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        return int(query.order_by(None).count() or 0)


def _solicitudes_triage_options_sql(*, base_query, selected: str, now_dt: datetime, today_rd):
    selected_code = str(selected or "").strip().lower()
    parts = _solicitudes_triage_sql_parts(now_dt=now_dt, today_rd=today_rd)
    clauses = parts["clauses"]
    code_order = [code for code, _label in _SOLICITUDES_TRIAGE_DEFS]
    counts_by_code = {code: 0 for code in code_order}
    all_count = 0

    try:
        entities = [func.count(func.distinct(Solicitud.id)).label("all_count")]
        for code in code_order:
            clause = clauses.get(code)
            if clause is None:
                entities.append(func.count(func.distinct(case((False, Solicitud.id), else_=None))).label(f"count_{code}"))
                continue
            entities.append(
                func.count(
                    func.distinct(
                        case((clause, Solicitud.id), else_=None)
                    )
                ).label(f"count_{code}")
            )
        row = base_query.order_by(None).with_entities(*entities).first()
        all_count = int(getattr(row, "all_count", 0) or 0) if row is not None else 0
        if row is not None:
            for code in code_order:
                counts_by_code[code] = int(getattr(row, f"count_{code}", 0) or 0)
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        all_count = _query_count_distinct_solicitudes(base_query)
        for code in code_order:
            clause = clauses.get(code)
            if clause is not None:
                counts_by_code[code] = _query_count_distinct_solicitudes(base_query.filter(clause))

    options = [{
        "code": "",
        "label": "Todas",
        "count": int(all_count),
        "active": selected_code == "",
    }]
    for code, label in _SOLICITUDES_TRIAGE_DEFS:
        options.append({
            "code": code,
            "label": label,
            "count": int(counts_by_code.get(code, 0) or 0),
            "active": selected_code == code,
        })
    return options


def _solicitud_has_responsable(item) -> bool:
    return str(getattr(item, "last_actor_label", "") or "").strip().lower() not in {"", "sin responsable"}


def _solicitud_matches_triage(item, triage_code: str) -> bool:
    triage = str(triage_code or "").strip().lower()
    if not triage:
        return True

    estado = str(getattr(item, "estado", "") or "").strip().lower()
    signal = str(getattr(item, "operational_signal_code", "") or "").strip().lower()
    manual = str(getattr(item, "manual_followup_state", "") or "").strip().lower()
    has_responsable = _solicitud_has_responsable(item)

    if triage == "urgentes":
        return signal in {
            "vencida",
            "atencion_hoy",
            "sin_movimiento",
            "espera_pago_prolongada",
            "reemplazo_sin_seguimiento",
        }
    if triage == "vencidas":
        return manual == "vencida" or signal == "vencida"
    if triage == "atencion_hoy":
        return manual == "hoy" or signal == "atencion_hoy"
    if triage == "espera_pago":
        return estado == "espera_pago"
    if triage == "espera_pago_prolongada":
        return signal == "espera_pago_prolongada"
    if triage == "reemplazo":
        return estado == "reemplazo"
    if triage == "reemplazo_sin_seguimiento":
        return signal == "reemplazo_sin_seguimiento"
    if triage == "sin_movimiento":
        return signal == "sin_movimiento"
    if triage == "activas_estables":
        return estado == "activa" and signal == "estable"
    if triage == "sin_responsable":
        return not has_responsable
    return True


def _solicitudes_triage_options(*, rows: list, selected: str) -> list[dict]:
    selected_code = str(selected or "").strip().lower()
    all_count = len(rows or [])
    options = [{
        "code": "",
        "label": "Todas",
        "count": int(all_count),
        "active": selected_code == "",
    }]
    for code, label in _SOLICITUDES_TRIAGE_DEFS:
        count = sum(1 for row in (rows or []) if _solicitud_matches_triage(row, code))
        options.append({
            "code": code,
            "label": label,
            "count": int(count),
            "active": selected_code == code,
        })
    return options


class _SolicitudPrioridadVM:
    __slots__ = (
        "_s",
        "priority_score",
        "priority_label",
        "is_stagnant",
        "estado_desde_at",
        "days_in_state",
        "priority_message",
        "priority_source",
        "priority_source_estimated",
        "next_step_label",
        "next_step_reason",
        "next_step_actionable",
        "needs_followup_today",
        "manual_followup_state",
        "manual_followup_label",
        "manual_followup_badge_class",
        "manual_followup_hint",
        "operational_signal_code",
        "operational_signal_label",
        "operational_signal_badge_class",
        "operational_signal_hint",
        "operational_signal_rank",
        "last_activity_at",
        "last_actor_label",
    )

    def __init__(
        self,
        s,
        *,
        score: int,
        label: str,
        stagnant: bool,
        now_dt: datetime,
        today_rd,
        last_actor_label: str = "Sin responsable",
    ):
        self._s = s
        self.priority_score = int(score)
        self.priority_label = label
        self.is_stagnant = bool(stagnant)
        estado_desde, source, estimated = resolve_solicitud_estado_priority_anchor(s)
        self.estado_desde_at = estado_desde
        self.days_in_state = int(days_in_state(estado_desde, now_dt=now_dt) or 0)
        self.priority_message = priority_message_for_solicitud(
            estado=str(getattr(s, "estado", "") or ""),
            days_in_current_state=self.days_in_state,
        )
        self.priority_source = source
        self.priority_source_estimated = bool(estimated)
        next_label, next_reason, next_actionable = _solicitudes_prioridad_next_step(
            estado_raw=getattr(s, 'estado', ''),
            is_stagnant=self.is_stagnant,
        )
        self.next_step_label = next_label
        self.next_step_reason = next_reason
        self.next_step_actionable = bool(next_actionable)
        self.needs_followup_today = _solicitud_needs_followup_today(
            is_stagnant=self.is_stagnant,
            priority_label=self.priority_label,
        )
        manual_followup = _manual_followup_snapshot(
            getattr(s, "fecha_seguimiento_manual", None),
            today_rd=today_rd,
        )
        self.manual_followup_state = manual_followup["state"]
        self.manual_followup_label = manual_followup["label"]
        self.manual_followup_badge_class = manual_followup["badge_class"]
        self.manual_followup_hint = manual_followup["hint"]
        self.last_activity_at = _solicitud_last_activity_at(s)
        self.last_actor_label = str(last_actor_label or "Sin responsable")
        has_active_reemplazo = bool(_active_reemplazo_for_solicitud(s))
        signal = _solicitud_operativa_signal(
            estado_raw=getattr(s, "estado", ""),
            priority_label=self.priority_label,
            manual_followup_state=self.manual_followup_state,
            days_in_state=self.days_in_state,
            has_active_reemplazo=has_active_reemplazo,
            last_activity_at=self.last_activity_at,
            now_dt=now_dt,
        )
        self.operational_signal_code = str(signal.get("code", "estable"))
        self.operational_signal_label = str(signal.get("label", "Estable"))
        self.operational_signal_badge_class = str(signal.get("badge_class", "text-bg-success"))
        self.operational_signal_hint = str(signal.get("hint", ""))
        self.operational_signal_rank = int(signal.get("rank", 0) or 0)

    def __getattr__(self, name):
        return getattr(self._s, name)


class _SolicitudOperativaListVM:
    __slots__ = (
        "_s",
        "priority_label",
        "is_stagnant",
        "days_in_state",
        "priority_message",
        "needs_followup_today",
        "manual_followup_state",
        "manual_followup_label",
        "manual_followup_badge_class",
        "manual_followup_hint",
        "last_activity_at",
        "last_actor_label",
        "next_step_label",
        "next_step_reason",
        "operativa_badge_label",
        "operativa_badge_class",
        "primary_cta_label",
        "primary_cta_kind",
        "primary_cta_href",
        "primary_cta_form_action",
        "primary_cta_btn_class",
        "primary_cta_help",
        "operational_signal_code",
        "operational_signal_label",
        "operational_signal_badge_class",
        "operational_signal_hint",
        "operational_signal_rank",
        "quick_summary",
    )

    def __init__(self, s, *, now_dt: datetime, today_rd, last_actor_label: str, has_active_reemplazo: bool):
        self._s = s
        score, label, stagnant, _hours = _solicitud_priority_snapshot(s, now_dt=now_dt)
        self.priority_label = label
        self.is_stagnant = bool(stagnant)
        _ = score
        estado_desde, _source, _estimated = resolve_solicitud_estado_priority_anchor(s)
        self.days_in_state = int(days_in_state(estado_desde, now_dt=now_dt) or 0)
        self.priority_message = priority_message_for_solicitud(
            estado=str(getattr(s, "estado", "") or ""),
            days_in_current_state=self.days_in_state,
        )
        self.needs_followup_today = _solicitud_needs_followup_today(
            is_stagnant=self.is_stagnant,
            priority_label=self.priority_label,
        )
        manual_followup = _manual_followup_snapshot(
            getattr(s, "fecha_seguimiento_manual", None),
            today_rd=today_rd,
        )
        self.manual_followup_state = manual_followup["state"]
        self.manual_followup_label = manual_followup["label"]
        self.manual_followup_badge_class = manual_followup["badge_class"]
        self.manual_followup_hint = manual_followup["hint"]
        self.last_activity_at = _solicitud_last_activity_at(s)
        self.last_actor_label = str(last_actor_label or "Sin responsable")
        self.next_step_label, self.next_step_reason, _next_actionable = _solicitudes_prioridad_next_step(
            estado_raw=getattr(s, "estado", ""),
            is_stagnant=self.is_stagnant,
        )
        self.operativa_badge_label, self.operativa_badge_class = _solicitud_list_operativa_badge(
            estado_raw=getattr(s, "estado", ""),
            priority_label=self.priority_label,
            manual_followup_state=self.manual_followup_state,
        )
        cta = _solicitud_list_primary_cta(
            solicitud=s,
            priority_label=self.priority_label,
            is_stagnant=self.is_stagnant,
            needs_followup_today=self.needs_followup_today,
            manual_followup_state=self.manual_followup_state,
        )
        self.primary_cta_label = cta["label"]
        self.primary_cta_kind = cta["kind"]
        self.primary_cta_href = cta["href"]
        self.primary_cta_form_action = cta["form_action"]
        self.primary_cta_btn_class = cta["btn_class"]
        self.primary_cta_help = cta["help"]
        signal = _solicitud_operativa_signal(
            estado_raw=getattr(s, "estado", ""),
            priority_label=self.priority_label,
            manual_followup_state=self.manual_followup_state,
            days_in_state=self.days_in_state,
            has_active_reemplazo=bool(has_active_reemplazo),
            last_activity_at=self.last_activity_at,
            now_dt=now_dt,
        )
        self.operational_signal_code = str(signal.get("code", "estable"))
        self.operational_signal_label = str(signal.get("label", "Estable"))
        self.operational_signal_badge_class = str(signal.get("badge_class", "text-bg-success"))
        self.operational_signal_hint = str(signal.get("hint", ""))
        self.operational_signal_rank = int(signal.get("rank", 0) or 0)
        self.quick_summary = _solicitud_quick_summary(s)

    def __getattr__(self, name):
        return getattr(self._s, name)


class _SolicitudOperativaTriageScanRow:
    """Fila liviana para fallback in-memory de triage.

    Calcula solo datos necesarios para filtros/conteos/orden, y difiere la VM
    completa de render para las filas de la página actual.
    """

    __slots__ = (
        "_s",
        "id",
        "estado",
        "last_actor_user_id",
        "last_actor_label",
        "days_in_state",
        "manual_followup_state",
        "priority_label",
        "operational_signal_code",
        "operational_signal_rank",
        "active_reemplazo",
    )

    def __init__(self, s, *, now_dt: datetime, today_rd, last_actor_user_id: int | None, active_reemplazo):
        self._s = s
        self.id = int(getattr(s, "id", 0) or 0)
        self.estado = str(getattr(s, "estado", "") or "")
        self.last_actor_user_id = int(last_actor_user_id) if last_actor_user_id is not None else None
        self.last_actor_label = "Sin responsable" if self.last_actor_user_id is None else f"Staff #{self.last_actor_user_id}"
        self.active_reemplazo = active_reemplazo

        estado_norm = str(self.estado or "").strip().lower()
        estado_desde = None
        repl_inicio = getattr(active_reemplazo, "fecha_inicio_reemplazo", None) if active_reemplazo is not None else None
        if estado_norm == "reemplazo" and repl_inicio is not None:
            estado_desde = repl_inicio
        if estado_desde is None:
            estado_desde = getattr(s, "estado_actual_desde", None)
        if estado_desde is None and estado_norm == "activa":
            estado_desde = (
                getattr(s, "fecha_inicio_seguimiento", None)
                or getattr(s, "fecha_ultima_modificacion", None)
                or getattr(s, "fecha_solicitud", None)
            )
        if estado_desde is None and estado_norm == "reemplazo":
            estado_desde = (
                getattr(s, "fecha_ultima_modificacion", None)
                or getattr(s, "fecha_solicitud", None)
            )

        days_value = int(days_in_state(estado_desde, now_dt=now_dt) or 0)
        label = priority_band_for_days(days_value)
        if estado_norm not in {"activa", "reemplazo"}:
            label = "normal"
        self.priority_label = str(label or "")
        self.days_in_state = days_value
        manual_followup = _manual_followup_snapshot(
            getattr(s, "fecha_seguimiento_manual", None),
            today_rd=today_rd,
        )
        self.manual_followup_state = str(manual_followup.get("state", "") or "")
        signal = _solicitud_operativa_signal(
            estado_raw=getattr(s, "estado", ""),
            priority_label=self.priority_label,
            manual_followup_state=self.manual_followup_state,
            days_in_state=self.days_in_state,
            has_active_reemplazo=bool(active_reemplazo),
            last_activity_at=_solicitud_last_activity_at(s),
            now_dt=now_dt,
        )
        self.operational_signal_code = str(signal.get("code", "estable") or "estable")
        self.operational_signal_rank = int(signal.get("rank", 0) or 0)

    def __getattr__(self, name):
        return getattr(self._s, name)


def _solicitudes_prioridad_as_int(name: str, default: int, *, lo: int | None = None, hi: int | None = None) -> int:
    try:
        value = int(request.args.get(name, default) or default)
    except Exception:
        value = default
    if lo is not None:
        value = max(lo, value)
    if hi is not None:
        value = min(hi, value)
    return value


def _solicitudes_prioridad_context(*, include_rows: bool = True, include_filter_options: bool = True):
    q = (request.args.get('q') or '').strip()
    estado = (request.args.get('estado') or '').strip().lower()
    prioridad = (request.args.get('prioridad') or '').strip().lower()
    responsable_raw = (request.args.get('responsable') or '').strip().lower()
    estancadas = (request.args.get('estancadas') or '').strip() in ('1', 'true', 'True', 'yes', 'si')
    page = _solicitudes_prioridad_as_int('page', 1, lo=1, hi=10_000)
    per_page = _solicitudes_prioridad_as_int('per_page', 25, lo=10, hi=200)
    ahora = utc_now_naive()
    today_value = rd_today()

    allowed_states = ['activa', 'reemplazo', 'proceso', 'espera_pago', 'pagada']
    default_states = ['activa', 'reemplazo']
    allowed_priority = ['critica', 'urgente', 'atencion', 'normal']
    if estado not in allowed_states:
        estado = ''
    if prioridad not in allowed_priority:
        prioridad = ''
    responsible_filter_actor_id: int | None = None
    responsible_filter_unassigned = False
    if responsable_raw in {"sin_responsable", "sin-responsable", "none"}:
        responsable_raw = "none"
        responsible_filter_unassigned = True
    elif responsable_raw:
        try:
            parsed_actor_id = int(responsable_raw)
            if parsed_actor_id > 0:
                responsible_filter_actor_id = parsed_actor_id
                responsable_raw = str(parsed_actor_id)
            else:
                responsable_raw = ''
        except Exception:
            responsable_raw = ''
    estados_filtrados = [estado] if estado else list(default_states)

    solicitud_attrs = []
    for attr in (
        'id',
        'cliente_id',
        'codigo_solicitud',
        'ciudad_sector',
        'estado',
        'row_version',
        'fecha_solicitud',
        'estado_actual_desde',
        'fecha_ultima_actividad',
        'fecha_ultima_modificacion',
        'updated_at',
        'fecha_seguimiento_manual',
        'rutas_cercanas',
        'modalidad_trabajo',
        'horario',
        'candidata_id',
    ):
        if hasattr(Solicitud, attr):
            solicitud_attrs.append(getattr(Solicitud, attr))

    cliente_attrs = []
    for attr in ('id', 'nombre_completo', 'codigo'):
        if hasattr(Cliente, attr):
            cliente_attrs.append(getattr(Cliente, attr))

    query = Solicitud.query
    options_list = []
    if solicitud_attrs:
        options_list.append(load_only(*solicitud_attrs))
    try:
        if cliente_attrs:
            options_list.append(joinedload(Solicitud.cliente).load_only(*cliente_attrs))
        else:
            options_list.append(joinedload(Solicitud.cliente))
        options_list.append(selectinload(Solicitud.reemplazos).load_only(
            Reemplazo.id,
            Reemplazo.fecha_inicio_reemplazo,
            Reemplazo.fecha_fin_reemplazo,
        ))
    except Exception:
        pass
    if options_list:
        query = query.options(*options_list)
    query = query.filter(Solicitud.estado.in_(estados_filtrados))

    if q:
        like = f"%{q}%"
        filtros = []
        for attr in ('codigo_solicitud', 'ciudad_sector', 'rutas_cercanas', 'modalidad_trabajo', 'horario'):
            if hasattr(Solicitud, attr):
                filtros.append(getattr(Solicitud, attr).ilike(like))

        try:
            if hasattr(Cliente, 'nombre_completo'):
                filtros.append(Cliente.nombre_completo.ilike(like))
            if hasattr(Cliente, 'codigo'):
                filtros.append(Cliente.codigo.ilike(like))
            if hasattr(Cliente, 'telefono'):
                filtros.append(Cliente.telefono.ilike(like))
        except Exception:
            pass

        try:
            if hasattr(Candidata, 'nombre_completo'):
                filtros.append(Candidata.nombre_completo.ilike(like))
            if hasattr(Candidata, 'cedula'):
                filtros.append(Candidata.cedula.ilike(like))
            if hasattr(Candidata, 'codigo'):
                filtros.append(Candidata.codigo.ilike(like))
            if hasattr(Candidata, 'numero_telefono'):
                filtros.append(Candidata.numero_telefono.ilike(like))
        except Exception:
            pass

        if filtros:
            try:
                query = query.join(Cliente, Solicitud.cliente_id == Cliente.id)
            except Exception:
                pass
            try:
                if hasattr(Solicitud, 'candidata_id') and hasattr(Candidata, 'fila'):
                    query = query.outerjoin(Candidata, Solicitud.candidata_id == Candidata.fila)
            except Exception:
                pass
            query = query.filter(or_(*filtros))

    rows = query.all()
    row_ids = [int(getattr(s, "id", 0) or 0) for s in (rows or []) if int(getattr(s, "id", 0) or 0) > 0]
    last_actor_by_solicitud = _resolve_solicitud_last_actor_user_ids(row_ids)
    actor_ids = sorted({
        int(uid) for uid in (last_actor_by_solicitud.values() or [])
        if uid is not None and int(uid or 0) > 0
    })
    username_by_actor = _staff_username_map(actor_ids)

    selected_rows = []
    filter_options_raw: dict[int | None, dict] = {}
    for s in (rows or []):
        score, label, stale, _hours = _solicitud_priority_snapshot(s, now_dt=ahora)
        if prioridad and label != prioridad:
            continue
        if estancadas and not stale:
            continue
        sid = int(getattr(s, "id", 0) or 0)
        actor_user_id = last_actor_by_solicitud.get(sid)
        if actor_user_id is not None:
            try:
                actor_user_id = int(actor_user_id)
            except Exception:
                actor_user_id = None
        if actor_user_id is not None and actor_user_id <= 0:
            actor_user_id = None
        opt_key = actor_user_id if actor_user_id is not None else None
        if opt_key not in filter_options_raw:
            filter_options_raw[opt_key] = {
                "value": str(opt_key) if opt_key is not None else "none",
                "label": username_by_actor.get(opt_key, f"Staff #{opt_key}") if opt_key is not None else "Sin responsable",
                "count": 0,
            }
        filter_options_raw[opt_key]["count"] += 1
        if responsible_filter_unassigned and actor_user_id is not None:
            continue
        if responsible_filter_actor_id is not None and actor_user_id != responsible_filter_actor_id:
            continue
        actor_label = "Sin responsable"
        if actor_user_id is not None and actor_user_id > 0:
            actor_label = username_by_actor.get(actor_user_id, f"Staff #{actor_user_id}")
        estado_desde, _source, _estimated = resolve_solicitud_estado_priority_anchor(s)
        days_state = int(days_in_state(estado_desde, now_dt=ahora) or 0)
        needs_followup_today = _solicitud_needs_followup_today(
            is_stagnant=bool(stale),
            priority_label=label,
        )
        manual_followup = _manual_followup_snapshot(
            getattr(s, "fecha_seguimiento_manual", None),
            today_rd=today_value,
        )
        signal = _solicitud_operativa_signal(
            estado_raw=getattr(s, "estado", ""),
            priority_label=label,
            manual_followup_state=manual_followup["state"],
            days_in_state=days_state,
            has_active_reemplazo=bool(_active_reemplazo_for_solicitud(s)),
            last_activity_at=_solicitud_last_activity_at(s),
            now_dt=ahora,
        )
        selected_rows.append({
            "s": s,
            "id": sid,
            "score": int(score),
            "label": str(label or ""),
            "stale": bool(stale),
            "days": int(days_state),
            "actor_user_id": actor_user_id,
            "actor_label": actor_label,
            "needs_followup_today": bool(needs_followup_today),
            "manual_followup_state": str(manual_followup["state"] or ""),
            "signal_code": str(signal.get("code", "") or ""),
        })

    selected_rows.sort(
        key=lambda row: (
            -priority_band_rank(row["label"]),
            -int(row["days"] or 0),
            int(row["id"] or 0),
        )
    )

    total = len(selected_rows)
    responsible_summary_raw: dict[int | None, dict] = {}

    count_activa = 0
    count_reemplazo = 0
    count_critica = 0
    count_urgente = 0
    count_atencion = 0
    count_normal = 0
    count_sin_movimiento = 0
    count_espera_pago_prolongada = 0
    count_reemplazo_sin_seguimiento = 0

    for row in selected_rows:
        actor_user_id = row["actor_user_id"]
        key = actor_user_id if actor_user_id is not None else None
        if key not in responsible_summary_raw:
            responsible_summary_raw[key] = {
                "actor_user_id": key,
                "responsable_label": username_by_actor.get(key, f"Staff #{key}") if key is not None else "Sin responsable",
                "total": 0,
                "stagnant": 0,
                "high_priority": 0,
                "reemplazo": 0,
                "needs_today": 0,
                "overdue": 0,
                "espera_pago_prolongada": 0,
                "reemplazo_sin_seguimiento": 0,
            }
        bucket = responsible_summary_raw[key]
        bucket["total"] += 1
        current_label = str(row["label"] or "").strip().lower()
        if current_label == 'critica':
            count_critica += 1
            bucket["stagnant"] += 1
            bucket["high_priority"] += 1
        elif current_label == 'urgente':
            count_urgente += 1
            bucket["stagnant"] += 1
            bucket["high_priority"] += 1
        elif current_label == 'atencion':
            count_atencion += 1
        else:
            count_normal += 1
        current_estado = str(getattr(row["s"], 'estado', '') or '').strip().lower()
        if current_estado == 'activa':
            count_activa += 1
        elif current_estado == 'reemplazo':
            count_reemplazo += 1
            bucket["reemplazo"] += 1
        if bool(row["needs_followup_today"]):
            bucket["needs_today"] += 1
        if str(row["manual_followup_state"] or "").strip().lower() == "vencida":
            bucket["overdue"] += 1
        signal_code = str(row["signal_code"] or "").strip().lower()
        if signal_code == "sin_movimiento":
            count_sin_movimiento += 1
        elif signal_code == "espera_pago_prolongada":
            count_espera_pago_prolongada += 1
            bucket["espera_pago_prolongada"] += 1
        elif signal_code == "reemplazo_sin_seguimiento":
            count_reemplazo_sin_seguimiento += 1
            bucket["reemplazo_sin_seguimiento"] += 1

    responsible_summary = sorted(
        (value for key, value in responsible_summary_raw.items() if key is not None),
        key=lambda row: (-int(row["total"]), str(row["responsable_label"]).lower()),
    )
    if None in responsible_summary_raw:
        responsible_summary.append(responsible_summary_raw[None])
    for row in responsible_summary:
        total_value = int(row.get("total", 0) or 0)
        riesgo_value = (
            int(row.get("high_priority", 0) or 0)
            + int(row.get("overdue", 0) or 0)
            + int(row.get("espera_pago_prolongada", 0) or 0)
            + int(row.get("reemplazo_sin_seguimiento", 0) or 0)
        )
        row["risk_total"] = int(riesgo_value)
        if total_value >= 12 or riesgo_value >= 6:
            row["load_tier"] = "alta"
        elif total_value >= 7 or riesgo_value >= 3:
            row["load_tier"] = "media"
        else:
            row["load_tier"] = "normal"

    responsible_filter_options = []
    if include_filter_options:
        responsible_filter_options = sorted(
            (value for key, value in filter_options_raw.items() if key is not None),
            key=lambda row: (-int(row["count"]), str(row["label"]).lower()),
        )
        if None in filter_options_raw:
            responsible_filter_options.append(filter_options_raw[None])
        total_filter_count = sum(int(row.get("count", 0) or 0) for row in responsible_filter_options)
        responsible_filter_options = [{
            "value": "",
            "label": "Todos",
            "count": int(total_filter_count),
        }] + responsible_filter_options

    assigned_responsibles = [row for row in responsible_summary if row.get("actor_user_id") is not None]
    top_load_label = ""
    top_load_value = 0
    top_risk_label = ""
    top_risk_value = 0
    if assigned_responsibles:
        top_load_row = max(
            assigned_responsibles,
            key=lambda row: (int(row.get("total", 0) or 0), -int(row.get("actor_user_id", 0) or 0)),
        )
        top_risk_row = max(
            assigned_responsibles,
            key=lambda row: (int(row.get("risk_total", 0) or 0), int(row.get("high_priority", 0) or 0)),
        )
        top_load_label = str(top_load_row.get("responsable_label", "") or "")
        top_load_value = int(top_load_row.get("total", 0) or 0)
        top_risk_label = str(top_risk_row.get("responsable_label", "") or "")
        top_risk_value = int(top_risk_row.get("risk_total", 0) or 0)

    start = (page - 1) * per_page
    end = start + per_page
    paged = []
    if include_rows:
        page_rows = selected_rows[start:end]
        paged = [
            _SolicitudPrioridadVM(
                row["s"],
                score=row["score"],
                label=row["label"],
                stagnant=row["stale"],
                now_dt=ahora,
                today_rd=today_value,
                last_actor_label=row["actor_label"],
            )
            for row in page_rows
        ]

    return dict(
        solicitudes=paged,
        q=q,
        estado=estado,
        prioridad=prioridad,
        responsable=responsable_raw,
        estancadas=estancadas,
        today_iso=today_value.isoformat() if isinstance(today_value, date) else "",
        page=page,
        per_page=per_page,
        total=total,
        total_count=total,
        count_activa=count_activa,
        count_reemplazo=count_reemplazo,
        count_critica=count_critica,
        count_urgente=count_urgente,
        count_atencion=count_atencion,
        count_normal=count_normal,
        count_sin_movimiento=count_sin_movimiento,
        count_espera_pago_prolongada=count_espera_pago_prolongada,
        count_reemplazo_sin_seguimiento=count_reemplazo_sin_seguimiento,
        count_sin_responsable=int((responsible_summary_raw.get(None) or {}).get("total", 0) or 0),
        top_load_label=top_load_label,
        top_load_value=top_load_value,
        top_risk_label=top_risk_label,
        top_risk_value=top_risk_value,
        responsible_summary=responsible_summary,
        responsible_filter_options=responsible_filter_options,
        has_more=(end < total) if include_rows else False,
        allowed_states=allowed_states,
        allowed_priority=allowed_priority,
    )


@admin_bp.route('/solicitudes/prioridad')
@login_required
@admin_required
def solicitudes_prioridad():
    with _p1c1_perf_scope("solicitudes_prioridad") as perf_done:
        list_ctx = _solicitudes_prioridad_context()
        if _admin_async_wants_json():
            html = render_template('admin/_solicitudes_prioridad_results.html', **list_ctx)
            payload = _admin_async_payload(
                success=True,
                message='Listado actualizado.',
                category='info',
                replace_html=html,
                update_target='#prioridadAsyncRegion',
                extra={
                    "page": int(list_ctx["page"]),
                    "per_page": int(list_ctx["per_page"]),
                    "total": int(list_ctx["total"]),
                    "query": list_ctx["q"],
                    "estado": list_ctx["estado"],
                    "prioridad": list_ctx["prioridad"],
                    "responsable": list_ctx["responsable"],
                    "estancadas": bool(list_ctx["estancadas"]),
                    "total_count": int(list_ctx["total_count"]),
                    "count_activa": int(list_ctx["count_activa"]),
                    "count_reemplazo": int(list_ctx["count_reemplazo"]),
                    "count_critica": int(list_ctx["count_critica"]),
                    "count_urgente": int(list_ctx["count_urgente"]),
                    "count_atencion": int(list_ctx["count_atencion"]),
                    "count_normal": int(list_ctx["count_normal"]),
                    "count_sin_movimiento": int(list_ctx["count_sin_movimiento"]),
                    "count_espera_pago_prolongada": int(list_ctx["count_espera_pago_prolongada"]),
                    "count_reemplazo_sin_seguimiento": int(list_ctx["count_reemplazo_sin_seguimiento"]),
                },
            )
            response = jsonify(payload)
            response.status_code = 200
            return perf_done(response, html_bytes=len(html.encode("utf-8")), extra={"mode": "async_full"})

        html = render_template('admin/solicitudes_prioridad.html', **list_ctx)
        return perf_done(html, extra={"mode": "full"})


@admin_bp.route('/solicitudes/prioridad/_summary')
@login_required
@admin_required
def solicitudes_prioridad_summary_fragment():
    with _p1c1_perf_scope("solicitudes_prioridad_summary_fragment") as perf_done:
        list_ctx = _solicitudes_prioridad_context(include_rows=False, include_filter_options=False)
        html = render_template('admin/_solicitudes_prioridad_summary_region.html', **list_ctx)
        response = make_response(html, 200)
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        response.headers["X-Async-Fragment-Region"] = "prioridadSummaryAsyncRegion"
        return perf_done(response, html_bytes=len(html.encode("utf-8")), extra={"mode": "fragment_summary"})


@admin_bp.route('/solicitudes/prioridad/_responsables')
@login_required
@admin_required
def solicitudes_prioridad_responsables_fragment():
    with _p1c1_perf_scope("solicitudes_prioridad_responsables_fragment") as perf_done:
        list_ctx = _solicitudes_prioridad_context(include_rows=False, include_filter_options=False)
        html = render_template('admin/_solicitudes_prioridad_responsables_region.html', **list_ctx)
        response = make_response(html, 200)
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        response.headers["X-Async-Fragment-Region"] = "prioridadResponsablesAsyncRegion"
        return perf_done(response, html_bytes=len(html.encode("utf-8")), extra={"mode": "fragment_responsables"})



# ============================================================
#                                   API
# ============================================================
from flask import request, jsonify
from sqlalchemy import or_, and_

@admin_bp.route('/api/candidatas', methods=['GET'])
@login_required
@admin_required
def api_candidatas():
    """
    API para autocomplete de candidatas.

    - Si no hay 'q', devuelve hasta 50 candidatas ordenadas por nombre.
    - Respuesta: {"results":[{"id":..., "text":...}, ...]}

    - Busca por nombre, cédula, teléfono y código (coincidencia parcial, case-insensitive)
    - Soporta múltiples palabras/tokens
    - Devuelve texto: "Nombre — Cédula — Teléfono" (según aplique)

    IMPORTANTE:
    - Fuerza NO-CACHE para evitar respuestas 304 que rompen el fetch/json en el front.
    """
    term = (request.args.get('q') or '').strip()

    query = Candidata.query

    def _norm_tokens(s: str):
        s = (s or '').strip()
        if not s:
            return []
        return [t for t in s.split() if t]

    def _label(c: Candidata) -> str:
        nombre = (c.nombre_completo or '').strip()
        ced = (c.cedula or '').strip()
        tel = (c.numero_telefono or '').strip()
        cod = (c.codigo or '').strip()

        extra_parts = []
        if ced:
            extra_parts.append(ced)
        if tel:
            extra_parts.append(tel)
        # Si quieres mostrar el código también, descomenta:
        # if cod:
        #     extra_parts.append(cod)

        extra = ""
        if extra_parts:
            extra = " — " + " — ".join(extra_parts)

        if nombre:
            return f"{nombre}{extra}".strip()

        base = f"ID {c.fila}"
        return f"{base}{extra}".strip()

    if term:
        tokens = _norm_tokens(term)

        filters = []
        for t in tokens:
            like = f"%{t}%"
            filters.append(
                or_(
                    Candidata.nombre_completo.ilike(like),
                    Candidata.cedula.ilike(like),
                    Candidata.numero_telefono.ilike(like),
                    Candidata.codigo.ilike(like),
                )
            )

        query = query.filter(and_(*filters))

    candidatas = (
        query
        .order_by(Candidata.nombre_completo.asc(), Candidata.fila.asc())
        .limit(50)
        .all()
    )

    results = [{"id": int(c.fila), "text": _label(c)} for c in candidatas]

    resp = jsonify({"results": results})

    # ✅ Anti-cache duro (evita 304 y respuestas “sin body”)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"

    return resp


# ============================================================
#                           LISTADO / CONTADORES
# ============================================================
@admin_bp.route('/solicitudes')
@login_required
@staff_required
def listar_solicitudes():
    with _p1c1_perf_scope("solicitudes_list") as perf_done:
        is_async = _admin_async_wants_json()
        q = (request.args.get('q') or '').strip()
        estado = (request.args.get('estado') or '').strip().lower()
        triage = (request.args.get('triage') or '').strip().lower()
        if triage not in _SOLICITUDES_TRIAGE_CODES:
            triage = ''

        try:
            page = int(request.args.get('page', 1) or 1)
        except Exception:
            page = 1
        page = max(1, page)

        try:
            per_page = int(request.args.get('per_page', 25) or 25)
        except Exception:
            per_page = 25
        per_page = max(10, min(per_page, 200))

        allowed_states = ['proceso', 'activa', 'reemplazo', 'espera_pago', 'pagada', 'cancelada']
        row_order = (Solicitud.fecha_solicitud.desc(), Solicitud.id.desc())
        detail_attrs = []
        for attr in (
            "id",
            "cliente_id",
            "candidata_id",
            "codigo_solicitud",
            "ciudad_sector",
            "rutas_cercanas",
            "modalidad_trabajo",
            "horario",
            "nota_cliente",
            "estado",
            "fecha_solicitud",
            "last_copiado_at",
            "estado_actual_desde",
            "fecha_seguimiento_manual",
            "row_version",
            "updated_at",
            "fecha_ultima_modificacion",
            "fecha_ultima_actividad",
        ):
            if hasattr(Solicitud, attr):
                detail_attrs.append(getattr(Solicitud, attr))

        triage_scan_attrs = []
        for attr in (
            "id",
            "estado",
            "fecha_solicitud",
            "estado_actual_desde",
            "fecha_inicio_seguimiento",
            "fecha_seguimiento_manual",
            "updated_at",
            "fecha_ultima_modificacion",
            "fecha_ultima_actividad",
        ):
            if hasattr(Solicitud, attr):
                triage_scan_attrs.append(getattr(Solicitud, attr))

        base_query = Solicitud.query
        if estado and estado in allowed_states:
            base_query = base_query.filter(Solicitud.estado == estado)
        else:
            estado = ''

        if q:
            like = f"%{q}%"
            base_query = (
                base_query
                .outerjoin(Cliente, Solicitud.cliente_id == Cliente.id)
                .outerjoin(Candidata, Solicitud.candidata_id == Candidata.fila)
                .filter(or_(
                    Solicitud.codigo_solicitud.ilike(like),
                    Solicitud.ciudad_sector.ilike(like),
                    Solicitud.rutas_cercanas.ilike(like),
                    Cliente.nombre_completo.ilike(like),
                    Cliente.codigo.ilike(like),
                    Cliente.telefono.ilike(like),
                    Candidata.nombre_completo.ilike(like),
                    Candidata.codigo.ilike(like),
                    Candidata.cedula.ilike(like),
                ))
            )

        now_utc = utc_now_naive()
        today_rd = rd_today()
        start = max(0, (page - 1) * per_page)
        triage_options = _solicitudes_triage_options(rows=[], selected=triage)
        solicitudes_operativas = []
        reemplazos_activos = {}
        total = 0
        has_more = False

        if triage:
            triage_scan_options = []
            if triage_scan_attrs:
                triage_scan_options.append(load_only(*triage_scan_attrs))
            triage_detail_options = []
            if detail_attrs:
                triage_detail_options.append(load_only(*detail_attrs))
            triage_detail_options.extend([
                joinedload(Solicitud.cliente),
                joinedload(Solicitud.candidata),
            ])
            triage_detail_options.append(
                selectinload(Solicitud.reemplazos).load_only(
                    Reemplazo.id,
                    Reemplazo.fecha_inicio_reemplazo,
                    Reemplazo.fecha_fin_reemplazo,
                    Reemplazo.created_at,
                )
            )
            use_in_memory_triage = not _solicitudes_query_supports_sql_triage(base_query)
            if not use_in_memory_triage:
                try:
                    triage_parts = _solicitudes_triage_sql_parts(now_dt=now_utc, today_rd=today_rd)
                    triage_clause = triage_parts["clauses"].get(triage)
                    triage_options = _solicitudes_triage_options_sql(
                        base_query=base_query,
                        selected=triage,
                        now_dt=now_utc,
                        today_rd=today_rd,
                    )
                    triage_query = base_query
                    if triage_clause is not None:
                        triage_query = triage_query.filter(triage_clause)
                    selected_total = None
                    for opt in (triage_options or []):
                        opt_code = str((opt or {}).get("code", "") or "").strip().lower()
                        if opt_code != triage:
                            continue
                        try:
                            selected_total = max(0, int((opt or {}).get("count", 0) or 0))
                        except Exception:
                            selected_total = None
                        break
                    if selected_total is None:
                        total = _query_count_distinct_solicitudes(triage_query)
                    else:
                        total = int(selected_total)
                    end = start + per_page
                    has_more = end < total
                    triage_query = (
                        triage_query
                        .options(*triage_scan_options, *triage_detail_options)
                        .order_by(
                            triage_parts["signal_rank"].desc(),
                            func.coalesce(triage_parts["state_anchor"], now_utc).asc(),
                            Solicitud.id.asc(),
                        )
                        .offset(start)
                        .limit(per_page)
                    )
                    triage_rows = triage_query.all()
                    triage_ids = [
                        int(getattr(s, "id", 0) or 0)
                        for s in (triage_rows or [])
                        if int(getattr(s, "id", 0) or 0) > 0
                    ]
                    last_actor_by_solicitud = _resolve_solicitud_last_actor_user_ids(triage_ids)
                    actor_ids = sorted({
                        int(uid) for uid in (last_actor_by_solicitud.values() or [])
                        if uid is not None and int(uid or 0) > 0
                    })
                    username_by_actor = _staff_username_map(actor_ids)
                    for s in (triage_rows or []):
                        sid = int(getattr(s, "id", 0) or 0)
                        actor_user_id = last_actor_by_solicitud.get(sid)
                        actor_label = "Sin responsable"
                        if actor_user_id is not None:
                            try:
                                actor_user_id = int(actor_user_id)
                            except Exception:
                                actor_user_id = None
                        if actor_user_id is not None and actor_user_id > 0:
                            actor_label = username_by_actor.get(actor_user_id, f"Staff #{actor_user_id}")
                        repl = _active_reemplazo_for_solicitud(s)
                        if repl:
                            reemplazos_activos[sid] = repl
                        item = _SolicitudOperativaListVM(
                            s,
                            now_dt=now_utc,
                            today_rd=today_rd,
                            last_actor_label=actor_label,
                            has_active_reemplazo=bool(repl),
                        )
                        if _solicitud_matches_triage(item, triage):
                            solicitudes_operativas.append(item)
                except Exception as exc:
                    rollback_done = False
                    try:
                        db.session.rollback()
                        rollback_done = True
                    except Exception:
                        rollback_done = False
                    current_app.logger.exception(
                        "admin.solicitudes triage SQL failed; falling back to in-memory triage",
                        extra={
                            "triage": triage,
                            "error_type": exc.__class__.__name__,
                            "error_message": str(exc),
                            "rollback_done": rollback_done,
                            "used_in_memory_fallback": True,
                        },
                    )
                    use_in_memory_triage = True
            if use_in_memory_triage:
                triage_query = base_query.options(*triage_scan_options).order_by(*row_order)
                triage_rows = triage_query.all()

                triage_ids = [
                    int(getattr(s, "id", 0) or 0)
                    for s in (triage_rows or [])
                    if int(getattr(s, "id", 0) or 0) > 0
                ]
                last_actor_by_solicitud = _resolve_solicitud_last_actor_user_ids(triage_ids)
                triage_reemplazo_ids = [
                    int(getattr(s, "id", 0) or 0)
                    for s in (triage_rows or [])
                    if (
                        int(getattr(s, "id", 0) or 0) > 0
                        and str(getattr(s, "estado", "") or "").strip().lower() == "reemplazo"
                    )
                ]
                active_reemplazo_by_solicitud = (
                    _active_reemplazo_map_for_solicitudes(triage_reemplazo_ids)
                    if triage_reemplazo_ids
                    else {}
                )
                triage_rows_scan = []
                for s in (triage_rows or []):
                    sid = int(getattr(s, "id", 0) or 0)
                    actor_user_id = last_actor_by_solicitud.get(sid)
                    if actor_user_id is not None:
                        try:
                            actor_user_id = int(actor_user_id)
                        except Exception:
                            actor_user_id = None
                    repl = active_reemplazo_by_solicitud.get(sid)
                    triage_rows_scan.append(
                        _SolicitudOperativaTriageScanRow(
                            s,
                            now_dt=now_utc,
                            today_rd=today_rd,
                            last_actor_user_id=actor_user_id,
                            active_reemplazo=repl,
                        )
                    )

                triage_rows_scan.sort(
                    key=lambda item: (
                        -int(getattr(item, "operational_signal_rank", 0) or 0),
                        -int(getattr(item, "days_in_state", 0) or 0),
                        int(getattr(item, "id", 0) or 0),
                    )
                )
                triage_options = _solicitudes_triage_options(rows=triage_rows_scan, selected=triage)
                filtered_scan_rows = [item for item in triage_rows_scan if _solicitud_matches_triage(item, triage)]
                total = len(filtered_scan_rows)
                end = start + per_page
                has_more = end < total
                page_scan_rows = filtered_scan_rows[start:end]
                page_actor_ids = sorted({
                    int(getattr(item, "last_actor_user_id", 0) or 0)
                    for item in (page_scan_rows or [])
                    if int(getattr(item, "last_actor_user_id", 0) or 0) > 0
                })
                username_by_actor = _staff_username_map(page_actor_ids)
                page_ids = [int(getattr(item, "id", 0) or 0) for item in (page_scan_rows or []) if int(getattr(item, "id", 0) or 0) > 0]
                page_detail_by_id: dict[int, Solicitud] = {}
                if page_ids:
                    page_detail_rows = (
                        Solicitud.query
                        .options(*triage_detail_options)
                        .filter(Solicitud.id.in_(page_ids))
                        .all()
                    )
                    page_detail_by_id = {
                        int(getattr(row, "id", 0) or 0): row
                        for row in (page_detail_rows or [])
                        if int(getattr(row, "id", 0) or 0) > 0
                    }
                solicitudes_operativas = []
                for item in (page_scan_rows or []):
                    sid = int(getattr(item, "id", 0) or 0)
                    actor_user_id = getattr(item, "last_actor_user_id", None)
                    actor_label = "Sin responsable"
                    if actor_user_id is not None and int(actor_user_id or 0) > 0:
                        actor_label = username_by_actor.get(int(actor_user_id), f"Staff #{int(actor_user_id)}")
                    page_solicitud = page_detail_by_id.get(sid) or item._s
                    repl = getattr(item, "active_reemplazo", None)
                    if sid in page_detail_by_id:
                        repl = _active_reemplazo_for_solicitud(page_solicitud) or repl
                    if repl is not None:
                        reemplazos_activos[sid] = repl
                    solicitudes_operativas.append(
                        _SolicitudOperativaListVM(
                            page_solicitud,
                            now_dt=now_utc,
                            today_rd=today_rd,
                            last_actor_label=actor_label,
                            has_active_reemplazo=bool(repl),
                        )
                    )
        else:
            try:
                total = int(
                    base_query
                    .with_entities(func.count(func.distinct(Solicitud.id)))
                    .scalar()
                    or 0
                )
            except Exception:
                # Compatibilidad con stubs/unit tests que no implementan with_entities/scalar.
                total = int(base_query.order_by(None).count() or 0)
            has_more = (start + per_page) < total
            detail_query = base_query
            detail_options = []
            if detail_attrs:
                detail_options.append(load_only(*detail_attrs))
            detail_options.extend([
                joinedload(Solicitud.cliente),
                joinedload(Solicitud.candidata),
                selectinload(Solicitud.reemplazos).load_only(
                    Reemplazo.id,
                    Reemplazo.fecha_inicio_reemplazo,
                    Reemplazo.fecha_fin_reemplazo,
                    Reemplazo.created_at,
                ),
            ])
            detail_query = (
                detail_query
                .options(*detail_options)
                .order_by(*row_order)
                .offset(start)
                .limit(per_page)
            )
            page_rows = detail_query.all()

            page_ids = [
                int(getattr(s, "id", 0) or 0)
                for s in (page_rows or [])
                if int(getattr(s, "id", 0) or 0) > 0
            ]
            last_actor_by_solicitud = _resolve_solicitud_last_actor_user_ids(page_ids)
            actor_ids = sorted({
                int(uid) for uid in (last_actor_by_solicitud.values() or [])
                if uid is not None and int(uid or 0) > 0
            })
            username_by_actor = _staff_username_map(actor_ids)
            for s in (page_rows or []):
                sid = int(getattr(s, "id", 0) or 0)
                actor_user_id = last_actor_by_solicitud.get(sid)
                actor_label = "Sin responsable"
                if actor_user_id is not None:
                    try:
                        actor_user_id = int(actor_user_id)
                    except Exception:
                        actor_user_id = None
                if actor_user_id is not None and actor_user_id > 0:
                    actor_label = username_by_actor.get(actor_user_id, f"Staff #{actor_user_id}")
                repl = _active_reemplazo_for_solicitud(s)
                if repl:
                    reemplazos_activos[sid] = repl
                solicitudes_operativas.append(
                    _SolicitudOperativaListVM(
                        s,
                        now_dt=now_utc,
                        today_rd=today_rd,
                        last_actor_label=actor_label,
                        has_active_reemplazo=bool(repl),
                    )
                )

            solicitudes_operativas.sort(
                key=lambda item: (
                    -int(getattr(item, "operational_signal_rank", 0) or 0),
                    -int(getattr(item, "days_in_state", 0) or 0),
                    int(getattr(item, "id", 0) or 0),
                )
            )
            triage_options = _solicitudes_triage_options(rows=solicitudes_operativas, selected=triage)
            if triage_options:
                triage_options[0]["count"] = int(total)

        proc_count = 0
        copiable_count = 0
        if not is_async:
            proc_count, copiable_count, copiable_warning = _solicitudes_summary_counts()
            if copiable_warning:
                flash(copiable_warning, "warning")

        role = (
            str(getattr(current_user, "role", "") or "").strip().lower()
            or str(session.get("role", "") or "").strip().lower()
        )
        is_admin_role = role in ("owner", "admin")

        list_ctx = dict(
            q=q,
            estado=estado,
            solicitudes=solicitudes_operativas,
            reemplazos_activos=reemplazos_activos,
            is_admin_role=is_admin_role,
            total=total,
            page=page,
            per_page=per_page,
            has_more=has_more,
            chat_feature_enabled=_chat_enabled(),
            triage=triage,
            triage_label=_SOLICITUDES_TRIAGE_LABELS.get(triage, "Todas"),
            triage_options=triage_options,
        )

        if is_async:
            html = render_template('admin/_solicitudes_list_results.html', **list_ctx)
            payload = _admin_async_payload(
                success=True,
                message='Listado actualizado.',
                category='info',
                replace_html=html,
                update_target='#solicitudesAsyncRegion',
                extra={
                    "page": page,
                    "per_page": per_page,
                    "total": total,
                    "query": q,
                    "estado": estado,
                    "triage": triage,
                },
            )
            response = jsonify(payload)
            response.status_code = 200
            return perf_done(response, html_bytes=len(html.encode("utf-8")), extra={"mode": "async_list"})

        html = render_template(
            'admin/solicitudes_list.html',
            proc_count=proc_count,
            copiable_count=copiable_count,
            allowed_states=allowed_states,
            **list_ctx,
        )
        return perf_done(html, html_bytes=len(html.encode("utf-8")), extra={"mode": "full"})


@admin_bp.route('/solicitudes/_summary')
@login_required
@staff_required
def solicitudes_summary_fragment():
    with _p1c1_perf_scope("solicitudes_summary_fragment") as perf_done:
        proc_count, copiable_count, _warning = _solicitudes_summary_counts()
        html = render_template(
            'admin/_solicitudes_summary_region.html',
            proc_count=proc_count,
            copiable_count=copiable_count,
        )
        response = make_response(html, 200)
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        response.headers["X-Async-Fragment-Region"] = "solicitudesSummaryAsyncRegion"
        return perf_done(response, html_bytes=len(html.encode("utf-8")), extra={"mode": "fragment_summary"})


def _solicitudes_summary_counts() -> tuple[int, int, str]:
    proc_count = int(Solicitud.query.filter_by(estado='proceso').count() or 0)
    start_utc, _ = _today_utc_bounds()
    warning = ""
    try:
        copiable_count = int(
            Solicitud.query
            .filter(Solicitud.estado.in_(('activa', 'reemplazo')))
            .filter(
                or_(
                    Solicitud.last_copiado_at.is_(None),
                    Solicitud.last_copiado_at < start_utc
                )
            )
            .count()
            or 0
        )
    except SQLAlchemyError:
        db.session.rollback()
        copiable_count = int(
            Solicitud.query
            .filter(Solicitud.estado.in_(('activa', 'reemplazo')))
            .count()
            or 0
        )
        warning = (
            "No se pudo aplicar el filtro de copia diaria; mostrando el total de solicitudes activas/reemplazo."
        )
    return proc_count, copiable_count, warning


def _matching_created_by() -> str:
    try:
        if getattr(current_user, "is_authenticated", False):
            username = getattr(current_user, "username", None) or getattr(current_user, "id", None)
            if username:
                return str(username)
    except Exception:
        pass
    return str(session.get("usuario") or "sistema")


_NOTIF_TIPO_CANDIDATAS_ENVIADAS = "candidatas_enviadas"
_NOTIF_TIPO_SOLICITUD_ESTADO = "solicitud_estado"
_NOTIF_TIPO_CANDIDATA_SELECCIONADA = "candidata_seleccionada"
_NOTIF_TIPO_REEMPLAZO_ACTIVADO = "reemplazo_activado"
_NOTIF_ESTADOS_RELEVANTES = {"espera_pago", "reemplazo", "pagada", "cancelada"}
_NOTIF_ESTADO_LABELS = {
    "proceso": "en proceso",
    "activa": "activa",
    "reemplazo": "en reemplazo",
    "espera_pago": "en espera de pago",
    "pagada": "pagada",
    "cancelada": "cancelada",
}
_ACTIVE_ASSIGNMENT_STATUS = ("enviada", "vista", "seleccionada")
_ASSIGNMENT_CLOSEABLE_STATUS = ("sugerida", "enviada", "vista", "seleccionada")
_CANCEL_RELEASEABLE_STATUS = ("sugerida", "enviada", "vista", "seleccionada")


def _upsert_cliente_notificacion_candidatas(solicitud: Solicitud, count: int) -> None:
    if not solicitud or not getattr(solicitud, "cliente_id", None) or int(count or 0) <= 0:
        return

    now = utc_now_naive()
    recent_from = now - timedelta(hours=24)
    titulo = "Candidatas enviadas"
    cuerpo = (
        f"La agencia te envio candidatas compatibles para la solicitud "
        f"{(getattr(solicitud, 'codigo_solicitud', None) or f'SOL-{solicitud.id}') }."
    )

    existing = (
        ClienteNotificacion.query
        .filter_by(
            cliente_id=solicitud.cliente_id,
            solicitud_id=solicitud.id,
            tipo=_NOTIF_TIPO_CANDIDATAS_ENVIADAS,
            is_read=False,
            is_deleted=False,
        )
        .filter(ClienteNotificacion.created_at >= recent_from)
        .order_by(ClienteNotificacion.id.desc())
        .first()
    )

    if existing:
        prev_payload = existing.payload if isinstance(existing.payload, dict) else {}
        prev_count = 0
        try:
            prev_count = int(prev_payload.get("count") or 0)
        except Exception:
            prev_count = 0
        existing.payload = {"count": max(0, prev_count) + int(count)}
        existing.titulo = titulo
        existing.cuerpo = cuerpo
        existing.updated_at = now
        _emit_domain_outbox_event(
            event_type="CLIENTE_NOTIFICACION_UPDATED",
            aggregate_type="ClienteNotificacion",
            aggregate_id=existing.id,
            aggregate_version=None,
            payload={
                "cliente_id": int(getattr(solicitud, "cliente_id", 0) or 0),
                "solicitud_id": int(getattr(solicitud, "id", 0) or 0),
                "notificacion_id": int(getattr(existing, "id", 0) or 0),
                "tipo": _NOTIF_TIPO_CANDIDATAS_ENVIADAS,
                "count": int(existing.payload.get("count") or 0),
            },
        )
        return

    notif = ClienteNotificacion(
        cliente_id=solicitud.cliente_id,
        solicitud_id=solicitud.id,
        tipo=_NOTIF_TIPO_CANDIDATAS_ENVIADAS,
        titulo=titulo,
        cuerpo=cuerpo,
        payload={"count": int(count)},
        is_read=False,
        is_deleted=False,
    )
    db.session.add(notif)
    db.session.flush()
    _emit_domain_outbox_event(
        event_type="CLIENTE_NOTIFICACION_CREATED",
        aggregate_type="ClienteNotificacion",
        aggregate_id=notif.id,
        aggregate_version=None,
        payload={
            "cliente_id": int(getattr(solicitud, "cliente_id", 0) or 0),
            "solicitud_id": int(getattr(solicitud, "id", 0) or 0),
            "notificacion_id": int(getattr(notif, "id", 0) or 0),
            "tipo": _NOTIF_TIPO_CANDIDATAS_ENVIADAS,
            "count": int(count or 0),
        },
    )


def _codigo_solicitud_label(solicitud: Solicitud) -> str:
    if not solicitud:
        return "SOL"
    raw = getattr(solicitud, "codigo_solicitud", None)
    if raw:
        return str(raw)
    sid = _safe_int(getattr(solicitud, "id", 0), default=0)
    return f"SOL-{sid}" if sid > 0 else "SOL"


def _find_recent_unread_cliente_notif(
    *,
    cliente_id: int,
    solicitud_id: int | None,
    tipo: str,
    recent_hours: int = 24,
):
    if cliente_id <= 0:
        return None
    recent_from = utc_now_naive() - timedelta(hours=max(1, int(recent_hours or 24)))
    query = (
        ClienteNotificacion.query
        .filter_by(
            cliente_id=int(cliente_id),
            tipo=str(tipo or "")[:80],
            is_read=False,
            is_deleted=False,
        )
    )
    if int(solicitud_id or 0) > 0:
        query = query.filter_by(solicitud_id=int(solicitud_id))
    return (
        query
        .filter(ClienteNotificacion.created_at >= recent_from)
        .order_by(ClienteNotificacion.id.desc())
        .first()
    )


def _create_cliente_notificacion_simple(
    *,
    solicitud: Solicitud,
    tipo: str,
    titulo: str,
    cuerpo: str,
    payload: dict | None = None,
    dedupe_hours: int = 24,
    dedupe_payload_keys: tuple[str, ...] = (),
):
    if not solicitud:
        return None
    cliente_id = _safe_int(getattr(solicitud, "cliente_id", 0), default=0)
    solicitud_id = _safe_int(getattr(solicitud, "id", 0), default=0)
    if cliente_id <= 0 or solicitud_id <= 0:
        return None

    now = utc_now_naive()
    payload_data = dict(payload or {})

    existing = _find_recent_unread_cliente_notif(
        cliente_id=cliente_id,
        solicitud_id=solicitud_id,
        tipo=tipo,
        recent_hours=dedupe_hours,
    )
    if existing is not None and dedupe_payload_keys:
        prev_payload = existing.payload if isinstance(existing.payload, dict) else {}
        same_payload = True
        for key in dedupe_payload_keys:
            if prev_payload.get(key) != payload_data.get(key):
                same_payload = False
                break
        if same_payload:
            existing.titulo = str(titulo or existing.titulo or "Notificacion")
            existing.cuerpo = str(cuerpo or existing.cuerpo or "")
            existing.updated_at = now
            return existing

    if existing is not None and not dedupe_payload_keys:
        existing.titulo = str(titulo or existing.titulo or "Notificacion")
        existing.cuerpo = str(cuerpo or existing.cuerpo or "")
        existing.updated_at = now
        return existing

    notif = ClienteNotificacion(
        cliente_id=cliente_id,
        solicitud_id=solicitud_id,
        tipo=str(tipo or "")[:80],
        titulo=str(titulo or "Notificacion")[:200],
        cuerpo=str(cuerpo or ""),
        payload=payload_data or None,
        is_read=False,
        is_deleted=False,
    )
    db.session.add(notif)
    db.session.flush()
    _emit_domain_outbox_event(
        event_type="CLIENTE_NOTIFICACION_CREATED",
        aggregate_type="ClienteNotificacion",
        aggregate_id=int(getattr(notif, "id", 0) or 0),
        aggregate_version=None,
        payload={
            "cliente_id": int(cliente_id),
            "solicitud_id": int(solicitud_id),
            "notificacion_id": int(getattr(notif, "id", 0) or 0),
            "tipo": str(tipo or "")[:80],
        },
    )
    return notif


def _notify_cliente_status_change(solicitud: Solicitud, transition: dict | None) -> None:
    if not solicitud or not isinstance(transition, dict) or not bool(transition.get("changed")):
        return
    to_estado = str(transition.get("to") or "").strip().lower()
    from_estado = str(transition.get("from") or "").strip().lower()
    if to_estado not in _NOTIF_ESTADOS_RELEVANTES:
        return
    code = _codigo_solicitud_label(solicitud)
    to_label = _NOTIF_ESTADO_LABELS.get(to_estado, to_estado)
    from_label = _NOTIF_ESTADO_LABELS.get(from_estado, from_estado or "actualizada")
    titulo = "Estado de solicitud actualizado"
    cuerpo = f"Tu solicitud {code} cambio de {from_label} a {to_label}."
    _create_cliente_notificacion_simple(
        solicitud=solicitud,
        tipo=_NOTIF_TIPO_SOLICITUD_ESTADO,
        titulo=titulo,
        cuerpo=cuerpo,
        payload={"from": from_estado or None, "to": to_estado},
        dedupe_hours=12,
        dedupe_payload_keys=("to",),
    )


def _notify_cliente_candidata_asignada(solicitud: Solicitud, candidata_id: int | None = None) -> None:
    if not solicitud:
        return
    code = _codigo_solicitud_label(solicitud)
    cid = _safe_int(candidata_id, default=0)
    _create_cliente_notificacion_simple(
        solicitud=solicitud,
        tipo=_NOTIF_TIPO_CANDIDATA_SELECCIONADA,
        titulo="Candidata seleccionada",
        cuerpo=f"Ya tienes candidata seleccionada para la solicitud {code}.",
        payload={"candidata_id": (int(cid) if cid > 0 else None)},
        dedupe_hours=24,
        dedupe_payload_keys=("candidata_id",),
    )


def _notify_cliente_reemplazo_activado(solicitud: Solicitud, reemplazo_id: int | None = None) -> None:
    if not solicitud:
        return
    code = _codigo_solicitud_label(solicitud)
    rid = _safe_int(reemplazo_id, default=0)
    _create_cliente_notificacion_simple(
        solicitud=solicitud,
        tipo=_NOTIF_TIPO_REEMPLAZO_ACTIVADO,
        titulo="Reemplazo activado",
        cuerpo=f"Activamos reemplazo para la solicitud {code}. Te enviaremos nuevas candidatas.",
        payload={"reemplazo_id": (int(rid) if rid > 0 else None)},
        dedupe_hours=24,
        dedupe_payload_keys=("reemplazo_id",),
    )


def _matching_candidate_flags(solicitud: Solicitud, candidata_ids: list[int]) -> tuple[set[int], set[int]]:
    """Devuelve (bloqueadas_por_otro_cliente, rechazadas_por_mismo_cliente)."""
    blocked_ids: set[int] = set()
    rejected_ids: set[int] = set()
    if not solicitud or not candidata_ids:
        return blocked_ids, rejected_ids

    try:
        active_rows = (
            db.session.query(SolicitudCandidata.candidata_id)
            .join(Solicitud, Solicitud.id == SolicitudCandidata.solicitud_id)
            .filter(
                SolicitudCandidata.candidata_id.in_(candidata_ids),
                SolicitudCandidata.status.in_(_ACTIVE_ASSIGNMENT_STATUS),
                SolicitudCandidata.solicitud_id != solicitud.id,
                Solicitud.cliente_id != solicitud.cliente_id,
            )
            .all()
        )
        blocked_ids = {int(row[0]) for row in active_rows if row and row[0] is not None}
    except Exception:
        blocked_ids = set()

    try:
        rejected_rows = (
            db.session.query(SolicitudCandidata.candidata_id)
            .join(Solicitud, Solicitud.id == SolicitudCandidata.solicitud_id)
            .filter(
                SolicitudCandidata.candidata_id.in_(candidata_ids),
                SolicitudCandidata.status == "descartada",
                Solicitud.cliente_id == solicitud.cliente_id,
            )
            .all()
        )
        rejected_ids = {int(row[0]) for row in rejected_rows if row and row[0] is not None}
    except Exception:
        rejected_ids = set()

    return blocked_ids, rejected_ids


def _sync_solicitud_candidatas_after_assignment(solicitud: Solicitud, assigned_candidata_id: int, actor: str = "") -> None:
    """
    Al asignar una candidata en Solicitud:
    - candidata asignada => status 'seleccionada'
    - resto de candidatas de esa solicitud en estados abiertos => status 'liberada'
    """
    if not solicitud or not getattr(solicitud, "id", None) or not assigned_candidata_id:
        return
    invariant_sync_solicitud_candidatas_after_assignment(
        solicitud=solicitud,
        assigned_candidata_id=int(assigned_candidata_id),
        actor=(actor or _matching_created_by()),
    )


def _with_for_update_if_supported(query):
    if query is None or not hasattr(query, "with_for_update"):
        return query
    try:
        return query.with_for_update()
    except Exception:
        return query


def _lock_candidata_for_update(candidata_id: int) -> Candidata | None:
    try:
        cid = int(candidata_id)
    except Exception:
        return None
    query_obj = getattr(Candidata, "query", None)
    if query_obj is None:
        return None
    if hasattr(query_obj, "filter_by"):
        query = query_obj.filter_by(fila=cid)
        query = _with_for_update_if_supported(query)
        if hasattr(query, "first"):
            return query.first()
    if hasattr(query_obj, "get"):
        try:
            return query_obj.get(cid)
        except Exception:
            return None
    return None


def _lock_candidatas_for_update(candidata_ids: list[int]) -> dict[int, Candidata]:
    ids = sorted({int(x) for x in (candidata_ids or []) if int(x or 0) > 0})
    if not ids:
        return {}

    query_obj = getattr(Candidata, "query", None)
    if query_obj is not None and hasattr(query_obj, "filter"):
        try:
            query = query_obj.filter(Candidata.fila.in_(ids))
            query = _with_for_update_if_supported(query)
            if hasattr(query, "all"):
                rows = query.all() or []
                out = {
                    int(getattr(cand, "fila", 0) or 0): cand
                    for cand in rows
                    if int(getattr(cand, "fila", 0) or 0) > 0
                }
                if out:
                    return out
        except Exception:
            pass

    out: dict[int, Candidata] = {}
    for candidata_id in ids:
        cand = _lock_candidata_for_update(candidata_id)
        if cand is not None:
            out[int(cand.fila)] = cand
    return out


def _release_solicitud_candidatas_on_cancel(
    solicitud: Solicitud,
    *,
    actor: str = "",
    motivo: str = "",
) -> dict:
    if not solicitud or not getattr(solicitud, "id", None):
        return {"released_count": 0, "candidata_ids": []}
    try:
        return invariant_release_solicitud_candidatas_on_cancel(
            solicitud=solicitud,
            actor=(actor or _matching_created_by()),
            motivo=motivo or "",
        )
    except Exception:
        return {"released_count": 0, "candidata_ids": []}


def _staff_actor_name() -> str:
    actor = (
        getattr(current_user, "username", None)
        or getattr(current_user, "id", None)
        or session.get("usuario")
        or "sistema"
    )
    return str(actor)[:100]


def _admin_business_actor_key() -> str:
    actor = (_staff_actor_name() or "staff").strip().lower()[:80]
    ip = (_client_ip() or "0.0.0.0").strip().lower()[:64]
    return f"{actor}:{ip}"


def _admin_block_sensitive_action(
    *,
    scope: str,
    entity_type: str,
    entity_id: int | str,
    limit: int,
    window_seconds: int,
    min_interval_seconds: int = 0,
    summary: str,
    next_url: str,
    fallback: str,
) -> Response | None:
    actor_key = _admin_business_actor_key()
    blocked, _ = enforce_business_limit(
        cache_obj=cache,
        scope=scope,
        actor=actor_key,
        limit=limit,
        window_seconds=window_seconds,
        reason="admin_action_burst",
        summary=summary,
        metadata={"route": (request.path or ""), "entity_type": entity_type, "entity_id": str(entity_id)},
    )
    if blocked:
        _audit_log(
            action_type="BUSINESS_FLOW_BLOCKED",
            entity_type=entity_type,
            entity_id=entity_id,
            summary=summary,
            metadata={"rule": "admin_action_burst", "scope": scope, "actor": actor_key},
            success=False,
            error="admin_action_burst_blocked",
        )
        flash("Demasiadas acciones seguidas. Espera un momento e intenta nuevamente.", "warning")
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    if int(min_interval_seconds or 0) > 0:
        blocked_fast, _ = enforce_min_human_interval(
            cache_obj=cache,
            scope=f"{scope}_interval",
            actor=f"{actor_key}:{entity_type}:{entity_id}",
            min_seconds=int(min_interval_seconds),
            reason="admin_non_human_timing",
            summary=summary,
            metadata={"route": (request.path or ""), "entity_type": entity_type, "entity_id": str(entity_id)},
        )
        if blocked_fast:
            _audit_log(
                action_type="BUSINESS_FLOW_BLOCKED",
                entity_type=entity_type,
                entity_id=entity_id,
                summary=summary,
                metadata={"rule": "admin_non_human_timing", "scope": f"{scope}_interval", "actor": actor_key},
                success=False,
                error="admin_non_human_timing_blocked",
            )
            flash("Acción bloqueada temporalmente por ritmo inusual. Intenta de nuevo en unos segundos.", "warning")
            return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    return None


def _admin_noop_repeat_blocked(
    *,
    scope: str,
    entity_type: str,
    entity_id: int | str,
    state: str,
    summary: str,
) -> bool:
    actor_key = _admin_business_actor_key()
    blocked, _ = enforce_business_limit(
        cache_obj=cache,
        scope=f"{scope}_noop",
        actor=f"{actor_key}:{entity_type}:{entity_id}:{(state or '').strip().lower()}",
        limit=1,
        window_seconds=180,
        reason="admin_repeated_noop",
        summary=summary,
        metadata={
            "route": (request.path or ""),
            "entity_type": entity_type,
            "entity_id": str(entity_id),
            "state": (state or "").strip().lower(),
        },
    )
    if not blocked:
        return False
    _audit_log(
        action_type="BUSINESS_FLOW_BLOCKED",
        entity_type=entity_type,
        entity_id=entity_id,
        summary=summary,
        metadata={"rule": "admin_repeated_noop", "scope": f"{scope}_noop", "state": (state or "").strip().lower()},
        success=False,
        error="admin_repeated_noop_blocked",
    )
    return True


def _mark_candidata_estado(cand: Candidata, nuevo_estado: str, *, nota_descalificacion: str | None = None) -> None:
    if not cand:
        return
    try:
        invariant_change_candidate_state(
            candidata_id=int(getattr(cand, "fila", 0) or 0),
            new_state=str(nuevo_estado or "").strip().lower(),
            actor=_staff_actor_name(),
            nota_descalificacion=nota_descalificacion,
            candidata_obj=cand,
        )
    except InvariantConflictError:
        raise


def _log_lista_state_change(cand: Candidata, *, source: str, faltantes: list[str] | None = None, from_state: str | None = None) -> None:
    if not cand:
        return
    from_value = (from_state or "").strip().lower() or None
    to_value = (getattr(cand, "estado", None) or "").strip().lower() or "lista_para_trabajar"
    log_candidata_action(
        action_type="CANDIDATA_MARK_LISTA",
        candidata=cand,
        summary=f"Candidata marcada lista para trabajar: {cand.nombre_completo or cand.fila}",
        metadata={
            "reason": "readiness_ok",
            "faltantes": list(faltantes or []),
            "source": (source or "manual").strip().lower(),
        },
        changes={"estado": {"from": from_value, "to": to_value}},
        success=True,
    )


def _active_reemplazo_for_solicitud(solicitud: Solicitud):
    if not solicitud:
        return None
    activos = [
        r for r in (getattr(solicitud, "reemplazos", None) or [])
        if bool(getattr(r, "fecha_inicio_reemplazo", None)) and not bool(getattr(r, "fecha_fin_reemplazo", None))
    ]
    if not activos:
        return None
    return sorted(
        activos,
        key=lambda rr: getattr(rr, "fecha_inicio_reemplazo", None) or getattr(rr, "created_at", None) or datetime.min,
        reverse=True,
    )[0]


def _active_reemplazo_map_for_solicitudes(solicitud_ids: list[int]) -> dict[int, Reemplazo]:
    ids = sorted({int(sid) for sid in (solicitud_ids or []) if int(sid or 0) > 0})
    if not ids:
        return {}
    try:
        rows = (
            Reemplazo.query
            .options(load_only(
                Reemplazo.id,
                Reemplazo.solicitud_id,
                Reemplazo.fecha_inicio_reemplazo,
                Reemplazo.fecha_fin_reemplazo,
                Reemplazo.created_at,
            ))
            .filter(
                Reemplazo.solicitud_id.in_(ids),
                Reemplazo.fecha_inicio_reemplazo.isnot(None),
                Reemplazo.fecha_fin_reemplazo.is_(None),
            )
            .all()
        )
    except Exception:
        return {}

    out: dict[int, Reemplazo] = {}
    for row in (rows or []):
        sid = int(getattr(row, "solicitud_id", 0) or 0)
        if sid <= 0:
            continue
        prev = out.get(sid)
        row_anchor = getattr(row, "fecha_inicio_reemplazo", None) or getattr(row, "created_at", None) or datetime.min
        if prev is None:
            out[sid] = row
            continue
        prev_anchor = getattr(prev, "fecha_inicio_reemplazo", None) or getattr(prev, "created_at", None) or datetime.min
        if row_anchor >= prev_anchor:
            out[sid] = row
    return out


def _parse_matching_candidata_ids(raw_ids: list[str] | None) -> list[int]:
    parsed: list[int] = []
    for raw in (raw_ids or []):
        try:
            val = int(str(raw).strip())
            if val > 0:
                parsed.append(val)
        except Exception:
            continue
    return sorted(set(parsed))


def _matching_force_send_enabled(raw_value) -> bool:
    return str(raw_value or "").strip().lower() in ("1", "true", "on", "yes")


def _matching_send_result(
    *,
    success: bool,
    message: str,
    category: str,
    error_code: str | None = None,
    status_code: int = 200,
) -> dict:
    return {
        "success": bool(success),
        "message": str(message or ""),
        "category": str(category or "info"),
        "error_code": error_code,
        "status_code": int(status_code or 200),
    }


def _matching_ranking_cache_key(*, solicitud_id: int) -> str:
    actor = (
        str(getattr(current_user, "id", "") or "").strip()
        or str(getattr(current_user, "username", "") or "").strip()
        or "staff"
    )
    return f"admin:matching:ranking:v2:{int(solicitud_id)}:{actor}"


def _matching_batch_interview_ids(candidata_ids: list[int]) -> set[int]:
    ids = sorted({int(x) for x in (candidata_ids or []) if int(x or 0) > 0})
    if not ids:
        return set()
    try:
        rows = (
            db.session.query(Entrevista.candidata_id)
            .filter(Entrevista.candidata_id.in_(ids))
            .distinct()
            .all()
        )
    except Exception:
        return set()
    return {int(row[0]) for row in (rows or []) if row and int(row[0] or 0) > 0}


def _matching_build_ranking_map(ranked_candidates: list[dict]) -> dict[int, dict]:
    cand_ids = []
    for item in (ranked_candidates or []):
        cand = item.get("candidate") if isinstance(item, dict) else None
        cand_id = int(getattr(cand, "fila", 0) or 0)
        if cand_id > 0:
            cand_ids.append(cand_id)
    interview_ids = _matching_batch_interview_ids(cand_ids)

    out: dict[int, dict] = {}
    for item in (ranked_candidates or []):
        cand = item.get("candidate") if isinstance(item, dict) else None
        cand_id = int(getattr(cand, "fila", 0) or 0)
        if cand_id <= 0:
            continue
        out[cand_id] = {
            "score": int(item.get("score") or 0),
            "operational_score": int(item.get("operational_score") or 0),
            "bonus_test": int(item.get("bonus_test") or 0),
            "breakdown_snapshot": dict(item.get("breakdown_snapshot") or {}),
            "candidate_guard": build_candidate_guard(cand, has_interview=bool(cand_id in interview_ids)),
        }
    return out


def _matching_live_candidate_guard_map(*, candidata_ids: list[int]) -> dict[int, str]:
    ids = sorted({int(x) for x in (candidata_ids or []) if int(x or 0) > 0})
    if not ids:
        return {}
    candidates = (
        Candidata.query
        .filter(Candidata.fila.in_(ids))
        .all()
    )
    by_id = {
        int(getattr(cand, "fila", 0) or 0): cand
        for cand in (candidates or [])
        if int(getattr(cand, "fila", 0) or 0) > 0
    }
    interview_ids = _matching_batch_interview_ids(ids)
    out: dict[int, str] = {}
    for cand_id in ids:
        cand = by_id.get(cand_id)
        if cand is None:
            continue
        out[cand_id] = build_candidate_guard(cand, has_interview=bool(cand_id in interview_ids))
    return out


def _matching_store_ranking_cache(*, solicitud: Solicitud, ranking_map: dict[int, dict]) -> None:
    if bool(current_app.config.get("TESTING")):
        return
    cache_key = _matching_ranking_cache_key(solicitud_id=int(getattr(solicitud, "id", 0) or 0))
    payload = {
        "solicitud_fingerprint": build_solicitud_fingerprint(solicitud),
        "ranking_map": dict(ranking_map or {}),
    }
    try:
        cache.set(cache_key, payload, timeout=180)
    except Exception:
        return


def _matching_cached_ranking_map(
    *,
    solicitud: Solicitud,
    candidata_ids: list[int] | None = None,
) -> dict[int, dict]:
    if bool(current_app.config.get("TESTING")):
        return {}
    cache_key = _matching_ranking_cache_key(solicitud_id=int(getattr(solicitud, "id", 0) or 0))
    try:
        cached = cache.get(cache_key)
    except Exception:
        cached = None
    ranking_map = {}
    cached_fp = ""
    if isinstance(cached, dict):
        if isinstance(cached.get("ranking_map"), dict):
            ranking_map = dict(cached.get("ranking_map") or {})
            cached_fp = str(cached.get("solicitud_fingerprint") or "").strip().lower()
        else:
            ranking_map = dict(cached or {})
    if not ranking_map:
        return {}

    current_fp = build_solicitud_fingerprint(solicitud)
    if cached_fp and cached_fp != str(current_fp or "").strip().lower():
        return {}

    requested_ids = sorted({int(x) for x in (candidata_ids or []) if int(x or 0) > 0})
    if requested_ids:
        live_guards = _matching_live_candidate_guard_map(candidata_ids=requested_ids)
        for candidata_id in requested_ids:
            cached_item = ranking_map.get(int(candidata_id)) or {}
            cached_guard = str(cached_item.get("candidate_guard") or "").strip().lower()
            live_guard = str(live_guards.get(int(candidata_id)) or "").strip().lower()
            if not cached_guard or not live_guard or cached_guard != live_guard:
                return {}

    return ranking_map


def _matching_send_candidatas_result(
    *,
    solicitud_id: int,
    candidata_ids: list[int],
    force_send: bool,
    ranking_map: dict[int, dict] | None = None,
) -> dict:
    solicitud = Solicitud.query.filter_by(id=solicitud_id).first_or_404()
    if not candidata_ids:
        return _matching_send_result(
            success=False,
            message="Selecciona al menos una candidata para enviar.",
            category="warning",
            error_code="no_selection",
        )

    expected_version = _expected_row_version()
    if _critical_concurrency_guards_enabled() and expected_version is not None:
        current_version = int(getattr(solicitud, "row_version", 0) or 0)
        if int(expected_version) != current_version:
            return _matching_send_result(
                success=False,
                message="La solicitud cambió mientras trabajabas. Recarga y vuelve a intentar.",
                category="warning",
                error_code="conflict",
                status_code=409,
            )

    idem_row, duplicate = _claim_idempotency(
        scope="matching_send",
        entity_type="Solicitud",
        entity_id=solicitud.id,
        action="matching_enviar_candidatas",
    )
    if duplicate:
        if _idempotency_request_conflict(idem_row):
            return _matching_send_result(
                success=False,
                message=_idempotency_conflict_message(),
                category="warning",
                error_code="idempotency_conflict",
                status_code=409,
            )
        prev_status = int(getattr(idem_row, "response_status", 0) or 0)
        if 200 <= prev_status < 300:
            return _matching_send_result(
                success=True,
                message="Acción ya aplicada previamente.",
                category="info",
                status_code=200,
            )
        return _matching_send_result(
            success=False,
            message="Solicitud duplicada detectada. Espera y vuelve a intentar.",
            category="warning",
            error_code="conflict",
            status_code=409,
        )

    locked_candidates = _lock_candidatas_for_update(candidata_ids)

    if not locked_candidates:
        return _matching_send_result(
            success=False,
            message="No se encontraron candidatas válidas para enviar.",
            category="warning",
            error_code="no_valid_candidates",
        )

    blocked_candidate_ids, rejected_candidate_ids = _matching_candidate_flags(solicitud, candidata_ids)
    selected_blocked = sorted(set(candidata_ids) & blocked_candidate_ids)
    if selected_blocked:
        return _matching_send_result(
            success=False,
            message="Esta candidata ya fue enviada a otro cliente. Solo puede enviarse a otro cuando sea rechazada.",
            category="danger",
            error_code="blocked_other_client",
            status_code=409,
        )

    selected_rejected = sorted(set(candidata_ids) & rejected_candidate_ids)
    if selected_rejected and not force_send:
        return _matching_send_result(
            success=False,
            message="⚠️ Esta candidata fue rechazada por este cliente anteriormente. Marca 'Enviar de todas formas' para confirmar.",
            category="warning",
            error_code="rejected_without_force",
        )

    selected_disqualified = {
        int(c.fila)
        for c in locked_candidates.values()
        if candidata_esta_descalificada(c)
    }
    if selected_disqualified:
        return _matching_send_result(
            success=False,
            message="No se puede enviar una candidata descalificada al cliente.",
            category="danger",
            error_code="disqualified",
        )

    selected_not_ready = {}
    for c in locked_candidates.values():
        ready_ok, reasons = candidata_is_ready_to_send(c)
        if ready_ok:
            continue
        selected_not_ready[int(c.fila)] = [
            r for r in (reasons or []) if not str(r).lower().startswith("advertencia:")
        ]
    if selected_not_ready:
        sample_id = sorted(selected_not_ready.keys())[0]
        reasons = selected_not_ready.get(sample_id) or ["Faltan requisitos de completitud."]
        details = "; ".join(reasons[:4])
        return _matching_send_result(
            success=False,
            message=f"Esta candidata no está lista para enviar: {details}",
            category="danger",
            error_code="not_ready",
        )

    ranking_map = dict(ranking_map or {})
    if not ranking_map:
        ranking_map = _matching_cached_ranking_map(solicitud=solicitud, candidata_ids=candidata_ids)
    if not ranking_map:
        ranked_candidates = rank_candidates(solicitud, top_k=30)
        ranking_map = _matching_build_ranking_map(ranked_candidates)
        _matching_store_ranking_cache(solicitud=solicitud, ranking_map=ranking_map)
    created_by = _matching_created_by()
    processed_candidates = []
    state = {"processed": 0, "processed_ids": []}

    try:
        def _persist_matching_send(_attempt: int):
            state["processed"] = 0
            state["processed_ids"] = []
            processed_candidates.clear()
            existing_query = getattr(SolicitudCandidata, "query", None)
            existing_rows = []
            if existing_query is not None:
                if hasattr(existing_query, "filter"):
                    existing_rows = (
                        existing_query
                        .filter(
                            SolicitudCandidata.solicitud_id == int(solicitud.id),
                            SolicitudCandidata.candidata_id.in_(candidata_ids),
                        )
                        .all()
                    )
                elif hasattr(existing_query, "filter_by"):
                    for candidata_id in (candidata_ids or []):
                        try:
                            cid = int(candidata_id or 0)
                        except Exception:
                            cid = 0
                        if cid <= 0:
                            continue
                        row = (
                            existing_query
                            .filter_by(solicitud_id=int(solicitud.id), candidata_id=int(cid))
                            .first()
                        )
                        if row is not None:
                            existing_rows.append(row)
            existing_by_candidata = {
                int(getattr(row, "candidata_id", 0) or 0): row
                for row in (existing_rows or [])
                if int(getattr(row, "candidata_id", 0) or 0) > 0
            }
            for candidata_id in candidata_ids:
                cand = locked_candidates.get(int(candidata_id))
                if not cand:
                    continue

                exists = existing_by_candidata.get(int(candidata_id))
                exists_status = (getattr(exists, "status", "") or "").strip().lower() if exists else ""
                if exists and exists_status in _ACTIVE_ASSIGNMENT_STATUS:
                    raise ValueError("already_sent")

                ranked_item = ranking_map.get(candidata_id) or {"score": 0, "breakdown_snapshot": {}}
                breakdown_snapshot = ranked_item.get("breakdown_snapshot") or {
                    "city_detectada": "Ciudad no detectada",
                    "tokens_match": "Tokens sin coincidencia fuerte",
                    "rutas_match": "Rutas sin coincidencia fuerte",
                    "modalidad_match": "Sin datos",
                    "horario_match": "Sin datos",
                    "skills_match": "Sin datos",
                    "mascota_penalty": "Sin datos",
                    "test_bonus": "Bonus test: +0",
                    "components": list(ranked_item.get("breakdown") or []),
                }
                if exists:
                    exists.score_snapshot = int(ranked_item.get("score") or 0)
                    exists.breakdown_snapshot = breakdown_snapshot
                    exists.status = "enviada"
                    exists.created_by = created_by
                else:
                    row = SolicitudCandidata(
                        solicitud_id=solicitud.id,
                        candidata_id=candidata_id,
                        score_snapshot=int(ranked_item.get("score") or 0),
                        breakdown_snapshot=breakdown_snapshot,
                        status="enviada",
                        created_by=created_by,
                    )
                    db.session.add(row)
                state["processed"] = int(state.get("processed", 0)) + 1
                state["processed_ids"].append(candidata_id)
                processed_candidates.append(cand)

            if int(state.get("processed", 0)) > 0:
                _upsert_cliente_notificacion_candidatas(solicitud, int(state.get("processed", 0)))
                _emit_domain_outbox_event(
                    event_type="MATCHING_CANDIDATAS_ENVIADAS",
                    aggregate_type="Solicitud",
                    aggregate_id=solicitud.id,
                    aggregate_version=(int(getattr(solicitud, "row_version", 0) or 0) + 1),
                    payload={
                        "solicitud_id": int(solicitud.id),
                        "candidata_ids": list(state.get("processed_ids") or []),
                        "count": int(state.get("processed", 0) or 0),
                    },
                )
                _set_idempotency_response(idem_row, status=200, code="ok")

        def _verify_matching_rows() -> bool:
            if int(state.get("processed", 0)) <= 0:
                return False
            try:
                saved_count = (
                    SolicitudCandidata.query
                    .filter(
                        SolicitudCandidata.solicitud_id == solicitud.id,
                        SolicitudCandidata.candidata_id.in_(state.get("processed_ids") or []),
                        SolicitudCandidata.status == "enviada",
                    )
                    .count()
                )
                return int(saved_count) == len(state.get("processed_ids") or [])
            except Exception:
                return len(state.get("processed_ids") or []) == int(state.get("processed", 0))

        result = _execute_form_save(
            persist_fn=_persist_matching_send,
            verify_fn=_verify_matching_rows,
            entity_type="Solicitud",
            entity_id=solicitud.id,
            summary=f"Guardar envío matching solicitud {solicitud.id}",
            metadata={"candidata_ids": candidata_ids},
        )

        if int(state.get("processed", 0)) > 0 and result.ok:
            _audit_log(
                action_type="MATCHING_SEND",
                entity_type="Solicitud",
                entity_id=solicitud.id,
                summary=f"Envío de candidatas en matching para solicitud {solicitud.codigo_solicitud or solicitud.id}",
                metadata={"candidata_ids": candidata_ids, "processed": int(state.get("processed", 0))},
            )
            for cand in processed_candidates:
                log_candidata_action(
                    action_type="MATCHING_SEND",
                    candidata=cand,
                    summary=f"Candidata enviada en matching a solicitud {solicitud.codigo_solicitud or solicitud.id}",
                    metadata={"solicitud_id": solicitud.id, "cliente_id": getattr(solicitud, "cliente_id", None)},
                    success=True,
                )
            return _matching_send_result(
                success=True,
                message=f"Candidata enviada al cliente. Total procesadas: {int(state.get('processed', 0))}.",
                category="success",
            )

        if not result.ok and str(getattr(result, "error_message", "") or "").strip().lower() == "already_sent":
            db.session.rollback()
            return _matching_send_result(
                success=False,
                message="Una o más candidatas ya fueron enviadas en otra sesión. Recarga e intenta nuevamente.",
                category="warning",
                error_code="conflict",
                status_code=409,
            )

        if not result.ok:
            log_error_event(
                error_type="MATCHING_ERROR",
                exc=f"Error robusto enviando candidatas en matching: {result.error_message}",
                route=request.path,
                entity_type="solicitud",
                entity_id=solicitud.id,
                request_id=request.headers.get("X-Request-ID"),
                status_code=500,
            )
            _audit_log(
                action_type="MATCHING_SEND",
                entity_type="Solicitud",
                entity_id=solicitud.id,
                summary=f"Fallo enviando candidatas en matching para solicitud {solicitud.id}",
                metadata={"candidata_ids": candidata_ids, "processed_ids": state.get("processed_ids") or []},
                success=False,
                error="No se pudieron enviar candidatas.",
            )
            for cand in processed_candidates:
                log_candidata_action(
                    action_type="MATCHING_SEND",
                    candidata=cand,
                    summary=f"Fallo enviando candidata en matching para solicitud {solicitud.id}",
                    metadata={"solicitud_id": solicitud.id, "cliente_id": getattr(solicitud, "cliente_id", None)},
                    success=False,
                    error="No se pudieron enviar candidatas.",
                )
            return _matching_send_result(
                success=False,
                message="No se pudo guardar correctamente. Intente nuevamente.",
                category="danger",
                error_code="send_failed",
            )

        db.session.rollback()
        return _matching_send_result(
            success=False,
            message="No se encontraron candidatas válidas para enviar.",
            category="warning",
            error_code="no_valid_candidates",
        )
    except Exception:
        db.session.rollback()
        log_error_event(
            error_type="MATCHING_ERROR",
            exc="Error enviando candidatas en matching",
            route=request.path,
            entity_type="solicitud",
            entity_id=solicitud.id,
            request_id=request.headers.get("X-Request-ID"),
            status_code=500,
        )
        _audit_log(
            action_type="MATCHING_SEND",
            entity_type="Solicitud",
            entity_id=solicitud.id,
            summary=f"Fallo enviando candidatas en matching para solicitud {solicitud.id}",
            metadata={"candidata_ids": candidata_ids},
            success=False,
            error="No se pudieron enviar candidatas.",
        )
        for cand in processed_candidates:
            log_candidata_action(
                action_type="MATCHING_SEND",
                candidata=cand,
                summary=f"Fallo enviando candidata en matching para solicitud {solicitud.id}",
                metadata={"solicitud_id": solicitud.id, "cliente_id": getattr(solicitud, "cliente_id", None)},
                success=False,
                error="No se pudieron enviar candidatas.",
            )
        return _matching_send_result(
            success=False,
            message="No se pudieron enviar candidatas. Intenta nuevamente.",
            category="danger",
            error_code="unexpected_error",
            status_code=500,
        )


@admin_bp.route('/matching/solicitudes')
@login_required
@staff_required
def matching_solicitudes():
    solicitudes = (
        Solicitud.query
        .options(joinedload(Solicitud.cliente))
        .filter(Solicitud.estado.in_(("activa", "reemplazo")))
        .order_by(Solicitud.fecha_solicitud.desc(), Solicitud.id.desc())
        .limit(300)
        .all()
    )
    if _admin_async_wants_json():
        html = render_template(
            "admin/_matching_solicitudes_region.html",
            solicitudes=solicitudes,
        )
        return jsonify(_admin_async_payload(
            success=True,
            message='Listado de matching actualizado.',
            category='info',
            redirect_url=url_for('admin.matching_solicitudes'),
            replace_html=html,
            update_target='#matchingSolicitudesAsyncRegion',
        )), 200
    return render_template("admin/matching_solicitudes.html", solicitudes=solicitudes)


@admin_bp.route('/matching/solicitudes/<int:solicitud_id>')
@login_required
@staff_required
def matching_detalle_solicitud(solicitud_id: int):
    solicitud = (
        Solicitud.query
        .options(joinedload(Solicitud.cliente), joinedload(Solicitud.reemplazos))
        .filter_by(id=solicitud_id)
        .first_or_404()
    )
    has_reemplazo_activo = _active_reemplazo_for_solicitud(solicitud) is not None
    ranked_candidates = rank_candidates(solicitud, top_k=30)
    _matching_store_ranking_cache(solicitud=solicitud, ranking_map=_matching_build_ranking_map(ranked_candidates))
    ranked_candidate_ids = []
    for item in ranked_candidates:
        try:
            ranked_candidate_ids.append(int(item["candidate"].fila))
        except Exception:
            continue
    blocked_candidate_ids, rejected_candidate_ids = _matching_candidate_flags(solicitud, ranked_candidate_ids)
    disqualified_candidate_ids = {
        int(item["candidate"].fila)
        for item in ranked_candidates
        if candidata_esta_descalificada(item.get("candidate"))
    }
    sent_candidates = (
        SolicitudCandidata.query
        .filter_by(solicitud_id=solicitud.id)
        .order_by(SolicitudCandidata.created_at.desc(), SolicitudCandidata.id.desc())
        .all()
    )
    async_fragment = (request.args.get("fragment") or "").strip().lower()
    template_ctx = dict(
        solicitud=solicitud,
        form_idempotency_key=_new_form_idempotency_key(),
        ranked_candidates=ranked_candidates,
        sent_candidates=sent_candidates,
        blocked_candidate_ids=blocked_candidate_ids,
        rejected_candidate_ids=rejected_candidate_ids,
        disqualified_candidate_ids=disqualified_candidate_ids,
        has_reemplazo_activo=has_reemplazo_activo,
        async_fragment=async_fragment,
    )
    if _admin_async_wants_json():
        update_target = '#matchingDetalleAsyncRegion'
        if async_fragment == 'ranking':
            update_target = '#matchingRankingAsyncRegion'
        elif async_fragment in ('historial', 'history'):
            update_target = '#matchingHistoryAsyncRegion'
        html = render_template("admin/_matching_detalle_region.html", **template_ctx)
        return jsonify(_admin_async_payload(
            success=True,
            message='Detalle de matching actualizado.',
            category='info',
            redirect_url=url_for('admin.matching_detalle_solicitud', solicitud_id=solicitud.id),
            replace_html=html,
            update_target=update_target,
        )), 200
    return render_template(
        "admin/matching_detalle.html",
        **template_ctx,
    )


@admin_bp.route('/matching/solicitudes/<int:solicitud_id>/enviar', methods=['POST'])
@login_required
@staff_required
def matching_enviar_candidatas(solicitud_id: int):
    candidata_ids = _parse_matching_candidata_ids(request.form.getlist("candidata_ids"))
    force_send = _matching_force_send_enabled(request.form.get("force_send"))
    result = _matching_send_candidatas_result(
        solicitud_id=solicitud_id,
        candidata_ids=candidata_ids,
        force_send=force_send,
    )
    if result.get("success"):
        flash(result.get("message") or "Candidatas enviadas.", result.get("category") or "success")
        return redirect(url_for("admin.matching_detalle_solicitud", solicitud_id=solicitud_id))

    error_code = str(result.get("error_code") or "").strip()
    if error_code in ("conflict", "idempotency_conflict", "blocked_other_client"):
        flash(result.get("message") or "Conflicto detectado. Recarga e intenta nuevamente.", result.get("category") or "warning")
        return redirect(url_for("admin.matching_detalle_solicitud", solicitud_id=solicitud_id))
    if error_code == "disqualified":
        abort(403, description=result.get("message") or "No se puede enviar una candidata descalificada al cliente.")
    if error_code == "not_ready":
        flash(result.get("message") or "Esta candidata no está lista para enviar.", "danger")
        abort(400, description=result.get("message") or "Esta candidata no está lista para enviar.")

    flash(result.get("message") or "No se pudieron enviar candidatas. Intenta nuevamente.", result.get("category") or "danger")
    return redirect(url_for("admin.matching_detalle_solicitud", solicitud_id=solicitud_id))


@admin_bp.route('/matching/solicitudes/<int:solicitud_id>/enviar/ui', methods=['POST'])
@login_required
@staff_required
def matching_enviar_candidatas_ui(solicitud_id: int):
    candidata_ids = _parse_matching_candidata_ids(request.form.getlist("candidata_ids"))
    force_send = _matching_force_send_enabled(request.form.get("force_send"))
    detail_url = url_for("admin.matching_detalle_solicitud", solicitud_id=solicitud_id)
    try:
        result = _matching_send_candidatas_result(
            solicitud_id=solicitud_id,
            candidata_ids=candidata_ids,
            force_send=force_send,
        )
    except Exception:
        result = _matching_send_result(
            success=False,
            message="No se pudieron enviar candidatas. Intenta nuevamente.",
            category="danger",
            error_code="unexpected_error",
            status_code=500,
        )

    http_status = int(result.get("status_code") or 200)
    if http_status < 200 or http_status > 599:
        http_status = 200
    try:
        solicitud = (
            Solicitud.query
            .options(joinedload(Solicitud.cliente), joinedload(Solicitud.reemplazos))
            .filter_by(id=solicitud_id)
            .first_or_404()
        )
        has_reemplazo_activo = _active_reemplazo_for_solicitud(solicitud) is not None
        ranked_candidates = rank_candidates(solicitud, top_k=30)
        _matching_store_ranking_cache(solicitud=solicitud, ranking_map=_matching_build_ranking_map(ranked_candidates))
        ranked_candidate_ids = []
        for item in ranked_candidates:
            try:
                ranked_candidate_ids.append(int(item["candidate"].fila))
            except Exception:
                continue
        blocked_candidate_ids, rejected_candidate_ids = _matching_candidate_flags(solicitud, ranked_candidate_ids)
        disqualified_candidate_ids = {
            int(item["candidate"].fila)
            for item in ranked_candidates
            if candidata_esta_descalificada(item.get("candidate"))
        }
        sent_candidates = (
            SolicitudCandidata.query
            .filter_by(solicitud_id=solicitud.id)
            .order_by(SolicitudCandidata.created_at.desc(), SolicitudCandidata.id.desc())
            .all()
        )
        replace_html = render_template(
            "admin/_matching_detalle_region.html",
            solicitud=solicitud,
            form_idempotency_key=_new_form_idempotency_key(),
            ranked_candidates=ranked_candidates,
            sent_candidates=sent_candidates,
            blocked_candidate_ids=blocked_candidate_ids,
            rejected_candidate_ids=rejected_candidate_ids,
            disqualified_candidate_ids=disqualified_candidate_ids,
            has_reemplazo_activo=has_reemplazo_activo,
            async_fragment="",
        )
    except Exception:
        replace_html = None

    return jsonify(_admin_async_payload(
        success=bool(result.get("success")),
        message=result.get("message") or "",
        category=result.get("category") or ("success" if result.get("success") else "danger"),
        redirect_url=detail_url,
        replace_html=replace_html,
        update_target="#matchingDetalleAsyncRegion",
        error_code=result.get("error_code"),
    )), http_status


def _blob_len_expr(col):
    dialect = ""
    try:
        bind = db.session.get_bind()
        if bind is not None and bind.dialect is not None:
            dialect = str(bind.dialect.name or "").lower()
    except Exception:
        dialect = ""

    if dialect == "postgresql":
        return func.coalesce(func.octet_length(col), 0)
    return func.coalesce(func.length(col), 0)


def _build_auditoria_completitud_rows(q: str = "") -> list[dict]:
    q = (q or "").strip()[:128]
    base = Candidata.query.options(
        load_only(
            Candidata.fila,
            Candidata.nombre_completo,
            Candidata.cedula,
            Candidata.codigo,
            Candidata.estado,
            Candidata.entrevista,
            Candidata.referencias_laboral,
            Candidata.referencias_familiares,
        )
    ).filter(
        Candidata.codigo.isnot(None),
        Candidata.codigo != "",
        func.length(func.trim(Candidata.codigo)) > 0,
    )
    if q:
        like = f"%{q}%"
        base = base.filter(
            or_(
                Candidata.nombre_completo.ilike(like),
                Candidata.cedula.ilike(like),
                Candidata.codigo.ilike(like),
            )
        )

    entrevistas_subq = (
        db.session.query(
            Entrevista.candidata_id.label("candidata_id"),
            func.count(Entrevista.id).label("entrevistas_count"),
        )
        .group_by(Entrevista.candidata_id)
        .subquery()
    )

    rows = (
        base.outerjoin(entrevistas_subq, entrevistas_subq.c.candidata_id == Candidata.fila)
        .add_columns(
            func.coalesce(entrevistas_subq.c.entrevistas_count, 0).label("entrevistas_count"),
            _blob_len_expr(Candidata.depuracion).label("depuracion_len"),
            _blob_len_expr(Candidata.perfil).label("perfil_len"),
            _blob_len_expr(Candidata.cedula1).label("cedula1_len"),
            _blob_len_expr(Candidata.cedula2).label("cedula2_len"),
        )
        .order_by(Candidata.fila.desc())
        .all()
    )

    audits: list[dict] = []
    for cand, entrevistas_count, dep_len, perfil_len, ced1_len, ced2_len in rows:
        flags = {
            "entrevista": entrevista_ok(getattr(cand, "entrevista", None), entrevistas_count),
            "depuracion": binario_ok(dep_len),
            "perfil": binario_ok(perfil_len),
            "cedula1": binario_ok(ced1_len),
            "cedula2": binario_ok(ced2_len),
            "referencias_laboral": referencias_ok(getattr(cand, "referencias_laboral", None)),
            "referencias_familiares": referencias_ok(getattr(cand, "referencias_familiares", None)),
        }
        faltantes = faltantes_desde_flags(flags)
        audits.append(
            {
                "candidata": cand,
                "flags": flags,
                "faltantes": faltantes,
                "tiene": [k for k, ok in flags.items() if ok],
                "incompleta": es_incompleta(flags),
            }
        )
    return audits


def _links_completar_por_faltantes(candidata_id: int, faltantes: list[str]) -> list[dict]:
    faltantes_set = set(faltantes or [])
    links: list[dict] = []

    if faltantes_set.intersection({"depuracion", "perfil", "cedula1", "cedula2"}):
        links.append(
            {
                "label": "Documentos",
                "url": url_for("subir_fotos.subir_fotos", accion="subir", fila=candidata_id),
            }
        )
    if "entrevista" in faltantes_set:
        links.append(
            {
                "label": "Entrevista",
                "url": url_for("entrevistas_de_candidata", fila=candidata_id),
            }
        )
    if faltantes_set.intersection({"referencias_laboral", "referencias_familiares"}):
        links.append(
            {
                "label": "Referencias",
                "url": url_for("referencias", candidata=candidata_id),
            }
        )
    links.append(
        {
            "label": "Editar candidata",
            "url": url_for("buscar_candidata", candidata_id=candidata_id),
        }
    )
    return links


_CANDIDATAS_FINALIZAR_BADGE_CACHE_KEY = "admin:candidatas:por_finalizar:badge:v1"
_CANDIDATAS_FINALIZAR_BADGE_TTL_SEC_DEFAULT = 60
_CANDIDATAS_FINALIZAR_URGENCIA_RANK = {"critica": 4, "alta": 3, "media": 2, "baja": 1}
_CANDIDATAS_FINALIZAR_URGENCIA_BADGE = {
    "critica": "text-bg-danger",
    "alta": "text-bg-warning",
    "media": "text-bg-info",
    "baja": "text-bg-secondary",
}
_CANDIDATAS_FINALIZAR_LABELS = {
    "entrevista": "Entrevista",
    "referencias_laboral": "Ref laboral",
    "referencias_familiares": "Ref familiar",
    "depuracion": "Depuración",
    "perfil": "Perfil",
    "cedula1": "Cédula 1",
    "cedula2": "Cédula 2",
    "codigo": "Código interno",
}


def _candidatas_finalizar_badge_ttl_sec() -> int:
    try:
        raw = int(
            (
                os.getenv("ADMIN_CANDIDATAS_FINALIZAR_BADGE_TTL_SEC")
                or str(_CANDIDATAS_FINALIZAR_BADGE_TTL_SEC_DEFAULT)
            ).strip()
        )
    except Exception:
        raw = _CANDIDATAS_FINALIZAR_BADGE_TTL_SEC_DEFAULT
    return max(20, min(raw, 300))


def _candidatas_finalizar_badge_cache_get() -> int | None:
    if not _cache_ok():
        return None
    try:
        raw = cache.get(_CANDIDATAS_FINALIZAR_BADGE_CACHE_KEY)
        if raw is None:
            return None
        value = int(raw)
        return value if value >= 0 else 0
    except Exception:
        return None


def _candidatas_finalizar_badge_cache_set(value: int) -> None:
    if not _cache_ok():
        return
    try:
        cache.set(
            _CANDIDATAS_FINALIZAR_BADGE_CACHE_KEY,
            max(0, int(value or 0)),
            timeout=_candidatas_finalizar_badge_ttl_sec(),
        )
    except Exception:
        return


def _dias_sin_avance(candidata, now_dt: datetime) -> int:
    anchor = getattr(candidata, "fecha_cambio_estado", None) or getattr(candidata, "marca_temporal", None)
    if not isinstance(anchor, datetime):
        return 0
    delta = now_dt - anchor
    return max(0, int(delta.days))


def _urgencia_finalizacion(*, faltantes: list[str], estado_inconsistente: bool, dias_sin_avance: int) -> str:
    score = 0
    faltantes_set = set(faltantes or [])
    if estado_inconsistente:
        score += 5
    if "codigo" in faltantes_set:
        score += 4
    if "entrevista" in faltantes_set:
        score += 3
    if "referencias_laboral" in faltantes_set:
        score += 2
    if "referencias_familiares" in faltantes_set:
        score += 2
    if "cedula1" in faltantes_set:
        score += 2
    if "cedula2" in faltantes_set:
        score += 2
    if "depuracion" in faltantes_set:
        score += 1
    if "perfil" in faltantes_set:
        score += 1

    if dias_sin_avance >= 21:
        score += 4
    elif dias_sin_avance >= 14:
        score += 3
    elif dias_sin_avance >= 7:
        score += 2
    elif dias_sin_avance >= 3:
        score += 1

    # Escala actual: 0..26 puntos.
    # Conserva la intención original (70/45/20) reescalada a esta base:
    # crítica >= 18, alta >= 12, media >= 5.
    if score >= 18:
        return "critica"
    if score >= 12:
        return "alta"
    if score >= 5:
        return "media"
    return "baja"


def _siguiente_paso_finalizacion(
    *,
    candidata_id: int,
    estado_actual: str,
    faltantes: list[str],
    ready_real: bool,
    estado_inconsistente: bool,
) -> dict:
    faltantes_set = set(faltantes or [])
    if estado_inconsistente or "codigo" in faltantes_set:
        return {
            "label": "revisar estado",
            "accion_label": "Revisar estado",
            "url": url_for("buscar_candidata", candidata_id=candidata_id),
            "method": "get",
        }
    if "entrevista" in faltantes_set:
        return {
            "label": "completar entrevista",
            "accion_label": "Completar entrevista",
            "url": url_for("entrevistas_de_candidata", fila=candidata_id),
            "method": "get",
        }
    if faltantes_set.intersection({"referencias_laboral", "referencias_familiares"}):
        return {
            "label": "verificar referencias",
            "accion_label": "Verificar referencias",
            "url": url_for("referencias", candidata=candidata_id),
            "method": "get",
        }
    if "depuracion" in faltantes_set:
        return {
            "label": "subir depuración",
            "accion_label": "Subir depuración",
            "url": url_for("subir_fotos.subir_fotos", accion="subir", fila=candidata_id),
            "method": "get",
        }
    if "perfil" in faltantes_set:
        return {
            "label": "subir perfil",
            "accion_label": "Subir perfil",
            "url": url_for("subir_fotos.subir_fotos", accion="subir", fila=candidata_id),
            "method": "get",
        }
    if faltantes_set.intersection({"cedula1", "cedula2"}):
        return {
            "label": "subir cédulas",
            "accion_label": "Subir cédulas",
            "url": url_for("subir_fotos.subir_fotos", accion="subir", fila=candidata_id),
            "method": "get",
        }
    if ready_real and estado_actual != "lista_para_trabajar":
        return {
            "label": "marcar lista para trabajar si ya cumple",
            "accion_label": "Marcar lista",
            "url": url_for("admin.marcar_candidata_lista_para_trabajar", candidata_id=candidata_id),
            "method": "post",
        }
    return {
        "label": "revisar estado",
        "accion_label": "Revisar estado",
        "url": url_for("buscar_candidata", candidata_id=candidata_id),
        "method": "get",
    }


def _build_candidatas_por_finalizar_rows(q: str = "", *, count_only: bool = False) -> list[dict] | int:
    q = (q or "").strip()[:128]
    base = Candidata.query.options(
        load_only(
            Candidata.fila,
            Candidata.nombre_completo,
            Candidata.cedula,
            Candidata.codigo,
            Candidata.estado,
            Candidata.entrevista,
            Candidata.referencias_laboral,
            Candidata.referencias_familiares,
            Candidata.contactos_referencias_laborales,
            Candidata.referencias_familiares_detalle,
            Candidata.fecha_cambio_estado,
            Candidata.marca_temporal,
        )
    ).filter(
        Candidata.estado.notin_(["descalificada", "trabajando"]),
    )
    if q:
        like = f"%{q}%"
        base = base.filter(
            or_(
                Candidata.nombre_completo.ilike(like),
                Candidata.cedula.ilike(like),
                Candidata.codigo.ilike(like),
            )
        )

    entrevistas_subq = (
        db.session.query(
            Entrevista.candidata_id.label("candidata_id"),
            func.count(Entrevista.id).label("entrevistas_count"),
        )
        .group_by(Entrevista.candidata_id)
        .subquery()
    )

    rows_query = (
        base.outerjoin(entrevistas_subq, entrevistas_subq.c.candidata_id == Candidata.fila)
        .add_columns(
            func.coalesce(entrevistas_subq.c.entrevistas_count, 0).label("entrevistas_count"),
            _blob_len_expr(Candidata.depuracion).label("depuracion_len"),
            _blob_len_expr(Candidata.perfil).label("perfil_len"),
            _blob_len_expr(Candidata.cedula1).label("cedula1_len"),
            _blob_len_expr(Candidata.cedula2).label("cedula2_len"),
        )
    )
    if not count_only:
        rows_query = rows_query.order_by(Candidata.fila.desc())
    rows_iter = rows_query.yield_per(200) if count_only else rows_query.all()

    now_dt = utc_now_naive()
    result: list[dict] = []
    count_result = 0
    for cand, entrevistas_count, dep_len, perfil_len, ced1_len, ced2_len in rows_iter:
        falta_entrevista = not entrevista_ok(getattr(cand, "entrevista", None), entrevistas_count)
        ref_lab_txt = getattr(cand, "contactos_referencias_laborales", None) or getattr(cand, "referencias_laboral", None)
        ref_fam_txt = getattr(cand, "referencias_familiares_detalle", None) or getattr(cand, "referencias_familiares", None)
        falta_ref_lab = not referencias_ok(ref_lab_txt)
        falta_ref_fam = not referencias_ok(ref_fam_txt)
        falta_depuracion = not binario_ok(dep_len)
        falta_perfil = not binario_ok(perfil_len)
        falta_cedula1 = not binario_ok(ced1_len)
        falta_cedula2 = not binario_ok(ced2_len)
        falta_codigo = not candidata_tiene_codigo_valido(getattr(cand, "codigo", None))

        faltantes = []
        if falta_entrevista:
            faltantes.append("entrevista")
        if falta_ref_lab:
            faltantes.append("referencias_laboral")
        if falta_ref_fam:
            faltantes.append("referencias_familiares")
        if falta_depuracion:
            faltantes.append("depuracion")
        if falta_perfil:
            faltantes.append("perfil")
        if falta_cedula1:
            faltantes.append("cedula1")
        if falta_cedula2:
            faltantes.append("cedula2")
        if falta_codigo:
            faltantes.append("codigo")

        ready_snapshot = SimpleNamespace(
            estado=getattr(cand, "estado", None),
            codigo=getattr(cand, "codigo", None),
            entrevista=getattr(cand, "entrevista", None),
            referencias_laboral=getattr(cand, "referencias_laboral", None),
            referencias_familiares=getattr(cand, "referencias_familiares", None),
            contactos_referencias_laborales=getattr(cand, "contactos_referencias_laborales", None),
            referencias_familiares_detalle=getattr(cand, "referencias_familiares_detalle", None),
            depuracion=int(dep_len or 0),
            perfil=int(perfil_len or 0),
            cedula1=int(ced1_len or 0),
            cedula2=int(ced2_len or 0),
            entrevistas_nuevas=SimpleNamespace(count=(lambda n=int(entrevistas_count or 0): n)),
        )
        ready_real, ready_reasons = candidata_is_ready_to_send(ready_snapshot)
        estado_actual = (getattr(cand, "estado", None) or "").strip().lower()
        estado_inconsistente = (
            (ready_real and estado_actual != "lista_para_trabajar")
            or ((not ready_real) and estado_actual == "lista_para_trabajar")
        )
        needs_action = bool(faltantes) or estado_inconsistente or (estado_actual != "lista_para_trabajar")
        if not needs_action:
            continue
        if count_only:
            count_result += 1
            continue

        dias = _dias_sin_avance(cand, now_dt)
        urgencia = _urgencia_finalizacion(
            faltantes=faltantes,
            estado_inconsistente=estado_inconsistente,
            dias_sin_avance=dias,
        )
        siguiente = _siguiente_paso_finalizacion(
            candidata_id=int(cand.fila),
            estado_actual=estado_actual,
            faltantes=faltantes,
            ready_real=ready_real,
            estado_inconsistente=estado_inconsistente,
        )
        links = _links_completar_por_faltantes(int(cand.fila), faltantes)
        if estado_actual != "lista_para_trabajar":
            links.append(
                {
                    "label": "Revisar estado",
                    "url": url_for("buscar_candidata", candidata_id=int(cand.fila)),
                }
            )

        result.append(
            {
                "candidata": cand,
                "estado_actual": estado_actual or "sin_estado",
                "dias_sin_avance": dias,
                "faltantes": faltantes,
                "faltantes_labels": [_CANDIDATAS_FINALIZAR_LABELS.get(k, k) for k in faltantes],
                "urgencia": urgencia,
                "urgencia_badge": _CANDIDATAS_FINALIZAR_URGENCIA_BADGE.get(urgencia, "text-bg-secondary"),
                "siguiente_paso": siguiente,
                "links": links,
                "ready_real": bool(ready_real),
                "ready_reasons": list(ready_reasons or []),
                "estado_inconsistente": bool(estado_inconsistente),
            }
        )

    result.sort(
        key=lambda r: (
            -int(_CANDIDATAS_FINALIZAR_URGENCIA_RANK.get(r.get("urgencia"), 1)),
            -int(r.get("dias_sin_avance") or 0),
            -int(getattr(r.get("candidata"), "fila", 0) or 0),
        )
    )
    if count_only:
        return int(count_result)
    return result


def _candidatas_por_finalizar_badge_count() -> int:
    cached = _candidatas_finalizar_badge_cache_get()
    if cached is not None:
        return max(0, int(cached))
    try:
        value = int(_build_candidatas_por_finalizar_rows(q="", count_only=True))
    except Exception:
        value = 0
    _candidatas_finalizar_badge_cache_set(value)
    return max(0, int(value))


@admin_bp.app_template_global("candidatas_por_finalizar_badge_count")
def candidatas_por_finalizar_badge_count() -> int:
    if not has_request_context():
        return 0
    if not bool(getattr(current_user, "is_authenticated", False)):
        return 0
    role = (
        str(getattr(current_user, "role", "") or "").strip().lower()
        or str(session.get("role", "") or "").strip().lower()
    )
    if role not in {"owner", "admin", "secretaria"}:
        return 0
    return _candidatas_por_finalizar_badge_count()


@admin_bp.route('/candidatas/por-finalizar', methods=['GET'])
@login_required
@staff_required
def candidatas_por_finalizar():
    q = (request.args.get("q") or "").strip()[:128]
    urgencia = (request.args.get("urgencia") or "").strip().lower()
    rows = _build_candidatas_por_finalizar_rows(q=q)
    if urgencia in {"critica", "alta", "media", "baja"}:
        rows = [r for r in rows if r.get("urgencia") == urgencia]

    if not q and urgencia not in {"critica", "alta", "media", "baja"}:
        _candidatas_finalizar_badge_cache_set(len(rows))

    resumen = {
        "total": len(rows),
        "critica": sum(1 for r in rows if r.get("urgencia") == "critica"),
        "alta": sum(1 for r in rows if r.get("urgencia") == "alta"),
        "media": sum(1 for r in rows if r.get("urgencia") == "media"),
        "baja": sum(1 for r in rows if r.get("urgencia") == "baja"),
    }
    return render_template(
        "admin/candidatas_por_finalizar.html",
        q=q,
        urgencia=urgencia,
        resumen=resumen,
        rows=rows,
        labels=_CANDIDATAS_FINALIZAR_LABELS,
        next_url=request.full_path if request.query_string else request.path,
    )


@admin_bp.route('/candidatas/auditoria-completitud', methods=['GET'])
@login_required
@staff_required
def candidatas_auditoria_completitud():
    q = (request.args.get("q") or "").strip()[:128]
    solo_criticas = (request.args.get("solo_criticas") or "").strip() in ("1", "true", "on")
    solo_docs = (request.args.get("solo_docs") or "").strip() in ("1", "true", "on")
    solo_refs = (request.args.get("solo_refs") or "").strip() in ("1", "true", "on")

    audits_all = _build_auditoria_completitud_rows(q=q)
    total_analizadas = len(audits_all)
    completas = sum(
        1
        for a in audits_all
        if candidata_tiene_codigo_valido(getattr(a.get("candidata"), "codigo", None)) and not a["incompleta"]
    )
    incompletas = [
        a for a in audits_all
        if candidata_tiene_codigo_valido(getattr(a.get("candidata"), "codigo", None)) and a["incompleta"]
    ]

    if solo_criticas:
        incompletas = [a for a in incompletas if solo_criticos(a["faltantes"])]
    if solo_docs:
        incompletas = [a for a in incompletas if solo_sin_documentos(a["faltantes"])]
    if solo_refs:
        incompletas = [a for a in incompletas if solo_sin_referencias(a["faltantes"])]

    labels = {
        "entrevista": "Entrevista",
        "depuracion": "Depuración",
        "perfil": "Perfil",
        "cedula1": "Cédula 1",
        "cedula2": "Cédula 2",
        "referencias_laboral": "Ref laboral",
        "referencias_familiares": "Ref familiar",
    }
    for row in incompletas:
        cand = row["candidata"]
        row["tiene_labels"] = [labels[k] for k in row["tiene"]]
        row["faltantes_labels"] = [labels[k] for k in row["faltantes"]]
        row["links_completar"] = _links_completar_por_faltantes(cand.fila, row["faltantes"])

    return render_template(
        "admin/candidatas_auditoria_completitud.html",
        q=q,
        total_analizadas=total_analizadas,
        total_completas=completas,
        total_incompletas=len(incompletas),
        auditorias=incompletas,
        solo_criticas=solo_criticas,
        solo_docs=solo_docs,
        solo_refs=solo_refs,
        labels=labels,
    )


@admin_bp.route('/candidatas/descalificacion', methods=['GET'])
@login_required
@staff_required
def candidatas_descalificacion():
    q = (request.args.get("q") or "").strip()[:128]
    page = max(1, request.args.get("page", default=1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", default=25, type=int)))

    base = Candidata.query
    if q:
        like = f"%{q}%"
        base = base.filter(
            or_(
                Candidata.nombre_completo.ilike(like),
                Candidata.cedula.ilike(like),
                Candidata.codigo.ilike(like),
            )
        )

    pagination = (
        base.order_by(Candidata.fila.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    role = (
        str(getattr(current_user, "role", "") or "").strip().lower()
        or str(session.get("role", "") or "").strip().lower()
    )
    is_admin_role = role in ("owner", "admin")
    return render_template(
        "admin/candidatas_descalificacion.html",
        q=q,
        candidatas=pagination.items,
        pagination=pagination,
        page=page,
        per_page=per_page,
        is_admin_role=is_admin_role,
    )


@admin_bp.route('/candidatas/<int:candidata_id>/descalificar', methods=['POST'])
@login_required
@admin_required
def descalificar_candidata(candidata_id: int):
    cand = Candidata.query.filter_by(fila=candidata_id).first_or_404()
    motivo = (request.form.get("motivo") or "").strip()
    next_url = (request.form.get("next") or "").strip()
    fallback = url_for("buscar_candidata", candidata_id=cand.fila)

    if not motivo:
        flash("Debes indicar el motivo de descalificación.", "warning")
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    actor = (
        getattr(current_user, "username", None)
        or getattr(current_user, "id", None)
        or session.get("usuario")
        or "sistema"
    )

    try:
        invariant_change_candidate_state(
            candidata_id=int(cand.fila),
            new_state="descalificada",
            actor=str(actor),
            nota_descalificacion=motivo,
            reason=motivo,
            candidata_obj=cand,
        )
        db.session.commit()
        _audit_log(
            action_type="CANDIDATA_DESCALIFICAR",
            entity_type="Candidata",
            entity_id=cand.fila,
            summary=f"Candidata descalificada: {cand.nombre_completo or cand.fila}",
            metadata={"motivo": motivo},
            changes={"estado": {"from": None, "to": "descalificada"}},
        )
        log_candidata_action(
            action_type="CANDIDATA_DESQUALIFY",
            candidata=cand,
            summary=f"Candidata descalificada: {cand.nombre_completo or cand.fila}",
            metadata={"motivo": motivo},
            changes={"estado": {"from": None, "to": "descalificada"}},
            success=True,
        )
        _emit_domain_outbox_event(
            event_type="CANDIDATA_ESTADO_CAMBIADO",
            aggregate_type="Candidata",
            aggregate_id=int(cand.fila),
            aggregate_version=None,
            payload={"candidata_id": int(cand.fila), "to": "descalificada", "reason": motivo[:255]},
        )
        db.session.commit()
        flash("Candidata descalificada correctamente.", "success")
    except InvariantConflictError as inv_exc:
        db.session.rollback()
        flash(str(inv_exc) or "Conflicto de estado de candidata.", "warning")
    except Exception:
        db.session.rollback()
        _audit_log(
            action_type="CANDIDATA_DESCALIFICAR",
            entity_type="Candidata",
            entity_id=cand.fila,
            summary=f"Fallo descalificando candidata {cand.fila}",
            metadata={"motivo": motivo},
            success=False,
            error="No se pudo descalificar la candidata.",
        )
        log_candidata_action(
            action_type="CANDIDATA_DESQUALIFY",
            candidata=cand,
            summary=f"Fallo descalificando candidata {cand.fila}",
            metadata={"motivo": motivo},
            success=False,
            error="No se pudo descalificar la candidata.",
        )
        flash("No se pudo descalificar la candidata.", "danger")

    return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)


@admin_bp.route('/candidatas/<int:candidata_id>/reactivar', methods=['POST'])
@login_required
@admin_required
def reactivar_candidata(candidata_id: int):
    cand = Candidata.query.filter_by(fila=candidata_id).first_or_404()
    next_url = (request.form.get("next") or "").strip()
    fallback = url_for("buscar_candidata", candidata_id=cand.fila)

    actor = (
        getattr(current_user, "username", None)
        or getattr(current_user, "id", None)
        or session.get("usuario")
        or "sistema"
    )

    try:
        invariant_change_candidate_state(
            candidata_id=int(cand.fila),
            new_state="lista_para_trabajar",
            actor=str(actor),
            reason="reactivar",
            candidata_obj=cand,
        )
        db.session.commit()
        _audit_log(
            action_type="CANDIDATA_REACTIVAR",
            entity_type="Candidata",
            entity_id=cand.fila,
            summary=f"Candidata reactivada: {cand.nombre_completo or cand.fila}",
            changes={"estado": {"from": "descalificada", "to": "lista_para_trabajar"}},
        )
        log_candidata_action(
            action_type="CANDIDATA_REACTIVATE",
            candidata=cand,
            summary=f"Candidata reactivada: {cand.nombre_completo or cand.fila}",
            changes={"estado": {"from": "descalificada", "to": "lista_para_trabajar"}},
            success=True,
        )
        _emit_domain_outbox_event(
            event_type="CANDIDATA_ESTADO_CAMBIADO",
            aggregate_type="Candidata",
            aggregate_id=int(cand.fila),
            aggregate_version=None,
            payload={"candidata_id": int(cand.fila), "to": "lista_para_trabajar", "reason": "reactivar"},
        )
        db.session.commit()
        flash("Candidata reactivada correctamente.", "success")
    except InvariantConflictError as inv_exc:
        db.session.rollback()
        flash(str(inv_exc) or "Conflicto de estado de candidata.", "warning")
    except Exception:
        db.session.rollback()
        _audit_log(
            action_type="CANDIDATA_REACTIVAR",
            entity_type="Candidata",
            entity_id=cand.fila,
            summary=f"Fallo reactivando candidata {cand.fila}",
            success=False,
            error="No se pudo reactivar la candidata.",
        )
        log_candidata_action(
            action_type="CANDIDATA_REACTIVATE",
            candidata=cand,
            summary=f"Fallo reactivando candidata {cand.fila}",
            success=False,
            error="No se pudo reactivar la candidata.",
        )
        flash("No se pudo reactivar la candidata.", "danger")

    return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)


@admin_bp.route('/candidatas/<int:candidata_id>/marcar_trabajando', methods=['POST'])
@login_required
@staff_required
def marcar_candidata_trabajando(candidata_id: int):
    cand = Candidata.query.filter_by(fila=candidata_id).first_or_404()
    next_url = (request.form.get("next") or "").strip()
    fallback = url_for("buscar_candidata", candidata_id=cand.fila)

    if candidata_esta_descalificada(cand):
        flash("No se puede marcar como trabajando una candidata descalificada.", "danger")
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    actor = (
        getattr(current_user, "username", None)
        or getattr(current_user, "id", None)
        or session.get("usuario")
        or "sistema"
    )

    try:
        invariant_change_candidate_state(
            candidata_id=int(cand.fila),
            new_state="trabajando",
            actor=str(actor),
            reason="manual",
            candidata_obj=cand,
        )
        db.session.commit()
        _audit_log(
            action_type="CANDIDATA_ESTADO_TRABAJANDO",
            entity_type="Candidata",
            entity_id=cand.fila,
            summary=f"Candidata marcada trabajando: {cand.nombre_completo or cand.fila}",
            changes={"estado": {"from": "lista_para_trabajar", "to": "trabajando"}},
        )
        log_candidata_action(
            action_type="CANDIDATA_MARK_TRABAJANDO",
            candidata=cand,
            summary=f"Candidata marcada trabajando: {cand.nombre_completo or cand.fila}",
            changes={"estado": {"from": "lista_para_trabajar", "to": "trabajando"}},
            success=True,
        )
        _emit_domain_outbox_event(
            event_type="CANDIDATA_ESTADO_CAMBIADO",
            aggregate_type="Candidata",
            aggregate_id=int(cand.fila),
            aggregate_version=None,
            payload={"candidata_id": int(cand.fila), "to": "trabajando", "reason": "manual"},
        )
        db.session.commit()
        flash("Candidata marcada como trabajando.", "success")
    except InvariantConflictError as inv_exc:
        db.session.rollback()
        flash(str(inv_exc) or "Conflicto de estado de candidata.", "warning")
    except Exception:
        db.session.rollback()
        log_candidata_action(
            action_type="CANDIDATA_MARK_TRABAJANDO",
            candidata=cand,
            summary=f"Fallo marcando candidata trabajando {cand.fila}",
            success=False,
            error="No se pudo actualizar estado a trabajando.",
        )
        flash("No se pudo actualizar el estado a trabajando.", "danger")

    return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)


@admin_bp.route('/candidatas/<int:candidata_id>/marcar_lista_para_trabajar', methods=['POST'])
@login_required
@staff_required
def marcar_candidata_lista_para_trabajar(candidata_id: int):
    cand = Candidata.query.filter_by(fila=candidata_id).first_or_404()
    next_url = (request.form.get("next") or "").strip()
    fallback = url_for("buscar_candidata", candidata_id=cand.fila)

    if candidata_esta_descalificada(cand):
        flash("No se puede marcar como lista para trabajar una candidata descalificada.", "danger")
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    ready_ok, reasons = candidata_is_ready_to_send(cand)
    blocking = [r for r in (reasons or []) if not str(r).lower().startswith("advertencia:")]
    if not ready_ok or blocking:
        flash(
            "No se puede pasar a lista para trabajar. Falta: "
            + "; ".join(blocking[:4]),
            "warning",
        )
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    estado_previo = (getattr(cand, "estado", None) or "").strip().lower()
    actor = (
        getattr(current_user, "username", None)
        or getattr(current_user, "id", None)
        or session.get("usuario")
        or "sistema"
    )

    try:
        invariant_change_candidate_state(
            candidata_id=int(cand.fila),
            new_state="lista_para_trabajar",
            actor=str(actor),
            reason="manual",
            candidata_obj=cand,
        )
        db.session.commit()
        _audit_log(
            action_type="CANDIDATA_ESTADO_LISTA",
            entity_type="Candidata",
            entity_id=cand.fila,
            summary=f"Candidata marcada lista para trabajar: {cand.nombre_completo or cand.fila}",
            changes={"estado": {"from": estado_previo or None, "to": "lista_para_trabajar"}},
        )
        _log_lista_state_change(
            cand,
            source="manual",
            faltantes=[],
            from_state=estado_previo,
        )
        log_candidata_action(
            action_type="CANDIDATA_ESTADO_LISTA",
            candidata=cand,
            summary=f"Estado candidata actualizado a lista para trabajar: {cand.nombre_completo or cand.fila}",
            metadata={"source": "manual"},
            changes={"estado": {"from": estado_previo or None, "to": "lista_para_trabajar"}},
            success=True,
        )
        _emit_domain_outbox_event(
            event_type="CANDIDATA_ESTADO_CAMBIADO",
            aggregate_type="Candidata",
            aggregate_id=int(cand.fila),
            aggregate_version=None,
            payload={"candidata_id": int(cand.fila), "to": "lista_para_trabajar", "reason": "manual"},
        )
        db.session.commit()
        flash("Candidata marcada como lista para trabajar.", "success")
    except InvariantConflictError as inv_exc:
        db.session.rollback()
        flash(str(inv_exc) or "Conflicto de estado de candidata.", "warning")
    except Exception:
        db.session.rollback()
        log_candidata_action(
            action_type="CANDIDATA_MARK_LISTA",
            candidata=cand,
            summary=f"Fallo marcando candidata lista para trabajar {cand.fila}",
            success=False,
            error="No se pudo actualizar estado a lista para trabajar.",
        )
        flash("No se pudo actualizar el estado a lista para trabajar.", "danger")

    return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)


# ============================================================
#                               RESUMEN KPI
# ============================================================
_SOLICITUDES_RESUMEN_CACHE_KEY = "admin:solicitudes:resumen:v2"
_SOLICITUDES_RESUMEN_CACHE_TTL_SEC_DEFAULT = 45


def _solicitudes_resumen_cache_ttl_sec() -> int:
    try:
        raw = int((os.getenv("ADMIN_SOLICITUDES_RESUMEN_CACHE_TTL_SEC") or str(_SOLICITUDES_RESUMEN_CACHE_TTL_SEC_DEFAULT)).strip())
    except Exception:
        raw = _SOLICITUDES_RESUMEN_CACHE_TTL_SEC_DEFAULT
    return max(15, min(raw, 300))


def _solicitudes_resumen_cache_get() -> dict | None:
    if not _cache_ok():
        return None
    try:
        payload = cache.get(_SOLICITUDES_RESUMEN_CACHE_KEY)
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return None


def _solicitudes_resumen_cache_set(payload: dict) -> None:
    if not _cache_ok():
        return
    try:
        cache.set(_SOLICITUDES_RESUMEN_CACHE_KEY, payload, timeout=_solicitudes_resumen_cache_ttl_sec())
    except Exception:
        pass


def _db_dialect_name() -> str:
    try:
        bind = db.session.get_bind()
        if bind is not None and getattr(bind, "dialect", None) is not None:
            return str(bind.dialect.name or "").strip().lower()
    except Exception:
        pass
    return ""


def _trend_bucket_expr(column, *, grain: str, dialect: str):
    if dialect == "sqlite":
        # SQLite no trae date_trunc: agrupamos por llave de texto estable y luego normalizamos a datetime.
        if grain == "week":
            return func.strftime("%Y-%W-1", column)
        return func.strftime("%Y-%m-01", column)
    return func.date_trunc(grain, column)


def _avg_seconds_expr(end_column, start_column, *, dialect: str):
    if dialect == "sqlite":
        # julianday() es portable en SQLite y devuelve diferencia en días.
        return (func.julianday(end_column) - func.julianday(start_column)) * 86400.0
    return func.extract("epoch", end_column - start_column)


def _normalize_bucket_value(value, *, grain: str, dialect: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if dialect == "sqlite":
        text = str(value or "").strip()
        try:
            if grain == "week":
                # `%W` usa semana iniciando lunes; fijamos el lunes de esa semana.
                return datetime.strptime(text, "%Y-%W-%w")
            return datetime.strptime(text, "%Y-%m-%d")
        except Exception:
            return None
    return None


@admin_bp.route('/solicitudes/resumen')
@login_required
@admin_required
def resumen_solicitudes():
    """
    KPIs con fechas coherentes en UTC y casteo numérico robusto.
    Usa cache corto para reducir costo y soporta agrupación temporal en SQLite.
    """
    cached_payload = _solicitudes_resumen_cache_get()
    if cached_payload:
        return render_template('admin/solicitudes_resumen.html', **cached_payload)

    dialect = _db_dialect_name()

    # Bordes UTC para hoy/semana/mes
    hoy = rd_today()
    week_start = hoy - timedelta(days=hoy.weekday())
    month_start = date(hoy.year, hoy.month, 1)

    # — Totales y estados —
    state_rows = (
        db.session.query(
            Solicitud.estado,
            func.count(Solicitud.id).label("cnt"),
        )
        .group_by(Solicitud.estado)
        .all()
    )
    state_counts = {str(st or "").strip().lower(): int(cnt or 0) for st, cnt in state_rows}
    total_sol = int(sum(int(cnt or 0) for _, cnt in state_rows))
    proc_count = int(state_counts.get('proceso', 0))
    act_count = int(state_counts.get('activa', 0))
    pag_count = int(state_counts.get('pagada', 0))
    cancel_count = int(state_counts.get('cancelada', 0))
    repl_count = int(state_counts.get('reemplazo', 0))

    # — Tasas —
    conversion_rate  = (pag_count    / total_sol * 100) if total_sol else 0
    replacement_rate = (repl_count   / total_sol * 100) if total_sol else 0
    abandon_rate     = (cancel_count / total_sol * 100) if total_sol else 0

    # — Promedios de tiempo (en días) —
    # Promedio publicación (last_copiado_at - fecha_solicitud)
    avg_pub_secs = (db.session.query(
        func.avg(_avg_seconds_expr(Solicitud.last_copiado_at, Solicitud.fecha_solicitud, dialect=dialect))
    ).filter(Solicitud.last_copiado_at.isnot(None)).scalar()) or 0
    avg_pub_days = avg_pub_secs / 86400

    # Promedio hasta pago (fecha_ultima_modificacion - fecha_solicitud) solo pagadas
    avg_pay_secs = (db.session.query(
        func.avg(_avg_seconds_expr(Solicitud.fecha_ultima_modificacion, Solicitud.fecha_solicitud, dialect=dialect))
    ).filter(Solicitud.estado == 'pagada').scalar()) or 0
    avg_pay_days = avg_pay_secs / 86400

    # Promedio hasta cancelación
    avg_cancel_secs = (db.session.query(
        func.avg(_avg_seconds_expr(Solicitud.fecha_cancelacion, Solicitud.fecha_solicitud, dialect=dialect))
    ).filter(Solicitud.fecha_cancelacion.isnot(None)).scalar()) or 0
    avg_cancel_days = avg_cancel_secs / 86400

    # — Top 5 ciudades (ignora NULL/'' para calidad de dato) —
    top_cities = (
        db.session.query(
            Solicitud.ciudad_sector,
            func.count(Solicitud.id).label('cnt')
        )
        .filter(Solicitud.ciudad_sector.isnot(None))
        .filter(func.length(func.trim(Solicitud.ciudad_sector)) > 0)
        .group_by(Solicitud.ciudad_sector)
        .order_by(desc('cnt'))
        .limit(5)
        .all()
    )

    # — Distribución por modalidad de trabajo —
    modality_dist = (
        db.session.query(
            Solicitud.modalidad_trabajo,
            func.count(Solicitud.id)
        )
        .group_by(Solicitud.modalidad_trabajo)
        .all()
    )

    # — Backlog: en proceso >7 días —
    backlog_threshold_days = 7
    backlog_alert = (
        Solicitud.query
        .filter_by(estado='proceso')
        .filter(Solicitud.fecha_solicitud < _now_utc() - timedelta(days=backlog_threshold_days))
        .count()
    )

    # — Tendencias (semanal/mensual) —
    trend_new_weekly  = (
        db.session.query(
            _trend_bucket_expr(Solicitud.fecha_solicitud, grain='week', dialect=dialect).label('period'),
            func.count(Solicitud.id)
        )
        .group_by('period').order_by('period')
        .all()
    )
    trend_new_monthly = (
        db.session.query(
            _trend_bucket_expr(Solicitud.fecha_solicitud, grain='month', dialect=dialect).label('period'),
            func.count(Solicitud.id)
        )
        .group_by('period').order_by('period')
        .all()
    )

    trend_paid_weekly  = (
        db.session.query(
            _trend_bucket_expr(Solicitud.fecha_ultima_modificacion, grain='week', dialect=dialect).label('period'),
            func.count(Solicitud.id)
        )
        .filter(Solicitud.estado == 'pagada')
        .group_by('period').order_by('period')
        .all()
    )
    trend_paid_monthly = (
        db.session.query(
            _trend_bucket_expr(Solicitud.fecha_ultima_modificacion, grain='month', dialect=dialect).label('period'),
            func.count(Solicitud.id)
        )
        .filter(Solicitud.estado == 'pagada')
        .group_by('period').order_by('period')
        .all()
    )

    trend_cancel_weekly  = (
        db.session.query(
            _trend_bucket_expr(Solicitud.fecha_cancelacion, grain='week', dialect=dialect).label('period'),
            func.count(Solicitud.id)
        )
        .filter(Solicitud.estado == 'cancelada')
        .group_by('period').order_by('period')
        .all()
    )
    trend_cancel_monthly = (
        db.session.query(
            _trend_bucket_expr(Solicitud.fecha_cancelacion, grain='month', dialect=dialect).label('period'),
            func.count(Solicitud.id)
        )
        .filter(Solicitud.estado == 'cancelada')
        .group_by('period').order_by('period')
        .all()
    )

    # Bordes para filtros por periodo (UTC)
    start_today_utc, _ = _today_utc_bounds()
    start_week_utc = datetime(week_start.year, week_start.month, week_start.day)
    start_month_utc = datetime(month_start.year, month_start.month, month_start.day)

    # — Órdenes realizadas (fecha_solicitud) —
    orders_today = Solicitud.query.filter(
        Solicitud.fecha_solicitud >= start_today_utc,
        Solicitud.fecha_solicitud < start_today_utc + timedelta(days=1)
    ).count()
    orders_week  = Solicitud.query.filter(Solicitud.fecha_solicitud >= start_week_utc).count()
    orders_month = Solicitud.query.filter(Solicitud.fecha_solicitud >= start_month_utc).count()

    # — Publicadas (copias) —
    daily_copy   = Solicitud.query.filter(
        Solicitud.last_copiado_at >= start_today_utc,
        Solicitud.last_copiado_at < start_today_utc + timedelta(days=1)
    ).count()
    weekly_copy  = Solicitud.query.filter(Solicitud.last_copiado_at >= start_week_utc).count()
    monthly_copy = Solicitud.query.filter(Solicitud.last_copiado_at >= start_month_utc).count()

    # — Pagos por periodo —
    daily_paid   = (Solicitud.query.filter_by(estado='pagada')
                    .filter(
                        Solicitud.fecha_ultima_modificacion >= start_today_utc,
                        Solicitud.fecha_ultima_modificacion < start_today_utc + timedelta(days=1)
                    ).count())
    weekly_paid  = (Solicitud.query.filter_by(estado='pagada')
                    .filter(Solicitud.fecha_ultima_modificacion >= start_week_utc).count())
    monthly_paid = (Solicitud.query.filter_by(estado='pagada')
                    .filter(Solicitud.fecha_ultima_modificacion >= start_month_utc).count())

    # — Cancelaciones por periodo —
    daily_cancel   = (Solicitud.query.filter_by(estado='cancelada')
                      .filter(
                          Solicitud.fecha_cancelacion >= start_today_utc,
                          Solicitud.fecha_cancelacion < start_today_utc + timedelta(days=1)
                      ).count())
    weekly_cancel  = (Solicitud.query.filter_by(estado='cancelada')
                      .filter(Solicitud.fecha_cancelacion >= start_week_utc).count())
    monthly_cancel = (Solicitud.query.filter_by(estado='cancelada')
                      .filter(Solicitud.fecha_cancelacion >= start_month_utc).count())

    # — Reemplazos por periodo (usa fecha_ultima_modificacion como proxy de cambio) —
    weekly_repl  = (Solicitud.query.filter_by(estado='reemplazo')
                    .filter(Solicitud.fecha_ultima_modificacion >= start_week_utc).count())
    monthly_repl = (Solicitud.query.filter_by(estado='reemplazo')
                    .filter(Solicitud.fecha_ultima_modificacion >= start_month_utc).count())

    # — Estadísticas mensuales de ingreso (pagadas) —
    # NOTA: con el monto guardado en formato canónico "1234.56",
    # el casteo directo a NUMERIC es seguro.
    stats_mensual = (
        db.session.query(
            _trend_bucket_expr(Solicitud.fecha_solicitud, grain='month', dialect=dialect).label('mes'),
            func.count(Solicitud.id).label('cantidad'),
            func.sum(cast(Solicitud.monto_pagado, Numeric(12, 2))).label('total_pagado')
        )
        .filter(Solicitud.estado == 'pagada')
        .group_by('mes').order_by('mes')
        .all()
    )
    trend_new_weekly_norm = []
    for period, cnt in trend_new_weekly:
        period_norm = _normalize_bucket_value(period, grain='week', dialect=dialect)
        if period_norm is not None:
            trend_new_weekly_norm.append((period_norm, int(cnt or 0)))

    trend_new_monthly_norm = []
    for period, cnt in trend_new_monthly:
        period_norm = _normalize_bucket_value(period, grain='month', dialect=dialect)
        if period_norm is not None:
            trend_new_monthly_norm.append((period_norm, int(cnt or 0)))

    trend_paid_weekly_norm = []
    for period, cnt in trend_paid_weekly:
        period_norm = _normalize_bucket_value(period, grain='week', dialect=dialect)
        if period_norm is not None:
            trend_paid_weekly_norm.append((period_norm, int(cnt or 0)))

    trend_paid_monthly_norm = []
    for period, cnt in trend_paid_monthly:
        period_norm = _normalize_bucket_value(period, grain='month', dialect=dialect)
        if period_norm is not None:
            trend_paid_monthly_norm.append((period_norm, int(cnt or 0)))

    trend_cancel_weekly_norm = []
    for period, cnt in trend_cancel_weekly:
        period_norm = _normalize_bucket_value(period, grain='week', dialect=dialect)
        if period_norm is not None:
            trend_cancel_weekly_norm.append((period_norm, int(cnt or 0)))

    trend_cancel_monthly_norm = []
    for period, cnt in trend_cancel_monthly:
        period_norm = _normalize_bucket_value(period, grain='month', dialect=dialect)
        if period_norm is not None:
            trend_cancel_monthly_norm.append((period_norm, int(cnt or 0)))

    stats_mensual_norm = []
    for mes, cantidad, total in stats_mensual:
        mes_norm = _normalize_bucket_value(mes, grain='month', dialect=dialect)
        if mes_norm is None:
            continue
        stats_mensual_norm.append((mes_norm, int(cantidad or 0), (total or Decimal("0.00"))))

    payload = {
        # Totales y estados
        "total_sol": total_sol,
        "proc_count": proc_count,
        "act_count": act_count,
        "pag_count": pag_count,
        "cancel_count": cancel_count,
        "repl_count": repl_count,
        # Tasas y promedios
        "conversion_rate": conversion_rate,
        "replacement_rate": replacement_rate,
        "abandon_rate": abandon_rate,
        "avg_pub_days": avg_pub_days,
        "avg_pay_days": avg_pay_days,
        "avg_cancel_days": avg_cancel_days,
        # Top y distribución
        "top_cities": [(city, int(cnt or 0)) for city, cnt in top_cities],
        "modality_dist": [(mod, int(cnt or 0)) for mod, cnt in modality_dist],
        "backlog_threshold_days": backlog_threshold_days,
        "backlog_alert": backlog_alert,
        # Tendencias
        "trend_new_weekly": trend_new_weekly_norm,
        "trend_new_monthly": trend_new_monthly_norm,
        "trend_paid_weekly": trend_paid_weekly_norm,
        "trend_paid_monthly": trend_paid_monthly_norm,
        "trend_cancel_weekly": trend_cancel_weekly_norm,
        "trend_cancel_monthly": trend_cancel_monthly_norm,
        # Órdenes realizadas
        "orders_today": orders_today,
        "orders_week": orders_week,
        "orders_month": orders_month,
        # Publicadas (copias)
        "daily_copy": daily_copy,
        "weekly_copy": weekly_copy,
        "monthly_copy": monthly_copy,
        # Pagos
        "daily_paid": daily_paid,
        "weekly_paid": weekly_paid,
        "monthly_paid": monthly_paid,
        # Cancelaciones
        "daily_cancel": daily_cancel,
        "weekly_cancel": weekly_cancel,
        "monthly_cancel": monthly_cancel,
        # Reemplazos
        "weekly_repl": weekly_repl,
        "monthly_repl": monthly_repl,
        # Ingreso mensual
        "stats_mensual": stats_mensual_norm,
    }
    _solicitudes_resumen_cache_set(payload)
    return render_template('admin/solicitudes_resumen.html', **payload)



# =============================================================================
#                     COPIAR SOLICITUDES (LISTA + POST) — ROBUSTO
# =============================================================================
from datetime import datetime, timedelta, timezone
from sqlalchemy import or_, desc, cast
from sqlalchemy.sql import func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload
import json
import re
from decimal import Decimal, InvalidOperation

# ──────────────────────────────────────────────────────────────────────────────
# AREAS_COMUNES_CHOICES centralizado (con fallback)
# ──────────────────────────────────────────────────────────────────────────────
try:
    from .routes import AREAS_COMUNES_CHOICES  # type: ignore
except Exception:
    AREAS_COMUNES_CHOICES = [
        ('sala', 'Sala'), ('comedor', 'Comedor'), ('cocina', 'Cocina'),
        ('salon_juegos', 'Salón de juegos'), ('terraza', 'Terraza'),
        ('jardin', 'Jardín'), ('estudio', 'Estudio'), ('patio', 'Patio'),
        ('piscina', 'Piscina'), ('marquesina', 'Marquesina'),
        ('todas_anteriores', 'Todas las anteriores'), ('otro', 'Otro'),
    ]
AREAS_MAP = {k: v for k, v in AREAS_COMUNES_CHOICES}

# --------------------------- HELPERS SEGUROS ---------------------------------

def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (int, float, bool)):
        return str(v)
    try:
        return str(v).strip()
    except Exception:
        return str(v)

def _to_naive_utc(dt):
    if dt is None:
        return None
    try:
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return dt

def _utc_day_bounds(dt: datetime | None = None):
    base = (dt or utc_now_naive()).date()
    start = datetime(base.year, base.month, base.day)
    end = start + timedelta(days=1)
    return start, end

def _safe_join(parts, sep="\n"):
    out = []
    for p in parts or []:
        s = _s(p)
        if s:
            out.append(s)
    return sep.join(out)

def _first_nonempty_attr(obj, names: list[str], default=""):
    for n in names:
        if hasattr(obj, n):
            v = getattr(obj, n)
            s = _s(v)
            if s:
                return s
    return default

def _as_list(v):
    if v is None:
        return []
    if isinstance(v, (list, tuple, set)):
        return [_s(x) for x in v if _s(x)]
    if isinstance(v, dict):
        return [_s(x) for x in v.values() if _s(x)]
    if isinstance(v, str):
        txt = v.strip()
        if not txt:
            return []
        try:
            parsed = json.loads(txt)
            return _as_list(parsed)
        except Exception:
            if "," in txt:
                return [_s(x) for x in txt.split(",") if _s(x)]
            if ";" in txt:
                return [_s(x) for x in txt.split(";") if _s(x)]
            return [txt]
    return [_s(v)]

def _unique_keep_order(seq):
    """Devuelve únicos preservando el orden de aparición."""
    seen = set()
    out = []
    for x in seq or []:
        sx = _s(x)
        if not sx:
            continue
        if sx in seen:
            continue
        seen.add(sx)
        out.append(sx)
    return out

def _fmt_banos(val) -> str:
    if val is None:
        return ""
    s = _s(val).lower().replace("½", ".5").replace(" 1/2", ".5").replace("1/2", ".5")
    try:
        x = float(s) if any(ch.isdigit() for ch in s) else None
        if x is None:
            return _s(val)
        if abs(x - int(x)) < 1e-9:
            return str(int(x))
        return str(x)
    except Exception:
        return _s(val)

def _norm_area(a: str) -> str:
    k = _s(a).lower()
    if k in {"otro", "otro...", "otro…"}:
        return ""
    if k in AREAS_MAP:
        return AREAS_MAP[k]
    alias = {
        "balcon": "Balcón", "balcón": "Balcón",
        "lavado": "Lavado", "terraza": "Terraza",
        "jardin": "Jardín", "salon_juegos": "Salón de juegos",
    }
    if k in alias:
        return alias[k]
    return a.strip().title()

def _fmt_codigo_humano(codigo: str) -> str:
    c = (codigo or "").strip()
    if not c:
        return ""
    if "-" in c:
        left, right = c.split("-", 1)
    else:
        left, right = c, ""
    try:
        digits = re.findall(r"(\d+)", left)
        if digits:
            n_str = digits[-1]
            n = int(n_str)
            left_fmt = left[: left.rfind(n_str)] + f"{n:,}"
        else:
            left_fmt = left
    except Exception:
        left_fmt = left
    return f"{left_fmt}-{right}" if right else left_fmt

def _format_money_usd(raw) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    s = s.replace("RD$", "").replace("$", "").replace(" ", "")
    us_pattern = r"^\d{1,3}(,\d{3})+(\.\d+)?$"
    eu_pattern = r"^\d{1,3}(\.\d{3})+(,\d+)?$"
    plain_digits = r"^\d+$"
    try:
        if re.match(us_pattern, s):
            num = s.replace(",", "")
            val = Decimal(num)
        elif re.match(eu_pattern, s):
            num = s.replace(".", "").replace(",", ".")
            val = Decimal(num)
        elif re.match(plain_digits, s):
            val = Decimal(s)
        else:
            if "," in s and "." not in s and re.match(r"^\d{1,3}(,\d{3})+$", s):
                val = Decimal(s.replace(",", ""))
            else:
                if "," in s and "." not in s and s.count(",") == 1:
                    val = Decimal(s.replace(",", "."))
                else:
                    val = Decimal(s.replace(",", ""))
        if val == val.to_integral():
            return f"${int(val):,}"
        return f"${val:,.2f}"
    except Exception:
        return f"${s}"

# ------------------------------ RUTAS ----------------------------------------

# RUTAS ADMIN – copiar solicitudes (con nota_cliente al final si existe)

# Helper específico para formatear el código de la solicitud
# ------------------------------ RUTAS ----------------------------------------

# RUTAS ADMIN – copiar solicitudes (con nota_cliente al final si existe)

# Helper específico para formatear el código de la solicitud
def _fmt_codigo_solicitud(codigo: str) -> str:
    """
    Formatea solo el tramo numérico final del código si:
      - NO tiene ya comas ni puntos (es decir, no fue formateado antes).
    Ejemplos:
      'SOL-1000'  -> 'SOL-1,000'
      '1000'      -> '1,000'
      'SOL-1,333' -> 'SOL-1,333'  (no se toca)
      '2,005'     -> '2,005'      (no se toca, evita el bug 2,5)
    """
    c = (codigo or "").strip()
    if not c:
        return ""

    # Si ya tiene coma o punto, asumimos que el usuario ya le dio el formato que quiere
    if "," in c or "." in c:
        return c

    # Buscar el último bloque de dígitos en el string
    m = re.search(r"(\d+)(?!.*\d)", c)
    if not m:
        # No hay números, devuelve tal cual
        return c

    n_str = m.group(1)
    try:
        n = int(n_str)
    except ValueError:
        return c

    # Formatear con separador de miles
    formatted = f"{n:,}"  # 1000 -> '1,000'
    # Reconstruir el código con el tramo numérico formateado
    return c[:m.start(1)] + formatted + c[m.end(1):]


@admin_bp.route('/solicitudes/copiar')
@login_required
@staff_required
def copiar_solicitudes():
    """
    Lista solicitudes copiables y arma el texto final:
    - Modalidad/Hogar sin prefijos fijos.
    - Mascotas solo si hay.
    - Líneas en blanco entre bloques.
    - Funciones en el MISMO ORDEN seleccionado (y 'otro' al final si aplica).
    - Agrega detalles extras según el tipo (niñera / enfermera / chofer).
    """
    q = _s(request.args.get('q'))

    # Paginación robusta
    try:
        page = int(request.args.get('page', 1) or 1)
    except Exception:
        page = 1
    page = max(1, page)

    try:
        per_page = int(request.args.get('per_page', 50) or 50)
    except Exception:
        per_page = 50
    per_page = max(10, min(per_page, 200))

    start_utc, _ = _utc_day_bounds()

    base_q = (
        Solicitud.query
        .options(
            load_only(
                Solicitud.id,
                Solicitud.estado,
                Solicitud.candidata_id,
                Solicitud.codigo_solicitud,
                Solicitud.fecha_solicitud,
                Solicitud.last_copiado_at,
                Solicitud.ciudad_sector,
                Solicitud.modalidad_trabajo,
                Solicitud.rutas_cercanas,
                Solicitud.funciones,
                Solicitud.funciones_otro,
                Solicitud.tipo_lugar,
                Solicitud.habitaciones,
                Solicitud.banos,
                Solicitud.dos_pisos,
                Solicitud.areas_comunes,
                Solicitud.area_otro,
                Solicitud.adultos,
                Solicitud.ninos,
                Solicitud.edades_ninos,
                Solicitud.mascota,
                Solicitud.edad_requerida,
                Solicitud.experiencia,
                Solicitud.horario,
                Solicitud.sueldo,
                Solicitud.pasaje_aporte,
                Solicitud.nota_cliente,
                Solicitud.detalles_servicio,
                Solicitud.tipo_servicio,
            ),
            selectinload(Solicitud.reemplazos).load_only(
                Reemplazo.id,
                Reemplazo.oportunidad_nueva,
                Reemplazo.candidata_new_id,
                Reemplazo.fecha_inicio_reemplazo,
                Reemplazo.fecha_fin_reemplazo,
            ).selectinload(Reemplazo.candidata_new).load_only(
                Candidata.fila,
                Candidata.nombre_completo,
            ),
        )
        .filter(Solicitud.estado.in_(('activa', 'reemplazo')))
        .filter(or_(
            Solicitud.last_copiado_at.is_(None),
            Solicitud.last_copiado_at < start_utc
        ))
    )

    if q:
        like = f"%{q}%"
        filtros = []
        for col in (
            Solicitud.ciudad_sector,
            Solicitud.codigo_solicitud,
            Solicitud.rutas_cercanas,
            Solicitud.modalidad_trabajo
        ):
            filtros.append(col.ilike(like))
        filtros.append(cast(Solicitud.funciones, db.Text).ilike(like))
        base_q = base_q.filter(or_(*filtros))

    query_ordenada = base_q.order_by(
        desc(Solicitud.estado == 'reemplazo'),
        Solicitud.fecha_solicitud.desc()
    )

    total = query_ordenada.count()
    raw_sols = (
        query_ordenada
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    # Form temporal para leer choices y labels
    form = AdminSolicitudForm()

    FUNCIONES_CHOICES      = list(getattr(form, 'funciones',      None).choices or [])
    FUNCIONES_LABELS       = {k: v for k, v in FUNCIONES_CHOICES}

    NINERA_TAREAS_CHOICES  = list(getattr(form, 'ninera_tareas',  None).choices or [])
    NINERA_TAREAS_LABELS   = {k: v for k, v in NINERA_TAREAS_CHOICES}

    ENF_TAREAS_CHOICES     = list(getattr(form, 'enf_tareas',     None).choices or [])
    ENF_TAREAS_LABELS      = {k: v for k, v in ENF_TAREAS_CHOICES}

    ENF_MOV_CHOICES        = list(getattr(form, 'enf_movilidad',  None).choices or [])
    ENF_MOV_LABELS         = {k: v for k, v in ENF_MOV_CHOICES}

    solicitudes = []
    for s in raw_sols:
        if s.estado == 'reemplazo':
            reems = list(s.reemplazos or [])
        else:
            reems = [r for r in (s.reemplazos or []) if bool(getattr(r, 'oportunidad_nueva', False))]

        # ====================== FUNCIONES (ORDEN CORRECTO) ======================
        raw_codes = _unique_keep_order(_as_list(getattr(s, 'funciones', None)))
        raw_codes = [c for c in raw_codes if c != 'otro']

        funcs = []
        for code in raw_codes:
            label = FUNCIONES_LABELS.get(code)
            if label:
                funcs.append(label)

        custom_f = _s(getattr(s, 'funciones_otro', None))
        if custom_f:
            funcs.append(custom_f)

        # ====================== ADULTOS / NIÑOS ======================
        adultos_val = _s(getattr(s, 'adultos', None))
        ninos_line = ""
        ninos_raw = getattr(s, 'ninos', None)
        if ninos_raw not in (None, "", 0, "0"):
            ninos_line = f"Niños: {_s(ninos_raw)}"
            ed = _s(getattr(s, 'edades_ninos', None))
            if ed:
                ninos_line += f" ({ed})"

        # ====================== MODALIDAD ======================
        modalidad = _first_nonempty_attr(s, ['modalidad_trabajo', 'modalidad', 'tipo_modalidad'], '')
        modalidad_line = canonicalize_modalidad_trabajo(modalidad) if modalidad else ""

        # ====================== HOGAR ======================
        hogar_partes_detalle = []
        habitaciones = getattr(s, 'habitaciones', None)
        if habitaciones not in (None, "", 0, "0"):
            hogar_partes_detalle.append(f"{_s(habitaciones)} habitaciones")
        banos_txt = _fmt_banos(getattr(s, 'banos', None))
        if banos_txt:
            hogar_partes_detalle.append(f"{banos_txt} baños")
        if bool(getattr(s, 'dos_pisos', False)):
            hogar_partes_detalle.append("2 pisos")

        areas = []
        for a in _as_list(getattr(s, 'areas_comunes', None)):
            area_norm = _norm_area(a)
            if area_norm:
                areas.append(area_norm)
        area_otro = _s(getattr(s, 'area_otro', None))
        if area_otro:
            area_norm = _norm_area(area_otro)
            if area_norm:
                areas.append(area_norm)
        if areas:
            hogar_partes_detalle.append(", ".join(areas))

        tipo_lugar = _s(getattr(s, 'tipo_lugar', None))
        # Solo imprimimos algo del hogar si hay detalles reales (habitaciones, baños o áreas).
        if hogar_partes_detalle:
            if tipo_lugar:
                hogar_descr = f"{tipo_lugar} - {', '.join(hogar_partes_detalle)}"
            else:
                hogar_descr = ", ".join(hogar_partes_detalle)
        else:
            hogar_descr = ""

        # ====================== MASCOTAS ======================
        mascota_val = _s(getattr(s, 'mascota', None))
        mascota_line = f"Mascotas: {mascota_val}" if mascota_val else ""

        # ====================== CAMPOS BASE ======================
        codigo         = _s(getattr(s, 'codigo_solicitud', None))
        ciudad_sector  = _s(getattr(s, 'ciudad_sector', None))
        rutas_cercanas = _s(getattr(s, 'rutas_cercanas', None))

        # Edad requerida
        edad_req_val = getattr(s, 'edad_requerida', None)
        if isinstance(edad_req_val, (list, tuple, set, dict, str)):
            edad_req = ", ".join([_s(x) for x in _as_list(edad_req_val)])
        else:
            edad_req = _s(edad_req_val)

        experiencia    = _s(getattr(s, 'experiencia', None))
        experiencia_it = f"*{experiencia}*" if experiencia else ""
        horario        = _s(getattr(s, 'horario', None))

        # Sueldo
        sueldo_final  = _format_money_usd(getattr(s, 'sueldo', None))
        pasaje_texto = _pasaje_operativo_phrase_from_solicitud(s)

        # Nota del cliente (al final, sin prefijo)
        nota_cli = _s(getattr(s, 'nota_cliente', None))

        # ====================== DETALLES SERVICIO (NIÑERA / ENFERMERA / CHOFER) ======================
        detalles = getattr(s, 'detalles_servicio', None) or {}
        ts_det   = detalles.get("tipo") or _s(getattr(s, 'tipo_servicio', None))

        ninera_block = ""
        enf_block    = ""
        chofer_block = ""

        # ---- NIÑERA ----
        if ts_det == 'NINERA':
            cant_ninos = detalles.get("cantidad_ninos") or detalles.get("cant_ninos")
            edades_n   = detalles.get("edades_ninos")   or detalles.get("edades")
            tareas_cd  = detalles.get("tareas") or []
            cond_esp   = detalles.get("condicion_especial") or detalles.get("condicion")

            lineas_nin = []

            if cant_ninos or edades_n:
                base = "Niños a cuidar: "
                if cant_ninos:
                    base += str(cant_ninos)
                if edades_n:
                    base += f" ({edades_n})"
                lineas_nin.append(base)

            if tareas_cd:
                etiquetas = []
                for code in _as_list(tareas_cd):
                    lbl = NINERA_TAREAS_LABELS.get(code)
                    if lbl:
                        etiquetas.append(lbl)
                    else:
                        etiquetas.append(str(code))
                lineas_nin.append("Tareas con los niños: " + ", ".join(etiquetas))

            if cond_esp:
                lineas_nin.append(f"Condición especial: {cond_esp}")

            ninera_block = "\n".join(lineas_nin) if lineas_nin else ""

        # ---- ENFERMERA / CUIDADORA ----
        elif ts_det == 'ENFERMERA':
            a_quien   = detalles.get("a_quien_cuida") or detalles.get("a_quien")
            cond_prin = detalles.get("condicion_principal") or detalles.get("condicion")
            movilidad = detalles.get("movilidad") or ""
            tareas_cd = detalles.get("tareas") or []

            lineas_enf = []
            if a_quien:
                lineas_enf.append(f"A quién cuida: {a_quien}")

            if movilidad:
                mov_lbl = ENF_MOV_LABELS.get(movilidad, movilidad)
                if mov_lbl:
                    lineas_enf.append(f"Movilidad: {mov_lbl}")

            if cond_prin:
                lineas_enf.append(f"Condición principal: {cond_prin}")

            if tareas_cd:
                etiquetas = []
                for code in _as_list(tareas_cd):
                    lbl = ENF_TAREAS_LABELS.get(code)
                    if lbl:
                        etiquetas.append(lbl)
                    else:
                        etiquetas.append(str(code))
                lineas_enf.append("Tareas de cuidado: " + ", ".join(etiquetas))

            enf_block = "\n".join(lineas_enf) if lineas_enf else ""

        # ---- CHOFER ----
        elif ts_det == 'CHOFER':
            vehiculo    = detalles.get("vehiculo")
            tipo_veh    = detalles.get("tipo_vehiculo")
            tipo_otro   = detalles.get("tipo_vehiculo_otro")
            rutas       = detalles.get("rutas")
            viajes_larg = detalles.get("viajes_largos")
            lic_det     = detalles.get("licencia_requisitos") or detalles.get("licencia_detalle")

            lineas_ch = []
            if vehiculo:
                if vehiculo == 'cliente':
                    lineas_ch.append("Vehículo: del cliente")
                elif vehiculo == 'empleado':
                    lineas_ch.append("Vehículo: propio del chofer")
                else:
                    lineas_ch.append(f"Vehículo: {vehiculo}")

            if tipo_veh or tipo_otro:
                tv = tipo_otro or tipo_veh
                lineas_ch.append(f"Tipo de vehículo: {tv}")

            if rutas:
                lineas_ch.append(f"Rutas habituales: {rutas}")

            if viajes_larg is not None:
                lineas_ch.append("Viajes largos / fuera de la ciudad: Sí" if viajes_larg else "Viajes largos / fuera de la ciudad: No")

            if lic_det:
                lineas_ch.append(f"Licencia / experiencia: {lic_det}")

            chofer_block = "\n".join(lineas_ch) if lineas_ch else ""

        # ===== Texto final =====
        cod_fmt = _fmt_codigo_solicitud(codigo) if codigo else ""
        header_block = "\n".join([
            f"Disponible ( {cod_fmt} )" if cod_fmt else "Disponible",
            f"📍 {ciudad_sector}" if ciudad_sector else "📍",
            f"Ruta más cercana: {rutas_cercanas}" if rutas_cercanas else "Ruta más cercana: ",
        ])

        info_lines = []
        if modalidad_line:
            info_lines.append(modalidad_line)
        if edad_req:
            info_lines.append("")
            info_lines.append(f"Edad: {edad_req}")
        info_lines.extend(["", "Dominicana", "Que sepa leer y escribir"])
        if experiencia_it:
            info_lines.append(f"Experiencia en: {experiencia_it}")
        if horario:
            info_lines.append(f"Horario: {horario}")
        info_block = "\n".join([x for x in info_lines])

        funciones_block = f"Funciones: {', '.join(funcs)}" if funcs else ""
        hogar_line      = hogar_descr

        familia_parts = []
        if adultos_val:
            familia_parts.append(f"Adultos: {adultos_val}")
        if ninos_line:
            familia_parts.append(ninos_line)
        if mascota_line:
            familia_parts.append(mascota_line)
        familia_block = "\n".join(familia_parts) if familia_parts else ""

        sueldo_block = ""
        if sueldo_final:
            sueldo_block = (
                f"Sueldo: {sueldo_final} mensual"
                + f", {pasaje_texto}"
            )

        # Armamos el orden final SIN cambiar el modelo original,
        # solo metiendo los bloques de detalles donde corresponde.
        parts = [
            header_block,
            "",
            info_block.strip() if info_block.strip() else None,
            "",
            funciones_block if funciones_block else None,
            "",
            hogar_line if hogar_line else None,
            "",
            ninera_block if ninera_block else None,
            enf_block if enf_block else None,
            chofer_block if chofer_block else None,
            "" if (ninera_block or enf_block or chofer_block) else None,
            familia_block if familia_block else None,
            "",
            sueldo_block if sueldo_block else None,
            "",
            (nota_cli if nota_cli else None),
        ]

        cleaned = []
        for p in parts:
            if p is None:
                continue
            if p == "" and (not cleaned or cleaned[-1] == ""):
                continue
            cleaned.append(p)
        order_text = "\n".join(cleaned).rstrip()

        solicitudes.append({
            'id': s.id,
            'codigo_solicitud': codigo,
            'ciudad_sector': ciudad_sector,
            'estado': s.estado,
            'candidata_id': getattr(s, 'candidata_id', None),
            'direccion': getattr(s, 'direccion', None),
            'reemplazos': reems,
            'funcs': funcs,
            'modalidad': modalidad,
            'order_text': order_text
        })

    has_more = (page * per_page) < total
    is_admin_role = _current_staff_role() in ('admin', 'owner')

    partial_ctx = dict(
        solicitudes=solicitudes,
        q=q,
        page=page,
        per_page=per_page,
        total=total,
        has_more=has_more,
        is_admin_role=is_admin_role,
    )

    if _admin_async_wants_json():
        html = render_template('admin/_solicitudes_copiar_results.html', **partial_ctx)
        return jsonify(_admin_async_payload(
            success=True,
            message='Listado actualizado.',
            category='info',
            replace_html=html,
            update_target='#copiarSolicitudesResults',
            extra={
                "page": page,
                "per_page": per_page,
                "total": total,
                "query": q,
            },
        )), 200

    return render_template(
        'admin/solicitudes_copiar.html',
        **partial_ctx
    )




@admin_bp.route('/solicitudes/<int:id>/copiar', methods=['POST'])
@login_required
@staff_required
def copiar_solicitud(id):
    s = Solicitud.query.get_or_404(id)
    next_url = (request.form.get('next') or request.referrer or '').strip()
    fallback = url_for('admin.copiar_solicitudes')

    if s.estado not in ('activa', 'reemplazo'):
        return _copiar_action_response(
            ok=False,
            message='Esta solicitud no es copiable en su estado actual.',
            category='warning',
            next_url=next_url,
            fallback=fallback,
            http_status=409,
        )

    start_utc, _ = _utc_day_bounds()
    last = _to_naive_utc(getattr(s, 'last_copiado_at', None))
    if last is not None and last >= start_utc:
        return _copiar_action_response(
            ok=True,
            message='Esta solicitud ya fue marcada como copiada hoy.',
            category='info',
            next_url=next_url,
            fallback=fallback,
            http_status=200,
            extra={"solicitud_id": s.id, "estado": s.estado, "remove_card": True},
        )

    try:
        s.last_copiado_at = func.now()
        db.session.commit()
        _audit_log(
            action_type="SOLICITUD_PUBLICAR",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Solicitud marcada como publicada/copiada: {s.codigo_solicitud or s.id}",
        )
        return _copiar_action_response(
            ok=True,
            message=f'Solicitud { _s(s.codigo_solicitud) } copiada. Ya no se mostrará hasta mañana.',
            category='success',
            next_url=next_url,
            fallback=fallback,
            http_status=200,
            extra={"solicitud_id": s.id, "estado": s.estado, "remove_card": True},
        )
    except SQLAlchemyError:
        db.session.rollback()
        return _copiar_action_response(
            ok=False,
            message='No se pudo marcar la solicitud como copiada.',
            category='danger',
            next_url=next_url,
            fallback=fallback,
            http_status=500,
        )
    except Exception:
        db.session.rollback()
        return _copiar_action_response(
            ok=False,
            message='Ocurrió un error al marcar como copiada.',
            category='danger',
            next_url=next_url,
            fallback=fallback,
            http_status=500,
        )


@admin_bp.route('/solicitudes/<int:id>/pausar_espera_perfil', methods=['POST'])
@login_required
@staff_required
def pausar_espera_perfil_desde_copiar(id):
    s = Solicitud.query.get_or_404(id)
    next_url = (request.form.get('next') or request.referrer or '').strip()
    fallback = url_for('admin.copiar_solicitudes')

    if s.estado == 'espera_pago':
        return _copiar_action_response(
            ok=True,
            message='La solicitud ya está en pausa por espera de perfil.',
            category='info',
            next_url=next_url,
            fallback=fallback,
            http_status=200,
            extra={"solicitud_id": s.id, "estado": "espera_pago", "remove_card": True},
        )

    estado_actual = (s.estado or '').strip().lower()
    if estado_actual in ('cancelada', 'pagada'):
        return _copiar_action_response(
            ok=False,
            message='No se puede pausar por espera de perfil en el estado actual.',
            category='warning',
            next_url=next_url,
            fallback=fallback,
            http_status=409,
        )

    try:
        if hasattr(s, 'estado_previo_espera_pago'):
            s.estado_previo_espera_pago = estado_actual or 'activa'
        _set_solicitud_estado(s, 'espera_pago')
        if hasattr(s, 'fecha_cambio_espera_pago'):
            s.fecha_cambio_espera_pago = utc_now_naive()
        if hasattr(s, 'usuario_cambio_espera_pago'):
            s.usuario_cambio_espera_pago = _staff_actor_name()
        db.session.commit()
        _audit_log(
            action_type="SOLICITUD_ESPERA_PERFIL_PONER",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Solicitud en pausa por espera de perfil: {s.codigo_solicitud or s.id}",
            changes={"estado": {"from": estado_actual, "to": "espera_pago"}},
        )
        return _copiar_action_response(
            ok=True,
            message='Solicitud pausada por espera de perfil.',
            category='success',
            next_url=next_url,
            fallback=fallback,
            http_status=200,
            extra={"solicitud_id": s.id, "estado": "espera_pago", "remove_card": True},
        )
    except Exception:
        db.session.rollback()
        return _copiar_action_response(
            ok=False,
            message='No se pudo pausar la solicitud por espera de perfil.',
            category='danger',
            next_url=next_url,
            fallback=fallback,
            http_status=500,
        )


@admin_bp.route('/solicitudes/<int:id>/reanudar_espera_perfil', methods=['POST'])
@login_required
@staff_required
def reanudar_espera_perfil_desde_copiar(id):
    s = Solicitud.query.get_or_404(id)
    next_url = (request.form.get('next') or request.referrer or '').strip()
    fallback = url_for('admin.copiar_solicitudes')

    if s.estado != 'espera_pago':
        return _copiar_action_response(
            ok=True,
            message='La solicitud no está en pausa por espera de perfil.',
            category='info',
            next_url=next_url,
            fallback=fallback,
            http_status=200,
            extra={"solicitud_id": s.id, "estado": s.estado, "remove_card": False},
        )

    try:
        restore = (getattr(s, 'estado_previo_espera_pago', None) or '').strip().lower()
        if restore in ('', 'espera_pago', 'cancelada', 'pagada'):
            restore = 'activa'
        _set_solicitud_estado(s, restore)
        if hasattr(s, 'fecha_cambio_espera_pago'):
            s.fecha_cambio_espera_pago = utc_now_naive()
        if hasattr(s, 'usuario_cambio_espera_pago'):
            s.usuario_cambio_espera_pago = _staff_actor_name()
        db.session.commit()
        _audit_log(
            action_type="SOLICITUD_ESPERA_PERFIL_QUITAR",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Solicitud reanudada desde espera de perfil: {s.codigo_solicitud or s.id}",
            changes={"estado": {"from": "espera_pago", "to": restore}},
        )
        return _copiar_action_response(
            ok=True,
            message=f'Solicitud reanudada y puesta en {restore}.',
            category='success',
            next_url=next_url,
            fallback=fallback,
            http_status=200,
            extra={"solicitud_id": s.id, "estado": restore, "remove_card": False},
        )
    except Exception:
        db.session.rollback()
        return _copiar_action_response(
            ok=False,
            message='No se pudo reanudar la solicitud.',
            category='danger',
            next_url=next_url,
            fallback=fallback,
            http_status=500,
            extra={"solicitud_id": s.id, "estado": s.estado, "remove_card": False},
        )


def _admin_async_wants_json() -> bool:
    # Wrapper local para evitar tocar callsites existentes.
    return shared_admin_async_wants_json(request)


def _copiar_wants_json() -> bool:
    # Compatibilidad interna con tests/flujo existente.
    return _admin_async_wants_json()


def _admin_async_payload(
    *,
    success: bool,
    message: str = '',
    category: str = 'info',
    redirect_url: str | None = None,
    replace_html: str | None = None,
    update_target: str | None = None,
    update_targets: list | None = None,
    invalidate_targets: list | None = None,
    remove_element: str | None = None,
    errors: list | None = None,
    error_code: str | None = None,
    extra: dict | None = None,
) -> dict:
    # Wrapper local para evitar tocar callsites existentes.
    try:
        if (error_code or "").strip().lower() in {"conflict", "idempotency_conflict"}:
            bump_operational_counter("concurrency_conflict_count")
    except Exception:
        pass
    return shared_admin_async_payload(
        success=success,
        message=message,
        category=category,
        redirect_url=redirect_url,
        replace_html=replace_html,
        update_target=update_target,
        update_targets=update_targets,
        invalidate_targets=invalidate_targets,
        remove_element=remove_element,
        errors=errors,
        error_code=error_code,
        extra=extra,
    )


def _copiar_action_response(
    *,
    ok: bool,
    message: str,
    category: str,
    next_url: str,
    fallback: str,
    http_status: int = 200,
    error_code: str | None = None,
    extra=None,
):
    safe_next = next_url if _is_safe_redirect_url(next_url) else fallback
    extra_payload = dict(extra or {})
    remove_element = None
    if bool(extra_payload.get("remove_card")) and extra_payload.get("solicitud_id"):
        remove_element = f"#sol-{extra_payload.get('solicitud_id')}"
    if _admin_async_wants_json():
        payload = _admin_async_payload(
            success=bool(ok),
            message=message,
            category=category,
            redirect_url=safe_next,
            update_target="#copiarSolicitudesResults",
            remove_element=remove_element,
            error_code=error_code,
            extra=extra_payload,
        )
        payload["next"] = safe_next  # compatibilidad con flujo anterior
        return jsonify(payload), http_status
    flash(message, category)
    return redirect(safe_next)


def _cliente_detail_id_from_url(url_like: str) -> int:
    raw = str(url_like or "").strip()
    if not raw:
        return 0
    try:
        path = str(urlparse(raw).path or "").strip()
    except Exception:
        path = raw.split("#", 1)[0].split("?", 1)[0]
    if not path:
        return 0
    normalized_path = path.rstrip("/")
    match = re.search(r"/clientes/(\d+)$", normalized_path)
    if not match:
        return 0
    try:
        return int(match.group(1) or 0)
    except Exception:
        return 0


def _solicitudes_list_action_response(
    *,
    ok: bool,
    message: str,
    category: str,
    next_url: str,
    fallback: str,
    http_status: int = 200,
    error_code: str | None = None,
    replace_html: str | None = None,
    update_target: str | None = None,
    update_targets: list | None = None,
    invalidate_targets: list | None = None,
    redirect_url: str | None = None,
    extra: dict | None = None,
):
    safe_next = next_url if _is_safe_redirect_url(next_url) else fallback
    dynamic_target = (request.form.get("_async_target") or request.args.get("_async_target") or "").strip()
    if not dynamic_target.startswith("#"):
        dynamic_target = ""
    resolved_target = update_target or dynamic_target or "#solicitudesAsyncRegion"
    resolved_redirect = redirect_url if redirect_url is not None else safe_next
    resolved_update_targets = list(update_targets or [])
    resolved_invalidate_targets = list(invalidate_targets or [])

    def _solicitudes_summary_update_target() -> dict:
        summary_fragment_url = url_for("admin.solicitudes_summary_fragment")
        target_payload = {
            "target": "#solicitudesSummaryAsyncRegion",
            "invalidate": True,
            "redirect_url": summary_fragment_url,
        }
        try:
            proc_count, copiable_count, _warning = _solicitudes_summary_counts()
            target_payload["replace_html"] = render_template(
                "admin/_solicitudes_summary_region.html",
                proc_count=proc_count,
                copiable_count=copiable_count,
            )
        except Exception:
            # Fallback seguro: mantiene invalidación por fragment endpoint.
            pass
        return target_payload

    if (
        bool(ok)
        and resolved_target == "#solicitudesAsyncRegion"
        and not replace_html
        and resolved_redirect
        and not resolved_update_targets
    ):
        resolved_update_targets = [
            {"target": "#solicitudesAsyncRegion", "invalidate": True, "redirect_url": resolved_redirect},
            _solicitudes_summary_update_target(),
        ]
    if (
        bool(ok)
        and resolved_target == "#clienteSolicitudesAsyncRegion"
        and not replace_html
        and resolved_redirect
        and not resolved_update_targets
    ):
        cliente_id = _cliente_detail_id_from_url(resolved_redirect)
        solicitudes_fragment_url = (
            url_for("admin.cliente_detail_solicitudes_fragment", cliente_id=cliente_id)
            if cliente_id > 0
            else resolved_redirect
        )
        summary_fragment_url = (
            url_for("admin.cliente_detail_summary_fragment", cliente_id=cliente_id)
            if cliente_id > 0
            else resolved_redirect
        )
        resolved_update_targets = [
            {"target": "#clienteSolicitudesAsyncRegion", "invalidate": True, "redirect_url": solicitudes_fragment_url},
            {"target": "#clienteSummaryAsyncRegion", "invalidate": True, "redirect_url": summary_fragment_url},
        ]
    if _admin_async_wants_json():
        payload = _admin_async_payload(
            success=bool(ok),
            message=message,
            category=category,
            redirect_url=resolved_redirect,
            replace_html=replace_html,
            update_target=resolved_target,
            update_targets=resolved_update_targets,
            invalidate_targets=resolved_invalidate_targets,
            error_code=error_code,
            extra=(extra or None),
        )
        payload["next"] = safe_next
        return jsonify(payload), http_status
    flash(message, category)
    return redirect(safe_next)


def _reemplazo_operativo_region_config(dynamic_target: str) -> dict | None:
    target = str(dynamic_target or "").strip()
    if target.startswith("#solicitudReemplazoActionsAsyncRegion-"):
        return {"style": "list", "scope": "#solicitudesAsyncScope"}
    if target.startswith("#clienteSolicitudReemplazoActionsAsyncRegion-"):
        return {"style": "cliente", "scope": "#clienteSolicitudesAsyncScope"}
    return None


def _build_cliente_summary_kpi(*, cliente, solicitudes: list) -> dict:
    total_sol = len(solicitudes or [])
    estados_count = {
        "proceso": 0,
        "activa": 0,
        "pagada": 0,
        "cancelada": 0,
        "reemplazo": 0,
        "otro": 0,
    }

    monto_total_pagado = Decimal("0.00")
    primera_solicitud = None
    ultima_solicitud = None

    for s in (solicitudes or []):
        estado = (getattr(s, "estado", "") or "").strip().lower() or "otro"
        if estado not in estados_count:
            estado = "otro"
        estados_count[estado] += 1

        raw_monto = (getattr(s, "monto_pagado", "") or "").strip()
        if raw_monto:
            try:
                monto_total_pagado += Decimal(raw_monto)
            except Exception:
                pass

        fs = getattr(s, "fecha_solicitud", None)
        if fs:
            if primera_solicitud is None or fs < primera_solicitud:
                primera_solicitud = fs
            if ultima_solicitud is None or fs > ultima_solicitud:
                ultima_solicitud = fs

    ultima_actividad = getattr(cliente, "fecha_ultima_actividad", None) or ultima_solicitud
    monto_total_pagado_str = f"RD$ {monto_total_pagado:,.2f}"

    return {
        "total_solicitudes": total_sol,
        "estados": estados_count,
        "monto_total_pagado": monto_total_pagado,
        "monto_total_pagado_str": monto_total_pagado_str,
        "primera_solicitud": primera_solicitud,
        "ultima_solicitud": ultima_solicitud,
        "ultima_actividad": ultima_actividad,
    }


def _render_reemplazo_actions_region(*, solicitud_id: int, dynamic_target: str, next_url: str) -> str | None:
    cfg = _reemplazo_operativo_region_config(dynamic_target)
    if cfg is None:
        return None

    query = Solicitud.query
    if hasattr(query, "options"):
        try:
            query = query.options(joinedload(Solicitud.reemplazos), joinedload(Solicitud.candidata))
        except Exception:
            pass
    if hasattr(query, "filter_by"):
        query = query.filter_by(id=solicitud_id)
    solicitud = None
    if hasattr(query, "first"):
        try:
            solicitud = query.first()
        except Exception:
            solicitud = None
    if solicitud is None and hasattr(query, "first_or_404"):
        try:
            solicitud = query.first_or_404()
        except Exception:
            solicitud = None
    if solicitud is None and hasattr(query, "all"):
        try:
            rows = list(query.all() or [])
            for row in rows:
                if int(getattr(row, "id", 0) or 0) == int(solicitud_id):
                    solicitud = row
                    break
        except Exception:
            solicitud = None
    if solicitud is None:
        return None

    repl_activo = _active_reemplazo_for_solicitud(solicitud)
    estado_norm = (getattr(solicitud, "estado", "") or "").strip().lower()
    repl_operable = bool(repl_activo and estado_norm == "reemplazo")
    role = (
        str(getattr(current_user, "role", "") or "").strip().lower()
        or str(session.get("role", "") or "").strip().lower()
    )
    is_admin_role = role in ("owner", "admin")
    effective_next = next_url or request.full_path

    return render_template(
        "admin/_reemplazo_actions_region.html",
        solicitud=solicitud,
        repl_activo=repl_activo,
        repl_operable=repl_operable,
        is_admin_role=is_admin_role,
        async_target=dynamic_target,
        async_scope=cfg["scope"],
        next_url=effective_next,
        style=cfg["style"],
        form_idempotency_key=_new_form_idempotency_key(),
    )


def _reemplazo_parent_async_target(dynamic_target: str) -> str:
    target = str(dynamic_target or "").strip()
    if target == "#solicitudesAsyncRegion" or target.startswith("#solicitudReemplazoActionsAsyncRegion-"):
        return "#solicitudesAsyncRegion"
    if target == "#clienteSolicitudesAsyncRegion" or target.startswith("#clienteSolicitudReemplazoActionsAsyncRegion-"):
        return "#clienteSolicitudesAsyncRegion"
    return ""


def _maybe_solicitud_operativa_core_async_response(
    *,
    solicitud_id: int,
    ok: bool,
    message: str,
    category: str,
    next_url: str,
    fallback: str,
    http_status: int = 200,
    error_code: str | None = None,
):
    dynamic_target = (request.form.get("_async_target") or request.args.get("_async_target") or "").strip()
    if (not _admin_async_wants_json()) or (dynamic_target != "#solicitudOperativaCoreAsyncRegion"):
        return None

    safe_next = next_url if _is_safe_redirect_url(next_url) else fallback
    solicitud = (
        Solicitud.query
        .options(joinedload(Solicitud.candidata))
        .filter_by(id=solicitud_id)
        .first()
    )
    if solicitud is None:
        payload = _admin_async_payload(
            success=False,
            message='No encontramos la solicitud para refrescar el bloque operativo.',
            category='danger',
            redirect_url=safe_next,
            update_target=dynamic_target,
            error_code='not_found',
        )
        payload["next"] = safe_next
        return jsonify(payload), 404

    now_utc = utc_now_naive()
    _score, priority_label, is_stagnant, _hours = _solicitud_priority_snapshot(solicitud, now_dt=now_utc)
    manual_followup = _manual_followup_snapshot(
        getattr(solicitud, "fecha_seguimiento_manual", None),
        today_rd=rd_today(),
    )
    solicitud_detail_url = url_for("admin.detalle_solicitud", cliente_id=solicitud.cliente_id, id=solicitud.id)
    html = render_template(
        'admin/_solicitud_operativa_core_region.html',
        solicitud=solicitud,
        async_feedback={"message": message, "category": category},
        now_utc=now_utc,
        priority_label_operativa=priority_label,
        needs_followup_today=_solicitud_needs_followup_today(
            is_stagnant=is_stagnant,
            priority_label=priority_label,
        ),
        manual_followup=manual_followup,
        solicitud_detail_url=solicitud_detail_url,
    )
    payload = _admin_async_payload(
        success=bool(ok),
        message=message,
        category=category,
        redirect_url=safe_next,
        replace_html=html,
        update_target=dynamic_target,
        update_targets=(
            [
                {
                    "target": "#solicitudSummaryAsyncRegion",
                    "invalidate": True,
                    "redirect_url": url_for(
                        "admin.solicitud_detail_summary_fragment",
                        cliente_id=solicitud.cliente_id,
                        id=solicitud.id,
                    ),
                },
            ]
            if bool(ok)
            else None
        ),
        error_code=error_code,
    )
    payload["next"] = safe_next
    return jsonify(payload), http_status


def _clientes_list_action_response(
    *,
    ok: bool,
    message: str,
    category: str,
    next_url: str,
    fallback: str,
    http_status: int = 200,
    error_code: str | None = None,
):
    safe_next = next_url if _is_safe_redirect_url(next_url) else fallback
    if _admin_async_wants_json():
        payload = _admin_async_payload(
            success=bool(ok),
            message=message,
            category=category,
            redirect_url=safe_next,
            update_target="#clientesAsyncRegion",
            error_code=error_code,
        )
        payload["next"] = safe_next
        return jsonify(payload), http_status
    flash(message, category)
    return redirect(safe_next)


def _usuarios_list_next_url(*, q: str, page: int, per_page: int) -> str:
    return url_for(
        "admin.listar_usuarios",
        q=q,
        page=page,
        per_page=per_page,
    )


def _usuarios_list_page_data(*, q: str, page: int, per_page: int):
    query = StaffUser.query
    hidden_username = _emergency_hide_username()
    hidden_prefix = _emergency_hide_prefix()
    if hidden_username:
        query = query.filter(func.lower(StaffUser.username) != hidden_username)
    if hidden_prefix:
        query = query.filter(~func.lower(StaffUser.username).like(f"{hidden_prefix}%"))
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                StaffUser.username.ilike(like),
                StaffUser.email.ilike(like),
            )
        )

    total = query.count()
    usuarios = (
        query.order_by(StaffUser.created_at.desc(), StaffUser.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    last_page = max(1, (total + per_page - 1) // per_page)
    return usuarios, total, last_page


def _usuarios_list_context_for_refresh(next_url: str, fallback: str):
    safe_next = next_url if _is_safe_redirect_url(next_url) else fallback
    parsed = urlparse(safe_next)
    query = parse_qs(parsed.query)

    q = ((query.get("q") or [""])[0] or "").strip()
    try:
        page = int((query.get("page") or ["1"])[0] or 1)
    except Exception:
        page = 1
    page = max(1, page)

    try:
        per_page = int((query.get("per_page") or ["20"])[0] or 20)
    except Exception:
        per_page = 20
    per_page = max(10, min(per_page, 100))
    list_next = _usuarios_list_next_url(q=q, page=page, per_page=per_page)
    return q, page, per_page, list_next


def _usuarios_list_action_response(
    *,
    ok: bool,
    message: str,
    category: str,
    next_url: str,
    fallback: str,
    http_status: int = 200,
    error_code: str | None = None,
):
    safe_next = next_url if _is_safe_redirect_url(next_url) else fallback
    if _admin_async_wants_json():
        q, page, per_page, list_next = _usuarios_list_context_for_refresh(
            next_url=safe_next,
            fallback=fallback,
        )
        usuarios, total, last_page = _usuarios_list_page_data(
            q=q,
            page=page,
            per_page=per_page,
        )
        html = render_template(
            "admin/_usuarios_list_results.html",
            usuarios=usuarios,
            q=q,
            page=page,
            per_page=per_page,
            total=total,
            last_page=last_page,
            list_next=list_next,
            min_password_len=_staff_password_min_len(),
        )
        payload = _admin_async_payload(
            success=bool(ok),
            message=message,
            category=category,
            redirect_url=safe_next,
            replace_html=html,
            update_target="#usuariosAsyncRegion",
            error_code=error_code,
        )
        payload["next"] = safe_next
        return jsonify(payload), http_status
    flash(message, category)
    return redirect(safe_next)


def _security_admin_action_response(
    *,
    ok: bool,
    message: str,
    category: str,
    fallback: str,
    http_status: int = 200,
    error_code: str | None = None,
    replace_html: str | None = None,
    update_target: str | None = None,
    update_targets: list | None = None,
    invalidate_targets: list | None = None,
    errors: list | None = None,
):
    safe_next = _safe_next_url(fallback)
    if _admin_async_wants_json():
        payload = _admin_async_payload(
            success=bool(ok),
            message=message,
            category=category,
            redirect_url=safe_next,
            replace_html=replace_html,
            update_target=update_target,
            update_targets=update_targets,
            invalidate_targets=invalidate_targets,
            errors=errors or [],
            error_code=error_code,
        )
        payload["next"] = safe_next
        return jsonify(payload), http_status
    flash(message, category)
    return redirect(safe_next)


@admin_bp.route('/solicitudes/copiar/candidatas_lookup', methods=['GET'])
@login_required
@admin_required
def candidatas_lookup_copiar():
    q = (request.args.get('q') or '').strip()
    include_raw = (request.args.get('include_id') or '').strip()
    try:
        limit = int(request.args.get('limit', 50) or 50)
    except Exception:
        limit = 50
    limit = max(1, min(limit, 100))

    base_q = (
        Candidata.query
        .options(load_only(Candidata.fila, Candidata.nombre_completo))
        .filter(candidatas_activas_filter(Candidata))
    )

    items = []
    seen = set()
    if q:
        like = f"%{q}%"
        rows = (
            base_q
            .filter(or_(
                Candidata.nombre_completo.ilike(like),
                cast(Candidata.fila, db.Text).ilike(like),
            ))
            .order_by(Candidata.nombre_completo.asc())
            .limit(limit)
            .all()
        )
        for cand in rows:
            seen.add(int(cand.fila))
            items.append({
                "value": str(cand.fila),
                "text": f"{cand.nombre_completo} (ID {cand.fila})",
            })

    include_id = None
    try:
        include_id = int(include_raw) if include_raw else None
    except Exception:
        include_id = None
    if include_id and include_id not in seen:
        include_c = (
            base_q
            .filter(Candidata.fila == include_id)
            .first()
        )
        if include_c:
            items.insert(0, {
                "value": str(include_c.fila),
                "text": f"{include_c.nombre_completo} (ID {include_c.fila})",
            })

    return jsonify({
        "ok": True,
        "q": q,
        "count": len(items),
        "items": items,
    })


@admin_bp.route('/solicitudes/<int:id>/cancelar_desde_copiar', methods=['POST'])
@login_required
@staff_required
def cancelar_solicitud_desde_copiar(id):
    s = Solicitud.query.get_or_404(id)
    next_url = (request.form.get('next') or request.referrer or '').strip()
    fallback = url_for('admin.copiar_solicitudes')
    estado_actual = (s.estado or '').strip().lower()
    blocked_resp = _admin_block_sensitive_action(
        scope="admin_solicitud_cancelar",
        entity_type="Solicitud",
        entity_id=s.id,
        limit=25,
        window_seconds=600,
        min_interval_seconds=1,
        summary=f"Bloqueo de cancelación de solicitud por patrón de abuso ({s.id})",
        next_url=next_url,
        fallback=fallback,
    )
    if blocked_resp is not None:
        if _admin_async_wants_json():
            return _copiar_action_response(
                ok=False,
                message='Demasiadas acciones seguidas. Espera un momento e intenta nuevamente.',
                category='warning',
                next_url=next_url,
                fallback=fallback,
                http_status=429,
                error_code='rate_limit',
            )
        return blocked_resp

    motivo = (request.form.get('motivo') or '').strip()
    if len(motivo) < 5:
        return _copiar_action_response(
            ok=False,
            message='Indica un motivo de cancelación (mínimo 5 caracteres).',
            category='danger',
            next_url=next_url,
            fallback=fallback,
            http_status=400,
        )

    if estado_actual in ('cancelada', 'pagada'):
        if _admin_noop_repeat_blocked(
            scope="admin_solicitud_cancelar",
            entity_type="Solicitud",
            entity_id=s.id,
            state=estado_actual,
            summary=f"Intento repetido de cancelar solicitud sin transición válida ({s.id})",
        ):
            return _copiar_action_response(
                ok=False,
                message='Acción bloqueada temporalmente por repetición de una operación ya aplicada.',
                category='warning',
                next_url=next_url,
                fallback=fallback,
                http_status=429,
            )
        return _copiar_action_response(
            ok=False,
            message=f'La solicitud está en estado «{estado_actual}» y no puede cancelarse desde esta pantalla.',
            category='warning',
            next_url=next_url,
            fallback=fallback,
            http_status=409,
            error_code='state_conflict',
        )

    if estado_actual not in ('proceso', 'activa', 'reemplazo', 'espera_pago'):
        estado_label = estado_actual or 'desconocido'
        return _copiar_action_response(
            ok=False,
            message=f'La solicitud está en estado «{estado_label}» y no admite cancelación desde este flujo.',
            category='warning',
            next_url=next_url,
            fallback=fallback,
            http_status=409,
            error_code='state_conflict',
        )

    expected_version = _expected_row_version()
    if _critical_concurrency_guards_enabled() and expected_version is not None:
        current_version = int(getattr(s, "row_version", 0) or 0)
        if int(expected_version) != current_version:
            return _copiar_action_response(
                ok=False,
                message='La solicitud cambió mientras trabajabas. Recarga y vuelve a intentar.',
                category='warning',
                next_url=next_url,
                fallback=fallback,
                http_status=409,
                error_code='conflict',
            )

    idem_row, duplicate = _claim_idempotency(
        scope="solicitud_estado_cancelar",
        entity_type="Solicitud",
        entity_id=s.id,
        action="cancelar_solicitud_desde_copiar",
    )
    if duplicate:
        if _idempotency_request_conflict(idem_row):
            return _copiar_action_response(
                ok=False,
                message=_idempotency_conflict_message(),
                category='warning',
                next_url=next_url,
                fallback=fallback,
                http_status=409,
                error_code='idempotency_conflict',
            )
        prev_status = int(getattr(idem_row, "response_status", 0) or 0)
        if 200 <= prev_status < 300:
            return _copiar_action_response(
                ok=True,
                message='Acción ya aplicada previamente.',
                category='info',
                next_url=next_url,
                fallback=fallback,
                http_status=200,
            )
        return _copiar_action_response(
            ok=False,
            message='Solicitud duplicada detectada. Espera y vuelve a intentar.',
            category='warning',
            next_url=next_url,
            fallback=fallback,
            http_status=409,
            error_code='conflict',
        )

    try:
        released = _release_solicitud_candidatas_on_cancel(
            s,
            actor=_staff_actor_name(),
            motivo=motivo,
        )
        _set_solicitud_estado(s, 'cancelada')
        s.motivo_cancelacion = motivo
        s.fecha_cancelacion = _now_utc()
        if int(released.get("released_count", 0) or 0) > 0:
            _emit_domain_outbox_event(
                event_type="SOLICITUD_CANDIDATAS_LIBERADAS",
                aggregate_type="Solicitud",
                aggregate_id=s.id,
                aggregate_version=(int(getattr(s, "row_version", 0) or 0) + 1),
                payload={
                    "solicitud_id": int(s.id),
                    "count": int(released.get("released_count", 0) or 0),
                    "candidata_ids": list(released.get("candidata_ids") or []),
                    "reason": "cancelacion_solicitud",
                },
            )
        _set_idempotency_response(idem_row, status=200, code="ok")
        db.session.commit()
        _audit_log(
            action_type="SOLICITUD_CANCELAR_DESDE_COPIAR",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Solicitud cancelada desde copiar/publicar: {s.codigo_solicitud or s.id}",
            changes={"estado": {"from": estado_actual, "to": "cancelada"}},
            metadata={"motivo": motivo[:255]},
        )
        return _copiar_action_response(
            ok=True,
            message=f'Solicitud {s.codigo_solicitud} cancelada.',
            category='success',
            next_url=next_url,
            fallback=fallback,
            http_status=200,
            extra={"solicitud_id": s.id, "estado": "cancelada", "remove_card": True},
        )
    except StaleDataError:
        db.session.rollback()
        return _copiar_action_response(
            ok=False,
            message='La solicitud cambió por otra sesión. Recarga e intenta nuevamente.',
            category='warning',
            next_url=next_url,
            fallback=fallback,
            http_status=409,
            error_code='conflict',
        )
    except SQLAlchemyError:
        db.session.rollback()
        return _copiar_action_response(
            ok=False,
            message='No se pudo cancelar la solicitud.',
            category='danger',
            next_url=next_url,
            fallback=fallback,
            http_status=500,
        )
    except Exception:
        db.session.rollback()
        return _copiar_action_response(
            ok=False,
            message='Ocurrió un error al cancelar la solicitud.',
            category='danger',
            next_url=next_url,
            fallback=fallback,
            http_status=500,
        )


@admin_bp.route('/solicitudes/<int:id>/marcar_pagada_desde_copiar', methods=['POST'])
@login_required
@admin_required
def marcar_pagada_desde_copiar(id):
    s = Solicitud.query.get_or_404(id)
    next_url = (request.form.get('next') or request.referrer or '').strip()
    fallback = url_for('admin.copiar_solicitudes')
    action_payload = {"action": "paid"}

    def _paid_response(
        *,
        ok: bool,
        message: str,
        category: str,
        http_status: int = 200,
        error_code: str | None = None,
        extra: dict | None = None,
    ):
        merged = dict(action_payload)
        if extra:
            merged.update(dict(extra))
        return _copiar_action_response(
            ok=ok,
            message=message,
            category=category,
            next_url=next_url,
            fallback=fallback,
            http_status=http_status,
            error_code=error_code,
            extra=merged,
        )

    estado_actual = (s.estado or '').strip().lower()
    if estado_actual in ('cancelada', 'pagada'):
        return _paid_response(
            ok=False,
            message='Esta solicitud no admite marcarse como pagada en su estado actual.',
            category='warning',
            http_status=409,
            error_code='conflict',
        )

    expected_version = _expected_row_version()
    if _critical_concurrency_guards_enabled() and expected_version is not None:
        current_version = int(getattr(s, "row_version", 0) or 0)
        if int(expected_version) != current_version:
            return _paid_response(
                ok=False,
                message='La solicitud cambió mientras trabajabas. Recarga y vuelve a intentar.',
                category='warning',
                http_status=409,
                error_code='conflict',
            )

    idem_row, duplicate = _claim_idempotency(
        scope="solicitud_marcar_pagada_desde_copiar",
        entity_type="Solicitud",
        entity_id=s.id,
        action="marcar_pagada_desde_copiar",
    )
    if duplicate:
        if _idempotency_request_conflict(idem_row):
            return _paid_response(
                ok=False,
                message=_idempotency_conflict_message(),
                category='warning',
                http_status=409,
                error_code='idempotency_conflict',
            )
        prev_status = int(getattr(idem_row, "response_status", 0) or 0)
        if 200 <= prev_status < 300:
            return _paid_response(
                ok=True,
                message='Acción ya aplicada previamente.',
                category='info',
                http_status=200,
            )
        return _paid_response(
            ok=False,
            message='Solicitud duplicada detectada. Espera y vuelve a intentar.',
            category='warning',
            http_status=409,
            error_code='conflict',
        )

    candidata_raw = (request.form.get('candidata_id') or '').strip()
    monto_raw = (request.form.get('monto_pagado') or '').strip()
    if not candidata_raw or not monto_raw:
        return _paid_response(
            ok=False,
            message='Para marcar pagado debes indicar candidata y monto pagado.',
            category='danger',
            http_status=400,
        )

    try:
        candidata_id = int(candidata_raw)
    except Exception:
        return _paid_response(
            ok=False,
            message='La candidata seleccionada no es válida.',
            category='danger',
            http_status=400,
        )

    cand = _lock_candidata_for_update(candidata_id)
    if not cand:
        return _paid_response(
            ok=False,
            message='La candidata seleccionada no existe.',
            category='danger',
            http_status=404,
        )

    if candidata_esta_descalificada(cand):
        return _paid_response(
            ok=False,
            message='No se puede asignar una candidata descalificada.',
            category='danger',
            http_status=409,
            error_code='conflict',
        )

    blocked_candidate_ids, _ = _matching_candidate_flags(s, [int(cand.fila)])
    if int(cand.fila) in blocked_candidate_ids:
        return _paid_response(
            ok=False,
            message='Esta candidata ya está bloqueada por otra solicitud activa de otro cliente.',
            category='warning',
            http_status=409,
            error_code='blocked_other_client',
        )

    try:
        s.candidata_id = cand.fila
        _sync_solicitud_candidatas_after_assignment(s, cand.fila)
        _mark_candidata_estado(cand, "trabajando")
        s.monto_pagado = _parse_money_to_decimal_str(monto_raw)
        _set_solicitud_estado(s, 'pagada')
        _emit_domain_outbox_event(
            event_type="SOLICITUD_CANDIDATA_ASIGNADA",
            aggregate_type="Solicitud",
            aggregate_id=s.id,
            aggregate_version=(int(getattr(s, "row_version", 0) or 0) + 1),
            payload={
                "solicitud_id": int(s.id),
                "candidata_id": int(cand.fila),
                "from": estado_actual,
                "to": "pagada",
                "monto_pagado": s.monto_pagado,
            },
        )
        _set_idempotency_response(idem_row, status=200, code="ok")
        db.session.commit()
        try:
            _notify_cliente_candidata_asignada(s, candidata_id=int(getattr(cand, "fila", 0) or 0))
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.warning(
                "marcar_pagada_desde_copiar notify_cliente_candidata_asignada_failed solicitud_id=%s candidata_id=%s",
                int(getattr(s, "id", 0) or 0),
                int(getattr(cand, "fila", 0) or 0),
                exc_info=True,
            )
        _audit_log(
            action_type="SOLICITUD_MARCAR_PAGADA_DESDE_COPIAR",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Solicitud marcada pagada desde copiar/publicar: {s.codigo_solicitud or s.id}",
            changes={"estado": {"from": estado_actual, "to": "pagada"}},
            metadata={"candidata_id": cand.fila, "monto_pagado": s.monto_pagado},
        )
        return _paid_response(
            ok=True,
            message='Solicitud marcada como pagada.',
            category='success',
            http_status=200,
            extra={
                "solicitud_id": s.id,
                "estado": "pagada",
                "remove_card": True,
                "candidata_id": cand.fila,
            },
        )
    except InvariantConflictError as inv_exc:
        db.session.rollback()
        return _paid_response(
            ok=False,
            message=str(inv_exc) or 'Conflicto de estado de candidata.',
            category='warning',
            http_status=409,
            error_code=getattr(inv_exc, "code", "conflict"),
        )
    except StaleDataError:
        db.session.rollback()
        return _paid_response(
            ok=False,
            message='La solicitud cambió por otra sesión. Recarga e intenta nuevamente.',
            category='warning',
            http_status=409,
            error_code='conflict',
        )
    except ValueError:
        db.session.rollback()
        return _paid_response(
            ok=False,
            message='El monto ingresado no es válido. Revísalo e inténtalo de nuevo.',
            category='danger',
            http_status=400,
        )
    except SQLAlchemyError:
        db.session.rollback()
        return _paid_response(
            ok=False,
            message='No se pudo marcar la solicitud como pagada.',
            category='danger',
            http_status=500,
        )
    except Exception:
        db.session.rollback()
        return _paid_response(
            ok=False,
            message='Ocurrió un error al marcar la solicitud como pagada.',
            category='danger',
            http_status=500,
        )


# =============================================================================
#                 VISTAS "EN PROCESO" Y RESUMEN DIARIO (MEJORADAS)
# =============================================================================

# Utilidades compartidas (si ya las definiste antes, no las dupliques):
def _now_utc() -> datetime:
    return utc_now_naive()

def _utc_day_bounds(dt: datetime | None = None):
    """(inicio_día_utc, fin_día_utc) para dt (o hoy UTC)."""
    base = (dt or utc_now_naive()).date()
    start = datetime(base.year, base.month, base.day)
    end = start + timedelta(days=1)
    return start, end

from urllib.parse import urlparse, urljoin

def _is_safe_redirect_url(target: str) -> bool:
    if not target:
        return False
    ref = urlparse(request.host_url)
    test = urlparse(urljoin(request.host_url, target))
    return (test.scheme in ('http', 'https')) and (ref.netloc == test.netloc)

# ---------------------------------------
# Clientes con solicitudes "en proceso"
# ---------------------------------------
@admin_bp.route('/solicitudes/proceso/clients')
@login_required
@staff_required
def listar_clientes_con_proceso():
    """
    Lista clientes con solicitudes en 'proceso' y el conteo de pendientes.
    Incluye paginación opcional: ?page=1&per_page=50 y búsqueda ?q=...
    """
    q = (request.args.get('q') or '').strip()
    page = max(1, int(request.args.get('page', 1) or 1))
    per_page = max(10, min(int(request.args.get('per_page', 50) or 50), 200))

    base = (
        db.session.query(
            Cliente.id,
            Cliente.nombre_completo,
            Cliente.codigo,
            Cliente.telefono,
            func.count(Solicitud.id).label('pendientes')
        )
        .join(Solicitud, Solicitud.cliente_id == Cliente.id)
        .filter(Solicitud.estado == 'proceso')
        .group_by(Cliente.id, Cliente.nombre_completo, Cliente.codigo, Cliente.telefono)
    )

    if q:
        like = f'%{q}%'
        base = base.filter(
            or_(
                Cliente.nombre_completo.ilike(like),
                Cliente.codigo.ilike(like),
                Cliente.telefono.ilike(like),
            )
        )

    total = base.count()
    resultados = (base
                  .order_by(Cliente.nombre_completo.asc())
                  .offset((page - 1) * per_page)
                  .limit(per_page)
                  .all())

    return render_template(
        'admin/solicitudes_proceso_clients.html',
        resultados=resultados,
        q=q,
        page=page,
        per_page=per_page,
        total=total,
        has_more=(page * per_page) < total
    )

# ---------------------------------------
# Listado de solicitudes "en proceso" por cliente
# ---------------------------------------
@admin_bp.route('/solicitudes/proceso/<int:cliente_id>')
@login_required
@staff_required
def listar_solicitudes_de_cliente_proceso(cliente_id):
    c = Cliente.query.get_or_404(cliente_id)

    # Paginación ligera por si hay muchas
    page = max(1, int(request.args.get('page', 1) or 1))
    per_page = max(10, min(int(request.args.get('per_page', 50) or 50), 200))

    base = (Solicitud.query
            .filter_by(cliente_id=cliente_id, estado='proceso')
            .order_by(Solicitud.fecha_solicitud.desc()))
    total = base.count()
    solicitudes = (base
                   .offset((page - 1) * per_page)
                   .limit(per_page)
                   .all())

    return render_template(
        'admin/solicitudes_proceso_list.html',
        cliente=c,
        solicitudes=solicitudes,
        page=page,
        per_page=per_page,
        total=total,
        has_more=(page * per_page) < total
    )

# ---------------------------------------
# Acciones rápidas sobre "proceso"
# ---------------------------------------
@admin_bp.route('/solicitudes/proceso/acciones')
@login_required
@staff_required
def acciones_solicitudes_proceso():
    # Paginación opcional
    page = max(1, int(request.args.get('page', 1) or 1))
    per_page = max(10, min(int(request.args.get('per_page', 50) or 50), 200))

    base = (Solicitud.query
            .filter_by(estado='proceso')
            .order_by(Solicitud.fecha_solicitud.desc()))
    total = base.count()
    solicitudes = (base
                   .offset((page - 1) * per_page)
                   .limit(per_page)
                   .all())

    list_ctx = dict(
        solicitudes=solicitudes,
        page=page,
        per_page=per_page,
        total=total,
        has_more=(page * per_page) < total,
    )

    if _admin_async_wants_json():
        html = render_template('admin/_solicitudes_proceso_acciones_results.html', **list_ctx)
        return jsonify(_admin_async_payload(
            success=True,
            message='Listado actualizado.',
            category='info',
            replace_html=html,
            update_target='#procesoAccionesAsyncRegion',
            extra={
                "page": page,
                "per_page": per_page,
                "total": total,
            },
        )), 200

    return render_template(
        'admin/solicitudes_proceso_acciones.html',
        **list_ctx,
    )

# ---------------------------------------
# Activar solicitud (de proceso -> activa)
# ---------------------------------------
@admin_bp.route('/solicitudes/<int:id>/activar', methods=['POST'])
@login_required
@staff_required
def activar_solicitud_directa(id):
    s = Solicitud.query.get_or_404(id)
    next_url = request.form.get('next') or request.referrer
    fallback = url_for('admin.acciones_solicitudes_proceso')

    def _action_response(
        *,
        ok: bool,
        message: str,
        category: str,
        http_status: int = 200,
        error_code: str | None = None,
    ):
        response_extra = {
            "focus_row_id": int(s.id),
            "flash_row": True,
            "preserve_open_collapses": True,
        } if bool(ok) else None
        core_async_resp = _maybe_solicitud_operativa_core_async_response(
            solicitud_id=s.id,
            ok=ok,
            message=message,
            category=category,
            next_url=next_url or '',
            fallback=fallback,
            http_status=http_status,
            error_code=error_code,
        )
        if core_async_resp is not None:
            return core_async_resp
        return _solicitudes_list_action_response(
            ok=ok,
            message=message,
            category=category,
            next_url=next_url or '',
            fallback=fallback,
            http_status=http_status,
            error_code=error_code,
            extra=response_extra,
        )

    blocked_resp = _admin_block_sensitive_action(
        scope="admin_solicitud_activar",
        entity_type="Solicitud",
        entity_id=s.id,
        limit=35,
        window_seconds=600,
        min_interval_seconds=1,
        summary=f"Bloqueo de activación de solicitud por patrón de abuso ({s.id})",
        next_url=next_url or "",
        fallback=fallback,
    )
    if blocked_resp is not None:
        if _admin_async_wants_json():
            return _action_response(
                ok=False,
                message='Demasiadas acciones seguidas. Espera un momento e intenta nuevamente.',
                category='warning',
                http_status=429,
                error_code='rate_limit',
            )
        return blocked_resp

    try:
        if s.estado != 'proceso':
            if _admin_noop_repeat_blocked(
                scope="admin_solicitud_activar",
                entity_type="Solicitud",
                entity_id=s.id,
                state=(s.estado or ""),
                summary=f"Intento repetido de activar solicitud fuera de flujo ({s.id})",
            ):
                return _action_response(
                    ok=False,
                    message='Acción bloqueada temporalmente: esta solicitud no está en estado proceso.',
                    category='warning',
                    http_status=429,
                    error_code='rate_limit',
                )
            return _action_response(
                ok=False,
                message=f'La solicitud {s.codigo_solicitud} no está en "proceso".',
                category='warning',
                http_status=409,
                error_code='conflict',
            )

        _set_solicitud_estado_with_outbox(s, 'activa')
        db.session.commit()
        _audit_log(
            action_type="SOLICITUD_ACTIVAR",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Solicitud activada: {s.codigo_solicitud or s.id}",
            changes={"estado": {"from": "proceso", "to": "activa"}},
        )
        return _action_response(
            ok=True,
            message=f'Solicitud {s.codigo_solicitud} marcada como activa.',
            category='success',
        )
    except SQLAlchemyError:
        db.session.rollback()
        _audit_log(
            action_type="SOLICITUD_ACTIVAR",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Fallo activando solicitud {s.codigo_solicitud or s.id}",
            success=False,
            error="No se pudo activar la solicitud.",
        )
        return _action_response(
            ok=False,
            message='No se pudo activar la solicitud.',
            category='danger',
            http_status=500,
            error_code='server_error',
        )
    except Exception:
        db.session.rollback()
        _audit_log(
            action_type="SOLICITUD_ACTIVAR",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Fallo activando solicitud {s.codigo_solicitud or s.id}",
            success=False,
            error="Ocurrió un error al activar la solicitud.",
        )
        return _action_response(
            ok=False,
            message='Ocurrió un error al activar la solicitud.',
            category='danger',
            http_status=500,
            error_code='server_error',
        )


@admin_bp.route('/solicitudes/<int:id>/seguimiento_manual', methods=['POST'])
@login_required
@staff_required
def actualizar_seguimiento_manual_solicitud(id):
    s = Solicitud.query.get_or_404(id)
    next_url = request.form.get('next') or request.referrer
    fallback = url_for('admin.detalle_solicitud', cliente_id=s.cliente_id, id=s.id)
    raw_date = (request.form.get('fecha_seguimiento_manual') or '').strip()

    def _action_response(
        *,
        ok: bool,
        message: str,
        category: str,
        http_status: int = 200,
        error_code: str | None = None,
    ):
        response_extra = {
            "focus_row_id": int(s.id),
            "flash_row": True,
            "preserve_open_collapses": True,
        } if bool(ok) else None
        core_async_resp = _maybe_solicitud_operativa_core_async_response(
            solicitud_id=s.id,
            ok=ok,
            message=message,
            category=category,
            next_url=next_url or '',
            fallback=fallback,
            http_status=http_status,
            error_code=error_code,
        )
        if core_async_resp is not None:
            return core_async_resp
        return _solicitudes_list_action_response(
            ok=ok,
            message=message,
            category=category,
            next_url=next_url or '',
            fallback=fallback,
            http_status=http_status,
            error_code=error_code,
            extra=response_extra,
        )

    parsed_date = None
    if raw_date:
        try:
            parsed_date = datetime.strptime(raw_date, '%Y-%m-%d').date()
        except Exception:
            return _action_response(
                ok=False,
                message='Fecha de seguimiento inválida.',
                category='warning',
                http_status=400,
                error_code='invalid_date',
            )

    old_value = getattr(s, 'fecha_seguimiento_manual', None)
    if old_value == parsed_date:
        return _action_response(
            ok=True,
            message='Seguimiento manual sin cambios.',
            category='info',
        )

    try:
        s.fecha_seguimiento_manual = parsed_date
        if hasattr(s, 'fecha_ultima_actividad'):
            s.fecha_ultima_actividad = utc_now_naive()
        if hasattr(s, 'fecha_ultima_modificacion'):
            s.fecha_ultima_modificacion = utc_now_naive()
        db.session.commit()
        _audit_log(
            action_type='SOLICITUD_SEGUIMIENTO_MANUAL_SET',
            entity_type='Solicitud',
            entity_id=s.id,
            summary=f"Seguimiento manual actualizado en solicitud {s.codigo_solicitud or s.id}",
            changes={
                "fecha_seguimiento_manual": {
                    "from": old_value.isoformat() if isinstance(old_value, date) else None,
                    "to": parsed_date.isoformat() if isinstance(parsed_date, date) else None,
                }
            },
        )
        if parsed_date is None:
            return _action_response(
                ok=True,
                message='Seguimiento manual limpiado.',
                category='success',
            )
        return _action_response(
            ok=True,
            message='Seguimiento manual guardado.',
            category='success',
        )
    except Exception:
        db.session.rollback()
        return _action_response(
            ok=False,
            message='No se pudo guardar el seguimiento manual.',
            category='danger',
            http_status=500,
            error_code='server_error',
        )


@admin_bp.route('/solicitudes/<int:id>/poner_espera_pago', methods=['POST'])
@login_required
@staff_required
def poner_espera_pago_solicitud(id):
    s = Solicitud.query.get_or_404(id)
    next_url = request.form.get('next') or request.referrer
    fallback = url_for('admin.detalle_solicitud', cliente_id=s.cliente_id, id=s.id)

    def _action_response(
        *,
        ok: bool,
        message: str,
        category: str,
        http_status: int = 200,
        error_code: str | None = None,
    ):
        response_extra = {
            "focus_row_id": int(s.id),
            "flash_row": True,
            "preserve_open_collapses": True,
        } if bool(ok) else None
        core_async_resp = _maybe_solicitud_operativa_core_async_response(
            solicitud_id=s.id,
            ok=ok,
            message=message,
            category=category,
            next_url=next_url or '',
            fallback=fallback,
            http_status=http_status,
            error_code=error_code,
        )
        if core_async_resp is not None:
            return core_async_resp
        return _solicitudes_list_action_response(
            ok=ok,
            message=message,
            category=category,
            next_url=next_url or '',
            fallback=fallback,
            http_status=http_status,
            error_code=error_code,
            extra=response_extra,
        )

    blocked_resp = _admin_block_sensitive_action(
        scope="admin_solicitud_espera_pago_poner",
        entity_type="Solicitud",
        entity_id=s.id,
        limit=40,
        window_seconds=600,
        min_interval_seconds=1,
        summary=f"Bloqueo de cambio a espera de pago por patrón de abuso ({s.id})",
        next_url=next_url or "",
        fallback=fallback,
    )
    if blocked_resp is not None:
        if _admin_async_wants_json():
            return _action_response(
                ok=False,
                message='Demasiadas acciones seguidas. Espera un momento e intenta nuevamente.',
                category='warning',
                http_status=429,
                error_code='rate_limit',
            )
        return blocked_resp

    expected_version = _expected_row_version()
    if _critical_concurrency_guards_enabled() and expected_version is not None:
        current_version = int(getattr(s, "row_version", 0) or 0)
        if int(expected_version) != current_version:
            return _action_response(
                ok=False,
                message='La solicitud cambió mientras trabajabas. Recarga y vuelve a intentar.',
                category='warning',
                http_status=409,
                error_code='conflict',
            )

    idem_row, duplicate = _claim_idempotency(
        scope="solicitud_estado_espera_pago_poner",
        entity_type="Solicitud",
        entity_id=s.id,
        action="poner_espera_pago",
    )
    if duplicate:
        if _idempotency_request_conflict(idem_row):
            return _action_response(
                ok=False,
                message=_idempotency_conflict_message(),
                category='warning',
                http_status=409,
                error_code='idempotency_conflict',
            )
        prev_status = int(getattr(idem_row, "response_status", 0) or 0)
        if 200 <= prev_status < 300:
            return _action_response(
                ok=True,
                message='Acción ya aplicada previamente.',
                category='info',
            )
        return _action_response(
            ok=False,
            message='Solicitud duplicada detectada. Espera y vuelve a intentar.',
            category='warning',
            http_status=409,
            error_code='conflict',
        )

    if s.estado == 'espera_pago':
        if _admin_noop_repeat_blocked(
            scope="admin_solicitud_espera_pago_poner",
            entity_type="Solicitud",
            entity_id=s.id,
            state="espera_pago",
            summary=f"Intento repetido de poner en espera de pago una solicitud ya en espera ({s.id})",
        ):
            return _action_response(
                ok=False,
                message='Acción bloqueada temporalmente: la solicitud ya está en espera de pago.',
                category='warning',
                http_status=429,
                error_code='rate_limit',
            )
        return _action_response(
            ok=False,
            message='La solicitud ya está en espera de pago.',
            category='info',
            http_status=409,
            error_code='conflict',
        )

    estado_actual = (s.estado or '').strip().lower()
    if estado_actual in ('cancelada',):
        return _action_response(
            ok=False,
            message='No se puede poner en espera de pago una solicitud cancelada.',
            category='warning',
            http_status=409,
            error_code='conflict',
        )

    try:
        if hasattr(s, 'estado_previo_espera_pago'):
            s.estado_previo_espera_pago = estado_actual or 'activa'
        _set_solicitud_estado_with_outbox(s, 'espera_pago')
        if hasattr(s, 'fecha_cambio_espera_pago'):
            s.fecha_cambio_espera_pago = utc_now_naive()
        if hasattr(s, 'usuario_cambio_espera_pago'):
            s.usuario_cambio_espera_pago = _staff_actor_name()
        _set_idempotency_response(idem_row, status=200, code="ok")
        db.session.commit()
        _audit_log(
            action_type="SOLICITUD_ESPERA_PAGO_PONER",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Solicitud puesta en espera de pago: {s.codigo_solicitud or s.id}",
            changes={"estado": {"from": estado_actual, "to": "espera_pago"}},
        )
        return _action_response(
            ok=True,
            message='Solicitud marcada en espera de pago.',
            category='success',
        )
    except StaleDataError:
        db.session.rollback()
        return _action_response(
            ok=False,
            message='La solicitud cambió por otra sesión. Recarga e intenta nuevamente.',
            category='warning',
            http_status=409,
            error_code='conflict',
        )
    except Exception:
        db.session.rollback()
        return _action_response(
            ok=False,
            message='No se pudo cambiar la solicitud a espera de pago.',
            category='danger',
            http_status=500,
            error_code='server_error',
        )


@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/<int:id>/espera_pago/poner', methods=['POST'])
@login_required
@staff_required
def poner_espera_pago_solicitud_cliente(cliente_id, id):
    s = Solicitud.query.filter_by(id=id, cliente_id=cliente_id).first_or_404()
    next_url = request.form.get('next') or request.referrer
    fallback = url_for('admin.detalle_cliente', cliente_id=cliente_id) + f"#sol-{s.id}"
    blocked_resp = _admin_block_sensitive_action(
        scope="admin_solicitud_espera_pago_poner",
        entity_type="Solicitud",
        entity_id=s.id,
        limit=40,
        window_seconds=600,
        min_interval_seconds=1,
        summary=f"Bloqueo de cambio a espera de pago por patrón de abuso ({s.id})",
        next_url=next_url or "",
        fallback=fallback,
    )
    if blocked_resp is not None:
        return blocked_resp

    expected_version = _expected_row_version()
    if _critical_concurrency_guards_enabled() and expected_version is not None:
        current_version = int(getattr(s, "row_version", 0) or 0)
        if int(expected_version) != current_version:
            flash('La solicitud cambió mientras trabajabas. Recarga y vuelve a intentar.', 'warning')
            return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    idem_row, duplicate = _claim_idempotency(
        scope="solicitud_estado_espera_pago_poner",
        entity_type="Solicitud",
        entity_id=s.id,
        action="poner_espera_pago",
    )
    if duplicate:
        if _idempotency_request_conflict(idem_row):
            flash(_idempotency_conflict_message(), 'warning')
            return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)
        prev_status = int(getattr(idem_row, "response_status", 0) or 0)
        if 200 <= prev_status < 300:
            flash('Acción ya aplicada previamente.', 'info')
            return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)
        flash('Solicitud duplicada detectada. Espera y vuelve a intentar.', 'warning')
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    if s.estado == 'espera_pago':
        if _admin_noop_repeat_blocked(
            scope="admin_solicitud_espera_pago_poner",
            entity_type="Solicitud",
            entity_id=s.id,
            state="espera_pago",
            summary=f"Intento repetido de poner en espera de pago una solicitud ya en espera ({s.id})",
        ):
            flash('Acción bloqueada temporalmente: la solicitud ya está en espera de pago.', 'warning')
            return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)
        flash('La solicitud ya está en espera de pago.', 'info')
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    estado_actual = (s.estado or '').strip().lower()
    if estado_actual in ('cancelada',):
        flash('No se puede poner en espera de pago una solicitud cancelada.', 'warning')
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    try:
        if hasattr(s, 'estado_previo_espera_pago'):
            s.estado_previo_espera_pago = estado_actual or 'activa'
        _set_solicitud_estado_with_outbox(s, 'espera_pago')
        if hasattr(s, 'fecha_cambio_espera_pago'):
            s.fecha_cambio_espera_pago = utc_now_naive()
        if hasattr(s, 'usuario_cambio_espera_pago'):
            s.usuario_cambio_espera_pago = _staff_actor_name()
        _set_idempotency_response(idem_row, status=200, code="ok")
        db.session.commit()
        _audit_log(
            action_type="SOLICITUD_ESPERA_PAGO_PONER",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Solicitud puesta en espera de pago: {s.codigo_solicitud or s.id}",
            changes={"estado": {"from": estado_actual, "to": "espera_pago"}},
        )
        flash('Solicitud marcada en espera de pago.', 'success')
    except StaleDataError:
        db.session.rollback()
        flash('La solicitud cambió por otra sesión. Recarga e intenta nuevamente.', 'warning')
    except Exception:
        db.session.rollback()
        flash('No se pudo cambiar la solicitud a espera de pago.', 'danger')

    return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)


@admin_bp.route('/solicitudes/<int:id>/quitar_espera_pago', methods=['POST'])
@login_required
@staff_required
def quitar_espera_pago_solicitud(id):
    s = Solicitud.query.get_or_404(id)
    next_url = request.form.get('next') or request.referrer
    fallback = url_for('admin.detalle_solicitud', cliente_id=s.cliente_id, id=s.id)

    def _action_response(
        *,
        ok: bool,
        message: str,
        category: str,
        http_status: int = 200,
        error_code: str | None = None,
    ):
        response_extra = {
            "focus_row_id": int(s.id),
            "flash_row": True,
            "preserve_open_collapses": True,
        } if bool(ok) else None
        core_async_resp = _maybe_solicitud_operativa_core_async_response(
            solicitud_id=s.id,
            ok=ok,
            message=message,
            category=category,
            next_url=next_url or '',
            fallback=fallback,
            http_status=http_status,
            error_code=error_code,
        )
        if core_async_resp is not None:
            return core_async_resp
        return _solicitudes_list_action_response(
            ok=ok,
            message=message,
            category=category,
            next_url=next_url or '',
            fallback=fallback,
            http_status=http_status,
            error_code=error_code,
            extra=response_extra,
        )

    blocked_resp = _admin_block_sensitive_action(
        scope="admin_solicitud_espera_pago_quitar",
        entity_type="Solicitud",
        entity_id=s.id,
        limit=40,
        window_seconds=600,
        min_interval_seconds=1,
        summary=f"Bloqueo de salida de espera de pago por patrón de abuso ({s.id})",
        next_url=next_url or "",
        fallback=fallback,
    )
    if blocked_resp is not None:
        if _admin_async_wants_json():
            return _action_response(
                ok=False,
                message='Demasiadas acciones seguidas. Espera un momento e intenta nuevamente.',
                category='warning',
                http_status=429,
                error_code='rate_limit',
            )
        return blocked_resp

    expected_version = _expected_row_version()
    if _critical_concurrency_guards_enabled() and expected_version is not None:
        current_version = int(getattr(s, "row_version", 0) or 0)
        if int(expected_version) != current_version:
            return _action_response(
                ok=False,
                message='La solicitud cambió mientras trabajabas. Recarga y vuelve a intentar.',
                category='warning',
                http_status=409,
                error_code='conflict',
            )

    idem_row, duplicate = _claim_idempotency(
        scope="solicitud_estado_espera_pago_quitar",
        entity_type="Solicitud",
        entity_id=s.id,
        action="quitar_espera_pago",
    )
    if duplicate:
        if _idempotency_request_conflict(idem_row):
            return _action_response(
                ok=False,
                message=_idempotency_conflict_message(),
                category='warning',
                http_status=409,
                error_code='idempotency_conflict',
            )
        prev_status = int(getattr(idem_row, "response_status", 0) or 0)
        if 200 <= prev_status < 300:
            return _action_response(
                ok=True,
                message='Acción ya aplicada previamente.',
                category='info',
            )
        return _action_response(
            ok=False,
            message='Solicitud duplicada detectada. Espera y vuelve a intentar.',
            category='warning',
            http_status=409,
            error_code='conflict',
        )

    if s.estado != 'espera_pago':
        if _admin_noop_repeat_blocked(
            scope="admin_solicitud_espera_pago_quitar",
            entity_type="Solicitud",
            entity_id=s.id,
            state=(s.estado or ""),
            summary=f"Intento repetido de quitar espera de pago fuera de flujo ({s.id})",
        ):
            return _action_response(
                ok=False,
                message='Acción bloqueada temporalmente: la solicitud no está en espera de pago.',
                category='warning',
                http_status=429,
                error_code='rate_limit',
            )
        _audit_log(
            action_type="BUSINESS_FLOW_BLOCKED",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Flujo inválido al quitar espera de pago ({s.id})",
            metadata={"rule": "invalid_state", "estado_actual": (s.estado or "").strip().lower()},
            success=False,
            error="solicitud_not_in_espera_pago",
        )
        return _action_response(
            ok=False,
            message='La solicitud no está en espera de pago.',
            category='info',
            http_status=409,
            error_code='conflict',
        )

    try:
        restore = (getattr(s, 'estado_previo_espera_pago', None) or '').strip().lower()
        if restore in ('', 'espera_pago', 'cancelada'):
            restore = 'activa'
        _set_solicitud_estado_with_outbox(s, restore)
        if hasattr(s, 'fecha_cambio_espera_pago'):
            s.fecha_cambio_espera_pago = utc_now_naive()
        if hasattr(s, 'usuario_cambio_espera_pago'):
            s.usuario_cambio_espera_pago = _staff_actor_name()
        _set_idempotency_response(idem_row, status=200, code="ok")
        db.session.commit()
        _audit_log(
            action_type="SOLICITUD_ESPERA_PAGO_QUITAR",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Solicitud reactivada desde espera de pago: {s.codigo_solicitud or s.id}",
            changes={"estado": {"from": "espera_pago", "to": restore}},
        )
        return _action_response(
            ok=True,
            message=f'Solicitud reactivada desde espera de pago a {restore}.',
            category='success',
        )
    except StaleDataError:
        db.session.rollback()
        return _action_response(
            ok=False,
            message='La solicitud cambió por otra sesión. Recarga e intenta nuevamente.',
            category='warning',
            http_status=409,
            error_code='conflict',
        )
    except Exception:
        db.session.rollback()
        return _action_response(
            ok=False,
            message='No se pudo quitar espera de pago.',
            category='danger',
            http_status=500,
            error_code='server_error',
        )


@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/<int:id>/espera_pago/quitar', methods=['POST'])
@login_required
@staff_required
def quitar_espera_pago_solicitud_cliente(cliente_id, id):
    s = Solicitud.query.filter_by(id=id, cliente_id=cliente_id).first_or_404()
    next_url = request.form.get('next') or request.referrer
    fallback = url_for('admin.detalle_cliente', cliente_id=cliente_id) + f"#sol-{s.id}"
    blocked_resp = _admin_block_sensitive_action(
        scope="admin_solicitud_espera_pago_quitar",
        entity_type="Solicitud",
        entity_id=s.id,
        limit=40,
        window_seconds=600,
        min_interval_seconds=1,
        summary=f"Bloqueo de salida de espera de pago por patrón de abuso ({s.id})",
        next_url=next_url or "",
        fallback=fallback,
    )
    if blocked_resp is not None:
        return blocked_resp

    expected_version = _expected_row_version()
    if _critical_concurrency_guards_enabled() and expected_version is not None:
        current_version = int(getattr(s, "row_version", 0) or 0)
        if int(expected_version) != current_version:
            flash('La solicitud cambió mientras trabajabas. Recarga y vuelve a intentar.', 'warning')
            return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    idem_row, duplicate = _claim_idempotency(
        scope="solicitud_estado_espera_pago_quitar",
        entity_type="Solicitud",
        entity_id=s.id,
        action="quitar_espera_pago",
    )
    if duplicate:
        if _idempotency_request_conflict(idem_row):
            flash(_idempotency_conflict_message(), 'warning')
            return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)
        prev_status = int(getattr(idem_row, "response_status", 0) or 0)
        if 200 <= prev_status < 300:
            flash('Acción ya aplicada previamente.', 'info')
            return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)
        flash('Solicitud duplicada detectada. Espera y vuelve a intentar.', 'warning')
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    if s.estado != 'espera_pago':
        if _admin_noop_repeat_blocked(
            scope="admin_solicitud_espera_pago_quitar",
            entity_type="Solicitud",
            entity_id=s.id,
            state=(s.estado or ""),
            summary=f"Intento repetido de quitar espera de pago fuera de flujo ({s.id})",
        ):
            flash('Acción bloqueada temporalmente: la solicitud no está en espera de pago.', 'warning')
            return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)
        _audit_log(
            action_type="BUSINESS_FLOW_BLOCKED",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Flujo inválido al quitar espera de pago ({s.id})",
            metadata={"rule": "invalid_state", "estado_actual": (s.estado or "").strip().lower()},
            success=False,
            error="solicitud_not_in_espera_pago",
        )
        flash('La solicitud no está en espera de pago.', 'info')
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    try:
        restore = (getattr(s, 'estado_previo_espera_pago', None) or '').strip().lower()
        if restore in ('', 'espera_pago', 'cancelada'):
            restore = 'activa'
        _set_solicitud_estado_with_outbox(s, restore)
        if hasattr(s, 'fecha_cambio_espera_pago'):
            s.fecha_cambio_espera_pago = utc_now_naive()
        if hasattr(s, 'usuario_cambio_espera_pago'):
            s.usuario_cambio_espera_pago = _staff_actor_name()
        _set_idempotency_response(idem_row, status=200, code="ok")
        db.session.commit()
        _audit_log(
            action_type="SOLICITUD_ESPERA_PAGO_QUITAR",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Solicitud reactivada desde espera de pago: {s.codigo_solicitud or s.id}",
            changes={"estado": {"from": "espera_pago", "to": restore}},
        )
        flash(f'Solicitud reactivada desde espera de pago a {restore}.', 'success')
    except StaleDataError:
        db.session.rollback()
        flash('La solicitud cambió por otra sesión. Recarga e intenta nuevamente.', 'warning')
    except Exception:
        db.session.rollback()
        flash('No se pudo quitar espera de pago.', 'danger')

    return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

# -----------------------------------------------------------------------------
# Cancelación con confirmación (GET muestra formulario, POST ejecuta)
# URL: /admin/clientes/<cliente_id>/solicitudes/<id>/cancelar
# -----------------------------------------------------------------------------
@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/<int:id>/cancelar', methods=['GET', 'POST'])
@login_required
@staff_required
def cancelar_solicitud(cliente_id, id):
    s = Solicitud.query.filter_by(id=id, cliente_id=cliente_id).first_or_404()
    wants_async = _admin_async_wants_json()
    form_idempotency_key = _new_form_idempotency_key()

    # Destino preferido de regreso
    next_url = request.args.get('next') or request.form.get('next') or request.referrer
    fallback = url_for('admin.detalle_cliente', cliente_id=cliente_id)
    safe_next = next_url if _is_safe_redirect_url(next_url) else fallback

    def _render_cancel_region(*, form_state=None, motivo_value='', async_feedback=None):
        return render_template(
            'admin/_cancelar_solicitud_form_region.html',
            solicitud=s,
            next_url=next_url,
            form_idempotency_key=form_idempotency_key,
            form=form_state,
            motivo_value=motivo_value,
            async_feedback=async_feedback,
        )

    def _async_cancel_response(
        *,
        ok: bool,
        message: str,
        category: str,
        http_status: int = 200,
        error_code: str | None = None,
        include_region: bool = False,
        form_state=None,
        motivo_value='',
        async_feedback=None,
    ):
        payload = _admin_async_payload(
            success=bool(ok),
            message=message,
            category=category,
            redirect_url=safe_next,
            replace_html=(
                _render_cancel_region(
                    form_state=form_state,
                    motivo_value=motivo_value,
                    async_feedback=async_feedback,
                )
                if include_region else None
            ),
            update_target="#cancelarSolicitudAsyncRegion",
            error_code=error_code,
        )
        return jsonify(payload), http_status

    if request.method == 'GET':
        # Idempotencia y reglas de estado
        if s.estado == 'cancelada':
            flash(f'La solicitud {s.codigo_solicitud} ya estaba cancelada.', 'warning')
            return redirect(safe_next)
        if s.estado == 'pagada':
            flash(f'La solicitud {s.codigo_solicitud} está pagada y no puede cancelarse.', 'warning')
            return redirect(safe_next)

        return render_template(
            'admin/cancelar_solicitud.html',
            solicitud=s,
            next_url=next_url,
            form_idempotency_key=form_idempotency_key,
        )

    # POST (confirma cancelación)
    motivo = (request.form.get('motivo') or '').strip()
    if len(motivo) < 5:
        if wants_async:
            msg = 'Indica un motivo de cancelación (mínimo 5 caracteres).'
            return _async_cancel_response(
                ok=False,
                message=msg,
                category='warning',
                http_status=200,
                error_code='invalid_input',
                include_region=True,
                form_state={'motivo': {'errors': ['Indica un motivo válido.']}},
                motivo_value=motivo,
                async_feedback={"message": msg, "category": "warning"},
            )
        flash('Indica un motivo de cancelación (mínimo 5 caracteres).', 'danger')
        return render_template(
            'admin/cancelar_solicitud.html',
            solicitud=s,
            next_url=next_url,
            form_idempotency_key=form_idempotency_key,
            form={'motivo': {'errors': ['Indica un motivo válido.']}}
        )

    expected_version = _expected_row_version()
    if _critical_concurrency_guards_enabled() and expected_version is not None:
        current_version = int(getattr(s, "row_version", 0) or 0)
        if int(expected_version) != current_version:
            msg = 'La solicitud cambió mientras trabajabas. Recarga y vuelve a intentar.'
            if wants_async:
                return _async_cancel_response(
                    ok=False,
                    message=msg,
                    category='warning',
                    http_status=409,
                    error_code='conflict',
                    include_region=True,
                    motivo_value=motivo,
                    async_feedback={"message": msg, "category": "warning"},
                )
            flash(msg, 'warning')
            return redirect(safe_next)

    idem_row, duplicate = _claim_idempotency(
        scope="solicitud_estado_cancelar",
        entity_type="Solicitud",
        entity_id=s.id,
        action="cancelar_solicitud",
    )
    if duplicate:
        if _idempotency_request_conflict(idem_row):
            msg = _idempotency_conflict_message()
            if wants_async:
                return _async_cancel_response(
                    ok=False,
                    message=msg,
                    category='warning',
                    http_status=409,
                    error_code='idempotency_conflict',
                    include_region=True,
                    motivo_value=motivo,
                    async_feedback={"message": msg, "category": "warning"},
                )
            flash(msg, 'warning')
            return redirect(safe_next)
        prev_status = int(getattr(idem_row, "response_status", 0) or 0)
        if 200 <= prev_status < 300:
            msg = 'Acción ya aplicada previamente.'
            if wants_async:
                return _async_cancel_response(
                    ok=True,
                    message=msg,
                    category='info',
                    http_status=200,
                )
            flash(msg, 'info')
            return redirect(safe_next)
        msg = 'Solicitud duplicada detectada. Espera y vuelve a intentar.'
        if wants_async:
            return _async_cancel_response(
                ok=False,
                message=msg,
                category='warning',
                http_status=409,
                error_code='conflict',
                include_region=True,
                motivo_value=motivo,
                async_feedback={"message": msg, "category": "warning"},
            )
        flash(msg, 'warning')
        return redirect(safe_next)

    if s.estado not in ('proceso', 'activa', 'reemplazo'):
        if wants_async:
            msg = 'Esta solicitud no se puede cancelar en su estado actual.'
            return _async_cancel_response(
                ok=False,
                message=msg,
                category='warning',
                http_status=200,
                error_code='invalid_state',
                include_region=True,
                form_state=None,
                motivo_value=motivo,
                async_feedback={"message": msg, "category": "warning"},
            )
        flash(f'No se puede cancelar la solicitud en estado «{s.estado}».', 'warning')
        return redirect(safe_next)

    try:
        estado_prev = (s.estado or "").strip().lower()
        released = _release_solicitud_candidatas_on_cancel(
            s,
            actor=_staff_actor_name(),
            motivo=motivo,
        )
        _set_solicitud_estado_with_outbox(
            s,
            'cancelada',
            payload_extra={"motivo": motivo[:255]},
        )
        s.motivo_cancelacion = motivo
        s.fecha_cancelacion = _now_utc()
        if int(released.get("released_count", 0) or 0) > 0:
            _emit_domain_outbox_event(
                event_type="SOLICITUD_CANDIDATAS_LIBERADAS",
                aggregate_type="Solicitud",
                aggregate_id=s.id,
                aggregate_version=(int(getattr(s, "row_version", 0) or 0) + 1),
                payload={
                    "solicitud_id": int(s.id),
                    "count": int(released.get("released_count", 0) or 0),
                    "candidata_ids": list(released.get("candidata_ids") or []),
                    "reason": "cancelacion_solicitud",
                },
            )
        _set_idempotency_response(idem_row, status=200, code="ok")
        db.session.commit()
        if wants_async:
            return _async_cancel_response(
                ok=True,
                message=f'Solicitud {s.codigo_solicitud} cancelada.',
                category='success',
                http_status=200,
            )
        flash(f'Solicitud {s.codigo_solicitud} cancelada.', 'success')
    except StaleDataError:
        db.session.rollback()
        if wants_async:
            return _async_cancel_response(
                ok=False,
                message='La solicitud cambió por otra sesión. Recarga e intenta nuevamente.',
                category='warning',
                http_status=409,
                error_code='conflict',
            )
        flash('La solicitud cambió por otra sesión. Recarga e intenta nuevamente.', 'warning')
    except SQLAlchemyError:
        db.session.rollback()
        if wants_async:
            return _async_cancel_response(
                ok=False,
                message='No se pudo cancelar la solicitud.',
                category='danger',
                http_status=500,
                error_code='server_error',
            )
        flash('No se pudo cancelar la solicitud.', 'danger')
    except Exception:
        db.session.rollback()
        if wants_async:
            return _async_cancel_response(
                ok=False,
                message='Ocurrió un error al cancelar la solicitud.',
                category='danger',
                http_status=500,
                error_code='server_error',
            )
        flash('Ocurrió un error al cancelar la solicitud.', 'danger')

    return redirect(safe_next)

# -----------------------------------------------------------------------------
# Cancelación directa (sin formulario)
# URL: /admin/solicitudes/<id>/cancelar_directo  (POST)
# -----------------------------------------------------------------------------
@admin_bp.route('/solicitudes/<int:id>/cancelar_directo', methods=['POST'])
@login_required
@admin_required
def cancelar_solicitud_directa(id):
    s = Solicitud.query.get_or_404(id)

    # Destino preferido de regreso
    next_url = request.args.get('next') or request.form.get('next') or request.referrer
    fallback = url_for('admin.acciones_solicitudes_proceso')

    def _action_response(
        *,
        ok: bool,
        message: str,
        category: str,
        http_status: int = 200,
        error_code: str | None = None,
    ):
        core_async_resp = _maybe_solicitud_operativa_core_async_response(
            solicitud_id=s.id,
            ok=ok,
            message=message,
            category=category,
            next_url=next_url or '',
            fallback=fallback,
            http_status=http_status,
            error_code=error_code,
        )
        if core_async_resp is not None:
            return core_async_resp
        return _solicitudes_list_action_response(
            ok=ok,
            message=message,
            category=category,
            next_url=next_url or '',
            fallback=fallback,
            http_status=http_status,
            error_code=error_code,
        )

    blocked_resp = _admin_block_sensitive_action(
        scope="admin_solicitud_cancelar",
        entity_type="Solicitud",
        entity_id=s.id,
        limit=25,
        window_seconds=600,
        min_interval_seconds=1,
        summary=f"Bloqueo de cancelación directa de solicitud por patrón de abuso ({s.id})",
        next_url=next_url or "",
        fallback=fallback,
    )
    if blocked_resp is not None:
        if _admin_async_wants_json():
            return _action_response(
                ok=False,
                message='Demasiadas acciones seguidas. Espera un momento e intenta nuevamente.',
                category='warning',
                http_status=429,
                error_code='rate_limit',
            )
        return blocked_resp

    expected_version = _expected_row_version()
    if _critical_concurrency_guards_enabled() and expected_version is not None:
        current_version = int(getattr(s, "row_version", 0) or 0)
        if int(expected_version) != current_version:
            return _action_response(
                ok=False,
                message='La solicitud cambió mientras trabajabas. Recarga y vuelve a intentar.',
                category='warning',
                http_status=409,
                error_code='conflict',
            )

    idem_row, duplicate = _claim_idempotency(
        scope="solicitud_estado_cancelar",
        entity_type="Solicitud",
        entity_id=s.id,
        action="cancelar_solicitud_directa",
    )
    if duplicate:
        if _idempotency_request_conflict(idem_row):
            return _action_response(
                ok=False,
                message=_idempotency_conflict_message(),
                category='warning',
                http_status=409,
                error_code='idempotency_conflict',
            )
        prev_status = int(getattr(idem_row, "response_status", 0) or 0)
        if 200 <= prev_status < 300:
            return _action_response(
                ok=True,
                message='Acción ya aplicada previamente.',
                category='info',
            )
        return _action_response(
            ok=False,
            message='Solicitud duplicada detectada. Espera y vuelve a intentar.',
            category='warning',
            http_status=409,
            error_code='conflict',
        )

    if s.estado == 'cancelada':
        if _admin_noop_repeat_blocked(
            scope="admin_solicitud_cancelar",
            entity_type="Solicitud",
            entity_id=s.id,
            state="cancelada",
            summary=f"Intento repetido de cancelar solicitud ya cancelada ({s.id})",
        ):
            return _action_response(
                ok=False,
                message='Acción bloqueada temporalmente: la solicitud ya estaba cancelada.',
                category='warning',
                http_status=429,
                error_code='rate_limit',
            )
        return _action_response(
            ok=False,
            message=f'La solicitud {s.codigo_solicitud} ya estaba cancelada.',
            category='warning',
            http_status=409,
            error_code='conflict',
        )

    if s.estado == 'pagada':
        if _admin_noop_repeat_blocked(
            scope="admin_solicitud_cancelar",
            entity_type="Solicitud",
            entity_id=s.id,
            state="pagada",
            summary=f"Intento repetido de cancelar solicitud pagada ({s.id})",
        ):
            return _action_response(
                ok=False,
                message='Acción bloqueada temporalmente: la solicitud está pagada y no puede cancelarse.',
                category='warning',
                http_status=429,
                error_code='rate_limit',
            )
        return _action_response(
            ok=False,
            message=f'La solicitud {s.codigo_solicitud} está pagada y no puede cancelarse.',
            category='warning',
            http_status=409,
            error_code='conflict',
        )

    if s.estado not in ('proceso', 'activa', 'reemplazo'):
        _audit_log(
            action_type="BUSINESS_FLOW_BLOCKED",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Flujo inválido al cancelar solicitud ({s.id})",
            metadata={"rule": "invalid_state", "estado_actual": (s.estado or "").strip().lower()},
            success=False,
            error="solicitud_invalid_state_for_cancel",
        )
        return _action_response(
            ok=False,
            message=f'No se puede cancelar la solicitud en estado «{s.estado}».',
            category='warning',
            http_status=409,
            error_code='conflict',
        )

    try:
        estado_prev = (s.estado or "").strip().lower()
        released = _release_solicitud_candidatas_on_cancel(
            s,
            actor=_staff_actor_name(),
            motivo=(request.form.get('motivo') or '').strip() or 'Cancelación directa (sin motivo)',
        )
        _set_solicitud_estado_with_outbox(
            s,
            'cancelada',
            payload_extra={"motivo": (request.form.get('motivo') or '').strip()[:255]},
        )
        s.fecha_cancelacion = _now_utc()
        s.motivo_cancelacion = (request.form.get('motivo') or '').strip() or 'Cancelación directa (sin motivo)'
        if int(released.get("released_count", 0) or 0) > 0:
            _emit_domain_outbox_event(
                event_type="SOLICITUD_CANDIDATAS_LIBERADAS",
                aggregate_type="Solicitud",
                aggregate_id=s.id,
                aggregate_version=(int(getattr(s, "row_version", 0) or 0) + 1),
                payload={
                    "solicitud_id": int(s.id),
                    "count": int(released.get("released_count", 0) or 0),
                    "candidata_ids": list(released.get("candidata_ids") or []),
                    "reason": "cancelacion_solicitud_directa",
                },
            )
        _set_idempotency_response(idem_row, status=200, code="ok")
        db.session.commit()
        _audit_log(
            action_type="SOLICITUD_CANCELAR_DIRECTO",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Solicitud cancelada directo: {s.codigo_solicitud or s.id}",
            changes={"estado": {"from": estado_prev, "to": "cancelada"}},
            metadata={"motivo": (s.motivo_cancelacion or "")[:255]},
        )
        return _action_response(
            ok=True,
            message=f'Solicitud {s.codigo_solicitud} cancelada.',
            category='success',
        )
    except StaleDataError:
        db.session.rollback()
        return _action_response(
            ok=False,
            message='La solicitud cambió por otra sesión. Recarga e intenta nuevamente.',
            category='warning',
            http_status=409,
            error_code='conflict',
        )
    except SQLAlchemyError:
        db.session.rollback()
        _audit_log(
            action_type="SOLICITUD_CANCELAR_DIRECTO",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Fallo cancelando solicitud directo {s.codigo_solicitud or s.id}",
            success=False,
            error="No se pudo cancelar la solicitud.",
        )
        return _action_response(
            ok=False,
            message='No se pudo cancelar la solicitud.',
            category='danger',
            http_status=500,
            error_code='server_error',
        )
    except Exception:
        db.session.rollback()
        _audit_log(
            action_type="SOLICITUD_CANCELAR_DIRECTO",
            entity_type="Solicitud",
            entity_id=s.id,
            summary=f"Fallo cancelando solicitud directo {s.codigo_solicitud or s.id}",
            success=False,
            error="Ocurrió un error al cancelar la solicitud.",
        )
        return _action_response(
            ok=False,
            message='Ocurrió un error al cancelar la solicitud.',
            category='danger',
            http_status=500,
            error_code='server_error',
        )

# ---------------------------------------
# Resumen diario por cliente (UTC)
# ---------------------------------------
@admin_bp.route('/clientes/resumen_diario')
@login_required
@admin_required
def resumen_diario_clientes():
    """
    Agrupa solo las solicitudes de HOY (UTC) por cliente.
    Evita usar func.date(...) → usamos rangos [start_utc, end_utc).
    """
    start_utc, end_utc = _utc_day_bounds()

    resumen = (
        db.session.query(
            Cliente.nombre_completo,
            Cliente.codigo,
            Cliente.telefono,
            func.count(Solicitud.id).label('total_solicitudes')
        )
        .join(Solicitud, Solicitud.cliente_id == Cliente.id)
        .filter(Solicitud.fecha_solicitud >= start_utc,
                Solicitud.fecha_solicitud < end_utc)
        .group_by(Cliente.id, Cliente.nombre_completo, Cliente.codigo, Cliente.telefono)
        .order_by(func.count(Solicitud.id).desc(), Cliente.nombre_completo.asc())
        .all()
    )

    return render_template(
        'admin/clientes_resumen_diario.html',
        resumen=resumen,
        hoy=start_utc.date()  # mostramos la fecha UTC usada
    )

# =============================================================================
#                              COMPATIBILIDAD (ADMIN)
# =============================================================================

def calc_score_compat(solicitud: Solicitud, candidata: Candidata):
    """
    DEPRECATED: conservar alias local mientras todo el sistema consume el engine único.
    """
    return format_compat_result(compute_match(solicitud, candidata))

# ---------------------------------
# VISTA: resumen HTML de compatibilidad
# ---------------------------------
@admin_bp.route('/compatibilidad/<int:cliente_id>/<int:candidata_id>')
@login_required
@admin_required
def ver_compatibilidad(cliente_id, candidata_id):
    cliente = Cliente.query.get_or_404(cliente_id)
    solicitud = (Solicitud.query
                 .filter_by(cliente_id=cliente_id)
                 .order_by(Solicitud.fecha_solicitud.desc())
                 .first())
    if not solicitud:
        flash("Este cliente aún no tiene solicitudes para calcular compatibilidad.", "warning")
        return redirect(url_for('admin.detalle_cliente', cliente_id=cliente_id))

    candidata = Candidata.query.get_or_404(candidata_id)
    res = format_compat_result(compute_match(solicitud, candidata))

    return render_template(
        'admin/compat_resumen.html',
        cliente=cliente,
        solicitud=solicitud,
        candidata=candidata,
        compat=res
    )

# ---------------------------------
# VISTA: PDF de compatibilidad (WeasyPrint)
# ---------------------------------
@admin_bp.route('/compatibilidad/<int:cliente_id>/<int:candidata_id>/pdf')
@login_required
@admin_required
def pdf_compatibilidad(cliente_id, candidata_id):
    cliente = Cliente.query.get_or_404(cliente_id)
    solicitud = (Solicitud.query
                 .filter_by(cliente_id=cliente_id)
                 .order_by(Solicitud.fecha_solicitud.desc())
                 .first())
    if not solicitud:
        flash("Este cliente aún no tiene solicitudes para PDF de compatibilidad.", "warning")
        return redirect(url_for('admin.detalle_cliente', cliente_id=cliente_id))

    candidata = Candidata.query.get_or_404(candidata_id)
    res = format_compat_result(compute_match(solicitud, candidata))

    html_str = render_template(
        'admin/compat_pdf.html',
        cliente=cliente,
        solicitud=solicitud,
        candidata=candidata,
        compat=res,
        generado_en=_now_utc()
    )

    try:
        from weasyprint import HTML
        pdf_bytes = HTML(string=html_str, base_url=request.host_url).write_pdf()
        filename = f"compat_{cliente.codigo or cliente.id}_{candidata.fila}.pdf"
        return Response(
            pdf_bytes,
            mimetype='application/pdf',
            headers={'Content-Disposition': f'inline; filename={filename}'}
        )
    except Exception:
        # Fallback/feature flag: no romper UX si WeasyPrint no está presente
        flash("WeasyPrint no está disponible. Mostrando versión HTML del reporte.", "warning")
        return html_str

@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/link-publico', methods=['GET'])
@login_required
@admin_required
def generar_link_publico_solicitud(cliente_id):
    c = Cliente.query.get_or_404(cliente_id)

    created_by = str(getattr(current_user, "username", "") or getattr(current_user, "id", "") or "")
    link = generar_link_publico_compartible_cliente(c, created_by=created_by)
    try:
        max_age_days = int((os.getenv("PUBLIC_SOLICITUD_TOKEN_MAX_AGE_DAYS") or "30").strip())
    except Exception:
        max_age_days = 30
    max_age_days = max(1, min(365, max_age_days))

    return render_template(
        'admin/cliente_link_publico_solicitud.html',
        cliente=c,
        link_publico=link,
        max_age_days=max_age_days,
    )


@admin_bp.route('/solicitudes/nueva-publica/link', methods=['GET'])
@login_required
@staff_required
def generar_link_publico_cliente_nuevo():
    created_by = str(getattr(current_user, "username", "") or getattr(current_user, "id", "") or "")
    link = generar_link_publico_compartible_cliente_nuevo(created_by=created_by)
    try:
        max_age_days = int((os.getenv("PUBLIC_SOLICITUD_NUEVA_TOKEN_MAX_AGE_DAYS") or "30").strip())
    except Exception:
        max_age_days = 30
    max_age_days = max(1, min(365, max_age_days))

    return render_template(
        'admin/cliente_nuevo_link_publico_solicitud.html',
        link_publico=link,
        max_age_days=max_age_days,
    )


_ADMIN_CHAT_MESSAGE_MAX_LEN = 1800
_ADMIN_CHAT_CONV_LIMIT = 60
_ADMIN_CHAT_MSG_LIMIT = 50
_CHAT_TYPING_TTL_SECONDS = 5
_CHAT_TYPING_EXPIRE_MIN = 2
_CHAT_TYPING_EXPIRE_MAX = 8
_CHAT_TYPING_CACHE_PREFIX = "chat:typing"
_CHAT_STATUS_OPEN = "open"
_CHAT_STATUS_PENDING = "pending"
_CHAT_STATUS_CLOSED = "closed"
_CHAT_STATUS_VALUES = {_CHAT_STATUS_OPEN, _CHAT_STATUS_PENDING, _CHAT_STATUS_CLOSED}
_CHAT_SLA_LEVEL_NORMAL = "normal"
_CHAT_SLA_LEVEL_WARNING = "warning"
_CHAT_SLA_LEVEL_OVERDUE = "overdue"
_CHAT_ASSIGNMENT_SCOPE_ALL = "all"
_CHAT_ASSIGNMENT_SCOPE_MINE = "mine"
_CHAT_ASSIGNMENT_SCOPE_UNASSIGNED = "unassigned"
_CHAT_ASSIGNMENT_SCOPE_VALUES = {
    _CHAT_ASSIGNMENT_SCOPE_ALL,
    _CHAT_ASSIGNMENT_SCOPE_MINE,
    _CHAT_ASSIGNMENT_SCOPE_UNASSIGNED,
}
_CHAT_STAFF_QUICK_REPLIES = (
    {
        "key": "saludo_inicial",
        "category": "Inicio",
        "title": "Saludo inicial",
        "body": "Hola, gracias por escribirnos. Soy parte del equipo de soporte y te estaré ayudando por este medio.",
    },
    {
        "key": "confirmacion_recibido",
        "category": "Seguimiento",
        "title": "Confirmar recibido",
        "body": "Recibimos tu mensaje correctamente. Gracias por el detalle.",
    },
    {
        "key": "pedir_mas_informacion",
        "category": "Seguimiento",
        "title": "Pedir más información",
        "body": "Para ayudarte mejor, ¿podrías compartir un poco más de contexto? Si aplica, incluye fecha, horario y cualquier detalle importante.",
    },
    {
        "key": "en_revision",
        "category": "Seguimiento",
        "title": "Estamos revisando",
        "body": "Estamos revisando tu caso con el equipo y te actualizamos por aquí en breve.",
    },
    {
        "key": "cierre_amable",
        "category": "Cierre",
        "title": "Cierre amable",
        "body": "Quedamos atentos por si necesitas algo adicional. Gracias por comunicarte con nosotros.",
    },
)


def _chat_sla_minutes_from_env(key: str, default_value: int) -> int:
    try:
        return max(1, int(str(os.getenv(key, str(default_value))).strip()))
    except Exception:
        return int(default_value)


_CHAT_SLA_WARNING_MINUTES = _chat_sla_minutes_from_env("CHAT_SLA_WARNING_MINUTES", 15)
_CHAT_SLA_OVERDUE_MINUTES = _chat_sla_minutes_from_env("CHAT_SLA_OVERDUE_MINUTES", 60)
if _CHAT_SLA_OVERDUE_MINUTES <= _CHAT_SLA_WARNING_MINUTES:
    _CHAT_SLA_OVERDUE_MINUTES = _CHAT_SLA_WARNING_MINUTES + 15


def _chat_enabled() -> bool:
    return bool(ChatConversation is not None and ChatMessage is not None)


def _chat_scope_key(*, cliente_id: int, solicitud_id: int | None) -> str:
    if chat_e2e_enabled():
        return chat_e2e_scope_key(cliente_id=int(cliente_id or 0), solicitud_id=solicitud_id)
    if int(solicitud_id or 0) > 0:
        return f"solicitud:{int(solicitud_id)}"
    return f"general:{int(cliente_id)}"


def _chat_subject_for_solicitud(solicitud: Solicitud | None) -> str:
    if not solicitud:
        return chat_e2e_subject("Soporte general")
    codigo = str(getattr(solicitud, "codigo_solicitud", "") or "").strip()
    base = f"Soporte solicitud {codigo}" if codigo else f"Soporte solicitud #{int(getattr(solicitud, 'id', 0) or 0)}"
    return chat_e2e_subject(base)


def _chat_message_preview(body: str) -> str:
    txt = re.sub(r"\s+", " ", str(body or "")).strip()
    return txt[:220]


def _chat_valid_status(raw: str | None, *, default: str = _CHAT_STATUS_OPEN, allow_all: bool = False) -> str:
    value = str(raw or "").strip().lower()
    if allow_all and value == "all":
        return "all"
    return value if value in _CHAT_STATUS_VALUES else str(default)


def _chat_valid_assignment_scope(raw: str | None, *, default: str = _CHAT_ASSIGNMENT_SCOPE_ALL) -> str:
    value = str(raw or "").strip().lower()
    return value if value in _CHAT_ASSIGNMENT_SCOPE_VALUES else str(default)


def _chat_staff_can_reassign() -> bool:
    return _current_staff_role() in {"owner", "admin"}


def _chat_typing_cache_key(conversation_id: int, actor_type: str) -> str:
    cid = max(0, int(conversation_id or 0))
    actor = str(actor_type or "").strip().lower()[:16] or "unknown"
    return f"{_CHAT_TYPING_CACHE_PREFIX}:{cid}:{actor}"


def _chat_get_typing_state(*, conversation_id: int, actor_type: str) -> dict:
    key = _chat_typing_cache_key(conversation_id, actor_type)
    raw = bp_get(key, default=None, context="chat_typing_get")
    payload = raw if isinstance(raw, dict) else {}
    if not payload:
        return {"is_typing": False, "expires_at": None, "expires_in": 0}
    is_typing = bool(payload.get("is_typing"))
    expires_at = str(payload.get("expires_at") or "").strip() or None
    expires_in = max(0, int(payload.get("expires_in") or 0))
    if not is_typing:
        return {"is_typing": False, "expires_at": None, "expires_in": 0}
    return {"is_typing": True, "expires_at": expires_at, "expires_in": expires_in}


def _chat_set_typing_state(
    *,
    conversation_id: int,
    actor_type: str,
    is_typing: bool,
    expires_seconds: int | None = None,
) -> dict:
    ttl = int(expires_seconds or _CHAT_TYPING_TTL_SECONDS)
    ttl = max(_CHAT_TYPING_EXPIRE_MIN, min(ttl, _CHAT_TYPING_EXPIRE_MAX))
    key = _chat_typing_cache_key(conversation_id, actor_type)
    if not bool(is_typing):
        bp_set(key, {"is_typing": False}, timeout=max(2, _CHAT_TYPING_EXPIRE_MIN), context="chat_typing_set")
        return {"is_typing": False, "expires_at": None, "expires_in": 0}
    expires_at_dt = utc_now_naive() + timedelta(seconds=int(ttl))
    expires_at = iso_utc_z(expires_at_dt)
    payload = {"is_typing": True, "expires_at": expires_at, "expires_in": int(ttl)}
    bp_set(key, payload, timeout=max(2, int(ttl) + 1), context="chat_typing_set")
    return payload


def _chat_cliente_presence_key(cliente_id: int) -> str:
    return f"clientes_presence:{int(cliente_id)}"


def _chat_cliente_presence_for_conversation(*, cliente_id: int, conversation_id: int) -> dict:
    now = utc_now_naive()
    out = {
        "state": "offline",
        "label": "",
        "in_this_chat": False,
        "active_elsewhere": False,
        "last_seen_at": None,
    }
    cid = int(cliente_id or 0)
    conv_id = int(conversation_id or 0)
    if cid <= 0 or conv_id <= 0:
        return out
    raw = bp_get(_chat_cliente_presence_key(cid), default=None, context="chat_cliente_presence_get")
    if not isinstance(raw, dict):
        return out
    current_path = str(raw.get("current_path") or "").strip().lower()
    if not current_path:
        return out
    last_seen_at = _parse_iso_utc(raw.get("last_seen_at"))
    if last_seen_at is None:
        return out
    delta = max(0, int((now - last_seen_at).total_seconds()))
    if delta > 20:
        return out
    out["last_seen_at"] = iso_utc_z(last_seen_at)
    active_in_client = current_path.startswith("/clientes/")
    if not active_in_client:
        return out
    payload_conv_id = _safe_int(raw.get("conversation_id"), default=0)
    in_this_chat = current_path.startswith("/clientes/chat") and payload_conv_id == conv_id
    if in_this_chat:
        out["state"] = "in_this_chat"
        out["label"] = "Cliente en este chat"
        out["in_this_chat"] = True
        return out
    out["state"] = "active_elsewhere"
    out["label"] = "Cliente activo en otra pantalla"
    out["active_elsewhere"] = True
    return out


def _chat_humanize_age_seconds(seconds: int | None) -> str:
    total = int(seconds or 0)
    if total <= 0:
        return "ahora"
    minutes = total // 60
    if minutes < 1:
        return "menos de 1 min"
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    if hours < 24:
        rem = minutes % 60
        return f"{hours} h" if rem == 0 else f"{hours} h {rem} min"
    days = hours // 24
    rem_h = hours % 24
    return f"{days} d" if rem_h == 0 else f"{days} d {rem_h} h"


def _chat_staff_quick_reply_groups() -> list[dict]:
    groups: list[dict] = []
    index_by_category: dict[str, int] = {}
    for row in _CHAT_STAFF_QUICK_REPLIES:
        category = str(row.get("category") or "General").strip() or "General"
        pos = index_by_category.get(category)
        if pos is None:
            pos = len(groups)
            index_by_category[category] = pos
            groups.append({"category": category, "items": []})
        groups[pos]["items"].append(
            {
                "key": str(row.get("key") or "").strip(),
                "title": str(row.get("title") or "").strip(),
                "body": str(row.get("body") or "").strip(),
            }
        )
    return groups


def _chat_operational_snapshot(conv, *, now_ref: datetime | None = None) -> dict:
    now_value = now_ref or utc_now_naive()
    status = _chat_valid_status(getattr(conv, "status", None), default=_CHAT_STATUS_OPEN)
    last_message_at = getattr(conv, "last_message_at", None)
    last_sender = str(getattr(conv, "last_message_sender_type", "") or "").strip().lower()
    staff_unread_count = int(getattr(conv, "staff_unread_count", 0) or 0)
    waiting_on_staff = status == _CHAT_STATUS_OPEN and (last_sender == "cliente" or staff_unread_count > 0)

    age_seconds = None
    if last_message_at:
        try:
            age_seconds = max(0, int((now_value - last_message_at).total_seconds()))
        except Exception:
            age_seconds = None

    warning_seconds = int(_CHAT_SLA_WARNING_MINUTES * 60)
    overdue_seconds = int(_CHAT_SLA_OVERDUE_MINUTES * 60)

    if status == _CHAT_STATUS_CLOSED:
        return {
            "sla_level": None,
            "sla_label": "Cerrada",
            "sla_summary": "Cerrada (fuera de foco operativo)",
            "sla_age_seconds": age_seconds,
            "sla_waiting_on_staff": False,
            "sla_priority_rank": 40,
            "sla_badge_class": "text-bg-secondary",
        }

    if status == _CHAT_STATUS_PENDING:
        return {
            "sla_level": _CHAT_SLA_LEVEL_NORMAL,
            "sla_label": "Reciente",
            "sla_summary": "Pendiente (prioridad operativa baja)",
            "sla_age_seconds": age_seconds,
            "sla_waiting_on_staff": False,
            "sla_priority_rank": 30,
            "sla_badge_class": "text-bg-light border text-muted",
        }

    if waiting_on_staff and age_seconds is not None and age_seconds >= overdue_seconds:
        return {
            "sla_level": _CHAT_SLA_LEVEL_OVERDUE,
            "sla_label": "Atrasada",
            "sla_summary": f"Atrasada: cliente esperando hace {_chat_humanize_age_seconds(age_seconds)}",
            "sla_age_seconds": age_seconds,
            "sla_waiting_on_staff": True,
            "sla_priority_rank": 0,
            "sla_badge_class": "text-bg-danger",
        }

    if waiting_on_staff and age_seconds is not None and age_seconds >= warning_seconds:
        return {
            "sla_level": _CHAT_SLA_LEVEL_WARNING,
            "sla_label": "Atención",
            "sla_summary": f"Atención: cliente esperando hace {_chat_humanize_age_seconds(age_seconds)}",
            "sla_age_seconds": age_seconds,
            "sla_waiting_on_staff": True,
            "sla_priority_rank": 10,
            "sla_badge_class": "text-bg-warning",
        }

    if waiting_on_staff and age_seconds is not None:
        summary = f"Reciente: cliente esperando hace {_chat_humanize_age_seconds(age_seconds)}"
    elif waiting_on_staff:
        summary = "Reciente: pendiente de respuesta al cliente"
    else:
        summary = "Reciente: en seguimiento"

    return {
        "sla_level": _CHAT_SLA_LEVEL_NORMAL,
        "sla_label": "Reciente",
        "sla_summary": summary,
        "sla_age_seconds": age_seconds,
        "sla_waiting_on_staff": bool(waiting_on_staff),
        "sla_priority_rank": 20,
        "sla_badge_class": "text-bg-info",
    }


def _chat_priority_order_expression(*, now_ref: datetime):
    warning_at = now_ref - timedelta(minutes=int(_CHAT_SLA_WARNING_MINUTES))
    overdue_at = now_ref - timedelta(minutes=int(_CHAT_SLA_OVERDUE_MINUTES))
    waiting_on_staff = or_(
        ChatConversation.last_message_sender_type == "cliente",
        ChatConversation.staff_unread_count > 0,
    )
    return case(
        (
            and_(
                ChatConversation.status == _CHAT_STATUS_OPEN,
                waiting_on_staff,
                ChatConversation.last_message_at.isnot(None),
                ChatConversation.last_message_at <= overdue_at,
            ),
            0,
        ),
        (
            and_(
                ChatConversation.status == _CHAT_STATUS_OPEN,
                waiting_on_staff,
                ChatConversation.last_message_at.isnot(None),
                ChatConversation.last_message_at <= warning_at,
            ),
            1,
        ),
        (ChatConversation.status == _CHAT_STATUS_OPEN, 2),
        (ChatConversation.status == _CHAT_STATUS_PENDING, 3),
        else_=4,
    )


def _chat_set_status(conversation, *, to_status: str, actor_type: str) -> bool:
    conv = conversation
    if conv is None:
        return False
    next_status = _chat_valid_status(to_status, default=_CHAT_STATUS_OPEN)
    current_status = _chat_valid_status(getattr(conv, "status", None), default=_CHAT_STATUS_OPEN)
    if current_status == next_status:
        return False
    now = utc_now_naive()
    conv.status = next_status
    conv.updated_at = now
    db.session.add(conv)
    _chat_emit_event(
        event_type="CHAT_CONVERSATION_STATUS_CHANGED",
        conversation=conv,
        reader_type=(str(actor_type or "").strip().lower()[:20] or None),
        extra_payload={"from": current_status, "to": next_status},
    )
    return True


def _chat_get_or_create_conversation(*, cliente_id: int, solicitud_id: int | None = None):
    if not _chat_enabled():
        abort(404)
    cid = int(cliente_id or 0)
    sid = int(solicitud_id or 0) or None
    if cid <= 0:
        abort(404)

    if chat_e2e_enabled():
        try:
            enforce_e2e_cliente_id(cid)
            enforce_e2e_solicitud_id(sid)
        except E2EChatGuardError as exc:
            abort(403, description=str(exc.reason))

    cliente = Cliente.query.filter_by(id=cid).first_or_404()
    solicitud = None
    if sid:
        solicitud = Solicitud.query.filter_by(id=sid, cliente_id=cid).first_or_404()

    scope_key = _chat_scope_key(cliente_id=cid, solicitud_id=sid)
    conv = ChatConversation.query.filter_by(scope_key=scope_key).first()
    if conv is not None:
        if chat_e2e_enabled():
            try:
                enforce_e2e_conversation(conv)
            except E2EChatGuardError as exc:
                abort(403, description=str(exc.reason))
        return conv

    now = utc_now_naive()
    conv = ChatConversation(
        scope_key=scope_key,
        conversation_type="solicitud" if sid else "general",
        status="open",
        cliente_id=cid,
        solicitud_id=sid,
        subject=_chat_subject_for_solicitud(solicitud),
        created_at=now,
        updated_at=now,
    )
    db.session.add(conv)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        conv = ChatConversation.query.filter_by(scope_key=scope_key).first()
        if conv is None:
            raise
    return conv


def _chat_staff_conversation_or_404(conversation_id: int):
    if not _chat_enabled():
        abort(404)
    if chat_e2e_enabled():
        try:
            enforce_e2e_conversation_id(int(conversation_id or 0))
        except E2EChatGuardError as exc:
            abort(403, description=str(exc.reason))
    conv = ChatConversation.query.filter_by(id=int(conversation_id)).first_or_404()
    if chat_e2e_enabled():
        try:
            enforce_e2e_conversation(conv)
        except E2EChatGuardError as exc:
            abort(403, description=str(exc.reason))
    return conv


def _chat_staff_conversation_for_update_or_404(conversation_id: int):
    if not _chat_enabled():
        abort(404)
    if chat_e2e_enabled():
        try:
            enforce_e2e_conversation_id(int(conversation_id or 0))
        except E2EChatGuardError as exc:
            abort(403, description=str(exc.reason))
    q = ChatConversation.query.enable_eagerloads(False).filter_by(id=int(conversation_id))
    try:
        conv = q.with_for_update(of=ChatConversation).first_or_404()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception(
            "[chat] with_for_update failed in admin thread lookup; fallback unlocked query (conversation_id=%s, error=%s: %s)",
            int(conversation_id or 0),
            type(exc).__name__,
            str(exc),
        )
        conv = q.first_or_404()
    if chat_e2e_enabled():
        try:
            enforce_e2e_conversation(conv)
        except E2EChatGuardError as exc:
            abort(403, description=str(exc.reason))
    return conv


def _chat_wants_json() -> bool:
    accept = (request.headers.get("Accept") or "").lower()
    xr = (request.headers.get("X-Requested-With") or "").lower()
    return ("application/json" in accept) or (xr == "xmlhttprequest") or (str(request.args.get("ajax") or "").strip() == "1")


def _chat_serialize_conversation_for_staff(conv, *, now_ref: datetime | None = None) -> dict:
    cliente = getattr(conv, "cliente", None)
    solicitud = getattr(conv, "solicitud", None)
    assigned_staff = getattr(conv, "assigned_staff_user", None)
    last_message_at = getattr(conv, "last_message_at", None)
    assigned_at = getattr(conv, "assigned_at", None)
    assigned_staff_user_id = int(getattr(conv, "assigned_staff_user_id", 0) or 0)
    me_id = int(getattr(current_user, "id", 0) or 0)
    is_assigned_to_me = assigned_staff_user_id > 0 and assigned_staff_user_id == me_id
    is_assigned_to_other = assigned_staff_user_id > 0 and assigned_staff_user_id != me_id
    operational = _chat_operational_snapshot(conv, now_ref=now_ref)
    cliente_presence = _chat_cliente_presence_for_conversation(
        cliente_id=int(getattr(conv, "cliente_id", 0) or 0),
        conversation_id=int(getattr(conv, "id", 0) or 0),
    )
    cliente_typing = _chat_get_typing_state(
        conversation_id=int(getattr(conv, "id", 0) or 0),
        actor_type="cliente",
    )
    return {
        "id": int(conv.id),
        "conversation_type": str(getattr(conv, "conversation_type", "") or "general"),
        "status": str(getattr(conv, "status", "") or "open"),
        "subject": str(getattr(conv, "subject", "") or "Soporte"),
        "cliente_id": int(getattr(conv, "cliente_id", 0) or 0),
        "cliente_codigo": str(getattr(cliente, "codigo", "") or ""),
        "cliente_nombre": str(getattr(cliente, "nombre_completo", "") or ""),
        "solicitud_id": int(getattr(conv, "solicitud_id", 0) or 0) or None,
        "solicitud_codigo": str(getattr(solicitud, "codigo_solicitud", "") or "") if solicitud is not None else "",
        "last_message_at": iso_utc_z(last_message_at) if last_message_at else None,
        "last_message_preview": str(getattr(conv, "last_message_preview", "") or ""),
        "last_message_sender_type": str(getattr(conv, "last_message_sender_type", "") or ""),
        "cliente_unread_count": int(getattr(conv, "cliente_unread_count", 0) or 0),
        "staff_unread_count": int(getattr(conv, "staff_unread_count", 0) or 0),
        "assigned_staff_user_id": assigned_staff_user_id or None,
        "assigned_staff_username": str(getattr(assigned_staff, "username", "") or "") if assigned_staff_user_id > 0 else "",
        "assigned_at": iso_utc_z(assigned_at) if assigned_at else None,
        "is_assigned_to_me": bool(is_assigned_to_me),
        "is_assigned_to_other": bool(is_assigned_to_other),
        "sla_level": operational.get("sla_level"),
        "sla_label": operational.get("sla_label"),
        "sla_summary": operational.get("sla_summary"),
        "sla_age_seconds": operational.get("sla_age_seconds"),
        "sla_waiting_on_staff": bool(operational.get("sla_waiting_on_staff")),
        "sla_priority_rank": int(operational.get("sla_priority_rank", 99) or 99),
        "sla_badge_class": str(operational.get("sla_badge_class", "") or ""),
        "cliente_presence_state": str(cliente_presence.get("state") or ""),
        "cliente_presence_label": str(cliente_presence.get("label") or ""),
        "cliente_in_this_chat": bool(cliente_presence.get("in_this_chat")),
        "cliente_active_elsewhere": bool(cliente_presence.get("active_elsewhere")),
        "cliente_presence_last_seen_at": cliente_presence.get("last_seen_at"),
        "cliente_typing_in_this_chat": bool(cliente_typing.get("is_typing")),
        "cliente_typing_label": "Cliente está escribiendo..." if bool(cliente_typing.get("is_typing")) else "",
        "cliente_typing_expires_at": cliente_typing.get("expires_at"),
        "cliente_typing_expires_in": int(cliente_typing.get("expires_in") or 0),
        "thread_url": url_for("admin.chat_staff_inbox", conversation_id=int(conv.id)),
    }


def _chat_serialize_message_for_staff(msg) -> dict:
    sender_type = str(getattr(msg, "sender_type", "") or "")
    sender_name = "Cliente"
    if sender_type == "staff":
        sender_name = "Tú"
    else:
        cliente_row = getattr(msg, "sender_cliente", None)
        if cliente_row is not None:
            sender_name = str(getattr(cliente_row, "nombre_completo", "") or "Cliente")
    if sender_type == "staff":
        staff_row = getattr(msg, "sender_staff_user", None)
        if staff_row is not None and not str(getattr(msg, "sender_staff_user_id", "") or "") == str(getattr(current_user, "id", "") or ""):
            sender_name = str(getattr(staff_row, "username", "") or "Staff")
    return {
        "id": int(getattr(msg, "id", 0) or 0),
        "conversation_id": int(getattr(msg, "conversation_id", 0) or 0),
        "sender_type": sender_type,
        "sender_name": sender_name,
        "body": str(getattr(msg, "body", "") or ""),
        "created_at": iso_utc_z(getattr(msg, "created_at", None)),
        "is_mine": sender_type == "staff" and int(getattr(msg, "sender_staff_user_id", 0) or 0) == int(getattr(current_user, "id", 0) or 0),
    }


def _chat_emit_event(*, event_type: str, conversation, message=None, reader_type: str | None = None, extra_payload: dict | None = None) -> None:
    if conversation is None:
        return
    payload = {
        "cliente_id": int(getattr(conversation, "cliente_id", 0) or 0),
        "solicitud_id": int(getattr(conversation, "solicitud_id", 0) or 0) or None,
        "conversation_id": int(getattr(conversation, "id", 0) or 0),
        "conversation_type": str(getattr(conversation, "conversation_type", "") or "general"),
        "status": _chat_valid_status(getattr(conversation, "status", None), default=_CHAT_STATUS_OPEN),
        "cliente_unread_count": int(getattr(conversation, "cliente_unread_count", 0) or 0),
        "staff_unread_count": int(getattr(conversation, "staff_unread_count", 0) or 0),
        "assigned_staff_user_id": int(getattr(conversation, "assigned_staff_user_id", 0) or 0) or None,
        "assigned_at": iso_utc_z(getattr(conversation, "assigned_at", None)),
    }
    if message is not None:
        message_sender_type = str(getattr(message, "sender_type", "") or "").strip().lower()
        message_sender_name = "Soporte"
        if message_sender_type == "staff":
            staff_row = getattr(message, "sender_staff_user", None)
            if staff_row is not None:
                message_sender_name = str(getattr(staff_row, "username", "") or "Soporte")
        elif message_sender_type == "cliente":
            message_sender_name = "Cliente"
        payload.update(
            {
                "message_id": int(getattr(message, "id", 0) or 0),
                "sender_type": str(getattr(message, "sender_type", "") or ""),
                "preview": _chat_message_preview(getattr(message, "body", "") or ""),
                "message": {
                    "id": int(getattr(message, "id", 0) or 0),
                    "conversation_id": int(getattr(message, "conversation_id", 0) or 0),
                    "sender_type": str(getattr(message, "sender_type", "") or ""),
                    "sender_name": message_sender_name,
                    "body": str(getattr(message, "body", "") or ""),
                    "created_at": iso_utc_z(getattr(message, "created_at", None)),
                    "is_mine": False,
                },
            }
        )
    if reader_type:
        payload["reader_type"] = str(reader_type)[:20]
    if isinstance(extra_payload, dict) and extra_payload:
        payload.update(dict(extra_payload))

    _emit_domain_outbox_event(
        event_type=str(event_type or "").strip().upper(),
        aggregate_type="ChatConversation",
        aggregate_id=int(getattr(conversation, "id", 0) or 0),
        aggregate_version=None,
        payload=payload,
    )


def _chat_staff_global_badge_payload() -> dict:
    if not _chat_enabled():
        return {"unread_conversations": 0, "unread_messages": 0}

    query = ChatConversation.query
    if chat_e2e_enabled():
        prefix = chat_e2e_scope_prefix()
        if not prefix:
            return {"unread_conversations": 0, "unread_messages": 0}
        query = query.filter(ChatConversation.scope_key.like(f"{prefix}%"))

    unread_conv_count, unread_message_count = (
        query
        .filter(ChatConversation.staff_unread_count > 0)
        .with_entities(
            func.count(ChatConversation.id),
            func.coalesce(func.sum(ChatConversation.staff_unread_count), 0),
        )
        .first()
        or (0, 0)
    )
    return {
        "unread_conversations": int(unread_conv_count or 0),
        "unread_messages": int(unread_message_count or 0),
    }


def _admin_live_boot_after_id() -> int:
    try:
        return int(
            db.session.query(db.func.max(DomainOutbox.id))
            .filter(DomainOutbox.event_type.in_(sorted(_F4_LIVE_ALLOWED_EVENT_TYPES)))
            .scalar()
            or 0
        )
    except Exception:
        return 0


@admin_bp.route('/chat', methods=['GET'])
@login_required
@staff_required
def chat_staff_inbox():
    if not _chat_enabled():
        abort(404)

    conv_id = _safe_int(request.args.get("conversation_id"), default=0)
    q = re.sub(r"\s+", " ", str(request.args.get("q") or "")).strip()[:80]
    only_unread = str(request.args.get("only_unread") or "").strip() == "1"
    status_filter = _chat_valid_status(request.args.get("status"), default=_CHAT_STATUS_OPEN, allow_all=True)
    assignment_filter = _chat_valid_assignment_scope(request.args.get("assignment"), default=_CHAT_ASSIGNMENT_SCOPE_ALL)
    live_after_id = _admin_live_boot_after_id()
    my_staff_id = int(getattr(current_user, "id", 0) or 0)
    now_ref = utc_now_naive()
    priority_order = _chat_priority_order_expression(now_ref=now_ref)

    query = ChatConversation.query
    if chat_e2e_enabled():
        prefix = chat_e2e_scope_prefix()
        if not prefix:
            abort(403, description="e2e_guard_blocked")
        query = query.filter(ChatConversation.scope_key.like(f"{prefix}%"))
    if only_unread:
        query = query.filter(ChatConversation.staff_unread_count > 0)
    if status_filter != "all":
        query = query.filter(ChatConversation.status == status_filter)
    if assignment_filter == _CHAT_ASSIGNMENT_SCOPE_MINE:
        query = query.filter(ChatConversation.assigned_staff_user_id == my_staff_id)
    elif assignment_filter == _CHAT_ASSIGNMENT_SCOPE_UNASSIGNED:
        query = query.filter(ChatConversation.assigned_staff_user_id.is_(None))
    if q:
        like = f"%{q}%"
        query = query.join(Cliente, Cliente.id == ChatConversation.cliente_id).filter(
            or_(
                Cliente.nombre_completo.ilike(like),
                Cliente.codigo.ilike(like),
                ChatConversation.subject.ilike(like),
            )
        )
    conversations = (
        query
        .order_by(
            priority_order.asc(),
            ChatConversation.staff_unread_count.desc(),
            ChatConversation.last_message_at.desc().nullslast(),
            ChatConversation.id.desc(),
        )
        .limit(_ADMIN_CHAT_CONV_LIMIT)
        .all()
    )
    for conv in conversations:
        setattr(conv, "operational", _chat_operational_snapshot(conv, now_ref=now_ref))

    selected = None
    if conv_id > 0:
        selected = _chat_staff_conversation_or_404(conv_id)
    elif conversations:
        selected = conversations[0]

    messages = []
    if selected is not None:
        setattr(selected, "operational", _chat_operational_snapshot(selected, now_ref=now_ref))
        selected_presence = _chat_cliente_presence_for_conversation(
            cliente_id=int(getattr(selected, "cliente_id", 0) or 0),
            conversation_id=int(getattr(selected, "id", 0) or 0),
        )
        setattr(selected, "cliente_presence_label", str(selected_presence.get("label") or ""))
        setattr(selected, "cliente_presence_state", str(selected_presence.get("state") or ""))
        messages = (
            ChatMessage.query
            .filter_by(conversation_id=int(selected.id), is_deleted=False)
            .order_by(ChatMessage.id.desc())
            .limit(_ADMIN_CHAT_MSG_LIMIT)
            .all()
        )
        messages = list(reversed(messages or []))

    staff_assignable = (
        StaffUser.query
        .filter_by(is_active=True)
        .order_by(StaffUser.username.asc())
        .all()
    )

    return render_template(
        "admin/chat_inbox.html",
        chat_conversations=conversations,
        chat_selected=selected,
        chat_messages=messages,
        chat_query=q,
        chat_only_unread=only_unread,
        chat_status_filter=status_filter,
        chat_assignment_filter=assignment_filter,
        chat_can_reassign=_chat_staff_can_reassign(),
        chat_assignable_staff=staff_assignable,
        chat_quick_reply_groups=_chat_staff_quick_reply_groups(),
        chat_message_max_len=_ADMIN_CHAT_MESSAGE_MAX_LEN,
        chat_live_after_id=int(live_after_id or 0),
    )


@admin_bp.route('/chat/open', methods=['POST'])
@login_required
@staff_required
def chat_staff_open():
    if not _chat_enabled():
        return jsonify({"ok": False, "error": "chat_not_available"}), 404
    payload = request.get_json(silent=True) or {}
    cliente_id = _safe_int(request.form.get("cliente_id") or payload.get("cliente_id"), default=0)
    solicitud_id = _safe_int(request.form.get("solicitud_id") or payload.get("solicitud_id"), default=0)
    if cliente_id <= 0:
        return jsonify({"ok": False, "error": "cliente_required"}), 400
    if chat_e2e_enabled():
        try:
            enforce_e2e_cliente_id(int(cliente_id))
            enforce_e2e_solicitud_id(int(solicitud_id or 0) or None)
        except E2EChatGuardError as exc:
            return jsonify({"ok": False, "error": "e2e_guard_blocked", "reason": str(exc.reason)}), 403
    conv = _chat_get_or_create_conversation(
        cliente_id=int(cliente_id),
        solicitud_id=(int(solicitud_id) if int(solicitud_id or 0) > 0 else None),
    )
    db.session.commit()
    target_url = url_for("admin.chat_staff_inbox", conversation_id=int(conv.id))
    if _chat_wants_json():
        return jsonify({"ok": True, "conversation_id": int(conv.id), "redirect_url": target_url})
    return redirect(target_url)


@admin_bp.route('/chat/conversations.json', methods=['GET'])
@login_required
@staff_required
def chat_staff_conversations_json():
    if not _chat_enabled():
        return jsonify({"ok": False, "error": "chat_not_available"}), 404
    q = re.sub(r"\s+", " ", str(request.args.get("q") or "")).strip()[:80]
    only_unread = str(request.args.get("only_unread") or "").strip() == "1"
    status_filter = _chat_valid_status(request.args.get("status"), default=_CHAT_STATUS_OPEN, allow_all=True)
    assignment_filter = _chat_valid_assignment_scope(request.args.get("assignment"), default=_CHAT_ASSIGNMENT_SCOPE_ALL)
    my_staff_id = int(getattr(current_user, "id", 0) or 0)
    now_ref = utc_now_naive()
    priority_order = _chat_priority_order_expression(now_ref=now_ref)

    query = ChatConversation.query
    if chat_e2e_enabled():
        prefix = chat_e2e_scope_prefix()
        if not prefix:
            return jsonify({"ok": False, "error": "e2e_guard_blocked", "reason": "allowlist_empty"}), 403
        query = query.filter(ChatConversation.scope_key.like(f"{prefix}%"))
    if only_unread:
        query = query.filter(ChatConversation.staff_unread_count > 0)
    if status_filter != "all":
        query = query.filter(ChatConversation.status == status_filter)
    if assignment_filter == _CHAT_ASSIGNMENT_SCOPE_MINE:
        query = query.filter(ChatConversation.assigned_staff_user_id == my_staff_id)
    elif assignment_filter == _CHAT_ASSIGNMENT_SCOPE_UNASSIGNED:
        query = query.filter(ChatConversation.assigned_staff_user_id.is_(None))
    if q:
        like = f"%{q}%"
        query = query.join(Cliente, Cliente.id == ChatConversation.cliente_id).filter(
            or_(
                Cliente.nombre_completo.ilike(like),
                Cliente.codigo.ilike(like),
                ChatConversation.subject.ilike(like),
            )
        )
    rows = (
        query
        .order_by(
            priority_order.asc(),
            ChatConversation.staff_unread_count.desc(),
            ChatConversation.last_message_at.desc().nullslast(),
            ChatConversation.id.desc(),
        )
        .limit(_ADMIN_CHAT_CONV_LIMIT)
        .all()
    )
    total_unread = int(sum(int(getattr(r, "staff_unread_count", 0) or 0) for r in (rows or [])))
    return jsonify(
        {
            "ok": True,
            "items": [_chat_serialize_conversation_for_staff(r, now_ref=now_ref) for r in (rows or [])],
            "unread_count": total_unread,
            "ts": iso_utc_z(),
        }
    )


@admin_bp.route('/chat/badge.json', methods=['GET'])
@login_required
@staff_required
def chat_staff_badge_json():
    counters = _chat_staff_global_badge_payload()
    return jsonify(
        {
            "ok": True,
            "unread_conversations": int(counters.get("unread_conversations") or 0),
            "unread_messages": int(counters.get("unread_messages") or 0),
            "ts": iso_utc_z(),
        }
    )


@admin_bp.route('/chat/conversations/<int:conversation_id>/messages.json', methods=['GET'])
@login_required
@staff_required
def chat_staff_messages_json(conversation_id):
    if not _chat_enabled():
        return jsonify({"ok": False, "error": "chat_not_available"}), 404
    conv = _chat_staff_conversation_or_404(conversation_id)
    if chat_e2e_enabled():
        try:
            enforce_e2e_conversation(conv)
        except E2EChatGuardError as exc:
            return jsonify({"ok": False, "error": "e2e_guard_blocked", "reason": str(exc.reason)}), 403
    before_id = _safe_int(request.args.get("before_id"), default=0)
    limit = max(1, min(_safe_int(request.args.get("limit"), default=_ADMIN_CHAT_MSG_LIMIT), 80))

    query = (
        ChatMessage.query
        .filter_by(conversation_id=int(conv.id), is_deleted=False)
        .order_by(ChatMessage.id.desc())
    )
    if before_id > 0:
        query = query.filter(ChatMessage.id < int(before_id))
    rows = query.limit(limit + 1).all()
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]
    rows = list(reversed(rows or []))
    next_before_id = int(rows[0].id) if rows else int(before_id or 0)

    return jsonify(
        {
            "ok": True,
            "conversation": _chat_serialize_conversation_for_staff(conv, now_ref=utc_now_naive()),
            "items": [_chat_serialize_message_for_staff(m) for m in rows],
            "has_more": bool(has_more),
            "next_before_id": int(next_before_id),
            "ts": iso_utc_z(),
        }
    )


@admin_bp.route('/chat/conversations/<int:conversation_id>/messages', methods=['POST'])
@login_required
@staff_required
def chat_staff_send_message(conversation_id):
    if not _chat_enabled():
        return jsonify({"ok": False, "error": "chat_not_available"}), 404
    conv = _chat_staff_conversation_or_404(conversation_id)
    if chat_e2e_enabled():
        try:
            enforce_e2e_conversation(conv)
        except E2EChatGuardError as exc:
            return jsonify({"ok": False, "error": "e2e_guard_blocked", "reason": str(exc.reason)}), 403

    payload = request.get_json(silent=True) or {}
    body = re.sub(r"\s+", " ", str(request.form.get("body") or payload.get("body") or "")).strip()
    if not body:
        return jsonify({"ok": False, "error": "empty_message"}), 400
    if len(body) > _ADMIN_CHAT_MESSAGE_MAX_LEN:
        return jsonify({"ok": False, "error": "message_too_long", "max_len": _ADMIN_CHAT_MESSAGE_MAX_LEN}), 400

    actor = f"staff:{int(getattr(current_user, 'id', 0) or 0)}"
    blocked, _count = enforce_business_limit(
        cache_obj=cache,
        scope="chat_staff_send",
        actor=actor,
        limit=35,
        window_seconds=60,
        reason="chat_rate_limit",
        summary="Rate limit en chat staff",
        metadata={"conversation_id": int(conv.id)},
        alert_on_block=False,
    )
    if blocked:
        return jsonify({"ok": False, "error": "rate_limited"}), 429

    blocked_fast, _elapsed = enforce_min_human_interval(
        cache_obj=cache,
        scope="chat_staff_human_interval",
        actor=actor,
        min_seconds=1,
        reason="chat_too_fast",
        summary="Patrón de chat muy rápido staff",
        metadata={"conversation_id": int(conv.id)},
    )
    if blocked_fast:
        return jsonify({"ok": False, "error": "too_fast"}), 429

    idem_row = None
    incoming_idem = _incoming_idempotency_key()
    if incoming_idem:
        idem_row, duplicate = _claim_idempotency(
            scope="chat_staff_send_message_v1",
            entity_type="chat_conversation",
            entity_id=int(conv.id),
            action="send_message",
        )
        if duplicate:
            if _idempotency_request_conflict(idem_row):
                return jsonify({"ok": False, "error": "idempotency_conflict"}), 409
            return jsonify({"ok": True, "duplicate": True}), 200

    try:
        conv = _chat_staff_conversation_for_update_or_404(int(conversation_id))
        actor_staff_id = int(getattr(current_user, "id", 0) or 0)
        assignment_changed = False
        previous_assigned_staff_id = int(getattr(conv, "assigned_staff_user_id", 0) or 0)
        prev_status = _chat_valid_status(getattr(conv, "status", None), default=_CHAT_STATUS_OPEN)
        assignment_warning = None
        if previous_assigned_staff_id <= 0 and actor_staff_id > 0:
            conv.assigned_staff_user_id = actor_staff_id
            conv.assigned_at = utc_now_naive()
            assignment_changed = True
        elif previous_assigned_staff_id > 0 and previous_assigned_staff_id != actor_staff_id:
            assigned_row = StaffUser.query.filter_by(id=previous_assigned_staff_id).first()
            assignment_warning = {
                "code": "conversation_assigned_to_other_staff",
                "assigned_staff_user_id": previous_assigned_staff_id,
                "assigned_staff_username": str(getattr(assigned_row, "username", "") or "") or f"Staff #{previous_assigned_staff_id}",
            }
        now = utc_now_naive()
        msg = ChatMessage(
            conversation_id=int(conv.id),
            sender_type="staff",
            sender_staff_user_id=actor_staff_id,
            body=body,
            meta=e2e_message_meta({}),
            created_at=now,
        )
        db.session.add(msg)

        conv.last_message_at = now
        conv.last_message_preview = _chat_message_preview(body)
        conv.last_message_sender_type = "staff"
        conv.cliente_unread_count = int(getattr(conv, "cliente_unread_count", 0) or 0) + 1
        _chat_set_status(conv, to_status=_CHAT_STATUS_OPEN, actor_type="staff")
        conv.updated_at = now
        db.session.add(conv)
        db.session.flush()
        _chat_set_typing_state(
            conversation_id=int(conv.id),
            actor_type="staff",
            is_typing=False,
        )
        if assignment_changed:
            _chat_emit_event(
                event_type="CHAT_CONVERSATION_ASSIGNED",
                conversation=conv,
                reader_type="staff",
                extra_payload={
                    "from_assigned_staff_user_id": None,
                    "to_assigned_staff_user_id": int(getattr(conv, "assigned_staff_user_id", 0) or 0) or None,
                    "action": "auto_assigned_on_staff_reply",
                },
            )
        _chat_emit_event(event_type="CHAT_MESSAGE_CREATED", conversation=conv, message=msg)
        _set_idempotency_response(idem_row, status=200, code="ok")
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        current_app.logger.exception(
            "[chat] staff send message failed (conversation_id=%s, sql_error=%s: %s)",
            int(conversation_id or 0),
            type(exc).__name__,
            str(exc),
        )
        return jsonify({"ok": False, "error": "server_error"}), 500
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception(
            "[chat] staff send message failed (conversation_id=%s, error=%s: %s)",
            int(conversation_id or 0),
            type(exc).__name__,
            str(exc),
        )
        return jsonify({"ok": False, "error": "server_error"}), 500

    _publish_fast_path_stream_event(
        event_type="CHAT_MESSAGE_CREATED",
        aggregate_type="ChatConversation",
        aggregate_id=int(getattr(conv, "id", 0) or 0),
        aggregate_version=None,
        payload={
            "cliente_id": int(getattr(conv, "cliente_id", 0) or 0),
            "solicitud_id": int(getattr(conv, "solicitud_id", 0) or 0) or None,
            "conversation_id": int(getattr(conv, "id", 0) or 0),
            "conversation_type": str(getattr(conv, "conversation_type", "") or "general"),
            "status": _chat_valid_status(getattr(conv, "status", None), default=_CHAT_STATUS_OPEN),
            "cliente_unread_count": int(getattr(conv, "cliente_unread_count", 0) or 0),
            "staff_unread_count": int(getattr(conv, "staff_unread_count", 0) or 0),
            "assigned_staff_user_id": int(getattr(conv, "assigned_staff_user_id", 0) or 0) or None,
            "message_id": int(getattr(msg, "id", 0) or 0),
            "sender_type": str(getattr(msg, "sender_type", "") or ""),
            "preview": _chat_message_preview(getattr(msg, "body", "") or ""),
            "message": {
                "id": int(getattr(msg, "id", 0) or 0),
                "conversation_id": int(getattr(msg, "conversation_id", 0) or 0),
                "sender_type": str(getattr(msg, "sender_type", "") or ""),
                "sender_name": str(getattr(current_user, "username", "") or "Soporte"),
                "body": str(getattr(msg, "body", "") or ""),
                "created_at": iso_utc_z(getattr(msg, "created_at", None)),
                "is_mine": False,
            },
        },
        actor_id=f"staff:{int(getattr(current_user, 'id', 0) or 0)}",
        region="admin",
    )
    if prev_status != _CHAT_STATUS_OPEN:
        _publish_fast_path_stream_event(
            event_type="CHAT_CONVERSATION_STATUS_CHANGED",
            aggregate_type="ChatConversation",
            aggregate_id=int(getattr(conv, "id", 0) or 0),
            aggregate_version=None,
            payload={
                "cliente_id": int(getattr(conv, "cliente_id", 0) or 0),
                "solicitud_id": int(getattr(conv, "solicitud_id", 0) or 0) or None,
                "conversation_id": int(getattr(conv, "id", 0) or 0),
                "conversation_type": str(getattr(conv, "conversation_type", "") or "general"),
                "status": _CHAT_STATUS_OPEN,
                "from": prev_status,
                "to": _CHAT_STATUS_OPEN,
                "cliente_unread_count": int(getattr(conv, "cliente_unread_count", 0) or 0),
                "staff_unread_count": int(getattr(conv, "staff_unread_count", 0) or 0),
            },
            actor_id=f"staff:{int(getattr(current_user, 'id', 0) or 0)}",
            region="admin",
        )

    return jsonify(
        {
            "ok": True,
            "conversation": _chat_serialize_conversation_for_staff(conv, now_ref=utc_now_naive()),
            "message": _chat_serialize_message_for_staff(msg),
            "assignment_warning": assignment_warning,
            "ts": iso_utc_z(),
        }
    )


@admin_bp.route('/chat/conversations/<int:conversation_id>/typing', methods=['POST'])
@login_required
@staff_required
def chat_staff_typing(conversation_id):
    if not _chat_enabled():
        return jsonify({"ok": False, "error": "chat_not_available"}), 404
    conv = _chat_staff_conversation_or_404(int(conversation_id))
    payload = request.get_json(silent=True) or {}
    raw_typing = payload.get("is_typing")
    if isinstance(raw_typing, bool):
        is_typing = raw_typing
    else:
        is_typing = str(raw_typing or "").strip().lower() in {"1", "true", "yes", "on", "typing"}
    expires_in = _safe_int(payload.get("expires_in"), default=_CHAT_TYPING_TTL_SECONDS)
    state = _chat_set_typing_state(
        conversation_id=int(conv.id),
        actor_type="staff",
        is_typing=bool(is_typing),
        expires_seconds=int(expires_in or _CHAT_TYPING_TTL_SECONDS),
    )
    try:
        _chat_emit_event(
            event_type="CHAT_CONVERSATION_TYPING",
            conversation=conv,
            reader_type="staff",
            extra_payload={
                "actor_type": "staff",
                "is_typing": bool(state.get("is_typing")),
                "typing_expires_in": int(state.get("expires_in") or 0),
                "typing_expires_at": state.get("expires_at"),
                "assigned_staff_username": str(getattr(current_user, "username", "") or ""),
            },
        )
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        current_app.logger.exception(
            "[chat] staff typing failed (conversation_id=%s, sql_error=%s: %s)",
            int(conversation_id or 0),
            type(exc).__name__,
            str(exc),
        )
        return jsonify({"ok": False, "error": "server_error"}), 500
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception(
            "[chat] staff typing failed (conversation_id=%s, error=%s: %s)",
            int(conversation_id or 0),
            type(exc).__name__,
            str(exc),
        )
        return jsonify({"ok": False, "error": "server_error"}), 500
    _publish_fast_path_stream_event(
        event_type="CHAT_CONVERSATION_TYPING",
        aggregate_type="ChatConversation",
        aggregate_id=int(conv.id),
        aggregate_version=None,
        payload={
            "cliente_id": int(getattr(conv, "cliente_id", 0) or 0),
            "solicitud_id": int(getattr(conv, "solicitud_id", 0) or 0) or None,
            "conversation_id": int(conv.id),
            "conversation_type": str(getattr(conv, "conversation_type", "") or "general"),
            "status": _chat_valid_status(getattr(conv, "status", None), default=_CHAT_STATUS_OPEN),
            "cliente_unread_count": int(getattr(conv, "cliente_unread_count", 0) or 0),
            "staff_unread_count": int(getattr(conv, "staff_unread_count", 0) or 0),
            "actor_type": "staff",
            "is_typing": bool(state.get("is_typing")),
            "typing_expires_in": int(state.get("expires_in") or 0),
            "typing_expires_at": state.get("expires_at"),
            "assigned_staff_username": str(getattr(current_user, "username", "") or ""),
        },
        actor_id=f"staff:{int(getattr(current_user, 'id', 0) or 0)}",
        region="admin",
    )
    return jsonify(
        {
            "ok": True,
            "conversation_id": int(conv.id),
            "is_typing": bool(state.get("is_typing")),
            "typing_expires_in": int(state.get("expires_in") or 0),
            "typing_expires_at": state.get("expires_at"),
            "ts": iso_utc_z(),
        }
    )


@admin_bp.route('/chat/conversations/<int:conversation_id>/read', methods=['POST'])
@login_required
@staff_required
def chat_staff_mark_read(conversation_id):
    if not _chat_enabled():
        return jsonify({"ok": False, "error": "chat_not_available"}), 404
    try:
        conv = _chat_staff_conversation_for_update_or_404(int(conversation_id))
        if chat_e2e_enabled():
            try:
                enforce_e2e_conversation(conv)
            except E2EChatGuardError as exc:
                return jsonify({"ok": False, "error": "e2e_guard_blocked", "reason": str(exc.reason)}), 403
        changed = int(getattr(conv, "staff_unread_count", 0) or 0) > 0
        now = utc_now_naive()
        conv.staff_unread_count = 0
        conv.staff_last_read_at = now
        conv.updated_at = now
        db.session.add(conv)
        if changed:
            _chat_emit_event(event_type="CHAT_CONVERSATION_READ", conversation=conv, reader_type="staff")
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        current_app.logger.exception(
            "[chat] staff mark read failed (conversation_id=%s, sql_error=%s: %s)",
            int(conversation_id or 0),
            type(exc).__name__,
            str(exc),
        )
        return jsonify({"ok": False, "error": "server_error"}), 500
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception(
            "[chat] staff mark read failed (conversation_id=%s, error=%s: %s)",
            int(conversation_id or 0),
            type(exc).__name__,
            str(exc),
        )
        return jsonify({"ok": False, "error": "server_error"}), 500
    return jsonify(
        {
            "ok": True,
            "conversation_id": int(conv.id),
            "staff_unread_count": int(getattr(conv, "staff_unread_count", 0) or 0),
            "ts": iso_utc_z(),
        }
    )


def _chat_staff_set_assignment(
    conversation_id: int,
    *,
    assigned_staff_user_id: int | None,
    action: str,
    require_admin_level: bool = False,
):
    if not _chat_enabled():
        return jsonify({"ok": False, "error": "chat_not_available"}), 404
    if require_admin_level and not _chat_staff_can_reassign():
        return jsonify({"ok": False, "error": "forbidden"}), 403

    conv = _chat_staff_conversation_for_update_or_404(int(conversation_id))
    if chat_e2e_enabled():
        try:
            enforce_e2e_conversation(conv)
        except E2EChatGuardError as exc:
            return jsonify({"ok": False, "error": "e2e_guard_blocked", "reason": str(exc.reason)}), 403

    current_assigned = int(getattr(conv, "assigned_staff_user_id", 0) or 0)
    target_assigned = int(assigned_staff_user_id or 0)
    actor_staff_id = int(getattr(current_user, "id", 0) or 0)
    if str(action or "").strip().lower() == "release" and (not _chat_staff_can_reassign()):
        if current_assigned > 0 and current_assigned != actor_staff_id:
            return jsonify({"ok": False, "error": "forbidden"}), 403

    if target_assigned > 0:
        staff_row = StaffUser.query.filter_by(id=target_assigned, is_active=True).first()
        if staff_row is None:
            return jsonify({"ok": False, "error": "assigned_staff_not_found"}), 400
    else:
        target_assigned = 0

    changed = current_assigned != target_assigned
    now = utc_now_naive()
    conv.assigned_staff_user_id = target_assigned or None
    conv.assigned_at = now if target_assigned > 0 else None
    conv.updated_at = now
    db.session.add(conv)
    if changed:
        _chat_emit_event(
            event_type="CHAT_CONVERSATION_ASSIGNED",
            conversation=conv,
            reader_type="staff",
            extra_payload={
                "from_assigned_staff_user_id": current_assigned or None,
                "to_assigned_staff_user_id": target_assigned or None,
                "action": str(action or "").strip().lower()[:40] or None,
            },
        )
    db.session.commit()
    return jsonify(
        {
            "ok": True,
            "changed": bool(changed),
            "conversation": _chat_serialize_conversation_for_staff(conv, now_ref=utc_now_naive()),
            "ts": iso_utc_z(),
        }
    )


@admin_bp.route('/chat/conversations/<int:conversation_id>/take', methods=['POST'])
@login_required
@staff_required
def chat_staff_take_conversation(conversation_id):
    return _chat_staff_set_assignment(
        int(conversation_id),
        assigned_staff_user_id=int(getattr(current_user, "id", 0) or 0),
        action="take",
    )


@admin_bp.route('/chat/conversations/<int:conversation_id>/release', methods=['POST'])
@login_required
@staff_required
def chat_staff_release_conversation(conversation_id):
    return _chat_staff_set_assignment(
        int(conversation_id),
        assigned_staff_user_id=None,
        action="release",
    )


@admin_bp.route('/chat/conversations/<int:conversation_id>/assign', methods=['POST'])
@login_required
@staff_required
def chat_staff_assign_conversation(conversation_id):
    payload = request.get_json(silent=True) or {}
    assigned_staff_user_id = _safe_int(
        request.form.get("assigned_staff_user_id") or payload.get("assigned_staff_user_id"),
        default=0,
    )
    if assigned_staff_user_id <= 0:
        return jsonify({"ok": False, "error": "assigned_staff_required"}), 400
    return _chat_staff_set_assignment(
        int(conversation_id),
        assigned_staff_user_id=int(assigned_staff_user_id),
        action="reassign",
        require_admin_level=True,
    )


def _chat_staff_change_status(conversation_id: int, *, to_status: str):
    if not _chat_enabled():
        return jsonify({"ok": False, "error": "chat_not_available"}), 404
    conv = _chat_staff_conversation_for_update_or_404(int(conversation_id))
    if chat_e2e_enabled():
        try:
            enforce_e2e_conversation(conv)
        except E2EChatGuardError as exc:
            return jsonify({"ok": False, "error": "e2e_guard_blocked", "reason": str(exc.reason)}), 403
    changed = _chat_set_status(conv, to_status=to_status, actor_type="staff")
    db.session.commit()
    return jsonify(
        {
            "ok": True,
            "changed": bool(changed),
            "conversation": _chat_serialize_conversation_for_staff(conv, now_ref=utc_now_naive()),
            "ts": iso_utc_z(),
        }
    )


@admin_bp.route('/chat/<int:conversation_id>/mark_pending', methods=['POST'])
@login_required
@staff_required
def chat_staff_mark_pending(conversation_id):
    return _chat_staff_change_status(int(conversation_id), to_status=_CHAT_STATUS_PENDING)


@admin_bp.route('/chat/<int:conversation_id>/mark_closed', methods=['POST'])
@login_required
@staff_required
def chat_staff_mark_closed(conversation_id):
    return _chat_staff_change_status(int(conversation_id), to_status=_CHAT_STATUS_CLOSED)


@admin_bp.route('/chat/<int:conversation_id>/reopen', methods=['POST'])
@login_required
@staff_required
def chat_staff_reopen(conversation_id):
    return _chat_staff_change_status(int(conversation_id), to_status=_CHAT_STATUS_OPEN)


_SEG_CASO_ESTADOS_CERRADOS = {"cerrado_exitoso", "cerrado_no_exitoso", "duplicado"}
_SEG_CASO_ESTADOS = {
    "nuevo",
    "en_gestion",
    "esperando_candidata",
    "esperando_staff",
    "programado",
    "listo_para_enviar",
    "enviado",
    "cerrado_exitoso",
    "cerrado_no_exitoso",
    "duplicado",
}
_SEG_CASO_PRIORIDADES = {"baja", "normal", "alta", "urgente"}
_SEG_CASO_CANALES = {"llamada", "whatsapp", "chat", "presencial", "referida", "otro"}
_SEG_CASO_WAITING_STATES = {"esperando_candidata", "esperando_staff"}


def _seg_tables_ready() -> bool:
    names = {
        "seguimiento_candidatas_casos",
        "seguimiento_candidatas_contactos",
        "seguimiento_candidatas_eventos",
    }
    try:
        bind = db.session.get_bind()
        insp = sa_inspect(bind)
        existing = {str(t) for t in insp.get_table_names()}
        missing = names - existing
        if not missing:
            return True
        if bool(current_app.config.get("TESTING")):
            SeguimientoCandidataContacto.__table__.create(bind=bind, checkfirst=True)
            SeguimientoCandidataCaso.__table__.create(bind=bind, checkfirst=True)
            SeguimientoCandidataEvento.__table__.create(bind=bind, checkfirst=True)
            return True
        return False
    except Exception:
        return False


def _seg_staff_user_id() -> int:
    return int(getattr(current_user, "id", 0) or 0)


def _seg_now():
    return utc_now_naive()


def _seg_normalize_phone(raw_value: str) -> str:
    raw = str(raw_value or "").strip()
    if not raw:
        return ""
    return re.sub(r"\D+", "", raw)[:32]


def _seg_public_id() -> str:
    now = _seg_now()
    token = secrets.token_hex(3).upper()
    return f"SC-{now.strftime('%Y%m%d')}-{token}"


def _seg_is_open_state(estado: str) -> bool:
    return str(estado or "").strip().lower() not in _SEG_CASO_ESTADOS_CERRADOS


def _seg_add_event(caso: SeguimientoCandidataCaso, *, event_type: str, old_value=None, new_value=None, note: str = ""):
    ev = SeguimientoCandidataEvento(
        caso_id=int(caso.id),
        event_type=str(event_type or "")[:60],
        actor_staff_user_id=_seg_staff_user_id() or None,
        old_value=old_value if isinstance(old_value, dict) else None,
        new_value=new_value if isinstance(new_value, dict) else None,
        note=(note or "").strip()[:4000] or None,
        created_at=_seg_now(),
    )
    _maybe_assign_sqlite_pk(ev, SeguimientoCandidataEvento)
    db.session.add(ev)


def _seg_emit_event(caso: SeguimientoCandidataCaso, event_type: str):
    payload = {
        "case_id": int(caso.id),
        "public_id": str(caso.public_id or ""),
        "estado": str(caso.estado or ""),
        "owner_staff_user_id": int(caso.owner_staff_user_id) if caso.owner_staff_user_id else None,
        "due_at": iso_utc_z(caso.due_at) if getattr(caso, "due_at", None) else None,
    }
    _emit_domain_outbox_event(
        event_type=event_type,
        aggregate_type="SeguimientoCandidataCaso",
        aggregate_id=int(caso.id),
        aggregate_version=int(getattr(caso, "row_version", 0) or 0) + 1,
        payload=payload,
    )


def _seg_serialize_case(caso: SeguimientoCandidataCaso) -> dict:
    return {
        "id": int(caso.id),
        "public_id": str(caso.public_id or ""),
        "candidata_id": int(caso.candidata_id) if caso.candidata_id else None,
        "solicitud_id": int(caso.solicitud_id) if caso.solicitud_id else None,
        "contacto_id": int(caso.contacto_id) if caso.contacto_id else None,
        "nombre_contacto": str(caso.nombre_contacto or ""),
        "telefono_norm": str(caso.telefono_norm or ""),
        "canal_origen": str(caso.canal_origen or ""),
        "estado": str(caso.estado or ""),
        "prioridad": str(caso.prioridad or ""),
        "owner_staff_user_id": int(caso.owner_staff_user_id) if caso.owner_staff_user_id else None,
        "owner_staff_username": str(getattr(getattr(caso, "owner_staff_user", None), "username", "") or ""),
        "proxima_accion_tipo": str(caso.proxima_accion_tipo or ""),
        "proxima_accion_detalle": str(caso.proxima_accion_detalle or ""),
        "due_at": iso_utc_z(caso.due_at) if caso.due_at else None,
        "last_movement_at": iso_utc_z(caso.last_movement_at) if caso.last_movement_at else None,
        "is_open": _seg_is_open_state(str(caso.estado or "")),
    }


@admin_bp.app_template_global("seguimiento_candidatas_badge_count")
def seguimiento_candidatas_badge_count() -> int:
    if not _seg_tables_ready():
        return 0
    if not has_request_context():
        return 0
    if not bool(getattr(current_user, "is_authenticated", False)):
        return 0
    role = str(role_for_user(current_user) or "").strip().lower()
    if role not in {"owner", "admin", "secretaria"}:
        return 0
    now = _seg_now()
    return int(
        db.session.query(func.count(SeguimientoCandidataCaso.id))
        .filter(
            SeguimientoCandidataCaso.closed_at.is_(None),
            SeguimientoCandidataCaso.is_merged.is_(False),
            SeguimientoCandidataCaso.due_at.isnot(None),
            SeguimientoCandidataCaso.due_at < now,
        )
        .scalar()
        or 0
    )


@admin_bp.route("/seguimiento-candidatas/cola", methods=["GET"])
@login_required
@staff_required
def seguimiento_candidatas_cola():
    if not _seg_tables_ready():
        abort(503)
    return render_template("admin/seguimiento_candidatas_cola.html")


@admin_bp.route("/seguimiento-candidatas/cola.json", methods=["GET"])
@login_required
@staff_required
def seguimiento_candidatas_cola_json():
    if not _seg_tables_ready():
        return jsonify({"ok": False, "error": "tracking_tables_unavailable"}), 503
    now = _seg_now()
    q = (request.args.get("q") or "").strip()[:120]
    base = SeguimientoCandidataCaso.query.filter(SeguimientoCandidataCaso.is_merged.is_(False))
    if q:
        il = f"%{q}%"
        base = base.filter(
            or_(
                SeguimientoCandidataCaso.nombre_contacto.ilike(il),
                SeguimientoCandidataCaso.telefono_norm.ilike(il),
                SeguimientoCandidataCaso.public_id.ilike(il),
            )
        )
    quick_mode = str(request.args.get("quick") or "").strip().lower() in {"1", "true", "yes", "y"}
    req_limit = _safe_int(request.args.get("limit"), default=0)
    if quick_mode:
        limit = req_limit if req_limit > 0 else 80
    else:
        limit = req_limit if req_limit > 0 else 250
    limit = max(20, min(limit, 250))

    rows = (
        base.order_by(SeguimientoCandidataCaso.last_movement_at.desc().nullslast())
        .limit(limit)
        .all()
    )
    data = [_seg_serialize_case(r) for r in rows]
    def _parse_due_naive_utc(raw_due: str | None):
        dt = parse_iso_utc(raw_due) if raw_due else None
        if dt is None:
            return None
        if getattr(dt, "tzinfo", None) is not None:
            try:
                return dt.astimezone().replace(tzinfo=None)
            except Exception:
                return dt.replace(tzinfo=None)
        return dt

    now_rd_date = to_rd(now).date()
    enriched = []
    for row in data:
        due_dt = _parse_due_naive_utc(row.get("due_at"))
        enriched.append((row, due_dt))

    buckets = {
        "vencidos": [r for (r, due_dt) in enriched if r["is_open"] and due_dt and due_dt < now],
        "hoy": [r for (r, due_dt) in enriched if r["is_open"] and due_dt and to_rd(due_dt).date() == now_rd_date],
        "sin_responsable": [r for (r, _due_dt) in enriched if r["is_open"] and not r.get("owner_staff_user_id")],
        "en_gestion": [r for (r, _due_dt) in enriched if r["is_open"] and r.get("estado") == "en_gestion"],
    }
    return jsonify({"ok": True, "items": data, "buckets": buckets, "ts": iso_utc_z(now)})


@admin_bp.route("/seguimiento-candidatas/casos", methods=["POST"])
@login_required
@staff_required
def seguimiento_candidatas_crear_caso():
    if not _seg_tables_ready():
        return jsonify({"ok": False, "error": "tracking_tables_unavailable"}), 503
    payload = request.get_json(silent=True) or {}
    telefono_norm = _seg_normalize_phone(request.form.get("telefono_norm") or payload.get("telefono_norm") or "")
    nombre_contacto = (request.form.get("nombre_contacto") or payload.get("nombre_contacto") or "").strip()[:200]
    canal_origen = str(request.form.get("canal_origen") or payload.get("canal_origen") or "otro").strip().lower()
    candidata_id = _safe_int(request.form.get("candidata_id") or payload.get("candidata_id"), default=0)
    solicitud_id = _safe_int(request.form.get("solicitud_id") or payload.get("solicitud_id"), default=0)
    proxima_accion_tipo = (request.form.get("proxima_accion_tipo") or payload.get("proxima_accion_tipo") or "").strip()[:40]
    proxima_accion_detalle = (request.form.get("proxima_accion_detalle") or payload.get("proxima_accion_detalle") or "").strip()[:300]
    prioridad = str(request.form.get("prioridad") or payload.get("prioridad") or "normal").strip().lower()
    due_at_raw = (request.form.get("due_at") or payload.get("due_at") or "").strip()
    due_at = parse_iso_utc(due_at_raw) if due_at_raw else None

    if not (candidata_id > 0 or telefono_norm):
        return jsonify({"ok": False, "error": "identity_required"}), 400
    if canal_origen not in _SEG_CASO_CANALES:
        return jsonify({"ok": False, "error": "invalid_canal_origen"}), 400
    if not proxima_accion_tipo:
        return jsonify({"ok": False, "error": "proxima_accion_required"}), 400
    if prioridad not in _SEG_CASO_PRIORIDADES:
        return jsonify({"ok": False, "error": "invalid_prioridad"}), 400

    contacto_id = None
    if telefono_norm:
        contacto = SeguimientoCandidataContacto.query.filter_by(telefono_norm=telefono_norm).first()
        if contacto is None:
            contacto = SeguimientoCandidataContacto(
                telefono_norm=telefono_norm,
                nombre_reportado=nombre_contacto or None,
                canal_preferido=canal_origen,
                created_at=_seg_now(),
                updated_at=_seg_now(),
            )
            _maybe_assign_sqlite_pk(contacto, SeguimientoCandidataContacto)
            db.session.add(contacto)
            db.session.flush()
        contacto_id = int(contacto.id)

    dup_q = SeguimientoCandidataCaso.query.filter(
        SeguimientoCandidataCaso.is_merged.is_(False),
        SeguimientoCandidataCaso.closed_at.is_(None),
    )
    dup_filters = []
    if telefono_norm:
        dup_filters.append(SeguimientoCandidataCaso.telefono_norm == telefono_norm)
    if candidata_id > 0:
        dup_filters.append(SeguimientoCandidataCaso.candidata_id == int(candidata_id))
    if dup_filters:
        dup_q = dup_q.filter(or_(*dup_filters))
    existing = dup_q.order_by(SeguimientoCandidataCaso.last_movement_at.desc().nullslast()).first()

    now = _seg_now()
    if due_at is None:
        due_at = now + timedelta(days=7)
    caso = SeguimientoCandidataCaso(
        public_id=_seg_public_id(),
        candidata_id=int(candidata_id) if candidata_id > 0 else None,
        solicitud_id=int(solicitud_id) if solicitud_id > 0 else None,
        contacto_id=int(contacto_id) if contacto_id else None,
        nombre_contacto=nombre_contacto or None,
        telefono_norm=telefono_norm or None,
        canal_origen=canal_origen,
        estado="nuevo",
        prioridad=prioridad,
        owner_staff_user_id=_seg_staff_user_id(),
        created_by_staff_user_id=_seg_staff_user_id(),
        taken_at=now,
        proxima_accion_tipo=proxima_accion_tipo or None,
        proxima_accion_detalle=proxima_accion_detalle or None,
        due_at=due_at,
        status_changed_at=now,
        last_movement_at=now,
        created_at=now,
        updated_at=now,
    )
    _maybe_assign_sqlite_pk(caso, SeguimientoCandidataCaso)
    db.session.add(caso)
    db.session.flush()
    _seg_add_event(caso, event_type="case_created", new_value=_seg_serialize_case(caso))
    _seg_emit_event(caso, "staff.case_tracking.created")
    db.session.commit()
    return jsonify(
        {
            "ok": True,
            "case": _seg_serialize_case(caso),
            "duplicate_detected": bool(existing is not None),
            "existing_case_id": int(existing.id) if existing else None,
            "overdue_count": seguimiento_candidatas_badge_count(),
            "auto_due_applied": not bool(due_at_raw),
        }
    )


@admin_bp.route("/seguimiento-candidatas/casos/<int:caso_id>", methods=["GET"])
@login_required
@staff_required
def seguimiento_candidatas_caso_detail(caso_id):
    if not _seg_tables_ready():
        abort(503)
    caso = SeguimientoCandidataCaso.query.get_or_404(int(caso_id))
    eventos = (
        SeguimientoCandidataEvento.query.filter_by(caso_id=int(caso.id))
        .order_by(SeguimientoCandidataEvento.created_at.desc())
        .limit(200)
        .all()
    )
    return render_template("admin/seguimiento_candidatas_caso_detail.html", caso=caso, eventos=eventos)


@admin_bp.route("/seguimiento-candidatas/casos/", methods=["GET"])
@login_required
@staff_required
def seguimiento_candidatas_casos_index():
    if not _seg_tables_ready():
        abort(503)
    return redirect(url_for("admin.seguimiento_candidatas_cola"))


def _seg_require_open_case(caso_id: int) -> SeguimientoCandidataCaso:
    if not _seg_tables_ready():
        abort(503)
    caso = SeguimientoCandidataCaso.query.get_or_404(int(caso_id))
    if not _seg_is_open_state(str(caso.estado or "")):
        abort(409)
    return caso


@admin_bp.route("/seguimiento-candidatas/casos/<int:caso_id>/tomar", methods=["POST"])
@login_required
@staff_required
def seguimiento_candidatas_tomar(caso_id):
    caso = _seg_require_open_case(int(caso_id))
    prev_owner = int(caso.owner_staff_user_id or 0)
    now = _seg_now()
    caso.owner_staff_user_id = _seg_staff_user_id()
    caso.taken_at = now
    caso.last_movement_at = now
    _seg_add_event(
        caso,
        event_type="case_taken",
        old_value={"owner_staff_user_id": prev_owner or None},
        new_value={"owner_staff_user_id": _seg_staff_user_id()},
        note="takeover" if prev_owner and prev_owner != _seg_staff_user_id() else "take",
    )
    if prev_owner and prev_owner != _seg_staff_user_id():
        log_admin_action(
            event="SEGUIMIENTO_CASO_TAKEOVER",
            status="ok",
            entity_type="seguimiento_candidatas_casos",
            entity_id=int(caso.id),
            summary=f"Takeover seguimiento candidata caso {caso.id}",
            metadata={"from_owner": prev_owner, "to_owner": _seg_staff_user_id()},
        )
    _seg_emit_event(caso, "staff.case_tracking.taken")
    db.session.commit()
    return jsonify({"ok": True, "case": _seg_serialize_case(caso)})


@admin_bp.route("/seguimiento-candidatas/casos/<int:caso_id>/reasignar", methods=["POST"])
@login_required
@staff_required
def seguimiento_candidatas_reasignar(caso_id):
    caso = _seg_require_open_case(int(caso_id))
    role = str(role_for_user(current_user) or "").strip().lower()
    if role not in {"owner", "admin"}:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    payload = request.get_json(silent=True) or {}
    owner_id = _safe_int(request.form.get("owner_staff_user_id") or payload.get("owner_staff_user_id"), default=0)
    if owner_id <= 0:
        return jsonify({"ok": False, "error": "owner_required"}), 400
    prev_owner = int(caso.owner_staff_user_id or 0)
    caso.owner_staff_user_id = int(owner_id)
    caso.last_movement_at = _seg_now()
    _seg_add_event(caso, event_type="case_reassigned", old_value={"owner_staff_user_id": prev_owner or None}, new_value={"owner_staff_user_id": owner_id})
    _seg_emit_event(caso, "staff.case_tracking.updated")
    db.session.commit()
    return jsonify({"ok": True, "case": _seg_serialize_case(caso)})


@admin_bp.route("/seguimiento-candidatas/casos/<int:caso_id>/estado", methods=["POST"])
@login_required
@staff_required
def seguimiento_candidatas_estado(caso_id):
    if not _seg_tables_ready():
        return jsonify({"ok": False, "error": "tracking_tables_unavailable"}), 503
    caso = SeguimientoCandidataCaso.query.get_or_404(int(caso_id))
    payload = request.get_json(silent=True) or {}
    estado = str(request.form.get("estado") or payload.get("estado") or "").strip().lower()
    if estado not in _SEG_CASO_ESTADOS:
        return jsonify({"ok": False, "error": "invalid_estado"}), 400
    if estado in _SEG_CASO_ESTADOS_CERRADOS:
        return jsonify({"ok": False, "error": "use_close_endpoint"}), 400
    prev = str(caso.estado or "")
    if _seg_is_open_state(estado):
        if not caso.owner_staff_user_id or not (caso.proxima_accion_tipo and caso.due_at):
            return jsonify({"ok": False, "error": "open_case_requires_owner_action_due"}), 400
    now = _seg_now()
    caso.estado = estado
    caso.status_changed_at = now
    caso.waiting_since_at = now if estado in _SEG_CASO_WAITING_STATES else None
    caso.last_movement_at = now
    _seg_add_event(caso, event_type="state_changed", old_value={"estado": prev}, new_value={"estado": estado})
    _seg_emit_event(caso, "staff.case_tracking.updated")
    db.session.commit()
    return jsonify({"ok": True, "case": _seg_serialize_case(caso)})


@admin_bp.route("/seguimiento-candidatas/casos/<int:caso_id>/nota", methods=["POST"])
@login_required
@staff_required
def seguimiento_candidatas_nota(caso_id):
    if not _seg_tables_ready():
        return jsonify({"ok": False, "error": "tracking_tables_unavailable"}), 503
    caso = SeguimientoCandidataCaso.query.get_or_404(int(caso_id))
    payload = request.get_json(silent=True) or {}
    note = (request.form.get("note") or payload.get("note") or "").strip()[:4000]
    if not note:
        return jsonify({"ok": False, "error": "note_required"}), 400
    caso.last_movement_at = _seg_now()
    _seg_add_event(caso, event_type="note_added", note=note)
    _seg_emit_event(caso, "staff.case_tracking.updated")
    db.session.commit()
    return jsonify({"ok": True})


@admin_bp.route("/seguimiento-candidatas/casos/<int:caso_id>/proxima-accion", methods=["POST"])
@login_required
@staff_required
def seguimiento_candidatas_proxima_accion(caso_id):
    if not _seg_tables_ready():
        return jsonify({"ok": False, "error": "tracking_tables_unavailable"}), 503
    caso = _seg_require_open_case(int(caso_id))
    payload = request.get_json(silent=True) or {}
    action_type = (request.form.get("proxima_accion_tipo") or payload.get("proxima_accion_tipo") or "").strip()[:40]
    action_detail = (request.form.get("proxima_accion_detalle") or payload.get("proxima_accion_detalle") or "").strip()[:300]
    due_at_raw = (request.form.get("due_at") or payload.get("due_at") or "").strip()
    due_at = parse_iso_utc(due_at_raw) if due_at_raw else None
    if not action_type or not due_at:
        return jsonify({"ok": False, "error": "action_and_due_required"}), 400
    caso.proxima_accion_tipo = action_type
    caso.proxima_accion_detalle = action_detail or None
    caso.due_at = due_at
    caso.last_movement_at = _seg_now()
    _seg_add_event(caso, event_type="next_action_updated", new_value={"proxima_accion_tipo": action_type, "due_at": iso_utc_z(due_at)})
    _seg_emit_event(caso, "staff.case_tracking.updated")
    db.session.commit()
    return jsonify({"ok": True, "case": _seg_serialize_case(caso)})


@admin_bp.route("/seguimiento-candidatas/casos/<int:caso_id>/cerrar", methods=["POST"])
@login_required
@staff_required
def seguimiento_candidatas_cerrar(caso_id):
    if not _seg_tables_ready():
        return jsonify({"ok": False, "error": "tracking_tables_unavailable"}), 503
    caso = SeguimientoCandidataCaso.query.get_or_404(int(caso_id))
    payload = request.get_json(silent=True) or {}
    estado = str(request.form.get("estado") or payload.get("estado") or "cerrado_no_exitoso").strip().lower()
    close_reason = (request.form.get("close_reason") or payload.get("close_reason") or "").strip()[:255]
    if estado not in _SEG_CASO_ESTADOS_CERRADOS:
        return jsonify({"ok": False, "error": "invalid_closed_state"}), 400
    if not close_reason:
        return jsonify({"ok": False, "error": "close_reason_required"}), 400
    now = _seg_now()
    prev = str(caso.estado or "")
    caso.estado = estado
    caso.closed_at = now
    caso.closed_by_staff_user_id = _seg_staff_user_id()
    caso.close_reason = close_reason
    caso.status_changed_at = now
    caso.waiting_since_at = None
    caso.last_movement_at = now
    _seg_add_event(caso, event_type="case_closed", old_value={"estado": prev}, new_value={"estado": estado}, note=close_reason)
    log_admin_action(
        event="SEGUIMIENTO_CASO_CIERRE",
        status="ok",
        entity_type="seguimiento_candidatas_casos",
        entity_id=int(caso.id),
        summary=f"Cierre seguimiento candidata caso {caso.id}",
        metadata={"close_reason": close_reason, "to_estado": estado},
    )
    _seg_emit_event(caso, "staff.case_tracking.closed")
    db.session.commit()
    return jsonify({"ok": True, "case": _seg_serialize_case(caso)})


@admin_bp.route("/seguimiento-candidatas/casos/<int:caso_id>/reabrir", methods=["POST"])
@login_required
@staff_required
def seguimiento_candidatas_reabrir(caso_id):
    if not _seg_tables_ready():
        return jsonify({"ok": False, "error": "tracking_tables_unavailable"}), 503
    caso = SeguimientoCandidataCaso.query.get_or_404(int(caso_id))
    if not caso.owner_staff_user_id or not (caso.proxima_accion_tipo and caso.due_at):
        return jsonify({"ok": False, "error": "open_case_requires_owner_action_due"}), 400
    now = _seg_now()
    caso.estado = "en_gestion"
    caso.closed_at = None
    caso.closed_by_staff_user_id = None
    caso.close_reason = None
    caso.status_changed_at = now
    caso.last_movement_at = now
    _seg_add_event(caso, event_type="case_reopened", new_value={"estado": "en_gestion"})
    _seg_emit_event(caso, "staff.case_tracking.updated")
    db.session.commit()
    return jsonify({"ok": True, "case": _seg_serialize_case(caso)})


@admin_bp.route("/seguimiento-candidatas/badge.json", methods=["GET"])
@login_required
@staff_required
def seguimiento_candidatas_badge_json():
    if not _seg_tables_ready():
        return jsonify({"ok": False, "error": "tracking_tables_unavailable"}), 503
    return jsonify({"ok": True, "overdue_count": seguimiento_candidatas_badge_count(), "ts": iso_utc_z()})
