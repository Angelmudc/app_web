from types import SimpleNamespace
from unittest.mock import patch

from flask import redirect

from app import app as flask_app


def _login_secretaria(client):
    return client.post('/admin/login', data={'usuario': 'Karla', 'clave': '9989'}, follow_redirects=False)


def test_entrevistas_crud_routes_apuntan_a_handlers_nuevos_incluyendo_pdf():
    endpoints_crud = [
        'entrevistas_index',
        'entrevistas_buscar',
        'entrevistas_lista',
        'entrevistas_de_candidata',
        'entrevista_nueva_db',
        'entrevista_editar_redirect',
        'entrevista_editar_db',
    ]
    endpoints_pdf = [
        'generar_pdf_entrevista_db',
        'generar_pdf_entrevista',
        'generar_pdf_entrevista_nueva_db',
        'generar_pdf_ultima_entrevista_candidata',
    ]

    for ep in endpoints_crud:
        fn = flask_app.view_functions.get(ep)
        assert fn is not None
        assert fn.__module__ == 'core.handlers.entrevistas_handlers'

    for ep in endpoints_pdf:
        fn = flask_app.view_functions.get(ep)
        assert fn is not None
        assert fn.__module__ == 'core.handlers.entrevistas_pdf_handlers'


def test_entrevista_nueva_guardado_ok_redirige_a_lista_candidata():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    cand = SimpleNamespace(fila=1, estado='en_proceso', entrevista='')
    preguntas = [SimpleNamespace(id=1, enunciado='Pregunta 1')]

    ok_result = SimpleNamespace(ok=True, attempts=1, error_message='')
    with patch('core.handlers.entrevistas_handlers.legacy_h._get_candidata_safe_by_pk', return_value=cand), \
         patch('core.handlers.entrevistas_handlers._get_preguntas_db_por_tipo', return_value=preguntas), \
         patch('core.handlers.entrevistas_handlers.execute_robust_save', return_value=ok_result):
        resp = client.post(
            '/entrevistas/nueva/1/domestica',
            data={'q_1': 'respuesta util'},
            follow_redirects=False,
        )

    assert resp.status_code in (302, 303)
    assert '/entrevistas/candidata/1' in (resp.location or '')


def test_entrevista_editar_guardado_ok_redirige_a_lista_candidata():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    cand = SimpleNamespace(fila=1, estado='en_proceso', entrevista='')
    preguntas = [SimpleNamespace(id=1, enunciado='Pregunta 1')]
    entrevista = SimpleNamespace(id=7, candidata_id=1, tipo='domestica')

    class _EntrevistaQuery:
        def get_or_404(self, _eid):
            return entrevista

    class _RespuestasQuery:
        def filter_by(self, **_kwargs):
            return self

        def all(self):
            return []

    entrevista_model = SimpleNamespace(query=_EntrevistaQuery(), id=1)
    respuestas_model = SimpleNamespace(query=_RespuestasQuery())
    ok_result = SimpleNamespace(ok=True, attempts=1, error_message='')

    with patch('core.handlers.entrevistas_handlers.Entrevista', new=entrevista_model), \
         patch('core.handlers.entrevistas_handlers.EntrevistaRespuesta', new=respuestas_model), \
         patch('core.handlers.entrevistas_handlers.legacy_h._get_candidata_safe_by_pk', return_value=cand), \
         patch('core.handlers.entrevistas_handlers._get_preguntas_db_por_tipo', return_value=preguntas), \
         patch('core.handlers.entrevistas_handlers.execute_robust_save', return_value=ok_result):
        resp = client.post(
            '/entrevistas/editar/7',
            data={'q_1': 'respuesta editada'},
            follow_redirects=False,
        )

    assert resp.status_code in (302, 303)
    assert '/entrevistas/candidata/1' in (resp.location or '')


def test_entrevista_nueva_respeta_guarda_descalificacion():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    cand = SimpleNamespace(fila=1, estado='descalificada', entrevista='')

    with patch('core.handlers.entrevistas_handlers.legacy_h._get_candidata_safe_by_pk', return_value=cand), \
         patch('core.handlers.entrevistas_handlers.assert_candidata_no_descalificada', return_value=redirect('/bloqueada')), \
         patch('core.handlers.entrevistas_handlers.execute_robust_save') as save_mock:
        resp = client.post(
            '/entrevistas/nueva/1/domestica',
            data={'q_1': 'respuesta bloqueada'},
            follow_redirects=False,
        )

    assert resp.status_code in (302, 303)
    assert (resp.location or '').endswith('/bloqueada')
    save_mock.assert_not_called()


def test_entrevista_editar_redirect_compat_id_query_param():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    resp = client.get('/entrevistas/editar?id=123', follow_redirects=False)

    assert resp.status_code in (302, 303)
    assert '/entrevistas/editar/123' in (resp.location or '')


def test_entrevista_nueva_con_next_redirige_al_contexto():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    cand = SimpleNamespace(fila=2, estado='en_proceso', entrevista='')
    preguntas = [SimpleNamespace(id=1, enunciado='Pregunta 1')]
    ok_result = SimpleNamespace(ok=True, attempts=1, error_message='')

    with patch('core.handlers.entrevistas_handlers.legacy_h._get_candidata_safe_by_pk', return_value=cand), \
         patch('core.handlers.entrevistas_handlers._get_preguntas_db_por_tipo', return_value=preguntas), \
         patch('core.handlers.entrevistas_handlers.execute_robust_save', return_value=ok_result):
        resp = client.post(
            '/entrevistas/nueva/2/domestica?next=/finalizar_proceso?fila=2',
            data={'q_1': 'respuesta util', 'next': '/finalizar_proceso?fila=2'},
            follow_redirects=False,
        )

    assert resp.status_code in (302, 303)
    assert (resp.location or '').endswith('/finalizar_proceso?fila=2')
