# admin/decorators.py
from functools import wraps
from flask import abort, flash, redirect, url_for, request
from flask_login import current_user

def admin_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        # Si no está autenticado, mándalo al login correcto del blueprint admin
        if not current_user.is_authenticated:
            flash("Debes iniciar sesión.", "warning")
            return redirect(url_for('admin.login', next=request.path))

        # Normaliza: acepta is_admin, role o rol (por compatibilidad)
        role = (getattr(current_user, "role", "") or getattr(current_user, "rol", "") or "").strip().lower()
        is_admin_flag = bool(getattr(current_user, "is_admin", False))
        es_admin = is_admin_flag or role == "admin"

        if not es_admin:
            abort(403)
        return view_func(*args, **kwargs)
    return wrapper
