# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import time
from unittest.mock import patch

from app import app as flask_app
from config_app import db, cache
from models import StaffAuditLog
import admin.routes as admin_routes


def _login(client, usuario: str, clave: str):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def _latest_audit(action_type: str):
    with flask_app.app_context():
        return (
            StaffAuditLog.query
            .filter(StaffAuditLog.action_type == action_type)
            .order_by(StaffAuditLog.id.desc())
            .first()
        )


def test_s1c1_live_endpoints_allow_secretaria_role():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    cache.clear()
    client = flask_app.test_client()
    assert _login(client, "Karla", "9989").status_code in (302, 303)

    poll = client.get("/admin/live/invalidation/poll?after_id=0&limit=5", follow_redirects=False)
    stream = client.get("/admin/live/invalidation/stream?once=1", follow_redirects=False)
    assert poll.status_code == 200
    assert stream.status_code == 200


def test_s1c1_live_poll_returns_429_on_burst():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    cache.clear()
    env = {
        "ENABLE_OPERATIONAL_RATE_LIMITS": "1",
        "LIVE_POLL_WINDOW": "60",
        "LIVE_POLL_MAX_USER": "1",
        "LIVE_POLL_MAX_IP": "1",
        "LIVE_POLL_MAX_SESSION": "1",
        "LIVE_POLL_BLOCK": "60",
    }
    with patch.dict(os.environ, env, clear=False):
        client = flask_app.test_client()
        assert _login(client, "Cruz", "8998").status_code in (302, 303)

        ok = client.get("/admin/live/invalidation/poll?after_id=0&limit=5", follow_redirects=False)
        blocked = client.get("/admin/live/invalidation/poll?after_id=0&limit=5", follow_redirects=False)
        assert ok.status_code == 200
        assert blocked.status_code == 429
        payload = blocked.get_json() or {}
        assert payload.get("error") == "rate_limited"

    row = _latest_audit("LIVE_RATE_LIMITED")
    assert row is not None
    assert (row.route or "") == "/admin/live/invalidation/poll"


def test_s1c1_presence_ping_returns_429_on_abuse():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    cache.clear()
    env = {
        "ENABLE_OPERATIONAL_RATE_LIMITS": "1",
        "LIVE_PING_WINDOW": "60",
        "LIVE_PING_MAX_USER": "1",
        "LIVE_PING_MAX_IP": "1",
        "LIVE_PING_MAX_SESSION": "1",
        "LIVE_PING_BLOCK": "60",
    }
    with patch.dict(os.environ, env, clear=False):
        client = flask_app.test_client()
        assert _login(client, "Cruz", "8998").status_code in (302, 303)

        ok = client.post(
            "/admin/monitoreo/presence/ping",
            json={"session_id": "rl-abuse-tab", "current_path": "/admin/monitoreo?a=1"},
            follow_redirects=False,
        )
        blocked = client.post(
            "/admin/monitoreo/presence/ping",
            json={"session_id": "rl-abuse-tab", "current_path": "/admin/monitoreo?a=2"},
            follow_redirects=False,
        )
        assert ok.status_code == 200
        assert blocked.status_code == 429
        payload = blocked.get_json() or {}
        assert payload.get("error") == "rate_limited"


def test_s1c1_locks_ping_returns_429_on_abuse():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    cache.clear()
    env = {
        "ENABLE_OPERATIONAL_RATE_LIMITS": "1",
        "LIVE_LOCKS_PING_WINDOW": "60",
        "LIVE_LOCKS_PING_MAX_USER": "1",
        "LIVE_LOCKS_PING_MAX_IP": "1",
        "LIVE_LOCKS_PING_MAX_SESSION": "1",
        "LIVE_LOCKS_PING_BLOCK": "60",
    }
    with patch.dict(os.environ, env, clear=False):
        client = flask_app.test_client()
        assert _login(client, "Cruz", "8998").status_code in (302, 303)

        payload = {"entity_type": "candidata", "entity_id": "7777", "current_path": "/buscar?candidata_id=7777"}
        ok = client.post("/admin/seguridad/locks/ping", json=payload, follow_redirects=False)
        blocked = client.post("/admin/seguridad/locks/ping", json=payload, follow_redirects=False)
        assert ok.status_code == 200
        assert blocked.status_code == 429
        body = blocked.get_json() or {}
        assert body.get("error") == "rate_limited"


def test_s1c1_live_stream_blocks_n_plus_1_with_429():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    cache.clear()
    env = {
        "LIVE_STREAM_MAX_CONCURRENT_USER": "3",
        "LIVE_STREAM_MAX_CONCURRENT_SESSION": "3",
        "LIVE_STREAM_MAX_CONCURRENT_IP": "1",
    }
    with patch.dict(os.environ, env, clear=False):
        client = flask_app.test_client()
        assert _login(client, "Cruz", "8998").status_code in (302, 303)

        ip = "127.0.0.1"
        key = f"{admin_routes._LIVE_STREAM_CONCURRENCY_KEY_PREFIX}:ip:{ip}"
        seeded = admin_routes.bp_set(key, {"existing": float(time.time()) + 120.0}, timeout=240, context="test_live_stream_preseed")
        assert bool(seeded) is True

        blocked = client.get("/admin/live/invalidation/stream?once=1", follow_redirects=False)
        assert blocked.status_code == 429
        payload = blocked.get_json() or {}
        assert payload.get("error") == "concurrency_limit"
        assert payload.get("scope") == "ip"

    row = _latest_audit("LIVE_STREAM_CONCURRENCY_BLOCKED")
    assert row is not None
    assert (row.route or "") == "/admin/live/invalidation/stream"


def test_s1c1_live_endpoints_keep_authorized_admin_happy_path():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    cache.clear()
    env = {
        "ENABLE_OPERATIONAL_RATE_LIMITS": "1",
        "LIVE_POLL_MAX_USER": "50",
        "LIVE_POLL_MAX_IP": "100",
        "LIVE_POLL_MAX_SESSION": "50",
        "LIVE_PING_MAX_USER": "200",
        "LIVE_PING_MAX_IP": "300",
        "LIVE_PING_MAX_SESSION": "200",
        "LIVE_STREAM_MAX_CONCURRENT_USER": "5",
        "LIVE_STREAM_MAX_CONCURRENT_SESSION": "5",
        "LIVE_STREAM_MAX_CONCURRENT_IP": "10",
    }
    with patch.dict(os.environ, env, clear=False):
        client = flask_app.test_client()
        assert _login(client, "Cruz", "8998").status_code in (302, 303)

        poll = client.get("/admin/live/invalidation/poll?after_id=0&limit=5", follow_redirects=False)
        stream = client.get("/admin/live/invalidation/stream?once=1", follow_redirects=False)
        ping = client.post("/admin/monitoreo/presence/ping", json={"current_path": "/admin/monitoreo"}, follow_redirects=False)
        assert poll.status_code == 200
        assert stream.status_code == 200
        assert ping.status_code == 200
