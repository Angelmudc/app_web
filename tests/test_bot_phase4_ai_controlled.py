# -*- coding: utf-8 -*-
from __future__ import annotations

import requests
from unittest.mock import patch

from app import app as flask_app
from config_app import db
from models import BotContactIdentity, BotConversation, BotDecisionLog, BotEscalation, BotMessage, BotSetting
from services.bot_ai_service import classify_intent, redact_sensitive_text
from services.bot_conversation_service import set_current_step


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


def _reset_bot_tables() -> None:
    db.session.query(BotEscalation).delete()
    db.session.query(BotDecisionLog).delete()
    db.session.query(BotMessage).delete()
    db.session.query(BotConversation).delete()
    db.session.query(BotContactIdentity).delete()
    db.session.query(BotSetting).delete()
    db.session.commit()


def _payload(msg_id: str, phone: str, text: str) -> dict:
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": phone, "profile": {"name": "Test"}}],
                            "messages": [
                                {
                                    "id": msg_id,
                                    "from": phone,
                                    "timestamp": "1715000100",
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }


def test_ai_disabled_does_not_execute_ai(monkeypatch):
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    monkeypatch.setenv("WHATSAPP_VALIDATE_SIGNATURE", "false")
    monkeypatch.setenv("BOT_AI_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    with patch("bot.whatsapp_routes.classify_intent") as ai_mock:
        resp = client.post("/bot/whatsapp/webhook", json=_payload("wamid-ai-off-1", "18095551111", "hola"))
    assert resp.status_code == 200
    ai_mock.assert_not_called()


def test_intent_unknown_escalates(monkeypatch):
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    monkeypatch.setenv("WHATSAPP_VALIDATE_SIGNATURE", "false")
    monkeypatch.setenv("BOT_AI_ENABLED", "true")
    monkeypatch.setenv("BOT_AI_API_KEY", "fake")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    monkeypatch.setattr(
        "bot.whatsapp_routes.classify_intent",
        lambda *_args, **_kwargs: {
            "ok": True,
            "intent": "UNKNOWN",
            "answer_text": "No se",
            "confidence": 0.9,
            "requires_human": True,
            "escalation_reason": "AI_HUMAN_OR_UNKNOWN",
            "prompt_version": "phase4_v1",
            "ai_model": "fake",
        },
    )

    resp = client.post("/bot/whatsapp/webhook", json=_payload("wamid-unk-1", "18095552222", "que procede"))
    assert resp.status_code == 200
    with flask_app.app_context():
        conv = BotConversation.query.filter_by(phone_e164="+18095552222").first()
        assert conv is not None
        assert conv.status == "pending_human"


def test_invalid_json_in_ai_response_escalates(monkeypatch):
    monkeypatch.setenv("BOT_AI_ENABLED", "true")
    monkeypatch.setenv("BOT_AI_PROVIDER", "openai")
    monkeypatch.setenv("BOT_AI_API_KEY", "fake-key")

    class _Resp:
        content = b"1"

        def raise_for_status(self):
            return None

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": "NOT-JSON"}}]}

    with patch("services.bot_ai_service.requests.post", return_value=_Resp()):
        out = classify_intent("hola", {})
    assert out["ok"] is False
    assert out["error_code"] == "json_parse_error"
    assert out["requires_human"] is True


def test_invalid_api_key_maps_safe_error(monkeypatch):
    monkeypatch.setenv("BOT_AI_ENABLED", "true")
    monkeypatch.setenv("BOT_AI_PROVIDER", "openai")
    monkeypatch.setenv("BOT_AI_API_KEY", "fake-key")

    class _Resp401:
        status_code = 401

        @staticmethod
        def json():
            return {"error": {"type": "invalid_request_error", "message": "Incorrect API key provided"}}

    err = requests.HTTPError("401 unauthorized")
    err.response = _Resp401()

    with patch("services.bot_ai_service.requests.post", side_effect=err):
        out = classify_intent("hola", {})
    assert out["ok"] is False
    assert out["error_code"] == "invalid_api_key"


def test_timeout_maps_safe_error(monkeypatch):
    monkeypatch.setenv("BOT_AI_ENABLED", "true")
    monkeypatch.setenv("BOT_AI_PROVIDER", "openai")
    monkeypatch.setenv("BOT_AI_API_KEY", "fake-key")
    with patch("services.bot_ai_service.requests.post", side_effect=requests.Timeout("timeout")):
        out = classify_intent("hola", {})
    assert out["ok"] is False
    assert out["error_code"] == "timeout"


def test_network_error_maps_safe_error(monkeypatch):
    monkeypatch.setenv("BOT_AI_ENABLED", "true")
    monkeypatch.setenv("BOT_AI_PROVIDER", "openai")
    monkeypatch.setenv("BOT_AI_API_KEY", "fake-key")
    with patch("services.bot_ai_service.requests.post", side_effect=requests.ConnectionError("down")):
        out = classify_intent("hola", {})
    assert out["ok"] is False
    assert out["error_code"] == "network_error"


def test_provider_bad_response_maps_safe_error(monkeypatch):
    monkeypatch.setenv("BOT_AI_ENABLED", "true")
    monkeypatch.setenv("BOT_AI_PROVIDER", "openai")
    monkeypatch.setenv("BOT_AI_API_KEY", "fake-key")

    class _Resp500:
        status_code = 500

        @staticmethod
        def json():
            return {"error": {"type": "server_error", "message": "internal error"}}

    err = requests.HTTPError("500")
    err.response = _Resp500()

    with patch("services.bot_ai_service.requests.post", side_effect=err):
        out = classify_intent("hola", {})
    assert out["ok"] is False
    assert out["error_code"] == "provider_bad_response"


def test_low_confidence_escalates(monkeypatch):
    monkeypatch.setenv("BOT_AI_ENABLED", "true")
    monkeypatch.setenv("BOT_AI_PROVIDER", "openai")
    monkeypatch.setenv("BOT_AI_API_KEY", "fake-key")

    class _Resp:
        content = b"1"

        def raise_for_status(self):
            return None

        @staticmethod
        def json():
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"intent":"FAQ_HORARIOS","answer_text":"Estamos para ayudarte","confidence":0.3,"requires_human":false}'
                        }
                    }
                ]
            }

    with patch("services.bot_ai_service.requests.post", return_value=_Resp()):
        out = classify_intent("horario", {})
    assert out["ok"] is True
    assert out["requires_human"] is True
    assert out["escalation_reason"] == "AI_LOW_CONFIDENCE"


