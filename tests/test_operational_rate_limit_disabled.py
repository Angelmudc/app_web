# -*- coding: utf-8 -*-

import re
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import app as flask_app
from config_app import db
from models import StaffUser
from tests.t1_testkit import ensure_sqlite_compat_tables


_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def _extract_csrf(html: str) -> str:
    m = _CSRF_RE.search(html or "")
    return m.group(1) if m else ""


def _login(client, usuario: str, clave: str, ip: str):
    page = client.get("/admin/login", follow_redirects=False, environ_overrides={"REMOTE_ADDR": ip})
    assert page.status_code == 200
    token = _extract_csrf(page.data.decode("utf-8", errors="ignore"))
    assert token
    return client.post(
        "/admin/login",
        data={"usuario": usuario, "clave": clave, "csrf_token": token},
        follow_redirects=False,
        environ_overrides={"REMOTE_ADDR": ip},
    )


def _ensure_operational_staff():
    with flask_app.app_context():
        ensure_sqlite_compat_tables([StaffUser], reset=False)
        user = StaffUser.query.filter_by(username="round9-ops-owner").first()
        if user is None:
            user = StaffUser(username="round9-ops-owner", role="admin", is_active=True)
            db.session.add(user)
        user.password_hash = generate_password_hash("round9-ops-password", method="pbkdf2:sha256")
        user.is_active = True
        user.mfa_enabled = False
        db.session.commit()


def test_development_login_rate_limit_active_blocks_sixth_attempt():
    """La política única: login rate limit activo también en development."""
    prev_testing = bool(flask_app.config.get("TESTING"))
    prev_csrf = bool(flask_app.config.get("WTF_CSRF_ENABLED", True))
    flask_app.config["TESTING"] = False
    flask_app.config["WTF_CSRF_ENABLED"] = True

    try:
        ip = "10.77.10.101"
        client = flask_app.test_client()
        statuses = []
        for _ in range(7):
            resp = _login(client, "round8-explicit-rate-limit", "clave-incorrecta", ip)
            statuses.append(resp.status_code)
        assert all(status != 429 for status in statuses[:5])
        assert statuses[5] == 429
        assert statuses[6] == 429
    finally:
        flask_app.config["TESTING"] = prev_testing
        flask_app.config["WTF_CSRF_ENABLED"] = prev_csrf


def test_internal_routes_monitoring_and_forms_not_rate_blocked():
    prev_testing = bool(flask_app.config.get("TESTING"))
    prev_csrf = bool(flask_app.config.get("WTF_CSRF_ENABLED", True))
    flask_app.config["TESTING"] = False
    flask_app.config["WTF_CSRF_ENABLED"] = True
    _ensure_operational_staff()

    try:
        # Este test valida rate limiting operativo, no flujo MFA.
        with patch.dict("os.environ", {"STAFF_MFA_REQUIRED": "0", "ENABLE_LOGIN_RATE_LIMITS": "0"}, clear=False):
            ip = "10.77.10.2"
            client = flask_app.test_client()
            login = _login(client, "round9-ops-owner", "round9-ops-password", ip)
            assert login.status_code in (302, 303)

            # GET interno repetido (monitoreo)
            for _ in range(80):
                r = client.get("/admin/monitoreo/summary.json", follow_redirects=False, environ_overrides={"REMOTE_ADDR": ip})
                assert r.status_code == 200

            # POST interno repetido (form/realtime)
            page = client.get("/admin/login", follow_redirects=False, environ_overrides={"REMOTE_ADDR": ip})
            token = _extract_csrf(page.data.decode("utf-8", errors="ignore"))
            assert token
            for _ in range(20):
                p = client.post(
                    "/admin/monitoreo/presence/ping",
                    json={"current_path": "/admin/monitoreo", "page_title": "Monitoreo"},
                    headers={"X-CSRFToken": token},
                    follow_redirects=False,
                    environ_overrides={"REMOTE_ADDR": ip},
                )
                assert p.status_code == 200
    finally:
        flask_app.config["TESTING"] = prev_testing
        flask_app.config["WTF_CSRF_ENABLED"] = prev_csrf


def test_admin_form_posts_not_blocked_by_global_admin_action_guard_default_off():
    prev_testing = bool(flask_app.config.get("TESTING"))
    prev_csrf = bool(flask_app.config.get("WTF_CSRF_ENABLED", True))
    flask_app.config["TESTING"] = False
    flask_app.config["WTF_CSRF_ENABLED"] = True
    _ensure_operational_staff()

    try:
        with patch.dict(
            "os.environ",
            {
                "ENABLE_OPERATIONAL_RATE_LIMITS": "1",
                "ENABLE_LOGIN_RATE_LIMITS": "0",
                "STAFF_MFA_REQUIRED": "0",
                "ADMIN_ACTION_MAX": "1",
                "ADMIN_ACTION_WINDOW": "60",
                "ADMIN_ACTION_LOCK": "120",
                "ENABLE_ADMIN_GLOBAL_ACTION_GUARD": "0",
            },
            clear=False,
        ):
            ip = "10.77.10.3"
            client = flask_app.test_client()
            login = _login(client, "round9-ops-owner", "round9-ops-password", ip)
            assert login.status_code in (302, 303)

            for _ in range(3):
                page = client.get("/admin/clientes/nuevo", follow_redirects=False, environ_overrides={"REMOTE_ADDR": ip})
                assert page.status_code == 200
                token = _extract_csrf(page.data.decode("utf-8", errors="ignore"))
                assert token
                resp = client.post(
                    "/admin/clientes/nuevo",
                    data={"nombre_completo": "", "csrf_token": token},
                    follow_redirects=False,
                    environ_overrides={"REMOTE_ADDR": ip},
                )
                assert resp.status_code == 200
    finally:
        flask_app.config["TESTING"] = prev_testing
        flask_app.config["WTF_CSRF_ENABLED"] = prev_csrf


def test_authenticated_requests_not_throttled_by_scrape_guard_default_off():
    prev_testing = bool(flask_app.config.get("TESTING"))
    prev_csrf = bool(flask_app.config.get("WTF_CSRF_ENABLED", True))
    flask_app.config["TESTING"] = False
    flask_app.config["WTF_CSRF_ENABLED"] = True
    _ensure_operational_staff()

    try:
        with patch.dict(
            "os.environ",
            {
                "ENABLE_OPERATIONAL_RATE_LIMITS": "1",
                "ENABLE_LOGIN_RATE_LIMITS": "0",
                "STAFF_MFA_REQUIRED": "0",
                "STAFF_WORK_MAX_REQ": "1",
                "AUTH_WORK_MAX_REQ": "1",
                "SCRAPE_ADMIN_MAX_REQ": "1",
                "ENABLE_AUTHENTICATED_OPERATIONAL_RATE_LIMITS": "0",
            },
            clear=False,
        ):
            ip = "10.77.10.4"
            client = flask_app.test_client()
            login = _login(client, "round9-ops-owner", "round9-ops-password", ip)
            assert login.status_code in (302, 303)

            for _ in range(6):
                resp = client.get("/admin/clientes", follow_redirects=False, environ_overrides={"REMOTE_ADDR": ip})
                assert resp.status_code != 429
    finally:
        flask_app.config["TESTING"] = prev_testing
        flask_app.config["WTF_CSRF_ENABLED"] = prev_csrf
