# -*- coding: utf-8 -*-
from __future__ import annotations

from decimal import Decimal

from flask import flash, redirect, render_template, request, session, url_for
from flask_login import current_user

from config_app import db
from decorators import roles_required
from core.services.candidatas_shared import get_candidata_by_id
from core.services.date_utils import parse_date
from core.services.candidata_finance import configure_candidate_finance, register_candidate_payment
from core.services.candidata_quick_edit import update_candidate_inscription
from core.services.search import search_candidatas_limited
from utils.candidata_readiness import maybe_update_estado_por_completitud
from utils.timezone import utc_now_naive
from services.candidata_invariants import InvariantConflictError, change_candidate_state as invariant_change_candidate_state
from services.candidata_assignment_guard import validate_candidata_assignment_context

from core import legacy_handlers as legacy_h


@roles_required("admin", "secretaria")
def inscripcion():
    mensaje = ""
    resultados = []
    candidata = None

    if request.method == "POST":
        if request.form.get("guardar_inscripcion"):
            cid = (request.form.get("candidata_id") or "").strip()
            if not cid.isdigit():
                flash("❌ ID inválido.", "error")
                return redirect(url_for("inscripcion"))

            obj = get_candidata_by_id(cid)
            if not obj:
                flash("⚠️ Candidata no encontrada.", "error")
                return redirect(url_for("inscripcion"))

            actor = (
                getattr(current_user, "username", None)
                or getattr(current_user, "id", None)
                or session.get("usuario")
                or "sistema"
            )
            result = update_candidate_inscription(
                obj,
                dict(request.form or {}),
                actor=str(actor),
                code_generator=legacy_h.generar_codigo_unico,
                now_fn=utc_now_naive,
                readiness_updater=maybe_update_estado_por_completitud,
            )
            if result.ok:
                flash(f"✅ Inscripción guardada. Código: {obj.codigo}", "success")
                candidata = obj
            else:
                if result.error_code == "code_generation_error":
                    legacy_h.app.logger.error("❌ Error generando código único")
                    flash("❌ No se pudo generar el código.", "error")
                else:
                    legacy_h.app.logger.error("❌ Error al guardar inscripción: %s", result.error_code or result.message)
                    flash("❌ Error al guardar inscripción.", "error")
                return redirect(url_for("inscripcion"))
        else:
            q = (request.form.get("buscar") or "").strip()[:128]
            if q:
                try:
                    resultados = search_candidatas_limited(q, limit=300, minimal_fields=True)
                    if not resultados:
                        flash("⚠️ No se encontraron coincidencias.", "error")
                except Exception:
                    legacy_h.app.logger.exception("❌ Error buscando en inscripción")
                    flash("❌ Error al buscar.", "error")

    else:
        q = (request.args.get("buscar") or "").strip()[:128]
        if q:
            try:
                resultados = search_candidatas_limited(q, limit=300, minimal_fields=True)
                if not resultados:
                    mensaje = "⚠️ No se encontraron coincidencias."
            except Exception:
                legacy_h.app.logger.exception("❌ Error buscando candidatas (GET) en inscripción")
                mensaje = "❌ Error al buscar."

        sel = (request.args.get("candidata_seleccionada") or "").strip()
        if not resultados and sel.isdigit():
            candidata = get_candidata_by_id(sel)
            if not candidata:
                mensaje = "⚠️ Candidata no encontrada."

    return render_template(
        "inscripcion.html",
        resultados=resultados,
        candidata=candidata,
        mensaje=mensaje,
    )