def test_redacts_sensitive_pii_before_ai_send():
    text = "Mi cedula es 001-1234567-8 y vivo en calle 5 sector Centro"
    redacted = redact_sensitive_text(text)
    assert "1234567" not in redacted
    assert "calle 5" not in redacted.lower()


def test_dry_run_ai_autoreply_does_not_send_whatsapp(monkeypatch):
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    monkeypatch.setenv("WHATSAPP_VALIDATE_SIGNATURE", "false")
    monkeypatch.setenv("BOT_AI_ENABLED", "true")
    monkeypatch.setenv("BOT_AI_API_KEY", "fake")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "true")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    monkeypatch.setattr(
        "bot.whatsapp_routes.classify_intent",
        lambda *_args, **_kwargs: {
            "ok": True,
            "intent": "FAQ_HORARIOS",
            "answer_text": "Atendemos de lunes a viernes.",
            "confidence": 0.91,
            "requires_human": False,
            "prompt_version": "phase4_v1",
            "ai_model": "fake",
        },
    )

    with patch("bot.whatsapp_routes.send_text_message", return_value={"ok": False, "skipped": True, "status": "queued", "reason": "dry_run"}) as send_mock:
        resp = client.post("/bot/whatsapp/webhook", json=_payload("wamid-dry-ai-1", "18095553333", "horario"))
    assert resp.status_code == 200
    send_mock.assert_called_once()


