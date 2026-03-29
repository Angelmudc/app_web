# -*- coding: utf-8 -*-
from flask import Blueprint

contratos_bp = Blueprint(
    "contratos",
    __name__,
    template_folder="../templates/contratos",
)

from . import routes  # noqa: E402,F401
