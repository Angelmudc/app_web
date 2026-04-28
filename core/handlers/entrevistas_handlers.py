# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from typing import Optional

from flask import abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user
from flask_wtf.csrf import generate_csrf
from jinja2 import TemplateNotFound

from config_app import cache, db
from decorators import roles_required
from models import Candidata, Entrevista, EntrevistaPregunta, EntrevistaRespuesta
from utils.audit_entity import log_candidata_action
from utils.candidata_readiness import maybe_update_estado_por_completitud
from utils.guards import assert_candidata_no_descalificada, candidatas_activas_filter
from utils.robust_save import execute_robust_save, legacy_text_is_useful
from utils.timezone import utc_now_naive

from core import legacy_handlers as legacy_h
from core.services.search import apply_search_to_candidata_query


def _safe_next_url() -> str:
    nxt = (request.values.get("next") or "").strip()
    return nxt if legacy_h._is_safe_next(nxt) else ""


@cache.memoize(timeout=int(os.getenv("CACHE_PREGUNTAS_SECONDS", "300")))
def _get_preguntas_db_por_tipo_cached(tipo_cached: str):
    return (
        EntrevistaPregunta.query
        .filter(EntrevistaPregunta.activa.is_(True))
        .filter(EntrevistaPregunta.clave.like(f"{tipo_cached}.%"))
        .order_by(EntrevistaPregunta.orden.asc(), EntrevistaPregunta.id.asc())
        .all()
    )


def _get_preguntas_db_por_tipo(tipo: str):
    """Devuelve preguntas activas para un tipo (domestica/enfermera/empleo_general)."""
    tipo = (tipo or "").strip().lower()
    if not tipo:
        return []

    return _get_preguntas_db_por_tipo_cached(tipo)


def _safe_setattr(obj, name: str, value):
    """Setea un atributo solo si existe en el modelo."""
    if hasattr(obj, name):
        try:
            setattr(obj, name, value)
            return True
        except Exception:
            return False
    return False


def _current_staff_actor() -> str:
    return str(
        getattr(current_user, "username", None)
        or getattr(current_user, "id", None)
        or session.get("usuario")
        or "sistema"
    )


def _verify_interview_new_saved(entrevista_id: int, candidata_id: Optional[int] = None) -> bool:
    if not int(entrevista_id or 0):
        return False
    entrevista = (
        Entrevista.query
        .filter(Entrevista.id == int(entrevista_id))
        .first()
    )
    if not entrevista:
        return False
    if candidata_id and int(getattr(entrevista, "candidata_id", 0) or 0) != int(candidata_id):
        return False
    answers_count = (
        EntrevistaRespuesta.query
        .filter(EntrevistaRespuesta.entrevista_id == int(entrevista_id))
        .count()
    )
    if int(answers_count or 0) <= 0:
        return False
    useful_answers = (
        EntrevistaRespuesta.query
        .filter(EntrevistaRespuesta.entrevista_id == int(entrevista_id))
        .all()
    )
    return any(legacy_text_is_useful(getattr(a, "respuesta", None)) for a in useful_answers)


def _build_legacy_interview_text(preguntas, respuestas_por_pregunta: dict[int, str]) -> str:
    lines = []
    for p in (preguntas or []):
        q = (getattr(p, "enunciado", None) or getattr(p, "clave", None) or f"Pregunta {getattr(p, 'id', '')}").strip()
        a = (respuestas_por_pregunta.get(getattr(p, "id", 0)) or "").strip()
        if not q:
            continue
        lines.append(f"{q}: {a if a else '-'}")
    return "\n".join(lines).strip()[:12000]


@roles_required('admin', 'secretaria')
def entrevistas_index():
    """Entrada principal a las entrevistas NUEVAS (DB)."""
    return redirect(url_for('entrevistas_buscar'))