def test_autoreply_off_does_not_send(monkeypatch):
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    monkeypatch.setenv("WHATSAPP_VALIDATE_SIGNATURE", "false")
    monkeypatch.setenv("BOT_AI_ENABLED", "true")
    monkeypatch.setenv("BOT_AI_API_KEY", "fake")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    monkeypatch.setattr(
        "bot.whatsapp_routes.classify_intent",
        lambda *_args, **_kwargs: {
            "ok": True,
            "intent": "FAQ_UBICACION",
            "answer_text": "Estamos en Santiago.",
            "confidence": 0.95,
            "requires_human": False,
            "prompt_version": "phase4_v1",
            "ai_model": "fake",
        },
    )

    with patch("bot.whatsapp_routes.send_text_message") as send_mock:
        resp = client.post("/bot/whatsapp/webhook", json=_payload("wamid-auto-off-1", "18095554444", "ubicacion"))
    assert resp.status_code == 200
    send_mock.assert_not_called()


def test_safe_faq_generates_suggestion(monkeypatch):
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    monkeypatch.setenv("WHATSAPP_VALIDATE_SIGNATURE", "false")
    monkeypatch.setenv("BOT_AI_ENABLED", "true")
    monkeypatch.setenv("BOT_AI_API_KEY", "fake")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    monkeypatch.setattr(
        "bot.whatsapp_routes.classify_intent",
        lambda *_args, **_kwargs: {
            "ok": True,
            "intent": "FAQ_CONTACTO",
            "answer_text": "Te asistimos por este WhatsApp.",
            "confidence": 0.96,
            "requires_human": False,
            "prompt_version": "phase4_v1",
            "ai_model": "fake",
        },
    )

    resp = client.post("/bot/whatsapp/webhook", json=_payload("wamid-sug-1", "18095555555", "contacto"))
    assert resp.status_code == 200

    with flask_app.app_context():
        conv = BotConversation.query.filter_by(phone_e164="+18095555555").first()
        assert conv is not None
        dec = (
            BotDecisionLog.query.filter_by(conversation_id=conv.id, decision_type="ai_classification")
            .order_by(BotDecisionLog.id.desc())
            .first()
        )
        assert dec is not None
        assert dec.ai_used is True
        assert dec.facts_json.get("intent") == "FAQ_CONTACTO"


def test_protocol_context_is_sent_to_ai_and_logged(monkeypatch):
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    monkeypatch.setenv("WHATSAPP_VALIDATE_SIGNATURE", "false")
    monkeypatch.setenv("BOT_AI_ENABLED", "true")
    monkeypatch.setenv("BOT_AI_API_KEY", "fake")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+18095556661", contact_name="Proto Context", status="open")
        db.session.add(conv)
        db.session.commit()
        set_current_step(conv, current_step_code="ADDRESS", last_completed_step="BASIC_INFO")

    seen_context = {}

    def _fake_classify(_text, context=None):
        seen_context.update(context or {})
        return {
            "ok": True,
            "intent": "FAQ_REQUISITOS",
            "answer_text": "Comparte ciudad, sector y dirección exacta, por favor.",
            "confidence": 0.91,
            "requires_human": False,
            "prompt_version": "phase4_v2",
            "ai_model": "fake",
        }

    monkeypatch.setattr("bot.whatsapp_routes.classify_intent", _fake_classify)

    resp = client.post("/bot/whatsapp/webhook", json=_payload("wamid-proto-ctx-1", "18095556661", "vivo en santiago"))
    assert resp.status_code == 200

    proto = seen_context.get("protocol_context") or {}
    assert proto.get("current_step_code") == "ADDRESS"
    assert proto.get("protocol_version") == "domesticas_v1"
    assert isinstance(proto.get("step_prompt"), str) and proto.get("step_prompt")

    with flask_app.app_context():
        conv = BotConversation.query.filter_by(phone_e164="+18095556661").first()
        assert conv is not None
        dec = (
            BotDecisionLog.query.filter_by(conversation_id=conv.id, decision_type="ai_classification")
            .order_by(BotDecisionLog.id.desc())
            .first()
        )
        assert dec is not None
        facts = dec.facts_json or {}
        assert facts.get("current_step_code") == "ADDRESS"
        assert facts.get("protocol_version") == "domesticas_v1"
        assert facts.get("step_title")
        assert facts.get("step_requires_human") is False


