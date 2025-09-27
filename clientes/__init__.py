# clientes/__init__.py
# -*- coding: utf-8 -*-
from flask import Blueprint

# Crea UNA sola instancia del blueprint aquí
clientes_bp = Blueprint(
    'clientes',
    __name__,
    url_prefix='/clientes',
    template_folder='../templates/clientes'
)

__all__ = ['clientes_bp']

# Importa las rutas DESPUÉS de crear el blueprint (evita ciclos)
from . import routes  # <- tu archivo único con TODAS las rutas
