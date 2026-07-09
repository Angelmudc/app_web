#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL_TEST"] = "sqlite:////private/tmp/bot_local_simulator.db"
os.environ["WHATSAPP_ENABLED"] = "false"
os.environ["BOT_DRY_RUN"] = "true"
os.environ["BOT_AUTOREPLY_ENABLED"] = "false"
os.environ["BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL"] = "false"
os.environ["BOT_PROTOCOL_AUTO_ADVANCE_ENABLED"] = "false"

from app import app as flask_app


def _extract_csrf(html: str) -> str:
    m_meta = re.search(r'<meta name="csrf-token"\s+content="([^"]+)"', html)
    if m_meta:
        return m_meta.group(1)
    m_input = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    if not m_input:
        return ""
    return m_input.group(1)


def _print_turn(idx: int, user_text: str, payload: dict) -> None:
    print(f"\n--- Turno {idx} ---")
    print(f"user: {user_text}")
    print(f"current_step: {payload.get('current_step')}")
    print(f"suggested_reply: {payload.get('suggested_reply')}")
    print("debug_protocol_state:", json.dumps(payload.get("debug_protocol_state") or {}, ensure_ascii=False))
    print("entities:", json.dumps(payload.get("protocol_entities") or {}, ensure_ascii=False))
    print("future_entities:", json.dumps(payload.get("protocol_future_entities") or {}, ensure_ascii=False))
    print(f"requires_human: {bool(payload.get('requires_human'))}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Reproduce un caso de práctica local del bot sin UI.")
    parser.add_argument("--messages", nargs="+", required=True, help="Mensajes en orden.")
    parser.add_argument("--expect-final-step", default="", help="Paso final esperado (opcional).")
    args = parser.parse_args()

    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    login = client.post("/admin/login", data={"usuario": "Owner", "clave": "admin123"}, follow_redirects=False)
    if login.status_code not in (302, 303):
        print(f"FAIL login status={login.status_code}")
        return 1

    create = client.post("/admin/bot/practica", data={}, follow_redirects=False)
    if create.status_code not in (302, 303):
        print(f"FAIL create practice status={create.status_code}")
        return 1
    location = create.headers.get("Location") or ""
    conv_id = int(location.rstrip("/").split("/")[-1])
    print(f"conversation_id={conv_id}")

    last_payload: dict = {}
    for idx, msg in enumerate(args.messages, start=1):
        sent = client.post(f"/admin/bot/practica/{conv_id}/mensaje", json={"text": msg}, follow_redirects=False)
        if sent.status_code != 200:
            print(f"FAIL send status={sent.status_code} msg={msg}")
            return 1
        payload = sent.get_json() or {}
        last_payload = payload
        _print_turn(idx, msg, payload)

    debug_resp = client.get(f"/admin/bot/practica/{conv_id}/debug.json", follow_redirects=False)
    print("\n--- debug.json ---")
    print(debug_resp.get_data(as_text=True))

    if args.expect_final_step:
        expected = str(args.expect_final_step).strip().upper()
        got = str(last_payload.get("current_step") or "").strip().upper()
        ok = expected == got
        print(f"\nEXPECT final_step={expected} got={got} => {'PASS' if ok else 'FAIL'}")
        return 0 if ok else 2

    print("\nPASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
