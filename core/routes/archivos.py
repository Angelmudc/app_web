from flask import Blueprint
from core.handlers import archivos_handlers as h
from core.handlers import gestionar_archivos_handlers as gestionar_h

bp = Blueprint('archivos_routes', __name__)

RULES = [
    ('/subir_fotos', 'subir_fotos', h.subir_fotos, ['GET', 'POST']),
    ('/subir_fotos/imagen/<int:fila>/<campo>', 'ver_imagen', h.ver_imagen, ['GET']),
    ('/gestionar_archivos', 'gestionar_archivos', gestionar_h.gestionar_archivos, ['GET', 'POST']),
    ('/gestionar_archivos/descargar_uno', 'descargar_uno_db', h.descargar_uno_db, ['GET']),
]

for rule, endpoint, view_func, methods in RULES:
    bp.add_url_rule(rule, endpoint=endpoint, view_func=view_func, methods=methods)
