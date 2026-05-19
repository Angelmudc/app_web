# -*- coding: utf-8 -*-
from __future__ import annotations

from scripts.local import check_bot_ai_provider


def test_check_provider_reports_disabled_without_calling_ai(monkeypatch, capsys):
    monkeypatch.setenv("BOT_AI_ENABLED", "false")
    monkeypatch.setenv("BOT_AI_PROVIDER", "openai")
    monkeypatch.setenv("BOT_AI_MODEL", "gpt-4.1-mini")
    monkeypatch.setattr("scripts.local.check_bot_ai_provider.classify_intent", lambda *_a, **_k: {"ok": True})

    code = check_bot_ai_provider.main()
    out = capsys.readouterr().out

    assert code == 2
    assert "ok: false" in out
    assert "error_code: ai_disabled" in out


def test_check_provider_ok_output_no_secret(monkeypatch, capsys):
    monkeypatch.setenv("BOT_AI_ENABLED", "true")
    monkeypatch.setenv("BOT_AI_PROVIDER", "openai")
    monkeypatch.setenv("BOT_AI_MODEL", "gpt-4.1-mini")
    monkeypatch.setenv("BOT_AI_API_KEY", "sk-test-super-secret-value")
    monkeypatch.setattr(
        "scripts.local.check_bot_ai_provider.classify_intent",
        lambda *_a, **_k: {
            "ok": True,
            "intent": "FAQ_HORARIOS",
            "answer_text": "Horario laboral.",
            "confidence": 0.9,
            "requires_human": False,
        },
    )

    code = check_bot_ai_provider.main()
    out = capsys.readouterr().out

    assert code == 0
    assert "ok: true" in out
    assert "parsed_json: true" in out
    assert "sk-test-super-secret-value" not in out
    assert "BOT_AI_API_KEY" not in out


def test_check_provider_error_output(monkeypatch, capsys):
    monkeypatch.setenv("BOT_AI_ENABLED", "true")
    monkeypatch.setenv("BOT_AI_PROVIDER", "openai")
    monkeypatch.setenv("BOT_AI_MODEL", "gpt-4.1-mini")
    monkeypatch.setattr(
        "scripts.local.check_bot_ai_provider.classify_intent",
        lambda *_a, **_k: {
            "ok": False,
            "error_code": "invalid_api_key",
            "error_type": "auth_error",
        },
    )

    code = check_bot_ai_provider.main()
    out = capsys.readouterr().out

    assert code == 1
    assert "ok: false" in out
    assert "error_code: invalid_api_key" in out
    assert "error_type: auth_error" in out
