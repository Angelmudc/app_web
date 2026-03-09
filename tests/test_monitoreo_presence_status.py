# -*- coding: utf-8 -*-

import re
from datetime import datetime, timedelta

from app import app as flask_app
from config_app import db, cache
from models import StaffUser
from admin.routes import _presence_key, _PRESENCE_INDEX_KEY
from sqlalchemy import func


_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def _extract_csrf(html: str) -> str:
    m = _CSRF_RE.search(html or "")
    return m.group(1) if m else ""


def _ensure_staff_user(username: str, role: str, password: str) -> StaffUser:
    row = StaffUser.query.filter(func.lower(StaffUser.username) == username.lower()).first()
    if row is None:
        row = StaffUser(username=username, role=role, is_active=True)
        row.set_password(password)
        db.session.add(row)
        db.session.commit()
        return row
    row.role = role
    row.is_active = True
    row.set_password(password)
    db.session.commit()
    return row


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


def test_monitoreo_presence_status_rich_payload_and_summary_states():
    prev_testing = bool(flask_app.config.get("TESTING"))
    prev_csrf = bool(flask_app.config.get("WTF_CSRF_ENABLED", True))
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = True

    try:
        with flask_app.app_context():
            karla = _ensure_staff_user("KarlaStatus", "secretaria", "9989")
            anyi = _ensure_staff_user("AnyiStatus", "secretaria", "9989")
            cruz = _ensure_staff_user("CruzStatus", "admin", "8998")
            maria = _ensure_staff_user("MariaInactiveStatus", "secretaria", "9989")
            _ensure_staff_user("ViewerStatus", "admin", "8998")

            cache.delete(_presence_key(int(karla.id)))
            cache.delete(_presence_key(int(anyi.id)))
            cache.delete(_presence_key(int(cruz.id)))
            cache.delete(_presence_key(int(maria.id)))
            cache.delete(_PRESENCE_INDEX_KEY)

        client_karla = flask_app.test_client()
        login_karla = _login_staff(client_karla, "KarlaStatus", "9989")
        assert login_karla.status_code in (302, 303)

        ping_page = client_karla.get("/admin/login", follow_redirects=False)
        ping_token = _extract_csrf(ping_page.data.decode("utf-8", errors="ignore"))
        assert ping_token
        now = datetime.utcnow()
        ping_resp = client_karla.post(
            "/admin/monitoreo/presence/ping",
            json={
                "current_path": "/admin/monitoreo",
                "page_title": "Monitoreo",
                "client_status": "active",
                "last_interaction_at": now.isoformat(timespec="seconds") + "Z",
            },
            headers={"X-CSRFToken": ping_token},
            follow_redirects=False,
        )
        assert ping_resp.status_code == 200
        assert ping_resp.get_json().get("ok") is True

        with flask_app.app_context():
            anyi = StaffUser.query.filter(func.lower(StaffUser.username) == "anyistatus").first()
            cruz = StaffUser.query.filter(func.lower(StaffUser.username) == "cruzstatus").first()
            maria = StaffUser.query.filter(func.lower(StaffUser.username) == "mariainactivestatus").first()
            assert anyi and cruz and maria

            now_iso = now.isoformat(timespec="seconds") + "Z"
            old_seen_iso = (now - timedelta(seconds=45)).isoformat(timespec="seconds") + "Z"
            old_interaction_iso = (now - timedelta(minutes=2)).isoformat(timespec="seconds") + "Z"

            cache.set(
                _presence_key(int(anyi.id)),
                {
                        "user_id": int(anyi.id),
                        "username": "AnyiStatus",
                    "role": "secretaria",
                    "current_path": "/admin/monitoreo/logs",
                    "page_title": "Logs",
                    "last_seen_at": now_iso,
                    "last_interaction_at": old_interaction_iso,
                    "client_status": "idle",
                    "action_type": "LIVE_HEARTBEAT",
                    "action_hint": "logs",
                },
                timeout=65,
            )
            cache.set(
                _presence_key(int(cruz.id)),
                {
                        "user_id": int(cruz.id),
                        "username": "CruzStatus",
                    "role": "admin",
                    "current_path": "/admin/monitoreo/candidatas/22",
                    "page_title": "Historial candidata",
                    "last_seen_at": now_iso,
                    "last_interaction_at": now_iso,
                    "client_status": "hidden",
                    "action_type": "LIVE_HEARTBEAT",
                    "action_hint": "candidatas",
                },
                timeout=65,
            )
            cache.set(
                _presence_key(int(maria.id)),
                {
                        "user_id": int(maria.id),
                        "username": "MariaInactiveStatus",
                    "role": "secretaria",
                    "current_path": "/admin/monitoreo",
                    "page_title": "Monitoreo",
                    "last_seen_at": old_seen_iso,
                    "last_interaction_at": old_interaction_iso,
                    "client_status": "active",
                    "action_type": "LIVE_HEARTBEAT",
                    "action_hint": "dashboard",
                },
                timeout=65,
            )
            cache.set(_PRESENCE_INDEX_KEY, [int(karla.id), int(anyi.id), int(cruz.id), int(maria.id)], timeout=3600)

        client_admin = flask_app.test_client()
        login_admin = _login_staff(client_admin, "ViewerStatus", "8998")
        assert login_admin.status_code in (302, 303)

        summary_resp = client_admin.get("/admin/monitoreo/summary.json", follow_redirects=False)
        assert summary_resp.status_code == 200
        summary = summary_resp.get_json() or {}
        by_user = {str(p.get("username")): p for p in (summary.get("presence") or [])}

        assert by_user.get("KarlaStatus", {}).get("status") == "active"
        assert by_user.get("AnyiStatus", {}).get("status") == "idle"
        assert by_user.get("CruzStatus", {}).get("status") == "hidden"
        assert by_user.get("MariaInactiveStatus", {}).get("status") == "inactive"
        assert by_user.get("KarlaStatus", {}).get("client_status") == "active"
        assert by_user.get("KarlaStatus", {}).get("last_interaction_at")
    finally:
        flask_app.config["TESTING"] = prev_testing
        flask_app.config["WTF_CSRF_ENABLED"] = prev_csrf
