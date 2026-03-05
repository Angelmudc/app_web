# -*- coding: utf-8 -*-

import re

from app import app as flask_app


_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def _extract_csrf(html: str) -> str:
    m = _CSRF_RE.search(html or "")
    return m.group(1) if m else ""


def _login_admin(client):
    page = client.get("/admin/login", follow_redirects=False)
    token = _extract_csrf(page.data.decode("utf-8", errors="ignore"))
    assert token
    return client.post(
        "/admin/login",
        data={"usuario": "Cruz", "clave": "8998", "csrf_token": token},
        follow_redirects=False,
    )


def test_monitoreo_stream_once_headers_and_heartbeat():
    prev_testing = bool(flask_app.config.get("TESTING"))
    prev_csrf = bool(flask_app.config.get("WTF_CSRF_ENABLED", True))
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = True

    try:
        client = flask_app.test_client()
        login_resp = _login_admin(client)
        assert login_resp.status_code in (302, 303)

        resp = client.get("/admin/monitoreo/stream?once=1", follow_redirects=False)
        assert resp.status_code == 200
        assert "text/event-stream" in (resp.headers.get("Content-Type") or "")
        assert (resp.headers.get("Cache-Control") or "").lower() == "no-cache"
        assert (resp.headers.get("X-Accel-Buffering") or "").lower() == "no"
        body = resp.data.decode("utf-8", errors="ignore")
        assert "event: heartbeat" in body
    finally:
        flask_app.config["TESTING"] = prev_testing
        flask_app.config["WTF_CSRF_ENABLED"] = prev_csrf
