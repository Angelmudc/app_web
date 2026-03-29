# -*- coding: utf-8 -*-
from __future__ import annotations

from decimal import Decimal

from flask import flash, redirect, render_template, request, session, url_for
from flask_login import current_user

from config_app import db
from decorators import roles_required
from core.services.candidatas_shared import get_candidata_by_id
from core.services.date_utils import parse_date, parse_decimal
from core.services.search import search_candidatas_limited
from utils.candidata_readiness import maybe_update_estado_por_completitud
from utils.timezone import utc_now_naive
from services.candidata_invariants import InvariantConflictError, change_candidate_state as invariant_change_candidate_state

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

            if not obj.codigo:
                try:
                    obj.codigo = legacy_h.generar_codigo_unico()
                except Exception:
                    legacy_h.app.logger.exception("❌ Error generando código único")
                    flash("❌ No se pudo generar el código.", "error")
                    return redirect(url_for("inscripcion"))

            obj.medio_inscripcion = (request.form.get("medio") or "").strip()[:60] or obj.medio_inscripcion
            obj.inscripcion = request.form.get("estado") == "si"
            obj.monto = parse_decimal(request.form.get("monto") or "") or obj.monto
            obj.fecha = parse_date(request.form.get("fecha") or "") or obj.fecha

            if obj.inscripcion:
                if obj.monto and obj.fecha:
                    obj.estado = "inscrita"
                else:
                    obj.estado = "inscrita_incompleta"
            else:
                obj.estado = "proceso_inscripcion"

            obj.fecha_cambio_estado = utc_now_naive()
            obj.usuario_cambio_estado = session.get("usuario", "desconocido")[:64]
            try:
                actor = (
                    getattr(current_user, "username", None)
                    or getattr(current_user, "id", None)
                    or session.get("usuario")
                    or "sistema"
                )
                maybe_update_estado_por_completitud(obj, actor=str(actor))
            except Exception:
                pass

            try:
                db.session.commit()
                flash(f"✅ Inscripción guardada. Código: {obj.codigo}", "success")
                candidata = obj
            except Exception:
                db.session.rollback()
                legacy_h.app.logger.exception("❌ Error al guardar inscripción")
                flash("❌ Error al guardar inscripción.", "error")
                return redirect(url_for("inscripcion"))
        else:
            q = (request.form.get("buscar") or "").strip()[:128]
            if q:
                try:
                    resultados = search_candidatas_limited(q, limit=300)
                    if not resultados:
                        flash("⚠️ No se encontraron coincidencias.", "error")
                except Exception:
                    legacy_h.app.logger.exception("❌ Error buscando en inscripción")
                    flash("❌ Error al buscar.", "error")

    else:
        q = (request.args.get("buscar") or "").strip()[:128]
        if q:
            try:
                resultados = search_candidatas_limited(q, limit=300)
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


@roles_required("admin", "secretaria")
def porciento():
    resultados, candidata = [], None

    if request.method == "POST":
        fila_id = (request.form.get("fila_id") or "").strip()
        if not fila_id.isdigit():
            flash("❌ Fila inválida.", "danger")
            return redirect(url_for("porciento"))

        obj = get_candidata_by_id(fila_id)
        if not obj:
            flash("⚠️ Candidata no encontrada.", "warning")
            return redirect(url_for("porciento"))

        fecha_pago = parse_date(request.form.get("fecha_pago") or "")
        fecha_inicio = parse_date(request.form.get("fecha_inicio") or "")
        monto_total = parse_decimal(request.form.get("monto_total") or "")

        if None in (fecha_pago, fecha_inicio, monto_total):
            flash("❌ Datos incompletos o inválidos.", "danger")
            return redirect(url_for("porciento", candidata=fila_id))

        try:
            porcentaje = (monto_total * Decimal("0.25")).quantize(Decimal("0.01"))
        except Exception:
            flash("❌ Monto inválido.", "danger")
            return redirect(url_for("porciento", candidata=fila_id))

        obj.fecha_de_pago = fecha_pago
        obj.inicio = fecha_inicio
        obj.monto_total = monto_total
        obj.porciento = porcentaje
        try:
            invariant_change_candidate_state(
                candidata_id=int(obj.fila),
                new_state="trabajando",
                actor=str(session.get("usuario", "desconocido") or "desconocido"),
                reason="legacy_porciento",
                candidata_obj=obj,
            )
        except InvariantConflictError as inv_exc:
            flash(f"⚠️ {str(inv_exc)}", "warning")
            return redirect(url_for("porciento", candidata=fila_id))

        try:
            db.session.commit()
            flash(
                f"✅ Se guardó correctamente. 25 % de {monto_total} es {porcentaje}. Estado: Trabajando.",
                "success",
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
                resultados = search_candidatas_limited(q, limit=300)
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

    return render_template("porciento.html", resultados=resultados, candidata=candidata)


@roles_required("admin", "secretaria")
def pagos():
    resultados, candidata = [], None

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

        actual = obj.porciento if obj.porciento is not None else Decimal("0.00")
        try:
            actual = Decimal(str(actual))
        except Exception:
            actual = Decimal("0.00")

        nuevo = actual - monto_pagado
        if nuevo < Decimal("0"):
            nuevo = Decimal("0.00")

        obj.porciento = nuevo.quantize(Decimal("0.01"))
        obj.calificacion = calificacion

        try:
            obj.fecha_de_pago = legacy_h.rd_today()
        except Exception:
            pass

        try:
            db.session.commit()
            flash("✅ Pago guardado con éxito.", "success")
            candidata = obj
        except Exception:
            db.session.rollback()
            legacy_h.app.logger.exception("❌ Error al guardar pago")
            flash("❌ Error al guardar.", "danger")

        return render_template("pagos.html", resultados=[], candidata=candidata)

    q = (request.args.get("busqueda") or "").strip()[:128]
    sel = (request.args.get("candidata") or "").strip()

    if q:
        try:
            filas = search_candidatas_limited(q, limit=300)

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

    return render_template("pagos.html", resultados=resultados, candidata=candidata)
