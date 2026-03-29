# -*- coding: utf-8 -*-

import socket
from unittest.mock import patch

import pytest

from app import app as flask_app
from config_app import create_app
from utils.ssrf_guard import OutboundURLBlocked, validate_external_url


def _build_app_with_cors(monkeypatch):
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("FLASK_SECRET_KEY", "x" * 64)
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://panel.example.com")
    monkeypatch.setenv("CORS_ALLOWED_METHODS", "GET,POST,OPTIONS")
    monkeypatch.setenv("CORS_ALLOWED_HEADERS", "Content-Type,X-CSRF-Token")
    monkeypatch.setenv("CSP_MODE", "enforce")
    return create_app()


def test_cors_preflight_allows_only_allowlist(monkeypatch):
    app = _build_app_with_cors(monkeypatch)
    app.config["TESTING"] = True
    client = app.test_client()

    allowed = client.options(
        "/ping",
        headers={
            "Origin": "https://panel.example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-CSRF-Token",
        },
    )
    assert allowed.status_code == 204
    assert allowed.headers.get("Access-Control-Allow-Origin") == "https://panel.example.com"
    assert "*" not in (allowed.headers.get("Access-Control-Allow-Origin") or "")
    assert "POST" in (allowed.headers.get("Access-Control-Allow-Methods") or "")

    blocked = client.options(
        "/ping",
        headers={
            "Origin": "https://evil.example.net",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert blocked.status_code == 403


def test_security_headers_present(monkeypatch):
    app = _build_app_with_cors(monkeypatch)
    app.config["TESTING"] = True
    client = app.test_client()

    resp = client.get("/ping", headers={"Origin": "https://panel.example.com"})
    assert resp.status_code == 200
    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-XSS-Protection") == "0"
    assert bool(resp.headers.get("Content-Security-Policy"))
    assert resp.headers.get("Access-Control-Allow-Origin") == "https://panel.example.com"


def test_validate_external_url_blocks_loopback():
    with pytest.raises(OutboundURLBlocked):
        validate_external_url("http://127.0.0.1:8080/internal")


def test_validate_external_url_blocks_private_dns_resolution():
    fake_private_dns = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.20.30.40", 443)),
    ]
    with patch("utils.ssrf_guard.socket.getaddrinfo", return_value=fake_private_dns):
        with pytest.raises(OutboundURLBlocked):
            validate_external_url("https://api.telegram.org/bot1/sendMessage")


def test_validate_external_url_accepts_public_host():
    fake_public_dns = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("149.154.167.220", 443)),
    ]
    with patch("utils.ssrf_guard.socket.getaddrinfo", return_value=fake_public_dns):
        out = validate_external_url(
            "https://api.telegram.org/bot1/sendMessage",
            allowed_hosts={"api.telegram.org"},
        )
    assert out == "https://api.telegram.org/bot1/sendMessage"


def test_internal_admin_endpoint_not_public():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    resp = client.get("/admin/health", follow_redirects=False)
    assert resp.status_code in (302, 303)
