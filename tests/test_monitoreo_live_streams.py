# -*- coding: utf-8 -*-

from uuid import uuid4

from app import app as flask_app


def _login(client, usuario, clave):
    return client.post('/admin/login', data={'usuario': usuario, 'clave': clave}, follow_redirects=False)


def test_monitoreo_live_stream_handshakes():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()

    cand_id = 1000 + int(uuid4().hex[:4], 16)

    assert _login(client, 'Cruz', '8998').status_code in (302, 303)

    resp = client.get('/admin/monitoreo/stream?once=1', follow_redirects=False)
    assert resp.status_code == 200
    assert 'text/event-stream' in (resp.content_type or '')
    assert (resp.headers.get('Cache-Control') or '').lower() == 'no-cache'
    assert (resp.headers.get('X-Accel-Buffering') or '').lower() == 'no'
    body = resp.data.decode('utf-8', errors='ignore')
    assert 'event: heartbeat' in body

    cand_resp = client.get(f'/admin/monitoreo/candidatas/{cand_id}/stream?once=1', follow_redirects=False)
    assert cand_resp.status_code == 200
    assert 'text/event-stream' in (cand_resp.content_type or '')
    cand_body = cand_resp.data.decode('utf-8', errors='ignore')
    assert 'event: heartbeat' in cand_body
