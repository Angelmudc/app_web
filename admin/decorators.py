# admin/decorators.py
from functools import wraps
from flask import abort, flash, redirect, url_for, request
from flask_login import current_user

def admin_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            flash("Debes iniciar sesión.", "warning")
            return redirect(url_for('auth.login', next=request.path))

        es_admin = getattr(current_user, "is_admin", False) or getattr(current_user, "rol", "") == "admin"
        if not es_admin:
            abort(403)
        return view_func(*args, **kwargs)
    return wrapper
