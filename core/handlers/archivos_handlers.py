# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import re

from flask import Response, abort, current_app, flash, redirect, render_template, request, send_file, session, url_for
from flask_login import current_user
from sqlalchemy.exc import DBAPIError, OperationalError

from config_app import db
from decorators import roles_required
from models import Candidata
from utils.audit_entity import log_candidata_action
from utils.candidata_readiness import maybe_update_estado_por_completitud
from utils.robust_save import binary_has_content, execute_robust_save, safe_bytes_length
from utils.upload_limits import MAX_FILE_BYTES, file_too_large, get_filestorage_size, human_size
from utils.upload_security import validate_upload_file

from core import legacy_handlers as legacy_h
from core.services.search import apply_search_to_candidata_query


ALLOWED_IMG_FIELDS = ("depuracion", "perfil", "cedula1", "cedula2")


def _retry_query(callable_fn, retries: int = 2, swallow: bool = False):
    last_err = None
    for _ in range(retries + 1):
        try:
            return callable_fn()
        except (OperationalError, DBAPIError) as exc:
            try:
                db.session.rollback()
            except Exception:
                pass
            last_err = exc
            continue
    if swallow:
        return None
    raise last_err


def _get_candidata_by_fila_or_pk(fila_id: int):
    if not fila_id:
        return None

    cand = None
    try:
        cand = db.session.get(Candidata, fila_id)
    except Exception:
        cand = None

    if cand:
        return cand

    try:
        return Candidata.query.filter_by(fila=fila_id).first()
    except Exception:
        return None


def _build_docs_flags(cand):
    if not cand:
        return {k: False for k in ALLOWED_IMG_FIELDS}
    return {
        "depuracion": bool(getattr(cand, "depuracion", None)),
        "perfil": bool(getattr(cand, "perfil", None)),
        "cedula1": bool(getattr(cand, "cedula1", None)),
        "cedula2": bool(getattr(cand, "cedula2", None)),
    }


def _upload_limits_view_context() -> dict:
    max_file_bytes = int(MAX_FILE_BYTES(current_app))
    max_file_mb = max_file_bytes / float(1024 * 1024)
    total_limit = int(current_app.config.get("MAX_CONTENT_LENGTH") or 0)
    return {
        "upload_max_file_bytes": max_file_bytes,
        "upload_max_file_mb": f"{max_file_mb:.1f}",
        "upload_total_limit_text": human_size(total_limit) if total_limit > 0 else "sin límite",
    }


def _safe_seek_upload(file_storage) -> None:
    try:
        if file_storage is not None and getattr(file_storage, "stream", None) is not None:
            file_storage.stream.seek(0)
    except Exception:
        return


def _verify_candidata_docs_saved(candidata_id: int, expected_fields: dict[str, int]) -> bool:
    cand = _get_candidata_by_fila_or_pk(candidata_id)
    if not cand:
        return False
    for field_name in (expected_fields or {}).keys():
        val = getattr(cand, field_name, None)
        if not binary_has_content(val):
            return False
    return True


def _current_staff_actor() -> str:
    return str(
        getattr(current_user, "username", None)
        or getattr(current_user, "id", None)
        or session.get("usuario")
        or "sistema"
    )


def _to_bytes(data):
    if data is None:
        return None
    if isinstance(data, memoryview):
        return data.tobytes()
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    try:
        return bytes(data)
    except Exception:
        return None


def _detect_mimetype_and_ext(data: bytes):
    if not data:
        return ("application/octet-stream", "bin")
    head = data[:12]
    if head.startswith(b"\x89PNG"):
        return ("image/png", "png")
    if head.startswith(b"\xFF\xD8\xFF"):
        return ("image/jpeg", "jpg")
    if head[:4] == b"GIF8":
        return ("image/gif", "gif")
    if head[:4] == b"%PDF":
        return ("application/pdf", "pdf")
    return ("application/octet-stream", "bin")