@roles_required("owner", "admin")
def porciento():
    resultados, candidata = [], None
    assignment_guard = None

    if request.method == "POST":
        fila_id = (request.form.get("fila_id") or "").strip()
        if not fila_id.isdigit():
            flash("❌ Fila inválida.", "danger")
            return redirect(url_for("porciento"))

        obj = get_candidata_by_id(fila_id)
        if not obj:
            flash("⚠️ Candidata no encontrada.", "warning")
            return redirect(url_for("porciento"))

        actor = (
            getattr(current_user, "username", None)
            or getattr(current_user, "id", None)
            or session.get("usuario")
            or "sistema"
        )
        result = configure_candidate_finance(obj, dict(request.form or {}), actor=str(actor), now_fn=utc_now_naive)
        if not result.ok:
            legacy_h.app.logger.error("❌ Error al actualizar porciento: %s", result.error_code or result.message)
            flash(f"❌ {result.message}", "danger")
            return redirect(url_for("porciento", candidata=fila_id))
        assignment_guard = validate_candidata_assignment_context(candidata_id=int(obj.fila))
        marked_working = False
        try:
            if assignment_guard.can_mark_working:
                invariant_change_candidate_state(
                    candidata_id=int(obj.fila),
                    new_state="trabajando",
                    actor=str(session.get("usuario", "desconocido") or "desconocido"),
                    reason="legacy_porciento",
                    candidata_obj=obj,
                )
                marked_working = True
        except InvariantConflictError as inv_exc:
            flash(f"⚠️ No se pudo marcar trabajando: {str(inv_exc)}", "warning")

        try:
            db.session.commit()
            if marked_working:
                flash(
                    f"✅ Se guardó correctamente. 25 % de {obj.monto_total} es {obj.porciento}. Estado: Trabajando.",
                    "success",
                )
            else:
                detail = assignment_guard.reason_message if assignment_guard else "No hay contexto operativo válido."
                flash(
                    f"✅ Se guardó el 25 % ({obj.porciento}) sin forzar estado trabajando. Motivo: {detail}",
                    "warning",
                )
            candidata = obj
        except Exception:
            db.session.rollback()
            legacy_h.app.logger.exception("❌ Error al actualizar porciento")
            flash("❌ Error al actualizar.", "danger")
            return redirect(url_for("porciento", candidata=fila_id))

    else:
        q = (request.args.get("busqueda") or "").strip()[:128]
        if q:
            try:
                resultados = search_candidatas_limited(q, limit=300, minimal_fields=True)
                if not resultados:
                    flash("⚠️ No se encontraron coincidencias.", "warning")
            except Exception:
                legacy_h.app.logger.exception("❌ Error buscando (GET) en porciento")
                flash("❌ Error al buscar.", "warning")

        sel = (request.args.get("candidata") or "").strip()
        if sel.isdigit() and not resultados:
            candidata = get_candidata_by_id(sel)
            if not candidata:
                flash("⚠️ Candidata no encontrada.", "warning")

    if candidata:
        assignment_guard = assignment_guard or validate_candidata_assignment_context(candidata_id=int(candidata.fila))
    return render_template("porciento.html", resultados=resultados, candidata=candidata, assignment_guard=assignment_guard)