@roles_required('admin', 'secretaria')
def entrevistas_buscar():
    """Busca una candidata y te manda a la lista de entrevistas de esa candidata."""
    q = (request.form.get('busqueda') or request.args.get('q') or '').strip()[:128]
    next_url = _safe_next_url()
    resultados = []
    mensaje = None

    if request.method == 'POST':
        if not q:
            flash('⚠️ Escribe algo para buscar.', 'warning')
            return redirect(url_for('entrevistas_buscar'))

    if q:
        try:
            filas = (
                apply_search_to_candidata_query(
                    Candidata.query.filter(candidatas_activas_filter(Candidata)),
                    q
                )
                .order_by(Candidata.nombre_completo.asc())
                .limit(200)
                .all()
            )
            resultados = filas or []
            if not resultados:
                mensaje = '⚠️ No se encontraron candidatas.'
        except Exception:
            current_app.logger.exception('❌ Error buscando candidatas (entrevistas_buscar)')
            mensaje = '❌ Error al buscar. Intenta de nuevo.'

    try:
        return render_template('entrevistas/buscar.html', q=q, resultados=resultados, mensaje=mensaje, next_url=next_url)
    except TemplateNotFound:
        token = generate_csrf()
        html = [
            '<h2>Entrevistas (NUEVAS - DB) · Buscar candidata</h2>',
            '<form method="POST">',
            f'<input type="hidden" name="csrf_token" value="{token}">',
            f'<input name="busqueda" placeholder="Nombre / Cédula / Teléfono" style="width:320px" value="{q or ""}">',
            '<button type="submit">Buscar</button>',
            '</form>',
        ]
        if mensaje:
            html.append(f"<p>{mensaje}</p>")
        if resultados:
            html.append('<hr><ul>')
            for c in resultados:
                html.append(
                    f"<li><b>{(c.nombre_completo or '').strip()}</b> · {c.cedula or ''} · {c.numero_telefono or ''} "
                    f"— <a href=\"{url_for('entrevistas_de_candidata', fila=c.fila)}\">Ver entrevistas</a> "
                    f"— <a href=\"{url_for('entrevista_nueva_db', fila=c.fila, tipo='domestica')}\">Nueva doméstica</a> "
                    f"— <a href=\"{url_for('entrevista_nueva_db', fila=c.fila, tipo='enfermera')}\">Nueva enfermera</a>"
                    f"</li>"
                )
            html.append('</ul>')
        html.append('<hr><p><a href="/home">Volver a Home</a></p>')
        return "\n".join(html)


@roles_required('admin', 'secretaria')
def entrevistas_lista():
    """Lista rápida de las últimas entrevistas NUEVAS guardadas (debug/QA)."""
    next_url = _safe_next_url()
    try:
        q = Entrevista.query
        if hasattr(Entrevista, 'id'):
            q = q.order_by(Entrevista.id.desc())
        entrevistas = q.limit(50).all()
    except Exception:
        current_app.logger.exception('❌ Error cargando entrevistas (lista)')
        entrevistas = []

    try:
        current_app.logger.info('✅ Render entrevistas/lista.html (entrevistas_lista)')
        return render_template('entrevistas/lista.html', entrevistas=entrevistas, next_url=next_url)
    except TemplateNotFound:
        current_app.logger.exception('❌ TemplateNotFound: entrevistas/lista.html')
        html = ['<h2>Entrevistas (NUEVAS - DB) · Últimas 50</h2>']
        html.append('<p><a href="/entrevistas/buscar">Buscar candidata</a></p>')
        if not entrevistas:
            html.append('<p>No hay entrevistas aún.</p>')
        else:
            html.append('<ul>')
            for e in entrevistas:
                fila = getattr(e, 'candidata_id', None)
                tipo = getattr(e, 'tipo', None) if hasattr(e, 'tipo') else None
                eid = getattr(e, 'id', None)

                link_cand = f' — <a href="{url_for("entrevistas_de_candidata", fila=fila)}">ver candidata</a>' if fila else ''
                link_edit = f' — <a href="{url_for("entrevista_editar_db", entrevista_id=eid)}">editar</a>' if eid else ''

                html.append(f"<li>ID: {eid or ''} · candidata_id: {fila or ''} · tipo: {tipo or ''}{link_cand}{link_edit}</li>")
            html.append('</ul>')

        html.append('<hr><p><a href="/home">Volver a Home</a></p>')
        return "\n".join(html)


