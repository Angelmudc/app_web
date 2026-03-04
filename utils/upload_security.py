# -*- coding: utf-8 -*-
import os
from typing import Optional, Tuple, Dict, Any

from werkzeug.utils import secure_filename


DEFAULT_ALLOWED_EXTS = {"jpg", "jpeg", "png"}
DEFAULT_ALLOWED_MIMES = {"image/jpeg", "image/png"}


def _is_true(v: str) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "on"}


def _detect_mimetype(data: bytes) -> str:
    if not data:
        return "application/octet-stream"
    head = data[:12]
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"\xFF\xD8\xFF"):
        return "image/jpeg"
    if head[:4] == b"%PDF":
        return "application/pdf"
    return "application/octet-stream"


def _allowed_from_env() -> Tuple[set, set]:
    max_exts = (os.getenv("UPLOAD_ALLOWED_EXTS") or "jpg,jpeg,png").strip().lower()
    allowed_exts = {x.strip() for x in max_exts.split(",") if x.strip()}
    if not allowed_exts:
        allowed_exts = set(DEFAULT_ALLOWED_EXTS)

    allowed_mimes = set(DEFAULT_ALLOWED_MIMES)
    if "pdf" in allowed_exts:
        allowed_mimes.add("application/pdf")

    # Mantener PDF opcional para no romper preview de imagen en flujos legacy
    if _is_true(os.getenv("UPLOAD_ALLOW_PDF", "0")):
        allowed_exts.add("pdf")
        allowed_mimes.add("application/pdf")

    return allowed_exts, allowed_mimes


def validate_upload_file(file_storage, max_bytes: Optional[int] = None) -> Tuple[bool, Optional[bytes], Optional[str], Dict[str, Any]]:
    """
    Valida archivo de subida de forma centralizada.

    Retorna: (ok, bytes|None, error|None, meta)
    meta incluye: filename_safe, ext, mimetype
    """
    if not file_storage:
        return False, None, "Archivo no recibido.", {}

    raw_name = (getattr(file_storage, "filename", "") or "").strip()
    filename_safe = secure_filename(raw_name)
    if not filename_safe:
        return False, None, "Nombre de archivo inválido.", {}

    if "." not in filename_safe:
        return False, None, "El archivo no tiene extensión.", {"filename_safe": filename_safe}

    ext = filename_safe.rsplit(".", 1)[1].lower().strip()
    allowed_exts, allowed_mimes = _allowed_from_env()
    if ext not in allowed_exts:
        return False, None, f"Extensión no permitida: .{ext}", {"filename_safe": filename_safe, "ext": ext}

    try:
        data = file_storage.read() or b""
    except Exception:
        return False, None, "No se pudo leer el archivo.", {"filename_safe": filename_safe, "ext": ext}

    if not data:
        return False, None, "El archivo está vacío.", {"filename_safe": filename_safe, "ext": ext}

    max_bytes = int(max_bytes or int(os.getenv("MAX_IMAGE_BYTES", str(5 * 1024 * 1024))))
    if len(data) > max_bytes:
        return False, None, f"Archivo demasiado grande. Máximo {max_bytes // (1024 * 1024)}MB.", {
            "filename_safe": filename_safe,
            "ext": ext,
        }

    mimetype = _detect_mimetype(data)
    if mimetype not in allowed_mimes:
        return False, None, "Mimetype no permitido.", {
            "filename_safe": filename_safe,
            "ext": ext,
            "mimetype": mimetype,
        }

    return True, data, None, {
        "filename_safe": filename_safe,
        "ext": ext,
        "mimetype": mimetype,
    }
