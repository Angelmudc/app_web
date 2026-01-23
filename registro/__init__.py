from flask import Blueprint

registro_bp = Blueprint(
    'registro',
    __name__,
    template_folder='templates'
)

from . import routes