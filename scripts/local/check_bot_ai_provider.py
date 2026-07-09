#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.bot_ai_service import ai_config, classify_intent


def main() -> int:
    cfg = ai_config()
    provider = cfg["provider"]
    model = cfg["model"]
    enabled = (os.getenv("BOT_AI_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on"}

    print(f"provider: {provider}")
    print(f"model: {model}")

    if not enabled:
        print("ok: false")
        print("error_code: ai_disabled")
        print("error_type: runtime_guard")
        return 2

    result = classify_intent("¿Cuál es su horario de atención?", context={"history": []})
    print(f"ok: {'true' if bool(result.get('ok')) else 'false'}")
    if bool(result.get("ok")):
        parsed_json = all(k in result for k in {"intent", "answer_text", "confidence", "requires_human"})
        print(f"parsed_json: {'true' if parsed_json else 'false'}")
        print(f"intent: {str(result.get('intent') or '')}")
        print(f"requires_human: {'true' if bool(result.get('requires_human')) else 'false'}")
        print(f"confidence: {float(result.get('confidence') or 0.0):.2f}")
        return 0

    print(f"error_code: {str(result.get('error_code') or 'ai_error')}")
    print(f"error_type: {str(result.get('error_type') or 'unknown_error')}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
