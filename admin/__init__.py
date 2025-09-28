# admin/__init__.py
from flask import Blueprint

admin_bp = Blueprint(
    'admin',
    __name__,
    url_prefix='/admin'
)

# registra las rutas del blueprint
from . import routes
