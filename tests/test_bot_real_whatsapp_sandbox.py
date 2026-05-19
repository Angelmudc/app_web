from __future__ import annotations

from app import app as flask_app
from config_app import db
from models import (
    BotContactIdentity,
    BotConversation,
    BotDecisionLog,
    BotEscalation,
    BotMessage,
    BotSandboxOutbound,
    BotSandboxReviewQueue,
    BotSetting,
)
from services.bot_constants import MESSAGE_DIRECTION_INBOUND, MESSAGE_SOURCE_WHATSAPP_USER, MESSAGE_STATUS_INBOUND_RECEIVED
from services.bot_sandbox_review_service import approve_review
from services.bot_sandbox_service import (
    SandboxSafetyError,
    apply_delivery_webhook_update,
    enqueue_sandbox_outbound,
    is_real_sandbox_paused,
    is_sandbox_auto_reply_active,
    run_sandbox_worker_once,
    set_real_sandbox_paused,
)
from unittest.mock import patch


def _ensure_tables() -> None:
    db.session.remove()
    with db.engine.begin() as conn:
        BotSandboxReviewQueue.__table__.drop(bind=conn, checkfirst=True)
        BotSandboxOutbound.__table__.drop(bind=conn, checkfirst=True)
        BotContactIdentity.__table__.create(bind=conn, checkfirst=True)
        BotConversation.__table__.create(bind=conn, checkfirst=True)
        BotMessage.__table__.create(bind=conn, checkfirst=True)
        BotDecisionLog.__table__.create(bind=conn, checkfirst=True)
        BotSetting.__table__.create(bind=conn, checkfirst=True)
        BotEscalation.__table__.create(bind=conn, checkfirst=True)
        BotSandboxOutbound.__table__.create(bind=conn, checkfirst=True)
        BotSandboxReviewQueue.__table__.create(bind=conn, checkfirst=True)
    db.session.query(BotSandboxReviewQueue).delete()
    db.session.query(BotSandboxOutbound).delete()
    db.session.query(BotEscalation).delete()
    db.session.query(BotDecisionLog).delete()
    db.session.query(BotMessage).delete()
    db.session.query(BotConversation).delete()
    db.session.query(BotContactIdentity).delete()
    db.session.query(BotSetting).delete()
    db.session.commit()


def _base_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("BOT_STAGING_MODE", "false")
    monkeypatch.setenv("BOT_SANDBOX_MODE", "false")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_SANDBOX_ENABLED", "true")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_MANUAL_REVIEW_REQUIRED", "true")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_OWNER_ONLY", "true")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_PROVIDER", "meta_sandbox")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_ALLOWED_NUMBERS", "+18095550111,+18095550112")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_SIMULATE", "true")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_MAX_PER_MIN", "2")
    monkeypatch.setenv("BOT_SANDBOX_MAX_RETRIES", "2")


def _new_review(phone: str = "+18095550111") -> BotSandboxReviewQueue:
    conv = BotConversation(channel="whatsapp", phone_e164=phone, contact_name="Owner", status="open", metadata_json={"sandbox_conversation": True})
    db.session.add(conv)
    db.session.flush()
    inbound = BotMessage(
        conversation_id=int(conv.id),
        direction=MESSAGE_DIRECTION_INBOUND,
        source=MESSAGE_SOURCE_WHATSAPP_USER,
        message_type="text",
        text_body="hola",
        status=MESSAGE_STATUS_INBOUND_RECEIVED,
        wa_message_id=f"wa-{conv.id}",
    )
    db.session.add(inbound)
    db.session.flush()
    review = BotSandboxReviewQueue(
        conversation_id=int(conv.id),
        inbound_message_id=int(inbound.id),
        final_suggested_reply="Mensaje controlado",
        base_suggested_reply="Mensaje controlado",
        ai_suggested_reply="",
        status="pending_review",
        safety_status="ok",
        metadata_json={"requires_human": True, "current_step": "WELCOME"},
    )
    db.session.add(review)
    db.session.commit()
    return review


