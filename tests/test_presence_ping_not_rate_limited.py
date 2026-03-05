# -*- coding: utf-8 -*-

import re

from app import app as flask_app


_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def _extract_csrf(html: str) -> str:
    m = _CSRF_RE.search(html or "")
    return m.group(1) if m else ""


def test_presence_ping_10_requests_without_429():
    prev_testing = bool(flask_app.config.get("TESTING"))
    prev_csrf = bool(flask_app.config.get("WTF_CSRF_ENABLED", True))
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = True

    try:
        client = flask_app.test_client()
        login_page = client.get("/admin/login", follow_redirects=False)
        assert login_page.status_code == 200
        login_csrf = _extract_csrf(login_page.data.decode("utf-8", errors="ignore"))
        assert login_csrf

        login_resp = client.post(
            "/admin/login",
            data={"usuario": "Cruz", "clave": "8998", "csrf_token": login_csrf},
            follow_redirects=False,
        )
        assert login_resp.status_code in (302, 303)

        # Activar capa anti-scrape/rate-limit después de autenticar.
        flask_app.config["TESTING"] = False

        ping_page = client.get("/admin/login", follow_redirects=False)
        ping_csrf = _extract_csrf(ping_page.data.decode("utf-8", errors="ignore"))
        assert ping_csrf

        for _ in range(10):
            resp = client.post(
                "/admin/monitoreo/presence/ping",
                json={"current_path": "/admin/monitoreo", "page_title": "Monitoreo"},
                headers={"X-CSRFToken": ping_csrf},
                follow_redirects=False,
            )
            assert resp.status_code == 200
            assert resp.get_json().get("ok") is True
    finally:
        flask_app.config["TESTING"] = prev_testing
        flask_app.config["WTF_CSRF_ENABLED"] = prev_csrf
