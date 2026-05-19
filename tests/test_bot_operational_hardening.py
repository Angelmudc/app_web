# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import patch

from app import app as flask_app
from config_app import db
from models import BotContactIdentity, BotConversation, BotDecisionLog, BotEscalation, BotMessage, BotSetting
from services.bot_rate_limit_service import reset_rate_limits
from services.environment_guard_service import (
    assert_local_safe_environment,
    enforce_production_safety_startup,
    get_sensitive_flags_snapshot,
    mask_database_url,
)


def _ensure_bot_tables() -> None:
    BotEscalation.__table__.drop(bind=db.engine, checkfirst=True)
    BotDecisionLog.__table__.drop(bind=db.engine, checkfirst=True)
    BotMessage.__table__.drop(bind=db.engine, checkfirst=True)
    BotConversation.__table__.drop(bind=db.engine, checkfirst=True)
    BotContactIdentity.__table__.drop(bind=db.engine, checkfirst=True)
    BotSetting.__table__.drop(bind=db.engine, checkfirst=True)
    BotContactIdentity.__table__.create(bind=db.engine, checkfirst=True)
    BotConversation.__table__.create(bind=db.engine, checkfirst=True)
    BotMessage.__table__.create(bind=db.engine, checkfirst=True)
    BotDecisionLog.__table__.create(bind=db.engine, checkfirst=True)
    BotSetting.__table__.create(bind=db.engine, checkfirst=True)
    BotEscalation.__table__.create(bind=db.engine, checkfirst=True)


def _login_staff(client) -> None:
    r = client.post("/admin/login", data={"usuario": "Owner", "clave": "admin123"}, follow_redirects=False)
    assert r.status_code in (302, 303)


def test_environment_guard_local_and_production_block(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DATABASE_URL_LOCAL", "sqlite:///:memory:")
    assert_local_safe_environment()

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "true")
    monkeypatch.setenv("APP_ENV", "production")
    try:
        enforce_production_safety_startup()
        assert False, "Debió bloquear flags peligrosos en producción"
    except RuntimeError as exc:
        assert "production_dangerous_flags_enabled" in str(exc)


def test_bot_health_endpoint_and_ui_warnings(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_bot_tables()

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("BOT_STAGING_MODE", "false")
    monkeypatch.setenv("BOT_SANDBOX_MODE", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "true")
    monkeypatch.setenv("WHATSAPP_ENABLED", "true")
    monkeypatch.setenv("BOT_DRY_RUN", "false")
    monkeypatch.setenv("BOT_AI_ENABLED", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "true")
    _login_staff(client)

    health = client.get("/admin/bot/health", follow_redirects=False)
    assert health.status_code in (200, 302)
    if health.status_code == 302:
        health = client.get("/admin/bot/health", follow_redirects=True)
    h = health.get_data(as_text=True)
    assert "angeldelacruz" not in h
    assert "pass@" not in h
    assert "sslmode=disable" not in h

    conv = client.get("/admin/bot/conversaciones", follow_redirects=False)
    assert conv.status_code in (200, 302)


def test_soft_rate_limit_on_draft_create(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    reset_rate_limits()

    with flask_app.app_context():
        _ensure_bot_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+18095557777", contact_name="Rate", status="open")
        db.session.add(conv)
        db.session.commit()
        conv_id = int(conv.id)

    _login_staff(client)

    with patch("admin.bot_routes.create_candidate_draft", side_effect=ValueError("summary_status_not_allowed")):
        for _ in range(4):
            r = client.post(f"/admin/bot/conversaciones/{conv_id}/candidate-draft/crear", data={}, follow_redirects=False)
            assert r.status_code in (302, 303)

        limited = client.post(f"/admin/bot/conversaciones/{conv_id}/candidate-draft/crear", data={}, follow_redirects=True)
        assert limited.status_code == 200
        assert "Acción limitada temporalmente" in limited.get_data(as_text=True)


def test_sensitive_flags_snapshot_values(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_AI_ENABLED", "false")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")
    snap = get_sensitive_flags_snapshot()
    assert snap["app_env"] == "development"
    assert snap["whatsapp_enabled"] is False


def test_mask_database_url_localhost_masking():
    out = mask_database_url(
        "postgresql+psycopg2://angeldelacruz:pass@localhost:5432/domestica_cibao_local?sslmode=disable"
    )
    assert out["db_driver"] == "postgresql"
    assert out["db_host_type"] == "local"
    assert out["db_url_masked"] == "postgresql://***@localhost:5432/domestica_cibao_local"
    assert "angeldelacruz" not in out["db_url_masked"]
    assert "pass" not in out["db_url_masked"]
    assert "sslmode" not in out["db_url_masked"]


def test_mask_database_url_remote_host():
    out = mask_database_url("postgresql://user:pass@prod-db.acme.com:5432/prod_main?sslmode=require")
    assert out["db_host_type"] == "non_local"
    assert out["db_url_masked"] == "postgresql://***@non-local-host/***"


def test_sensitive_snapshot_remote_host_adds_warning():
    with flask_app.app_context():
        old = flask_app.config.get("SQLALCHEMY_DATABASE_URI")
        flask_app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://user:pass@remote.acme.com:5432/prod_db?sslmode=require"
        snap = get_sensitive_flags_snapshot()
        flask_app.config["SQLALCHEMY_DATABASE_URI"] = old
    assert snap["db_host_type"] == "non_local"
    assert snap["db_url_masked"] == "postgresql://***@non-local-host/***"
    assert any("Base de datos no local" in w for w in snap["warnings"])


def test_mask_database_url_sqlite():
    out = mask_database_url("sqlite:////tmp/local.db")
    assert out["db_driver"] == "sqlite"
    assert out["db_host_type"] == "sqlite"
    assert out["db_url_masked"] == "sqlite:///***"
