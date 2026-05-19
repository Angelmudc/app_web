# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch
import requests

from services.bot_practice_ai_reply_service import get_practice_reply_with_ai_fallback


def _conv_local_practice() -> SimpleNamespace:
    return SimpleNamespace(metadata_json={"conversation_type": "local_practice"})


def _base_env(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_PRACTICE_REAL_OUTBOUND_ENABLED", "false")


def test_flag_off_no_ia(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_PRACTICE_AI_REPLY_ENABLED", "false")
    out = get_practice_reply_with_ai_fallback(
        conversation=_conv_local_practice(),
        base_suggested_reply="Responde SI o NO.",
        current_step="PERSONAL_CONFIRMATION",
        candidate_message="hola",
        requires_human=False,
    )
    assert out["ai_reply_used"] is False
    assert out["suggested_reply"] == "Responde SI o NO."
    assert out["ai_reply_fallback_reason"] == "feature_flag_disabled"


def test_flag_on_local_attempts_ai(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_PRACTICE_AI_REPLY_ENABLED", "true")
    with patch("services.bot_practice_ai_reply_service._call_provider", return_value="Por favor responde SI o NO para continuar.") as call_mock:
        out = get_practice_reply_with_ai_fallback(
            conversation=_conv_local_practice(),
            base_suggested_reply="Responde SI o NO.",
            current_step="PERSONAL_CONFIRMATION",
            candidate_message="si",
            requires_human=False,
        )
    assert call_mock.called
    assert out["ai_reply_used"] is True
    assert out["suggested_reply_source"] == "practice_ai"


def test_dangerous_promise_fallback(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_PRACTICE_AI_REPLY_ENABLED", "true")
    with patch("services.bot_practice_ai_reply_service._call_provider", return_value="Tranquila, te conseguimos empleo hoy mismo"):
        out = get_practice_reply_with_ai_fallback(
            conversation=_conv_local_practice(),
            base_suggested_reply="Gracias. Continuamos con la siguiente etapa.",
            current_step="BASIC_INFO",
            candidate_message="ok",
            requires_human=False,
        )
    assert out["ai_reply_used"] is False
    assert out["ai_reply_fallback_reason"] == "dangerous_promise"


def test_long_ai_reply_fallback(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_PRACTICE_AI_REPLY_ENABLED", "true")
    with patch("services.bot_practice_ai_reply_service._call_provider", return_value=("x" * 251)):
        out = get_practice_reply_with_ai_fallback(
            conversation=_conv_local_practice(),
            base_suggested_reply="Comparte tu ciudad.",
            current_step="ADDRESS",
            candidate_message="santiago",
            requires_human=False,
        )
    assert out["ai_reply_used"] is False
    assert out["ai_reply_fallback_reason"] == "reply_too_long"


def test_timeout_or_error_fallback(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_PRACTICE_AI_REPLY_ENABLED", "true")
    with patch("services.bot_practice_ai_reply_service._call_provider", side_effect=requests.Timeout("boom")):
        out = get_practice_reply_with_ai_fallback(
            conversation=_conv_local_practice(),
            base_suggested_reply="Comparte tu ciudad.",
            current_step="ADDRESS",
            candidate_message="santiago",
            requires_human=False,
        )
    assert out["ai_reply_used"] is False
    assert out["ai_reply_fallback_reason"] == "timeout"


def test_requires_human_preserves_notice(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_PRACTICE_AI_REPLY_ENABLED", "true")
    with patch("services.bot_practice_ai_reply_service._call_provider", return_value="Gracias. Esto pasará a revisión humana antes de continuar."):
        out = get_practice_reply_with_ai_fallback(
            conversation=_conv_local_practice(),
            base_suggested_reply="Necesita revisión humana para continuar.",
            current_step="ID_VERIFICATION",
            candidate_message="ok",
            requires_human=True,
        )
    assert out["ai_reply_used"] is True
    assert "revisión humana" in out["suggested_reply"].lower()


def test_does_not_modify_entities_or_step(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_PRACTICE_AI_REPLY_ENABLED", "true")
    with patch("services.bot_practice_ai_reply_service._call_provider", return_value="Por favor confirma si eres tú: responde SI o NO."):
        out = get_practice_reply_with_ai_fallback(
            conversation=_conv_local_practice(),
            base_suggested_reply="Responde SI o NO.",
            current_step="PERSONAL_CONFIRMATION",
            candidate_message="hola",
            context={"protocol_entities": {"name": "Ana"}},
            requires_human=False,
        )
    assert out["base_suggested_reply"] == "Responde SI o NO."
    assert out["suggested_reply"]
