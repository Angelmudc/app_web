from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

tmp_db = Path(tempfile.gettempdir()) / "app_web_replay_sandbox_assisted_admin.sqlite"
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

from app import app
from config_app import db
from models import BotContactIdentity, BotConversation, BotDecisionLog, BotEscalation, BotMessage, BotSandboxOutbound, BotSandboxReviewQueue, BotSetting
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


def main() -> int:
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    _reset()
    metrics = {
        "pending_before": 0,
        "approved": 0,
        "edited": 0,
        "rejected": 0,
        "blocked": 0,
        "simulated_sent": 0,
        "unsafe_blocked": 0,
        "outbound_real_count": 0,
        "whatsapp_real_count": 0,
    }

    with app.app_context():
        client = app.test_client()

        login = client.post("/admin/login", data={"usuario": "Owner", "clave": "admin123"}, follow_redirects=False)
        if login.status_code not in (302, 303):
            raise RuntimeError("login_failed")

        for i in range(6):
            client.post(
                "/admin/bot/sandbox/webhook/inbound",
                json={
                    "from": f"+1999000990{i}",
                    "name": f"Assist Replay {i}",
                    "message": f"hola replay {i}",
                    "message_id": f"assist-replay-{i}",
                    "timestamp": "2026-05-14T10:00:00Z",
                },
            )

        pending = client.get("/admin/bot/sandbox/asistente/pending.json", follow_redirects=True).get_json() or {}
        items = list(pending.get("items") or [])
        metrics["pending_before"] = len(items)
        if len(items) < 5:
            raise RuntimeError("insufficient_reviews")

        rid1 = int(items[0]["id"])
        rid2 = int(items[1]["id"])
        rid3 = int(items[2]["id"])
        rid4 = int(items[3]["id"])
        rid5 = int(items[4]["id"])

        r = client.post(f"/admin/bot/sandbox/asistente/review/{rid1}/approve", json={})
        if r.status_code == 200:
            metrics["approved"] += 1

        r = client.post(
            f"/admin/bot/sandbox/asistente/review/{rid2}/edit-approve",
            json={"edited_text": "Gracias por escribir. Seguimos con la siguiente etapa."},
        )
        if r.status_code == 200:
            metrics["edited"] += 1

        r = client.post(
            f"/admin/bot/sandbox/asistente/review/{rid3}/edit-approve",
            json={"edited_text": "Ya estas aprobada y empleo seguro hoy"},
        )
        if r.status_code in (200, 409):
            review = BotSandboxReviewQueue.query.get(rid3)
            if review and review.status == "blocked":
                metrics["unsafe_blocked"] += 1

        r = client.post(f"/admin/bot/sandbox/asistente/review/{rid4}/reject", json={"reason": "manual_reject"})
        if r.status_code == 200:
            metrics["rejected"] += 1

        r = client.post(f"/admin/bot/sandbox/asistente/review/{rid5}/block", json={"reason": "manual_block"})
        if r.status_code == 200:
            metrics["blocked"] += 1

        client.post("/admin/bot/sandbox/asistente/worker/run", json={"batch_size": 50})
        run_sandbox_worker_once(batch_size=50)

        metrics["simulated_sent"] = BotSandboxReviewQueue.query.filter_by(status="simulated_sent").count()
        metrics["outbound_real_count"] = BotSandboxOutbound.query.filter(BotSandboxOutbound.provider != "fake").count()
        metrics["whatsapp_real_count"] = (
            BotMessage.query.filter(BotMessage.direction == "outbound")
            .filter(BotMessage.source.notin_(["admin_manual", "sandbox_review"]))
            .count()
        )

    for k, v in metrics.items():
        print(f"{k}={v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
