# -*- coding: utf-8 -*-

import os
from datetime import datetime, timedelta
from functools import wraps

from flask import abort, current_app, flash, redirect, request, session, url_for
from flask_login import UserMixin
from werkzeug.security import check_password_hash

try:
    from flask_login import current_user
except Exception:
    current_user = None

BREAKGLASS_USER_ID = "breakglass"


class BreakglassUser(UserMixin):
    id = BREAKGLASS_USER_ID
    role = "admin"
    is_active = True
    is_anonymous = False

    @property
    def is_authenticated(self):
        return True

    def get_id(self):
        return BREAKGLASS_USER_ID


def _is_true_env(value: str, default: bool = False) -> bool:
    raw = (value or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


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


def is_breakglass_enabled() -> bool:
    return _is_true_env(os.getenv("BREAKGLASS_ENABLED", "0"), default=False)


def breakglass_username() -> str:
    return (os.getenv("BREAKGLASS_USERNAME") or BREAKGLASS_USER_ID).strip() or BREAKGLASS_USER_ID


def breakglass_password_hash() -> str:
    return (os.getenv("BREAKGLASS_PASSWORD_HASH") or "").strip()


def breakglass_ttl_seconds() -> int:
    try:
        return max(60, int((os.getenv("BREAKGLASS_SESSION_TTL_SECONDS") or "3600").strip()))
    except Exception:
        return 3600


def breakglass_allowed_ip(ip: str) -> bool:
    allow_raw = (os.getenv("BREAKGLASS_ALLOWED_IPS") or "").strip()
    # Breakglass 100% amarrado por IP: si no hay allowlist, NO permite.
    if not allow_raw:
        return False
    allow = {x.strip() for x in allow_raw.split(",") if x.strip()}
    return (ip or "").strip() in allow


def get_request_ip() -> str:
    trust_xff = _is_true_env(os.getenv("TRUST_XFF", "0"), default=False)
    if trust_xff:
        cf_ip = (request.headers.get("CF-Connecting-IP") or "").strip()
        if cf_ip:
            return cf_ip[:64]

        x_real = (request.headers.get("X-Real-IP") or "").strip()
        if x_real:
            return x_real[:64]

        xff = (request.headers.get("X-Forwarded-For") or "").strip()
        if xff:
            return xff.split(",")[0].strip()[:64]

    return (request.remote_addr or "0.0.0.0").strip()[:64]


def check_breakglass_password(raw_password: str) -> bool:
    pwd_hash = breakglass_password_hash()
    if not pwd_hash:
        return False
    try:
        return check_password_hash(pwd_hash, raw_password or "")
    except Exception:
        return False


def breakglass_is_expired(sess=None) -> bool:
    s = sess if sess is not None else session
    expires_at = s.get("breakglass_expires_at")
    if not expires_at:
        return True
    try:
        return datetime.utcnow() >= datetime.fromisoformat(str(expires_at).strip())
    except Exception:
        return True


def is_breakglass_session_valid(sess=None) -> bool:
    s = sess if sess is not None else session
    if not bool(s.get("is_breakglass")):
        return False
    return not breakglass_is_expired(s)


def set_breakglass_session(sess=None):
    s = sess if sess is not None else session
    ttl = breakglass_ttl_seconds()
    expires_at = datetime.utcnow() + timedelta(seconds=ttl)
    s["is_breakglass"] = True
    s["breakglass_expires_at"] = expires_at.isoformat(timespec="seconds")
    s["is_admin_session"] = True
    s["usuario"] = breakglass_username()
    s["role"] = "admin"


def clear_breakglass_session(sess=None):
    s = sess if sess is not None else session
    s.pop("is_breakglass", None)
    s.pop("breakglass_expires_at", None)


def build_breakglass_user():
    return BreakglassUser()


def is_breakglass_user_obj(user_obj=None) -> bool:
    u = user_obj if user_obj is not None else current_user
    try:
        if not u or not getattr(u, "is_authenticated", False):
            return False
        return str(u.get_id() or "").strip() == BREAKGLASS_USER_ID
    except Exception:
        return False


def log_breakglass_attempt(success: bool, ip: str, ua: str):
    msg = (
        f"BREAKGLASS LOGIN {'SUCCESS' if success else 'FAIL'} "
        f"ip={ip or '-'} ua={(ua or '-')[:240]}"
    )
    try:
        current_app.logger.warning(msg)
    except Exception:
        pass


def _current_user_role() -> str:
    try:
        if not current_user:
            return ""
        role = getattr(current_user, "role", None) or getattr(current_user, "rol", None) or ""
        return str(role).strip().lower()
    except Exception:
        return ""


def _normalize_staff_role_name(role_value: str) -> str:
    role = (role_value or "").strip().lower()
    if not role:
        return ""
    if role in ("owner",):
        return "owner"
    if role in ("admin",):
        return "admin"
    if role in ("secretaria", "secretary", "secre", "secretaría"):
        return "secretaria"
    return role


def _is_staff_user_model() -> bool:
    try:
        if not current_user or not getattr(current_user, "is_authenticated", False):
            return False
        uid = str(current_user.get_id() or "").strip()
        return uid.startswith("staff:")
    except Exception:
        return False


def get_staff_role() -> str:
    role = _normalize_staff_role_name(_current_user_role())

    if role == "admin" and is_breakglass_user_obj() and is_breakglass_session_valid():
        return "admin"

    if role not in ("owner", "admin", "secretaria"):
        return ""

    if _is_staff_user_model():
        try:
            if hasattr(current_user, "is_active") and not bool(current_user.is_active):
                return ""
        except Exception:
            return ""
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
        if role not in ("owner", "admin"):
            abort(403)
        return view_func(*args, **kwargs)
    return wrapper


def secretaria_or_admin_required(view_func):
    return staff_required(view_func)
