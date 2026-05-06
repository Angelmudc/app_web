# -*- coding: utf-8 -*-
from __future__ import annotations

from app import app as flask_app


def test_public_live_ping_accepts_valid_event():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    resp = client.post(
        "/live/ping",
        json={"event_type": "heartbeat", "current_path": "/", "page_title": "Inicio"},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    data = resp.get_json() or {}
    assert data.get("ok") is True


def test_public_live_ping_rejects_invalid_payload_and_large_body():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    invalid = client.post(
        "/live/ping",
        json={"event_type": "evil_event", "current_path": "/"},
        follow_redirects=False,
    )
    assert invalid.status_code == 400

    large = client.post(
        "/live/ping",
        data=("x" * 7000),
        headers={"Content-Type": "application/json"},
        follow_redirects=False,
    )
    assert large.status_code in (400, 413)


def test_public_live_ping_rate_limit_by_ip():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    # Usa env default del módulo; disparamos suficientes requests para observar 429.
    limited = False
    for _ in range(120):
        resp = client.post(
            "/live/ping",
            json={"event_type": "heartbeat", "current_path": "/"},
            follow_redirects=False,
        )
        if resp.status_code == 429:
            limited = True
            break
    assert limited is True
