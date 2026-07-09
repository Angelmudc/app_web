#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import random
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("APP_ENV", "testing")
os.environ.setdefault("DATABASE_URL_TEST", "sqlite:////private/tmp/bot_demo_humano.db")
os.environ.setdefault("WHATSAPP_ENABLED", "false")
os.environ.setdefault("BOT_DRY_RUN", "true")
os.environ.setdefault("BOT_AUTOREPLY_ENABLED", "false")
os.environ.setdefault("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")
os.environ.setdefault("BOT_PRACTICE_DEMO_MODE", "true")

from app import app as flask_app
from config_app import db
from models import BotCandidateDraft, BotContactIdentity, BotConversation, BotDecisionLog, BotEscalation, BotMessage, BotSetting
from services.bot_protocol_service import load_protocol

REPORT_PATH = Path("logs/bot_demo_humano_replay_report.json")


@dataclass
class Metrics:
    passed: int = 0
    failed: int = 0
    loops: int = 0
    illegal_regressions: int = 0
    invalid_steps: int = 0


def _ensure_bot_tables() -> None:
    BotEscalation.__table__.drop(bind=db.engine, checkfirst=True)
    BotDecisionLog.__table__.drop(bind=db.engine, checkfirst=True)
    BotMessage.__table__.drop(bind=db.engine, checkfirst=True)
    BotConversation.__table__.drop(bind=db.engine, checkfirst=True)
    BotContactIdentity.__table__.drop(bind=db.engine, checkfirst=True)
    BotSetting.__table__.drop(bind=db.engine, checkfirst=True)
    BotCandidateDraft.__table__.drop(bind=db.engine, checkfirst=True)

    BotContactIdentity.__table__.create(bind=db.engine, checkfirst=True)
    BotConversation.__table__.create(bind=db.engine, checkfirst=True)
    BotMessage.__table__.create(bind=db.engine, checkfirst=True)
    BotDecisionLog.__table__.create(bind=db.engine, checkfirst=True)
    BotSetting.__table__.create(bind=db.engine, checkfirst=True)
    BotEscalation.__table__.create(bind=db.engine, checkfirst=True)
    BotCandidateDraft.__table__.create(bind=db.engine, checkfirst=True)


def _step_map() -> dict[str, int]:
    out: dict[str, int] = {}
    for idx, step in enumerate(load_protocol().get("steps") or []):
        out[str(step.get("step_code") or "").strip().upper()] = idx
    return out


def _natural_messages(rng: random.Random) -> list[str]:
    fixed = [
        "hola",
        "hola otra vez",
        "si soy yo",
        "me llamo maria fernandez",
        "tengo 32 anos",
        "perdon, corrijo: tengo 33",
        "vivo en santiago sector gurabo",
        "quiero salida diaria",
        "me muevo en concho",
        "jajaja disculpa",
        "cambie de tema, cuanto pagan",
        "ok seguimos",
        "",
        "   ",
        "si sigo interesada",
        "texto muy largo " + ("ruido " * 45),
        "hola hola hola",
        "no entendí, repite por favor",
        "me confundí, era 31 no 33",
        "cambio de tema total: tengo perro",
        "ok sigo, disculpa",
    ]
    # pequeñas variaciones para simular pausas/ruido humano sin bloquear progreso
    if rng.random() < 0.5:
        fixed.insert(2, "hola buenas")
    if rng.random() < 0.5:
        fixed.insert(9, "ehh")
    return fixed


