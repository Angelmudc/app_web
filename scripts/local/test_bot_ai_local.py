#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app
from config_app import db
from models import BotConversation, BotDecisionLog, BotMessage
from sqlalchemy.exc import OperationalError
from services.bot_ai_service import classify_intent, generate_safe_reply
from services.bot_constants import (
    CONVERSATION_STATUS_PENDING_HUMAN,
    DECISION_RESULT_ALLOW,
    DECISION_RESULT_ESCALATE,
    DECISION_RESULT_MANUAL_ONLY,
    DECISION_TYPE_AI_CLASSIFICATION,
    DECISION_TYPE_AUTO_REPLY,
    MESSAGE_DIRECTION_INBOUND,
    MESSAGE_SOURCE_WHATSAPP_USER,
    MESSAGE_STATUS_INBOUND_STORED,
)
from services.bot_conversation_service import get_or_create_manual_conversation
from services.bot_decision_service import register_decision
from services.bot_identity_service import get_or_create_identity


DEFAULT_PHONE_E164 = "+18090000000"
DEFAULT_TEXT = "Hola, ¿cuáles son los requisitos para contratar una doméstica?"


class LocalBotAISafetyError(RuntimeError):
    pass


def _is_true(value: str | None, *, default: bool = False) -> bool:
    raw = (value or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _require_safe_flags(mode: str) -> None:
    if _is_true(os.getenv("BOT_AUTOREPLY_ENABLED"), default=False):
        raise LocalBotAISafetyError("Bloqueado: BOT_AUTOREPLY_ENABLED debe ser false para esta prueba local controlada.")
    if _is_true(os.getenv("WHATSAPP_ENABLED"), default=False):
        raise LocalBotAISafetyError("Bloqueado: WHATSAPP_ENABLED debe ser false para evitar envio real.")
    if not _is_true(os.getenv("BOT_DRY_RUN"), default=True):
        raise LocalBotAISafetyError("Bloqueado: BOT_DRY_RUN debe ser true para mantener modo seguro.")

    if mode == "real":
        if not _is_true(os.getenv("BOT_AI_ENABLED"), default=False):
            raise LocalBotAISafetyError("Bloqueado: BOT_AI_ENABLED=true es requerido para modo real.")
        if not (os.getenv("BOT_AI_API_KEY") or "").strip():
            raise LocalBotAISafetyError("Bloqueado: BOT_AI_API_KEY no esta configurada para modo real.")


def _mock_ai_result() -> dict[str, Any]:
    return {
        "ok": True,
        "intent": "FAQ_REQUISITOS",
        "answer_text": "Podemos orientarte con los requisitos generales del proceso. Un asesor humano te confirma los detalles de tu caso.",
        "confidence": 0.94,
        "requires_human": False,
        "escalation_reason": "AI_SAFE",
        "prompt_version": "phase4_v1_mock",
        "ai_model": "mock-local",
    }


def run_local_ai_suggestion_test(*, mode: str, phone_e164: str, inbound_text: str) -> dict[str, Any]:
    selected_mode = (mode or "").strip().lower()
    if selected_mode not in {"mock", "real"}:
        raise ValueError("mode invalido; use 'mock' o 'real'")

    _require_safe_flags(selected_mode)

    conversation = get_or_create_manual_conversation(phone_e164=phone_e164, contact_name="Prueba IA Local", autocommit=False)
    identity, _resolved = get_or_create_identity(phone_e164)
    conversation.identity_id = identity.id

    inbound = BotMessage(
        conversation_id=conversation.id,
        direction=MESSAGE_DIRECTION_INBOUND,
        source=MESSAGE_SOURCE_WHATSAPP_USER,
        message_type="text",
        wa_message_id=f"local-ai-{selected_mode}-{uuid.uuid4().hex[:16]}",
        text_body=inbound_text,
        status=MESSAGE_STATUS_INBOUND_STORED,
        raw_payload_json={"source": "local_test_script", "mode": selected_mode},
    )
    db.session.add(inbound)
    db.session.flush()

    if selected_mode == "mock":
        ai_result = _mock_ai_result()
    else:
        ai_result = classify_intent(
            inbound_text,
            context={
                "identity_role": str(identity.identity_status or "unknown"),
                "history": [{"role": "user", "text": inbound_text}],
            },
        )

    ai_intent = str(ai_result.get("intent") or "UNKNOWN").strip().upper()
    ai_confidence = float(ai_result.get("confidence") or 0)
    requires_human = bool(ai_result.get("requires_human", True))
    ai_answer_text = (ai_result.get("answer_text") or "").strip() or generate_safe_reply(ai_intent)
    ai_rule_code = str(ai_result.get("escalation_reason") or ai_result.get("error_code") or "AI_UNKNOWN").strip() or "AI_UNKNOWN"

    ai_facts = {
        "intent": ai_intent,
        "confidence": ai_confidence,
        "requires_human": requires_human,
        "suggested_reply": ai_answer_text,
        "local_test_mode": selected_mode,
    }

    decision = register_decision(
        conversation=conversation,
        decision_type=DECISION_TYPE_AI_CLASSIFICATION,
        decision_result=DECISION_RESULT_ESCALATE if requires_human else DECISION_RESULT_ALLOW,
        rule_code=ai_rule_code,
        reason_human="Prueba local controlada IA en modo sugerencia",
        message=inbound,
        facts_json=ai_facts,
        ai_used=True,
        ai_model=str(ai_result.get("ai_model") or ""),
        ai_prompt_version=str(ai_result.get("prompt_version") or ""),
        autocommit=False,
    )

    register_decision(
        conversation=conversation,
        decision_type=DECISION_TYPE_AUTO_REPLY,
        decision_result=DECISION_RESULT_MANUAL_ONLY,
        rule_code="AUTOREPLY_DISABLED_LOCAL_TEST",
        reason_human="Prueba local en sugerencia: sin autorespuesta ni envio WhatsApp",
        message=inbound,
        facts_json=ai_facts,
        ai_used=True,
        ai_model=str(ai_result.get("ai_model") or ""),
        ai_prompt_version=str(ai_result.get("prompt_version") or ""),
        autocommit=False,
    )

    if requires_human:
        conversation.status = CONVERSATION_STATUS_PENDING_HUMAN

    db.session.commit()

    result = {
        "conversation_id": int(conversation.id),
        "message_id": int(inbound.id),
        "intent": ai_intent,
        "confidence": ai_confidence,
        "answer_text": ai_answer_text,
        "requires_human": requires_human,
        "decision_log_id": int(decision.id),
        "mode": selected_mode,
        "whatsapp_sent": False,
    }
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prueba local controlada IA (solo sugerencia; sin WhatsApp real).")
    parser.add_argument("--mode", choices=["mock", "real"], default="mock")
    parser.add_argument("--phone", default=DEFAULT_PHONE_E164)
    parser.add_argument("--text", default=DEFAULT_TEXT)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    with app.app_context():
        try:
            result = run_local_ai_suggestion_test(mode=args.mode, phone_e164=args.phone, inbound_text=args.text)
        except LocalBotAISafetyError as exc:
            print(f"ERROR: {exc}")
            return 2
        except OperationalError:
            print("ERROR: base de datos no lista para prueba local. Ejecuta migraciones primero.")
            return 3
        except Exception as exc:
            print(f"ERROR: fallo inesperado ({type(exc).__name__}).")
            return 1

    print(f"conversation_id: {result['conversation_id']}")
    print(f"message_id: {result['message_id']}")
    print(f"intent: {result['intent']}")
    print(f"confidence: {result['confidence']:.2f}")
    print(f"answer_text: {result['answer_text']}")
    print(f"requires_human: {str(result['requires_human']).lower()}")
    print(f"decision_log_id: {result['decision_log_id']}")
    print(f"mode: {result['mode']}")
    print("whatsapp_sent: false")
    print(f"admin_url: /admin/bot/conversaciones/{result['conversation_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
