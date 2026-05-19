# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import patch

from app import app as flask_app
from config_app import db
from models import BotContactIdentity, BotConversation, BotDecisionLog, BotEscalation, BotMessage, BotSetting
from scripts.local import test_bot_ai_local as local_ai_script
from scripts.local.test_bot_ai_local import LocalBotAISafetyError, run_local_ai_suggestion_test


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


def _set_safe_env(monkeypatch) -> None:
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")


def test_mock_mode_does_not_call_openai_or_whatsapp(monkeypatch):
    flask_app.config["TESTING"] = True
    _set_safe_env(monkeypatch)
    monkeypatch.setenv("BOT_AI_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

        with patch("services.bot_ai_service.requests.post") as openai_post_mock:
            with patch("services.whatsapp_cloud_service.requests.post") as whatsapp_post_mock:
                result = run_local_ai_suggestion_test(
                    mode="mock",
                    phone_e164="+18090000000",
                    inbound_text="Hola, ¿cuáles son los requisitos para contratar una doméstica?",
                )

    assert result["mode"] == "mock"
    assert result["whatsapp_sent"] is False
    openai_post_mock.assert_not_called()
    whatsapp_post_mock.assert_not_called()



def test_mock_mode_creates_conversation_message_and_decision_log(monkeypatch):
    flask_app.config["TESTING"] = True
    _set_safe_env(monkeypatch)
    monkeypatch.setenv("BOT_AI_ENABLED", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

        result = run_local_ai_suggestion_test(
            mode="mock",
            phone_e164="+18090000000",
            inbound_text="Hola, ¿cuáles son los requisitos para contratar una doméstica?",
        )

        conv = BotConversation.query.get(result["conversation_id"])
        msg = BotMessage.query.get(result["message_id"])
        dec = BotDecisionLog.query.get(result["decision_log_id"])
        outbound_count = BotMessage.query.filter_by(conversation_id=conv.id, direction="outbound").count()
        manual_only_dec = BotDecisionLog.query.filter_by(conversation_id=conv.id, decision_type="auto_reply").first()

    assert conv is not None
    assert msg is not None
    assert dec is not None
    assert msg.direction == "inbound"
    assert dec.decision_type == "ai_classification"
    assert dec.facts_json.get("intent") == "FAQ_REQUISITOS"
    assert outbound_count == 0
    assert manual_only_dec is not None
    assert manual_only_dec.decision_result == "manual_only"



def test_real_mode_without_api_key_fails_safe(monkeypatch):
    flask_app.config["TESTING"] = True
    _set_safe_env(monkeypatch)
    monkeypatch.setenv("BOT_AI_ENABLED", "true")
    monkeypatch.delenv("BOT_AI_API_KEY", raising=False)

    with flask_app.app_context():
        with patch("scripts.local.test_bot_ai_local.classify_intent") as ai_mock:
            try:
                run_local_ai_suggestion_test(mode="real", phone_e164="+18090000000", inbound_text="hola")
                raised = None
            except Exception as exc:
                raised = exc

    assert isinstance(raised, LocalBotAISafetyError)
    ai_mock.assert_not_called()



def test_refuses_when_autoreply_enabled(monkeypatch):
    flask_app.config["TESTING"] = True
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "true")

    with flask_app.app_context():
        with patch("scripts.local.test_bot_ai_local.classify_intent") as ai_mock:
            try:
                run_local_ai_suggestion_test(mode="mock", phone_e164="+18090000000", inbound_text="hola")
                raised = None
            except Exception as exc:
                raised = exc

    assert isinstance(raised, LocalBotAISafetyError)
    ai_mock.assert_not_called()



def test_refuses_when_whatsapp_enabled(monkeypatch):
    flask_app.config["TESTING"] = True
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("WHATSAPP_ENABLED", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")

    with flask_app.app_context():
        with patch("scripts.local.test_bot_ai_local.classify_intent") as ai_mock:
            try:
                run_local_ai_suggestion_test(mode="mock", phone_e164="+18090000000", inbound_text="hola")
                raised = None
            except Exception as exc:
                raised = exc

    assert isinstance(raised, LocalBotAISafetyError)
    ai_mock.assert_not_called()


def test_refuses_when_dry_run_is_false(monkeypatch):
    flask_app.config["TESTING"] = True
    monkeypatch.setenv("BOT_DRY_RUN", "false")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")

    with flask_app.app_context():
        with patch("scripts.local.test_bot_ai_local.classify_intent") as ai_mock:
            try:
                run_local_ai_suggestion_test(mode="mock", phone_e164="+18090000000", inbound_text="hola")
                raised = None
            except Exception as exc:
                raised = exc

    assert isinstance(raised, LocalBotAISafetyError)
    ai_mock.assert_not_called()


def test_real_mode_refuses_when_ai_disabled(monkeypatch):
    flask_app.config["TESTING"] = True
    _set_safe_env(monkeypatch)
    monkeypatch.setenv("BOT_AI_ENABLED", "false")
    monkeypatch.setenv("BOT_AI_API_KEY", "fake-key")

    with flask_app.app_context():
        with patch("scripts.local.test_bot_ai_local.classify_intent") as ai_mock:
            try:
                run_local_ai_suggestion_test(mode="real", phone_e164="+18090000000", inbound_text="hola")
                raised = None
            except Exception as exc:
                raised = exc

    assert isinstance(raised, LocalBotAISafetyError)
    ai_mock.assert_not_called()


def test_main_output_does_not_print_api_key(monkeypatch, capsys):
    flask_app.config["TESTING"] = True
    _set_safe_env(monkeypatch)
    monkeypatch.setenv("BOT_AI_ENABLED", "true")
    monkeypatch.setenv("BOT_AI_API_KEY", "sk-test-local-secret-value")
    monkeypatch.setattr(
        "scripts.local.test_bot_ai_local.run_local_ai_suggestion_test",
        lambda **_kwargs: {
            "conversation_id": 1,
            "message_id": 2,
            "intent": "FAQ_REQUISITOS",
            "confidence": 0.9,
            "answer_text": "ok",
            "requires_human": False,
            "decision_log_id": 3,
            "mode": "real",
            "whatsapp_sent": False,
        },
    )
    monkeypatch.setattr("sys.argv", ["test_bot_ai_local.py", "--mode", "real"])

    code = local_ai_script.main()
    captured = capsys.readouterr()

    assert code == 0
    assert "sk-test-local-secret-value" not in captured.out
    assert "BOT_AI_API_KEY" not in captured.out
