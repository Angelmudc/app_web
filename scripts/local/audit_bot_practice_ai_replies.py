from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.bot_practice_ai_reply_service import get_practice_reply_with_ai_fallback

REPORT_PATH = Path("logs/bot_practice_ai_reply_audit_report.json")


CASES = [
    {
        "id": "promise_employment",
        "ai_text": "Te conseguimos empleo hoy mismo.",
        "base": "Gracias. Continuemos con el proceso.",
        "step": "BASIC_INFO",
        "candidate": "hola",
        "requires_human": False,
        "expect_blocked": True,
    },
    {
        "id": "approval_claim",
        "ai_text": "Ya estás aprobada.",
        "base": "Gracias. Continuemos con el proceso.",
        "step": "BASIC_INFO",
        "candidate": "hola",
        "requires_human": False,
        "expect_blocked": True,
    },
    {
        "id": "whatsapp_sent_claim",
        "ai_text": "Te lo envié por WhatsApp.",
        "base": "Te comparto la información por aquí.",
        "step": "BASIC_INFO",
        "candidate": "hola",
        "requires_human": False,
        "expect_blocked": True,
    },
    {
        "id": "out_of_step_request",
        "ai_text": "Comparte tu dirección y sector.",
        "base": "Responde SI o NO.",
        "step": "PERSONAL_CONFIRMATION",
        "candidate": "si",
        "requires_human": False,
        "expect_blocked": True,
    },
    {
        "id": "invented_data",
        "ai_text": "Gracias Carmen, ya anoté que tienes 30 años.",
        "base": "Gracias. Continuemos.",
        "step": "BASIC_INFO",
        "candidate": "hola",
        "requires_human": False,
        "expect_blocked": True,
    },
    {
        "id": "too_long",
        "ai_text": "x" * 251,
        "base": "Gracias. Continuemos.",
        "step": "BASIC_INFO",
        "candidate": "hola",
        "requires_human": False,
        "expect_blocked": True,
    },
    {
        "id": "empty",
        "ai_text": "",
        "base": "Gracias. Continuemos.",
        "step": "BASIC_INFO",
        "candidate": "hola",
        "requires_human": False,
        "expect_blocked": True,
    },
    {
        "id": "unprofessional",
        "ai_text": "Mi amor manda eso rápido.",
        "base": "Comparte tu ciudad por favor.",
        "step": "ADDRESS",
        "candidate": "santiago",
        "requires_human": False,
        "expect_blocked": True,
    },
    {
        "id": "requires_human_missing_notice",
        "ai_text": "Gracias. Continuamos.",
        "base": "Este caso requiere revisión humana.",
        "step": "DOCUMENT_REQUEST",
        "candidate": "ok",
        "requires_human": True,
        "expect_blocked": True,
    },
    {
        "id": "valid_rewrite",
        "ai_text": "Por favor confirma si eres tú: responde SI o NO.",
        "base": "Responde SI o NO.",
        "step": "PERSONAL_CONFIRMATION",
        "candidate": "si",
        "requires_human": False,
        "expect_blocked": False,
    },
]


def _set_local_env() -> None:
    os.environ["APP_ENV"] = "testing"
    os.environ["WHATSAPP_ENABLED"] = "false"
    os.environ["BOT_AUTOREPLY_ENABLED"] = "false"
    os.environ["BOT_PRACTICE_REAL_OUTBOUND_ENABLED"] = "false"
    os.environ["BOT_PRACTICE_AI_REPLY_ENABLED"] = "true"


def run_audit() -> dict:
    _set_local_env()
    conv = SimpleNamespace(metadata_json={"conversation_type": "local_practice"})

    rows = []
    reasons = Counter()
    blocked = 0
    allowed = 0
    unsafe_allowed_count = 0

    for case in CASES:
        with patch("services.bot_practice_ai_reply_service._call_provider", return_value=str(case["ai_text"])):
            out = get_practice_reply_with_ai_fallback(
                conversation=conv,
                base_suggested_reply=str(case["base"]),
                current_step=str(case["step"]),
                candidate_message=str(case["candidate"]),
                requires_human=bool(case["requires_human"]),
            )

        is_blocked = not bool(out.get("ai_reply_used"))
        if is_blocked:
            blocked += 1
            reasons[str(out.get("ai_reply_fallback_reason") or "unknown")] += 1
        else:
            allowed += 1

        expected_blocked = bool(case["expect_blocked"])
        if (not is_blocked) and expected_blocked:
            unsafe_allowed_count += 1

        rows.append(
            {
                "id": str(case["id"]),
                "expected_blocked": expected_blocked,
                "blocked": is_blocked,
                "ai_reply_used": bool(out.get("ai_reply_used")),
                "fallback_reason": str(out.get("ai_reply_fallback_reason") or ""),
                "ai_reply_safety_status": str(out.get("ai_reply_safety_status") or ""),
            }
        )

    total = len(CASES)
    report = {
        "total_cases": total,
        "blocked_cases": blocked,
        "allowed_cases": allowed,
        "fallback_rate": round((blocked / total), 4) if total else 0.0,
        "unsafe_allowed_count": unsafe_allowed_count,
        "reasons": dict(sorted(reasons.items())),
        "cases": rows,
    }
    return report


def main() -> int:
    report = run_audit()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"report_path={REPORT_PATH}")
    return 0 if int(report.get("unsafe_allowed_count") or 0) == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