def test_protocol_step_requires_human_forces_escalation_without_step_change(monkeypatch):
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    monkeypatch.setenv("WHATSAPP_VALIDATE_SIGNATURE", "false")
    monkeypatch.setenv("BOT_AI_ENABLED", "true")
    monkeypatch.setenv("BOT_AI_API_KEY", "fake")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+18095556662", contact_name="Proto Human", status="open")
        db.session.add(conv)
        db.session.commit()
        set_current_step(conv, current_step_code="DOCUMENT_REQUEST", last_completed_step="GROUP_WARNING")
        before_meta = dict(conv.metadata_json or {})

    monkeypatch.setattr(
        "bot.whatsapp_routes.classify_intent",
        lambda *_args, **_kwargs: {
            "ok": True,
            "intent": "FAQ_REQUISITOS",
            "answer_text": "Por favor comparte tus documentos.",
            "confidence": 0.95,
            "requires_human": False,
            "prompt_version": "phase4_v2",
            "ai_model": "fake",
        },
    )

    resp = client.post("/bot/whatsapp/webhook", json=_payload("wamid-proto-human-1", "18095556662", "te envio docs"))
    assert resp.status_code == 200

    with flask_app.app_context():
        conv = BotConversation.query.filter_by(phone_e164="+18095556662").first()
        assert conv is not None
        after_meta = dict(conv.metadata_json or {})
        assert after_meta.get("current_step_code") == before_meta.get("current_step_code")
        assert after_meta.get("protocol_version") == before_meta.get("protocol_version")

        ai_dec = (
            BotDecisionLog.query.filter_by(conversation_id=conv.id, decision_type="ai_classification")
            .order_by(BotDecisionLog.id.desc())
            .first()
        )
        assert ai_dec is not None
        assert ai_dec.decision_result == "escalate"
        facts = ai_dec.facts_json or {}
        assert facts.get("requires_human") is True
        assert facts.get("step_requires_human") is True
        assert facts.get("current_step_code") == "DOCUMENT_REQUEST"

        auto_dec = (
            BotDecisionLog.query.filter_by(conversation_id=conv.id, decision_type="auto_reply")
            .order_by(BotDecisionLog.id.desc())
            .first()
        )
        assert auto_dec is not None
        assert auto_dec.decision_result == "manual_only"


