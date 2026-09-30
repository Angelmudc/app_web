# -*- coding: utf-8 -*-
"""Regresiones focales para DOMESTICA SECURITY ROUND 1."""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest

from app import app as flask_app
from services.whatsapp_cloud_service import send_image_message, send_text_message


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html or "")
    return match.group(1) if match else ""


def test_development_whatsapp_fails_closed_even_if_legacy_flags_are_wrong(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("LEGACY_AUTOMATION_FROZEN", "false")
    monkeypatch.setenv("WHATSAPP_ENABLED", "true")
    monkeypatch.setenv("BOT_DRY_RUN", "false")
    monkeypatch.delenv("BOT_REAL_WHATSAPP_SANDBOX_ENABLED", raising=False)
    monkeypatch.delenv("BOT_REAL_WHATSAPP_PROVIDER", raising=False)

    with patch("services.whatsapp_cloud_service.requests.post") as post:
        result = send_text_message("18095550000", "security round 1")

    assert result["error_code"] == "development_whatsapp_blocked"
    post.assert_not_called()

    with patch("services.whatsapp_cloud_service.requests.post") as post:
        image_result = send_image_message("18095550000", "https://example.test/image.png")
    assert image_result["error_code"] == "development_whatsapp_blocked"
    post.assert_not_called()


def test_development_login_rate_limit_blocks_sixth_attempt(monkeypatch):
    previous_testing = bool(flask_app.config.get("TESTING"))
    flask_app.config["TESTING"] = False
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("ENABLE_LOGIN_RATE_LIMITS", raising=False)
    monkeypatch.setenv("LOGIN_RATE_IP_1M", "5")
    monkeypatch.setenv("LOGIN_RATE_USER_1M", "5")
    monkeypatch.setenv("LOGIN_RATE_IP_1H", "100")
    monkeypatch.setenv("LOGIN_RATE_USER_1H", "100")
    monkeypatch.setenv("LOGIN_BLOCK_THRESHOLD", "100")
    monkeypatch.setenv("LOGIN_DELAY_MS_BASE", "0")

    try:
        client = flask_app.test_client()
        statuses = []
        for _ in range(6):
            page = client.get(
                "/clientes/login",
                environ_overrides={"REMOTE_ADDR": "198.51.100.77"},
            )
            token = _csrf(page.get_data(as_text=True))
            response = client.post(
                "/clientes/login",
                data={
                    "username": "round1-unknown-client",
                    "password": "wrong-password",
                    "csrf_token": token,
                },
                follow_redirects=False,
                environ_overrides={"REMOTE_ADDR": "198.51.100.77"},
            )
            statuses.append(response.status_code)
        assert statuses[-1] == 429
    finally:
        flask_app.config["TESTING"] = previous_testing


def test_invalid_public_token_stays_public_and_never_redirects_to_internal_login(monkeypatch):
    previous_testing = bool(flask_app.config.get("TESTING"))
    flask_app.config["TESTING"] = True
    try:
        client = flask_app.test_client()
        with patch("clientes.routes._ensure_public_token_usage_table", return_value=True), \
             patch("clientes.routes._public_link_usage_by_hash", return_value=None), \
             patch("clientes.routes._resolve_public_link_token", return_value=(None, "invalid", {})):
            response = client.get("/clientes/f/ROUND1-invalid-token", follow_redirects=False)
    finally:
        flask_app.config["TESTING"] = previous_testing

    assert response.status_code == 404
    assert not (response.headers.get("Location") or "").startswith("/clientes/login")
    body = response.get_data(as_text=True).lower()
    assert "traceback" not in body
    assert "/admin/" not in body


def _post_cliente_login(client, username: str, ip: str, headers: dict[str, str] | None = None):
    page = client.get(
        "/clientes/login",
        environ_overrides={"REMOTE_ADDR": ip},
        headers=headers or {},
    )
    token = _csrf(page.get_data(as_text=True))
    return client.post(
        "/clientes/login",
        data={"username": username, "password": "wrong-round2", "csrf_token": token},
        follow_redirects=False,
        environ_overrides={"REMOTE_ADDR": ip},
        headers=headers or {},
    )


@pytest.mark.parametrize(
    "header_name, header_values",
    [
        ("X-Forwarded-For", [f"203.0.113.{idx}" for idx in range(1, 7)]),
        ("X-Real-IP", [f"198.51.100.{idx}" for idx in range(1, 7)]),
        ("X-Forwarded-For", [f"203.0.113.{idx}, 10.0.0.1" for idx in range(1, 7)]),
    ],
)
def test_untrusted_proxy_headers_cannot_bypass_cliente_login_rate_limit(
    monkeypatch, header_name, header_values
):
    previous_testing = bool(flask_app.config.get("TESTING"))
    flask_app.config["TESTING"] = False
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("TRUST_XFF", "0")
    monkeypatch.setenv("LOGIN_RATE_IP_1M", "5")
    monkeypatch.setenv("LOGIN_RATE_USER_1M", "100")
    monkeypatch.setenv("LOGIN_RATE_IP_1H", "100")
    monkeypatch.setenv("LOGIN_RATE_USER_1H", "100")
    monkeypatch.setenv("LOGIN_BLOCK_THRESHOLD", "100")
    monkeypatch.setenv("LOGIN_DELAY_MS_BASE", "0")
    ip = "192.0.2.77"

    try:
        client = flask_app.test_client()
        statuses = [
            _post_cliente_login(
                client,
                "round2-untrusted-header-user",
                ip,
                {header_name: value},
            ).status_code
            for value in header_values
        ]
        assert statuses[-1] == 429
        assert flask_app.config["TRUSTED_PROXY_FOR_IP"] is False
        assert flask_app.config["TRUSTED_PROXY_HOPS"] == 0
    finally:
        flask_app.config["TESTING"] = previous_testing


@pytest.mark.parametrize("env_name", ["development", "local", "test", "", "devx", "staging", " DevX "])
def test_whatsapp_sender_blocks_non_production_or_unknown_environments(monkeypatch, env_name):
    if env_name == "":
        monkeypatch.delenv("APP_ENV", raising=False)
        monkeypatch.setenv("FLASK_ENV", "production")
    else:
        monkeypatch.setenv("APP_ENV", env_name)
        monkeypatch.setenv("LEGACY_AUTOMATION_FROZEN", "false")
    monkeypatch.setenv("WHATSAPP_ENABLED", "true")
    monkeypatch.setenv("BOT_DRY_RUN", "false")
    monkeypatch.delenv("BOT_REAL_WHATSAPP_SANDBOX_ENABLED", raising=False)
    monkeypatch.delenv("BOT_REAL_WHATSAPP_PROVIDER", raising=False)
    with patch("services.whatsapp_cloud_service.requests.post") as post:
        expected = "legacy_automation_frozen" if env_name == "" else "development_whatsapp_blocked"
        assert send_text_message("18095550000", "round 2")["error_code"] == expected
        assert send_image_message("18095550000", "https://example.test/round2.png")["error_code"] == expected
    post.assert_not_called()


def test_whatsapp_production_and_explicit_staging_sandbox_use_mocked_transport(monkeypatch):
    monkeypatch.setenv("WHATSAPP_ENABLED", "true")
    monkeypatch.setenv("BOT_DRY_RUN", "false")
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "token-for-test")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "phone-for-test")
    response = type("Response", (), {"status_code": 200, "content": True, "text": "{}", "json": lambda self: {"messages": [{"id": "wamid-round2"}]}})()

    monkeypatch.setenv("LEGACY_AUTOMATION_FROZEN", "true")
    with patch("services.whatsapp_cloud_service.requests.post") as post:
        monkeypatch.setenv("APP_ENV", "production")
        result = send_text_message("18095550000", "round 2 production")
    assert result["error_code"] == "legacy_automation_frozen"
    post.assert_not_called()

    monkeypatch.setenv("LEGACY_AUTOMATION_FROZEN", "false")
    for env_name, sandbox in ((" staging ", True),):
        monkeypatch.setenv("APP_ENV", env_name)
        if sandbox:
            monkeypatch.setenv("BOT_REAL_WHATSAPP_SANDBOX_ENABLED", "true")
            monkeypatch.setenv("BOT_REAL_WHATSAPP_PROVIDER", "meta_sandbox")
        else:
            monkeypatch.delenv("BOT_REAL_WHATSAPP_SANDBOX_ENABLED", raising=False)
            monkeypatch.delenv("BOT_REAL_WHATSAPP_PROVIDER", raising=False)
        with patch("services.whatsapp_cloud_service.requests.post", return_value=response) as post:
            result = send_text_message("18095550000", "round 2 mocked")
        assert result["ok"] is True
        post.assert_called_once()
