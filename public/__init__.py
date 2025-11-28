# app_web/public/__init__.py

from flask import Blueprint

public_bp = Blueprint(
    "public",
    __name__,
    template_folder="../templates/public",
    static_folder="../static/public"
)

# 👇 ESTA LÍNEA ES LA CLAVE:
# Importa las rutas para que se registren en el blueprint.
from . import routes  # noqa: F401,E402
