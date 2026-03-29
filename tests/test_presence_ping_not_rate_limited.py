# -*- coding: utf-8 -*-

import os
import re
from unittest.mock import patch

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


def test_presence_ping_identical_burst_is_deduped_before_live_limit():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    env = {
        "ENABLE_OPERATIONAL_RATE_LIMITS": "1",
        "LIVE_PING_WINDOW": "60",
        "LIVE_PING_MAX_USER": "3",
        "LIVE_PING_MAX_IP": "3",
        "LIVE_PING_MAX_SESSION": "3",
        "LIVE_PING_BLOCK": "60",
        "LIVE_PING_DEDUPE_SECONDS": "2",
    }
    with patch.dict(os.environ, env, clear=False):
        client = flask_app.test_client()
        assert client.post("/admin/login", data={"usuario": "Cruz", "clave": "8998"}, follow_redirects=False).status_code in (302, 303)

        payload = {
            "session_id": "dedupe-test-tab-1",
            "current_path": "/buscar?candidata_id=100",
            "route_label": "Buscar / Editar candidata",
            "entity_type": "candidata",
            "entity_id": "100",
            "current_action": "searching",
            "action_label": "Buscando",
            "tab_visible": True,
            "is_idle": False,
            "is_typing": False,
            "has_unsaved_changes": False,
            "modal_open": False,
            "lock_owner": "",
        }
        for _ in range(8):
            resp = client.post("/admin/monitoreo/presence/ping", json=payload, follow_redirects=False)
            assert resp.status_code == 200
            assert (resp.get_json() or {}).get("ok") is True
