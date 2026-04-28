# -*- coding: utf-8 -*-
from __future__ import annotations

from flask import abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user
from sqlalchemy import or_
from sqlalchemy.orm import load_only

from config_app import db
from decorators import roles_required
from utils.audit_entity import log_candidata_action
from utils.candidata_readiness import candidata_docs_complete, candidata_has_interview, candidata_referencias_complete
from utils.candidata_readiness import maybe_update_estado_por_completitud
from utils.robust_save import execute_robust_save, safe_bytes_length

from core import legacy_handlers as legacy_h


@roles_required("admin", "secretaria")
def finalizar_proceso_buscar():
    q = (request.args.get("q") or "").strip()[:128]
    next_url = (request.args.get("next") or "").strip()
    if not legacy_h._is_safe_next(next_url):
        next_url = ""
    resultados = []
    if q:
        like = f"%{q}%"
        try:
            resultados = (
                legacy_h.Candidata.query.options(
                    load_only(
                        legacy_h.Candidata.fila,
                        legacy_h.Candidata.nombre_completo,
                        legacy_h.Candidata.cedula,
                        legacy_h.Candidata.estado,
                        legacy_h.Candidata.codigo,
                    )
                )
                .filter(
                    or_(
                        legacy_h.Candidata.nombre_completo.ilike(like),
                        legacy_h.Candidata.cedula.ilike(like),
                        legacy_h.Candidata.codigo.ilike(like),
                    )
                )
                .order_by(legacy_h.Candidata.nombre_completo.asc())
                .limit(300)
                .all()
            )
        except Exception:
            current_app.logger.exception("❌ Error buscando en finalizar_proceso_buscar")
            resultados = []
    return render_template("finalizar_proceso_buscar.html", q=q, resultados=resultados, next_url=next_url)