def test_webhook_does_not_break_when_ai_fails(monkeypatch):
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    monkeypatch.setenv("WHATSAPP_VALIDATE_SIGNATURE", "false")
    monkeypatch.setenv("BOT_AI_ENABLED", "true")
    monkeypatch.setenv("BOT_AI_API_KEY", "fake")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    monkeypatch.setattr("bot.whatsapp_routes.classify_intent", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

    resp = client.post("/bot/whatsapp/webhook", json=_payload("wamid-ai-fail-1", "18095556666", "hola"))
    assert resp.status_code == 200
    with flask_app.app_context():
        msg = BotMessage.query.filter_by(wa_message_id="wamid-ai-fail-1").first()
        assert msg is not None


def test_ai_does_not_run_when_conversation_pending_human(monkeypatch):
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    monkeypatch.setenv("WHATSAPP_VALIDATE_SIGNATURE", "false")
    monkeypatch.setenv("BOT_AI_ENABLED", "true")
    monkeypatch.setenv("BOT_AI_API_KEY", "fake")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+18095557771", status="pending_human")
        db.session.add(conv)
        db.session.commit()

    with patch("bot.whatsapp_routes.classify_intent") as ai_mock:
        resp = client.post("/bot/whatsapp/webhook", json=_payload("wamid-pending-1", "18095557771", "hola"))
    assert resp.status_code == 200
    ai_mock.assert_not_called()


def test_ai_does_not_run_when_identity_ambiguous(monkeypatch):
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    monkeypatch.setenv("WHATSAPP_VALIDATE_SIGNATURE", "false")
    monkeypatch.setenv("BOT_AI_ENABLED", "true")
    monkeypatch.setenv("BOT_AI_API_KEY", "fake")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    monkeypatch.setattr(
        "bot.whatsapp_routes.get_or_create_identity",
        lambda _phone: (
            type("IdentityLite", (), {"id": 1})(),
            {
                "identity_status": "ambiguous",
                "rule_code": "AMBIGUOUS_MULTIPLE_MATCHES",
                "reason_human": "Multiples coincidencias por telefono",
                "client_ids": [1, 2],
                "candidate_ids": [],
            },
        ),
    )

    with patch("bot.whatsapp_routes.classify_intent") as ai_mock:
        resp = client.post("/bot/whatsapp/webhook", json=_payload("wamid-amb-1", "18095557772", "hola"))
    assert resp.status_code == 200
    ai_mock.assert_not_called()
    with flask_app.app_context():
        conv = BotConversation.query.filter_by(phone_e164="+18095557772").first()
        assert conv is not None
        assert conv.status == "pending_human"


def test_daily_limit_reached_blocks_provider_and_registers_decision(monkeypatch):
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    monkeypatch.setenv("WHATSAPP_VALIDATE_SIGNATURE", "false")
    monkeypatch.setenv("BOT_AI_ENABLED", "true")
    monkeypatch.setenv("BOT_AI_API_KEY", "fake")
    monkeypatch.setenv("BOT_AI_DAILY_REQUEST_LIMIT", "0")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    with patch("bot.whatsapp_routes.classify_intent") as ai_mock:
        resp = client.post("/bot/whatsapp/webhook", json=_payload("wamid-daily-limit-1", "18095559991", "hola"))
    assert resp.status_code == 200
    ai_mock.assert_not_called()

    with flask_app.app_context():
        conv = BotConversation.query.filter_by(phone_e164="+18095559991").first()
        assert conv is not None
        dec = (
            BotDecisionLog.query.filter_by(conversation_id=conv.id, decision_type="ai_classification")
            .order_by(BotDecisionLog.id.desc())
            .first()
        )
        assert dec is not None
        assert dec.rule_code == "AI_DAILY_LIMIT_REACHED"


def test_provider_not_supported_scales_without_network(monkeypatch):
    monkeypatch.setenv("BOT_AI_ENABLED", "true")
    monkeypatch.setenv("BOT_AI_PROVIDER", "other")
    monkeypatch.setenv("BOT_AI_API_KEY", "fake")
    with patch("services.bot_ai_service.requests.post") as post_mock:
        out = classify_intent("hola", {})
    assert out["ok"] is False
    assert out["error_code"] == "provider_not_supported"
    post_mock.assert_not_called()


def test_api_key_missing_scales_without_network(monkeypatch):
    monkeypatch.setenv("BOT_AI_ENABLED", "true")
    monkeypatch.setenv("BOT_AI_PROVIDER", "openai")
    monkeypatch.delenv("BOT_AI_API_KEY", raising=False)
    with patch("services.bot_ai_service.requests.post") as post_mock:
        out = classify_intent("hola", {})
    assert out["ok"] is False
    assert out["error_code"] == "api_key_missing"
    post_mock.assert_not_called()


def test_duplicate_message_does_not_trigger_ai_twice(monkeypatch):
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    monkeypatch.setenv("WHATSAPP_VALIDATE_SIGNATURE", "false")
    monkeypatch.setenv("BOT_AI_ENABLED", "true")
    monkeypatch.setenv("BOT_AI_API_KEY", "fake")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    monkeypatch.setattr(
        "bot.whatsapp_routes.classify_intent",
        lambda *_args, **_kwargs: {
            "ok": True,
            "intent": "FAQ_CONTACTO",
            "answer_text": "Te asistimos por este WhatsApp.",
            "confidence": 0.96,
            "requires_human": False,
            "prompt_version": "phase4_v1",
            "ai_model": "fake",
        },
    )
    payload = _payload("wamid-dup-1", "18095557773", "contacto")
    resp1 = client.post("/bot/whatsapp/webhook", json=payload)
    resp2 = client.post("/bot/whatsapp/webhook", json=payload)
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    with flask_app.app_context():
        conv = BotConversation.query.filter_by(phone_e164="+18095557773").first()
        assert conv is not None
        dec_count = BotDecisionLog.query.filter_by(conversation_id=conv.id, decision_type="ai_classification").count()
        assert dec_count == 1


def test_ai_context_redacts_cedula_and_address_before_provider_call(monkeypatch):
    monkeypatch.setenv("BOT_AI_ENABLED", "true")
    monkeypatch.setenv("BOT_AI_PROVIDER", "openai")
    monkeypatch.setenv("BOT_AI_API_KEY", "fake-key")
    captured = {}

    class _Resp:
        content = b"1"

        def raise_for_status(self):
            return None

        @staticmethod
        def json():
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"intent":"FAQ_CONTACTO","answer_text":"Te ayudamos por WhatsApp","confidence":0.9,"requires_human":false}'
                        }
                    }
                ]
            }

    def _fake_post(url, headers, json, timeout):
        captured["payload"] = json
        return _Resp()

    with patch("services.bot_ai_service.requests.post", side_effect=_fake_post):
        out = classify_intent("Mi cedula 001-1234567-8, direccion calle 8 sector C", {"history": []})
    assert out["ok"] is True
    body = captured["payload"]
    user_content = body["messages"][1]["content"]
    assert "1234567" not in user_content
    assert "calle 8" not in user_content.lower()


