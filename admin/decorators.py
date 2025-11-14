# admin/decorators.py
# -*- coding: utf-8 -*-
from functools import wraps
from flask import abort, flash, redirect, url_for, request
from flask_login import current_user


# ============================================================================
# 🔐 Solo ADMIN
# ============================================================================
def admin_required(view_func):
    """
    Restringe acceso exclusivamente a administradores.
    Acepta:
      - current_user.role == 'admin'
      - current_user.is_admin == True (compatibilidad)
    Si no está logueado, envía al login del admin.
    """
    @wraps(view_func)
    def wrapper(*args, **kwargs):

        # No autenticado → manda al login admin
        if not current_user.is_authenticated:
            flash("Debes iniciar sesión.", "warning")
            return redirect(url_for("admin.login", next=request.path))

        # Normalización del rol
        role = (getattr(current_user, "role", "")
                or getattr(current_user, "rol", "")
                or "").strip().lower()

        is_admin_flag = bool(getattr(current_user, "is_admin", False))
        es_admin = is_admin_flag or role == "admin"

        if not es_admin:
            abort(403)

        return view_func(*args, **kwargs)

    return wrapper


# ============================================================================
# 🔐 ADMIN + SECRETARIA
# ============================================================================
def staff_required(view_func):
    """
    Permite acceso a:
      - Administradores
      - Secretarias
    Ideal para rutas donde NO se realiza algo delicado (eliminar, configurar).
    """
    @wraps(view_func)
    def wrapper(*args, **kwargs):

        # No autenticado → login
        if not current_user.is_authenticated:
            flash("Debes iniciar sesión.", "warning")
            return redirect(url_for("admin.login", next=request.path))

        # Normalización del rol
        role = (getattr(current_user, "role", "")
                or getattr(current_user, "rol", "")
                or "").strip().lower()

        is_admin_flag = bool(getattr(current_user, "is_admin", False))

        # staff incluye admin y secretaria
        if role not in ("admin", "secretaria") and not is_admin_flag:
            abort(403)

        return view_func(*args, **kwargs)

    return wrapper
