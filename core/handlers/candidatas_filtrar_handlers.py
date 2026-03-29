# -*- coding: utf-8 -*-
from __future__ import annotations

import re

from flask import current_app, render_template, request
from sqlalchemy import or_

from config_app import db
from decorators import roles_required

from core import legacy_handlers as legacy_h


@roles_required("admin", "secretaria")
def filtrar():
    form_data = {
        "ciudad": (request.values.get("ciudad") or "").strip()[:120],
        "rutas": (request.values.get("rutas") or "").strip()[:120],
        "modalidad": (request.values.get("modalidad") or "").strip()[:60],
        "experiencia_anos": (request.values.get("experiencia_anos") or "").strip()[:30],
        "areas_experiencia": (request.values.get("areas_experiencia") or "").strip()[:120],
        "estado": (request.values.get("estado") or "").strip()[:40],
    }

    filtros = []

    def _terms(raw: str, max_terms: int = 8):
        tokens = [p.strip() for p in re.split(r"[,\s]+", raw or "") if p.strip()]
        return tokens[:max_terms]

    if form_data["ciudad"]:
        ciudades = _terms(form_data["ciudad"])
        if ciudades:
            filtros.append(or_(*[legacy_h.Candidata.direccion_completa.ilike(f"%{c}%") for c in ciudades]))

    if form_data["rutas"]:
        rutas = _terms(form_data["rutas"])
        if rutas:
            filtros.append(or_(*[legacy_h.Candidata.rutas_cercanas.ilike(f"%{r}%") for r in rutas]))

    if form_data["modalidad"]:
        filtros.append(legacy_h.Candidata.modalidad_trabajo_preferida.ilike(f"%{form_data['modalidad']}%"))

    if form_data["experiencia_anos"]:
        ea = form_data["experiencia_anos"]
        if ea == "3 años o más":
            filtros.append(
                or_(
                    legacy_h.Candidata.anos_experiencia.ilike("%3 años%"),
                    legacy_h.Candidata.anos_experiencia.ilike("%4 años%"),
                    legacy_h.Candidata.anos_experiencia.ilike("%5 años%"),
                )
            )
        else:
            filtros.append(legacy_h.Candidata.anos_experiencia == ea)

    if form_data["areas_experiencia"]:
        filtros.append(legacy_h.Candidata.areas_experiencia.ilike(f"%{form_data['areas_experiencia']}%"))

    if form_data["estado"]:
        estado_norm = form_data["estado"].replace(" ", "_")
        filtros.append(legacy_h.Candidata.estado == estado_norm)

    filtros.append(legacy_h.candidatas_activas_filter(legacy_h.Candidata))
    filtros.append(legacy_h.Candidata.codigo.isnot(None))
    filtros.append(or_(legacy_h.Candidata.porciento.is_(None), legacy_h.Candidata.porciento == 0))

    mensaje = None
    resultados = []

    try:
        query = (
            db.session.query(
                legacy_h.Candidata.nombre_completo,
                legacy_h.Candidata.codigo,
                legacy_h.Candidata.numero_telefono,
                legacy_h.Candidata.direccion_completa,
                legacy_h.Candidata.rutas_cercanas,
                legacy_h.Candidata.cedula,
                legacy_h.Candidata.modalidad_trabajo_preferida,
                legacy_h.Candidata.anos_experiencia,
                legacy_h.Candidata.estado,
            )
            .filter(*filtros)
            .order_by(legacy_h.Candidata.nombre_completo.asc())
        )
        candidatas = query.limit(500).all()

        if candidatas:
            resultados = [
                {
                    "nombre": c[0],
                    "codigo": c[1],
                    "telefono": c[2],
                    "direccion": c[3],
                    "rutas": c[4],
                    "cedula": c[5],
                    "modalidad": c[6],
                    "experiencia_anos": c[7],
                    "estado": c[8],
                }
                for c in candidatas
            ]
        else:
            if any(v for v in form_data.values()):
                mensaje = "⚠️ No se encontraron resultados para los filtros aplicados."

    except Exception as e:
        current_app.logger.error(f"❌ Error al filtrar candidatas: {e}", exc_info=True)
        mensaje = "❌ Error al filtrar los datos."

    estados = [
        "en_proceso",
        "proceso_inscripcion",
        "inscrita",
        "inscrita_incompleta",
        "lista_para_trabajar",
        "trabajando",
    ]

    return render_template(
        "filtrar.html",
        form_data=form_data,
        resultados=resultados,
        mensaje=mensaje,
        estados=estados,
    )
