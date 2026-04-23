# -*- coding: utf-8 -*-

import admin.routes as admin_routes
from unittest.mock import patch

from app import app as flask_app


def _login(client, usuario, clave):
    return client.post('/admin/login', data={'usuario': usuario, 'clave': clave}, follow_redirects=False)


def test_monitoreo_sse_stream_once_mode():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()

    assert _login(client, 'Cruz', '8998').status_code in (302, 303)

    resp = client.get('/admin/monitoreo/stream?once=1', follow_redirects=False)
    assert resp.status_code == 200
    assert 'text/event-stream' in (resp.content_type or '')

    body = resp.data.decode('utf-8', errors='ignore')
    assert 'event: active_snapshot' in body
    assert 'event: presence' in body
    assert 'event: heartbeat' in body


def test_stream_active_presence_for_operations_prefers_cached_snapshot():
    sentinel = [{"status": "active", "username": "Karla"}]
    with patch('admin.routes._presence_active_rows', return_value=[{"status": "idle"}]) as fallback_mock:
        out = admin_routes._stream_active_presence_for_operations(sentinel)
    fallback_mock.assert_not_called()
    assert out == sentinel


def test_stream_active_presence_for_operations_falls_back_to_presence_lookup():
    sentinel = [{"status": "active", "username": "Cruz"}]
    with patch('admin.routes._presence_active_rows', return_value=sentinel) as fallback_mock:
        out = admin_routes._stream_active_presence_for_operations(None)
    fallback_mock.assert_called_once_with()
    assert out == sentinel
