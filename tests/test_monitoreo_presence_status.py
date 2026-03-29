# -*- coding: utf-8 -*-

import re
from datetime import datetime, timedelta

from app import app as flask_app
from config_app import db
from models import StaffPresenceState, StaffUser
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
            db.session.query(StaffPresenceState).delete()
            db.session.commit()

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

            old_seen_dt = now - timedelta(seconds=45)
            old_interaction_dt = now - timedelta(minutes=2)
            db.session.add(
                StaffPresenceState(
                    user_id=int(anyi.id),
                    session_id="session-anyi",
                    route="/admin/monitoreo/logs",
                    page_title="Logs",
                    client_status="idle",
                    current_action="logs",
                    action_label="Revisando logs",
                    tab_visible=True,
                    is_idle=True,
                    last_seen_at=now,
                    last_interaction_at=old_interaction_dt,
                    started_at=old_interaction_dt,
                    updated_at=now,
                    state_hash="seed-anyi",
                )
            )
            db.session.add(
                StaffPresenceState(
                    user_id=int(cruz.id),
                    session_id="session-cruz",
                    route="/admin/monitoreo/candidatas/22",
                    page_title="Historial candidata",
                    client_status="hidden",
                    current_action="candidatas",
                    action_label="Viendo candidata",
                    tab_visible=False,
                    is_idle=False,
                    last_seen_at=now,
                    last_interaction_at=now,
                    started_at=now,
                    updated_at=now,
                    state_hash="seed-cruz",
                )
            )
            db.session.add(
                StaffPresenceState(
                    user_id=int(maria.id),
                    session_id="session-maria",
                    route="/admin/monitoreo",
                    page_title="Monitoreo",
                    client_status="active",
                    current_action="dashboard",
                    action_label="Viendo control room",
                    tab_visible=True,
                    is_idle=False,
                    last_seen_at=old_seen_dt,
                    last_interaction_at=old_interaction_dt,
                    started_at=old_interaction_dt,
                    updated_at=old_seen_dt,
                    state_hash="seed-maria",
                )
            )
            db.session.commit()

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
