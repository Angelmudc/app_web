# -*- coding: utf-8 -*-

from datetime import datetime, timedelta

from app import app as flask_app
from config_app import db
from models import StaffPresenceState, StaffUser
from sqlalchemy import func
from utils.staff_presence import build_presence_snapshot, upsert_staff_presence_snapshot


def _login(client, usuario, clave):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


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


def test_presence_state_upsert_avoids_unnecessary_writes():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        user = _ensure_staff_user("PresenceWriter", "secretaria", "9989")
        db.session.query(StaffPresenceState).filter(StaffPresenceState.user_id == int(user.id)).delete()
        db.session.commit()

        base_ts = datetime.utcnow().replace(microsecond=0)
        snapshot = build_presence_snapshot(
            {
                "route": "/admin/solicitudes",
                "route_label": "Solicitudes",
                "current_action": "solicitudes",
                "action_label": "Revisando solicitudes",
                "client_status": "active",
                "tab_visible": True,
            },
            fallback_route="/admin/solicitudes",
        )

        first = upsert_staff_presence_snapshot(
            user_id=int(user.id),
            session_id="tab-1",
            snapshot=snapshot,
            now=base_ts,
            touch_min_seconds=30,
        )
        assert first.get("ok") is True
        assert first.get("write_kind") == "insert"

        second = upsert_staff_presence_snapshot(
            user_id=int(user.id),
            session_id="tab-1",
            snapshot=snapshot,
            now=base_ts + timedelta(seconds=1),
            touch_min_seconds=30,
        )
        assert second.get("write_kind") == "noop"

        third = upsert_staff_presence_snapshot(
            user_id=int(user.id),
            session_id="tab-1",
            snapshot=snapshot,
            now=base_ts + timedelta(seconds=40),
            touch_min_seconds=30,
        )
        assert third.get("write_kind") == "touch"

        row = (
            StaffPresenceState.query
            .filter(
                StaffPresenceState.user_id == int(user.id),
                StaffPresenceState.session_id == "tab-1",
            )
            .first()
        )
        assert row is not None
        assert row.updated_at == base_ts
        assert row.last_seen_at == (base_ts + timedelta(seconds=40))


def test_presence_summary_groups_sessions_by_user():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        _ensure_staff_user("PresenceViewer", "admin", "8998")
        _ensure_staff_user("PresenceSec", "secretaria", "9989")
        sec = StaffUser.query.filter(func.lower(StaffUser.username) == "presencesec").first()
        assert sec is not None
        db.session.query(StaffPresenceState).filter(StaffPresenceState.user_id == int(sec.id)).delete()
        db.session.commit()

    c_sec = flask_app.test_client()
    c_admin = flask_app.test_client()
    assert _login(c_sec, "PresenceSec", "9989").status_code in (302, 303)
    assert _login(c_admin, "PresenceViewer", "8998").status_code in (302, 303)

    p1 = c_sec.post(
        "/admin/monitoreo/presence/ping",
        json={
            "session_id": "tab-a",
            "current_path": "/admin/clientes/373",
            "event_type": "open_entity",
            "action_hint": "viewing_client",
            "entity_type": "cliente",
            "entity_id": "373",
            "entity_name": "Cliente 373",
        },
        follow_redirects=False,
    )
    assert p1.status_code == 200

    p2 = c_sec.post(
        "/admin/monitoreo/presence/ping",
        json={
            "session_id": "tab-b",
            "current_path": "/admin/solicitudes/590",
            "event_type": "open_entity",
            "action_hint": "editing_request",
            "entity_type": "solicitud",
            "entity_id": "590",
            "entity_name": "Solicitud 590",
        },
        follow_redirects=False,
    )
    assert p2.status_code == 200

    summary = c_admin.get("/admin/monitoreo/summary.json", follow_redirects=False)
    assert summary.status_code == 200
    payload = summary.get_json() or {}
    rows = [p for p in (payload.get("presence") or []) if p.get("username") == "PresenceSec"]
    assert len(rows) == 1
    row = rows[0]
    assert int(row.get("session_count") or 0) == 2
    assert str(row.get("status_human") or "")
    assert str(row.get("supervision_human") or "")
    sessions = row.get("sessions") or []
    assert len(sessions) == 2
    assert all(str(s.get("status_human") or "") for s in sessions)
