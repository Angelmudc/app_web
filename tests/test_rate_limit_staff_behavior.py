# -*- coding: utf-8 -*-

import re

from app import app as flask_app


_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def _extract_csrf(html: str) -> str:
    m = _CSRF_RE.search(html or "")
    return m.group(1) if m else ""


def _login(client, usuario, clave, ip, csrf_token):
    return client.post(
        "/admin/login",
        data={"usuario": usuario, "clave": clave, "csrf_token": csrf_token},
        follow_redirects=False,
        environ_overrides={"REMOTE_ADDR": ip},
    )


def test_staff_admin_summary_high_volume_no_false_429():
    prev_testing = bool(flask_app.config.get("TESTING"))
    prev_csrf = bool(flask_app.config.get("WTF_CSRF_ENABLED", True))
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = True

    try:
        ip = "10.31.44.10"
        client = flask_app.test_client()
        login_page = client.get("/admin/login", follow_redirects=False, environ_overrides={"REMOTE_ADDR": ip})
        csrf_token = _extract_csrf(login_page.data.decode("utf-8", errors="ignore"))
        assert csrf_token
        assert _login(client, "Cruz", "8998", ip, csrf_token).status_code in (302, 303)

        flask_app.config["TESTING"] = False

        for _ in range(140):
            resp = client.get(
                "/admin/monitoreo/summary.json",
                follow_redirects=False,
                environ_overrides={"REMOTE_ADDR": ip},
            )
            assert resp.status_code == 200
    finally:
        flask_app.config["TESTING"] = prev_testing
        flask_app.config["WTF_CSRF_ENABLED"] = prev_csrf


def test_staff_multi_tab_polling_no_false_429():
    prev_testing = bool(flask_app.config.get("TESTING"))
    prev_csrf = bool(flask_app.config.get("WTF_CSRF_ENABLED", True))
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = True

    try:
        ip = "10.31.44.11"
        client = flask_app.test_client()
        login_page = client.get("/admin/login", follow_redirects=False, environ_overrides={"REMOTE_ADDR": ip})
        login_csrf = _extract_csrf(login_page.data.decode("utf-8", errors="ignore"))
        assert login_csrf
        assert _login(client, "Owner", "8899", ip, login_csrf).status_code in (302, 303)

        flask_app.config["TESTING"] = False

        for i in range(100):
            endpoints = (
                "/admin/monitoreo/summary.json",
                "/admin/monitoreo/logs.json?limit=1",
                "/admin/monitoreo/productividad.json",
                "/admin/monitoreo/presence.json",
            )
            target = endpoints[i % len(endpoints)]
            resp = client.get(
                target,
                follow_redirects=False,
                environ_overrides={"REMOTE_ADDR": ip},
            )
            assert resp.status_code == 200
    finally:
        flask_app.config["TESTING"] = prev_testing
        flask_app.config["WTF_CSRF_ENABLED"] = prev_csrf


def test_login_does_not_trigger_operational_rate_blocking():
    prev_testing = bool(flask_app.config.get("TESTING"))
    prev_csrf = bool(flask_app.config.get("WTF_CSRF_ENABLED", True))
    flask_app.config["TESTING"] = False
    flask_app.config["WTF_CSRF_ENABLED"] = True

    try:
        ip = "10.31.44.12"
        client = flask_app.test_client()
        login_page = client.get("/admin/login", follow_redirects=False, environ_overrides={"REMOTE_ADDR": ip})
        csrf_token = _extract_csrf(login_page.data.decode("utf-8", errors="ignore"))
        assert csrf_token
        blocked = False
        for _ in range(20):
            resp = _login(client, "Cruz", "clave-incorrecta", ip, csrf_token)
            if resp.status_code == 429:
                blocked = True
                break
        assert blocked is False
    finally:
        flask_app.config["TESTING"] = prev_testing
        flask_app.config["WTF_CSRF_ENABLED"] = prev_csrf
