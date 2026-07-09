from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

tmp_db = Path(tempfile.gettempdir()) / "app_web_replay_whatsapp_sandbox_realistic.sqlite"
if tmp_db.exists():
    tmp_db.unlink()


os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL_TEST"] = f"sqlite:///{tmp_db}"
os.environ["BOT_STAGING_MODE"] = "true"
os.environ["BOT_SANDBOX_MODE"] = "true"
os.environ["WHATSAPP_ENABLED"] = "false"
os.environ["BOT_DRY_RUN"] = "true"
os.environ["BOT_AUTOREPLY_ENABLED"] = "false"
os.environ["BOT_AI_ENABLED"] = "false"
os.environ["BOT_SANDBOX_FAIL_RATE"] = "0"
os.environ["BOT_SANDBOX_TIMEOUT_RATE"] = "0"
os.environ["BOT_SANDBOX_WEBHOOK_SIGNATURE_REQUIRED"] = "true"
os.environ["BOT_SANDBOX_WEBHOOK_SECRET"] = "sandbox-secret"

from app import app
from config_app import db
from models import BotContactIdentity, BotConversation, BotDecisionLog, BotEscalation, BotMessage, BotSandboxOutbound, BotSandboxReviewQueue, BotSetting
from services.bot_sandbox_review_service import approve_review, reject_review
from services.bot_sandbox_service import run_sandbox_worker_once


def _reset() -> None:
    with app.app_context():
        BotSandboxReviewQueue.__table__.drop(bind=db.engine, checkfirst=True)
        BotSandboxOutbound.__table__.drop(bind=db.engine, checkfirst=True)
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
        BotSandboxOutbound.__table__.create(bind=db.engine, checkfirst=True)
        BotSandboxReviewQueue.__table__.create(bind=db.engine, checkfirst=True)


def _cloud_payload(*, message_id: str, from_num: str, msg_type: str, text: str = "hola") -> dict:
    msg = {
        "from": from_num,
        "id": message_id,
        "timestamp": "1710000000",
        "type": msg_type,
    }
    if msg_type == "text":
        msg["text"] = {"body": text}
    elif msg_type == "audio":
        msg["audio"] = {"id": f"media-{message_id}", "mime_type": "audio/ogg"}
    elif msg_type == "image":
        msg["image"] = {"id": f"media-{message_id}", "mime_type": "image/jpeg"}
    elif msg_type == "document":
        msg["document"] = {"id": f"media-{message_id}", "mime_type": "application/pdf"}
    return {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"value": {"messages": [msg], "contacts": [{"profile": {"name": "Sandbox"}, "wa_id": from_num}]}}]}],
    }


def _sig(payload: dict) -> str:
    raw = json.dumps(payload).encode("utf-8")
    return hmac.new(os.environ["BOT_SANDBOX_WEBHOOK_SECRET"].encode("utf-8"), raw, hashlib.sha256).hexdigest()


def main() -> int:
    _reset()
    metrics = {
        "inbound_total": 0,
        "normalized_ok": 0,
        "rejected_payloads": 0,
        "duplicates_blocked": 0,
        "media_requires_human": 0,
        "reviews_created": 0,
        "approved": 0,
        "rejected": 0,
        "simulated_sent": 0,
        "outbound_real_count": 0,
        "whatsapp_real_count": 0,
    }

    plans: list[tuple[dict | str, bool]] = []
    for i in range(5):
        plans.append((_cloud_payload(message_id=f"wamid.realistic.text.{i}", from_num=f"1999000001{i}", msg_type="text", text=f"hola {i}"), True))
    plans.append((_cloud_payload(message_id="wamid.realistic.audio.1", from_num="19990000031", msg_type="audio"), True))
    plans.append((_cloud_payload(message_id="wamid.realistic.audio.2", from_num="19990000032", msg_type="audio"), True))
    plans.append((_cloud_payload(message_id="wamid.realistic.image.1", from_num="19990000041", msg_type="image"), True))
    plans.append((_cloud_payload(message_id="wamid.realistic.doc.1", from_num="19990000042", msg_type="document"), True))
    plans.append((_cloud_payload(message_id="wamid.realistic.text.0", from_num="19990000010", msg_type="text", text="dup"), True))
    plans.append((_cloud_payload(message_id="wamid.realistic.text.1", from_num="19990000011", msg_type="text", text="dup"), True))
    plans.append((_cloud_payload(message_id="wamid.realistic.bad.sig", from_num="19990000050", msg_type="text", text="firma mala"), False))
    plans.append(("corrupt", True))

    with app.app_context():
        client = app.test_client()
        for payload, valid_sig in plans:
            metrics["inbound_total"] += 1
            if payload == "corrupt":
                resp = client.post("/admin/bot/sandbox/webhook/inbound", data="not-json", content_type="application/json")
            else:
                body = json.dumps(payload)
                sig = _sig(payload) if valid_sig else "bad-signature"
                resp = client.post(
                    "/admin/bot/sandbox/webhook/inbound",
                    data=body,
                    content_type="application/json",
                    headers={"X-Sandbox-Signature": sig},
                )

            if resp.status_code == 200:
                data = resp.get_json() or {}
                metrics["normalized_ok"] += 1
                if bool(data.get("duplicate_webhook")):
                    metrics["duplicates_blocked"] += 1
                if bool(data.get("requires_human")) and str(data.get("message_type") or "") in {"audio", "image", "document"}:
                    metrics["media_requires_human"] += 1
            else:
                metrics["rejected_payloads"] += 1

        metrics["reviews_created"] = BotSandboxReviewQueue.query.count()

        reviews = BotSandboxReviewQueue.query.order_by(BotSandboxReviewQueue.id.asc()).all()
        for i, review in enumerate(reviews):
            inbound = review.inbound_message
            msg_type = str((inbound.message_type if inbound else "text") or "text")
            if msg_type in {"audio", "image", "document"}:
                if i % 2 == 0:
                    approve_review(review=review, reviewer_id=1, edited_text=None)
                    metrics["approved"] += 1
                else:
                    reject_review(review=review, reviewer_id=1, reason="manual_media_reject")
                    metrics["rejected"] += 1
            else:
                if i % 4 == 0:
                    reject_review(review=review, reviewer_id=1, reason="manual_reject")
                    metrics["rejected"] += 1
                else:
                    approve_review(review=review, reviewer_id=1, edited_text=None)
                    metrics["approved"] += 1
        db.session.commit()

        run_sandbox_worker_once(batch_size=200)
        metrics["simulated_sent"] = BotSandboxReviewQueue.query.filter_by(status="simulated_sent").count()
        metrics["outbound_real_count"] = BotSandboxOutbound.query.filter(BotSandboxOutbound.provider != "fake").count()
        metrics["whatsapp_real_count"] = BotMessage.query.filter(BotMessage.source != "whatsapp_user").filter(BotMessage.message_type == "whatsapp").count()

    for key in (
        "inbound_total",
        "normalized_ok",
        "rejected_payloads",
        "duplicates_blocked",
        "media_requires_human",
        "reviews_created",
        "approved",
        "rejected",
        "simulated_sent",
        "outbound_real_count",
        "whatsapp_real_count",
    ):
        print(f"{key}={metrics[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