@roles_required("admin", "secretaria")
def subir_fotos():
    accion = (request.args.get("accion") or "buscar").strip()
    fila_id = request.args.get("fila", type=int)
    next_url = (request.values.get("next") or "").strip()
    if not legacy_h._is_safe_next(next_url):
        next_url = ""
    resultados = []

    if accion == "buscar":
        if request.method == "POST":
            q = (request.form.get("busqueda") or "").strip()[:128]
            if not q:
                flash("⚠️ Ingresa algo para buscar.", "warning")
                return redirect(url_for("subir_fotos.subir_fotos", accion="buscar", next=next_url or None))

            try:
                filas = (
                    apply_search_to_candidata_query(Candidata.query, q)
                    .order_by(Candidata.nombre_completo.asc())
                    .limit(300)
                    .all()
                )
            except Exception:
                current_app.logger.exception("❌ Error buscando en subir_fotos")
                filas = []

            if not filas:
                flash("⚠️ No se encontraron candidatas.", "warning")
            else:
                resultados = [
                    {
                        "fila": c.fila,
                        "nombre": c.nombre_completo,
                        "telefono": c.numero_telefono or "No especificado",
                        "cedula": c.cedula or "No especificado",
                    }
                    for c in filas
                ]

        return render_template(
            "subir_fotos.html",
            accion="buscar",
            resultados=resultados,
            next_url=next_url,
            **_upload_limits_view_context(),
        )

    if accion == "subir":
        if not fila_id:
            flash("❌ Debes seleccionar primero una candidata.", "danger")
            return redirect(url_for("subir_fotos.subir_fotos", accion="buscar", next=next_url or None))

        candidata = _get_candidata_by_fila_or_pk(fila_id)
        if not candidata:
            flash("⚠️ Candidata no encontrada.", "warning")
            return redirect(url_for("subir_fotos.subir_fotos", accion="buscar", next=next_url or None))

        if request.method == "GET":
            tiene = _build_docs_flags(candidata)
            return render_template(
                "subir_fotos.html",
                accion="subir",
                fila=fila_id,
                tiene=tiene,
                next_url=next_url,
                **_upload_limits_view_context(),
            )

        files = {
            "depuracion": request.files.get("depuracion"),
            "perfil": request.files.get("perfil"),
            "cedula1": request.files.get("cedula1"),
            "cedula2": request.files.get("cedula2"),
        }

        archivos_validos = {}
        for campo, archivo in files.items():
            if campo in ALLOWED_IMG_FIELDS and archivo and archivo.filename:
                archivos_validos[campo] = archivo

        if not archivos_validos:
            flash("⚠️ Debes seleccionar al menos una imagen para subir.", "warning")
            tiene = _build_docs_flags(candidata)
            return render_template(
                "subir_fotos.html",
                accion="subir",
                fila=fila_id,
                tiene=tiene,
                next_url=next_url,
                **_upload_limits_view_context(),
            )

        try:
            max_file = int(MAX_FILE_BYTES(current_app))
            payload_bytes = {}

            for campo, archivo in archivos_validos.items():
                if not archivo or not getattr(archivo, "filename", ""):
                    continue
                if file_too_large(archivo, max_file):
                    detected_size = int(get_filestorage_size(archivo) or 0)
                    max_txt = human_size(max_file)
                    size_txt = human_size(detected_size)
                    log_candidata_action(
                        action_type="CANDIDATA_UPLOAD_DOCS_SIZE_REJECT",
                        candidata=candidata,
                        summary=f"Archivo rechazado por tamaño en {campo}",
                        metadata={
                            "candidata_id": int(fila_id),
                            "field": campo,
                            "max_bytes": int(max_file),
                            "size_bytes": int(detected_size),
                            "source": "subir_fotos",
                        },
                        success=False,
                        error="Archivo supera límite por campo.",
                    )
                    flash(f"Archivo demasiado pesado para {campo}. Máximo: {max_txt}. Tu archivo: {size_txt}.", "danger")
                    return redirect(url_for("subir_fotos.subir_fotos", accion="subir", fila=fila_id, next=next_url or None))

            for campo, archivo in archivos_validos.items():
                _safe_seek_upload(archivo)
                ok, data, err, meta = validate_upload_file(archivo, max_bytes=max_file)
                if not ok:
                    current_app.logger.warning(
                        "⚠️ Upload inválido campo=%s archivo=%s motivo=%s",
                        campo,
                        (meta or {}).get("filename_safe", ""),
                        err,
                    )
                    flash(f"❌ Archivo inválido en {campo}: {err}", "danger")
                    tiene = _build_docs_flags(candidata)
                    return render_template(
                        "subir_fotos.html",
                        accion="subir",
                        fila=fila_id,
                        tiene=tiene,
                        next_url=next_url,
                        **_upload_limits_view_context(),
                    )
                if safe_bytes_length(data) <= 0:
                    flash(f"❌ Archivo inválido en {campo}: el archivo está vacío.", "danger")
                    tiene = _build_docs_flags(candidata)
                    return render_template(
                        "subir_fotos.html",
                        accion="subir",
                        fila=fila_id,
                        tiene=tiene,
                        next_url=next_url,
                        **_upload_limits_view_context(),
                    )
                payload_bytes[campo] = data

            if not payload_bytes:
                flash("⚠️ Debes seleccionar al menos una imagen para subir.", "warning")
                tiene = _build_docs_flags(candidata)
                return render_template(
                    "subir_fotos.html",
                    accion="subir",
                    fila=fila_id,
                    tiene=tiene,
                    next_url=next_url,
                    **_upload_limits_view_context(),
                )

            def _persist_docs(_attempt: int):
                for campo, data in payload_bytes.items():
                    setattr(candidata, campo, data)
                try:
                    maybe_update_estado_por_completitud(candidata, actor=_current_staff_actor())
                except Exception:
                    pass

            expected_lengths = {k: safe_bytes_length(v) for k, v in payload_bytes.items()}
            result = execute_robust_save(
                session=db.session,
                persist_fn=_persist_docs,
                verify_fn=lambda: _verify_candidata_docs_saved(int(fila_id), expected_lengths),
            )

            metadata_base = {
                "candidata_id": int(fila_id),
                "fields": sorted(list(payload_bytes.keys())),
                "source": "subir_fotos",
                "attempt_count": int(result.attempts),
                "bytes_length": expected_lengths,
            }
            if not result.ok:
                current_app.logger.error(
                    "❌ Error guardando imágenes (robust_save) fila=%s attempts=%s error=%s",
                    fila_id,
                    result.attempts,
                    result.error_message,
                )
                log_candidata_action(
                    action_type="CANDIDATA_UPLOAD_DOCS_SAVE_FAIL",
                    candidata=candidata,
                    summary="Fallo guardado robusto de documentos de candidata",
                    metadata={**metadata_base, "error_message": (result.error_message or "")[:200]},
                    success=False,
                    error="No se pudo guardar documentos de forma verificada.",
                )
                log_candidata_action(
                    action_type="CANDIDATA_UPLOAD_DOCS",
                    candidata=candidata,
                    summary="Fallo al subir/actualizar documentos de candidata",
                    metadata={"fields": sorted(list(payload_bytes.keys())), "source": "subir_fotos"},
                    success=False,
                    error="Error guardando imágenes en base de datos.",
                )
                flash("No se pudo guardar. Intente de nuevo. Si persiste, contacte admin.", "danger")
                tiene = _build_docs_flags(candidata)
                return render_template(
                    "subir_fotos.html",
                    accion="subir",
                    fila=fila_id,
                    tiene=tiene,
                    next_url=next_url,
                    **_upload_limits_view_context(),
                )

            log_candidata_action(
                action_type="CANDIDATA_UPLOAD_DOCS_SAVE_OK",
                candidata=candidata,
                summary="Guardado robusto de documentos de candidata",
                metadata=metadata_base,
                success=True,
            )
            log_candidata_action(
                action_type="CANDIDATA_UPLOAD_DOCS",
                candidata=candidata,
                summary="Carga/actualización de documentos de candidata",
                metadata={
                    "fields": sorted(list(payload_bytes.keys())),
                    "source": "subir_fotos",
                },
                success=True,
            )
            flash("✅ Imágenes subidas/actualizadas correctamente.", "success")
            if next_url:
                return redirect(next_url)
            return redirect(url_for("subir_fotos.subir_fotos", accion="buscar"))

        except Exception:
            db.session.rollback()
            current_app.logger.exception("❌ Error guardando imágenes en la BD")
            log_candidata_action(
                action_type="CANDIDATA_UPLOAD_DOCS_SAVE_FAIL",
                candidata=candidata,
                summary="Fallo guardado robusto de documentos de candidata",
                metadata={
                    "candidata_id": int(fila_id),
                    "fields": sorted(list(archivos_validos.keys())),
                    "source": "subir_fotos",
                    "attempt_count": 1,
                    "bytes_length": {},
                },
                success=False,
                error="No se pudo guardar documentos de forma verificada.",
            )
            flash("No se pudo guardar. Intente de nuevo. Si persiste, contacte admin.", "danger")
            tiene = _build_docs_flags(candidata)
            return render_template(
                "subir_fotos.html",
                accion="subir",
                fila=fila_id,
                tiene=tiene,
                next_url=next_url,
                **_upload_limits_view_context(),
            )

    return redirect(url_for("subir_fotos.subir_fotos", accion="buscar", next=next_url or None))


