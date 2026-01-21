# webadmin/__init__.py
from flask import Blueprint

webadmin_bp = Blueprint(
    "webadmin",
    __name__,
    template_folder="../templates",
    static_folder="../static"
)

from . import routes  # noqa