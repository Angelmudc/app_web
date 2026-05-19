# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from app import app as flask_app
from config_app import db
from models import (
    BotContactIdentity,
    BotConversation,
    BotDecisionLog,
    BotEscalation,
    BotMessage,
    BotSetting,
)
from services.bot_constants import (
    CONVERSATION_STATUS_BOT_PAUSED,
    CONVERSATION_STATUS_OPEN,
    CONVERSATION_STATUS_RESOLVED,
    DECISION_RESULT_ALLOW,
    DECISION_TYPE_AUTO_REPLY,
    MESSAGE_DIRECTION_OUTBOUND,
    MESSAGE_SOURCE_ADMIN_MANUAL,
    MESSAGE_STATUS_OUTBOUND_QUEUED,
)
from services.bot_conversation_service import activate_conversation, get_or_create_manual_conversation, pause_conversation, resolve_conversation
from services.bot_decision_service import register_decision
from services.bot_message_service import create_manual_message
from services.bot_seed import DEFAULT_BOT_SETTINGS, seed_bot_settings


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


def test_bot_conversation_create_and_status_cycle():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

        conv = get_or_create_manual_conversation(phone_e164="+18095550111", contact_name="Prueba")
        assert conv is not None
        assert conv.status == CONVERSATION_STATUS_OPEN
        assert conv.bot_paused is False

        pause_conversation(conv, reason="qa")
        conv = db.session.get(BotConversation, int(conv.id))
        assert conv is not None
        assert conv.bot_paused is True
        assert conv.status == CONVERSATION_STATUS_BOT_PAUSED

        activate_conversation(conv)
        conv = db.session.get(BotConversation, int(conv.id))
        assert conv is not None
        assert conv.bot_paused is False
        assert conv.status == CONVERSATION_STATUS_OPEN

        resolve_conversation(conv)
        conv = db.session.get(BotConversation, int(conv.id))
        assert conv is not None
        assert conv.status == CONVERSATION_STATUS_RESOLVED
        assert conv.resolved_at is not None


def test_bot_message_create_manual():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

        conv = get_or_create_manual_conversation(phone_e164="+18095550112")
        msg = create_manual_message(conversation=conv, text_body="mensaje interno")

        assert msg.direction == MESSAGE_DIRECTION_OUTBOUND
        assert msg.source == MESSAGE_SOURCE_ADMIN_MANUAL
        assert msg.status == MESSAGE_STATUS_OUTBOUND_QUEUED

        conv_db = db.session.get(BotConversation, int(conv.id))
        assert conv_db is not None
        assert conv_db.last_message_at is not None


def test_bot_decision_register_and_invalid_states():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

        conv = get_or_create_manual_conversation(phone_e164="+18095550113")
        decision = register_decision(
            conversation=conv,
            decision_type=DECISION_TYPE_AUTO_REPLY,
            decision_result=DECISION_RESULT_ALLOW,
            rule_code="R_FAQ",
            reason_human="FAQ permitida",
            facts_json={"ok": True},
        )
        assert decision.id is not None

        with pytest.raises(ValueError):
            register_decision(
                conversation=conv,
                decision_type="INVALID",
                decision_result=DECISION_RESULT_ALLOW,
                rule_code="R",
                reason_human="x",
            )


def test_bot_conversation_requires_phone():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

        with pytest.raises(ValueError):
            get_or_create_manual_conversation(phone_e164="   ")


def test_bot_message_rejects_empty_text_and_invalid_direction_source():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

        conv = get_or_create_manual_conversation(phone_e164="+18095550114")

        with pytest.raises(ValueError):
            create_manual_message(conversation=conv, text_body="  ")

        with pytest.raises(ValueError):
            create_manual_message(conversation=conv, text_body="x", direction="inbound", source="admin_manual")


def test_seed_bot_settings_is_idempotent():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

        seed_bot_settings()
        first_count = BotSetting.query.count()
        assert first_count == len(DEFAULT_BOT_SETTINGS)

        seed_bot_settings()
        second_count = BotSetting.query.count()
        assert second_count == first_count
