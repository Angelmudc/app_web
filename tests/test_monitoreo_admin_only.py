# -*- coding: utf-8 -*-

from app import app as flask_app


def _login(client, usuario, clave):
    return client.post('/admin/login', data={'usuario': usuario, 'clave': clave}, follow_redirects=False)


def test_monitoreo_admin_only_forbidden_for_secretaria():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()

    login_resp = _login(client, 'Karla', '9989')
    assert login_resp.status_code in (302, 303)

    resp = client.get('/admin/monitoreo', follow_redirects=False)
    assert resp.status_code == 403


def test_monitoreo_admin_only_ok_for_admin():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()

    login_resp = _login(client, 'Cruz', '8998')
    assert login_resp.status_code in (302, 303)

    resp = client.get('/admin/monitoreo', follow_redirects=False)
    assert resp.status_code == 200
