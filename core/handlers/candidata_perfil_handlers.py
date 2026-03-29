# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from io import BytesIO

from flask import abort, current_app, render_template, request, send_file

from decorators import roles_required

from core import legacy_handlers as legacy_h


@roles_required('admin', 'secretaria')
def ver_perfil():
    """
    Perfil detallado de candidata. Usa carga con retry para evitar caídas por SSL.
    """
    fila = request.args.get('fila', type=int)
    if fila is None:
        abort(400, description="Falta el parámetro ?fila=<id>.")

    try:
        candidata = legacy_h._get_candidata_safe_by_pk(fila)
    except Exception:
        current_app.logger.exception("Error consultando Candidata.fila=%s", fila)
        abort(500, description="Error consultando la base de datos.")

    if not candidata:
        abort(404, description=f"No existe la candidata con fila={fila}")

    grupos = getattr(candidata, 'grupos_empleo', None)
    if isinstance(grupos, str):
        try:
            parsed = json.loads(grupos)
            grupos = parsed if isinstance(parsed, list) else [str(parsed)]
        except Exception:
            grupos = [g.strip() for g in grupos.split(',') if g.strip()] if grupos else []
    elif grupos is None:
        alt = getattr(candidata, 'grupos', None) or getattr(candidata, 'grupos_empleo_json', None)
        if isinstance(alt, str):
            try:
                parsed = json.loads(alt)
                grupos = parsed if isinstance(parsed, list) else [str(parsed)]
            except Exception:
                grupos = [g.strip() for g in alt.split(',') if g.strip()] if alt else []
        else:
            grupos = alt or []

    tiene_foto = bool(getattr(candidata, 'foto_perfil', None) or getattr(candidata, 'perfil', None))
    tiene_ced1 = bool(getattr(candidata, 'cedula1', None))
    tiene_ced2 = bool(getattr(candidata, 'cedula2', None))

    return render_template(
        'candidata_perfil.html',
        candidata=candidata,
        tiene_foto=tiene_foto,
        tiene_ced1=tiene_ced1,
        tiene_ced2=tiene_ced2,
        grupos=grupos
    )


@roles_required('admin', 'secretaria')
def perfil_candidata():
    """
    Sirve la imagen de perfil (bytes) con ruta más robusta:
    - Lee directo con engine.connect() y text(), con retry.
    - Si no hay imagen, 404.
    """
    fila = request.args.get('fila', type=int)
    if not fila:
        abort(400, description="Falta el parámetro ?fila=<id>.")

    try:
        img_bytes = legacy_h._fetch_image_bytes_safe(fila)
    except Exception:
        current_app.logger.exception("Error leyendo imagen de Candidata.fila=%s", fila)
        abort(500, description="No se pudo leer la imagen.")

    if not img_bytes:
        abort(404, description="La candidata no tiene foto almacenada.")

    bio = BytesIO(img_bytes)
    bio.seek(0)
    return send_file(
        bio,
        mimetype='image/jpeg',
        as_attachment=False,
        download_name=f"perfil_{fila}.jpg",
        max_age=0
    )
