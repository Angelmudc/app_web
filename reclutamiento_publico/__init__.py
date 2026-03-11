from flask import Blueprint

reclutamiento_publico_bp = Blueprint(
    "reclutamiento_publico",
    __name__,
    url_prefix="/trabaja-con-nosotros",
    template_folder="../templates/reclutamiento",
)

from . import routes  # noqa: E402,F401
