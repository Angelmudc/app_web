# -*- coding: utf-8 -*-
from __future__ import annotations

from flask import abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user
from sqlalchemy import or_
from sqlalchemy.orm import load_only

from config_app import db
from decorators import roles_required
from utils.audit_entity import log_candidata_action
from utils.candidata_readiness import maybe_update_estado_por_completitud
from utils.robust_save import execute_robust_save, safe_bytes_length

from core import legacy_handlers as legacy_h


@roles_required("admin", "secretaria")
def finalizar_proceso_buscar():
    q = (request.args.get("q") or "").strip()[:128]
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
    return render_template("finalizar_proceso_buscar.html", q=q, resultados=resultados)


@roles_required("admin", "secretaria")
def finalizar_proceso():
    fila = request.values.get("fila", type=int)
    if not fila:
        flash("Falta el parámetro ?fila=<id>.", "warning")
        return redirect(url_for("finalizar_proceso_buscar"))

    candidata = legacy_h.Candidata.query.get(fila)
    if not candidata:
        abort(404, description=f"No existe la candidata con fila={fila}")

    grupos = legacy_h._cfg_grupos_empleo()

    if request.method == "GET":
        return render_template("finalizar_proceso.html", candidata=candidata, grupos=grupos)

    foto_perfil_file = request.files.get("foto_perfil")
    cedula1_file = request.files.get("cedula1")
    cedula2_file = request.files.get("cedula2")

    faltan = []
    if not foto_perfil_file or foto_perfil_file.filename == "":
        faltan.append("Foto de perfil")
    if not cedula1_file or cedula1_file.filename == "":
        faltan.append("Cédula (frontal)")
    if not cedula2_file or cedula2_file.filename == "":
        faltan.append("Cédula (reverso)")

    if faltan:
        flash("Faltan archivos: " + ", ".join(faltan) + ".", "danger")
        return render_template("finalizar_proceso.html", candidata=candidata, grupos=grupos)

    try:
        foto_perfil_bytes = foto_perfil_file.read()
        cedula1_bytes = cedula1_file.read()
        cedula2_bytes = cedula2_file.read()
    except Exception as e:
        flash(f"Error leyendo archivos: {e}", "danger")
        return render_template("finalizar_proceso.html", candidata=candidata, grupos=grupos)

    if (
        safe_bytes_length(foto_perfil_bytes) <= 0
        or safe_bytes_length(cedula1_bytes) <= 0
        or safe_bytes_length(cedula2_bytes) <= 0
    ):
        flash("Los archivos no pueden estar vacíos.", "danger")
        return render_template("finalizar_proceso.html", candidata=candidata, grupos=grupos)

    foto_field = "foto_perfil" if hasattr(candidata, "foto_perfil") else ("perfil" if hasattr(candidata, "perfil") else None)
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
    if foto_field:
        expected_lengths[foto_field] = safe_bytes_length(foto_perfil_bytes)
    if ok_ced1:
        expected_lengths["cedula1"] = safe_bytes_length(cedula1_bytes)
    if ok_ced2:
        expected_lengths["cedula2"] = safe_bytes_length(cedula2_bytes)

    def _persist_finalizar(_attempt: int):
        if foto_field:
            setattr(candidata, foto_field, foto_perfil_bytes)
        if ok_ced1:
            candidata.cedula1 = cedula1_bytes
        if ok_ced2:
            candidata.cedula2 = cedula2_bytes
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
    return render_template("finalizar_proceso.html", candidata=candidata, grupos=grupos)