@roles_required('admin', 'secretaria')
def entrevistas_de_candidata(fila):
    next_url = _safe_next_url()
    candidata = legacy_h._get_candidata_safe_by_pk(fila)
    if not candidata:
        flash("⚠️ Candidata no encontrada.", "warning")
        return redirect(url_for('entrevistas_buscar'))

    entrevistas = (
        Entrevista.query
        .filter_by(candidata_id=fila)
        .order_by(Entrevista.id.desc())
        .all()
    )

    return render_template(
        "entrevistas/entrevistas_lista.html",
        candidata=candidata,
        entrevistas=entrevistas,
        next_url=next_url,
    )


@roles_required('admin', 'secretaria')
def entrevista_nueva_db(fila, tipo):
    next_url = _safe_next_url()
    candidata = legacy_h._get_candidata_safe_by_pk(fila)
    if not candidata:
        flash("⚠️ Candidata no encontrada.", "warning")
        return redirect(url_for('entrevistas_buscar'))
    blocked = assert_candidata_no_descalificada(
        candidata,
        action="crear entrevista",
        redirect_endpoint="entrevistas_de_candidata",
        redirect_kwargs={"fila": fila},
    )
    if blocked is not None:
        return blocked

    preguntas = _get_preguntas_db_por_tipo(tipo)
    if not preguntas:
        flash("⚠️ No hay preguntas configuradas para ese tipo de entrevista.", "warning")
        return redirect(url_for('entrevistas_de_candidata', fila=fila))

    if request.method == "POST":
        respuestas_payload = {}
        for p in preguntas:
            field = f"q_{p.id}"
            respuestas_payload[int(p.id)] = (request.form.get(field) or "").strip()

        if not any(legacy_text_is_useful(v) for v in respuestas_payload.values()):
            flash("❌ La entrevista está vacía o no es válida. Completa al menos una respuesta útil.", "danger")
            return redirect(url_for('entrevista_nueva_db', fila=fila, tipo=tipo))

        state = {"entrevista_id": 0, "legacy_text": ""}

        def _persist_interview(_attempt: int):
            entrevista = Entrevista(candidata_id=fila)
            _safe_setattr(entrevista, 'estado', 'completa')
            _safe_setattr(entrevista, 'creada_en', utc_now_naive())
            _safe_setattr(entrevista, 'actualizada_en', None)
            _safe_setattr(entrevista, 'tipo', (tipo or '').strip().lower())
            db.session.add(entrevista)
            db.session.flush()
            state["entrevista_id"] = int(getattr(entrevista, "id", 0) or 0)

            for p in preguntas:
                valor = respuestas_payload.get(int(p.id), "")
                r = EntrevistaRespuesta(
                    entrevista_id=entrevista.id,
                    pregunta_id=p.id,
                    respuesta=valor if valor else None,
                )
                _safe_setattr(r, 'creada_en', utc_now_naive())
                db.session.add(r)

            state["legacy_text"] = _build_legacy_interview_text(preguntas, respuestas_payload)
            if legacy_text_is_useful(state["legacy_text"]):
                candidata.entrevista = state["legacy_text"]

            try:
                maybe_update_estado_por_completitud(candidata, actor=_current_staff_actor())
            except Exception:
                pass

        result = execute_robust_save(
            session=db.session,
            persist_fn=_persist_interview,
            verify_fn=lambda: _verify_interview_new_saved(int(state.get("entrevista_id") or 0), int(fila)),
        )

        if result.ok:
            metadata_ok = {
                "candidata_id": int(fila),
                "entrevista_nueva": True,
                "entrevista_legacy": bool(legacy_text_is_useful(state.get("legacy_text") or "")),
                "attempt_count": int(result.attempts),
                "bytes_length": {},
            }
            log_candidata_action(
                action_type="CANDIDATA_INTERVIEW_SAVE_OK",
                candidata=candidata,
                summary=f"Guardado robusto entrevista OK ({(tipo or '').strip().lower() or 'domestica'})",
                metadata=metadata_ok,
                success=True,
            )
            log_candidata_action(
                action_type="CANDIDATA_INTERVIEW_NEW_CREATE",
                candidata=candidata,
                summary=f"Entrevista nueva guardada ({(tipo or '').strip().lower() or 'domestica'})",
                metadata={"entrevista_id": int(state.get("entrevista_id") or 0), "tipo": (tipo or "").strip().lower()},
                success=True,
            )
            flash("✅ Entrevista guardada.", "success")
            if next_url:
                return redirect(next_url)
            return redirect(url_for('entrevistas_de_candidata', fila=fila))

        current_app.logger.error(
            "❌ Error guardando entrevista (robust_save) fila=%s attempts=%s error=%s",
            fila,
            result.attempts,
            result.error_message,
        )
        log_candidata_action(
            action_type="CANDIDATA_INTERVIEW_SAVE_FAIL",
            candidata=candidata,
            summary=f"Fallo guardado robusto entrevista ({(tipo or '').strip().lower() or 'domestica'})",
            metadata={
                "candidata_id": int(fila),
                "entrevista_nueva": True,
                "entrevista_legacy": bool(legacy_text_is_useful(state.get("legacy_text") or "")),
                "attempt_count": int(result.attempts),
                "error_message": (result.error_message or "")[:200],
                "bytes_length": {},
            },
            success=False,
            error="No se pudo guardar entrevista nueva de forma verificada.",
        )
        log_candidata_action(
            action_type="CANDIDATA_INTERVIEW_NEW_CREATE",
            candidata=candidata,
            summary=f"Fallo guardando entrevista nueva ({(tipo or '').strip().lower() or 'domestica'})",
            metadata={"tipo": (tipo or "").strip().lower()},
            success=False,
            error="No se pudo guardar la entrevista nueva.",
        )
        flash("No se pudo guardar. Intente de nuevo. Si persiste, contacte admin.", "danger")
        return redirect(url_for('entrevista_nueva_db', fila=fila, tipo=tipo))

    return render_template(
        "entrevistas/entrevista_form.html",
        modo="nueva",
        tipo=(tipo or '').strip().lower(),
        candidata=candidata,
        preguntas=preguntas,
        respuestas_por_pregunta={},
        entrevista=None,
        next_url=next_url,
    )


