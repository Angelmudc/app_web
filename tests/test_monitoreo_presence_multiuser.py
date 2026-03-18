# -*- coding: utf-8 -*-

from app import app as flask_app


def _login(client, usuario, clave):
    return client.post('/admin/login', data={'usuario': usuario, 'clave': clave}, follow_redirects=False)


def test_presence_summary_multi_user_owner_admin_secretaria():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False

    c_owner = flask_app.test_client()
    c_admin = flask_app.test_client()
    c_sec = flask_app.test_client()

    assert _login(c_owner, 'Owner', 'admin123').status_code in (302, 303)
    assert _login(c_admin, 'Cruz', '8998').status_code in (302, 303)
    assert _login(c_sec, 'Karla', '9989').status_code in (302, 303)

    assert c_owner.post(
        '/admin/monitoreo/presence/ping',
        json={'current_path': '/admin/monitoreo', 'page_title': 'Control Room'},
        follow_redirects=False,
    ).status_code == 200
    assert c_admin.post(
        '/admin/monitoreo/presence/ping',
        json={'current_path': '/admin/solicitudes', 'page_title': 'Solicitudes'},
        follow_redirects=False,
    ).status_code == 200
    assert c_sec.post(
        '/admin/monitoreo/presence/ping',
        json={'current_path': '/buscar', 'page_title': 'Buscar'},
        follow_redirects=False,
    ).status_code == 200

    summary = c_owner.get('/admin/monitoreo/summary.json', follow_redirects=False)
    assert summary.status_code == 200
    payload = summary.get_json() or {}

    rows = payload.get('presence') or []
    by_user = {str(r.get('username')): r for r in rows}

    assert by_user.get('Owner', {}).get('status') == 'active'
    assert by_user.get('Cruz', {}).get('status') == 'active'
    assert by_user.get('Karla', {}).get('status') == 'active'

    present_ids = {int(r.get('user_id')) for r in rows if r.get('user_id') is not None}
    assert len(present_ids) >= 3
    assert int(payload.get('presence_active_count') or 0) >= 3
