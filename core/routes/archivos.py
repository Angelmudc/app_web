from flask import Blueprint
from core import legacy_handlers as h

bp = Blueprint('archivos_routes', __name__)

RULES = [
    ('/subir_fotos', 'subir_fotos', h.subir_fotos, ['GET', 'POST']),
    ('/subir_fotos/imagen/<int:fila>/<campo>', 'ver_imagen', h.ver_imagen, ['GET']),
    ('/gestionar_archivos', 'gestionar_archivos', h.gestionar_archivos, ['GET', 'POST']),
    ('/gestionar_archivos/descargar_uno', 'descargar_uno_db', h.descargar_uno_db, ['GET']),
]

for rule, endpoint, view_func, methods in RULES:
    bp.add_url_rule(rule, endpoint=endpoint, view_func=view_func, methods=methods)
