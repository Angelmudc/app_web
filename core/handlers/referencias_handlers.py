# -*- coding: utf-8 -*-
from __future__ import annotations

from flask import current_app, render_template, request, session

from decorators import roles_required
from core.services.candidatas_shared import get_candidata_by_id
from core.services.search import _prioritize_candidata_result, search_candidatas_limited

from core import legacy_handlers as legacy_h


@roles_required('admin', 'secretaria')
def referencias():
    mensaje = None
    accion = (request.args.get('accion') or 'buscar').strip()
    resultados = []
    candidata = None

    if request.method == 'POST' and 'busqueda' in request.form:
        termino = (request.form.get('busqueda') or '').strip()[:128]
        if termino:
            try:
                filas = search_candidatas_limited(
                    termino,
                    limit=300,
                    order_mode="id_desc",
                    log_label="referencias",
                )
                filas = _prioritize_candidata_result(
                    filas,
                    session.get("last_edited_candidata_fila"),
                )
                legacy_h._legacy_buscar_trace(
                    "referencias_search_results",
                    termino=termino,
                    filas=[int(getattr(r, "fila", 0) or 0) for r in (filas or [])[:10]],
                    total=len(filas or []),
                    last_edited=session.get("last_edited_candidata_fila"),
                )
            except Exception:
                current_app.logger.exception("❌ Error buscando candidatas en /referencias")
                filas = []

            resultados = [
                {
                    'id': c.fila,
                    'nombre': c.nombre_completo,
                    'cedula': c.cedula,
                    'telefono': c.numero_telefono or 'No especificado',
                } for c in filas
            ]
            if not resultados:
                mensaje = "⚠️ No se encontraron candidatas."
        else:
            mensaje = "⚠️ Ingresa un término de búsqueda."

        return render_template(
            'referencias.html',
            accion='buscar',
            resultados=resultados,
            mensaje=mensaje,
        )

    candidata_id = request.args.get('candidata', type=int)
    if request.method == 'GET' and candidata_id:
        candidata = get_candidata_by_id(candidata_id)
        if not candidata:
            mensaje = "⚠️ Candidata no encontrada."
            return render_template(
                'referencias.html',
                accion='buscar',
                resultados=[],
                mensaje=mensaje,
            )
        return render_template(
            'referencias.html',
            accion='ver',
            candidata=candidata,
            mensaje=mensaje,
        )

    if request.method == 'POST' and 'candidata_id' in request.form:
        cid = request.form.get('candidata_id', type=int)
        candidata = get_candidata_by_id(cid)
        if not candidata:
            mensaje = "⚠️ Candidata no existe."
        else:
            cand_ref_lab = (request.form.get('referencias_laboral') or '').strip()[:5000]
            cand_ref_fam = (request.form.get('referencias_familiares') or '').strip()[:5000]

            if not legacy_h.legacy_text_is_useful(cand_ref_lab) or not legacy_h.legacy_text_is_useful(cand_ref_fam):
                mensaje = "⚠️ Referencias inválidas. Usa texto real (no placeholders)."
                return render_template(
                    'referencias.html',
                    accion='ver',
                    candidata=candidata,
                    mensaje=mensaje,
                )

            candidata.referencias_laboral = cand_ref_lab
            candidata.referencias_familiares = cand_ref_fam
            candidata.contactos_referencias_laborales = cand_ref_lab
            candidata.referencias_familiares_detalle = cand_ref_fam
            result = legacy_h.execute_robust_save(
                session=legacy_h.db.session,
                persist_fn=lambda _attempt: None,
                verify_fn=lambda: legacy_h._verify_candidata_fields_saved(
                    int(cid),
                    {
                        "referencias_laboral": cand_ref_lab,
                        "referencias_familiares": cand_ref_fam,
                        "contactos_referencias_laborales": cand_ref_lab,
                        "referencias_familiares_detalle": cand_ref_fam,
                    },
                ),
            )
            if result.ok:
                mensaje = "✅ Referencias actualizadas."
            else:
                legacy_h.db.session.rollback()
                current_app.logger.error(
                    "❌ Error al guardar referencias candidata_id=%s attempts=%s error=%s",
                    cid,
                    result.attempts,
                    result.error_message,
                )
                mensaje = "❌ Error al guardar. No se pudo verificar la persistencia."

        return render_template(
            'referencias.html',
            accion='ver',
            candidata=candidata,
            mensaje=mensaje,
        )

    return render_template(
        'referencias.html',
        accion=accion if accion in ('buscar', 'ver') else 'buscar',
        resultados=[],
        mensaje=mensaje,
    )
