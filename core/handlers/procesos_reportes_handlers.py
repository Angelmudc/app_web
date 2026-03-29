# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import os

import pandas as pd
from flask import current_app, render_template, request, send_file
from sqlalchemy import func

from config_app import cache, db
from decorators import roles_required
from core.services.cache_keys import _cache_key_with_role
from core.services.db_retry import _retry_query

from core import legacy_handlers as legacy_h


@roles_required("admin")
def reporte_inscripciones():
    """
    Reporte de inscripciones por mes/año.
    """
    try:
        today = legacy_h.rd_today()
        mes = int(request.args.get("mes", today.month))
        anio = int(request.args.get("anio", today.year))
        descargar = request.args.get("descargar", "0") == "1"
        page = max(1, request.args.get("page", default=1, type=int))
        per_page = min(200, max(1, request.args.get("per_page", default=20, type=int)))
        if not (1 <= mes <= 12):
            return "Parámetro 'mes' inválido.", 400
        if anio < 2000 or anio > today.year + 1:
            return "Parámetro 'anio' inválido.", 400
    except Exception as e:
        return f"Parámetros inválidos: {e}", 400

    def _base_query():
        return (
            db.session.query(
                legacy_h.Candidata.nombre_completo,
                legacy_h.Candidata.direccion_completa,
                legacy_h.Candidata.numero_telefono,
                legacy_h.Candidata.cedula,
                legacy_h.Candidata.codigo,
                legacy_h.Candidata.medio_inscripcion,
                legacy_h.Candidata.inscripcion,
                legacy_h.Candidata.monto,
                legacy_h.Candidata.fecha,
            )
            .filter(
                legacy_h.Candidata.inscripcion.is_(True),
                legacy_h.Candidata.fecha.isnot(None),
                func.extract("month", legacy_h.Candidata.fecha) == mes,
                func.extract("year", legacy_h.Candidata.fecha) == anio,
            )
        )

    if descargar:
        def _fetch_all():
            return _base_query().order_by(legacy_h.Candidata.fecha.asc()).all()

        rows = _retry_query(_fetch_all, retries=2, swallow=True)
        if rows is None:
            return (
                render_template(
                    "reporte_inscripciones.html",
                    reporte_html="",
                    mes=mes,
                    anio=anio,
                    mensaje="❌ No fue posible conectarse a la base de datos para generar el Excel. Intenta de nuevo.",
                ),
                200,
            )

        if not rows:
            return (
                render_template(
                    "reporte_inscripciones.html",
                    reporte_html="",
                    mes=mes,
                    anio=anio,
                    mensaje=f"No se encontraron inscripciones para {mes}/{anio}.",
                ),
                200,
            )

        df = pd.DataFrame(
            [
                {
                    "Nombre": r[0] or "",
                    "Ciudad": r[1] or "",
                    "Teléfono": r[2] or "",
                    "Cédula": r[3] or "",
                    "Código": r[4] or "",
                    "Medio": r[5] or "",
                    "Inscripción": "Sí" if r[6] else "No",
                    "Monto": float(r[7] or 0),
                    "Fecha": legacy_h.format_rd_datetime(r[8], "%Y-%m-%d", "") if r[8] else "",
                }
                for r in rows
            ]
        )

        output = io.BytesIO()
        try:
            with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
                df.to_excel(writer, index=False, sheet_name="Reporte")
            output.seek(0)
        except Exception as e:
            current_app.logger.exception("❌ Error generando Excel de inscripciones")
            return (
                render_template(
                    "reporte_inscripciones.html",
                    reporte_html="",
                    mes=mes,
                    anio=anio,
                    mensaje=f"❌ Error generando el archivo: {e}",
                ),
                200,
            )

        filename = f"Reporte_Inscripciones_{anio}_{mes:02d}.xlsx"
        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def _fetch_page():
        q = _base_query().order_by(legacy_h.Candidata.fecha.desc())
        total = q.count()
        items = q.offset((page - 1) * per_page).limit(per_page).all()
        return total, items

    fetched = _retry_query(_fetch_page, retries=2, swallow=True)
    if fetched is None:
        return (
            render_template(
                "reporte_inscripciones.html",
                reporte_html="",
                mes=mes,
                anio=anio,
                mensaje="❌ No fue posible conectarse a la base de datos. Intenta nuevamente.",
            ),
            200,
        )

    total, items = fetched

    if not items:
        return (
            render_template(
                "reporte_inscripciones.html",
                reporte_html="",
                mes=mes,
                anio=anio,
                mensaje=f"No se encontraron inscripciones para {mes}/{anio}.",
            ),
            200,
        )

    df = pd.DataFrame(
        [
            {
                "Nombre": r[0] or "",
                "Ciudad": r[1] or "",
                "Teléfono": r[2] or "",
                "Cédula": r[3] or "",
                "Código": r[4] or "",
                "Medio": r[5] or "",
                "Inscripción": "Sí" if r[6] else "No",
                "Monto": float(r[7] or 0),
                "Fecha": legacy_h.format_rd_datetime(r[8], "%Y-%m-%d", "") if r[8] else "",
            }
            for r in items
        ]
    )

    reporte_html = df.to_html(classes="table table-striped", index=False, border=0)
    total_pages = (total + per_page - 1) // per_page

    return render_template(
        "reporte_inscripciones.html",
        reporte_html=reporte_html,
        mes=mes,
        anio=anio,
        mensaje="",
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages,
    )


@roles_required("admin", "secretaria")
@cache.cached(
    timeout=int(os.getenv("CACHE_REPORTE_PAGOS_SECONDS", "45")),
    key_prefix=lambda: _cache_key_with_role("reporte_pagos"),
)
def reporte_pagos():
    """
    Reporte de pagos pendientes (porciento > 0).
    """
    page = max(1, request.args.get("page", default=1, type=int))
    per_page = min(200, max(1, request.args.get("per_page", default=20, type=int)))

    def _fetch_page():
        q = (
            db.session.query(
                legacy_h.Candidata.nombre_completo,
                legacy_h.Candidata.cedula,
                legacy_h.Candidata.codigo,
                legacy_h.Candidata.porciento,
            )
            .filter(legacy_h.Candidata.porciento > 0)
            .order_by(legacy_h.Candidata.nombre_completo.asc())
        )

        total = q.count()
        items = q.offset((page - 1) * per_page).limit(per_page).all()
        return total, items

    fetched = _retry_query(_fetch_page, retries=2, swallow=True)
    if fetched is None:
        return (
            render_template(
                "reporte_pagos.html",
                pagos_pendientes=[],
                mensaje="❌ No fue posible conectarse a la base de datos. Intenta nuevamente.",
            ),
            200,
        )

    total, rows = fetched

    pagos_pendientes = [
        {
            "nombre": r[0] or "",
            "cedula": r[1] or "",
            "codigo": r[2] or "No especificado",
            "porcentaje_pendiente": float(r[3] or 0),
        }
        for r in rows
    ]

    mensaje = None if pagos_pendientes else "⚠️ No se encontraron pagos pendientes."
    total_pages = (total + per_page - 1) // per_page

    return render_template(
        "reporte_pagos.html",
        pagos_pendientes=pagos_pendientes,
        mensaje=mensaje,
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages,
    )
