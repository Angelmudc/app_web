from __future__ import annotations

import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

tmp_db = Path(tempfile.gettempdir()) / "app_web_replay_sandbox_manual_review_aggressive.sqlite"
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
from services.bot_sandbox_review_service import ReviewTransitionError, approve_review, reject_review
from services.bot_sandbox_service import run_sandbox_worker_once


def _reset_db() -> None:
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


def main() -> int:
    metrics = {
        "reviews_created": 0,
        "duplicates_blocked": 0,
        "invalid_transitions_blocked": 0,
        "unsafe_edits_blocked": 0,
        "outbox_duplicates": 0,
        "simulated_sent": 0,
        "audit_events": 0,
        "outbound_real_count": 0,
    }
    with app.app_context():
        _reset_db()
        client = app.test_client()

        for i in range(20):
            mid = f"aggr-wa-{i:03d}"
            payload = {
                "from": f"+199900010{i:02d}",
                "name": f"Candidata Agg {i}",
                "message": f"hola {i}",
                "message_id": mid,
                "timestamp": "2026-05-12T10:00:00Z",
            }
            r1 = client.post("/admin/bot/sandbox/webhook/inbound", json=payload)
            if r1.status_code == 200:
                metrics["reviews_created"] += 1
            r2 = client.post("/admin/bot/sandbox/webhook/inbound", json=payload)
            if (r2.get_json() or {}).get("idempotent"):
                metrics["duplicates_blocked"] += 1

        review_ids = [r.id for r in BotSandboxReviewQueue.query.order_by(BotSandboxReviewQueue.id.asc()).all()]

        unsafe_text = "Ya estas aprobada y empleo seguro hoy"
        for idx, rid in enumerate(review_ids):
            row = BotSandboxReviewQueue.query.get(rid)
            if idx % 5 == 0:
                try:
                    approve_review(review=row, reviewer_id=1, edited_text=unsafe_text)
                    db.session.commit()
                    if row.status == "blocked":
                        metrics["unsafe_edits_blocked"] += 1
                except ReviewTransitionError:
                    db.session.commit()
                    metrics["invalid_transitions_blocked"] += 1
            elif idx % 5 == 1:
                try:
                    safe_text = str(row.final_suggested_reply or "Gracias por escribir.").strip()
                    approve_review(review=row, reviewer_id=1, edited_text=safe_text)
                    db.session.commit()
                except ReviewTransitionError:
                    db.session.commit()
                    metrics["invalid_transitions_blocked"] += 1
            elif idx % 5 == 2:
                try:
                    approve_review(review=row, reviewer_id=1, edited_text=None)
                    db.session.commit()
                    review_refetched = BotSandboxReviewQueue.query.get(rid)
                    approve_review(review=review_refetched, reviewer_id=2, edited_text=None)
                    db.session.commit()
                except ReviewTransitionError:
                    db.session.commit()
                    metrics["invalid_transitions_blocked"] += 1
            elif idx % 5 == 3:
                try:
                    reject_review(review=row, reviewer_id=3, reason="manual")
                    db.session.commit()
                    review_refetched = BotSandboxReviewQueue.query.get(rid)
                    approve_review(review=review_refetched, reviewer_id=4, edited_text=None)
                    db.session.commit()
                except ReviewTransitionError:
                    db.session.commit()
                    metrics["invalid_transitions_blocked"] += 1
            else:
                db.session.commit()

        race_targets = review_ids[:6]

        def _race_approve(rid: int) -> str:
            with app.app_context():
                review = BotSandboxReviewQueue.query.get(rid)
                try:
                    approve_review(review=review, reviewer_id=7, edited_text=None)
                    db.session.commit()
                    return "ok"
                except ReviewTransitionError:
                    db.session.commit()
                    return "blocked"

        def _race_reject(rid: int) -> str:
            with app.app_context():
                review = BotSandboxReviewQueue.query.get(rid)
                try:
                    reject_review(review=review, reviewer_id=8, reason="race")
                    db.session.commit()
                    return "ok"
                except ReviewTransitionError:
                    db.session.commit()
                    return "blocked"

        with ThreadPoolExecutor(max_workers=6) as ex:
            futures = []
            for rid in race_targets[:3]:
                futures.append(ex.submit(_race_approve, rid))
                futures.append(ex.submit(_race_approve, rid))
            for rid in race_targets[3:6]:
                futures.append(ex.submit(_race_approve, rid))
                futures.append(ex.submit(_race_reject, rid))
            for f in futures:
                if f.result() == "blocked":
                    metrics["invalid_transitions_blocked"] += 1

        run_sandbox_worker_once(batch_size=500)

        reviews = BotSandboxReviewQueue.query.all()
        metrics["simulated_sent"] = BotSandboxReviewQueue.query.filter_by(status="simulated_sent").count()
        metrics["outbound_real_count"] = BotSandboxOutbound.query.filter(BotSandboxOutbound.provider != "fake").count()
        metrics["outbox_duplicates"] = max(0, BotSandboxOutbound.query.count() - len({r.outbound_message_id for r in reviews if r.outbound_message_id}))
        metrics["audit_events"] = sum(len((dict(r.metadata_json or {})).get("review_events") or []) for r in reviews)

    for key, value in metrics.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
