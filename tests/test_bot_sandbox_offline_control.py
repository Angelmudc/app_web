from __future__ import annotations

import pytest
from sqlalchemy import inspect

from app import app as flask_app
from config_app import db
from models import BotContactIdentity, BotConversation, BotDecisionLog, BotEscalation, BotMessage, BotSandboxOutbound, BotSetting
from services.bot_message_service import create_manual_message
from services.bot_sandbox_service import (
    SandboxSafetyError,
    enqueue_sandbox_outbound,
    force_fail_outbox_row,
    is_staging_offline_active,
    run_sandbox_worker_once,
)


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


def _new_conv(phone: str = "+19990000001") -> BotConversation:
    conv = BotConversation(channel="whatsapp", phone_e164=phone, contact_name="Sandbox", status="open", metadata_json={"sandbox_conversation": True})
    db.session.add(conv)
    db.session.commit()
    return conv


def test_offline_mode_flag_pair(monkeypatch):
    _base_env(monkeypatch)
    assert is_staging_offline_active() is True


def test_enqueue_and_worker_simulated_sent(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_SANDBOX_FAIL_RATE", "0")
    monkeypatch.setenv("BOT_SANDBOX_TIMEOUT_RATE", "0")
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conv()
        msg = create_manual_message(conversation=conv, text_body="hola")
        row = enqueue_sandbox_outbound(conversation=conv, message=msg)
        assert row.state == "queued"
        stats = run_sandbox_worker_once()
        assert stats["sent"] == 1
        db.session.refresh(row)
        assert row.state == "simulated_sent"


def test_retry_and_block_after_max(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_SANDBOX_FAIL_RATE", "1")
    monkeypatch.setenv("BOT_SANDBOX_TIMEOUT_RATE", "0")
    monkeypatch.setenv("BOT_SANDBOX_MAX_RETRIES", "2")
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conv()
        msg = create_manual_message(conversation=conv, text_body="retry")
        row = enqueue_sandbox_outbound(conversation=conv, message=msg)
        run_sandbox_worker_once()
        run_sandbox_worker_once()
        run_sandbox_worker_once()
        db.session.refresh(row)
        assert row.state in {"blocked", "failed"}
        assert int(row.retry_count or 0) >= 2


def test_duplicate_enqueue_returns_same_row(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conv()
        msg = create_manual_message(conversation=conv, text_body="dup")
        r1 = enqueue_sandbox_outbound(conversation=conv, message=msg)
        r2 = enqueue_sandbox_outbound(conversation=conv, message=msg)
        assert r1.id == r2.id


def test_sandbox_outbox_table_exists_before_enqueue(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        tables = set(inspect(db.engine).get_table_names())
        assert "bot_sandbox_outbox" in tables
        conv = _new_conv()
        msg = create_manual_message(conversation=conv, text_body="table-check")
        r1 = enqueue_sandbox_outbound(conversation=conv, message=msg)
        r2 = enqueue_sandbox_outbound(conversation=conv, message=msg)
        assert r1.id == r2.id


def test_real_phone_blocked(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conv(phone="+18095550001")
        msg = create_manual_message(conversation=conv, text_body="x")
        with pytest.raises(SandboxSafetyError):
            enqueue_sandbox_outbound(conversation=conv, message=msg)


def test_corruption_and_force_fail(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conv()
        msg = create_manual_message(conversation=conv, text_body="corrupt")
        row = enqueue_sandbox_outbound(conversation=conv, message=msg)
        row.state = "processing"
        db.session.commit()
        force_fail_outbox_row(int(row.id), "worker_restart")
        db.session.refresh(row)
        assert row.state == "failed"


def test_parallel_worker_no_illegal_duplicate_send(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_SANDBOX_FAIL_RATE", "0")
    monkeypatch.setenv("BOT_SANDBOX_TIMEOUT_RATE", "0")
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conv()
        for i in range(15):
            msg = create_manual_message(conversation=conv, text_body=f"m{i}")
            enqueue_sandbox_outbound(conversation=conv, message=msg)

        run_sandbox_worker_once(batch_size=10)
        # Simula restart + doble procesamiento del worker sin corrupción de estado.
        run_sandbox_worker_once(batch_size=10)
        run_sandbox_worker_once(batch_size=10)

        sent = BotSandboxOutbound.query.filter_by(state="simulated_sent").count()
        total = BotSandboxOutbound.query.count()
        assert sent == total
