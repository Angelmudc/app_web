# -*- coding: utf-8 -*-

import re

from app import app as flask_app
from config_app import db
from models import StaffUser
from sqlalchemy import func


_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def _extract_csrf(html: str) -> str:
    m = _CSRF_RE.search(html or "")
    return m.group(1) if m else ""


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


def _login_staff(client, username: str, password: str):
    page = client.get("/admin/login", follow_redirects=False)
    assert page.status_code == 200
    token = _extract_csrf(page.data.decode("utf-8", errors="ignore"))
    assert token
    return client.post(
        "/admin/login",
        data={"usuario": username, "clave": password, "csrf_token": token},
        follow_redirects=False,
    )


def _ping(client):
    page = client.get("/admin/login", follow_redirects=False)
    token = _extract_csrf(page.data.decode("utf-8", errors="ignore"))
    assert token
    return client.post(
        "/admin/monitoreo/presence/ping",
        json={"current_path": "/admin/monitoreo", "page_title": "Monitoreo"},
        headers={"X-CSRFToken": token},
        follow_redirects=False,
    )


def test_presence_ping_security_policy_staff_only():
    # Policy explícita: presence/ping permite staff (admin + secretaria), rechaza no-staff con 403.
    prev_testing = bool(flask_app.config.get("TESTING"))
    prev_csrf = bool(flask_app.config.get("WTF_CSRF_ENABLED", True))
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = True

    try:
        with flask_app.app_context():
            _ensure_staff_user("NoStaff", "cliente", "445566")

        anon = flask_app.test_client()
        anon_resp = _ping(anon)
        assert anon_resp.status_code in (302, 403)

        client_no_staff = flask_app.test_client()
        no_staff_login = _login_staff(client_no_staff, "NoStaff", "445566")
        assert no_staff_login.status_code in (302, 303)
        no_staff_resp = _ping(client_no_staff)
        # El guard global de /admin puede expulsar antes del endpoint (302) o terminar en 403.
        assert no_staff_resp.status_code in (302, 403)

        client_sec = flask_app.test_client()
        sec_login = _login_staff(client_sec, "Karla", "9989")
        assert sec_login.status_code in (302, 303)
        sec_resp = _ping(client_sec)
        assert sec_resp.status_code == 200

        client_admin = flask_app.test_client()
        admin_login = _login_staff(client_admin, "Cruz", "8998")
        assert admin_login.status_code in (302, 303)
        admin_resp = _ping(client_admin)
        assert admin_resp.status_code == 200
    finally:
        flask_app.config["TESTING"] = prev_testing
        flask_app.config["WTF_CSRF_ENABLED"] = prev_csrf
