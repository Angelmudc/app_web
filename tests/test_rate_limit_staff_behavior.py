# -*- coding: utf-8 -*-

import os
import re
import uuid
from unittest.mock import patch

from app import app as flask_app
from config_app import db
from models import StaffUser


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
        assert _login(client, "Owner", "admin123", ip, login_csrf).status_code in (302, 303)

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


def test_presence_ping_does_not_block_create_cliente_global_admin_bucket():
    prev_testing = bool(flask_app.config.get("TESTING"))
    prev_csrf = bool(flask_app.config.get("WTF_CSRF_ENABLED", True))
    flask_app.config["TESTING"] = False
    flask_app.config["WTF_CSRF_ENABLED"] = False

    env = {
        "ENABLE_OPERATIONAL_RATE_LIMITS": "1",
        "STAFF_MFA_REQUIRED": "0",
        "ADMIN_ACTION_MAX": "3",
        "ADMIN_ACTION_WINDOW": "60",
        "ADMIN_ACTION_LOCK": "120",
        "LIVE_PING_MAX_USER": "500",
        "LIVE_PING_MAX_IP": "500",
        "LIVE_PING_MAX_SESSION": "500",
        "LIVE_PING_WINDOW": "60",
        "LIVE_PING_DEDUPE_SECONDS": "1",
    }

    try:
        ip = "10.31.44.13"
        with patch.dict(os.environ, env, clear=False):
            client = flask_app.test_client()
            login = client.post(
                "/admin/login",
                data={"usuario": "Cruz", "clave": "8998"},
                follow_redirects=False,
                environ_overrides={"REMOTE_ADDR": ip},
            )
            assert login.status_code in (302, 303)

            # Simula telemetría intensa de presencia.
            for i in range(6):
                ping = client.post(
                    "/admin/monitoreo/presence/ping",
                    json={
                        "session_id": f"presence-tab-{i}",
                        "current_path": f"/admin/monitoreo?tab={i}",
                        "page_title": "Monitoreo",
                        "route_label": "Control Room",
                    },
                    follow_redirects=False,
                    environ_overrides={"REMOTE_ADDR": ip},
                )
                assert ping.status_code in (200, 429)

            create_resp = client.post(
                "/admin/clientes/nuevo",
                data={
                    "codigo": "",
                    "nombre_completo": "",
                    "email": "",
                    "telefono": "",
                },
                follow_redirects=False,
                environ_overrides={"REMOTE_ADDR": ip},
            )
            assert create_resp.status_code == 200
            html = create_resp.get_data(as_text=True)
            assert "Nuevo Cliente" in html
    finally:
        flask_app.config["TESTING"] = prev_testing
        flask_app.config["WTF_CSRF_ENABLED"] = prev_csrf


def test_admin_login_with_email_does_not_accumulate_security_lock_on_success():
    prev_testing = bool(flask_app.config.get("TESTING"))
    prev_csrf = bool(flask_app.config.get("WTF_CSRF_ENABLED", True))
    flask_app.config["TESTING"] = False
    flask_app.config["WTF_CSRF_ENABLED"] = True

    suffix = uuid.uuid4().hex[:8]
    username = f"sec_email_login_{suffix}"
    email = f"{username}@example.com"
    password = "Pass12345"

    env = {
        "ENABLE_OPERATIONAL_RATE_LIMITS": "1",
        "STAFF_MFA_REQUIRED": "0",
        "LOGIN_RATE_IP_1M": "200",
        "LOGIN_RATE_USER_1M": "200",
        "LOGIN_RATE_IP_1H": "200",
        "LOGIN_RATE_USER_1H": "200",
        # Umbral bajo para detectar rápidamente acumulación incorrecta de intentos.
        "LOGIN_BLOCK_THRESHOLD": "3",
        "LOGIN_DELAY_MS_BASE": "1",
        "ADMIN_LOGIN_MAX_INTENTOS": "200",
    }

    try:
        with flask_app.app_context():
            row = StaffUser(username=username, email=email, role="secretaria", is_active=True, mfa_enabled=False)
            row.set_password(password)
            db.session.add(row)
            db.session.commit()

        with patch.dict(os.environ, env, clear=False):
            ip = "10.31.44.14"
            client = flask_app.test_client()
            statuses = []

            for _ in range(6):
                login_page = client.get("/admin/login", follow_redirects=False, environ_overrides={"REMOTE_ADDR": ip})
                csrf_token = _extract_csrf(login_page.data.decode("utf-8", errors="ignore"))
                assert csrf_token
                resp = _login(client, email, password, ip, csrf_token)
                statuses.append(resp.status_code)

            # Si la limpieza usa el identificador correcto (email), no debe caer en 429.
            assert all(code in (302, 303) for code in statuses), f"statuses={statuses}"
    finally:
        with flask_app.app_context():
            row = StaffUser.query.filter_by(username=username).first()
            if row is not None:
                db.session.delete(row)
                db.session.commit()
        flask_app.config["TESTING"] = prev_testing
        flask_app.config["WTF_CSRF_ENABLED"] = prev_csrf
