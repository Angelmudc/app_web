from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("BOT_STAGING_MODE", "false")
os.environ.setdefault("BOT_SANDBOX_MODE", "false")
os.environ.setdefault("BOT_REAL_WHATSAPP_SANDBOX_ENABLED", "true")
os.environ.setdefault("BOT_REAL_WHATSAPP_MANUAL_REVIEW_REQUIRED", "true")
os.environ.setdefault("BOT_REAL_WHATSAPP_OWNER_ONLY", "true")
os.environ.setdefault("BOT_REAL_WHATSAPP_PROVIDER", "meta_sandbox")
os.environ.setdefault("BOT_REAL_WHATSAPP_ALLOWED_NUMBERS", "+18095550041")
os.environ.setdefault("BOT_SANDBOX_AUTO_REPLY_ENABLED", "true")
os.environ.setdefault("WHATSAPP_ENABLED", "true")
os.environ.setdefault("BOT_DRY_RUN", "false")
os.environ.setdefault("BOT_REAL_WHATSAPP_SIMULATE", "false")
os.environ.setdefault("WHATSAPP_VALIDATE_SIGNATURE", "false")
os.environ.setdefault("DATABASE_URL_TEST", "sqlite:////tmp/app_web_replay_sandbox_auto_reply.sqlite")

from app import app as flask_app
from config_app import db
from models import BotContactIdentity, BotConversation, BotDecisionLog, BotEscalation, BotMessage, BotSandboxOutbound, BotSandboxReviewQueue, BotSetting


def _ensure_and_reset() -> None:
    with db.engine.begin() as conn:
        BotSandboxReviewQueue.__table__.drop(bind=conn, checkfirst=True)
        BotSandboxOutbound.__table__.drop(bind=conn, checkfirst=True)
        BotEscalation.__table__.drop(bind=conn, checkfirst=True)
        BotDecisionLog.__table__.drop(bind=conn, checkfirst=True)
        BotMessage.__table__.drop(bind=conn, checkfirst=True)
        BotConversation.__table__.drop(bind=conn, checkfirst=True)
        BotContactIdentity.__table__.drop(bind=conn, checkfirst=True)
        BotSetting.__table__.drop(bind=conn, checkfirst=True)
        BotContactIdentity.__table__.create(bind=conn, checkfirst=True)
        BotConversation.__table__.create(bind=conn, checkfirst=True)
        BotMessage.__table__.create(bind=conn, checkfirst=True)
        BotDecisionLog.__table__.create(bind=conn, checkfirst=True)
        BotEscalation.__table__.create(bind=conn, checkfirst=True)
        BotSetting.__table__.create(bind=conn, checkfirst=True)
        BotSandboxOutbound.__table__.create(bind=conn, checkfirst=True)
        BotSandboxReviewQueue.__table__.create(bind=conn, checkfirst=True)


def _payload(*, wamid: str, wa_id: str, text: str) -> dict:
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": wa_id, "profile": {"name": "Replay"}}],
                            "messages": [{"id": wamid, "from": wa_id, "timestamp": "1715000021", "type": "text", "text": {"body": text}}],
                        }
                    }
                ]
            }
        ]
    }


def main() -> None:
    metrics = {
        "inbound_count": 0,
        "auto_reply_attempted": 0,
        "auto_reply_sent": 0,
        "auto_reply_skipped": 0,
        "duplicate_blocked": 0,
        "allowlist_blocked": 0,
        "production_send_count": 0,
        "real_public_whatsapp_send_count": 0,
        "sends_without_review": 0,
    }
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_and_reset()

    with patch("services.whatsapp_cloud_service.send_text_message", return_value={"ok": True, "wa_message_id": "wamid.replay.1", "http_status": 200, "raw_response": {"messages": [{"id": "wamid.replay.1"}]}}) as send_mock:
        ok = client.post("/bot/whatsapp/webhook", json=_payload(wamid="wamid-replay-1", wa_id="18095550041", text="hola autoreply test"))
        dup = client.post("/bot/whatsapp/webhook", json=_payload(wamid="wamid-replay-1", wa_id="18095550041", text="hola autoreply test"))
        blocked = client.post("/bot/whatsapp/webhook", json=_payload(wamid="wamid-replay-2", wa_id="18095559999", text="hola no allowlist"))
        assert ok.status_code == 200
        assert dup.status_code == 200
        assert blocked.status_code == 200
        metrics["auto_reply_attempted"] = int(send_mock.call_count)

    with flask_app.app_context():
        metrics["inbound_count"] = int(BotMessage.query.filter_by(direction="inbound").count())
        metrics["auto_reply_sent"] = int(BotSandboxOutbound.query.filter_by(state="simulated_sent").count())
        metrics["duplicate_blocked"] = int(BotMessage.query.filter_by(wa_message_id="wamid-replay-1").count() == 1)
        metrics["allowlist_blocked"] = int(
            BotSandboxReviewQueue.query.join(BotMessage, BotMessage.id == BotSandboxReviewQueue.inbound_message_id).filter(BotMessage.wa_message_id == "wamid-replay-2").count()
        )
        outboxes = BotSandboxOutbound.query.all()
        metrics["auto_reply_skipped"] = int(
            BotSandboxReviewQueue.query.join(BotMessage, BotMessage.id == BotSandboxReviewQueue.inbound_message_id).filter(BotMessage.wa_message_id == "wamid-replay-2").count()
        )
        for row in outboxes:
            provider = str(row.provider or "")
            if provider in {"meta_production", "twilio_production", "meta", "twilio", "whatsapp_cloud"}:
                metrics["production_send_count"] += 1
            audit = dict((dict(row.payload_json or {}).get("audit") or {}))
            if bool(audit.get("real_public_send", False)):
                metrics["real_public_whatsapp_send_count"] += 1
            meta = dict((dict(row.payload_json or {}).get("metadata") or {}))
            if str(meta.get("mode") or "") == "real_sandbox" and (not bool(meta.get("review_approved", False)) or int(meta.get("review_id") or 0) <= 0):
                metrics["sends_without_review"] += 1

    print("SANDBOX_AUTO_REPLY_REPLAY")
    for key in [
        "inbound_count",
        "auto_reply_attempted",
        "auto_reply_sent",
        "auto_reply_skipped",
        "duplicate_blocked",
        "allowlist_blocked",
        "production_send_count",
        "real_public_whatsapp_send_count",
        "sends_without_review",
    ]:
        print(f"{key}={metrics[key]}")
    print("sends_without_review permitido solo si review_mode=auto_sandbox")


if __name__ == "__main__":
    main()
