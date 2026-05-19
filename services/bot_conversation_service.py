"""Servicios base para conversaciones del bot (Fase 1)."""

from __future__ import annotations

from config_app import db
from models import BotConversation
from services.bot_constants import DECISION_RESULT_MANUAL_ONLY, DECISION_TYPE_PROTOCOL_STEP_CHANGE
from services.bot_constants import (
    CONVERSATION_STATUS_BOT_PAUSED,
    CONVERSATION_STATUS_OPEN,
    CONVERSATION_STATUS_RESOLVED,
    CONVERSATION_STATUSES,
)
from services.bot_decision_service import register_decision
from services.bot_protocol_service import load_protocol
from utils.timezone import utc_now_naive

DEFAULT_PROTOCOL_STEP = "WELCOME"
DEFAULT_PROTOCOL_CODE = "domesticas_v1"


def get_conversation_by_id(conversation_id: int) -> BotConversation | None:
    return db.session.get(BotConversation, int(conversation_id))


def get_or_create_manual_conversation(
    *, phone_e164: str, contact_name: str | None = None, autocommit: bool = True
) -> BotConversation:
    normalized_phone = (phone_e164 or "").strip()
    if not normalized_phone:
        raise ValueError("phone_e164 es requerido en formato E.164")

    conversation = (
        BotConversation.query.filter_by(channel="whatsapp", phone_e164=normalized_phone)
        .order_by(BotConversation.created_at.desc(), BotConversation.id.desc())
        .first()
    )
    if conversation:
        _ensure_protocol_state(conversation, autocommit=autocommit)
        return conversation

    conversation = BotConversation(
        channel="whatsapp",
        phone_e164=normalized_phone,
        contact_name=(contact_name or "").strip() or None,
        status=CONVERSATION_STATUS_OPEN,
        bot_paused=False,
    )
    db.session.add(conversation)
    _ensure_protocol_state(conversation, autocommit=False)
    if autocommit:
        db.session.commit()
    else:
        db.session.flush()
    return conversation


def get_protocol_state(conversation: BotConversation) -> dict:
    metadata = dict(getattr(conversation, "metadata_json", {}) or {})
    protocol = _safe_load_protocol()
    steps = protocol.get("steps") or []
    current = str(metadata.get("current_step_code") or DEFAULT_PROTOCOL_STEP).upper()
    step_codes = [str(s.get("step_code") or "").upper() for s in steps]
    if step_codes and current not in step_codes:
        current = DEFAULT_PROTOCOL_STEP
    idx = step_codes.index(current) if step_codes and current in step_codes else 0
    next_step = step_codes[idx + 1] if idx + 1 < len(step_codes) else None
    completed = str(metadata.get("last_completed_step") or "").upper() or None
    return {
        "current_step_code": current,
        "last_completed_step": completed,
        "next_step_code": next_step,
        "protocol_version": str(metadata.get("protocol_version") or protocol.get("protocol_code") or DEFAULT_PROTOCOL_CODE),
        "progress_current": idx + 1,
        "progress_total": len(step_codes),
        "progress_percent": int(((idx + 1) / len(step_codes)) * 100) if step_codes else 0,
    }


def set_current_step(
    conversation: BotConversation,
    *,
    current_step_code: str,
    last_completed_step: str | None = None,
    autocommit: bool = True,
) -> BotConversation:
    metadata = dict(getattr(conversation, "metadata_json", {}) or {})
    protocol = _safe_load_protocol()
    step_codes = {str(s.get("step_code") or "").upper() for s in (protocol.get("steps") or [])}
    normalized_current = str(current_step_code or DEFAULT_PROTOCOL_STEP).strip().upper()
    if step_codes and normalized_current not in step_codes:
        normalized_current = DEFAULT_PROTOCOL_STEP
    metadata["protocol_version"] = str(metadata.get("protocol_version") or protocol.get("protocol_code") or DEFAULT_PROTOCOL_CODE)
    metadata["current_step_code"] = normalized_current
    if last_completed_step:
        metadata["last_completed_step"] = str(last_completed_step).strip().upper()
    conversation.metadata_json = metadata
    conversation.updated_at = utc_now_naive()
    if autocommit:
        db.session.commit()
    else:
        db.session.flush()
    return conversation


