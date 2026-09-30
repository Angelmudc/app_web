# -*- coding: utf-8 -*-
"""Regresiones del perfil explícito local de seguridad Round 8."""

from __future__ import annotations

import re

from app import app as flask_app
from admin import routes as admin_routes
from services.security_test_profile import (
    enforce_security_test_profile_safety,
    security_test_profile_enabled,
    security_test_profile_requested,
)


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html or "")
    return match.group(1) if match else ""


def test_round8_profile_requires_exact_local_signal(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SECURITY_TEST_PROFILE", "round8")
    assert security_test_profile_requested() is True
    assert security_test_profile_enabled() is True
    enforce_security_test_profile_safety()

    monkeypatch.setenv("APP_ENV", "production")
    assert security_test_profile_enabled() is False
    try:
        enforce_security_test_profile_safety()
    except RuntimeError as exc:
        assert "bloqueado" in str(exc).lower()
    else:  # pragma: no cover - safety assertion
        raise AssertionError("Round 8 no debe activar en production")


def test_round8_profile_rejects_unknown_environment_and_non_env_activation(monkeypatch):
    monkeypatch.setenv("APP_ENV", "mystery")
    monkeypatch.setenv("SECURITY_TEST_PROFILE", "round8")
    assert security_test_profile_enabled() is False
    try:
        enforce_security_test_profile_safety()
    except RuntimeError as exc:
        assert "mystery" in str(exc)
    else:  # pragma: no cover - safety assertion
        raise AssertionError("APP_ENV desconocido no debe activar el perfil")

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("SECURITY_TEST_PROFILE", raising=False)
    monkeypatch.setenv("SECURITY_TEST_PROFILE_QUERY", "round8")
    monkeypatch.setenv("SECURITY_TEST_PROFILE_HEADER", "round8")
    assert security_test_profile_requested() is False
    assert security_test_profile_enabled() is False

    monkeypatch.setenv("APP_ENV", "production")
    with flask_app.app_context():
        assert admin_routes._staff_mfa_is_enforced() is True


def test_round8_real_staff_login_keeps_session_and_role_checks(monkeypatch):
    previous_testing = bool(flask_app.config.get("TESTING"))
    previous_csrf = bool(flask_app.config.get("WTF_CSRF_ENABLED", True))
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("SECURITY_TEST_PROFILE", "round8")
    try:
        client = flask_app.test_client()
        login = client.post(
            "/admin/login",
            data={"usuario": "Owner", "clave": "admin123"},
            follow_redirects=False,
        )
        assert login.status_code in (302, 303)
        assert client.get("/admin/monitoreo", follow_redirects=False).status_code == 200
        assert client.get("/admin/usuarios/nuevo", follow_redirects=False).status_code == 200

        # A request cookie/header/query value cannot turn the role into owner.
        assert client.get("/admin/usuarios/nuevo?SECURITY_TEST_PROFILE=round8").status_code == 200
    finally:
        flask_app.config["TESTING"] = previous_testing
        flask_app.config["WTF_CSRF_ENABLED"] = previous_csrf


def test_development_round8_still_limits_sixth_login_attempt(monkeypatch):
    previous_testing = bool(flask_app.config.get("TESTING"))
    previous_csrf = bool(flask_app.config.get("WTF_CSRF_ENABLED", True))
    flask_app.config["TESTING"] = False
    flask_app.config["WTF_CSRF_ENABLED"] = True
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SECURITY_TEST_PROFILE", "round8")
    monkeypatch.delenv("ENABLE_LOGIN_RATE_LIMITS", raising=False)
    monkeypatch.setenv("LOGIN_RATE_IP_1M", "5")
    monkeypatch.setenv("LOGIN_RATE_USER_1M", "5")
    monkeypatch.setenv("LOGIN_RATE_IP_1H", "100")
    monkeypatch.setenv("LOGIN_RATE_USER_1H", "100")
    monkeypatch.setenv("LOGIN_DELAY_MS_BASE", "0")
    ip = "198.51.100.88"
    username = "round8-rate-limit-user"
    try:
        client = flask_app.test_client()
        statuses = []
        for _ in range(6):
            page = client.get("/clientes/login", environ_overrides={"REMOTE_ADDR": ip})
            statuses.append(
                client.post(
                    "/clientes/login",
                    data={"username": username, "password": "wrong-round8", "csrf_token": _csrf(page.get_data(as_text=True))},
                    follow_redirects=False,
                    environ_overrides={"REMOTE_ADDR": ip},
                ).status_code
            )
        assert statuses[-1] == 429
    finally:
        flask_app.config["TESTING"] = previous_testing
        flask_app.config["WTF_CSRF_ENABLED"] = previous_csrf
