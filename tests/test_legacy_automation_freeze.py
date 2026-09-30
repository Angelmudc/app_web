from unittest.mock import patch

from app import app as flask_app
from services.environment_guard_service import (
    EnvironmentSafetyError,
    enforce_production_safety_startup,
    is_legacy_automation_frozen,
)
from services.whatsapp_cloud_service import send_image_message, send_text_message
from services.bot_sandbox_service import run_sandbox_worker_once


def test_production_freezes_legacy_automation_by_default(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("LEGACY_AUTOMATION_FROZEN", raising=False)
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    assert is_legacy_automation_frozen() is True
    enforce_production_safety_startup()


def test_production_startup_rejects_explicit_unfrozen_legacy_automation(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("LEGACY_AUTOMATION_FROZEN", "false")
    try:
        enforce_production_safety_startup()
    except EnvironmentSafetyError as exc:
        assert "LEGACY_AUTOMATION_FROZEN=false" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("La producción debe fallar cerrada si legacy no está frozen")


def test_whatsapp_legacy_sends_are_blocked_before_network(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("LEGACY_AUTOMATION_FROZEN", "true")
    with patch("services.whatsapp_cloud_service.requests.post") as post:
        text_result = send_text_message("+18095550101", "QA freeze")
        image_result = send_image_message("+18095550101", "https://example.invalid/qa.png")
    assert text_result["error_code"] == "legacy_automation_frozen"
    assert image_result["error_code"] == "legacy_automation_frozen"
    post.assert_not_called()


def test_webhook_legacy_is_closed_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("LEGACY_AUTOMATION_FROZEN", "true")
    client = flask_app.test_client()
    response = client.post("/bot/whatsapp/webhook", data=b"{}")
    assert response.status_code == 410
    assert response.get_json()["error"] == "legacy_automation_frozen"
    verify_response = client.get("/bot/whatsapp/webhook")
    assert verify_response.status_code == 410


def test_frozen_legacy_keeps_read_only_health_available(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("LEGACY_AUTOMATION_FROZEN", "true")
    previous_flags = dict(flask_app.config.get("FEATURE_FLAGS") or {})
    try:
        flags = dict(previous_flags)
        flags["bot_candidatas_legacy"] = False
        flask_app.config["FEATURE_FLAGS"] = flags
        response = flask_app.test_client().get("/admin/bot/health", follow_redirects=False)
        assert response.status_code in (200, 302)
        assert response.status_code != 404
    finally:
        flask_app.config["FEATURE_FLAGS"] = previous_flags


def test_legacy_workers_do_not_claim_jobs_when_frozen(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("LEGACY_AUTOMATION_FROZEN", "true")
    assert run_sandbox_worker_once(batch_size=10)["picked"] == 0
    assert run_sandbox_worker_once(batch_size=10)["blocked"] == 1