@roles_required("admin", "secretaria")
def ver_imagen(fila, campo):
    if campo not in ALLOWED_IMG_FIELDS:
        abort(404)

    cand = _get_candidata_by_fila_or_pk(fila)
    if not cand:
        abort(404)

    data = _to_bytes(getattr(cand, campo, None))
    if not data:
        abort(404)

    mt, _ext = _detect_mimetype_and_ext(data)
    if not mt.startswith("image/"):
        abort(404)

    return Response(data, mimetype=mt)


@roles_required("admin", "secretaria")
def descargar_uno_db():
    cid = request.args.get("id", type=int)
    doc = (request.args.get("doc") or "").strip().lower()

    if not cid or doc not in ("depuracion", "perfil", "cedula1", "cedula2"):
        return "Error: parámetros inválidos", 400

    def _load():
        return db.session.get(Candidata, cid)

    try:
        candidata = _retry_query(_load, retries=1, swallow=False)
    except Exception:
        current_app.logger.exception("❌ Error consultando candidata en descargar_uno_db")
        candidata = None

    if not candidata:
        return "Candidata no encontrada", 404

    data = getattr(candidata, doc, None)
    if not data:
        return f"No hay archivo para {doc}", 404

    if isinstance(data, memoryview):
        data = data.tobytes()
    elif isinstance(data, bytearray):
        data = bytes(data)
    elif not isinstance(data, (bytes,)):
        try:
            data = bytes(data)
        except Exception:
            return "Formato de archivo inválido.", 400

    head = data[:12]
    if head.startswith(b"\x89PNG"):
        mt, ext = "image/png", "png"
    elif head.startswith(b"\xFF\xD8\xFF"):
        mt, ext = "image/jpeg", "jpg"
    elif head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        mt, ext = "image/gif", "gif"
    elif head.startswith(b"%PDF"):
        mt, ext = "application/pdf", "pdf"
    else:
        mt, ext = "application/octet-stream", "bin"

    nombre = (getattr(candidata, "nombre_completo", "") or "").strip()
    if not nombre:
        nombre = f"fila_{cid}"

    safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", nombre)[:60].strip("_")
    filename = f"{doc}_{safe_name}_{cid}.{ext}"

    bio = io.BytesIO(data)
    bio.seek(0)

    current_app.logger.info("⬇️ Descargando doc=%s fila=%s nombre=%s", doc, cid, nombre)

    return send_file(
        bio,
        mimetype=mt,
        as_attachment=True,
        download_name=filename,
    )
