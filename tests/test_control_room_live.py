# -*- coding: utf-8 -*-

from datetime import datetime
from pathlib import Path

from app import app as flask_app
from config_app import db
from models import StaffAuditLog, StaffUser

_ADMIN_RUNTIME_ASSETS = (
    "js/core/admin_async.js",
    "js/core/admin_nav.js",
    "js/core/entity_lock.js",
    "js/core/live_invalidation.js",
    "js/chat/chat_global_badge.js",
    "js/core/control_room_presence.js",
    "js/core/live-refresh.js",
    "js/admin/solicitud_detail_ui.js",
)


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
    assert "js/monitoreo_live.js?v=2026-03-29.7" in html
    assert "data-monitoreo-live-rescue" in html


def test_control_room_live_bundle_exposes_boot_status_and_sse_fallback():
    js_path = Path("static/js/monitoreo_live.js")
    text = js_path.read_text(encoding="utf-8", errors="ignore")
    assert "window.__monitoreoLiveBoot" in text
    assert "window.__monitoreoLiveStatus" in text
    assert "SSE init fallido, fallback a polling" in text
    assert "[monitoreo] init file loaded" in text
    assert "[monitoreo] shell found" in text
    assert "[monitoreo] ready" in text


def test_home_does_not_include_control_room_presence_bundle():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    client = flask_app.test_client()
    resp = client.get("/home", follow_redirects=False)
    assert resp.status_code in (302, 303)
    location = resp.headers.get("Location") or ""
    assert ("/admin/login" in location) or ("/login" in location)
    login_resp = client.get(location, follow_redirects=False)
    assert login_resp.status_code == 200
    html = login_resp.data.decode("utf-8", errors="ignore")
    assert "js/core/control_room_presence.js" not in html
    assert 'data-live-presence-enabled="0"' in html


def test_login_page_does_not_include_control_room_presence_bundle():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    client = flask_app.test_client()
    resp = client.get("/login", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="ignore")
    assert "js/core/control_room_presence.js" not in html
    assert 'data-live-presence-enabled="0"' in html


def test_public_root_does_not_include_control_room_presence_bundle():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    client = flask_app.test_client()
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="ignore")
    assert "js/core/control_room_presence.js" not in html


def test_public_and_non_admin_views_do_not_include_admin_runtime_assets():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    client = flask_app.test_client()
    assert _login(client, "Karla", "9989").status_code in (302, 303)

    for url in ("/login", "/home", "/referencias"):
        resp = client.get(url, follow_redirects=True)
        assert resp.status_code == 200
        html = resp.data.decode("utf-8", errors="ignore")
        for asset in _ADMIN_RUNTIME_ASSETS:
            assert asset not in html, f"{url} no debe cargar {asset}"
        assert 'data-chat-global-badge-enabled="0"' in html
        assert 'data-live-presence-enabled="0"' in html
        assert "/admin/live/invalidation/poll" not in html
        assert "/admin/live/invalidation/stream" not in html
        assert "/admin/chat/badge.json" not in html


def test_admin_core_views_keep_runtime_where_it_is_needed():
    base_txt = Path("templates/base.html").read_text(encoding="utf-8", errors="ignore")
    assert "{% set _admin_light_runtime_endpoints = ['admin.tareas_hoy'] %}" in base_txt
    assert "{% set _admin_core_runtime_enabled = _is_admin_runtime_page and (not _is_admin_light_runtime_view) %}" in base_txt
    assert "{% if _admin_core_runtime_enabled %}" in base_txt
    assert "js/core/admin_async.js" in base_txt
    assert "js/core/admin_nav.js" in base_txt
    assert "js/core/entity_lock.js" in base_txt
    assert "js/chat/chat_global_badge.js" in base_txt
    assert "js/core/live_invalidation.js" in base_txt
    assert "data-chat-global-badge-enabled" in base_txt

    solicitudes_txt = Path("templates/admin/solicitudes_list.html").read_text(encoding="utf-8", errors="ignore")
    clientes_txt = Path("templates/admin/clientes_list.html").read_text(encoding="utf-8", errors="ignore")
    prioridad_txt = Path("templates/admin/solicitudes_prioridad.html").read_text(encoding="utf-8", errors="ignore")
    assert "{% extends 'base.html' %}" in solicitudes_txt
    assert "{% extends 'base.html' %}" in clientes_txt
    assert "{% extends 'base.html' %}" in prioridad_txt
    assert 'data-live-invalidation-scope="1"' in solicitudes_txt
    assert 'data-live-invalidation-scope="1"' in prioridad_txt


def test_solicitudes_views_use_single_live_runtime_channel_for_chat_badge():
    live_txt = Path("static/js/core/live_invalidation.js").read_text(encoding="utf-8", errors="ignore")
    badge_txt = Path("static/js/chat/chat_global_badge.js").read_text(encoding="utf-8", errors="ignore")

    assert "admin:live-invalidation-event" in live_txt
    assert "shouldUseSharedLiveChannel" in badge_txt
    assert "data-live-invalidation-view" in badge_txt
    assert "solicitudes_summary" in badge_txt
    assert "solicitudes_prioridad_summary" in badge_txt
    assert "if (sharedLiveChannel) return;" in badge_txt