def test_01_numero_no_allowlisted_bloqueado(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        review = _new_review(phone="+18095559999")
        try:
            approve_review(review=review, reviewer_id=1)
            db.session.commit()
            assert False
        except SandboxSafetyError as exc:
            db.session.rollback()
            assert "allowlist_blocked" in str(exc)


def test_02_review_requerida(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+18095550111", contact_name="Owner", status="open", metadata_json={"sandbox_conversation": True})
        db.session.add(conv)
        db.session.flush()
        msg = BotMessage(conversation_id=int(conv.id), direction="outbound", source="admin_manual", message_type="text", text_body="hola", status="outbound_queued")
        db.session.add(msg)
        db.session.flush()
        try:
            enqueue_sandbox_outbound(conversation=conv, message=msg, provider="meta_sandbox", metadata={"mode": "real_sandbox", "review_approved": False})
            assert False
        except SandboxSafetyError as exc:
            assert "review_required" in str(exc)


def test_03_approve_permite_enqueue_real_sandbox(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        review = _new_review()
        approve_review(review=review, reviewer_id=1)
        db.session.commit()
        row = BotSandboxOutbound.query.first()
        assert row is not None
        assert row.provider == "meta_sandbox"
        assert (row.payload_json or {}).get("metadata", {}).get("mode") == "real_sandbox"


def test_04_send_sin_approve_bloqueado(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        review = _new_review()
        assert BotSandboxOutbound.query.count() == 0
        stats = run_sandbox_worker_once(batch_size=10)
        assert stats["picked"] == 0


def test_05_provider_fake_fallback(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_REAL_WHATSAPP_PROVIDER", "fake")
    with flask_app.app_context():
        _ensure_tables()
        review = _new_review()
        approve_review(review=review, reviewer_id=1)
        db.session.commit()
        stats = run_sandbox_worker_once(batch_size=10)
        row = BotSandboxOutbound.query.first()
        assert stats["sent"] == 1
        assert row.provider == "fake"


def test_06_timeout_retry(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_REAL_WHATSAPP_SIMULATE", "false")
    with flask_app.app_context():
        _ensure_tables()
        review = _new_review()
        approve_review(review=review, reviewer_id=1)
        db.session.commit()
        stats = run_sandbox_worker_once(batch_size=10)
        row = BotSandboxOutbound.query.first()
        assert stats["failed"] == 1
        assert row.state == "failed"
        assert int(row.retry_count or 0) == 1


def test_06b_worker_real_sandbox_usa_adapter_meta(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_REAL_WHATSAPP_SIMULATE", "false")
    monkeypatch.setenv("WHATSAPP_ENABLED", "true")
    monkeypatch.setenv("BOT_DRY_RUN", "false")
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "x")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "123")
    with flask_app.app_context():
        _ensure_tables()
        review = _new_review()
        approve_review(review=review, reviewer_id=1)
        db.session.commit()
        with patch("services.whatsapp_cloud_service.send_text_message", return_value={"ok": True, "status": "sent", "wa_message_id": "wamid.1", "raw_response": {"messages": [{"id": "wamid.1"}]}, "http_status": 200}) as send_mock:
            stats = run_sandbox_worker_once(batch_size=10)
            assert stats["sent"] == 1
            assert send_mock.call_count == 1


def test_07_duplicate_delivery_webhook(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        review = _new_review()
        approve_review(review=review, reviewer_id=1)
        db.session.commit()
        run_sandbox_worker_once(batch_size=10)
        row = BotSandboxOutbound.query.first()
        pid = (row.payload_json or {}).get("audit", {}).get("provider_message_id")
        apply_delivery_webhook_update(provider_message_id=pid, delivery_status="delivered", payload={"x": 1})
        apply_delivery_webhook_update(provider_message_id=pid, delivery_status="delivered", payload={"x": 1})
        db.session.refresh(row)
        updates = ((row.payload_json or {}).get("audit", {}).get("delivery", {}).get("updates") or [])
        assert len(updates) >= 2


def test_08_delivery_update_persistido(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        review = _new_review()
        approve_review(review=review, reviewer_id=1)
        db.session.commit()
        run_sandbox_worker_once(batch_size=10)
        row = BotSandboxOutbound.query.first()
        pid = (row.payload_json or {}).get("audit", {}).get("provider_message_id")
        res = apply_delivery_webhook_update(provider_message_id=pid, delivery_status="delivered", payload={})
        db.session.commit()
        db.session.refresh(row)
        assert res["ok"] is True
        assert (row.payload_json or {}).get("audit", {}).get("delivery", {}).get("status") == "delivered"


def test_09_kill_switch(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        review = _new_review()
        approve_review(review=review, reviewer_id=1)
        set_real_sandbox_paused(paused=True, actor_id=1)
        db.session.commit()
        stats = run_sandbox_worker_once(batch_size=10)
        row = BotSandboxOutbound.query.first()
        db.session.refresh(row)
        assert is_real_sandbox_paused() is True
        assert stats["blocked"] >= 1
        assert row.state == "blocked"


def test_10_rate_limit(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        r1 = _new_review()
        r2 = _new_review()
        approve_review(review=r1, reviewer_id=1)
        approve_review(review=r2, reviewer_id=1)
        db.session.commit()
        # third in same minute must be blocked on enqueue
        r3 = _new_review()
        try:
            approve_review(review=r3, reviewer_id=1)
            db.session.commit()
        except SandboxSafetyError:
            db.session.rollback()
        rows = BotSandboxOutbound.query.order_by(BotSandboxOutbound.id.asc()).all()
        assert len(rows) == 2


def test_11_pause_resume(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        set_real_sandbox_paused(paused=True, actor_id=1)
        assert is_real_sandbox_paused() is True
        set_real_sandbox_paused(paused=False, actor_id=1)
        assert is_real_sandbox_paused() is False


def test_12_no_production_bypass(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_REAL_WHATSAPP_PROVIDER", "meta_production")
    with flask_app.app_context():
        _ensure_tables()
        review = _new_review()
        try:
            approve_review(review=review, reviewer_id=1)
            db.session.commit()
        except SandboxSafetyError:
            pass
        assert BotSandboxOutbound.query.count() == 0


def test_13_sandbox_send_count_allowlisted_only(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        ok = _new_review(phone="+18095550111")
        approve_review(review=ok, reviewer_id=1)
        blocked = _new_review(phone="+18095559999")
        try:
            approve_review(review=blocked, reviewer_id=1)
        except SandboxSafetyError:
            pass
        db.session.commit()
        stats = run_sandbox_worker_once(batch_size=20)
        assert stats["sent"] >= 1
        rows = BotSandboxOutbound.query.filter_by(state="simulated_sent").all()
        assert len(rows) >= 1
        assert all(r.phone_e164 in {"+18095550111", "+18095550112"} for r in rows)


def test_14_production_send_count_zero(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        review = _new_review()
        approve_review(review=review, reviewer_id=1)
        db.session.commit()
        run_sandbox_worker_once(batch_size=10)
        rows = BotSandboxOutbound.query.filter_by(state="simulated_sent").all()
        production = [r for r in rows if r.provider in {"meta_production", "twilio_production", "meta", "twilio", "whatsapp_cloud"}]
        assert len(production) == 0


def test_15_public_real_send_count_zero(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        review = _new_review()
        approve_review(review=review, reviewer_id=1)
        db.session.commit()
        run_sandbox_worker_once(batch_size=10)
        rows = BotSandboxOutbound.query.filter_by(state="simulated_sent").all()
        assert all(not bool((r.payload_json or {}).get("audit", {}).get("real_public_send", False)) for r in rows)


def test_16_masked_number_in_audit(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        review = _new_review(phone="+18095550111")
        approve_review(review=review, reviewer_id=1)
        db.session.commit()
        row = BotSandboxOutbound.query.first()
        payload = row.payload_json or {}
        audit = payload.get("audit", {})
        req = audit.get("request_payload", {})
        assert str(req.get("to", "")).strip() == ""
        assert str(req.get("to_masked", "")).startswith("***")


def test_17_review_id_and_approved_by_required(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+18095550111", contact_name="Owner", status="open", metadata_json={"sandbox_conversation": True})
        db.session.add(conv)
        db.session.flush()
        msg = BotMessage(conversation_id=int(conv.id), direction="outbound", source="admin_manual", message_type="text", text_body="hola", status="outbound_queued")
        db.session.add(msg)
        db.session.flush()
        try:
            enqueue_sandbox_outbound(
                conversation=conv,
                message=msg,
                provider="meta_sandbox",
                metadata={"mode": "real_sandbox", "review_approved": True, "manual_review_required": True, "owner_only": True, "auto_send_allowed": False},
            )
            assert False
        except SandboxSafetyError as exc:
            assert "review_id_required" in str(exc)


def test_18_owner_only_false_blocks(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+18095550111", contact_name="Owner", status="open", metadata_json={"sandbox_conversation": True})
        db.session.add(conv)
        db.session.flush()
        msg = BotMessage(conversation_id=int(conv.id), direction="outbound", source="admin_manual", message_type="text", text_body="hola", status="outbound_queued")
        db.session.add(msg)
        db.session.flush()
        try:
            enqueue_sandbox_outbound(
                conversation=conv,
                message=msg,
                provider="meta_sandbox",
                metadata={
                    "mode": "real_sandbox",
                    "review_approved": True,
                    "review_id": 1,
                    "reviewer": 1,
                    "approved_by": 1,
                    "manual_review_required": True,
                    "owner_only": False,
                    "auto_send_allowed": False,
                },
            )
            assert False
        except SandboxSafetyError as exc:
            assert "owner_only_required" in str(exc)


def test_19_manual_review_required_false_blocks(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+18095550111", contact_name="Owner", status="open", metadata_json={"sandbox_conversation": True})
        db.session.add(conv)
        db.session.flush()
        msg = BotMessage(conversation_id=int(conv.id), direction="outbound", source="admin_manual", message_type="text", text_body="hola", status="outbound_queued")
        db.session.add(msg)
        db.session.flush()
        try:
            enqueue_sandbox_outbound(
                conversation=conv,
                message=msg,
                provider="meta_sandbox",
                metadata={
                    "mode": "real_sandbox",
                    "review_approved": True,
                    "review_id": 1,
                    "reviewer": 1,
                    "approved_by": 1,
                    "manual_review_required": False,
                    "owner_only": True,
                    "auto_send_allowed": False,
                },
            )
            assert False
        except SandboxSafetyError as exc:
            assert "manual_review_required" in str(exc)


def test_20_allowlisted_without_approved_review_blocks(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+18095550111", contact_name="Owner", status="open", metadata_json={"sandbox_conversation": True})
        db.session.add(conv)
        db.session.flush()
        msg = BotMessage(conversation_id=int(conv.id), direction="outbound", source="admin_manual", message_type="text", text_body="hola", status="outbound_queued")
        db.session.add(msg)
        db.session.flush()
        try:
            enqueue_sandbox_outbound(
                conversation=conv,
                message=msg,
                provider="meta_sandbox",
                metadata={
                    "mode": "real_sandbox",
                    "review_approved": False,
                    "review_id": 1,
                    "reviewer": 1,
                    "approved_by": 1,
                    "manual_review_required": True,
                    "owner_only": True,
                    "auto_send_allowed": False,
                },
            )
            assert False
        except SandboxSafetyError as exc:
            assert "review_required" in str(exc)


def test_21_auto_reply_block_provider_no_sandbox(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_SANDBOX_AUTO_REPLY_ENABLED", "true")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_PROVIDER", "meta_production")
    monkeypatch.setenv("WHATSAPP_ENABLED", "true")
    monkeypatch.setenv("BOT_DRY_RUN", "false")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_SIMULATE", "false")
    with flask_app.app_context():
        assert is_sandbox_auto_reply_active() is False


def test_22_auto_reply_block_owner_only_false(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_SANDBOX_AUTO_REPLY_ENABLED", "true")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_OWNER_ONLY", "false")
    monkeypatch.setenv("WHATSAPP_ENABLED", "true")
    monkeypatch.setenv("BOT_DRY_RUN", "false")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_SIMULATE", "false")
    with flask_app.app_context():
        assert is_sandbox_auto_reply_active() is False
