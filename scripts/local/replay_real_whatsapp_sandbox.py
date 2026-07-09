from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_STAGING_MODE", "false")
os.environ.setdefault("BOT_SANDBOX_MODE", "false")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL_TEST", "sqlite:////tmp/app_web_real_sandbox_replay.db")
os.environ.setdefault("WHATSAPP_ENABLED", "false")
os.environ.setdefault("BOT_DRY_RUN", "true")
os.environ.setdefault("BOT_REAL_WHATSAPP_SANDBOX_ENABLED", "true")
os.environ.setdefault("BOT_REAL_WHATSAPP_MANUAL_REVIEW_REQUIRED", "true")
os.environ.setdefault("BOT_REAL_WHATSAPP_OWNER_ONLY", "true")
os.environ.setdefault("BOT_REAL_WHATSAPP_PROVIDER", "meta_sandbox")
os.environ.setdefault("BOT_REAL_WHATSAPP_ALLOWED_NUMBERS", "+18095550111,+18095550112")
os.environ.setdefault("BOT_REAL_WHATSAPP_SIMULATE", "true")
os.environ.setdefault("BOT_REAL_WHATSAPP_MAX_PER_MIN", "3")

from app import app as flask_app
from config_app import db
from models import BotContactIdentity, BotConversation, BotDecisionLog, BotEscalation, BotMessage, BotSandboxOutbound, BotSandboxReviewQueue, BotSetting
from services.bot_constants import MESSAGE_DIRECTION_INBOUND, MESSAGE_SOURCE_WHATSAPP_USER, MESSAGE_STATUS_INBOUND_RECEIVED
from services.bot_sandbox_review_service import approve_review
from services.bot_sandbox_service import (
    SandboxSafetyError,
    apply_delivery_webhook_update,
    run_sandbox_worker_once,
    set_real_sandbox_paused,
)


def _new_review(phone: str, txt: str) -> BotSandboxReviewQueue:
    conv = BotConversation(channel="whatsapp", phone_e164=phone, contact_name="Replay", status="open", metadata_json={"sandbox_conversation": True})
    db.session.add(conv)
    db.session.flush()
    inbound = BotMessage(
        conversation_id=int(conv.id),
        direction=MESSAGE_DIRECTION_INBOUND,
        source=MESSAGE_SOURCE_WHATSAPP_USER,
        message_type="text",
        text_body=txt,
        status=MESSAGE_STATUS_INBOUND_RECEIVED,
        wa_message_id=f"replay-{conv.id}",
    )
    db.session.add(inbound)
    db.session.flush()
    review = BotSandboxReviewQueue(
        conversation_id=int(conv.id),
        inbound_message_id=int(inbound.id),
        final_suggested_reply="Reply controlado",
        base_suggested_reply="Reply controlado",
        ai_suggested_reply="",
        status="pending_review",
        safety_status="ok",
        metadata_json={"requires_human": True, "current_step": "WELCOME"},
    )
    db.session.add(review)
    db.session.commit()
    return review