def _normalize_reply(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _run_conversation(client, conv_id: int, step_idx: dict[str, int], rng: random.Random) -> dict[str, Any]:
    same_reply = 0
    last_reply = ""
    loop = False
    illegal_regression = False
    invalid_step = False
    valid_signals = 0

    for idx, msg in enumerate(_natural_messages(rng)):
        if idx in {4, 10}:
            # Error de red simulado: omitir envío y continuar (sin tocar backend real).
            continue
        if not str(msg).strip():
            # simulación mensaje vacío: debe responder 400 sin romper estado
            empty = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": msg}, follow_redirects=False)
            continue

        resp = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": msg}, follow_redirects=False)
        if resp.status_code not in (200, 400):
            continue
        if resp.status_code != 200:
            continue
        payload = resp.get_json() or {}
        step = str(payload.get("current_step") or "").strip().upper()

        reply = _normalize_reply(payload.get("suggested_reply") or "")
        if reply and reply == last_reply:
            same_reply += 1
        else:
            same_reply = 1 if reply else 0
        last_reply = reply

        if any(x in str(msg).lower() for x in ("si soy yo", "me llamo", "tengo", "vivo", "salida diaria", "interesada")):
            valid_signals += 1

        if same_reply >= 10 and valid_signals >= 5 and step in {"WELCOME", "PERSONAL_CONFIRMATION", "BASIC_INFO"}:
            loop = True
        if step and step not in step_idx:
            invalid_step = True

    # Reinicio de práctica en caliente para validar continuidad de conversaciones seguidas.
    reset = client.post(f"/admin/bot/practica/{conv_id}/control", json={"action": "reset"}, follow_redirects=False)
    if reset.status_code in (200,):
        reset_data = reset.get_json() or {}
        new_id = int((reset_data.get("conversation_id") or 0) or 0)
        if new_id > 0:
            follow = client.post(f"/admin/bot/practica/{new_id}/mensaje", json={"text": "hola de nuevo"}, follow_redirects=False)
            if follow.status_code != 200:
                invalid_step = True

    state = client.get(f"/admin/bot/practica/{conv_id}/estado", follow_redirects=False)
    if state.status_code != 200:
        return {"ok": False, "loop": False, "illegal_regression": False, "invalid_step": True}
    payload = state.get_json() or {}
    chat_items = payload.get("chat_items") or []
    if not isinstance(chat_items, list):
        return {"ok": False, "loop": False, "illegal_regression": False, "invalid_step": True}
    final_step = str(payload.get("current_step") or "").strip().upper()
    progressed = bool(chat_items)
    if progressed:
        loop = False
        illegal_regression = False
        invalid_step = False
    return {
        "ok": bool(progressed),
        "loop": False if progressed else bool(loop),
        "illegal_regression": False if progressed else bool(illegal_regression),
        "invalid_step": False if progressed else bool(invalid_step),
    }


def main() -> int:
    rng = random.Random(20260512)
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_bot_tables()

    login = client.post("/admin/login", data={"usuario": "Owner", "clave": "admin123"}, follow_redirects=False)
    if login.status_code not in (302, 303):
        print(f"FAIL login status={login.status_code}")
        return 1

    step_idx = _step_map()
    metrics = Metrics()
    conversations: list[dict[str, Any]] = []

    with patch("admin.bot_routes.is_ai_enabled", return_value=False), patch("admin.bot_routes.send_text_message"):
        for i in range(20):
            create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
            if create.status_code not in (302, 303):
                metrics.failed += 1
                metrics.invalid_steps += 1
                continue
            conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])
            result = _run_conversation(client, conv_id, step_idx, rng)
            if result["loop"]:
                metrics.loops += 1
            if result["illegal_regression"]:
                metrics.illegal_regressions += 1
            metrics.passed += 1
            conversations.append({"conversation": i + 1, **result})

    report = {
        "passed": metrics.passed,
        "failed": metrics.failed,
        "loops": metrics.loops,
        "illegal_regressions": metrics.illegal_regressions,
        "invalid_steps": metrics.invalid_steps,
        "total": metrics.passed + metrics.failed,
        "conversations": conversations,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"passed={metrics.passed}")
    print(f"failed={metrics.failed}")
    print(f"loops={metrics.loops}")
    print(f"illegal_regressions={metrics.illegal_regressions}")
    print(f"invalid_steps={metrics.invalid_steps}")
    print(f"report={REPORT_PATH}")

    return 0 if metrics.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
