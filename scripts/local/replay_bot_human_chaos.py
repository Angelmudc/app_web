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
os.environ.setdefault("DATABASE_URL_TEST", "sqlite:////private/tmp/bot_human_chaos.db")
os.environ.setdefault("WHATSAPP_ENABLED", "false")
os.environ.setdefault("BOT_DRY_RUN", "true")
os.environ.setdefault("BOT_AUTOREPLY_ENABLED", "false")
os.environ.setdefault("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false")

from app import app as flask_app
from config_app import db
from models import BotCandidateDraft, BotContactIdentity, BotConversation, BotDecisionLog, BotEscalation, BotMessage, BotSetting
from services.bot_protocol_service import is_greeting_only, is_positive_confirmation, load_protocol

REPORT_PATH = Path("logs/bot_human_chaos_report.json")


@dataclass
class ReplayMetrics:
    passed: int = 0
    failed: int = 0
    loops_detected: int = 0
    bad_loops: int = 0
    repeated_due_to_noise: int = 0
    stuck_after_valid_signal: int = 0
    retrocesos_detectados: int = 0
    steps_invalidos: int = 0
    conversations_completed: int = 0
    stuck_conversations: int = 0
    total_turns: int = 0


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


def _is_explicit_correction(text: str) -> bool:
    return bool(re.search(r"\b(no\b.*\b(mejor|vivo|tengo|era|quise)|corrijo|quise decir)\b", str(text).lower()))


def _generate_message(rng: random.Random) -> str:
    greetings = ["hola", "klk", "hello", "buenas", "hola otra vez", "hola 😂"]
    confirms = ["si soy yo", "soy yo", "yes", "dale", "ok"]
    names = ["me llamo carmen", "soy ana", "me yamo jose", "yo soy juana", "mi nombre e luis"]
    ages = ["tengo 30", "tengo 33 años", "tengo treintai dos", "29", "40 años"]
    cities = ["vivo en santiago", "soy de puerto plata", "gurabo", "los ciruelitos", "villa mella"]
    work = ["salida diaria", "dormida", "salida diaria no mejor dormida", "toy pa salida"]
    noise = ["jeje", "tu sabe", "como tu ta", "ehh", "mmm", "audio largo loco"]
    sens = ["mi cedula es 001-1111111-1", "te mando foto de cédula", "pasaporte"]
    absurd = ["el perro cocina", "ayer fui a marte", "$$$$", "🤯🤯🤯", "q lo q con to"]

    pools = [greetings, confirms, names, ages, cities, work, noise, absurd]
    if rng.random() < 0.15:
        pools.append(sens)

    parts = [rng.choice(rng.choice(pools)) for _ in range(rng.randint(1, 4))]
    return " ".join(parts)


def _has_valid_progress_signal(text: str) -> bool:
    normalized = str(text or "").lower()
    if is_positive_confirmation(normalized, step_code="PERSONAL_CONFIRMATION"):
        return True
    if re.search(r"\b(me llamo|mi nombre es|tengo\s+\d{2}\b|\d{2}\s+anos|edad)\b", normalized):
        return True
    if re.search(r"\b(soy\s+[a-zñ]{3,}\s+[a-zñ]{3,})\b", normalized):
        return True
    return bool(re.search(r"\b(quiero trabajar|quiero registrarme|kiero trabajar|kiero registrarme)\b", normalized))


def _is_noise_message(text: str) -> bool:
    normalized = str(text or "").lower().strip()
    if not normalized:
        return True
    if is_greeting_only(normalized):
        return True
    return bool(re.search(r"\b(jeje|ehh|mmm|tu sabe|audio largo loco)\b", normalized))


