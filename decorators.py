# decorators.py
# -*- coding: utf-8 -*-

from functools import wraps
from flask import abort, flash, redirect, request, session, url_for

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

def _is_authenticated():
    """
    Verificación robusta de autenticación con flask-login.
    Evita AnonymousUser y objetos manipulados.
    """
    try:
        return bool(
            current_user
            and hasattr(current_user, "is_authenticated")
            and current_user.is_authenticated
        )
    except Exception:
        return False


def _safe_next():
    """
    Previene open-redirect.
    Solo permite rutas internas del mismo dominio.
    """
    nxt = request.full_path or request.path or "/"
    if isinstance(nxt, str) and nxt.startswith("/"):
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


def _get_role():
    """
    Obtiene y normaliza el rol del usuario.
    """
    if not current_user:
        return ""
    role = (
        getattr(current_user, "role", None)
        or getattr(current_user, "rol", None)
        or ""
    )
    return str(role).strip().lower()


def _is_admin():
    """
    Determina si el usuario es admin de forma segura.
    """
    if not current_user:
        return False
    return bool(
        getattr(current_user, "is_admin", False)
        or _get_role() == "admin"
    )


# ─────────────────────────────────────────────────────────────
# DECORATORS ADMIN / STAFF
# ─────────────────────────────────────────────────────────────

def admin_required(view_func):
    """
    Acceso SOLO admin.
    """
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not _is_authenticated():
            flash("Debes iniciar sesión.", "warning")
            return _redirect_login("admin.login")

        if not _is_admin():
            abort(403)

        return view_func(*args, **kwargs)
    return wrapper


def staff_required(view_func):
    """
    Acceso admin + secretaria.
    """
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not _is_authenticated():
            flash("Debes iniciar sesión.", "warning")
            return _redirect_login("admin.login")

        role = _get_role()
        if role not in ("admin", "secretaria") and not _is_admin():
            abort(403)

        return view_func(*args, **kwargs)
    return wrapper


# ─────────────────────────────────────────────────────────────
# DECORATORS CLIENTES
# ─────────────────────────────────────────────────────────────

def cliente_required(view_func):
    """
    Requiere login válido y que el usuario sea un Cliente real.
    Evita acceso por role inyectado o sesión manipulada.
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
            # Fallback defensivo si el modelo no está disponible
            if _get_role() != "cliente":
                abort(403)

        return view_func(*args, **kwargs)
    return wrapper


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
            return redirect(url_for("clientes.politicas", next=_safe_next()))

        return view_func(*args, **kwargs)
    return wrapper


# ─────────────────────────────────────────────────────────────
# LEGACY (session-based) — NO TOCAR
# ─────────────────────────────────────────────────────────────

def roles_required(*permitted_roles):
    """
    Decorador legacy por session.
    Mantener solo para rutas antiguas.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if "usuario" not in session:
                abort(401)
            if session.get("role") not in permitted_roles:
                abort(403)
            return view_func(*args, **kwargs)
        return wrapped
    return decorator


# Alias legacy
admin_required_session = roles_required("admin")
