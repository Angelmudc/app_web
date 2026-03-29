# decorators.py
# -*- coding: utf-8 -*-

from functools import wraps
from datetime import datetime, timedelta

from flask import abort, flash, redirect, request, session, url_for, current_app
from utils.timezone import utc_now_naive
from utils.staff_auth import (
    admin_required as _staff_admin_required,
    staff_required as _staff_required_decorator,
    get_staff_role as _get_staff_role,
    redirect_admin_login as _redirect_admin_login,
)
from utils.staff_mfa import session_get_mfa_pending

# ─────────────────────────────────────────────────────────────
# flask-login (carga segura)
# ─────────────────────────────────────────────────────────────
try:
    from flask_login import current_user
except Exception:
    current_user = None


# ─────────────────────────────────────────────────────────────
# Helpers internos (NO exportar)
# ─────────────────────────────────────────────────────────────

def _is_authenticated() -> bool:
    """
    Verificación robusta de autenticación con flask-login.
    Evita AnonymousUser y objetos manipulados.
    """
    try:
        return bool(
            current_user
            and hasattr(current_user, "is_authenticated")
            and bool(current_user.is_authenticated)
        )
    except Exception:
        return False


def _safe_next() -> str:
    """
    Previene open-redirect.
    Solo permite rutas internas seguras.
    """
    nxt = request.full_path or request.path or "/"
    if not isinstance(nxt, str):
        return "/"
    nxt = nxt.strip()

    # Solo rutas internas, nunca // ni externas
    if nxt.startswith("/") and not nxt.startswith("//"):
        return nxt
    return "/"


def _redirect_login(endpoint: str):
    """
    Redirección segura al login correspondiente.
    """
    try:
        return redirect(url_for(endpoint, next=_safe_next()))
    except Exception:
        abort(401)


def _get_role() -> str:
    """
    Obtiene y normaliza el rol del usuario.
    """
    try:
        if not current_user:
            return ""
        role = (
            getattr(current_user, "role", None)
            or getattr(current_user, "rol", None)
            or ""
        )
        return str(role).strip().lower()
    except Exception:
        return ""


def _is_admin() -> bool:
    """
    Determina si el usuario es admin de forma segura.
    """
    try:
        if not current_user:
            return False
        return bool(
            getattr(current_user, "is_admin", False)
            or _get_role() in {"owner", "admin"}
        )
    except Exception:
        return False


def _parse_logged_at(value):
    """
    logged_at viene de:
      session['logged_at'] = utc_now_naive().isoformat(timespec='seconds')
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).strip())
    except Exception:
        return None


def _get_session_ttl_seconds() -> int:
    """
    TTL defensivo:
    - Preferimos config PERMANENT_SESSION_LIFETIME.
    - Si no existe, usamos 30 días.
    """
    try:
        ttl = current_app.config.get("PERMANENT_SESSION_LIFETIME")
        if isinstance(ttl, timedelta):
            return int(ttl.total_seconds())
        if isinstance(ttl, (int, float)) and ttl > 0:
            return int(ttl)
    except Exception:
        pass
    return 60 * 60 * 24 * 30  # 30 días


# ─────────────────────────────────────────────────────────────
# DECORATORS ADMIN / STAFF
# ─────────────────────────────────────────────────────────────

def admin_required(view_func):
    """Acceso SOLO admin (StaffUser o fallback legacy habilitado)."""
    return _staff_admin_required(view_func)


def staff_required(view_func):
    """Acceso admin + secretaria (StaffUser o fallback legacy habilitado)."""
    return _staff_required_decorator(view_func)


# ─────────────────────────────────────────────────────────────
# DECORATORS CLIENTES
# ─────────────────────────────────────────────────────────────

def cliente_required(view_func):
    """
    Requiere login válido y que el usuario sea un Cliente real.
    """
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not _is_authenticated():
            return _redirect_login("clientes.login")

        try:
            from models import Cliente
        except Exception:
            Cliente = None

        if Cliente is not None:
            if not isinstance(current_user, Cliente):
                abort(403)
        else:
            if _get_role() != "cliente":
                abort(403)

        return view_func(*args, **kwargs)

    return wrapper  # ✅ obligatorio


def politicas_requeridas(view_func):
    """
    Obliga a que el cliente haya aceptado las políticas.
    """
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not _is_authenticated():
            return _redirect_login("clientes.login")

        if not getattr(current_user, "acepto_politicas", False):
            flash("Debes aceptar las políticas para continuar.", "warning")
            return redirect(
                url_for("clientes.politicas", next=_safe_next())
            )

        return view_func(*args, **kwargs)
    return wrapper


# ─────────────────────────────────────────────────────────────
# LEGACY (session-based)
# ─────────────────────────────────────────────────────────────

def roles_required(*permitted_roles):
    """Decorador legacy compatible con StaffUser + Flask-Login."""
    permitted = {str(r).strip().lower() for r in permitted_roles if str(r).strip()}
    # Jerarquía oficial staff: owner > admin > secretaria.
    # Owner hereda permisos de cualquier ruta definida para admin/secretaria.
    if {"admin", "secretaria"} & permitted:
        permitted.add("owner")

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if session_get_mfa_pending(session):
                flash("Debes completar MFA para continuar.", "warning")
                return _redirect_admin_login()

            role = _get_staff_role()
            if not role:
                flash("Debes iniciar sesión.", "warning")
                return _redirect_admin_login()

            logged_at = _parse_logged_at(session.get("logged_at"))
            if logged_at:
                ttl_seconds = _get_session_ttl_seconds()
                try:
                    if utc_now_naive() - logged_at > timedelta(seconds=ttl_seconds):
                        try:
                            session.clear()
                        except Exception:
                            pass
                        flash("Tu sesión expiró. Inicia sesión otra vez.", "warning")
                        return _redirect_admin_login()
                except Exception:
                    try:
                        session.clear()
                    except Exception:
                        pass
                    flash("Tu sesión expiró. Inicia sesión otra vez.", "warning")
                    return _redirect_admin_login()

            if role not in permitted:
                abort(403)

            return view_func(*args, **kwargs)
        return wrapped
    return decorator


admin_required_session = roles_required("admin")

# Compatibilidad de nombres legacy (no romper imports antiguos).
login_required_admin = admin_required
secretaria_or_admin_required = staff_required
secretaria_required = staff_required


def login_required_any(view_func):
    """
    Requiere autenticación por Flask-Login o sesión legacy.
    No fuerza rol específico; útil para rutas sensibles compartidas.
    """
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if session_get_mfa_pending(session):
            flash("Debes completar MFA para continuar.", "warning")
            return _redirect_admin_login()

        # Flask-Login
        if _is_authenticated():
            return view_func(*args, **kwargs)

        # Sesión legacy
        usuario = session.get("usuario")
        role = (session.get("role") or "").strip().lower()
        if usuario and role:
            return view_func(*args, **kwargs)

        flash("Debes iniciar sesión.", "warning")
        return _redirect_login("login")

    return wrapper
