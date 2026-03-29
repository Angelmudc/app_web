from types import SimpleNamespace
from unittest.mock import patch

from flask import Response, url_for

from app import app as flask_app
from core.services import pdf as pdf_service


def _login_secretaria(client):
    return client.post('/admin/login', data={'usuario': 'Karla', 'clave': '9989'}, follow_redirects=False)


def test_pdf_routes_apuntan_a_handler_nuevo_y_url_for_se_mantiene():
    endpoints = [
        'generar_pdf_entrevista_db',
        'generar_pdf_entrevista',
        'generar_pdf_entrevista_nueva_db',
        'generar_pdf_ultima_entrevista_candidata',
    ]
    for ep in endpoints:
        fn = flask_app.view_functions.get(ep)
        assert fn is not None
        assert fn.__module__ == 'core.handlers.entrevistas_pdf_handlers'

    with flask_app.test_request_context('/'):
        assert url_for('generar_pdf_entrevista_db', entrevista_id=7) == '/entrevistas/pdf/7'
        assert url_for('generar_pdf_entrevista', fila=9) == '/generar_pdf_entrevista?fila=9'
        assert url_for('generar_pdf_entrevista_nueva_db', entrevista_id=7) == '/entrevistas/pdf_nuevo/7'
        assert url_for('generar_pdf_ultima_entrevista_candidata', fila=9) == '/entrevistas/candidata/9/pdf'


def test_core_services_pdf_exporta_funciones_del_handler_nuevo():
    assert pdf_service.generar_pdf_entrevista_db.__module__ == 'core.handlers.entrevistas_pdf_handlers'
    assert pdf_service.generar_pdf_entrevista.__module__ == 'core.handlers.entrevistas_pdf_handlers'
    assert pdf_service.generar_pdf_ultima_entrevista_candidata.__module__ == 'core.handlers.entrevistas_pdf_handlers'


def test_generar_pdf_entrevista_400_y_404():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    resp_400 = client.get('/generar_pdf_entrevista', follow_redirects=False)
    assert resp_400.status_code == 400

    with flask_app.app_context():
        with patch('core.handlers.entrevistas_pdf_handlers.legacy_h._get_candidata_by_fila_or_pk', return_value=None):
            resp_404 = client.get('/generar_pdf_entrevista?fila=1', follow_redirects=False)
    assert resp_404.status_code == 404


def test_generar_pdf_entrevista_respuesta_pdf_binaria():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    cand = SimpleNamespace(
        fila=1,
        entrevista='Pregunta: respuesta útil',
        referencias_laboral='Ref laboral',
        referencias_familiares='Ref familiar',
    )
    fake_pdf_resp = Response(b'%PDF-demo', mimetype='application/pdf')

    with flask_app.app_context():
        with patch('core.handlers.entrevistas_pdf_handlers.legacy_h._get_candidata_by_fila_or_pk', return_value=cand), \
             patch('core.handlers.entrevistas_pdf_handlers.send_file', return_value=fake_pdf_resp):
            resp = client.get('/generar_pdf_entrevista?fila=1', follow_redirects=False)

    assert resp.status_code == 200
    assert resp.mimetype == 'application/pdf'


def test_pdf_nuevo_alias_delega_en_pdf_db():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    with flask_app.app_context():
        with patch('core.handlers.entrevistas_pdf_handlers.generar_pdf_entrevista_db', return_value=('ok', 200)) as db_pdf:
            resp = client.get('/entrevistas/pdf_nuevo/7', follow_redirects=False)

    assert resp.status_code == 200
    db_pdf.assert_called_once_with(7)


def test_pdf_ultima_entrevista_redirect_y_404():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    class _QueryFirst:
        def filter(self, *_a, **_k):
            return self

        def order_by(self, *_a, **_k):
            return self

        def first(self):
            return SimpleNamespace(id=12)

    class _QueryNone(_QueryFirst):
        def first(self):
            return None

    with flask_app.app_context():
        with patch('core.handlers.entrevistas_pdf_handlers.legacy_h.db.session.query', return_value=_QueryFirst()):
            resp_redirect = client.get('/entrevistas/candidata/1/pdf', follow_redirects=False)
    assert resp_redirect.status_code in (302, 303)
    assert '/entrevistas/pdf/12' in (resp_redirect.location or '')

    with flask_app.app_context():
        with patch('core.handlers.entrevistas_pdf_handlers.legacy_h.db.session.query', return_value=_QueryNone()):
            resp_404 = client.get('/entrevistas/candidata/1/pdf', follow_redirects=False)
    assert resp_404.status_code == 404
