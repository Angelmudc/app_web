# -*- coding: utf-8 -*-

import pytest

from config_app import _resolve_database_url_for_env


def test_local_uses_database_url_local(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://prod-user:prod-pass@prod-host/prod_db")
    monkeypatch.setenv("DATABASE_URL_LOCAL", "postgresql://local-user:local-pass@localhost/domestica_cibao_local")
    monkeypatch.setenv("DATABASE_URL_TEST", "postgresql://test-user:test-pass@localhost/domestica_cibao_test")

    url, label = _resolve_database_url_for_env("local")
    assert url.endswith("/domestica_cibao_local")
    assert label == "local/test"


def test_production_uses_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://prod-user:prod-pass@prod-host/prod_db")
    monkeypatch.setenv("DATABASE_URL_LOCAL", "postgresql://local-user:local-pass@localhost/domestica_cibao_local")
    monkeypatch.setenv("DATABASE_URL_TEST", "postgresql://test-user:test-pass@localhost/domestica_cibao_test")

    url, label = _resolve_database_url_for_env("production")
    assert url.endswith("/prod_db")
    assert label == "production"


def test_test_uses_database_url_test(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://prod-user:prod-pass@prod-host/prod_db")
    monkeypatch.setenv("DATABASE_URL_LOCAL", "postgresql://local-user:local-pass@localhost/domestica_cibao_local")
    monkeypatch.setenv("DATABASE_URL_TEST", "sqlite:///:memory:")

    url, label = _resolve_database_url_for_env("test")
    assert url.startswith("sqlite://")
    assert label == "test"


def test_local_missing_database_url_local_raises(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://prod-user:prod-pass@prod-host/prod_db")
    monkeypatch.delenv("DATABASE_URL_LOCAL", raising=False)
    monkeypatch.setenv("DATABASE_URL_TEST", "sqlite:///:memory:")

    with pytest.raises(RuntimeError, match="DATABASE_URL_LOCAL"):
        _resolve_database_url_for_env("local")


def test_invalid_app_env_raises_clear_error(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://prod-user:prod-pass@prod-host/prod_db")
    monkeypatch.setenv("DATABASE_URL_LOCAL", "postgresql://local-user:local-pass@localhost/domestica_cibao_local")
    monkeypatch.setenv("DATABASE_URL_TEST", "sqlite:///:memory:")

    with pytest.raises(RuntimeError, match="APP_ENV inválido"):
        _resolve_database_url_for_env("qa")


def test_non_prod_cannot_point_to_production_database(monkeypatch):
    prod_url = "postgresql://prod-user:prod-pass@prod-host/prod_db"
    monkeypatch.setenv("DATABASE_URL", prod_url)
    monkeypatch.setenv("DATABASE_URL_LOCAL", prod_url)
    monkeypatch.setenv("DATABASE_URL_TEST", "sqlite:///:memory:")

    with pytest.raises(RuntimeError, match="Bloqueado por seguridad"):
        _resolve_database_url_for_env("local")
