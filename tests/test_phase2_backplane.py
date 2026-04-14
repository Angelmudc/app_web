# -*- coding: utf-8 -*-

from unittest.mock import patch

from app import app as flask_app
from utils.distributed_backplane import BackplaneUnavailable, bp_get, bp_incr


def _login(client, usuario, clave):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def test_locks_ping_returns_503_when_backplane_unavailable():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    client = flask_app.test_client()
    assert _login(client, "Cruz", "8998").status_code in (302, 303)

    with patch(
        "admin.routes.lock_ping",
        return_value={
            "ok": False,
            "error": "distributed_backplane_unavailable",
            "message": "Lock temporalmente no disponible.",
        },
    ):
        resp = client.post(
            "/admin/seguridad/locks/ping",
            json={"entity_type": "candidata", "entity_id": "100"},
            follow_redirects=False,
        )

    assert resp.status_code == 503
    payload = resp.get_json() or {}
    assert payload.get("error") == "distributed_backplane_unavailable"


def test_locks_conflict_still_returns_readonly_with_backplane():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    owner = flask_app.test_client()
    admin = flask_app.test_client()
    assert _login(owner, "Owner", "admin123").status_code in (302, 303)
    assert _login(admin, "Cruz", "8998").status_code in (302, 303)

    r1 = owner.post(
        "/admin/seguridad/locks/ping",
        json={"entity_type": "candidata", "entity_id": "3991", "current_path": "/buscar?candidata_id=3991"},
        follow_redirects=False,
    )
    assert r1.status_code == 200
    assert (r1.get_json() or {}).get("state") == "owner"

    r2 = admin.post(
        "/admin/seguridad/locks/ping",
        json={"entity_type": "candidata", "entity_id": "3991", "current_path": "/buscar?candidata_id=3991"},
        follow_redirects=False,
    )
    assert r2.status_code == 200
    assert (r2.get_json() or {}).get("state") == "readonly"


def test_locks_ping_infers_solicitud_from_current_path_when_payload_is_incomplete():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    client = flask_app.test_client()
    assert _login(client, "Cruz", "8998").status_code in (302, 303)

    resp = client.post(
        "/admin/seguridad/locks/ping",
        json={"entity_type": "", "entity_id": "", "current_path": "/admin/clientes/337/solicitudes/731/editar"},
        follow_redirects=False,
    )

    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert payload.get("ok") is True
    lock = payload.get("lock") or {}
    assert lock.get("entity_type") == "solicitud"
    assert str(lock.get("entity_id") or "") == "731"


def test_locks_ping_returns_explicit_invalid_payload_details_when_entity_cannot_be_inferred():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    client = flask_app.test_client()
    assert _login(client, "Cruz", "8998").status_code in (302, 303)

    resp = client.post(
        "/admin/seguridad/locks/ping",
        json={"entity_type": "", "entity_id": "", "current_path": "/admin/seguridad/locks"},
        follow_redirects=False,
    )

    assert resp.status_code == 400
    payload = resp.get_json() or {}
    assert payload.get("error_code") == "invalid_entity_payload"
    assert payload.get("ok") is False
    missing_fields = set(payload.get("missing_fields") or [])
    assert "entity_type" in missing_fields
    assert "entity_id" in missing_fields


def test_admin_guard_fail_closed_when_backplane_session_control_is_unavailable():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    client = flask_app.test_client()
    assert _login(client, "Cruz", "8998").status_code in (302, 303)

    with patch("admin.routes.touch_staff_session", return_value={"ok": False, "reason": "backplane_unavailable"}):
        resp = client.get("/admin/monitoreo", follow_redirects=False)

    assert resp.status_code == 503
    assert "/admin/login" in (resp.headers.get("Location") or "")


def test_backplane_incr_fail_open_when_non_strict():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        flask_app.config["DISTRIBUTED_BACKPLANE_STRICT_RUNTIME"] = False
        with patch("utils.distributed_backplane.cache.get", side_effect=RuntimeError("cache down")):
            value = bp_incr("phase2:test:incr", timeout=15, strict=False, context="test_backplane")
    assert value == 0


def test_backplane_get_strict_raises():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        flask_app.config["DISTRIBUTED_BACKPLANE_STRICT_RUNTIME"] = False
        with patch("utils.distributed_backplane.cache.get", side_effect=RuntimeError("cache down")):
            try:
                bp_get("phase2:test:get", strict=True, context="test_backplane")
                assert False, "bp_get strict debía lanzar BackplaneUnavailable"
            except BackplaneUnavailable:
                pass
