# decorators.py
# -*- coding: utf-8 -*-

from functools import wraps
from flask import abort, flash, redirect, request, session, url_for

# Intentamos usar flask-login si existe (tu proyecto lo usa en admin y clientes)
try:
    from flask_login import current_user
except Exception:
    current_user = None


def _is_logged_flask_login():
    return bool(current_user and getattr(current_user, "is_authenticated", False))


def _get_role_flask_login():
    if not current_user:
        return ""
    role = (
        getattr(current_user, "role", None)
        or getattr(current_user, "rol", None)
        or ""
    )
    return str(role).strip().lower()


def _is_admin_flask_login():
    if not current_user:
        return False
    role = _get_role_flask_login()
    is_admin_flag = bool(getattr(current_user, "is_admin", False))
    return is_admin_flag or role == "admin"


def _redirect_to_login(login_endpoint: str):
    # next: siempre mejor con request.full_path o request.url
    nxt = request.url
    try:
        return redirect(url_for(login_endpoint, next=nxt))
    except Exception:
        # Si por alguna razón falla el endpoint, abort 401
        return abort(401)


# =============================================================================
# ✅ Decorators para ADMIN / STAFF usando flask-login
# =============================================================================

def admin_required(view_func):
    """
    Solo Admin.
    - Requiere flask-login.
    - Acepta: current_user.role == 'admin' o current_user.is_admin == True
    - Redirige al login admin si no está autenticado.
    """
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not _is_logged_flask_login():
            flash("Debes iniciar sesión.", "warning")
            return _redirect_to_login("admin.login")

        if not _is_admin_flask_login():
            abort(403)

        return view_func(*args, **kwargs)
    return wrapper


def staff_required(view_func):
    """
    Admin + Secretaria.
    - Requiere flask-login.
    - Permite role: admin, secretaria o is_admin True.
    """
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not _is_logged_flask_login():
            flash("Debes iniciar sesión.", "warning")
            return _redirect_to_login("admin.login")

        role = _get_role_flask_login()
        if role not in ("admin", "secretaria") and not _is_admin_flask_login():
            abort(403)

        return view_func(*args, **kwargs)
    return wrapper


# =============================================================================
# ✅ Decorators para CLIENTES usando flask-login (con check robusto)
# =============================================================================

def cliente_required(view_func):
    """
    Requiere que el usuario autenticado sea un Cliente.
    - Por defecto redirige a clientes.login
    - Evita romper si Cliente no se puede importar (carga perezosa).
    """
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not _is_logged_flask_login():
            return _redirect_to_login("clientes.login")

        # Import perezoso para evitar imports circulares
        try:
            from models import Cliente  # <-- tu clase Cliente en models.py
        except Exception:
            Cliente = None

        if Cliente is not None:
            if not isinstance(current_user, Cliente):
                return _redirect_to_login("clientes.login")
        else:
            # Fallback si no podemos importar Cliente:
            # intentamos detectar por atributos típicos
            role = _get_role_flask_login()
            if role not in ("cliente", "client"):
                return _redirect_to_login("clientes.login")

        return view_func(*args, **kwargs)
    return wrapper


def politicas_requeridas(view_func):
    """
    Obliga a que el cliente haya aceptado políticas.
    Depende de que ya esté logueado como cliente.
    """
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not _is_logged_flask_login():
            return _redirect_to_login("clientes.login")

        if not getattr(current_user, "acepto_politicas", False):
            flash("Debes aceptar las políticas para continuar.", "warning")
            return redirect(url_for("clientes.politicas", next=request.url))

        return view_func(*args, **kwargs)
    return wrapper


# =============================================================================
# ✅ Decorator LEGACY por session (para tu decorators.py viejo)
# =============================================================================

def roles_required(*permitted_roles):
    """
    Decorador por session (legacy):
      - Usa session['usuario']
      - Usa session['role']
    Ideal para rutas antiguas que todavía no migraste a flask-login.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if "usuario" not in session:
                return abort(401)
            if session.get("role") not in permitted_roles:
                return abort(403)
            return view_func(*args, **kwargs)
        return wrapped
    return decorator


# Alias legacy: solo admin por session
admin_required_session = roles_required("admin")
