# -*- coding: utf-8 -*-

from app import app as flask_app


def test_public_healthz_minimal_ok():
    client = flask_app.test_client()
    resp = client.get("/healthz", follow_redirects=False)
    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert payload.get("ok") is True
    assert payload.get("status") == "ok"
