# -*- coding: utf-8 -*-

from app import app as flask_app


def _login(client, usuario: str, clave: str):
    return client.post('/admin/login', data={'usuario': usuario, 'clave': clave}, follow_redirects=False)


def test_home_link_visible_for_owner_admin_secretaria():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False

    for usuario, clave in (('Owner', 'admin123'), ('Cruz', '8998'), ('Karla', '9989')):
        client = flask_app.test_client()
        assert _login(client, usuario, clave).status_code in (302, 303)
        resp = client.get('/home', follow_redirects=False)
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'href="/secretarias/solicitudes/filtro"' in html
        assert 'Buscar solicitudes' in html


def test_home_link_not_visible_for_public_or_cliente_role():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False

    public_client = flask_app.test_client()
    public_resp = public_client.get('/home', follow_redirects=False)
    assert public_resp.status_code in (302, 303)
    assert b'href="/secretarias/solicitudes/filtro"' not in public_resp.data

    cliente_client = flask_app.test_client()
    with cliente_client.session_transaction() as sess:
        sess['usuario'] = 'cliente_demo'
        sess['role'] = 'cliente'
        sess['is_admin_session'] = True
        sess['mfa_verified'] = True
        sess['logged_at'] = '2026-05-05T10:00:00'
    cliente_resp = cliente_client.get('/home', follow_redirects=False)
    assert cliente_resp.status_code in (302, 303)
    assert b'href="/secretarias/solicitudes/filtro"' not in cliente_resp.data


def test_secretarias_filtro_route_access_for_staff_and_denied_for_others():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False

    for usuario, clave in (('Owner', 'admin123'), ('Cruz', '8998'), ('Karla', '9989')):
        client = flask_app.test_client()
        assert _login(client, usuario, clave).status_code in (302, 303)
        ok = client.get('/secretarias/solicitudes/filtro', follow_redirects=False)
        assert ok.status_code == 200

    anon = flask_app.test_client()
    denied_anon = anon.get('/secretarias/solicitudes/filtro', follow_redirects=False)
    assert denied_anon.status_code in (302, 303)

    cliente_client = flask_app.test_client()
    with cliente_client.session_transaction() as sess:
        sess['usuario'] = 'cliente_demo'
        sess['role'] = 'cliente'
        sess['is_admin_session'] = True
        sess['mfa_verified'] = True
        sess['logged_at'] = '2026-05-05T10:00:00'
    denied_cliente = cliente_client.get('/secretarias/solicitudes/filtro', follow_redirects=False)
    assert denied_cliente.status_code in (302, 303, 403)
