# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from typing import Any

from flask import g, request, session

from config_app import cache
from models import StaffUser
from utils.audit_logger import log_action


ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_SECRETARIA = "secretaria"


def normalize_role(role_raw: str | None) -> str:
    role = (role_raw or "").strip().lower()
    if role in {ROLE_OWNER, ROLE_ADMIN, ROLE_SECRETARIA}:
        return role
    if role in {"secretary", "secre", "secretaría"}:
        return ROLE_SECRETARIA
    return ROLE_SECRETARIA


def _split_emails(raw: str | None) -> set[str]:
    out: set[str] = set()
    for chunk in (raw or "").split(","):
        val = chunk.strip().lower()
        if val:
            out.add(val)
    return out


def role_for_user(user: Any = None) -> str:
    role = normalize_role(getattr(user, "role", None) if user is not None else None)

    # Fallback por ENV (solo si viene por username/email, sin tocar BD):
    # útil para despliegues donde todavía no se asignó role explícito.
    email = (getattr(user, "email", None) or "").strip().lower() if user is not None else ""
    owner_email = (os.getenv("OWNER_EMAIL") or "").strip().lower()
    admin_emails = _split_emails(os.getenv("ADMIN_EMAILS"))

    if owner_email and email and email == owner_email:
        return ROLE_OWNER
    if email and email in admin_emails and role == ROLE_SECRETARIA:
        return ROLE_ADMIN

    if user is None:
        sess_role = normalize_role(session.get("role"))
        if sess_role in {ROLE_OWNER, ROLE_ADMIN, ROLE_SECRETARIA}:
            return sess_role

    return role


def is_staff_role(role: str | None) -> bool:
    return normalize_role(role) in {ROLE_OWNER, ROLE_ADMIN, ROLE_SECRETARIA}


def has_admin_access(role: str | None) -> bool:
    return normalize_role(role) in {ROLE_OWNER, ROLE_ADMIN}


def can(role: str | None, permission: str) -> bool:
    r = normalize_role(role)
    perm = (permission or "").strip().lower()
    if not perm:
        return False

    if r == ROLE_OWNER:
        return True

    admin_perms = {
        "admin:seguridad",
        "admin:errores",
        "admin:health",
        "admin:metricas",
        "admin:monitoreo",
        "admin:lock_takeover",
    }
    secretaria_perms = {
        "operaciones:base",
    }

    if r == ROLE_ADMIN:
        if perm in {"admin:roles", "admin:alert_channels"}:
            return False
        return (perm in admin_perms) or (perm in secretaria_perms)

    if r == ROLE_SECRETARIA:
        return perm in secretaria_perms

    return False


def permission_required_for_path(path: str) -> str | None:
    p = (path or "").strip().lower()
    if not p.startswith("/admin/"):
        return None

    # Presence ping se valida en la propia vista para staff autenticado.
    if p.startswith("/admin/monitoreo/presence/ping"):
        return None

    if p.startswith("/admin/roles"):
        return "admin:roles"
    if p.startswith("/admin/alertas/canales"):
        return "admin:alert_channels"
    if p.startswith("/admin/seguridad/"):
        return "admin:seguridad"
    if p.startswith("/admin/errores"):
        return "admin:errores"
    if p.startswith("/admin/health"):
        return "admin:health"
    if p.startswith("/admin/metricas"):
        return "admin:metricas"
    if p.startswith("/admin/monitoreo"):
        return "admin:monitoreo"

    return None


def _cache_get(key: str, default=None):
    try:
        return cache.get(key)
    except Exception:
        return default


def _cache_set(key: str, value, timeout: int):
    try:
        cache.set(key, value, timeout=timeout)
    except Exception:
        return None


def log_permission_denied(*, user: Any, required_permission: str, dedupe_seconds: int = 150) -> None:
    uid = None
    username = None
    role = role_for_user(user)
    try:
        if isinstance(user, StaffUser):
            uid = int(user.id)
            username = user.username
    except Exception:
        uid = None

    path = (request.path or "")[:255]
    dedupe_key = f"rbac:deny:{uid or username or 'anon'}:{path}:{required_permission}"
    if _cache_get(dedupe_key, default=False):
        return
    _cache_set(dedupe_key, 1, timeout=max(30, int(dedupe_seconds)))

    log_action(
        action_type="PERMISSION_DENIED",
        entity_type="security",
        entity_id=(str(uid) if uid is not None else None),
        summary="Intento de acceso a ruta restringida",
        metadata={
            "role": role,
            "path": path,
            "required_permission": required_permission,
            "username": username,
        },
        success=False,
        error="No autorizado para esta sección.",
    )
    try:
        g._authz_denied_logged = True
    except Exception:
        pass