@roles_required("owner", "admin")
def pagos():
    resultados, candidata = [], None
    payment_block_ui = None

    def _build_payment_block_ui(guard_result):
        if not guard_result or guard_result.can_charge:
            return None

        reason_code = str(getattr(guard_result, "reason_code", "") or "").strip()
        reason_message = str(getattr(guard_result, "reason_message", "") or "").strip()
        cause_by_code = {
            "no_active_assignment": "No existe una relación activa entre esta candidata y una solicitud cobrable.",
            "solicitud_state_blocked": "La solicitud relacionada está en un estado no cobrable para registrar pago.",
            "fallback_state_not_operable": "Existe vínculo legacy, pero no está en un estado operable para cobro.",
            "invalid_candidate_id": "La candidata seleccionada no tiene un identificador válido para validar cobro.",
            "validation_error": "Ocurrió un error al validar la asignación operativa antes del cobro.",
        }
        return {
            "title": "Pago bloqueado",
            "main_message": "No se puede registrar este pago porque la candidata no tiene una asignación activa válida.",
            "cause_message": cause_by_code.get(reason_code) or reason_message or "La validación operativa bloqueó el cobro.",
            "review_steps": [
                "Verifica que la candidata esté asignada a una solicitud.",
                "Verifica que exista relación activa en solicitudes_candidatas.",
                "Verifica que la solicitud esté en estado cobrable.",
                "Si la candidata aparece asignada pero sigue bloqueada, sincroniza la relación operativa.",
            ],
            "diagnostic": {
                "reason_code": reason_code or "unknown",
                "reason_message": reason_message or "Sin detalle",
                "matched_by": getattr(guard_result, "matched_by", None),
                "solicitud_id": getattr(guard_result, "solicitud_id", None),
                "cliente_id": getattr(guard_result, "cliente_id", None),
            },
        }

    def _parse_money_to_decimal(raw: str) -> Decimal:
        """
        Acepta:
          - 10000
          - 10,000
          - 10.000
          - 10,000.50
          - 10.000,50
        Devuelve Decimal con 2 decimales.
        """
        s = (raw or "").strip()
        if not s:
            raise ValueError("Monto vacío")

        allowed = "0123456789.,"
        s = "".join(ch for ch in s if ch in allowed)

        if not s or not any(ch.isdigit() for ch in s):
            raise ValueError("Monto inválido")

        if "." in s and "," in s:
            if s.rfind(",") > s.rfind("."):
                s = s.replace(".", "")
                s = s.replace(",", ".")
            else:
                s = s.replace(",", "")
        else:
            if "," in s:
                parts = s.split(",")
                if len(parts) == 2 and parts[1].isdigit() and 1 <= len(parts[1]) <= 2:
                    s = s.replace(",", ".")
                else:
                    s = s.replace(",", "")

            if "." in s:
                parts = s.split(".")
                if len(parts) == 2 and parts[1].isdigit() and 1 <= len(parts[1]) <= 2:
                    pass
                else:
                    s = s.replace(".", "")

        try:
            val = Decimal(s)
        except Exception:
            raise ValueError("Monto inválido")

        if val <= Decimal("0"):
            raise ValueError("El monto debe ser mayor que 0")

        return val.quantize(Decimal("0.01"))

    if request.method == "POST":
        fila = request.form.get("fila", type=int)
        monto_str = (request.form.get("monto_pagado") or "").strip()[:30]
        calificacion = (request.form.get("calificacion") or "").strip()[:200]

        if not fila or not monto_str or not calificacion:
            flash("❌ Datos inválidos.", "danger")
            return redirect(url_for("pagos"))

        try:
            monto_pagado = _parse_money_to_decimal(monto_str)
        except Exception as e:
            flash(f"❌ Monto inválido: {e}", "danger")
            return redirect(url_for("pagos"))

        obj = get_candidata_by_id(fila)
        if not obj:
            flash("⚠️ Candidata no encontrada.", "warning")
            return redirect(url_for("pagos"))
        assignment_guard = validate_candidata_assignment_context(candidata_id=int(obj.fila))
        payment_block_ui = _build_payment_block_ui(assignment_guard)
        if not assignment_guard.can_charge:
            flash(f"❌ Cobro bloqueado ({assignment_guard.reason_code}): {assignment_guard.reason_message}", "danger")
            return redirect(url_for("pagos", candidata=int(obj.fila)))
        actor = (
            getattr(current_user, "username", None)
            or getattr(current_user, "id", None)
            or session.get("usuario")
            or "sistema"
        )
        result = register_candidate_payment(
            obj,
            {"monto_pagado": monto_pagado, "calificacion": calificacion},
            actor=str(actor),
            now_fn=utc_now_naive,
        )
        if not result.ok:
            legacy_h.app.logger.error("❌ Error al registrar pago: %s", result.error_code or result.message)
            flash(f"❌ {result.message}", "danger")
            return redirect(url_for("pagos", candidata=int(obj.fila)))

        try:
            db.session.commit()
            flash("✅ Pago guardado con éxito.", "success")
            candidata = obj
        except Exception:
            db.session.rollback()
            legacy_h.app.logger.exception("❌ Error al guardar pago")
            flash("❌ Error al guardar.", "danger")

        return render_template(
            "pagos.html",
            resultados=[],
            candidata=candidata,
            assignment_guard=assignment_guard,
            payment_block_ui=payment_block_ui,
        )

    q = (request.args.get("busqueda") or "").strip()[:128]
    sel = (request.args.get("candidata") or "").strip()

    if q:
        try:
            filas = search_candidatas_limited(q, limit=300, minimal_fields=True)

            resultados = [
                {
                    "fila": c.fila,
                    "nombre": c.nombre_completo,
                    "cedula": c.cedula,
                    "telefono": c.numero_telefono or "No especificado",
                }
                for c in filas
            ]

            if not resultados:
                flash("⚠️ No se encontraron coincidencias.", "warning")
        except Exception:
            legacy_h.app.logger.exception("❌ Error buscando en pagos")
            flash("❌ Error al buscar.", "warning")

    if sel.isdigit() and not resultados:
        obj = get_candidata_by_id(sel)
        if obj:
            candidata = obj
        else:
            flash("⚠️ Candidata no encontrada.", "warning")
    assignment_guard = None
    if candidata:
        assignment_guard = validate_candidata_assignment_context(candidata_id=int(candidata.fila))
        payment_block_ui = _build_payment_block_ui(assignment_guard)
    return render_template(
        "pagos.html",
        resultados=resultados,
        candidata=candidata,
        assignment_guard=assignment_guard,
        payment_block_ui=payment_block_ui,
    )
