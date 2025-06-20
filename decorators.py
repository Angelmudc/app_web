# decorators.py

from functools import wraps
from flask import session, abort

def roles_required(*permitted_roles):
    """
    Decorador que permite el acceso sólo si:
      - Hay un 'usuario' en sesión.
      - session['role'] está entre los roles permitidos.
    """
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            # 1) Debe estar logueado
            if 'usuario' not in session:
                return abort(401)   # No autenticado
            # 2) Debe tener un rol válido
            if session.get('role') not in permitted_roles:
                return abort(403)   # Prohibido
            return f(*args, **kwargs)
        return wrapped
    return decorator

# Alias para acceso sólo de administradores
admin_required = roles_required('admin')
