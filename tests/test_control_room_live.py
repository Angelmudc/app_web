# -*- coding: utf-8 -*-

from datetime import datetime
from pathlib import Path

from app import app as flask_app
from config_app import db
from models import StaffAuditLog, StaffUser


def _login(client, usuario, clave):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def test_control_room_presence_global_and_entity_resolution():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    client_sec = flask_app.test_client()
    client_admin = flask_app.test_client()

    assert _login(client_sec, "Karla", "9989").status_code in (302, 303)
    ping = client_sec.post(
        "/admin/monitoreo/presence/ping",
        json={
            "current_path": "/admin/entrevista?candidata_id=3135",
            "event_type": "open_entity",
            "action_hint": "editing_interview",
            "candidata_id": "3135",
            "entity_name": "Brenda Perez",
            "entity_code": "DOM-1023",
        },
        follow_redirects=False,
    )
    assert ping.status_code == 200

    assert _login(client_admin, "Cruz", "8998").status_code in (302, 303)
    summary = client_admin.get("/admin/monitoreo/summary.json", follow_redirects=False)
    assert summary.status_code == 200
    payload = summary.get_json() or {}
    rows = [p for p in (payload.get("presence") or []) if p.get("username") == "Karla"]
    assert rows
    row = rows[0]
    assert row.get("status") == "active"
    assert row.get("current_path", "").startswith("/admin/entrevista")
    action_txt = (row.get("current_action_human") or "").lower()
    assert ("entrevista" in action_txt) or ("abrio" in action_txt)
    assert "DOM-1023" in (row.get("entity_display") or "")


def test_control_room_logs_human_action_mapping_and_entity_name_code():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    client_admin = flask_app.test_client()
    assert _login(client_admin, "Cruz", "8998").status_code in (302, 303)

    with flask_app.app_context():
        admin = StaffUser.query.filter_by(username="Cruz").first()
        assert admin is not None
        db.session.add(
            StaffAuditLog(
                created_at=datetime.utcnow(),
                actor_user_id=admin.id,
                actor_role="admin",
                action_type="CANDIDATA_INTERVIEW_NEW_CREATE",
                entity_type="candidata",
                entity_id="3135",
                summary="CANDIDATA_INTERVIEW_NEW_CREATE",
                metadata_json={"entity_name": "Brenda Perez", "entity_code": "DOM-1023"},
                success=True,
            )
        )
        db.session.commit()

    resp = client_admin.get("/admin/monitoreo/logs.json?action_type=CANDIDATA_INTERVIEW_NEW_CREATE&limit=10", follow_redirects=False)
    assert resp.status_code == 200
    data = resp.get_json() or {}
    items = data.get("items") or []
    assert items
    item = items[-1]
    assert item.get("action_human") == "Guardo entrevista"
    assert "DOM-1023" in (item.get("entity_display") or "")


def test_control_room_template_includes_versioned_live_bundle_bootstrap():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    client_admin = flask_app.test_client()
    assert _login(client_admin, "Cruz", "8998").status_code in (302, 303)

    resp = client_admin.get("/admin/monitoreo", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="ignore")
    assert "window.__monitoreoLiveExpectedVersion" in html
    assert "js/monitoreo_live.js?v=2026-03-29.4" in html
    assert "data-monitoreo-live-rescue" in html


def test_control_room_live_bundle_exposes_boot_status_and_sse_fallback():
    js_path = Path("static/js/monitoreo_live.js")
    text = js_path.read_text(encoding="utf-8", errors="ignore")
    assert "window.__monitoreoLiveBoot" in text
    assert "window.__monitoreoLiveStatus" in text
    assert "SSE init fallido, fallback a polling" in text
