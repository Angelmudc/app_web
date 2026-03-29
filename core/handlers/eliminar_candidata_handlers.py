# -*- coding: utf-8 -*-
from __future__ import annotations

from flask import current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

from decorators import roles_required

from core import legacy_handlers as legacy_h


@roles_required("admin", "secretaria")
def eliminar_candidata():
    """
    Pantalla para eliminar candidatas manualmente:
    - Paso 1: Buscar por nombre, cédula, teléfono o código.
    - Paso 2: Ver detalle completo (docs, entrevista, etc.).
    - Paso 3: Confirmar eliminación definitiva.

    ✅ SOLO se permite eliminar candidatas SIN historial:
    - sin solicitudes
    - sin llamadas
    - sin reemplazos
    """

    def _has_blob(v) -> bool:
        if v is None:
            return False
        if isinstance(v, memoryview):
            try:
                return len(v.tobytes()) > 0
            except Exception:
                return False
        if isinstance(v, (bytes, bytearray)):
            return len(v) > 0
        try:
            return bool(v)
        except Exception:
            return False

    def _safe_len(rel):
        try:
            if rel is None:
                return 0
            return len(rel)
        except Exception:
            return 0

    def _count_scalar(query):
        try:
            n = query.scalar()
            return int(n or 0)
        except Exception:
            return 0

    def build_docs_info(c):
        if not c:
            return {
                "tiene_cedula1": False,
                "tiene_cedula2": False,
                "tiene_perfil": False,
                "tiene_depuracion": False,
                "documentos_completos": False,
                "entrevista_realizada": False,
                "solicitudes_count": 0,
                "llamadas_count": 0,
                "reemplazos_count": 0,
                "tiene_historial": False,
                "puede_eliminar": False,
            }

        tiene_cedula1 = _has_blob(getattr(c, "cedula1", None))
        tiene_cedula2 = _has_blob(getattr(c, "cedula2", None))
        tiene_perfil = _has_blob(getattr(c, "perfil", None))
        tiene_dep = _has_blob(getattr(c, "depuracion", None))
        documentos_completos = (tiene_cedula1 and tiene_cedula2 and tiene_perfil and tiene_dep)

        entrevista_txt = (getattr(c, "entrevista", "") or "")
        entrevista_realizada = bool(str(entrevista_txt).strip())

        solicitudes_count = _safe_len(getattr(c, "solicitudes", None))
        llamadas_count = _safe_len(getattr(c, "llamadas", None))

        if solicitudes_count == 0:
            solicitudes_count = _count_scalar(
                legacy_h.db.session.query(func.count(legacy_h.Solicitud.id)).filter(
                    legacy_h.Solicitud.candidata_id == c.fila
                )
            )

        if llamadas_count == 0:
            llamadas_count = _count_scalar(
                legacy_h.db.session.query(func.count(legacy_h.LlamadaCandidata.id)).filter(
                    legacy_h.LlamadaCandidata.candidata_id == c.fila
                )
            )

        reemplazos_count = _count_scalar(
            legacy_h.db.session.query(func.count(legacy_h.Reemplazo.id)).filter(
                or_(
                    legacy_h.Reemplazo.candidata_old_id == c.fila,
                    legacy_h.Reemplazo.candidata_new_id == c.fila,
                )
            )
        )

        tiene_historial = (solicitudes_count > 0) or (llamadas_count > 0) or (reemplazos_count > 0)
        puede_eliminar = not tiene_historial

        return {
            "tiene_cedula1": tiene_cedula1,
            "tiene_cedula2": tiene_cedula2,
            "tiene_perfil": tiene_perfil,
            "tiene_depuracion": tiene_dep,
            "documentos_completos": documentos_completos,
            "entrevista_realizada": entrevista_realizada,
            "solicitudes_count": int(solicitudes_count or 0),
            "llamadas_count": int(llamadas_count or 0),
            "reemplazos_count": int(reemplazos_count or 0),
            "tiene_historial": tiene_historial,
            "puede_eliminar": puede_eliminar,
        }

    if request.method == "POST" and request.form.get("confirmar_eliminacion"):
        busqueda = (request.form.get("busqueda") or "").strip()[:128]
    else:
        busqueda = (
            (request.form.get("busqueda") if request.method == "POST" else request.args.get("busqueda")) or ""
        ).strip()[:128]

    resultados = []
    candidata = None
    mensaje = None
    docs_info = build_docs_info(None)

    if request.method == "POST" and request.form.get("confirmar_eliminacion"):
        role = (
            str(getattr(current_user, "role", "") or "").strip().lower()
            or str(session.get("role", "") or "").strip().lower()
        )
        if role != "admin":
            mensaje = "❌ Solo admin puede confirmar la eliminación definitiva de candidatas."
            return render_template(
                "candidata_eliminar.html",
                busqueda=busqueda,
                resultados=resultados,
                candidata=None,
                mensaje=mensaje,
                docs_info=docs_info,
            )

        cid = (request.form.get("candidata_id") or "").strip()
        if not cid.isdigit():
            mensaje = "❌ ID de candidata inválido."
        else:
            obj = legacy_h.db.session.get(legacy_h.Candidata, int(cid))
            if not obj:
                mensaje = "⚠️ La candidata ya no existe en la base de datos."
            else:
                docs_info = build_docs_info(obj)
                if docs_info["tiene_historial"]:
                    mensaje = (
                        "⚠️ No se puede eliminar esta candidata porque tiene historial: "
                        f"{docs_info['solicitudes_count']} solicitudes, "
                        f"{docs_info['llamadas_count']} llamadas y "
                        f"{docs_info['reemplazos_count']} reemplazos. "
                        "Recomendación: marcarla como inactiva / no disponible, pero NO borrarla."
                    )
                    candidata = obj
                else:
                    try:
                        nombre_log = obj.nombre_completo
                        cedula_log = obj.cedula
                        codigo_log = obj.codigo

                        legacy_h.db.session.delete(obj)
                        legacy_h.db.session.commit()

                        current_app.logger.info(
                            "✅ Candidata eliminada manualmente: fila=%s, nombre=%s, cedula=%s, codigo=%s",
                            cid, nombre_log, cedula_log, codigo_log
                        )
                        flash("✅ Candidata eliminada correctamente.", "success")
                        return redirect(url_for("eliminar_candidata", busqueda=busqueda or ""))
                    except IntegrityError:
                        legacy_h.db.session.rollback()
                        current_app.logger.exception("❌ FK bloqueó la eliminación de la candidata.")
                        mensaje = (
                            "❌ La base de datos no permitió eliminarla porque está ligada a otros registros. "
                            "Para no dañar el historial, es mejor marcarla como no disponible."
                        )
                        candidata = obj
                        docs_info = build_docs_info(obj)
                    except Exception:
                        legacy_h.db.session.rollback()
                        current_app.logger.exception("❌ Error al eliminar candidata manualmente")
                        mensaje = "❌ Ocurrió un error al eliminar. Intenta de nuevo."
                        candidata = obj
                        docs_info = build_docs_info(obj)

    if not candidata:
        cid = (request.args.get("candidata_id") or "").strip()
        if cid.isdigit():
            candidata = legacy_h.db.session.get(legacy_h.Candidata, int(cid))
            if not candidata:
                mensaje = "⚠️ Candidata no encontrada."
                docs_info = build_docs_info(None)
            else:
                docs_info = build_docs_info(candidata)

    if busqueda and not candidata:
        like = f"%{busqueda}%"
        try:
            resultados = (
                legacy_h.Candidata.query
                .filter(
                    or_(
                        legacy_h.Candidata.codigo.ilike(like),
                        legacy_h.Candidata.nombre_completo.ilike(like),
                        legacy_h.Candidata.cedula.ilike(like),
                        legacy_h.Candidata.numero_telefono.ilike(like),
                    )
                )
                .order_by(legacy_h.Candidata.nombre_completo.asc())
                .limit(100)
                .all()
            )
            if not resultados:
                mensaje = "⚠️ No se encontraron candidatas con ese dato."
        except Exception:
            current_app.logger.exception("❌ Error buscando candidatas para eliminar")
            mensaje = "❌ Ocurrió un error al buscar."

    return render_template(
        "candidata_eliminar.html",
        busqueda=busqueda,
        resultados=resultados,
        candidata=candidata,
        mensaje=mensaje,
        docs_info=docs_info,
    )
