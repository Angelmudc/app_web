from flask import Blueprint
from core.handlers import referencias_handlers as refs_h
from core.handlers import compat_candidata_handlers as compat_h
from core.handlers import candidata_perfil_handlers as perfil_h
from core.handlers import candidatas_porcentaje_handlers as porcentaje_h
from core.handlers import llamadas_candidatas_handlers as llamadas_h
from core.handlers import candidatas_list_handlers as list_h
from core.handlers import candidatas_filtrar_handlers as filtrar_h
from core.handlers import registro_interno_handlers as registro_h
from core.handlers import eliminar_candidata_handlers as eliminar_h
from core.handlers import buscar_candidata_handlers as buscar_h

bp = Blueprint('candidatas_routes', __name__)

RULES = [
    ('/registro_interno/', 'registro_interno', registro_h.registro_interno, ['GET', 'POST']),
    ('/candidatas', 'list_candidatas', list_h.list_candidatas, ['GET']),
    ('/candidatas_db', 'list_candidatas_db', list_h.list_candidatas_db, ['GET']),
    ('/buscar', 'buscar_candidata', buscar_h.buscar_candidata, ['GET', 'POST']),
    ('/filtrar', 'filtrar', filtrar_h.filtrar, ['GET', 'POST']),
    ('/referencias', 'referencias', refs_h.referencias, ['GET', 'POST']),
    ('/candidatas/llamadas', 'listado_llamadas_candidatas', llamadas_h.listado_llamadas_candidatas, ['GET']),
    ('/candidatas/<int:fila>/llamar', 'registrar_llamada_candidata', llamadas_h.registrar_llamada_candidata, ['GET', 'POST']),
    ('/candidatas/llamadas/reporte', 'reporte_llamadas_candidatas', llamadas_h.reporte_llamadas_candidatas, ['GET']),
    ('/candidata/perfil', 'candidata_ver_perfil', perfil_h.ver_perfil, ['GET']),
    ('/perfil_candidata', 'perfil_candidata', perfil_h.perfil_candidata, ['GET']),
    ('/secretarias/compat/candidata', 'compat_candidata', compat_h.compat_candidata, ['GET', 'POST']),
    ('/candidatas_porcentaje', 'candidatas_porcentaje', porcentaje_h.candidatas_porcentaje, ['GET']),
    ('/candidatas/eliminar', 'eliminar_candidata', eliminar_h.eliminar_candidata, ['GET', 'POST']),
]

for rule, endpoint, view_func, methods in RULES:
    bp.add_url_rule(rule, endpoint=endpoint, view_func=view_func, methods=methods)
