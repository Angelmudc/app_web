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
        estado='en_proceso',
        foto_perfil=b'img',
        perfil=None,
        cedula1=b'ced1',
        cedula2=b'ced2',
        grupos_empleo='["Interna","Limpieza"]',
    )


def test_candidata_perfil_routes_apuntan_a_handler_nuevo_y_url_for_se_mantiene():
    fn_ver = flask_app.view_functions.get('candidata_ver_perfil')
    fn_img = flask_app.view_functions.get('perfil_candidata')
    assert fn_ver is not None
    assert fn_img is not None
    assert fn_ver.__module__ == 'core.handlers.candidata_perfil_handlers'
    assert fn_img.__module__ == 'core.handlers.candidata_perfil_handlers'

    with flask_app.test_request_context('/'):
        assert url_for('candidata_ver_perfil') == '/candidata/perfil'
        assert url_for('perfil_candidata') == '/perfil_candidata'


def test_get_perfil_renderiza_html():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    cand = _build_candidata_stub(fila=10)
    with flask_app.app_context():
        with patch('core.legacy_handlers._get_candidata_safe_by_pk', return_value=cand):
            resp = client.get('/candidata/perfil?fila=10', follow_redirects=False)

    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert 'Perfil de' in body
    assert 'Candidata 10' in body


def test_get_perfil_sin_fila_responde_400():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    resp = client.get('/candidata/perfil', follow_redirects=False)
    assert resp.status_code == 400


def test_get_perfil_candidata_not_found_responde_404():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    with flask_app.app_context():
        with patch('core.legacy_handlers._get_candidata_safe_by_pk', return_value=None):
            resp = client.get('/candidata/perfil?fila=1', follow_redirects=False)

    assert resp.status_code == 404


def test_get_perfil_candidata_imagen_binaria_ok():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    with flask_app.app_context():
        with patch('core.legacy_handlers._fetch_image_bytes_safe', return_value=b'\xff\xd8\xffdemo'):
            resp = client.get('/perfil_candidata?fila=7', follow_redirects=False)

    assert resp.status_code == 200
    assert resp.mimetype == 'image/jpeg'
    assert resp.data.startswith(b'\xff\xd8\xff')


def test_get_perfil_candidata_imagen_400_y_404():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    resp_bad = client.get('/perfil_candidata', follow_redirects=False)
    assert resp_bad.status_code == 400

    with flask_app.app_context():
        with patch('core.legacy_handlers._fetch_image_bytes_safe', return_value=None):
            resp_not_found = client.get('/perfil_candidata?fila=1', follow_redirects=False)
    assert resp_not_found.status_code == 404