def advance_protocol_step(conversation: BotConversation, *, actor_id: int | None = None) -> BotConversation:
    protocol = load_protocol()
    steps = protocol.get("steps") or []
    if not steps:
        raise ValueError("Protocolo sin etapas configuradas")
    state = get_protocol_state(conversation)
    current = str(state.get("current_step_code") or DEFAULT_PROTOCOL_STEP).upper()
    step_codes = [str(s.get("step_code") or "").upper() for s in steps]
    if current not in step_codes:
        current = step_codes[0]
    idx = step_codes.index(current)
    next_idx = idx + 1
    if next_idx >= len(step_codes):
        raise ValueError("La conversación ya está en la última etapa del protocolo")
    return _apply_protocol_step_change(
        conversation,
        new_step_code=step_codes[next_idx],
        last_completed_step=current,
        action="advance",
        actor_id=actor_id,
        protocol=protocol,
    )


def regress_protocol_step(conversation: BotConversation, *, actor_id: int | None = None) -> BotConversation:
    protocol = load_protocol()
    steps = protocol.get("steps") or []
    if not steps:
        raise ValueError("Protocolo sin etapas configuradas")
    state = get_protocol_state(conversation)
    current = str(state.get("current_step_code") or DEFAULT_PROTOCOL_STEP).upper()
    step_codes = [str(s.get("step_code") or "").upper() for s in steps]
    if current not in step_codes:
        current = step_codes[0]
    idx = step_codes.index(current)
    prev_idx = idx - 1
    if prev_idx < 0:
        raise ValueError("La conversación ya está en la primera etapa del protocolo")
    return _apply_protocol_step_change(
        conversation,
        new_step_code=step_codes[prev_idx],
        last_completed_step=step_codes[prev_idx - 1] if prev_idx - 1 >= 0 else None,
        action="regress",
        actor_id=actor_id,
        protocol=protocol,
    )


def select_protocol_step(conversation: BotConversation, *, step_code: str, actor_id: int | None = None) -> BotConversation:
    protocol = load_protocol()
    steps = protocol.get("steps") or []
    if not steps:
        raise ValueError("Protocolo sin etapas configuradas")
    step_codes = [str(s.get("step_code") or "").upper() for s in steps]
    normalized = str(step_code or "").strip().upper()
    if normalized not in step_codes:
        raise ValueError("Etapa de protocolo inválida")
    idx = step_codes.index(normalized)
    return _apply_protocol_step_change(
        conversation,
        new_step_code=normalized,
        last_completed_step=step_codes[idx - 1] if idx - 1 >= 0 else None,
        action="select",
        actor_id=actor_id,
        protocol=protocol,
    )


def reset_protocol_state(conversation: BotConversation, *, actor_id: int | None = None) -> BotConversation:
    protocol = load_protocol()
    steps = protocol.get("steps") or []
    if not steps:
        raise ValueError("Protocolo sin etapas configuradas")
    first = str(steps[0].get("step_code") or DEFAULT_PROTOCOL_STEP).upper()
    return _apply_protocol_step_change(
        conversation,
        new_step_code=first,
        last_completed_step=None,
        action="reset",
        actor_id=actor_id,
        protocol=protocol,
    )


def complete_current_protocol_step(conversation: BotConversation, *, actor_id: int | None = None) -> BotConversation:
    state = get_protocol_state(conversation)
    current = str(state.get("current_step_code") or DEFAULT_PROTOCOL_STEP).upper()
    protocol = load_protocol()
    return _apply_protocol_step_change(
        conversation,
        new_step_code=current,
        last_completed_step=current,
        action="complete",
        actor_id=actor_id,
        protocol=protocol,
    )


