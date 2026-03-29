# -*- coding: utf-8 -*-

from app import app as flask_app
from config_app import db
from models import StaffPresenceState, StaffUser
from sqlalchemy import func


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


def test_presence_ping_accepts_snapshot_payload_and_persists_state():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        _ensure_staff_user("SnapshotAdmin", "admin", "8998")
        _ensure_staff_user("SnapshotSec", "secretaria", "9989")
        sec = StaffUser.query.filter(func.lower(StaffUser.username) == "snapshotsec").first()
        assert sec is not None
        db.session.query(StaffPresenceState).filter(StaffPresenceState.user_id == int(sec.id)).delete()
        db.session.commit()

    c_sec = flask_app.test_client()
    c_admin = flask_app.test_client()
    assert _login(c_sec, "SnapshotSec", "9989").status_code in (302, 303)
    assert _login(c_admin, "SnapshotAdmin", "8998").status_code in (302, 303)

    resp = c_sec.post(
        "/admin/monitoreo/presence/ping",
        json={
            "session_id": "tab-presence-1",
            "route": "/admin/solicitudes/590",
            "route_label": "Solicitudes",
            "entity_type": "solicitud",
            "entity_id": "590",
            "entity_name": "Solicitud 590",
            "entity_code": "SOL-590",
            "current_action": "editing_request",
            "action_label": "Editando solicitud 590",
            "tab_visible": True,
            "is_idle": False,
            "is_typing": True,
            "has_unsaved_changes": True,
            "modal_open": True,
            "lock_owner": "SnapshotSec",
            "last_interaction_at": "2026-03-29T03:00:00Z",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 200

    summary = c_admin.get("/admin/monitoreo/summary.json", follow_redirects=False)
    assert summary.status_code == 200
    payload = summary.get_json() or {}
    rows = [r for r in (payload.get("presence") or []) if r.get("username") == "SnapshotSec"]
    assert len(rows) == 1
    row = rows[0]
    assert row.get("session_count") == 1
    assert row.get("entity_type") == "solicitud"
    assert row.get("entity_id") == "590"
    assert bool(row.get("is_typing")) is True
    assert bool(row.get("has_unsaved_changes")) is True
    assert bool(row.get("modal_open")) is True
    assert row.get("lock_owner") == "SnapshotSec"


def test_presence_ping_same_snapshot_does_not_update_updated_at_immediately():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        _ensure_staff_user("SnapshotNoSpam", "secretaria", "9989")
        sec = StaffUser.query.filter(func.lower(StaffUser.username) == "snapshotnospam").first()
        assert sec is not None
        db.session.query(StaffPresenceState).filter(StaffPresenceState.user_id == int(sec.id)).delete()
        db.session.commit()

    c_sec = flask_app.test_client()
    assert _login(c_sec, "SnapshotNoSpam", "9989").status_code in (302, 303)

    payload = {
        "session_id": "tab-nosignal-1",
        "current_path": "/admin/clientes/373",
        "route_label": "Clientes",
        "entity_type": "cliente",
        "entity_id": "373",
        "entity_name": "Cliente 373",
        "current_action": "viewing_client",
        "action_label": "Viendo cliente 373",
        "tab_visible": True,
        "is_idle": False,
        "is_typing": False,
        "has_unsaved_changes": False,
        "modal_open": False,
        "lock_owner": "",
    }

    p1 = c_sec.post("/admin/monitoreo/presence/ping", json=payload, follow_redirects=False)
    assert p1.status_code == 200
    p2 = c_sec.post("/admin/monitoreo/presence/ping", json=payload, follow_redirects=False)
    assert p2.status_code == 200

    with flask_app.app_context():
        row = (
            StaffPresenceState.query
            .join(StaffUser, StaffUser.id == StaffPresenceState.user_id)
            .filter(func.lower(StaffUser.username) == "snapshotnospam")
            .filter(StaffPresenceState.session_id == "tab-nosignal-1")
            .first()
        )
        assert row is not None
        assert row.updated_at == row.started_at
