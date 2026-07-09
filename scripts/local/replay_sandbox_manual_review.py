from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

tmp_db = Path(tempfile.gettempdir()) / "app_web_replay_sandbox_manual_review.sqlite"
if tmp_db.exists():
    tmp_db.unlink()

def _set_env() -> None:
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


_set_env()

from app import app
from config_app import db
from models import BotContactIdentity, BotConversation, BotDecisionLog, BotEscalation, BotMessage, BotSandboxOutbound, BotSandboxReviewQueue, BotSetting
from services.bot_sandbox_service import run_sandbox_worker_once
from services.bot_sandbox_review_service import approve_review, reject_review


def main() -> int:
    inbound_total = 0
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
        client = app.test_client()

        for i in range(1, 6):
            resp = client.post(
                "/admin/bot/sandbox/webhook/inbound",
                json={
                    "from": f"+1999000000{i}",
                    "name": f"Candidata Sandbox {i}",
                    "message": f"hola sandbox {i}",
                    "message_id": f"replay-wa-{i:03d}",
                    "timestamp": "2026-05-12T10:00:00Z",
                },
            )
            if resp.status_code == 200:
                inbound_total += 1

        reviews = BotSandboxReviewQueue.query.order_by(BotSandboxReviewQueue.id.asc()).all()
        reviews_created = len(reviews)

        approved = 0
        rejected = 0
        edited = 0
        blocked = 0
        unsafe_allowed_count = 0

        for idx, review in enumerate(reviews):
            if idx in {0, 1}:
                approve_review(review=review, reviewer_id=1, edited_text=None)
                approved += 1
            elif idx == 2:
                approve_review(review=review, reviewer_id=1, edited_text="Gracias. Continúa con la siguiente etapa, por favor.")
                edited += 1
            elif idx == 3:
                reject_review(review=review, reviewer_id=1, reason="manual_reject_replay")
                rejected += 1
            else:
                approve_review(review=review, reviewer_id=1, edited_text="Te conseguimos empleo hoy mismo.")
                if review.status == "blocked":
                    blocked += 1
                else:
                    unsafe_allowed_count += 1
        db.session.commit()

        run_sandbox_worker_once(batch_size=100)

        simulated_sent = BotSandboxReviewQueue.query.filter_by(status="simulated_sent").count()
        outbound_real_count = BotSandboxOutbound.query.filter(BotSandboxOutbound.provider != "fake").count()

    report = {
        "inbound_total": inbound_total,
        "reviews_created": reviews_created,
        "approved": approved,
        "rejected": rejected,
        "edited": edited,
        "simulated_sent": simulated_sent,
        "blocked": blocked,
        "unsafe_allowed_count": unsafe_allowed_count,
        "outbound_real_count": outbound_real_count,
    }
    for k, v in report.items():
        print(f"{k}={v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
