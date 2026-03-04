from flask import Blueprint
from core import legacy_handlers as h

bp = Blueprint('entrevistas_routes', __name__)

RULES = [
    ('/entrevistas', 'entrevistas_index', h.entrevistas_index, ['GET']),
    ('/entrevistas/buscar', 'entrevistas_buscar', h.entrevistas_buscar, ['GET', 'POST']),
    ('/entrevistas/lista', 'entrevistas_lista', h.entrevistas_lista, ['GET']),
    ('/entrevistas/candidata/<int:fila>', 'entrevistas_de_candidata', h.entrevistas_de_candidata, ['GET']),
    ('/entrevistas/nueva/<int:fila>/<string:tipo>', 'entrevista_nueva_db', h.entrevista_nueva_db, ['GET', 'POST']),
    ('/entrevistas/editar', 'entrevista_editar_redirect', h.entrevista_editar_redirect, ['GET']),
    ('/entrevistas/editar/<int:entrevista_id>', 'entrevista_editar_db', h.entrevista_editar_db, ['GET', 'POST']),
    ('/entrevistas/pdf/<int:entrevista_id>', 'generar_pdf_entrevista_db', h.generar_pdf_entrevista_db, ['GET']),
    ('/generar_pdf_entrevista', 'generar_pdf_entrevista', h.generar_pdf_entrevista, ['GET']),
    ('/entrevistas/pdf_nuevo/<int:entrevista_id>', 'generar_pdf_entrevista_nueva_db', h.generar_pdf_entrevista_nueva_db, ['GET']),
    ('/entrevistas/candidata/<int:fila>/pdf', 'generar_pdf_ultima_entrevista_candidata', h.generar_pdf_ultima_entrevista_candidata, ['GET']),
]

for rule, endpoint, view_func, methods in RULES:
    bp.add_url_rule(rule, endpoint=endpoint, view_func=view_func, methods=methods)
