"""Servicios base para decisiones del bot (Fase 1)."""

from __future__ import annotations

from config_app import db
from models import BotConversation, BotDecisionLog, BotMessage
from services.bot_constants import DECISION_RESULTS, DECISION_TYPES


def register_decision(
    *,
    conversation: BotConversation,
    decision_type: str,
    decision_result: str,
    rule_code: str,
    reason_human: str,
    message: BotMessage | None = None,
    facts_json: dict | None = None,
    ai_used: bool = False,
    ai_model: str | None = None,
    ai_prompt_version: str | None = None,
    autocommit: bool = True,
) -> BotDecisionLog:
    normalized_type = (decision_type or "").strip().lower()
    normalized_result = (decision_result or "").strip().lower()
    if normalized_type not in DECISION_TYPES:
        raise ValueError(f"decision_type inválido: {decision_type}")
    if normalized_result not in DECISION_RESULTS:
        raise ValueError(f"decision_result inválido: {decision_result}")

    decision = BotDecisionLog(
        conversation_id=conversation.id,
        message_id=message.id if message else None,
        decision_type=normalized_type,
        decision_result=normalized_result,
        rule_code=(rule_code or "").strip() or "UNSPECIFIED_RULE",
        reason_human=(reason_human or "").strip() or "Sin detalle",
        facts_json=facts_json or {},
        ai_used=bool(ai_used),
        ai_model=(ai_model or "").strip() or None,
        ai_prompt_version=(ai_prompt_version or "").strip() or None,
    )
    db.session.add(decision)
    if autocommit:
        db.session.commit()
    else:
        db.session.flush()
    return decision
