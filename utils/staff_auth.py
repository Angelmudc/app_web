# -*- coding: utf-8 -*-

import os
from functools import wraps

from flask import abort, flash, redirect, request, session, url_for

try:
    from flask_login import current_user
except Exception:
    current_user = None

from config_app import USUARIOS


def _is_true_env(value: str, default: bool = False) -> bool:
    raw = (value or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def admin_legacy_enabled() -> bool:
    return _is_true_env(os.getenv("ADMIN_LEGACY_ENABLED", "1"), default=True)


def _safe_next() -> str:
    nxt = request.full_path or request.path or "/"
    if not isinstance(nxt, str):
        return "/"
    nxt = nxt.strip()
    if nxt.startswith("/") and not nxt.startswith("//"):
        return nxt
    return "/"


def redirect_admin_login():
    return redirect(url_for("admin.login", next=_safe_next()))


def _legacy_user_exists(username: str) -> bool:
    uname = (username or "").strip()
    if not uname:
        return False
    if uname in USUARIOS:
        return True
    ul = uname.lower()
    return any(str(k).strip().lower() == ul for k in (USUARIOS or {}).keys())


def _current_user_role() -> str:
    try:
        if not current_user:
            return ""
        role = getattr(current_user, "role", None) or getattr(current_user, "rol", None) or ""
        return str(role).strip().lower()
    except Exception:
        return ""


def _is_staff_user_model() -> bool:
    try:
        if not current_user or not getattr(current_user, "is_authenticated", False):
            return False
        uid = str(current_user.get_id() or "").strip()
        return uid.startswith("staff:")
    except Exception:
        return False


def _is_legacy_staff_login() -> bool:
    if not admin_legacy_enabled():
        return False

    # Flask-Login (legacy user cargado por user_loader)
    try:
        if current_user and getattr(current_user, "is_authenticated", False):
            uid = str(current_user.get_id() or "").strip()
            if _legacy_user_exists(uid):
                return True
    except Exception:
        pass

    # Fallback por sesión antigua
    uname = (session.get("usuario") or "").strip()
    role = (session.get("role") or "").strip().lower()
    return bool(uname and role in ("admin", "secretaria") and _legacy_user_exists(uname))


def get_staff_role() -> str:
    role = _current_user_role()
    if role not in ("admin", "secretaria"):
        return ""

    if _is_staff_user_model():
        try:
            if hasattr(current_user, "is_active") and not bool(current_user.is_active):
                return ""
        except Exception:
            return ""
        return role

    if _is_legacy_staff_login():
        return role

    return ""


def staff_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not get_staff_role():
            flash("Debes iniciar sesión.", "warning")
            return redirect_admin_login()
        return view_func(*args, **kwargs)
    return wrapper


def admin_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        role = get_staff_role()
        if not role:
            flash("Debes iniciar sesión.", "warning")
            return redirect_admin_login()
        if role != "admin":
            abort(403)
        return view_func(*args, **kwargs)
    return wrapper


def secretaria_or_admin_required(view_func):
    return staff_required(view_func)
