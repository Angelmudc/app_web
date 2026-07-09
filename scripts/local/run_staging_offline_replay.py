#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

tmp_db = Path(tempfile.gettempdir()) / "app_web_staging_offline_replay.sqlite"
if tmp_db.exists():
    tmp_db.unlink()
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL_TEST", f"sqlite:///{tmp_db}")
os.environ.setdefault("BOT_STAGING_MODE", "true")
os.environ.setdefault("BOT_SANDBOX_MODE", "true")
os.environ.setdefault("WHATSAPP_ENABLED", "false")
os.environ.setdefault("BOT_DRY_RUN", "true")
os.environ.setdefault("BOT_SANDBOX_FAIL_RATE", "0.15")
os.environ.setdefault("BOT_SANDBOX_TIMEOUT_RATE", "0.10")

from app import app
from config_app import db
from models import BotContactIdentity, BotConversation, BotDecisionLog, BotEscalation, BotMessage, BotSandboxOutbound, BotSetting
from services.bot_message_service import create_manual_message
from services.bot_sandbox_service import enqueue_sandbox_outbound, run_sandbox_worker_once


def main() -> int:
    with app.app_context():
        BotEscalation.__table__.drop(bind=db.engine, checkfirst=True)
        BotSandboxOutbound.__table__.drop(bind=db.engine, checkfirst=True)
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

        conv = BotConversation(channel="whatsapp", phone_e164="+19991234567", contact_name="Replay", status="open", metadata_json={"sandbox_conversation": True})
        db.session.add(conv)
        db.session.commit()

        texts = [
            "hola", "quiero trabajar", "ruido ???", "mensaje largo " * 20,
            "correccion: mi cedula estaba mal", "ok", "delay extremo", "fallback ia off", "requires_human",
        ]
        for txt in texts:
            msg = create_manual_message(conversation=conv, text_body=txt)
            enqueue_sandbox_outbound(conversation=conv, message=msg)
        db.session.commit()

        for _ in range(8):
            run_sandbox_worker_once(batch_size=50)

        total = BotSandboxOutbound.query.count()
        blocked = BotSandboxOutbound.query.filter_by(state="blocked").count()
        failed = BotSandboxOutbound.query.filter_by(state="failed").count()
        sent = BotSandboxOutbound.query.filter_by(state="simulated_sent").count()
        print("STAGING_REPLAY_SANDBOX")
        print(f"db={tmp_db}")
        print(f"total={total} sent={sent} failed={failed} blocked={blocked}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
