# -*- coding: utf-8 -*-

from pathlib import Path

from app import app as flask_app


def _login(client, usuario, clave):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def test_sse_once_includes_active_snapshot():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client, "Cruz", "8998").status_code in (302, 303)
    resp = client.get("/admin/monitoreo/stream?once=1", follow_redirects=False)
    assert resp.status_code == 200
    body = resp.data.decode("utf-8", errors="ignore")
    assert "event: active_snapshot" in body
    assert '"interval_sec": 1' in body


def test_summary_has_operations_and_activity_stream():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    client_sec = flask_app.test_client()
    client_admin = flask_app.test_client()
    assert _login(client_sec, "Karla", "9989").status_code in (302, 303)
    p = client_sec.post(
        "/admin/monitoreo/presence/ping",
        json={
            "current_path": "/admin/solicitudes?solicitud_id=412",
            "event_type": "open_entity",
            "action_hint": "solicitudes",
            "solicitud_id": "412",
            "entity_name": "SOL-412",
        },
        follow_redirects=False,
    )
    assert p.status_code == 200

    assert _login(client_admin, "Cruz", "8998").status_code in (302, 303)
    summary = client_admin.get("/admin/monitoreo/summary.json", follow_redirects=False)
    assert summary.status_code == 200
    data = summary.get_json() or {}
    assert "operations" in data
    assert "activity_stream" in data
    assert isinstance(data.get("activity_stream"), list)


def test_multi_user_conflict_detection_same_candidate():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    c1 = flask_app.test_client()
    c2 = flask_app.test_client()
    c_admin = flask_app.test_client()
    assert _login(c1, "Karla", "9989").status_code in (302, 303)
    assert _login(c2, "Anyi", "0931").status_code in (302, 303)
    for c in (c1, c2):
        r = c.post(
            "/admin/monitoreo/presence/ping",
            json={
                "current_path": "/admin/entrevista?candidata_id=2870",
                "event_type": "open_entity",
                "action_hint": "editing",
                "candidata_id": "2870",
                "entity_name": "Brenda Perez",
                "entity_code": "COD-1032",
            },
            follow_redirects=False,
        )
        assert r.status_code == 200

    assert _login(c_admin, "Cruz", "8998").status_code in (302, 303)
    summary = c_admin.get("/admin/monitoreo/summary.json", follow_redirects=False)
    payload = summary.get_json() or {}
    conflicts = payload.get("presence_conflicts") or []
    assert conflicts
    first = conflicts[0]
    assert first.get("entity_id") == "2870"
    assert len(first.get("users") or []) >= 2


def test_human_route_and_action_mappings():
    from admin import routes as ar

    assert ar._humanize_action("LIVE_PAGE_LOAD") == "Abrio pantalla"
    assert ar._humanize_action("LIVE_HEARTBEAT") == "Activo"
    assert ar._humanize_action("LIVE_TAB_FOCUS") == "Volvio a la app"
    assert ar._humanize_action("LIVE_OPEN_ENTITY") == "Abrio entidad"
    assert ar._humanize_action(None, route="/admin/matching", action_hint="matching") == "En Matching"
    assert ar._humanize_route("/entrevistas/buscar") == "Entrevistas: buscar"


def test_heartbeat_base_is_5_seconds():
    js_path = Path("static/js/core/control_room_presence.js")
    text = js_path.read_text(encoding="utf-8", errors="ignore")
    assert "const HEARTBEAT_MS = 5000;" in text
