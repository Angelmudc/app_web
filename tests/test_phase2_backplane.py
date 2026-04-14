# -*- coding: utf-8 -*-

import os
from unittest.mock import patch

from app import app as flask_app
from utils.distributed_backplane import BackplaneUnavailable, bp_get, bp_incr
from utils.enterprise_layer import touch_staff_session


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


def test_touch_staff_session_throttles_payload_refresh_on_regular_requests():
    flask_app.config["TESTING"] = True

    class _User:
        def __init__(self):
            self.id = 7
            self.username = "Cruz"
            self.role = "admin"

    calls = {"rev_get": 0}

    def _fake_coord_get(key, default=None):
        if str(key).startswith("enterprise:session_rev:"):
            calls["rev_get"] += 1
            return 1
        return default

    flask_session = {}
    with patch.dict(
        os.environ,
        {
            "STAFF_SESSION_TOUCH_INTERVAL_SECONDS": "60",
            "STAFF_SESSION_INDEX_REFRESH_SECONDS": "3600",
        },
        clear=False,
    ):
        with patch("utils.enterprise_layer._coord_get", side_effect=_fake_coord_get):
            with patch("utils.enterprise_layer._coord_set", return_value=True) as coord_set_mock:
                with patch("utils.enterprise_layer._append_index") as append_index_mock:
                    with flask_app.test_request_context("/admin/metricas", headers={"User-Agent": "pytest-agent"}):
                        out1 = touch_staff_session(
                            user=_User(),
                            flask_session=flask_session,
                            path="/admin/metricas",
                            endpoint="admin.metricas_dashboard",
                        )
                        out2 = touch_staff_session(
                            user=_User(),
                            flask_session=flask_session,
                            path="/admin/metricas",
                            endpoint="admin.metricas_dashboard",
                        )

    assert out1.get("ok") is True
    assert out2.get("ok") is True
    assert calls["rev_get"] == 2
    assert coord_set_mock.call_count == 1
    assert append_index_mock.call_count == 1


def test_touch_staff_session_live_endpoints_throttle_rev_read_short_window():
    flask_app.config["TESTING"] = True

    class _User:
        def __init__(self):
            self.id = 9
            self.username = "Karla"
            self.role = "secretaria"

    calls = {"rev_get": 0}

    def _fake_coord_get(key, default=None):
        if str(key).startswith("enterprise:session_rev:"):
            calls["rev_get"] += 1
            return 1
        return default

    flask_session = {}
    with patch.dict(
        os.environ,
        {
            "STAFF_SESSION_TOUCH_INTERVAL_SECONDS": "60",
            "STAFF_SESSION_LIVE_REV_CHECK_SECONDS": "30",
        },
        clear=False,
    ):
        with patch("utils.enterprise_layer._coord_get", side_effect=_fake_coord_get):
            with patch("utils.enterprise_layer._coord_set", return_value=True):
                with patch("utils.enterprise_layer._append_index"):
                    with flask_app.test_request_context("/admin/monitoreo/presence/ping", headers={"User-Agent": "pytest-agent"}):
                        out1 = touch_staff_session(
                            user=_User(),
                            flask_session=flask_session,
                            path="/admin/monitoreo/presence/ping",
                            endpoint="admin.monitoreo_presence_ping",
                        )
                        out2 = touch_staff_session(
                            user=_User(),
                            flask_session=flask_session,
                            path="/admin/monitoreo/presence/ping",
                            endpoint="admin.monitoreo_presence_ping",
                        )

    assert out1.get("ok") is True
    assert out2.get("ok") is True
    assert calls["rev_get"] == 1


def test_touch_staff_session_revoked_still_fail_closed():
    flask_app.config["TESTING"] = True

    class _User:
        def __init__(self):
            self.id = 11
            self.username = "Owner"
            self.role = "owner"

    flask_session = {
        "staff_session_token": "tok",
        "staff_session_rev": 1,
    }

    with patch("utils.enterprise_layer._coord_get", return_value=2):
        with flask_app.test_request_context("/admin/metricas", headers={"User-Agent": "pytest-agent"}):
            out = touch_staff_session(
                user=_User(),
                flask_session=flask_session,
                path="/admin/metricas",
                endpoint="admin.metricas_dashboard",
            )

    assert out.get("ok") is False
    assert out.get("reason") == "revoked"
