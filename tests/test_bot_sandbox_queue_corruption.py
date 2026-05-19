from __future__ import annotations

import pytest

from app import app as flask_app
from config_app import db
from models import BotContactIdentity, BotConversation, BotDecisionLog, BotEscalation, BotMessage, BotSandboxOutbound, BotSetting
from services.bot_message_service import create_manual_message
from services.bot_sandbox_service import SandboxSafetyError, enqueue_sandbox_outbound, run_sandbox_worker_once


def _ensure_tables() -> None:
    db.session.remove()
    with db.engine.begin() as conn:
        BotContactIdentity.__table__.create(bind=conn, checkfirst=True)
        BotConversation.__table__.create(bind=conn, checkfirst=True)
        BotMessage.__table__.create(bind=conn, checkfirst=True)
        BotDecisionLog.__table__.create(bind=conn, checkfirst=True)
        BotSetting.__table__.create(bind=conn, checkfirst=True)
        BotEscalation.__table__.create(bind=conn, checkfirst=True)
        BotSandboxOutbound.__table__.create(bind=conn, checkfirst=True)
    db.session.query(BotSandboxOutbound).delete()
    db.session.query(BotEscalation).delete()
    db.session.query(BotDecisionLog).delete()
    db.session.query(BotMessage).delete()
    db.session.query(BotConversation).delete()
    db.session.query(BotContactIdentity).delete()
    db.session.query(BotSetting).delete()
    db.session.commit()
    db.session.remove()


def _base_env(monkeypatch):
    monkeypatch.setenv("BOT_STAGING_MODE", "true")
    monkeypatch.setenv("BOT_SANDBOX_MODE", "true")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")


def _new_conv() -> BotConversation:
    conv = BotConversation(channel="whatsapp", phone_e164="+19990000111", contact_name="Sandbox", status="open", metadata_json={"sandbox_conversation": True})
    db.session.add(conv)
    db.session.commit()
    return conv


def test_duplicate_bot_message_id_protected(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conv()
        msg = create_manual_message(conversation=conv, text_body="dup-id")
        row1 = enqueue_sandbox_outbound(conversation=conv, message=msg)
        row2 = enqueue_sandbox_outbound(conversation=conv, message=msg)
        assert row1.id == row2.id


def test_invalid_state_write_is_rejected_by_constraint(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conv()
        msg = create_manual_message(conversation=conv, text_body="bad-state")
        row = enqueue_sandbox_outbound(conversation=conv, message=msg)
        row.state = "???"
        with pytest.raises(Exception):
            db.session.commit()


def test_next_retry_at_prevents_immediate_retry(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_SANDBOX_FAIL_RATE", "1")
    monkeypatch.setenv("BOT_SANDBOX_TIMEOUT_RATE", "0")
    monkeypatch.setenv("BOT_SANDBOX_RETRY_BACKOFF_SECONDS", "60")
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conv()
        msg = create_manual_message(conversation=conv, text_body="retry-gate")
        row = enqueue_sandbox_outbound(conversation=conv, message=msg)

        run_sandbox_worker_once(batch_size=10)
        db.session.refresh(row)
        assert row.state == "failed"
        first_retry = int(row.retry_count or 0)

        run_sandbox_worker_once(batch_size=10)
        db.session.refresh(row)
        assert int(row.retry_count or 0) == first_retry


def test_provider_fake_required(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conv()
        msg = create_manual_message(conversation=conv, text_body="provider")
        with pytest.raises(SandboxSafetyError):
            enqueue_sandbox_outbound(conversation=conv, message=msg, provider="whatsapp_cloud")