def _ensure_protocol_state(conversation: BotConversation, *, autocommit: bool) -> None:
    metadata = dict(getattr(conversation, "metadata_json", {}) or {})
    protocol = _safe_load_protocol()
    changed = False
    if not metadata.get("protocol_version"):
        metadata["protocol_version"] = str(protocol.get("protocol_code") or DEFAULT_PROTOCOL_CODE)
        changed = True
    if not metadata.get("current_step_code"):
        metadata["current_step_code"] = DEFAULT_PROTOCOL_STEP
        changed = True
    if "last_completed_step" not in metadata:
        metadata["last_completed_step"] = None
        changed = True
    if changed:
        conversation.metadata_json = metadata
        conversation.updated_at = utc_now_naive()
        if autocommit:
            db.session.commit()
        else:
            db.session.flush()


def _safe_load_protocol() -> dict:
    try:
        return load_protocol()
    except Exception:
        return {"protocol_code": DEFAULT_PROTOCOL_CODE, "steps": []}


def _apply_protocol_step_change(
    conversation: BotConversation,
    *,
    new_step_code: str,
    last_completed_step: str | None,
    action: str,
    actor_id: int | None,
    protocol: dict,
) -> BotConversation:
    old_state = get_protocol_state(conversation)
    old_step = str(old_state.get("current_step_code") or DEFAULT_PROTOCOL_STEP).upper()
    metadata = dict(getattr(conversation, "metadata_json", {}) or {})
    metadata["protocol_version"] = str(metadata.get("protocol_version") or protocol.get("protocol_code") or DEFAULT_PROTOCOL_CODE)
    metadata["current_step_code"] = str(new_step_code or DEFAULT_PROTOCOL_STEP).strip().upper()
    metadata["last_completed_step"] = str(last_completed_step).strip().upper() if last_completed_step else None
    conversation.metadata_json = metadata
    conversation.updated_at = utc_now_naive()
    register_decision(
        conversation=conversation,
        decision_type=DECISION_TYPE_PROTOCOL_STEP_CHANGE,
        decision_result=DECISION_RESULT_MANUAL_ONLY,
        rule_code=f"PROTOCOL_{action.upper()}_MANUAL",
        reason_human=f"Cambio manual de etapa: {action}",
        facts_json={
            "old_step": old_step,
            "new_step": metadata["current_step_code"],
            "actor_id": actor_id,
            "action": action,
            "protocol_version": metadata["protocol_version"],
        },
        autocommit=False,
    )
    db.session.commit()
    return conversation


def set_conversation_status(conversation: BotConversation, *, status: str) -> BotConversation:
    normalized = (status or "").strip().lower()
    if normalized not in CONVERSATION_STATUSES:
        raise ValueError(f"Estado de conversación inválido: {status}")
    conversation.status = normalized
    conversation.updated_at = utc_now_naive()
    db.session.commit()
    return conversation


def pause_conversation(conversation: BotConversation, *, reason: str | None = None) -> BotConversation:
    conversation.bot_paused = True
    conversation.bot_pause_reason = (reason or "").strip() or None
    conversation.status = CONVERSATION_STATUS_BOT_PAUSED
    conversation.updated_at = utc_now_naive()
    db.session.commit()
    return conversation


def activate_conversation(conversation: BotConversation) -> BotConversation:
    conversation.bot_paused = False
    conversation.bot_pause_reason = None
    if conversation.status == CONVERSATION_STATUS_BOT_PAUSED:
        conversation.status = CONVERSATION_STATUS_OPEN
    conversation.updated_at = utc_now_naive()
    db.session.commit()
    return conversation


def resolve_conversation(conversation: BotConversation) -> BotConversation:
    now = utc_now_naive()
    conversation.status = CONVERSATION_STATUS_RESOLVED
    conversation.resolved_at = now
    conversation.updated_at = now
    db.session.commit()
    return conversation