@roles_required('admin', 'secretaria')
def entrevista_editar_redirect():
    """Compat: soporta links viejos tipo /entrevistas/editar?id=123 o ?entrevista_id=123"""
    eid = (request.args.get('entrevista_id', type=int)
           or request.args.get('id', type=int))
    if not eid:
        abort(404)
    return redirect(url_for('entrevista_editar_db', entrevista_id=eid))


@roles_required('admin', 'secretaria')
def entrevista_editar_db(entrevista_id):
    next_url = _safe_next_url()
    entrevista = Entrevista.query.get_or_404(entrevista_id)
    fila = getattr(entrevista, 'candidata_id', None)

    candidata = legacy_h._get_candidata_safe_by_pk(fila) if fila else None
    if not candidata:
        flash("⚠️ Candidata no encontrada.", "warning")
        return redirect(url_for('entrevistas_buscar'))
    blocked = assert_candidata_no_descalificada(
        candidata,
        action="editar entrevista",
        redirect_endpoint="entrevistas_de_candidata",
        redirect_kwargs={"fila": fila},
    )
    if blocked is not None:
        return blocked

    respuestas = (
        EntrevistaRespuesta.query
        .filter_by(entrevista_id=entrevista.id)
        .all()
    )
    respuestas_por_pregunta = {r.pregunta_id: (r.respuesta or "") for r in respuestas}

    tipo = None
    if hasattr(entrevista, 'tipo') and getattr(entrevista, 'tipo', None):
        tipo = (getattr(entrevista, 'tipo') or '').strip().lower()

    if not tipo and respuestas:
        p0 = EntrevistaPregunta.query.get(respuestas[0].pregunta_id)
        if p0 and p0.clave and "." in p0.clave:
            tipo = p0.clave.split(".", 1)[0]

    tipo = tipo or "domestica"

    preguntas = _get_preguntas_db_por_tipo(tipo)
    if not preguntas:
        flash("⚠️ No hay preguntas configuradas para este tipo.", "warning")
        return redirect(url_for('entrevistas_de_candidata', fila=fila))

    if request.method == "POST":
        respuestas_payload = {}
        for p in preguntas:
            field = f"q_{p.id}"
            respuestas_payload[int(p.id)] = (request.form.get(field) or "").strip()

        if not any(legacy_text_is_useful(v) for v in respuestas_payload.values()):
            flash("❌ La entrevista está vacía o no es válida. Completa al menos una respuesta útil.", "danger")
            return redirect(url_for('entrevista_editar_db', entrevista_id=entrevista.id))

        def _persist_interview_update(_attempt: int):
            for p in preguntas:
                valor = respuestas_payload.get(int(p.id), "")
                r = (
                    EntrevistaRespuesta.query
                    .filter_by(entrevista_id=entrevista.id, pregunta_id=p.id)
                    .first()
                )

                if not r:
                    r = EntrevistaRespuesta(
                        entrevista_id=entrevista.id,
                        pregunta_id=p.id,
                    )
                    _safe_setattr(r, 'creada_en', utc_now_naive())
                    db.session.add(r)

                r.respuesta = valor if valor else None
                _safe_setattr(r, 'actualizada_en', utc_now_naive())

            _safe_setattr(entrevista, 'actualizada_en', utc_now_naive())
            _safe_setattr(entrevista, 'estado', 'completa')
            _safe_setattr(entrevista, 'tipo', tipo)

            legacy_text = _build_legacy_interview_text(preguntas, respuestas_payload)
            if legacy_text_is_useful(legacy_text):
                candidata.entrevista = legacy_text

            try:
                maybe_update_estado_por_completitud(candidata, actor=_current_staff_actor())
            except Exception:
                pass

        result = execute_robust_save(
            session=db.session,
            persist_fn=_persist_interview_update,
            verify_fn=lambda: _verify_interview_new_saved(int(getattr(entrevista, "id", 0) or 0), int(fila)),
        )

        if result.ok:
            log_candidata_action(
                action_type="CANDIDATA_INTERVIEW_SAVE_OK",
                candidata=candidata,
                summary=f"Guardado robusto entrevista OK ({tipo})",
                metadata={
                    "candidata_id": int(fila),
                    "entrevista_id": int(getattr(entrevista, "id", 0) or 0),
                    "entrevista_nueva": True,
                    "entrevista_legacy": True,
                    "attempt_count": int(result.attempts),
                    "bytes_length": {},
                },
                success=True,
            )
            log_candidata_action(
                action_type="CANDIDATA_INTERVIEW_NEW_CREATE",
                candidata=candidata,
                summary=f"Entrevista actualizada ({tipo})",
                metadata={"entrevista_id": entrevista.id, "tipo": tipo},
                success=True,
            )
            flash("✅ Entrevista actualizada.", "success")
            if next_url:
                return redirect(next_url)
            return redirect(url_for('entrevistas_de_candidata', fila=fila))

        current_app.logger.error(
            "❌ Error actualizando entrevista (robust_save) entrevista_id=%s attempts=%s error=%s",
            entrevista.id,
            result.attempts,
            result.error_message,
        )
        log_candidata_action(
            action_type="CANDIDATA_INTERVIEW_SAVE_FAIL",
            candidata=candidata,
            summary=f"Fallo guardado robusto entrevista ({tipo})",
            metadata={
                "candidata_id": int(fila),
                "entrevista_id": int(getattr(entrevista, "id", 0) or 0),
                "entrevista_nueva": True,
                "entrevista_legacy": True,
                "attempt_count": int(result.attempts),
                "error_message": (result.error_message or "")[:200],
                "bytes_length": {},
            },
            success=False,
            error="No se pudo guardar entrevista de forma verificada.",
        )
        log_candidata_action(
            action_type="CANDIDATA_INTERVIEW_NEW_CREATE",
            candidata=candidata,
            summary=f"Fallo actualizando entrevista ({tipo})",
            metadata={"entrevista_id": entrevista.id, "tipo": tipo},
            success=False,
            error="No se pudo actualizar la entrevista.",
        )
        flash("No se pudo guardar. Intente de nuevo. Si persiste, contacte admin.", "danger")
        return redirect(url_for('entrevista_editar_db', entrevista_id=entrevista.id))

    return render_template(
        "entrevistas/entrevista_form.html",
        modo="editar",
        tipo=tipo,
        candidata=candidata,
        preguntas=preguntas,
        respuestas_por_pregunta=respuestas_por_pregunta,
        entrevista=entrevista,
        next_url=next_url,
    )
