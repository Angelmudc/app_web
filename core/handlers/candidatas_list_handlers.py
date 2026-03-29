# -*- coding: utf-8 -*-
from __future__ import annotations

import os

from flask import current_app, flash, jsonify, render_template, request
from sqlalchemy.orm import load_only

from config_app import cache
from decorators import roles_required
from core.services.cache_keys import _cache_key_with_role
from core.services.search import apply_search_to_candidata_query

from core import legacy_handlers as legacy_h


@roles_required("admin", "secretaria")
def list_candidatas():
    q = (request.args.get("q") or "").strip()[:128]
    page = max(1, request.args.get("page", default=1, type=int))
    per_page = min(200, max(1, request.args.get("per_page", default=80, type=int)))

    try:
        base = legacy_h.Candidata.query.filter(
            legacy_h.candidatas_activas_filter(legacy_h.Candidata),
            legacy_h.Candidata.estado != "trabajando",
        )
        if q:
            base = apply_search_to_candidata_query(base, q)

        pagination = base.order_by(legacy_h.Candidata.nombre_completo.asc()).paginate(
            page=page,
            per_page=per_page,
            error_out=False,
        )
        candidatas = pagination.items
        return render_template(
            "candidatas.html",
            candidatas=candidatas,
            query=q,
            pagination=pagination,
            page=page,
            per_page=per_page,
        )
    except Exception:
        current_app.logger.exception("❌ Error listando candidatas")
        flash("Ocurrió un error al listar candidatas. Intenta de nuevo.", "danger")
        return render_template("candidatas.html", candidatas=[], query=q), 500


@roles_required("admin", "secretaria")
@cache.cached(
    timeout=int(os.getenv("CACHE_CANDIDATAS_DB_SECONDS", "60")),
    key_prefix=lambda: _cache_key_with_role("candidatas_db"),
)
def list_candidatas_db():
    try:
        max_rows = min(
            5000,
            max(100, int(os.getenv("MAX_CANDIDATAS_DB_ROWS", "1500"))),
        )

        candidatas = (
            legacy_h.Candidata.query.options(
                load_only(
                    legacy_h.Candidata.fila,
                    legacy_h.Candidata.marca_temporal,
                    legacy_h.Candidata.nombre_completo,
                    legacy_h.Candidata.edad,
                    legacy_h.Candidata.numero_telefono,
                    legacy_h.Candidata.direccion_completa,
                    legacy_h.Candidata.modalidad_trabajo_preferida,
                    legacy_h.Candidata.cedula,
                    legacy_h.Candidata.codigo,
                    legacy_h.Candidata.disponibilidad_inicio,
                    legacy_h.Candidata.trabaja_con_ninos,
                    legacy_h.Candidata.trabaja_con_mascotas,
                    legacy_h.Candidata.puede_dormir_fuera,
                    legacy_h.Candidata.sueldo_esperado,
                    legacy_h.Candidata.motivacion_trabajo,
                )
            )
            .limit(max_rows)
            .all()
        )

        resultado = []
        for c in candidatas:
            resultado.append(
                {
                    "fila": c.fila,
                    "marca_temporal": legacy_h.iso_utc_z(c.marca_temporal) if getattr(c, "marca_temporal", None) else None,
                    "nombre_completo": c.nombre_completo,
                    "edad": c.edad,
                    "numero_telefono": c.numero_telefono,
                    "direccion_completa": c.direccion_completa,
                    "modalidad_trabajo_preferida": c.modalidad_trabajo_preferida,
                    "cedula": c.cedula,
                    "codigo": c.codigo,
                    "disponibilidad_inicio": c.disponibilidad_inicio,
                    "trabaja_con_ninos": c.trabaja_con_ninos,
                    "trabaja_con_mascotas": c.trabaja_con_mascotas,
                    "puede_dormir_fuera": c.puede_dormir_fuera,
                    "sueldo_esperado": c.sueldo_esperado,
                    "motivacion_trabajo": c.motivacion_trabajo,
                }
            )
        return (
            jsonify(
                {
                    "candidatas": resultado,
                    "meta": {
                        "max_rows": max_rows,
                        "returned": len(resultado),
                    },
                }
            ),
            200,
        )

    except Exception:
        current_app.logger.exception("❌ Error leyendo candidatas desde la DB")
        return jsonify({"error": "Error al consultar la base de datos."}), 500
