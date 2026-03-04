from flask import Blueprint
from core import legacy_handlers as h

bp = Blueprint('candidatas_routes', __name__)

RULES = [
    ('/registro_interno/', 'registro_interno', h.registro_interno, ['GET', 'POST']),
    ('/candidatas', 'list_candidatas', h.list_candidatas, ['GET']),
    ('/candidatas_db', 'list_candidatas_db', h.list_candidatas_db, ['GET']),
    ('/buscar', 'buscar_candidata', h.buscar_candidata, ['GET', 'POST']),
    ('/filtrar', 'filtrar', h.filtrar, ['GET', 'POST']),
    ('/referencias', 'referencias', h.referencias, ['GET', 'POST']),
    ('/candidatas/llamadas', 'listado_llamadas_candidatas', h.listado_llamadas_candidatas, ['GET']),
    ('/candidatas/<int:fila>/llamar', 'registrar_llamada_candidata', h.registrar_llamada_candidata, ['GET', 'POST']),
    ('/candidatas/llamadas/reporte', 'reporte_llamadas_candidatas', h.reporte_llamadas_candidatas, ['GET']),
    ('/candidata/perfil', 'candidata_ver_perfil', h.ver_perfil, ['GET']),
    ('/perfil_candidata', 'perfil_candidata', h.perfil_candidata, ['GET']),
    ('/secretarias/compat/candidata', 'compat_candidata', h.compat_candidata, ['GET', 'POST']),
    ('/candidatas_porcentaje', 'candidatas_porcentaje', h.candidatas_porcentaje, ['GET']),
    ('/candidatas/eliminar', 'eliminar_candidata', h.eliminar_candidata, ['GET', 'POST']),
]

for rule, endpoint, view_func, methods in RULES:
    bp.add_url_rule(rule, endpoint=endpoint, view_func=view_func, methods=methods)
