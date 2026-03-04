from flask import Blueprint
from core import legacy_handlers as h

bp = Blueprint('procesos_routes', __name__)

RULES = [
    ('/robots.txt', 'robots_txt', h.robots_txt, ['GET']),
    ('/home', 'home', h.home, ['GET']),
    ('/login', 'login', h.login, ['GET', 'POST']),
    ('/logout', 'logout', h.logout, ['GET']),
    ('/inscripcion', 'inscripcion', h.inscripcion, ['GET', 'POST']),
    ('/porciento', 'porciento', h.porciento, ['GET', 'POST']),
    ('/pagos', 'pagos', h.pagos, ['GET', 'POST']),
    ('/reporte_inscripciones', 'reporte_inscripciones', h.reporte_inscripciones, ['GET']),
    ('/reporte_pagos', 'reporte_pagos', h.reporte_pagos, ['GET']),
    ('/dashboard_procesos', 'dashboard_procesos', h.dashboard_procesos, ['GET']),
    ('/auto_actualizar_estados', 'auto_actualizar_estados', h.auto_actualizar_estados, ['GET']),
    ('/secretarias/solicitudes/copiar', 'secretarias_copiar_solicitudes', h.secretarias_copiar_solicitudes, ['GET']),
    ('/secretarias/solicitudes/<int:id>/copiar', 'secretarias_copiar_solicitud', h.secretarias_copiar_solicitud, ['POST']),
    ('/secretarias/solicitudes/buscar', 'secretarias_buscar_solicitudes', h.secretarias_buscar_solicitudes, ['GET']),
    ('/finalizar_proceso/buscar', 'finalizar_proceso_buscar', h.finalizar_proceso_buscar, ['GET']),
    ('/finalizar_proceso', 'finalizar_proceso', h.finalizar_proceso, ['GET', 'POST']),
]

for rule, endpoint, view_func, methods in RULES:
    bp.add_url_rule(rule, endpoint=endpoint, view_func=view_func, methods=methods)
