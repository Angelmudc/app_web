import re
from types import SimpleNamespace
from unittest.mock import patch

from flask import url_for

from app import app as flask_app


def _login_secretaria(client):
    return client.post('/admin/login', data={'usuario': 'Karla', 'clave': '9989'}, follow_redirects=False)


def _build_candidata_stub(fila=1):
    return SimpleNamespace(
        fila=fila,
        nombre_completo=f'Candidata {fila}',
        cedula='001-0000000-1',
        numero_telefono='8090000000',
        referencias_laboral='',
        referencias_familiares='',
        contactos_referencias_laborales='',
        referencias_familiares_detalle='',
    )


def test_referencias_route_apunta_a_handler_nuevo_y_url_for_se_mantiene():
    fn = flask_app.view_functions.get('referencias')
    assert fn is not None
    assert fn.__module__ == 'core.handlers.referencias_handlers'

    with flask_app.test_request_context('/'):
        assert url_for('referencias') == '/referencias'


def test_referencias_get_basico_renderiza_busqueda():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    resp = client.get('/referencias', follow_redirects=False)
    body = resp.get_data(as_text=True)

    assert resp.status_code == 200
    assert 'referencias' in body.lower()


def test_referencias_busqueda_prioriza_ultima_fila_editada():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    fila_a = _build_candidata_stub(fila=10)
    fila_b = _build_candidata_stub(fila=20)

    with client.session_transaction() as sess:
        sess['last_edited_candidata_fila'] = 20

    with flask_app.app_context():
        with patch('core.handlers.referencias_handlers.search_candidatas_limited', return_value=[fila_a, fila_b]):
            resp = client.post('/referencias', data={'busqueda': 'demo'}, follow_redirects=False)

    body = resp.get_data(as_text=True)
    ids = re.findall(r'/referencias\?candidata=(\d+)', body)

    assert resp.status_code == 200
    assert ids and ids[0] == '20'


def test_referencias_guardado_preserva_side_effects_clave():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    cand = _build_candidata_stub(fila=99)

    def _ok_result(**_kwargs):
        return SimpleNamespace(ok=True, attempts=1, error_message='')

    with flask_app.app_context():
        with patch('core.handlers.referencias_handlers.get_candidata_by_id', return_value=cand), \
             patch('core.legacy_handlers.execute_robust_save', side_effect=_ok_result) as robust_mock:
            resp = client.post(
                '/referencias',
                data={
                    'candidata_id': '99',
                    'referencias_laboral': 'Laboral sincronizada',
                    'referencias_familiares': 'Familiar sincronizada',
                },
                follow_redirects=False,
            )

    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert 'Referencias actualizadas' in body
    robust_mock.assert_called_once()
    assert cand.referencias_laboral == 'Laboral sincronizada'
    assert cand.referencias_familiares == 'Familiar sincronizada'
    assert cand.contactos_referencias_laborales == 'Laboral sincronizada'
    assert cand.referencias_familiares_detalle == 'Familiar sincronizada'


def test_referencias_placeholders_no_disparan_robust_save():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    cand = _build_candidata_stub(fila=1)

    with flask_app.app_context():
        with patch('core.handlers.referencias_handlers.get_candidata_by_id', return_value=cand), \
             patch('core.legacy_handlers.execute_robust_save') as robust_mock:
            resp = client.post(
                '/referencias',
                data={
                    'candidata_id': '1',
                    'referencias_laboral': 'none',
                    'referencias_familiares': '--',
                },
                follow_redirects=False,
            )

    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert 'Referencias inv' in body
    robust_mock.assert_not_called()


def test_referencias_post_con_next_redirige_al_contexto():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    cand = _build_candidata_stub(fila=51)

    def _ok_result(**_kwargs):
        return SimpleNamespace(ok=True, attempts=1, error_message='')

    with flask_app.app_context():
        with patch('core.handlers.referencias_handlers.get_candidata_by_id', return_value=cand), \
             patch('core.legacy_handlers.execute_robust_save', side_effect=_ok_result):
            resp = client.post(
                '/referencias?next=/finalizar_proceso?fila=51',
                data={
                    'next': '/finalizar_proceso?fila=51',
                    'candidata_id': '51',
                    'referencias_laboral': 'Laboral ok',
                    'referencias_familiares': 'Familiar ok',
                },
                follow_redirects=False,
            )

    assert resp.status_code in (302, 303)
    assert (resp.headers.get('Location') or '').endswith('/finalizar_proceso?fila=51')