def test_ai_context_prioritizes_latest_and_truncates_history(monkeypatch):
    monkeypatch.setenv("BOT_AI_ENABLED", "true")
    monkeypatch.setenv("BOT_AI_PROVIDER", "openai")
    monkeypatch.setenv("BOT_AI_API_KEY", "fake-key")
    captured = {}

    class _Resp:
        content = b"1"

        def raise_for_status(self):
            return None

        @staticmethod
        def json():
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"intent":"FAQ_UBICACION","answer_text":"Estamos en Santiago","confidence":0.9,"requires_human":false}'
                        }
                    }
                ]
            }

    def _fake_post(url, headers, json, timeout):
        captured["payload"] = json
        return _Resp()

    ctx = {
        "history": [
            {"role": "user", "text": "hola 1"},
            {"role": "assistant", "text": "resp 1"},
            {"role": "user", "text": "hola 2"},
            {"role": "assistant", "text": "resp 2"},
            {"role": "user", "text": "hola 3"},
        ]
    }
    with patch("services.bot_ai_service.requests.post", side_effect=_fake_post):
        out = classify_intent("¿Dónde están ubicados?", ctx)
    assert out["ok"] is True
    safe = out["safe_context"]
    assert safe["latest_user_text"] == "¿Dónde están ubicados?"
    assert len(safe["history"]) <= 3
    assert safe["history"][-1]["text"] == "hola 3"


def test_ai_input_and_output_char_limits(monkeypatch):
    monkeypatch.setenv("BOT_AI_ENABLED", "true")
    monkeypatch.setenv("BOT_AI_PROVIDER", "openai")
    monkeypatch.setenv("BOT_AI_API_KEY", "fake-key")
    monkeypatch.setenv("BOT_AI_MAX_INPUT_CHARS", "20")
    monkeypatch.setenv("BOT_AI_MAX_OUTPUT_CHARS", "15")
    captured = {}

    class _Resp:
        content = b"1"

        def raise_for_status(self):
            return None

        @staticmethod
        def json():
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"intent":"FAQ_CONTACTO","answer_text":"1234567890ABCDEFGHIJ","confidence":0.9,"requires_human":false}'
                        }
                    }
                ]
            }

    def _fake_post(url, headers, json, timeout):
        captured["payload"] = json
        return _Resp()

    long_text = "x" * 100
    with patch("services.bot_ai_service.requests.post", side_effect=_fake_post):
        out = classify_intent(long_text, {"history": [{"role": "user", "text": long_text}]})
    assert out["ok"] is True
    assert len(out["answer_text"]) == 15
    safe = out["safe_context"]
    assert len(safe["latest_user_text"]) == 20
    assert len(safe["history"][0]["text"]) == 20
