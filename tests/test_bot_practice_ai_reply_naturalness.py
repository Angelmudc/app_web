# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from unittest.mock import patch

from app import app as flask_app
from config_app import db
from models import BotMessage
from services.bot_practice_ai_reply_service import get_practice_reply_with_ai_fallback, normalize_ai_reply_style
from tests.test_bot_practice_chat import _ensure_bot_tables, _login_staff, _reset_bot_tables


class _Conv:
    metadata_json = {"conversation_type": "local_practice"}


def _enable_local_ai(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_PRACTICE_REAL_OUTBOUND_ENABLED", "false")
    monkeypatch.setenv("BOT_PRACTICE_AI_REPLY_ENABLED", "true")


def test_normalize_style_controls_length_and_sentences():
    text = "  hola!!!   necesito esto???  responde si o no. responde si o no. tercera oracion innecesaria.  "
    out = normalize_ai_reply_style(text)
    assert "!!!" not in out
    assert "???" not in out
    assert len(out) <= 250
    parts = [p for p in out.replace("?", ".").replace("!", ".").split(".") if p.strip()]
    assert len(parts) <= 2


def test_anti_repetition_avoids_threepeat(monkeypatch):
    _enable_local_ai(monkeypatch)
    with patch("services.bot_practice_ai_reply_service._call_provider", return_value="Responde SI o NO."):
        out = get_practice_reply_with_ai_fallback(
            conversation=_Conv(),
            base_suggested_reply="Responde SI o NO.",
            current_step="PERSONAL_CONFIRMATION",
            candidate_message="hola",
            context={"recent_bot_suggestions": ["Responde SI o NO.", "Responde SI o NO."]},
            requires_human=False,
        )
    assert out["ai_reply_used"] is True
    assert str(out["suggested_reply"]).strip().lower() != "responde si o no."
    assert "si o no" in str(out["suggested_reply"]).lower()


def test_disallow_spam_emojis_and_prohibited_phrases(monkeypatch):
    _enable_local_ai(monkeypatch)
    with patch("services.bot_practice_ai_reply_service._call_provider", return_value="Mi amor 😂😂 responde SI o NO!!!"):
        out = get_practice_reply_with_ai_fallback(
            conversation=_Conv(),
            base_suggested_reply="Responde SI o NO.",
            current_step="PERSONAL_CONFIRMATION",
            candidate_message="hola",
            requires_human=False,
        )
    assert out["ai_reply_used"] is False
    assert out["ai_reply_fallback_reason"] == "unprofessional_tone"


def test_replay_humano_naturalidad_controlada_sin_regresion(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    _enable_local_ai(monkeypatch)
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    _login_staff(client)
    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])

    replies = []
    messages = ["hola", "holaa", "sii", "klk", "si"]
    with patch("services.bot_practice_ai_reply_service._call_provider", return_value="Responde SI o NO."):
        for txt in messages:
            resp = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": txt}, follow_redirects=False)
            assert resp.status_code == 200
            payload = resp.get_json() or {}
            replies.append(str(payload.get("suggested_reply") or ""))
            assert payload.get("current_step")
            assert isinstance(payload.get("protocol_entities"), dict)
            assert isinstance(payload.get("chat_items"), list)

    triple_same = False
    for i in range(2, len(replies)):
        if replies[i] == replies[i - 1] == replies[i - 2] and replies[i].strip():
            triple_same = True
            break
    assert triple_same is False

    with flask_app.app_context():
        outbounds = BotMessage.query.filter_by(conversation_id=conv_id, direction="outbound").count()
        assert outbounds == 0
