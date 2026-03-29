from flask import Blueprint
from core.handlers import auth_home_handlers as auth_home_h
from core.handlers import home_notifications_handlers as notif_h
from core.handlers import finalizar_proceso_handlers as finalizar_h
from core.handlers import secretarias_solicitudes_handlers as secretarias_solicitudes_h
from core.handlers import procesos_reportes_handlers as procesos_reportes_h
from core.handlers import procesos_transacciones_handlers as procesos_transacciones_h
from core.handlers import procesos_dashboard_handlers as procesos_dashboard_h
from core.handlers import procesos_automatizaciones_handlers as procesos_auto_h

bp = Blueprint('procesos_routes', __name__)

RULES = [
    ('/robots.txt', 'robots_txt', auth_home_h.robots_txt, ['GET']),
    ('/home', 'home', auth_home_h.home, ['GET']),
    ('/home/notificaciones-publicas/count.json', 'home_public_notifications_count', notif_h.home_public_notifications_count, ['GET']),
    ('/home/notificaciones-publicas/list.json', 'home_public_notifications_list', notif_h.home_public_notifications_list, ['GET']),
    ('/home/notificaciones-publicas/<int:notificacion_id>/leer', 'home_public_notifications_mark_read', notif_h.home_public_notifications_mark_read, ['POST']),
    ('/login', 'login', auth_home_h.login, ['GET', 'POST']),
    ('/logout', 'logout', auth_home_h.logout, ['POST']),
    ('/inscripcion', 'inscripcion', procesos_transacciones_h.inscripcion, ['GET', 'POST']),
    ('/porciento', 'porciento', procesos_transacciones_h.porciento, ['GET', 'POST']),
    ('/pagos', 'pagos', procesos_transacciones_h.pagos, ['GET', 'POST']),
    ('/reporte_inscripciones', 'reporte_inscripciones', procesos_reportes_h.reporte_inscripciones, ['GET']),
    ('/reporte_pagos', 'reporte_pagos', procesos_reportes_h.reporte_pagos, ['GET']),
    ('/dashboard_procesos', 'dashboard_procesos', procesos_dashboard_h.dashboard_procesos, ['GET']),
    ('/auto_actualizar_estados', 'auto_actualizar_estados', procesos_auto_h.auto_actualizar_estados, ['GET']),
    ('/secretarias/solicitudes/copiar', 'secretarias_copiar_solicitudes', secretarias_solicitudes_h.secretarias_copiar_solicitudes, ['GET']),
    ('/secretarias/solicitudes/<int:id>/copiar', 'secretarias_copiar_solicitud', secretarias_solicitudes_h.secretarias_copiar_solicitud, ['POST']),
    ('/secretarias/solicitudes/buscar', 'secretarias_buscar_solicitudes', secretarias_solicitudes_h.secretarias_buscar_solicitudes, ['GET']),
    ('/finalizar_proceso/buscar', 'finalizar_proceso_buscar', finalizar_h.finalizar_proceso_buscar, ['GET']),
    ('/finalizar_proceso', 'finalizar_proceso', finalizar_h.finalizar_proceso, ['GET', 'POST']),
]

for rule, endpoint, view_func, methods in RULES:
    bp.add_url_rule(rule, endpoint=endpoint, view_func=view_func, methods=methods)
