# -*- coding: utf-8 -*-

import re

from app import app as flask_app
from config_app import db
from models import StaffUser
from sqlalchemy import func


_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def _extract_csrf(html: str) -> str:
    m = _CSRF_RE.search(html or '')
    return m.group(1) if m else ''


def _login_with_csrf(client, usuario, clave):
    login_page = client.get('/admin/login', follow_redirects=False)
    assert login_page.status_code == 200
    token = _extract_csrf(login_page.data.decode('utf-8', errors='ignore'))
    assert token
    resp = client.post('/admin/login', data={'usuario': usuario, 'clave': clave, 'csrf_token': token}, follow_redirects=False)
    return resp, token


def _ensure_staff_user(username: str, role: str, password: str) -> None:
    row = StaffUser.query.filter(func.lower(StaffUser.username) == username.lower()).first()
    if row is None:
        row = StaffUser(username=username, role=role, is_active=True)
        row.set_password(password)
        db.session.add(row)
        db.session.commit()
        return
    row.role = role
    row.is_active = True
    row.set_password(password)
    db.session.commit()


def test_presence_ping_and_summary_presence_active():
    flask_app.config['TESTING'] = True
    prev_csrf = flask_app.config.get('WTF_CSRF_ENABLED', True)
    flask_app.config['WTF_CSRF_ENABLED'] = True

    try:
        with flask_app.app_context():
            _ensure_staff_user('Karla', 'secretaria', '9989')
            _ensure_staff_user('Cruz', 'admin', '8998')

        client_sec = flask_app.test_client()
        client_admin = flask_app.test_client()

        login_sec, _ = _login_with_csrf(client_sec, 'Karla', '9989')
        assert login_sec.status_code in (302, 303)

        ping_token_page = client_sec.get('/admin/login', follow_redirects=False)
        assert ping_token_page.status_code == 200
        ping_token = _extract_csrf(ping_token_page.data.decode('utf-8', errors='ignore'))
        assert ping_token

        ping_resp = client_sec.post(
            '/admin/monitoreo/presence/ping',
            json={'current_path': '/admin/clientes/532', 'page_title': 'Cliente 532'},
            headers={'X-CSRFToken': ping_token},
            follow_redirects=False,
        )
        assert ping_resp.status_code == 200
        assert ping_resp.get_json().get('ok') is True

        login_admin, _ = _login_with_csrf(client_admin, 'Cruz', '8998')
        assert login_admin.status_code in (302, 303)

        summary_resp = client_admin.get('/admin/monitoreo/summary.json', follow_redirects=False)
        assert summary_resp.status_code == 200
        summary = summary_resp.get_json()

        rows = [p for p in (summary.get('presence') or []) if p.get('username') == 'Karla']
        assert rows, 'Karla debe aparecer en presencia'
        row = rows[0]
        if row.get('current_path') != '/admin/clientes/532':
            sessions = row.get('sessions') or []
            assert any((s or {}).get('route') == '/admin/clientes/532' for s in sessions)
        assert row.get('status') in ('active', 'idle')
    finally:
        flask_app.config['WTF_CSRF_ENABLED'] = prev_csrf