@roles_required("admin", "secretaria")
def finalizar_proceso():
    fila = request.values.get("fila", type=int)
    next_url = (request.values.get("next") or "").strip()
    if not legacy_h._is_safe_next(next_url):
        next_url = ""
    current_return_url = (request.full_path or request.path or "").strip()
    if current_return_url.endswith("?"):
        current_return_url = current_return_url[:-1]

    queue_raw = (request.values.get("queue") or "").strip()
    queue_ids = []
    if queue_raw:
        seen = set()
        for token in queue_raw.split(","):
            token = token.strip()
            if not token.isdigit():
                continue
            value = int(token)
            if value <= 0 or value in seen:
                continue
            seen.add(value)
            queue_ids.append(value)
            if len(queue_ids) >= 300:
                break
    queue_value = ",".join(str(x) for x in queue_ids)
    prev_fila = None
    next_fila = None
    if fila and queue_ids and fila in queue_ids:
        idx = queue_ids.index(int(fila))
        if idx > 0:
            prev_fila = int(queue_ids[idx - 1])
        if idx < len(queue_ids) - 1:
            next_fila = int(queue_ids[idx + 1])
    elif fila:
        try:
            pending_states = ("en_proceso", "proceso_inscripcion", "inscrita", "inscrita_incompleta")
            prev_obj = (
                legacy_h.Candidata.query
                .filter(legacy_h.Candidata.fila < int(fila), legacy_h.Candidata.estado.in_(pending_states))
                .order_by(legacy_h.Candidata.fila.desc())
                .first()
            )
            next_obj = (
                legacy_h.Candidata.query
                .filter(legacy_h.Candidata.fila > int(fila), legacy_h.Candidata.estado.in_(pending_states))
                .order_by(legacy_h.Candidata.fila.asc())
                .first()
            )
            prev_fila = int(getattr(prev_obj, "fila", 0) or 0) or None
            next_fila = int(getattr(next_obj, "fila", 0) or 0) or None
        except Exception:
            prev_fila = None
            next_fila = None

    if not fila:
        flash("Falta el parámetro ?fila=<id>.", "warning")
        return redirect(url_for("finalizar_proceso_buscar"))

    candidata = legacy_h.Candidata.query.get(fila)
    if not candidata:
        abort(404, description=f"No existe la candidata con fila={fila}")

    grupos = legacy_h._cfg_grupos_empleo()
    docs = candidata_docs_complete(candidata)
    referencias = candidata_referencias_complete(candidata)
    checklist = {
        "entrevista": bool(candidata_has_interview(candidata)),
        "referencias": bool(referencias.get("referencias_laboral")) and bool(referencias.get("referencias_familiares")),
        "depuracion": bool((docs.get("flags") or {}).get("depuracion")),
        "perfil": bool((docs.get("flags") or {}).get("perfil")),
        "cedulas": bool((docs.get("flags") or {}).get("cedula1")) and bool((docs.get("flags") or {}).get("cedula2")),
    }

    if request.method == "GET":
        return render_template(
            "finalizar_proceso.html",
            candidata=candidata,
            grupos=grupos,
            next_url=next_url,
            current_return_url=current_return_url,
            queue_value=queue_value,
            prev_fila=prev_fila,
            next_fila=next_fila,
            checklist=checklist,
        )

    foto_perfil_file = request.files.get("foto_perfil")
    cedula1_file = request.files.get("cedula1")
    cedula2_file = request.files.get("cedula2")

    foto_field = "foto_perfil" if hasattr(candidata, "foto_perfil") else ("perfil" if hasattr(candidata, "perfil") else None)
    has_foto_existing = bool(getattr(candidata, "foto_perfil", None) or getattr(candidata, "perfil", None))
    has_ced1_existing = bool(getattr(candidata, "cedula1", None))
    has_ced2_existing = bool(getattr(candidata, "cedula2", None))

    faltan = []
    if not has_foto_existing and (not foto_perfil_file or foto_perfil_file.filename == ""):
        faltan.append("Foto de perfil")
    if not has_ced1_existing and (not cedula1_file or cedula1_file.filename == ""):
        faltan.append("Cédula (frontal)")
    if not has_ced2_existing and (not cedula2_file or cedula2_file.filename == ""):
        faltan.append("Cédula (reverso)")

    if faltan:
        flash("Faltan archivos: " + ", ".join(faltan) + ".", "danger")
        return render_template(
            "finalizar_proceso.html",
            candidata=candidata,
            grupos=grupos,
            next_url=next_url,
            current_return_url=current_return_url,
            queue_value=queue_value,
            prev_fila=prev_fila,
            next_fila=next_fila,
            checklist=checklist,
        )

    payload_bytes = {}
    try:
        if foto_perfil_file and foto_perfil_file.filename:
            payload_bytes["foto"] = foto_perfil_file.read()
        if cedula1_file and cedula1_file.filename:
            payload_bytes["cedula1"] = cedula1_file.read()
        if cedula2_file and cedula2_file.filename:
            payload_bytes["cedula2"] = cedula2_file.read()
    except Exception as e:
        flash(f"Error leyendo archivos: {e}", "danger")
        return render_template(
            "finalizar_proceso.html",
            candidata=candidata,
            grupos=grupos,
            next_url=next_url,
            current_return_url=current_return_url,
            queue_value=queue_value,
            prev_fila=prev_fila,
            next_fila=next_fila,
            checklist=checklist,
        )

    if any(safe_bytes_length(v) <= 0 for v in payload_bytes.values()):
        flash("Los archivos no pueden estar vacíos.", "danger")
        return render_template(
            "finalizar_proceso.html",
            candidata=candidata,
            grupos=grupos,
            next_url=next_url,
            current_return_url=current_return_url,
            queue_value=queue_value,
            prev_fila=prev_fila,
            next_fila=next_fila,
            checklist=checklist,
        )

    ok_foto = foto_field is not None
    ok_ced1 = hasattr(candidata, "cedula1")
    ok_ced2 = hasattr(candidata, "cedula2")

    if not (ok_foto and ok_ced1 and ok_ced2):
        detalles = []
        if not ok_foto:
            detalles.append("foto_perfil (o perfil) no existe en el modelo")
        if not ok_ced1:
            detalles.append("cedula1 no existe en el modelo")
        if not ok_ced2:
            detalles.append("cedula2 no existe en el modelo")
        flash("No se pudieron guardar algunos campos binarios: " + "; ".join(detalles), "warning")

    grupos_sel = request.form.getlist("grupos_empleo")
    if grupos_sel:
        if not legacy_h._save_grupos_empleo_safe(candidata, grupos_sel):
            flash("No se encontró columna para guardar los grupos (grupos_empleo / grupos / grupos_empleo_json).", "warning")

    try:
        actor = (
            getattr(current_user, "username", None)
            or getattr(current_user, "id", None)
            or session.get("usuario")
            or "sistema"
        )
        actor = str(actor)
    except Exception:
        actor = "sistema"

    expected_lengths = {}
    if foto_field and "foto" in payload_bytes:
        expected_lengths[foto_field] = safe_bytes_length(payload_bytes["foto"])
    if ok_ced1 and "cedula1" in payload_bytes:
        expected_lengths["cedula1"] = safe_bytes_length(payload_bytes["cedula1"])
    if ok_ced2 and "cedula2" in payload_bytes:
        expected_lengths["cedula2"] = safe_bytes_length(payload_bytes["cedula2"])

    def _persist_finalizar(_attempt: int):
        if foto_field and "foto" in payload_bytes:
            setattr(candidata, foto_field, payload_bytes["foto"])
        if ok_ced1 and "cedula1" in payload_bytes:
            candidata.cedula1 = payload_bytes["cedula1"]
        if ok_ced2 and "cedula2" in payload_bytes:
            candidata.cedula2 = payload_bytes["cedula2"]
        if grupos_sel:
            legacy_h._save_grupos_empleo_safe(candidata, grupos_sel)
        try:
            maybe_update_estado_por_completitud(candidata, actor=actor)
        except Exception:
            pass

    result = execute_robust_save(
        session=db.session,
        persist_fn=_persist_finalizar,
        verify_fn=lambda: legacy_h._verify_candidata_docs_saved(int(candidata.fila), expected_lengths),
    )

    if result.ok:
        log_candidata_action(
            action_type="CANDIDATA_UPLOAD_DOCS",
            candidata=candidata,
            summary="Finalización de proceso con carga de documentos",
            metadata={
                "fields": sorted(list(expected_lengths.keys())),
                "source": "finalizar_proceso",
                "attempt_count": int(result.attempts),
            },
            success=True,
        )
        flash("✅ Proceso finalizado y datos guardados correctamente.", "success")
        if next_url:
            return redirect(next_url)
        return redirect(url_for("candidata_ver_perfil", fila=candidata.fila))

    db.session.rollback()
    log_candidata_action(
        action_type="CANDIDATA_UPLOAD_DOCS",
        candidata=candidata,
        summary="Fallo finalizando proceso con documentos",
        metadata={"source": "finalizar_proceso", "attempt_count": int(result.attempts)},
        success=False,
        error=result.error_message,
    )
    flash("❌ Error guardando en la base de datos: no se pudo verificar la persistencia.", "danger")
    return render_template(
        "finalizar_proceso.html",
        candidata=candidata,
        grupos=grupos,
        next_url=next_url,
        current_return_url=current_return_url,
        queue_value=queue_value,
        prev_fila=prev_fila,
        next_fila=next_fila,
        checklist=checklist,
    )