def _run_one_conversation(client, conv_id: int, turns: int, step_idx: dict[str, int], rng: random.Random) -> dict[str, Any]:
    prev_rank = -1
    max_rank = -1
    same_reply_streak = 0
    prev_reply = ""
    bad_loop_detected = False
    repeated_due_to_noise = 0
    stuck_after_valid_signal = False
    valid_signal_without_progress = 0
    retroceso = False
    step_invalido = False

    for _ in range(turns):
        msg = _generate_message(rng)
        resp = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": msg}, follow_redirects=False)
        if resp.status_code != 200:
            return {
                "ok": False,
                "loop_detected": loop_detected,
                "retroceso": retroceso,
                "step_invalido": True,
                "final_rank": -1,
            }
        payload = resp.get_json() or {}
        step = str(payload.get("current_step") or "").strip().upper()
        rank = int(step_idx.get(step, -1))
        if rank < 0:
            step_invalido = True

        if prev_rank >= 0 and rank >= 0 and rank < prev_rank:
            has_pending = bool(payload.get("pending_corrections"))
            if not has_pending and not _is_explicit_correction(msg):
                retroceso = True
        prev_max_rank = max_rank
        if rank >= 0:
            prev_rank = rank
            max_rank = max(max_rank, rank)

        reply = re.sub(r"\s+", " ", str(payload.get("suggested_reply") or "").strip().lower())
        if reply and reply == prev_reply:
            same_reply_streak += 1
        else:
            same_reply_streak = 1 if reply else 0
        prev_reply = reply
        valid_signal = _has_valid_progress_signal(msg)
        is_noise = _is_noise_message(msg)
        progressed_now = rank > prev_max_rank
        can_progress_with_signal = step in {"WELCOME", "PERSONAL_CONFIRMATION"}
        if valid_signal and can_progress_with_signal and not progressed_now:
            valid_signal_without_progress += 1
        elif progressed_now or not can_progress_with_signal:
            valid_signal_without_progress = 0
        elif not valid_signal:
            valid_signal_without_progress = 0

        if same_reply_streak >= 4:
            if valid_signal and can_progress_with_signal:
                bad_loop_detected = True
            elif is_noise:
                repeated_due_to_noise += 1
        if valid_signal_without_progress >= 3 and same_reply_streak >= 4:
            stuck_after_valid_signal = True
            bad_loop_detected = True

    final = client.get(f"/admin/bot/practica/{conv_id}/estado", follow_redirects=False)
    final_payload = final.get_json() or {}
    final_step = str(final_payload.get("current_step") or "").strip().upper()
    if not final_step:
        debug = client.get(f"/admin/bot/practica/{conv_id}/debug.json", follow_redirects=False)
        debug_payload = debug.get_json() or {}
        final_step = str(debug_payload.get("current_step") or "").strip().upper()
    final_rank = int(step_idx.get(final_step, -1))
    if final_rank < 0:
        step_invalido = True

    return {
        "ok": not (bad_loop_detected or retroceso or step_invalido),
        "loop_detected": bad_loop_detected,
        "bad_loop_detected": bad_loop_detected,
        "repeated_due_to_noise": repeated_due_to_noise,
        "stuck_after_valid_signal": stuck_after_valid_signal,
        "retroceso": retroceso,
        "step_invalido": step_invalido,
        "final_rank": final_rank,
    }


def main() -> int:
    rng = random.Random(20260511)
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
    work_type_rank = int(step_idx.get("WORK_TYPE", 4))
    basic_info_rank = int(step_idx.get("BASIC_INFO", 2))

    metrics = ReplayMetrics()
    conversations: list[dict[str, Any]] = []

    with patch("admin.bot_routes.is_ai_enabled", return_value=False), patch("admin.bot_routes.send_text_message"):
        for n in range(50):
            create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
            if create.status_code not in (302, 303):
                metrics.failed += 1
                metrics.steps_invalidos += 1
                continue
            conv_id = int((create.headers.get("Location") or "").rstrip("/").split("/")[-1])
            turns = rng.randint(20, 40)
            metrics.total_turns += turns
            result = _run_one_conversation(client, conv_id, turns, step_idx, rng)

            if result["bad_loop_detected"]:
                metrics.bad_loops += 1
                metrics.loops_detected += 1
            metrics.repeated_due_to_noise += int(result["repeated_due_to_noise"])
            if result["stuck_after_valid_signal"]:
                metrics.stuck_after_valid_signal += 1
            if result["retroceso"]:
                metrics.retrocesos_detectados += 1
            if result["step_invalido"]:
                metrics.steps_invalidos += 1

            if result["final_rank"] >= work_type_rank:
                metrics.conversations_completed += 1
            if result["final_rank"] < basic_info_rank:
                metrics.stuck_conversations += 1

            if result["ok"]:
                metrics.passed += 1
            else:
                metrics.failed += 1

            conversations.append({
                "conversation": n + 1,
                "turns": turns,
                "final_rank": result["final_rank"],
                "loop_detected": result["loop_detected"],
                "bad_loop_detected": result["bad_loop_detected"],
                "repeated_due_to_noise": result["repeated_due_to_noise"],
                "stuck_after_valid_signal": result["stuck_after_valid_signal"],
                "retroceso": result["retroceso"],
                "step_invalido": result["step_invalido"],
                "ok": result["ok"],
            })

    average_turns = round((metrics.total_turns / 50.0), 2)
    report = {
        "passed": metrics.passed,
        "failed": metrics.failed,
        "loops_detected": metrics.loops_detected,
        "bad_loops": metrics.bad_loops,
        "repeated_due_to_noise": metrics.repeated_due_to_noise,
        "stuck_after_valid_signal": metrics.stuck_after_valid_signal,
        "retrocesos_detectados": metrics.retrocesos_detectados,
        "steps_invalidos": metrics.steps_invalidos,
        "average_turns": average_turns,
        "conversations_completed": metrics.conversations_completed,
        "stuck_conversations": metrics.stuck_conversations,
        "conversations": conversations,
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"passed: {report['passed']}")
    print(f"failed: {report['failed']}")
    print(f"loops_detected: {report['loops_detected']}")
    print(f"bad_loops: {report['bad_loops']}")
    print(f"repeated_due_to_noise: {report['repeated_due_to_noise']}")
    print(f"stuck_after_valid_signal: {report['stuck_after_valid_signal']}")
    print(f"retrocesos_detectados: {report['retrocesos_detectados']}")
    print(f"steps_invalidos: {report['steps_invalidos']}")
    print(f"average_turns: {report['average_turns']}")
    print(f"conversations_completed: {report['conversations_completed']}")
    print(f"stuck_conversations: {report['stuck_conversations']}")
    print(f"report: {REPORT_PATH}")

    # Fail hard if loop detector caught real loops.
    if report["bad_loops"] > 0:
        return 2
    if report["failed"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
