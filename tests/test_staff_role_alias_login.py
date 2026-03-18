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


def _upsert_staff(username: str, role: str, password: str):
    with flask_app.app_context():
        row = StaffUser.query.filter(func.lower(StaffUser.username) == username.lower()).first()
        if row is None:
            row = StaffUser(username=username, role=role, is_active=True)
            db.session.add(row)
        row.role = role
        row.is_active = True
        row.set_password(password)
        db.session.commit()


def _login_staff(client, username: str, password: str):
    page = client.get("/admin/login", follow_redirects=False)
    token = _extract_csrf(page.data.decode("utf-8", errors="ignore"))
    assert token
    return client.post(
        "/admin/login",
        data={"usuario": username, "clave": password, "csrf_token": token},
        follow_redirects=False,
    )


def _presence_ping(client):
    page = client.get("/admin/login", follow_redirects=False)
    token = _extract_csrf(page.data.decode("utf-8", errors="ignore"))
    assert token
    return client.post(
        "/admin/monitoreo/presence/ping",
        json={"current_path": "/admin/monitoreo", "page_title": "Monitoreo"},
        headers={"X-CSRFToken": token},
        follow_redirects=False,
    )


def test_secretary_alias_can_login_and_access_staff_ping():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = True
    _upsert_staff("SecAlias", "secretary", "12345678")

    client = flask_app.test_client()
    login_resp = _login_staff(client, "SecAlias", "12345678")
    assert login_resp.status_code in (302, 303)

    ping_resp = _presence_ping(client)
    assert ping_resp.status_code == 200


def test_invalid_staff_role_stays_denied_after_login():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = True
    _upsert_staff("BadRole", "cliente", "12345678")

    client = flask_app.test_client()
    login_resp = _login_staff(client, "BadRole", "12345678")
    assert login_resp.status_code in (302, 303)

    ping_resp = _presence_ping(client)
    assert ping_resp.status_code in (302, 403)
