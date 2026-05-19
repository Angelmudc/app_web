from __future__ import annotations

from datetime import timedelta

from app import app as flask_app
from config_app import db
from models import BotContactIdentity, BotConversation, BotDecisionLog, BotEscalation, BotMessage, BotSandboxOutbound, BotSetting
from services.bot_message_service import create_manual_message
from services.bot_sandbox_service import enqueue_sandbox_outbound, recover_orphan_processing_rows, run_sandbox_worker_once
from utils.timezone import utc_now_naive


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


def test_orphan_processing_rows_are_recovered(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_SANDBOX_PROCESSING_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("BOT_SANDBOX_FAIL_RATE", "0")
    monkeypatch.setenv("BOT_SANDBOX_TIMEOUT_RATE", "0")

    with flask_app.app_context():
        _ensure_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+19990000999", contact_name="Recovery", status="open", metadata_json={"sandbox_conversation": True})
        db.session.add(conv)
        db.session.commit()
        msg = create_manual_message(conversation=conv, text_body="recover")
        row = enqueue_sandbox_outbound(conversation=conv, message=msg)
        row.state = "processing"
        row.processing_at = utc_now_naive() - timedelta(minutes=30)
        db.session.commit()

        recovered = recover_orphan_processing_rows()
        assert recovered == 1
        db.session.refresh(row)
        assert row.state == "failed"
        assert int(row.retry_count or 0) >= 1


def test_run_worker_recovers_then_sends(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_SANDBOX_PROCESSING_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("BOT_SANDBOX_FAIL_RATE", "0")
    monkeypatch.setenv("BOT_SANDBOX_TIMEOUT_RATE", "0")

    with flask_app.app_context():
        _ensure_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+19990000123", contact_name="Recovery", status="open", metadata_json={"sandbox_conversation": True})
        db.session.add(conv)
        db.session.commit()
        msg = create_manual_message(conversation=conv, text_body="recover-and-send")
        row = enqueue_sandbox_outbound(conversation=conv, message=msg)
        row.state = "processing"
        row.processing_at = utc_now_naive() - timedelta(minutes=10)
        db.session.commit()

        stats = run_sandbox_worker_once(batch_size=10)
        assert stats["recovered"] >= 1
        db.session.refresh(row)
        assert row.state in {"failed", "simulated_sent"}
