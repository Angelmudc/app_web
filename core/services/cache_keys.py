from __future__ import annotations

from flask import request, session


def _cache_key_with_role(prefix: str):
    """Genera cache-key aislada por rol + querystring para evitar mezclar vistas."""
    role = session.get("role") or "anon"
    try:
        path_qs = request.full_path or request.path or ""
    except Exception:
        path_qs = ""
    return f"{prefix}:{role}:{path_qs}"