def main() -> None:
    with flask_app.app_context():
        with db.engine.begin() as conn:
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
        db.session.query(BotMessage).delete()
        db.session.query(BotConversation).delete()
        db.session.query(BotSetting).filter(BotSetting.key == "bot_real_whatsapp_sandbox_paused").delete()
        db.session.commit()
        set_real_sandbox_paused(paused=False, actor_id=1)
        db.session.commit()

        metrics = {
            "approved": 0,
            "blocked": 0,
            "sent": 0,
            "delivered": 0,
            "failed": 0,
            "retries": 0,
            "sandbox_provider_send_count": 0,
            "production_send_count": 0,
            "real_public_whatsapp_send_count": 0,
            "allowlisted_send_count": 0,
            "blocked_non_allowlisted_count": 0,
            "sends_without_review": 0,
            "sends_without_reviewer": 0,
            "sends_with_unmasked_number_in_ui_or_logs": 0,
            "provider_used": "",
        }

        r1 = _new_review("+18095550111", "hola")
        approve_review(review=r1, reviewer_id=1)
        metrics["approved"] += 1

        r2 = _new_review("+18095559999", "bloqueado")
        try:
            approve_review(review=r2, reviewer_id=1)
            metrics["approved"] += 1
        except SandboxSafetyError:
            metrics["blocked_non_allowlisted_count"] += 1

        stats = run_sandbox_worker_once(batch_size=50)
        metrics["sent"] += int(stats.get("sent", 0))
        metrics["failed"] += int(stats.get("failed", 0))
        metrics["blocked"] += int(stats.get("blocked", 0))

        for row in BotSandboxOutbound.query.all():
            metrics["provider_used"] = row.provider
            if row.state == "blocked" and "allowlist" in str(row.failure_reason or ""):
                metrics["blocked_non_allowlisted_count"] += 1

        first = BotSandboxOutbound.query.order_by(BotSandboxOutbound.id.asc()).first()
        if first:
            pid = (first.payload_json or {}).get("audit", {}).get("provider_message_id")
            if pid:
                apply_delivery_webhook_update(provider_message_id=pid, delivery_status="delivered", payload={"source": "replay"})
                metrics["delivered"] += 1

        os.environ["BOT_REAL_WHATSAPP_SIMULATE"] = "false"
        r3 = _new_review("+18095550112", "timeout")
        approve_review(review=r3, reviewer_id=1)
        stats2 = run_sandbox_worker_once(batch_size=50)
        metrics["failed"] += int(stats2.get("failed", 0))
        metrics["retries"] += int(stats2.get("retried", 0))

        set_real_sandbox_paused(paused=True, actor_id=1)
        db.session.commit()
        r4 = _new_review("+18095550111", "kill")
        try:
            approve_review(review=r4, reviewer_id=1)
        except SandboxSafetyError:
            metrics["blocked"] += 1
        stats3 = run_sandbox_worker_once(batch_size=50)
        metrics["blocked"] += int(stats3.get("blocked", 0))

        sent_rows = BotSandboxOutbound.query.filter(BotSandboxOutbound.state == "simulated_sent").all()
        for row in sent_rows:
            payload = dict(row.payload_json or {})
            meta = dict(payload.get("metadata") or {})
            audit = dict(payload.get("audit") or {})
            provider = str(row.provider or "")
            if provider in {"meta_sandbox", "twilio_sandbox"}:
                metrics["sandbox_provider_send_count"] += 1
            if provider in {"meta_production", "twilio_production", "meta", "twilio", "whatsapp_cloud"}:
                metrics["production_send_count"] += 1
            if bool(audit.get("real_public_send", False)):
                metrics["real_public_whatsapp_send_count"] += 1
            if bool(audit.get("allowlisted", False)):
                metrics["allowlisted_send_count"] += 1
            if not meta.get("review_approved", False) or int(meta.get("review_id") or 0) <= 0:
                metrics["sends_without_review"] += 1
            if int(meta.get("approved_by") or 0) <= 0 or int(meta.get("reviewer") or 0) <= 0:
                metrics["sends_without_reviewer"] += 1
            raw_to = str(((audit.get("request_payload") or {}).get("to") or "")).strip()
            raw_to_2 = str(audit.get("to") or "").strip()
            if raw_to.startswith("+") or raw_to_2.startswith("+"):
                metrics["sends_with_unmasked_number_in_ui_or_logs"] += 1
        print("REAL_WHATSAPP_SANDBOX_REPLAY")
        for k in [
            "approved",
            "blocked",
            "sent",
            "delivered",
            "failed",
            "retries",
            "sandbox_provider_send_count",
            "production_send_count",
            "real_public_whatsapp_send_count",
            "allowlisted_send_count",
            "blocked_non_allowlisted_count",
            "sends_without_review",
            "sends_without_reviewer",
            "sends_with_unmasked_number_in_ui_or_logs",
            "provider_used",
        ]:
            print(f"{k}={metrics[k]}")


if __name__ == "__main__":
    try:
        main()
    except SandboxSafetyError as exc:
        print(f"replay_blocked={exc}")
        raise
