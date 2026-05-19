# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import time
import os
import json
import hmac
import hashlib
from datetime import datetime
import requests

from flask import current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user
from sqlalchemy.exc import ProgrammingError
from sqlalchemy import inspect as sa_inspect, or_, text

from config_app import csrf, db
from decorators import roles_required, staff_required
from models import BotCandidateDraft, BotConversation, BotDecisionLog, BotMessage, BotSandboxOutbound, BotSandboxReviewQueue, BotSetting, Candidata
from services.bot_constants import (
    MESSAGE_DIRECTION_INBOUND,
    MESSAGE_SOURCE_WHATSAPP_USER,
    MESSAGE_STATUS_INBOUND_RECEIVED,
    CONVERSATION_STATUS_ARCHIVED,
    CONVERSATION_STATUS_BOT_PAUSED,
    CONVERSATION_STATUS_OPEN,
    CONVERSATION_STATUS_PENDING_HUMAN,
    CONVERSATION_STATUS_RESOLVED,
    CONVERSATION_STATUSES,
    MESSAGE_STATUS_OUTBOUND_FAILED,
    MESSAGE_STATUS_OUTBOUND_QUEUED,
    MESSAGE_STATUS_OUTBOUND_SENT,
    DECISION_RESULT_MANUAL_ONLY,
    DECISION_TYPE_PROTOCOL_CORRECTION_APPROVED,
    DECISION_TYPE_PROTOCOL_CORRECTION_REJECTED,
)
from services.bot_ai_service import classify_intent, generate_safe_reply, is_ai_enabled, is_autoreply_enabled
from services.bot_ai_limits_service import get_ai_daily_usage_summary
from services.bot_conversation_service import (
    activate_conversation,
    advance_protocol_step,
    complete_current_protocol_step,
    get_conversation_by_id,
    get_or_create_manual_conversation,
    get_protocol_state,
    pause_conversation,
    regress_protocol_step,
    reset_protocol_state,
    resolve_conversation,
    select_protocol_step,
    set_current_step,
)
from services.bot_protocol_service import (
    build_step_prompt,
    get_next_step,
    get_step,
    has_personal_data_signal,
    is_greeting_only,
    is_positive_confirmation,
    load_protocol,
)
from services.bot_protocol_service import approve_pending_correction, reject_pending_correction
from services.bot_inbound_pipeline_service import is_protocol_auto_advance_enabled, process_inbound_ai_pipeline
from services.bot_decision_service import register_decision
from services.bot_identity_service import find_candidate_phone_duplicates
from services.phone_identity_service import normalize_phone_to_e164
from services.bot_candidate_summary_service import (
    build_candidate_summary,
    get_candidate_summary_status,
    get_missing_required_candidate_fields,
)
from services.bot_message_service import create_manual_message
from services.bot_candidate_draft_service import (
    can_create_candidate_draft,
    create_candidate_draft,
    get_or_create_interview_flow_draft,
    get_candidate_draft,
    mark_candidate_draft_under_review,
    reject_candidate_draft,
)
from services.bot_candidate_conversion_preview_service import build_candidate_conversion_preview, map_draft_to_candidate_fields
from services.bot_candidate_creation_service import (
    create_candidate_from_draft,
    validate_candidate_creation,
    evaluate_real_creation_guardrails,
)
from services.bot_candidate_intake_service import (
    INTAKE_APPROVED,
    INTAKE_DUPLICATE,
    INTAKE_INCOMPLETE,
    INTAKE_NEEDS_FOLLOWUP,
    INTAKE_PENDING_REVIEW,
    INTAKE_REJECTED,
    convert_intake_to_candidate,
    edit_intake_before_approve,
    ensure_intake_fields,
    set_intake_status,
)
from services.bot_observability_service import bot_timing, log_bot_blocked, log_bot_event
from services.bot_rate_limit_service import allow_action
from services.bot_practice_ai_reply_service import get_practice_reply_with_ai_fallback
from services.bot_sandbox_service import (
    REAL_SANDBOX_SETTING_KEY,
    SandboxSafetyError,
    AUTO_REPLY_PAUSED_SETTING_KEY,
    apply_delivery_webhook_update,
    archive_old_sandbox_outbox,
    assert_staging_offline_security,
    cleanup_sandbox_outbox_terminal,
    enqueue_sandbox_outbound,
    is_real_whatsapp_sandbox_owner_only_active,
    is_real_sandbox_paused,
    is_sandbox_auto_reply_active,
    is_sandbox_auto_reply_enabled,
    is_sandbox_auto_reply_paused,
    is_sandbox_assistant_allowed,
    is_real_whatsapp_sandbox_enabled,
    is_staging_offline_active,
    run_sandbox_worker_once,
    sandbox_metrics_snapshot,
    set_sandbox_auto_reply_paused,
    set_real_sandbox_paused,
)
from services.bot_sandbox_review_service import (
    ReviewTransitionError,
    auto_approve_review_in_sandbox,
    approve_review,
    create_inbound_message,
    create_review_from_inbound,
    reject_review,
)
from services.bot_whatsapp_payload_normalizer import PayloadNormalizationError, normalize_sandbox_webhook_payload
from services.environment_guard_service import get_sensitive_flags_snapshot
from services.whatsapp_cloud_service import is_bot_dry_run, is_whatsapp_enabled, send_text_message
from services.bot_data_safety_helpers import as_dict
from utils.audit_logger import log_action
from utils.timezone import utc_now_naive
from . import admin_bp


_DRAFT_ROUTE_RE = re.compile(r"^bot_draft:(\d+)$")
_BOT_REVIEW_STATUSES = {"bot_pending_review", "bot_reviewing", "bot_approved", "bot_rejected"}
_LOCAL_PRACTICE_TYPE = "local_practice"


def _normalize_pending_corrections(raw_items) -> list[dict]:
    normalized: list[dict] = []
    for item in list(raw_items or []):
        if not isinstance(item, dict):
            continue
        row = dict(item)
        corr_id = row.get("id")
        try:
            row["_corr_id"] = int(corr_id)
        except Exception:
            row["_corr_id"] = None
        normalized.append(row)
    return normalized


def _safe_intake(draft: BotCandidateDraft) -> dict:
    intake = dict(dict(getattr(draft, "metadata_json", {}) or {}).get("intake") or {})
    return {
        "status": str(intake.get("status") or INTAKE_PENDING_REVIEW),
        "quality_score": int(intake.get("quality_score") or 0),
        "quality_flags": list(intake.get("quality_flags") or []),
        "duplicates": list(intake.get("duplicates") or []),
        "invalid_answers_count": int(intake.get("invalid_answers_count") or 0),
    }


def _is_true_env(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _practice_demo_mode_enabled() -> bool:
    return _is_true_env(current_app.config.get("BOT_PRACTICE_DEMO_MODE")) or _is_true_env(
        os.environ.get("BOT_PRACTICE_DEMO_MODE")
    )


def _practice_guardrails() -> dict:
    snap = get_sensitive_flags_snapshot()
    risky_flags: list[str] = []
    if not bool(snap.get("is_local_environment")):
        risky_flags.append("APP_ENV no local/development/testing")
    if not bool(snap.get("is_safe_local_database")):
        risky_flags.append("DB no local (localhost/sqlite)")
    if _is_true_env("true" if bool(snap.get("whatsapp_enabled")) else "false"):
        risky_flags.append("WHATSAPP_ENABLED=true")
    if _is_true_env("true" if bool(snap.get("bot_autoreply_enabled")) else "false"):
        risky_flags.append("BOT_AUTOREPLY_ENABLED=true")
    if _is_true_env("true" if bool(snap.get("real_creation_allowed")) else "false"):
        risky_flags.append("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL=true")
    return {"allowed": len(risky_flags) == 0, "reasons": risky_flags, "snapshot": snap}


def _is_local_practice_conversation(conversation: BotConversation | None) -> bool:
    if not conversation:
        return False
    metadata = dict(getattr(conversation, "metadata_json", {}) or {})
    return str(metadata.get("conversation_type") or "").strip().lower() == _LOCAL_PRACTICE_TYPE


def _new_local_practice_phone() -> str:
    nonce = int(time.time() * 1000) % 10_000_000
    return f"+1999{nonce:07d}"


def _base_local_practice_metadata() -> dict:
    return {
        "conversation_type": _LOCAL_PRACTICE_TYPE,
        "practice_created_at": str(utc_now_naive()),
        "protocol_version": "domesticas_v1",
        "current_step_code": "WELCOME",
        "last_completed_step": None,
        "protocol_entities": {},
        "protocol_future_entities": {},
        "pending_corrections": [],
        "practice_virtual_messages": [],
        "practice_debug_protocol_state": {},
    }


def _create_clean_local_practice_conversation(*, archive_previous: BotConversation | None = None) -> BotConversation:
    phone = _new_local_practice_phone()
    conversation = BotConversation(
        channel="whatsapp",
        phone_e164=phone,
        contact_name="Práctica Bot",
        status=CONVERSATION_STATUS_OPEN,
        bot_paused=False,
        metadata_json=_base_local_practice_metadata(),
    )
    db.session.add(conversation)
    db.session.flush()
    if archive_previous and archive_previous.id != conversation.id:
        archive_previous.status = CONVERSATION_STATUS_ARCHIVED
        archive_previous.bot_paused = False
        old_meta = dict(getattr(archive_previous, "metadata_json", {}) or {})
        old_meta["practice_archived_at"] = str(utc_now_naive())
        old_meta["practice_replaced_by_conversation_id"] = int(conversation.id)
        archive_previous.metadata_json = old_meta
    return conversation


def reset_local_practice_conversation(conversation: BotConversation) -> BotConversation:
    if not _is_local_practice_conversation(conversation):
        raise ValueError("conversation_not_local_practice")
    return _create_clean_local_practice_conversation(archive_previous=conversation)


def _normalize_quick_text(value: str) -> str:
    txt = str(value or "").strip().lower()
    txt = txt.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
    txt = re.sub(r"\s+", " ", txt)
    txt = txt.replace("kiero", "quiero").replace("sii", "si")
    return txt


def _looks_positive_continue(text: str, *, step_code: str = "") -> bool:
    normalized = _normalize_quick_text(text)
    hints = (
        "si",
        "sí",
        "ok",
        "claro",
        "dale",
        "vamos",
        "correcto",
        "si soy yo",
        "soy yo",
        "hazme las preguntas",
        "hasme las preguntas",
        "quiero registrarme",
        "quiero trabajar",
    )
    if str(step_code or "").strip().upper() == "PERSONAL_CONFIRMATION":
        # En confirmación personal no tratamos saludos como afirmación.
        return any(h in normalized for h in hints)
    return any(h in normalized for h in hints) or any(g in normalized for g in ("buenas", "ola", "hola"))


def _normalize_practice_virtual_messages(raw_items) -> list[dict]:
    out: list[dict] = []
    for item in list(raw_items or []):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        out.append(
            {
                "role": "bot_suggested",
                "text": text,
                "created_at": str(item.get("created_at") or ""),
                "source": str(item.get("source") or "protocol"),
                "is_virtual": True,
                "inbound_message_id": int(item.get("inbound_message_id") or 0) or None,
                "base_suggested_reply": str(item.get("base_suggested_reply") or ""),
                "ai_suggested_reply": str(item.get("ai_suggested_reply") or ""),
                "ai_reply_used": bool(item.get("ai_reply_used", False)),
                "ai_reply_safety_status": str(item.get("ai_reply_safety_status") or "disabled"),
                "ai_reply_fallback_reason": str(item.get("ai_reply_fallback_reason") or ""),
            }
        )
    return out


def _step_rank(step_code: str) -> int:
    try:
        steps = load_protocol().get("steps") or []
    except Exception:
        return -1
    normalized = str(step_code or "").strip().upper()
    for idx, step in enumerate(steps):
        if str(step.get("step_code") or "").strip().upper() == normalized:
            return idx
    return -1


def _is_same_or_after(step_code: str, threshold_step: str) -> bool:
    step_idx = _step_rank(step_code)
    threshold_idx = _step_rank(threshold_step)
    return step_idx >= 0 and threshold_idx >= 0 and step_idx >= threshold_idx


def _filter_future_entities_for_sidebar(
    future_entities: dict[str, dict], protocol_entities: dict[str, object], current_step_code: str
) -> dict[str, dict]:
    if not future_entities:
        return {}
    consume_by_step = {
        "ADDRESS": {"city"},
        "WORK_TYPE": {"work_type"},
        "TRANSPORT_ROUTE": {"route", "transport_route"},
    }
    consumed_keys = set()
    if str(current_step_code or "").strip().upper() in consume_by_step:
        consumed_keys.update(consume_by_step[str(current_step_code or "").strip().upper()])
    if _is_same_or_after(str(current_step_code or ""), "ADDRESS"):
        consumed_keys.add("city")
    if _is_same_or_after(str(current_step_code or ""), "WORK_TYPE"):
        consumed_keys.add("work_type")
    if _is_same_or_after(str(current_step_code or ""), "TRANSPORT_ROUTE"):
        consumed_keys.update({"route", "transport_route"})
    out: dict[str, dict] = {}
    for key, value in dict(future_entities).items():
        if key in consumed_keys:
            continue
        if key in protocol_entities:
            continue
        out[str(key)] = dict(value or {})
    return out


def _build_practice_state_payload(conversation: BotConversation) -> dict:
    messages = (
        BotMessage.query.filter_by(conversation_id=conversation.id)
        .order_by(BotMessage.created_at.asc(), BotMessage.id.asc())
        .all()
    )
    state = get_protocol_state(conversation)
    protocol_current_step = get_step(state.get("current_step_code") or "")
    pending_corrections = _normalize_pending_corrections((conversation.metadata_json or {}).get("pending_corrections") or [])
    metadata = dict(getattr(conversation, "metadata_json", {}) or {})
    protocol_entities = dict(metadata.get("protocol_entities") or {})
    raw_protocol_future_entities = dict(metadata.get("protocol_future_entities") or {})
    virtual_messages = _normalize_practice_virtual_messages(metadata.get("practice_virtual_messages") or [])
    debug_protocol_state = dict(metadata.get("practice_debug_protocol_state") or {})

    latest_ai = (
        BotDecisionLog.query.filter_by(conversation_id=conversation.id, decision_type="ai_classification")
        .order_by(BotDecisionLog.created_at.desc(), BotDecisionLog.id.desc())
        .first()
    )
    latest_auto = (
        BotDecisionLog.query.filter_by(conversation_id=conversation.id, decision_type="protocol_auto_advance")
        .order_by(BotDecisionLog.created_at.desc(), BotDecisionLog.id.desc())
        .first()
    )
    facts = dict(getattr(latest_ai, "facts_json", {}) or {})
    auto_facts = dict(getattr(latest_auto, "facts_json", {}) or {})
    suggested_reply = str(facts.get("suggested_reply") or auto_facts.get("next_step_prompt") or auto_facts.get("clarification_prompt") or "").strip()
    suggested_reply_source = ""
    base_suggested_reply = ""
    ai_suggested_reply = ""
    ai_reply_used = False
    ai_reply_safety_status = "disabled"
    ai_reply_fallback_reason = "not_available"
    suggested_created_at = None
    if suggested_reply and str(facts.get("suggested_reply") or "").strip():
        suggested_reply_source = "ai"
        suggested_created_at = getattr(latest_ai, "created_at", None)
    elif suggested_reply:
        suggested_reply_source = "protocol_auto_advance"
        suggested_created_at = getattr(latest_auto, "created_at", None)

    last_prompt = str(build_step_prompt(state.get("current_step_code") or "") or "").strip()
    protocol_future_entities = _filter_future_entities_for_sidebar(
        raw_protocol_future_entities,
        protocol_entities,
        str(state.get("current_step_code") or ""),
    )
    last_completed = str(state.get("last_completed_step") or "").strip().upper()
    current_step = str(state.get("current_step_code") or "").strip().upper()
    if (
        _is_same_or_after(last_completed, "BASIC_INFO")
        and current_step != "BASIC_INFO"
        and suggested_reply
        and (
            ("nombre completo" in suggested_reply.lower())
            or ("tu edad" in suggested_reply.lower())
            or ("cedula" in suggested_reply.lower())
        )
    ):
        suggested_reply = last_prompt
        suggested_reply_source = "protocol_current_step_guard"
    if (
        _is_same_or_after(last_completed, "ADDRESS")
        and current_step != "ADDRESS"
        and suggested_reply
        and (
            ("ciudad" in suggested_reply.lower())
            or ("sector" in suggested_reply.lower())
            or ("santiago" in suggested_reply.lower())
            or ("puerto plata" in suggested_reply.lower())
        )
    ):
        correction_allows_address = any(
            str(item.get("field") or "").strip().lower() in {"city", "address", "sector"}
            or str(item.get("suggested_step_code") or "").strip().upper() == "ADDRESS"
            for item in pending_corrections
            if isinstance(item, dict)
        )
        if not correction_allows_address:
            suggested_reply = last_prompt
            suggested_reply_source = "protocol_current_step_guard"
    if (not suggested_reply) and last_prompt:
        suggested_reply = last_prompt
        suggested_reply_source = "protocol"
        suggested_created_at = suggested_created_at or getattr(conversation, "updated_at", None) or getattr(conversation, "created_at", None)
    base_suggested_reply = suggested_reply

    requires_human = bool(facts.get("requires_human", False)) or bool((latest_auto and latest_auto.rule_code == "PROTOCOL_AUTO_ADVANCE_BLOCKED_HUMAN"))

    candidate_summary_status = get_candidate_summary_status(conversation)
    interview_flow_state = dict((conversation.metadata_json or {}).get("interview_flow") or {})
    draft_possible = can_create_candidate_draft(conversation)

    virtual_bot_message = virtual_messages[-1] if virtual_messages else None
    if virtual_bot_message:
        ai_reply_used = bool(virtual_bot_message.get("ai_reply_used"))
        ai_suggested_reply = str(virtual_bot_message.get("ai_suggested_reply") or "")
        ai_reply_safety_status = str(virtual_bot_message.get("ai_reply_safety_status") or "disabled")
        ai_reply_fallback_reason = str(virtual_bot_message.get("ai_reply_fallback_reason") or "")
        if not base_suggested_reply:
            base_suggested_reply = str(virtual_bot_message.get("base_suggested_reply") or suggested_reply or "")
    else:
        base_suggested_reply = base_suggested_reply or suggested_reply
    if (not virtual_bot_message) and suggested_reply:
        if str(suggested_reply_source or "").strip() == "practice_ai":
            ai_reply_used = True
            ai_suggested_reply = str(suggested_reply or "")
            ai_reply_safety_status = "ok"
            ai_reply_fallback_reason = ""
        virtual_bot_message = {
            "role": "bot_suggested",
            "text": suggested_reply,
            "created_at": str(suggested_created_at or getattr(conversation, "updated_at", None) or getattr(conversation, "created_at", None) or ""),
            "source": str(suggested_reply_source or "protocol"),
            "is_virtual": True,
            "inbound_message_id": None,
        }

    chat_items: list[dict] = []
    message_by_id: dict[int, dict] = {}
    for m in messages:
        item = {
            "role": "candidate" if str(m.direction or "") == "inbound" else "staff",
            "text": str(m.text_body or ""),
            "created_at": str(m.created_at or ""),
            "is_virtual": False,
            "message_id": int(m.id),
            "direction": str(m.direction or ""),
            "source": str(m.source or ""),
        }
        chat_items.append(item)
        message_by_id[int(m.id)] = item

    virtual_by_inbound: dict[int, list[dict]] = {}
    for vm in virtual_messages:
        inbound_id = vm.get("inbound_message_id")
        if inbound_id is None:
            continue
        virtual_by_inbound.setdefault(int(inbound_id), []).append(vm)

    ordered_items: list[dict] = []
    for m in messages:
        mid = int(m.id)
        ordered_items.append(message_by_id[mid])
        if str(m.direction or "") == "inbound":
            for vm in virtual_by_inbound.get(mid, []):
                ordered_items.append(vm)
    chat_items = ordered_items

    return {
        "conversation_id": int(conversation.id),
        "messages": [
            {
                "id": int(m.id),
                "direction": str(m.direction or ""),
                "source": str(m.source or ""),
                "text_body": str(m.text_body or ""),
                "status": str(m.status or ""),
                "created_at": str(m.created_at or ""),
            }
            for m in messages
        ],
        "current_step": str(state.get("current_step_code") or ""),
        "next_step": state.get("next_step_code"),
        "progress": {
            "current": int(state.get("progress_current") or 0),
            "total": int(state.get("progress_total") or 0),
            "percent": int(state.get("progress_percent") or 0),
        },
        "requires_human": requires_human,
        "suggested_reply": suggested_reply,
        "suggested_reply_source": str(suggested_reply_source or "protocol"),
        "base_suggested_reply": str(base_suggested_reply or ""),
        "ai_suggested_reply": str(ai_suggested_reply or ""),
        "ai_reply_used": bool(ai_reply_used),
        "ai_reply_safety_status": str(ai_reply_safety_status or "disabled"),
        "ai_reply_fallback_reason": str(ai_reply_fallback_reason or "not_available"),
        "virtual_bot_message": virtual_bot_message,
        "virtual_messages": virtual_messages,
        "chat_items": chat_items,
        "protocol_entities": protocol_entities,
        "protocol_future_entities": protocol_future_entities,
        "pending_corrections": pending_corrections,
        "summary_status": candidate_summary_status,
        "draft_possible": draft_possible,
        "protocol_step_requires_human": bool((protocol_current_step or {}).get("requires_human", False)),
        "protocol_step_title": str((protocol_current_step or {}).get("title") or ""),
        "last_prompt": last_prompt,
        "metadata_json": metadata,
        "debug_protocol_state": debug_protocol_state,
    }


def _build_practice_debug_payload(conversation: BotConversation) -> dict:
    state = _build_practice_state_payload(conversation)
    metadata = dict(getattr(conversation, "metadata_json", {}) or {})
    messages = list(state.get("messages") or [])
    chat_items = list(state.get("chat_items") or [])
    summarized_chat = []
    for item in chat_items[-25:]:
        summarized_chat.append(
            {
                "role": str(item.get("role") or ""),
                "text": str(item.get("text") or item.get("text_body") or ""),
                "created_at": str(item.get("created_at") or ""),
                "is_virtual": bool(item.get("is_virtual")),
                "source": str(item.get("source") or ""),
                "inbound_message_id": item.get("inbound_message_id"),
            }
        )
    last_user = next((m for m in reversed(messages) if str(m.get("direction") or "") == "inbound"), None)
    last_bot = next((m for m in reversed(chat_items) if str(m.get("role") or "") == "bot_suggested"), None)
    return {
        "ok": True,
        "conversation_id": int(conversation.id),
        "conversation_type": str(metadata.get("conversation_type") or ""),
        "current_step": str(state.get("current_step") or ""),
        "last_completed_step": str((state.get("debug_protocol_state") or {}).get("last_completed_step") or ""),
        "progress": dict(state.get("progress") or {}),
        "protocol_entities": dict(state.get("protocol_entities") or {}),
        "protocol_future_entities": dict(state.get("protocol_future_entities") or {}),
        "pending_corrections": list(state.get("pending_corrections") or []),
        "practice_virtual_messages": list(state.get("virtual_messages") or []),
        "chat_items": summarized_chat,
        "last_user_message": last_user,
        "last_bot_suggestion": {
            "text": str((last_bot or {}).get("text") or state.get("suggested_reply") or ""),
            "source": str((last_bot or {}).get("source") or state.get("suggested_reply_source") or ""),
            "created_at": str((last_bot or {}).get("created_at") or ""),
            "base_suggested_reply": str(state.get("base_suggested_reply") or ""),
            "ai_suggested_reply": str(state.get("ai_suggested_reply") or ""),
            "ai_reply_used": bool(state.get("ai_reply_used")),
            "ai_reply_safety_status": str(state.get("ai_reply_safety_status") or ""),
            "ai_reply_fallback_reason": str(state.get("ai_reply_fallback_reason") or ""),
        },
        "debug_protocol_state": dict(state.get("debug_protocol_state") or {}),
        "requires_human": bool(state.get("requires_human")),
        "summary_status": str(state.get("summary_status") or ""),
        "draft_possible": state.get("draft_possible"),
    }


def _extract_draft_id(creado_desde_ruta: str | None) -> int | None:
    route_value = str(creado_desde_ruta or "").strip()
    m = _DRAFT_ROUTE_RE.match(route_value)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _get_bot_review_state(draft: BotCandidateDraft | None) -> dict:
    base = {
        "status": "bot_pending_review",
        "reviewer_id": None,
        "review_taken_at": None,
        "approved_at": None,
        "rejected_at": None,
        "rejection_reason": None,
    }
    if not draft:
        return base
    raw = dict(getattr(draft, "metadata_json", {}) or {}).get("bot_review_workflow") or {}
    status = str(raw.get("status") or "").strip()
    if status not in _BOT_REVIEW_STATUSES:
        status = "bot_pending_review"
    base.update(
        {
            "status": status,
            "reviewer_id": raw.get("reviewer_id"),
            "review_taken_at": raw.get("review_taken_at"),
            "approved_at": raw.get("approved_at"),
            "rejected_at": raw.get("rejected_at"),
            "rejection_reason": raw.get("rejection_reason"),
        }
    )
    return base


def _set_bot_review_state(draft: BotCandidateDraft, state: dict) -> None:
    metadata = dict(getattr(draft, "metadata_json", {}) or {})
    metadata["bot_review_workflow"] = {
        "status": str(state.get("status") or "bot_pending_review"),
        "reviewer_id": state.get("reviewer_id"),
        "review_taken_at": state.get("review_taken_at"),
        "approved_at": state.get("approved_at"),
        "rejected_at": state.get("rejected_at"),
        "rejection_reason": state.get("rejection_reason"),
    }
    draft.metadata_json = metadata


def _get_bot_review_target(candidata_id: int) -> tuple[Candidata | None, BotCandidateDraft | None]:
    candidata = Candidata.query.filter_by(fila=int(candidata_id)).first()
    if not candidata:
        return None, None
    draft_id = _extract_draft_id(getattr(candidata, "creado_desde_ruta", None))
    if not draft_id:
        return candidata, None
    draft = BotCandidateDraft.query.filter_by(id=int(draft_id)).first()
    return candidata, draft


def _get_bot_review_target_locked(candidata_id: int) -> tuple[Candidata | None, BotCandidateDraft | None]:
    candidata = Candidata.query.filter_by(fila=int(candidata_id)).first()
    if not candidata:
        return None, None
    draft_id = _extract_draft_id(getattr(candidata, "creado_desde_ruta", None))
    if not draft_id:
        return candidata, None
    draft_query = BotCandidateDraft.query.filter_by(id=int(draft_id))
    try:
        draft_query = draft_query.with_for_update()
    except Exception:
        pass
    draft = draft_query.first()
    return candidata, draft


def _log_bot_review_blocked(
    *,
    candidata_id: int,
    actor_id: int | None,
    attempted_action: str,
    current_status: str,
    reason: str,
) -> None:
    log_action(
        action_type="bot_candidate_review_blocked",
        entity_type="Candidata",
        entity_id=str(candidata_id),
        summary="Bloqueo de transición en revisión manual de candidata bot",
        metadata={
            "candidata_id": int(candidata_id),
            "actor_id": actor_id,
            "attempted_action": attempted_action,
            "current_status": current_status,
            "reason": reason,
        },
        actor_user_id=actor_id,
        success=False,
        error="invalid_transition_concurrent",
    )


@admin_bp.route("/bot/conversaciones", methods=["GET"])
@staff_required
def bot_conversations_list():
    q = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "").strip().lower()

    query = BotConversation.query
    if q:
        like_q = f"%{q}%"
        query = query.filter((BotConversation.phone_e164.ilike(like_q)) | (BotConversation.contact_name.ilike(like_q)))
    if status and status in CONVERSATION_STATUSES:
        query = query.filter(BotConversation.status == status)

    conversations = query.order_by(BotConversation.last_message_at.desc(), BotConversation.id.desc()).limit(200).all()
    return render_template(
        "admin/bot/conversaciones.html",
        conversations=conversations,
        identity_duplicate_groups=find_candidate_phone_duplicates(),
        q=q,
        status_filter=status,
        statuses=[
            CONVERSATION_STATUS_OPEN,
            CONVERSATION_STATUS_PENDING_HUMAN,
            CONVERSATION_STATUS_BOT_PAUSED,
            CONVERSATION_STATUS_RESOLVED,
            CONVERSATION_STATUS_ARCHIVED,
        ],
        bot_safety=get_sensitive_flags_snapshot(),
    )


@admin_bp.route("/bot/practica", methods=["GET", "POST"])
@staff_required
def bot_practice_chat():
    guardrails = _practice_guardrails()
    if request.method == "POST":
        if not guardrails["allowed"]:
            flash("Práctica local bloqueada por guardrails: " + "; ".join(guardrails["reasons"]), "warning")
            return redirect(url_for("admin.bot_practice_chat"))
        conversation = _create_clean_local_practice_conversation()
        db.session.commit()
        return redirect(url_for("admin.bot_practice_chat_detail", conversation_id=conversation.id))

    conversation = None
    cid = request.args.get("conversation_id")
    if cid:
        try:
            maybe = get_conversation_by_id(int(cid))
            if _is_local_practice_conversation(maybe):
                conversation = maybe
        except Exception:
            conversation = None
    if not conversation:
        recent = BotConversation.query.order_by(BotConversation.updated_at.desc(), BotConversation.id.desc()).limit(200).all()
        conversation = next((x for x in recent if _is_local_practice_conversation(x)), None)
    practice_state = _build_practice_state_payload(conversation) if conversation else None
    return render_template(
        "admin/bot/practica_chat.html",
        conversation=conversation,
        practice_state=practice_state,
        practice_guardrails=guardrails,
        practice_demo_mode=_practice_demo_mode_enabled(),
        bot_safety=get_sensitive_flags_snapshot(),
    )


@admin_bp.route("/bot/practica/<int:conversation_id>", methods=["GET"])
@staff_required
def bot_practice_chat_detail(conversation_id: int):
    conversation = get_conversation_by_id(conversation_id)
    if not _is_local_practice_conversation(conversation):
        flash("Práctica no encontrada.", "warning")
        return redirect(url_for("admin.bot_practice_chat"))
    guardrails = _practice_guardrails()
    return render_template(
        "admin/bot/practica_chat.html",
        conversation=conversation,
        practice_state=_build_practice_state_payload(conversation),
        practice_guardrails=guardrails,
        practice_demo_mode=_practice_demo_mode_enabled(),
        bot_safety=get_sensitive_flags_snapshot(),
    )


@admin_bp.route("/bot/practica/<int:conversation_id>/mensaje", methods=["POST"])
@staff_required
def bot_practice_chat_send_message(conversation_id: int):
    t0 = time.perf_counter()
    guardrails = _practice_guardrails()
    if not guardrails["allowed"]:
        return jsonify({"ok": False, "error": "guardrails_blocked", "reasons": guardrails["reasons"]}), 403
    conversation = get_conversation_by_id(conversation_id)
    if not _is_local_practice_conversation(conversation):
        return jsonify({"ok": False, "error": "conversation_not_local_practice"}), 404

    data = request.get_json(silent=True) or {}
    body = str(data.get("text") or data.get("body") or request.form.get("text") or request.form.get("body") or "").strip()
    if not body:
        return jsonify({"ok": False, "error": "empty_body"}), 400

    state_before = get_protocol_state(conversation)
    inbound = create_manual_message(
        conversation=conversation,
        text_body=body,
        direction=MESSAGE_DIRECTION_INBOUND,
        source=MESSAGE_SOURCE_WHATSAPP_USER,
        status=MESSAGE_STATUS_INBOUND_RECEIVED,
    )
    t_after_inbound = time.perf_counter()
    pipeline_result = process_inbound_ai_pipeline(
        conversation=conversation,
        inbound_message=inbound,
        identity_status=str(getattr(conversation.identity, "identity_status", "") or "unknown"),
        message_type="text",
        phone_e164=conversation.phone_e164,
        allow_autoreply_send=False,
    )
    state_after_pipeline = get_protocol_state(conversation)
    t_after_pipeline = time.perf_counter()

    payload = _build_practice_state_payload(conversation)
    suggested_text = str(payload.get("suggested_reply") or payload.get("last_prompt") or "").strip()
    metadata = dict(getattr(conversation, "metadata_json", {}) or {})
    if suggested_text:
        existing = _normalize_practice_virtual_messages(metadata.get("practice_virtual_messages") or [])
        previous_text = str((existing[-1] or {}).get("text") or "").strip() if existing else ""
        if previous_text and previous_text == suggested_text:
            current_step = str(payload.get("current_step") or "").strip().upper()
            greeting_or_noise = is_greeting_only(body)
            next_step = get_next_step(current_step) or {}
            next_prompt = str(build_step_prompt(str(next_step.get("step_code") or "")) or "").strip()
            personal_confirmation_can_advance = True
            if current_step == "PERSONAL_CONFIRMATION":
                personal_confirmation_can_advance = bool(
                    is_positive_confirmation(body, step_code="PERSONAL_CONFIRMATION") or has_personal_data_signal(body)
                )
            can_use_next_step_fallback = (not greeting_or_noise) and (
                current_step != "PERSONAL_CONFIRMATION" or personal_confirmation_can_advance
            )
            if (
                current_step in {"WELCOME", "PERSONAL_CONFIRMATION"}
                and _looks_positive_continue(body, step_code=current_step)
                and next_prompt
                and can_use_next_step_fallback
            ):
                suggested_text = next_prompt
                payload["suggested_reply"] = next_prompt
                payload["suggested_reply_source"] = "protocol_next_step_fallback"
                if next_step and str(next_step.get("step_code") or "").strip().upper():
                    set_current_step(
                        conversation,
                        current_step_code=str(next_step.get("step_code") or ""),
                        last_completed_step=current_step,
                        autocommit=False,
                    )
            elif (
                next_prompt
                and str((state_before.get("last_completed_step") or "")).strip().upper() != current_step
                and can_use_next_step_fallback
            ):
                suggested_text = next_prompt
                payload["suggested_reply"] = next_prompt
                payload["suggested_reply_source"] = "protocol_anti_loop"
                if next_step and str(next_step.get("step_code") or "").strip().upper():
                    set_current_step(
                        conversation,
                        current_step_code=str(next_step.get("step_code") or ""),
                        last_completed_step=current_step,
                        autocommit=False,
                    )
            else:
                repeated_count = int(metadata.get("practice_confirmation_repeat_count") or 0)
                if current_step == "PERSONAL_CONFIRMATION":
                    options = [
                        "Para continuar, responde SI o NO.",
                        "Necesito confirmar si eres la persona interesada. Responde SI o NO.",
                    ]
                    suggested_text = options[repeated_count % len(options)]
                    metadata["practice_confirmation_repeat_count"] = repeated_count + 1
                elif greeting_or_noise:
                    current_prompt = str(build_step_prompt(current_step) or "").strip()
                    suggested_text = current_prompt or "Necesito la información de esta etapa para continuar."
                else:
                    suggested_text = "Perfecto. Continuemos con la siguiente información."
                payload["suggested_reply"] = suggested_text
                payload["suggested_reply_source"] = "anti_loop"
        base_suggested_text = str(suggested_text or "").strip()
        ai_overlay = get_practice_reply_with_ai_fallback(
            conversation=conversation,
            base_suggested_reply=base_suggested_text,
            current_step=str(payload.get("current_step") or ""),
            candidate_message=str(body or ""),
            context={
                "next_step": payload.get("next_step"),
                "protocol_step_title": payload.get("protocol_step_title"),
                "protocol_entities": dict(payload.get("protocol_entities") or {}),
                "recent_bot_suggestions": [str(item.get("text") or "") for item in existing[-5:]],
            },
            requires_human=bool(payload.get("requires_human")),
        )
        suggested_text = str(ai_overlay.get("suggested_reply") or base_suggested_text).strip()
        payload["suggested_reply"] = suggested_text
        payload["suggested_reply_source"] = str(ai_overlay.get("suggested_reply_source") or payload.get("suggested_reply_source") or "protocol")
        payload["base_suggested_reply"] = base_suggested_text
        payload["ai_suggested_reply"] = str(ai_overlay.get("ai_suggested_reply") or "")
        payload["ai_reply_used"] = bool(ai_overlay.get("ai_reply_used"))
        payload["ai_reply_safety_status"] = str(ai_overlay.get("ai_reply_safety_status") or "disabled")
        payload["ai_reply_fallback_reason"] = str(ai_overlay.get("ai_reply_fallback_reason") or "")
        existing.append(
            {
                "role": "bot_suggested",
                "text": suggested_text,
                "created_at": str(utc_now_naive()),
                "source": str(payload.get("suggested_reply_source") or "protocol"),
                "is_virtual": True,
                "inbound_message_id": int(inbound.id),
                "base_suggested_reply": base_suggested_text,
                "ai_suggested_reply": str(payload.get("ai_suggested_reply") or ""),
                "ai_reply_used": bool(payload.get("ai_reply_used")),
                "ai_reply_safety_status": str(payload.get("ai_reply_safety_status") or "disabled"),
                "ai_reply_fallback_reason": str(payload.get("ai_reply_fallback_reason") or ""),
            }
        )
        metadata["practice_virtual_messages"] = existing[-300:]
    protocol_auto = dict((pipeline_result or {}).get("protocol_auto_advance") or {})
    blocking_reason = ""
    if bool(protocol_auto.get("pending_correction")):
        blocking_reason = "pending_correction"
    elif bool(protocol_auto.get("out_of_step")):
        blocking_reason = "out_of_step"
    elif bool(protocol_auto.get("requires_human")):
        blocking_reason = "requires_human"
    elif bool(protocol_auto.get("enabled")) and not bool(protocol_auto.get("matched")):
        blocking_reason = str(protocol_auto.get("reason") or "validation_not_matched")
    metadata["practice_debug_protocol_state"] = {
        "current_step_before": str(state_before.get("current_step_code") or ""),
        "current_step_after": str(state_after_pipeline.get("current_step_code") or ""),
        "last_completed_step": str(state_after_pipeline.get("last_completed_step") or ""),
        "suggested_step_used": str(payload.get("current_step") or ""),
        "requires_human": bool(payload.get("requires_human")),
        "blocking_reason": blocking_reason,
    }
    conversation.metadata_json = metadata

    db.session.commit()
    t_after_commit = time.perf_counter()
    payload = _build_practice_state_payload(conversation)
    t_after_payload = time.perf_counter()
    timings = {
        "practice_message_ms": int((t_after_payload - t0) * 1000),
        "save_inbound_ms": int((t_after_inbound - t0) * 1000),
        "pipeline_ms": int((t_after_pipeline - t_after_inbound) * 1000),
        "state_build_ms": int((t_after_payload - t_after_commit) * 1000),
    }
    if timings["practice_message_ms"] > 2000:
        current_app.logger.warning("practice_message_slow conversation_id=%s timings=%s", conversation_id, timings)
    return jsonify({"ok": True, "timings": timings, **payload})


@admin_bp.route("/bot/practica/<int:conversation_id>/estado", methods=["GET"])
@staff_required
def bot_practice_chat_state(conversation_id: int):
    conversation = get_conversation_by_id(conversation_id)
    if not _is_local_practice_conversation(conversation):
        return jsonify({"ok": False, "error": "conversation_not_local_practice"}), 404
    return jsonify({"ok": True, **_build_practice_state_payload(conversation)})


@admin_bp.route("/bot/practica/<int:conversation_id>/debug.json", methods=["GET"])
@staff_required
def bot_practice_chat_debug_json(conversation_id: int):
    guardrails = _practice_guardrails()
    if not guardrails["allowed"]:
        return jsonify({"ok": False, "error": "guardrails_blocked", "reasons": guardrails["reasons"]}), 403
    conversation = get_conversation_by_id(conversation_id)
    if not _is_local_practice_conversation(conversation):
        return jsonify({"ok": False, "error": "conversation_not_local_practice"}), 404
    return jsonify(_build_practice_debug_payload(conversation))


@admin_bp.route("/bot/practica/<int:conversation_id>/control", methods=["POST"])
@staff_required
def bot_practice_chat_control(conversation_id: int):
    guardrails = _practice_guardrails()
    if not guardrails["allowed"]:
        return jsonify({"ok": False, "error": "guardrails_blocked", "reasons": guardrails["reasons"]}), 403
    conversation = get_conversation_by_id(conversation_id)
    if not _is_local_practice_conversation(conversation):
        return jsonify({"ok": False, "error": "conversation_not_local_practice"}), 404

    payload = request.get_json(silent=True) or {}
    action = str(payload.get("action") or "").strip().lower()
    try:
        if action == "reset":
            new_conv = reset_local_practice_conversation(conversation)
            db.session.commit()
            return jsonify(
                {
                    "ok": True,
                    "redirect_url": url_for("admin.bot_practice_chat_detail", conversation_id=new_conv.id),
                    **_build_practice_state_payload(new_conv),
                }
            )
        elif action == "advance":
            advance_protocol_step(conversation, actor_id=_protocol_actor_id())
        elif action == "regress":
            regress_protocol_step(conversation, actor_id=_protocol_actor_id())
        elif action == "complete":
            complete_current_protocol_step(conversation, actor_id=_protocol_actor_id())
        elif action == "summary":
            pass
        elif action == "create_draft":
            create_candidate_draft(conversation, actor_id=_protocol_actor_id())
        else:
            return jsonify({"ok": False, "error": "invalid_action"}), 400
    except ValueError as exc:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception:
        db.session.rollback()
        return jsonify({"ok": False, "error": "control_action_failed"}), 500
    db.session.commit()
    return jsonify({"ok": True, **_build_practice_state_payload(conversation)})


@admin_bp.route("/bot/identidades/duplicados", methods=["GET"])
@staff_required
def bot_identity_duplicates():
    duplicate_groups = find_candidate_phone_duplicates()
    return render_template("admin/bot/identidades_duplicados.html", duplicate_groups=duplicate_groups)


@admin_bp.route("/bot/candidatas-creadas", methods=["GET"])
@staff_required
def bot_created_candidates_list():
    columns = {str(c.get("name")) for c in sa_inspect(db.engine).get_columns("candidatas")}
    has_origen_registro = "origen_registro" in columns
    has_creado_desde_ruta = "creado_desde_ruta" in columns

    if has_origen_registro and has_creado_desde_ruta:
        filter_expr = or_(Candidata.origen_registro == "bot_draft", Candidata.creado_desde_ruta.like("bot_draft:%"))
    elif has_origen_registro:
        filter_expr = Candidata.origen_registro == "bot_draft"
    elif has_creado_desde_ruta:
        filter_expr = Candidata.creado_desde_ruta.like("bot_draft:%")
    else:
        filter_expr = None

    query = Candidata.query.order_by(Candidata.marca_temporal.desc(), Candidata.fila.desc())
    if filter_expr is not None:
        query = query.filter(filter_expr)
    else:
        query = query.filter(Candidata.fila == -1)
    candidates = query.limit(500).all()

    rows = []
    draft_ids: list[int] = []
    for cand in candidates:
        draft_id = _extract_draft_id(getattr(cand, "creado_desde_ruta", None))
        if draft_id is not None:
            draft_ids.append(draft_id)
        rows.append(
            {
                "candidata": cand,
                "draft_id": draft_id,
                "conversation_id": None,
            }
        )

    conversation_map: dict[int, int] = {}
    unique_draft_ids = sorted(set(draft_ids))
    if unique_draft_ids:
        draft_rows = (
            BotCandidateDraft.query.with_entities(BotCandidateDraft.id, BotCandidateDraft.conversation_id)
            .filter(BotCandidateDraft.id.in_(unique_draft_ids))
            .all()
        )
        for draft_id, conversation_id in draft_rows:
            try:
                conversation_map[int(draft_id)] = int(conversation_id)
            except Exception:
                continue

    for row in rows:
        draft_id = row["draft_id"]
        if draft_id is not None:
            row["conversation_id"] = conversation_map.get(int(draft_id))
            draft = BotCandidateDraft.query.filter_by(id=int(draft_id)).first()
            bot_review = _get_bot_review_state(draft)
            reviewer = draft.reviewer_user if draft else None
            row["bot_review"] = bot_review
            row["bot_review_reviewer_name"] = getattr(reviewer, "username", None) if reviewer else None
        else:
            row["bot_review"] = _get_bot_review_state(None)
            row["bot_review_reviewer_name"] = None

    metrics = {
        "total_created_from_bot": len(rows),
        "en_proceso": sum(1 for r in rows if str(getattr(r["candidata"], "estado", "")) == "en_proceso"),
        "revisadas_listas": sum(
            1
            for r in rows
            if str(getattr(r["candidata"], "estado", "")) in {"lista_para_trabajar", "inscrita", "inscrita_incompleta", "trabajando"}
        ),
        "rechazadas_descalificadas": sum(1 for r in rows if str(getattr(r["candidata"], "estado", "")) == "descalificada"),
    }

    return render_template(
        "admin/bot/candidatas_creadas.html",
        rows=rows,
        metrics=metrics,
    )


@admin_bp.route("/bot/candidate-intake", methods=["GET"])
@staff_required
def bot_candidate_intake_queue():
    return render_template("admin/bot/candidate_intake.html")


@admin_bp.route("/bot/candidate-intake/pending.json", methods=["GET"])
@staff_required
def bot_candidate_intake_pending_json():
    drafts = (
        BotCandidateDraft.query.filter(BotCandidateDraft.protocol_version == "interview_flow_v1")
        .order_by(BotCandidateDraft.updated_at.desc(), BotCandidateDraft.id.desc())
        .limit(250)
        .all()
    )
    items = []
    for draft in drafts:
        conv = BotConversation.query.get(int(draft.conversation_id))
        intake = ensure_intake_fields(draft, conv)
        mapped = map_draft_to_candidate_fields(draft)
        badge = str(intake.get("status") or INTAKE_PENDING_REVIEW)
        if badge == INTAKE_PENDING_REVIEW and not bool(as_dict(as_dict(getattr(conv, "metadata_json", {}) or {}).get("interview_flow")).get("completed")):
            badge = INTAKE_INCOMPLETE
        items.append(
            {
                "intake_id": int(draft.id),
                "conversation_id": int(draft.conversation_id),
                "name": str(mapped.get("nombre_completo") or ""),
                "phone": str(mapped.get("numero_telefono") or ""),
                "age": str(mapped.get("edad") or ""),
                "city_sector": ", ".join([x for x in [str(mapped.get("ciudad") or ""), str(mapped.get("sector") or "")] if x]),
                "availability": str(mapped.get("modalidad_trabajo_preferida") or ""),
                "created_at": str(draft.created_at or ""),
                "status": badge,
                "quality_score": int(intake.get("quality_score") or 0),
                "origin": "whatsapp_bot",
                "duplicates_count": len(list(intake.get("duplicates") or [])),
            }
        )
    db.session.commit()
    return jsonify({"ok": True, "items": items})


@admin_bp.route("/bot/candidate-intake/<int:intake_id>.json", methods=["GET"])
@staff_required
def bot_candidate_intake_detail_json(intake_id: int):
    draft = BotCandidateDraft.query.get(int(intake_id))
    if not draft:
        return jsonify({"ok": False, "error": "intake_not_found"}), 404
    conv = BotConversation.query.get(int(draft.conversation_id))
    intake = ensure_intake_fields(draft, conv)
    flow = as_dict(as_dict(getattr(conv, "metadata_json", {}) or {}).get("interview_flow"))
    decisions = (
        BotDecisionLog.query.filter_by(conversation_id=int(draft.conversation_id))
        .order_by(BotDecisionLog.created_at.desc(), BotDecisionLog.id.desc())
        .limit(80)
        .all()
    )
    msgs = (
        BotMessage.query.filter_by(conversation_id=int(draft.conversation_id))
        .order_by(BotMessage.created_at.asc(), BotMessage.id.asc())
        .all()
    )
    mapped = map_draft_to_candidate_fields(draft)
    db.session.commit()
    return jsonify(
        {
            "ok": True,
            "intake": {
                "id": int(draft.id),
                "conversation_id": int(draft.conversation_id),
                "status": str(intake.get("status") or INTAKE_PENDING_REVIEW),
                "quality_score": int(intake.get("quality_score") or 0),
                "quality_flags": list(intake.get("quality_flags") or []),
                "duplicates": list(intake.get("duplicates") or []),
                "invalid_answers_count": int(intake.get("invalid_answers_count") or 0),
                "mapped_fields": mapped,
                "detected_future_data": as_dict(flow.get("detected_future_data")),
                "collected_data": as_dict(flow.get("collected_data")),
                "summary": str(flow.get("summary") or ""),
                "completed": bool(flow.get("completed")),
                "ai_logs": [
                    {
                        "id": int(x.id),
                        "decision_type": str(x.decision_type or ""),
                        "decision_result": str(x.decision_result or ""),
                        "created_at": str(x.created_at or ""),
                    }
                    for x in decisions
                    if str(x.decision_type or "").startswith("ai_") or str(x.decision_type or "").startswith("interview_")
                ],
                "messages": [
                    {
                        "id": int(x.id),
                        "direction": str(x.direction or ""),
                        "text": str(x.text_body or ""),
                        "created_at": str(x.created_at or ""),
                    }
                    for x in msgs
                ],
            },
        }
    )


@admin_bp.route("/bot/candidate-intake/<int:intake_id>/action", methods=["POST"])
@staff_required
def bot_candidate_intake_action(intake_id: int):
    limited = _rate_limit_or_redirect("bot_candidate_intake_action", redirect_endpoint="admin.bot_candidate_intake_queue")
    if limited:
        return jsonify({"ok": False, "error": "rate_limited"}), 429
    draft = BotCandidateDraft.query.get(int(intake_id))
    if not draft:
        return jsonify({"ok": False, "error": "intake_not_found"}), 404
    conv = BotConversation.query.get(int(draft.conversation_id))
    payload = request.get_json(silent=True) or {}
    action = str(payload.get("action") or "").strip().lower()
    actor_id = _protocol_actor_id()
    try:
        ensure_intake_fields(draft, conv)
        if action == "reject":
            set_intake_status(draft, status=INTAKE_REJECTED, actor_id=actor_id, note=str(payload.get("note") or ""))
        elif action == "mark_duplicate":
            set_intake_status(draft, status=INTAKE_DUPLICATE, actor_id=actor_id, note=str(payload.get("note") or ""))
        elif action == "followup":
            set_intake_status(draft, status=INTAKE_NEEDS_FOLLOWUP, actor_id=actor_id, note=str(payload.get("note") or ""))
        elif action == "edit_before_approve":
            edit_intake_before_approve(draft, as_dict(payload.get("fields")))
            ensure_intake_fields(draft, conv)
            set_intake_status(draft, status=INTAKE_PENDING_REVIEW, actor_id=actor_id, note="edited_before_approve")
        elif action == "approve":
            set_intake_status(draft, status=INTAKE_APPROVED, actor_id=actor_id, note=str(payload.get("note") or ""))
            candidate_id = convert_intake_to_candidate(draft, actor_id=actor_id, review_id=int(draft.id))
            log_bot_event(
                "candidate_intake_approved",
                metadata={"draft_id": int(draft.id), "conversation_id": int(draft.conversation_id), "candidate_id": int(candidate_id)},
            )
            db.session.commit()
            return jsonify({"ok": True, "candidate_id": int(candidate_id), "status": INTAKE_APPROVED})
        else:
            return jsonify({"ok": False, "error": "invalid_action"}), 400
        db.session.commit()
        return jsonify({"ok": True, "status": _safe_intake(draft)["status"]})
    except ValueError as exc:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception:
        db.session.rollback()
        return jsonify({"ok": False, "error": "action_failed"}), 500


@admin_bp.route("/bot/candidate-intake/metrics.json", methods=["GET"])
@staff_required
def bot_candidate_intake_metrics_json():
    drafts = BotCandidateDraft.query.filter(BotCandidateDraft.protocol_version == "interview_flow_v1").all()
    today = utc_now_naive().date()
    completed_today = 0
    approved = 0
    rejected = 0
    duplicated = 0
    score_sum = 0
    score_count = 0
    avg_duration_min = 0.0
    durations: list[float] = []
    for draft in drafts:
        conv = BotConversation.query.get(int(draft.conversation_id))
        intake = ensure_intake_fields(draft, conv)
        status = str(intake.get("status") or INTAKE_PENDING_REVIEW)
        if status == INTAKE_APPROVED:
            approved += 1
        elif status == INTAKE_REJECTED:
            rejected += 1
        elif status == INTAKE_DUPLICATE:
            duplicated += 1
        score_sum += int(intake.get("quality_score") or 0)
        score_count += 1
        flow = as_dict(as_dict(getattr(conv, "metadata_json", {}) or {}).get("interview_flow"))
        if bool(flow.get("completed")) and getattr(draft, "created_at", None) and draft.created_at.date() == today:
            completed_today += 1
        started = getattr(conv, "created_at", None)
        ended = getattr(draft, "created_at", None)
        if started and ended:
            durations.append(max(0.0, (ended - started).total_seconds() / 60.0))
    if durations:
        avg_duration_min = sum(durations) / len(durations)
    db.session.commit()
    return jsonify(
        {
            "ok": True,
            "completed_today": completed_today,
            "approved": approved,
            "rejected": rejected,
            "duplicated": duplicated,
            "avg_score": round((score_sum / score_count), 2) if score_count else 0,
            "avg_interview_minutes": round(avg_duration_min, 2),
        }
    )


@admin_bp.route("/bot/conversaciones/<int:conversation_id>", methods=["GET"])
@staff_required
def bot_conversation_detail(conversation_id: int):
    conversation = get_conversation_by_id(conversation_id)
    if not conversation:
        flash("Conversación no encontrada.", "warning")
        return redirect(url_for("admin.bot_conversations_list"))

    messages = (
        BotMessage.query.filter_by(conversation_id=conversation.id)
        .order_by(BotMessage.created_at.asc(), BotMessage.id.asc())
        .all()
    )
    decisions = (
        BotDecisionLog.query.filter_by(conversation_id=conversation.id)
        .order_by(BotDecisionLog.created_at.desc(), BotDecisionLog.id.desc())
        .limit(50)
        .all()
    )
    ai_suggestion_by_message_id = {}
    ai_decisions_by_message_id: dict[int, list[BotDecisionLog]] = {}
    protocol_auto_decision_by_message_id = {}
    for d in decisions:
        msg_id = int(d.message_id or 0)
        if msg_id <= 0:
            continue
        ai_decisions_by_message_id.setdefault(msg_id, []).append(d)
        if d.decision_type in {"protocol_auto_advance", "protocol_pending_correction"}:
            protocol_auto_decision_by_message_id[msg_id] = d
        if d.decision_type not in {"ai_classification", "auto_reply"}:
            continue
        if not isinstance(d.facts_json, dict):
            continue
        if not d.facts_json.get("intent"):
            continue
        if msg_id in ai_suggestion_by_message_id:
            continue
        ai_suggestion_by_message_id[msg_id] = d
    protocol_state = get_protocol_state(conversation)
    pending_corrections = _normalize_pending_corrections((conversation.metadata_json or {}).get("pending_corrections") or [])
    pending_corrections_pending = [x for x in pending_corrections if str(x.get("status") or "") == "pending_human"]
    pending_corrections_approved = [x for x in pending_corrections if str(x.get("status") or "") == "approved"]
    pending_corrections_rejected = [x for x in pending_corrections if str(x.get("status") or "") == "rejected"]
    pending_corrections_superseded = [x for x in pending_corrections if str(x.get("status") or "") == "superseded"]
    current_step_code = protocol_state.get("current_step_code") or ""
    try:
        protocol_payload = load_protocol()
        protocol_current_step = get_step(current_step_code)
        protocol_next_step = get_next_step(current_step_code)
        protocol_step_prompt = build_step_prompt(current_step_code)
        protocol_steps = protocol_payload.get("steps") or []
    except Exception:
        protocol_current_step = None
        protocol_next_step = None
        protocol_step_prompt = "Protocolo no disponible temporalmente."
        protocol_steps = []
    candidate_summary = build_candidate_summary(conversation)
    candidate_summary_missing_fields = get_missing_required_candidate_fields(conversation)
    candidate_summary_status = get_candidate_summary_status(conversation)
    candidate_draft = get_candidate_draft(int(conversation.id))
    interview_flow_state = dict((conversation.metadata_json or {}).get("interview_flow") or {})
    candidate_draft_can_create = can_create_candidate_draft(conversation)
    candidate_conversion_preview = build_candidate_conversion_preview(candidate_draft) if candidate_draft else None
    candidate_creation_validation = validate_candidate_creation(candidate_draft) if candidate_draft else None
    candidate_creation_guardrails = evaluate_real_creation_guardrails()

    return render_template(
        "admin/bot/detalle_conversacion.html",
        conversation=conversation,
        messages=messages,
        ai_suggestion_by_message_id=ai_suggestion_by_message_id,
        ai_decisions_by_message_id=ai_decisions_by_message_id,
        protocol_auto_decision_by_message_id=protocol_auto_decision_by_message_id,
        bot_dry_run=is_bot_dry_run(),
        whatsapp_enabled=is_whatsapp_enabled(),
        bot_ai_enabled=is_ai_enabled(),
        bot_autoreply_enabled=is_autoreply_enabled(),
        bot_protocol_auto_advance_enabled=is_protocol_auto_advance_enabled(),
        ai_daily_usage=get_ai_daily_usage_summary(),
        protocol_state=protocol_state,
        protocol_current_step=protocol_current_step,
        protocol_next_step=protocol_next_step,
        protocol_step_prompt=protocol_step_prompt,
        protocol_steps=protocol_steps,
        pending_corrections=pending_corrections,
        pending_corrections_pending=pending_corrections_pending,
        pending_corrections_approved=pending_corrections_approved,
        pending_corrections_rejected=pending_corrections_rejected,
        pending_corrections_superseded=pending_corrections_superseded,
        candidate_summary=candidate_summary,
        candidate_summary_missing_fields=candidate_summary_missing_fields,
        candidate_summary_status=candidate_summary_status,
        interview_flow_state=interview_flow_state,
        candidate_draft=candidate_draft,
        candidate_draft_can_create=candidate_draft_can_create,
        candidate_conversion_preview=candidate_conversion_preview,
        candidate_creation_validation=candidate_creation_validation,
        candidate_creation_guardrails=candidate_creation_guardrails,
        candidate_real_confirm=bool((request.args.get("confirm_real_creation") or "").strip() == "1"),
        bot_safety=get_sensitive_flags_snapshot(),
    )


def _protocol_actor_id() -> int | None:
    try:
        return int(getattr(current_user, "id", 0) or 0) or None
    except Exception:
        return None


def _actor_rate_key() -> str:
    return str(_protocol_actor_id() or "anon")


def _rate_limit_or_redirect(action_key: str, *, redirect_endpoint: str, conversation_id: int | None = None):
    allowed, retry_after = allow_action(actor_key=_actor_rate_key(), action_key=action_key)
    if allowed:
        return None
    log_bot_blocked(
        "rate_limited",
        reason="rate_limit_soft",
        metadata={"action_key": action_key, "retry_after_seconds": retry_after, "actor_id": _protocol_actor_id()},
    )
    flash(f"Acción limitada temporalmente. Intenta en {retry_after}s.", "warning")
    if conversation_id is not None:
        return redirect(url_for(redirect_endpoint, conversation_id=conversation_id))
    return redirect(url_for(redirect_endpoint))


@admin_bp.route("/bot/conversaciones/<int:conversation_id>/correcciones/<int:correction_id>/aprobar", methods=["POST"])
@staff_required
def bot_protocol_correction_approve(conversation_id: int, correction_id: int):
    limited = _rate_limit_or_redirect(
        "bot_protocol_correction_approve",
        redirect_endpoint="admin.bot_conversation_detail",
        conversation_id=conversation_id,
    )
    if limited:
        return limited
    conversation = get_conversation_by_id(conversation_id)
    if not conversation:
        flash("Conversación no encontrada.", "warning")
        return redirect(url_for("admin.bot_conversations_list"))
    try:
        with bot_timing("bot_protocol_correction_approve", metadata={"conversation_id": conversation_id, "correction_id": correction_id}):
            metadata = dict(getattr(conversation, "metadata_json", {}) or {})
            metadata, approved_item = approve_pending_correction(metadata, correction_id, _protocol_actor_id())
            conversation.metadata_json = metadata
            register_decision(
                conversation=conversation,
                decision_type=DECISION_TYPE_PROTOCOL_CORRECTION_APPROVED,
                decision_result=DECISION_RESULT_MANUAL_ONLY,
                rule_code="PROTOCOL_CORRECTION_APPROVED_MANUAL",
                reason_human="Corrección pendiente aprobada manualmente por staff",
                facts_json={
                    "correction_id": approved_item.get("id"),
                    "field": approved_item.get("field"),
                    "old_value": approved_item.get("old_value"),
                    "new_value": approved_item.get("new_value"),
                    "approved_by": approved_item.get("approved_by"),
                },
                autocommit=False,
            )
            db.session.commit()
        flash("Corrección aprobada y aplicada a entidades del protocolo.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "warning")
    except Exception:
        db.session.rollback()
        flash("No se pudo aprobar la corrección.", "warning")
    return redirect(url_for("admin.bot_conversation_detail", conversation_id=conversation.id))


@admin_bp.route("/bot/conversaciones/<int:conversation_id>/correcciones/<int:correction_id>/rechazar", methods=["POST"])
@staff_required
def bot_protocol_correction_reject(conversation_id: int, correction_id: int):
    limited = _rate_limit_or_redirect(
        "bot_protocol_correction_reject",
        redirect_endpoint="admin.bot_conversation_detail",
        conversation_id=conversation_id,
    )
    if limited:
        return limited
    conversation = get_conversation_by_id(conversation_id)
    if not conversation:
        flash("Conversación no encontrada.", "warning")
        return redirect(url_for("admin.bot_conversations_list"))
    rejection_reason = (request.form.get("rejection_reason") or "").strip()
    try:
        with bot_timing("bot_protocol_correction_reject", metadata={"conversation_id": conversation_id, "correction_id": correction_id}):
            metadata = dict(getattr(conversation, "metadata_json", {}) or {})
            metadata, rejected_item = reject_pending_correction(metadata, correction_id, _protocol_actor_id(), rejection_reason)
            conversation.metadata_json = metadata
            register_decision(
                conversation=conversation,
                decision_type=DECISION_TYPE_PROTOCOL_CORRECTION_REJECTED,
                decision_result=DECISION_RESULT_MANUAL_ONLY,
                rule_code="PROTOCOL_CORRECTION_REJECTED_MANUAL",
                reason_human="Corrección pendiente rechazada manualmente por staff",
                facts_json={
                    "correction_id": rejected_item.get("id"),
                    "field": rejected_item.get("field"),
                    "old_value": rejected_item.get("old_value"),
                    "new_value": rejected_item.get("new_value"),
                    "rejected_by": rejected_item.get("rejected_by"),
                    "rejection_reason": rejected_item.get("rejection_reason"),
                },
                autocommit=False,
            )
            db.session.commit()
        flash("Corrección rechazada.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "warning")
    except Exception:
        db.session.rollback()
        flash("No se pudo rechazar la corrección.", "warning")
    return redirect(url_for("admin.bot_conversation_detail", conversation_id=conversation.id))


@admin_bp.route("/bot/conversaciones/<int:conversation_id>/protocolo/completar", methods=["POST"])
@staff_required
def bot_protocol_mark_completed(conversation_id: int):
    conversation = get_conversation_by_id(conversation_id)
    if not conversation:
        flash("Conversación no encontrada.", "warning")
        return redirect(url_for("admin.bot_conversations_list"))
    try:
        complete_current_protocol_step(conversation, actor_id=_protocol_actor_id())
        flash("Etapa marcada como completada (manual).", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "warning")
    except Exception:
        db.session.rollback()
        flash("No se pudo completar etapa. Protocolo no disponible.", "warning")
    return redirect(url_for("admin.bot_conversation_detail", conversation_id=conversation.id))


@admin_bp.route("/bot/conversaciones/<int:conversation_id>/protocolo/avanzar", methods=["POST"])
@staff_required
def bot_protocol_advance(conversation_id: int):
    conversation = get_conversation_by_id(conversation_id)
    if not conversation:
        flash("Conversación no encontrada.", "warning")
        return redirect(url_for("admin.bot_conversations_list"))
    try:
        advance_protocol_step(conversation, actor_id=_protocol_actor_id())
        flash("Etapa avanzada manualmente.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "warning")
    except Exception:
        db.session.rollback()
        flash("No se pudo avanzar etapa. Protocolo no disponible.", "warning")
    return redirect(url_for("admin.bot_conversation_detail", conversation_id=conversation.id))


@admin_bp.route("/bot/conversaciones/<int:conversation_id>/protocolo/retroceder", methods=["POST"])
@staff_required
def bot_protocol_regress(conversation_id: int):
    conversation = get_conversation_by_id(conversation_id)
    if not conversation:
        flash("Conversación no encontrada.", "warning")
        return redirect(url_for("admin.bot_conversations_list"))
    try:
        regress_protocol_step(conversation, actor_id=_protocol_actor_id())
        flash("Etapa retrocedida manualmente.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "warning")
    except Exception:
        db.session.rollback()
        flash("No se pudo retroceder etapa. Protocolo no disponible.", "warning")
    return redirect(url_for("admin.bot_conversation_detail", conversation_id=conversation.id))


@admin_bp.route("/bot/conversaciones/<int:conversation_id>/protocolo/seleccionar", methods=["POST"])
@staff_required
def bot_protocol_select(conversation_id: int):
    conversation = get_conversation_by_id(conversation_id)
    if not conversation:
        flash("Conversación no encontrada.", "warning")
        return redirect(url_for("admin.bot_conversations_list"))
    selected_step = (request.form.get("step_code") or "").strip().upper()
    try:
        select_protocol_step(conversation, step_code=selected_step, actor_id=_protocol_actor_id())
        flash(f"Etapa actualizada manualmente a {selected_step}.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "warning")
    except Exception:
        db.session.rollback()
        flash("No se pudo seleccionar etapa. Protocolo no disponible.", "warning")
    return redirect(url_for("admin.bot_conversation_detail", conversation_id=conversation.id))


@admin_bp.route("/bot/conversaciones/<int:conversation_id>/protocolo/reiniciar", methods=["POST"])
@staff_required
def bot_protocol_reset(conversation_id: int):
    conversation = get_conversation_by_id(conversation_id)
    if not conversation:
        flash("Conversación no encontrada.", "warning")
        return redirect(url_for("admin.bot_conversations_list"))
    confirm = (request.form.get("confirm_reset") or "").strip().upper()
    if confirm != "REINICIAR":
        flash("Confirmación inválida. Escribe REINICIAR para reiniciar protocolo.", "warning")
        return redirect(url_for("admin.bot_conversation_detail", conversation_id=conversation.id))
    try:
        reset_protocol_state(conversation, actor_id=_protocol_actor_id())
        flash("Protocolo reiniciado manualmente.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "warning")
    except Exception:
        db.session.rollback()
        flash("No se pudo reiniciar protocolo. Protocolo no disponible.", "warning")
    return redirect(url_for("admin.bot_conversation_detail", conversation_id=conversation.id))


@admin_bp.route("/bot/conversaciones/<int:conversation_id>/mensaje", methods=["POST"])
@staff_required
def bot_conversation_add_message(conversation_id: int):
    conversation = get_conversation_by_id(conversation_id)
    if not conversation:
        flash("Conversación no encontrada.", "warning")
        return redirect(url_for("admin.bot_conversations_list"))

    body = (request.form.get("body") or "").strip()
    if not body:
        flash("El mensaje está vacío.", "warning")
        return redirect(url_for("admin.bot_conversation_detail", conversation_id=conversation.id))

    enabled = is_whatsapp_enabled()
    dry_run = is_bot_dry_run()
    if is_staging_offline_active():
        msg = create_manual_message(conversation=conversation, text_body=body, status=MESSAGE_STATUS_OUTBOUND_QUEUED)
        try:
            enqueue_sandbox_outbound(conversation=conversation, message=msg, provider="fake")
            flash("Mensaje encolado en sandbox offline.", "success")
        except SandboxSafetyError as exc:
            msg.status = MESSAGE_STATUS_OUTBOUND_FAILED
            msg.error_code = "sandbox_security_block"
            msg.error_message = str(exc)[:255]
            db.session.commit()
            flash(f"Bloqueado por seguridad sandbox: {exc}", "warning")
        return redirect(url_for("admin.bot_conversation_detail", conversation_id=conversation.id))
    if not enabled or dry_run:
        create_manual_message(conversation=conversation, text_body=body, status=MESSAGE_STATUS_OUTBOUND_QUEUED)
        flash("Mensaje manual guardado en simulación (sin envío externo).", "success")
        return redirect(url_for("admin.bot_conversation_detail", conversation_id=conversation.id))

    send_result = send_text_message(conversation.phone_e164, body)
    status = MESSAGE_STATUS_OUTBOUND_SENT if send_result.get("ok") else MESSAGE_STATUS_OUTBOUND_FAILED
    msg = create_manual_message(conversation=conversation, text_body=body, status=status)
    msg.wa_message_id = send_result.get("wa_message_id")
    if not send_result.get("ok"):
        msg.error_code = str(send_result.get("error_code") or "")
        msg.error_message = str(send_result.get("error_message") or "")[:255] or None
    db.session.commit()
    if send_result.get("ok"):
        flash("Mensaje enviado a WhatsApp Cloud API.", "success")
    else:
        flash("Mensaje guardado, pero envío WhatsApp falló.", "warning")
    return redirect(url_for("admin.bot_conversation_detail", conversation_id=conversation.id))


@admin_bp.route("/bot/conversaciones/<int:conversation_id>/simular-inbound", methods=["POST"])
@staff_required
def bot_conversation_simulate_inbound(conversation_id: int):
    conversation = get_conversation_by_id(conversation_id)
    if not conversation:
        flash("Conversación no encontrada.", "warning")
        return redirect(url_for("admin.bot_conversations_list"))

    body = (request.form.get("body") or "").strip()
    if not body:
        flash("El mensaje inbound está vacío.", "warning")
        return redirect(url_for("admin.bot_conversation_detail", conversation_id=conversation.id))

    inbound = create_manual_message(
        conversation=conversation,
        text_body=body,
        direction=MESSAGE_DIRECTION_INBOUND,
        source=MESSAGE_SOURCE_WHATSAPP_USER,
        status=MESSAGE_STATUS_INBOUND_RECEIVED,
    )

    pipeline_result = process_inbound_ai_pipeline(
        conversation=conversation,
        inbound_message=inbound,
        identity_status=(conversation.identity.identity_status if conversation.identity else "unknown"),
        message_type="text",
        phone_e164=str(conversation.phone_e164 or ""),
        allow_autoreply_send=False,
        classify_intent_fn=classify_intent,
        generate_safe_reply_fn=generate_safe_reply,
        is_ai_enabled_fn=is_ai_enabled,
        is_autoreply_enabled_fn=is_autoreply_enabled,
    )
    db.session.commit()
    protocol_auto = (pipeline_result or {}).get("protocol_auto_advance") or {}
    if bool(protocol_auto.get("enabled")) and bool(protocol_auto.get("matched")) and protocol_auto.get("new_step"):
        flash(f"Etapa completada automáticamente en modo local/dry-run. Nueva etapa: {protocol_auto.get('new_step')}", "success")
    elif bool(protocol_auto.get("pending_correction")):
        item = protocol_auto.get("pending_correction_item") or {}
        flash(
            f"Corrección pendiente detectada ({item.get('field') or 'campo'}). Requiere confirmación humana. No se avanzó.",
            "warning",
        )
    elif bool(protocol_auto.get("out_of_step")):
        suggested = str(protocol_auto.get("suggested_step_code") or "N/A")
        flash(f"Respuesta fuera de etapa. Parece corresponder a: {suggested}. No se avanzó automáticamente.", "warning")
    elif bool(protocol_auto.get("enabled")) and bool(protocol_auto.get("requires_human")):
        flash("Auto-avance bloqueado: esta etapa requiere revisión humana.", "warning")
    elif bool(protocol_auto.get("enabled")) and not bool(protocol_auto.get("matched")):
        flash("Auto-avance no aplicado: respuesta inválida para la etapa actual.", "info")

    if not is_ai_enabled():
        flash("Mensaje inbound simulado guardado. IA apagada.", "info")
        return redirect(url_for("admin.bot_conversation_detail", conversation_id=conversation.id))

    if bool((pipeline_result or {}).get("daily_limit_reached")):
        flash("Límite IA alcanzado, requiere humano.", "warning")
        return redirect(url_for("admin.bot_conversation_detail", conversation_id=conversation.id))
    flash("Mensaje inbound simulado procesado con IA en modo sugerencia (manual_only).", "success")
    return redirect(url_for("admin.bot_conversation_detail", conversation_id=conversation.id))


@admin_bp.route("/bot/conversaciones/<int:conversation_id>/pausar", methods=["POST"])
@staff_required
def bot_conversation_pause(conversation_id: int):
    conversation = get_conversation_by_id(conversation_id)
    if not conversation:
        flash("Conversación no encontrada.", "warning")
        return redirect(url_for("admin.bot_conversations_list"))

    reason = (request.form.get("reason") or "").strip() or "Pausada manualmente por staff"
    pause_conversation(conversation, reason=reason)
    flash("Bot pausado para esta conversación.", "success")
    return redirect(url_for("admin.bot_conversation_detail", conversation_id=conversation.id))


@admin_bp.route("/bot/conversaciones/<int:conversation_id>/activar", methods=["POST"])
@staff_required
def bot_conversation_activate(conversation_id: int):
    conversation = get_conversation_by_id(conversation_id)
    if not conversation:
        flash("Conversación no encontrada.", "warning")
        return redirect(url_for("admin.bot_conversations_list"))

    activate_conversation(conversation)
    flash("Bot reactivado para esta conversación.", "success")
    return redirect(url_for("admin.bot_conversation_detail", conversation_id=conversation.id))


@admin_bp.route("/bot/conversaciones/<int:conversation_id>/resolver", methods=["POST"])
@staff_required
def bot_conversation_resolve(conversation_id: int):
    conversation = get_conversation_by_id(conversation_id)
    if not conversation:
        flash("Conversación no encontrada.", "warning")
        return redirect(url_for("admin.bot_conversations_list"))

    resolve_conversation(conversation)
    flash("Conversación marcada como resuelta.", "success")
    return redirect(url_for("admin.bot_conversation_detail", conversation_id=conversation.id))


@admin_bp.route("/bot/configuracion", methods=["GET"])
@staff_required
def bot_settings_view():
    settings = BotSetting.query.order_by(BotSetting.key.asc()).all()
    return render_template("admin/bot/configuracion.html", settings=settings, bot_safety=get_sensitive_flags_snapshot())


@admin_bp.route("/bot/health", methods=["GET"])
@staff_required
def bot_health_view():
    return render_template("admin/bot/health.html", bot_safety=get_sensitive_flags_snapshot())


@admin_bp.route("/bot/conversaciones/nueva", methods=["POST"])
@staff_required
def bot_conversation_create_manual():
    phone_e164 = (request.form.get("phone_e164") or "").strip()
    contact_name = (request.form.get("contact_name") or "").strip() or None
    if not phone_e164:
        flash("Debes indicar teléfono en formato E.164.", "warning")
        return redirect(url_for("admin.bot_conversations_list"))
    if is_staging_offline_active() and not phone_e164.startswith("+1999"):
        flash("En BOT_STAGING_MODE+BOT_SANDBOX_MODE solo se permiten números sandbox (+1999...).", "warning")
        return redirect(url_for("admin.bot_conversations_list"))

    conversation = get_or_create_manual_conversation(phone_e164=phone_e164, contact_name=contact_name)
    if is_staging_offline_active():
        meta = dict(getattr(conversation, "metadata_json", {}) or {})
        meta["sandbox_conversation"] = True
        conversation.metadata_json = meta
        db.session.commit()
    flash("Conversación creada/recuperada para pruebas internas.", "success")
    return redirect(url_for("admin.bot_conversation_detail", conversation_id=conversation.id))


@admin_bp.route("/bot/conversaciones/<int:conversation_id>/candidate-draft/crear", methods=["POST"])
@staff_required
def bot_candidate_draft_create(conversation_id: int):
    limited = _rate_limit_or_redirect(
        "bot_candidate_draft_create",
        redirect_endpoint="admin.bot_conversation_detail",
        conversation_id=conversation_id,
    )
    if limited:
        return limited
    conversation = get_conversation_by_id(conversation_id)
    if not conversation:
        flash("Conversación no encontrada.", "warning")
        return redirect(url_for("admin.bot_conversations_list"))
    try:
        with bot_timing("bot_candidate_draft_create", metadata={"conversation_id": conversation_id}):
            draft = create_candidate_draft(conversation, _protocol_actor_id())
        flash(f"Borrador de candidata creado (#{draft.id}).", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(f"No se puede crear borrador: {str(exc)}", "warning")
    except Exception:
        db.session.rollback()
        flash("No se pudo crear borrador de candidata.", "warning")
    return redirect(url_for("admin.bot_conversation_detail", conversation_id=conversation.id))


@admin_bp.route("/bot/conversaciones/<int:conversation_id>/candidate-draft/revisar", methods=["POST"])
@staff_required
def bot_candidate_draft_mark_under_review(conversation_id: int):
    conversation = get_conversation_by_id(conversation_id)
    if not conversation:
        flash("Conversación no encontrada.", "warning")
        return redirect(url_for("admin.bot_conversations_list"))
    try:
        mark_candidate_draft_under_review(conversation_id, _protocol_actor_id())
        flash("Borrador marcado como bajo revisión.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(f"No se pudo cambiar estado: {str(exc)}", "warning")
    except Exception:
        db.session.rollback()
        flash("No se pudo cambiar estado de borrador.", "warning")
    return redirect(url_for("admin.bot_conversation_detail", conversation_id=conversation.id))


@admin_bp.route("/bot/conversaciones/<int:conversation_id>/candidate-draft/rechazar", methods=["POST"])
@staff_required
def bot_candidate_draft_reject(conversation_id: int):
    conversation = get_conversation_by_id(conversation_id)
    if not conversation:
        flash("Conversación no encontrada.", "warning")
        return redirect(url_for("admin.bot_conversations_list"))
    notes = (request.form.get("notes") or "").strip()
    try:
        reject_candidate_draft(conversation_id, _protocol_actor_id(), notes=notes)
        flash("Borrador rechazado.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(f"No se pudo rechazar borrador: {str(exc)}", "warning")
    except Exception:
        db.session.rollback()
        flash("No se pudo rechazar borrador.", "warning")
    return redirect(url_for("admin.bot_conversation_detail", conversation_id=conversation.id))


@admin_bp.route("/bot/conversaciones/<int:conversation_id>/candidate-draft/preparar-creacion-real", methods=["POST"])
@staff_required
def bot_candidate_draft_prepare_real_creation(conversation_id: int):
    limited = _rate_limit_or_redirect(
        "bot_candidate_draft_prepare_real_creation",
        redirect_endpoint="admin.bot_conversation_detail",
        conversation_id=conversation_id,
    )
    if limited:
        return limited
    conversation = get_conversation_by_id(conversation_id)
    if not conversation:
        flash("Conversación no encontrada.", "warning")
        return redirect(url_for("admin.bot_conversations_list"))
    draft = get_candidate_draft(int(conversation.id))
    if not draft:
        flash("No hay borrador para convertir.", "warning")
        return redirect(url_for("admin.bot_conversation_detail", conversation_id=conversation.id))
    actor_id = _protocol_actor_id()
    validation = validate_candidate_creation(draft)
    guardrails = evaluate_real_creation_guardrails()
    log_action(
        action_type="candidate_real_creation_started",
        entity_type="BotCandidateDraft",
        entity_id=str(draft.id),
        summary="Inicio preparación creación candidata real",
        metadata={
            "actor_id": actor_id,
            "conversation_id": int(conversation.id),
            "draft_id": int(draft.id),
            "blocking_conflicts": validation.get("blocking_conflicts") or [],
            "warning_conflicts": validation.get("warning_conflicts") or [],
            "requires_human": True,
            "normalized_phone": (validation.get("payload") or {}).get("normalized_phone"),
            "source": "bot_draft",
            "guardrails": guardrails,
        },
        actor_user_id=actor_id,
        success=True,
    )
    if (not guardrails.get("allowed")) or validation.get("blocking_conflicts"):
        blocking_extra = []
        if not guardrails.get("allowed"):
            blocking_extra.append({"type": "guardrails_blocked", "guardrails": guardrails})
        log_action(
            action_type="candidate_real_creation_blocked",
            entity_type="BotCandidateDraft",
            entity_id=str(draft.id),
            summary="Creación candidata real bloqueada",
            metadata={
                "actor_id": actor_id,
                "conversation_id": int(conversation.id),
                "draft_id": int(draft.id),
                "blocking_conflicts": (validation.get("blocking_conflicts") or []) + blocking_extra,
                "warning_conflicts": validation.get("warning_conflicts") or [],
                "requires_human": True,
                "normalized_phone": (validation.get("payload") or {}).get("normalized_phone"),
                "source": "bot_draft",
                "guardrails": guardrails,
            },
            actor_user_id=actor_id,
            success=False,
            error=("blocked_guardrails" if not guardrails.get("allowed") else "blocked_conflicts"),
        )
        flash("Creación bloqueada por guard rails/configuración." if not guardrails.get("allowed") else "Creación bloqueada por conflictos.", "warning")
        return redirect(url_for("admin.bot_conversation_detail", conversation_id=conversation.id))
    draft.draft_status = "approved_for_creation"
    draft.reviewed_by = actor_id
    draft.reviewed_at = utc_now_naive()
    draft.updated_at = utc_now_naive()
    db.session.commit()
    flash("Preparación lista. Debes confirmar creación real manualmente.", "warning")
    return redirect(url_for("admin.bot_conversation_detail", conversation_id=conversation.id, confirm_real_creation=1))


@admin_bp.route("/bot/conversaciones/<int:conversation_id>/candidate-draft/crear-real", methods=["POST"])
@staff_required
def bot_candidate_draft_create_real(conversation_id: int):
    limited = _rate_limit_or_redirect(
        "bot_candidate_draft_create_real",
        redirect_endpoint="admin.bot_conversation_detail",
        conversation_id=conversation_id,
    )
    if limited:
        return limited
    conversation = get_conversation_by_id(conversation_id)
    if not conversation:
        flash("Conversación no encontrada.", "warning")
        return redirect(url_for("admin.bot_conversations_list"))
    draft = get_candidate_draft(int(conversation.id))
    if not draft:
        flash("No hay borrador para convertir.", "warning")
        return redirect(url_for("admin.bot_conversation_detail", conversation_id=conversation.id))
    if str(request.form.get("confirm_reviewed") or "") != "on":
        flash("Debes confirmar que revisaste la información.", "warning")
        return redirect(url_for("admin.bot_conversation_detail", conversation_id=conversation.id, confirm_real_creation=1))
    actor_id = _protocol_actor_id()
    guardrails = evaluate_real_creation_guardrails()
    try:
        with bot_timing("bot_candidate_draft_create_real", metadata={"conversation_id": conversation_id, "draft_id": int(draft.id)}):
            candidate = create_candidate_from_draft(draft, actor_id=actor_id)
        db.session.commit()
        v = validate_candidate_creation(draft)
        log_action(
            action_type="candidate_real_created",
            entity_type="Candidata",
            entity_id=str(candidate.fila),
            summary="Candidata real creada desde borrador bot",
            metadata={
                "actor_id": actor_id,
                "conversation_id": int(conversation.id),
                "draft_id": int(draft.id),
                "candidata_id": int(candidate.fila),
                "blocking_conflicts": v.get("blocking_conflicts") or [],
                "warning_conflicts": v.get("warning_conflicts") or [],
                "requires_human": True,
                "normalized_phone": (v.get("payload") or {}).get("normalized_phone"),
                "source": "bot_draft",
                "guardrails": guardrails,
            },
            actor_user_id=actor_id,
            success=True,
        )
        flash(f"Candidata REAL creada: #{candidate.fila}", "success")
    except ValueError:
        db.session.rollback()
        v = validate_candidate_creation(draft)
        log_action(
            action_type="candidate_real_creation_blocked",
            entity_type="BotCandidateDraft",
            entity_id=str(draft.id),
            summary="Creación candidata real bloqueada",
            metadata={
                "actor_id": actor_id,
                "conversation_id": int(conversation.id),
                "draft_id": int(draft.id),
                "blocking_conflicts": v.get("blocking_conflicts") or [],
                "warning_conflicts": v.get("warning_conflicts") or [],
                "requires_human": True,
                "normalized_phone": (v.get("payload") or {}).get("normalized_phone"),
                "source": "bot_draft",
                "guardrails": guardrails,
            },
            actor_user_id=actor_id,
            success=False,
            error=("blocked_guardrails" if not guardrails.get("allowed") else "blocked_conflicts"),
        )
        flash("Creación real bloqueada por guard rails/configuración." if not guardrails.get("allowed") else "Creación real bloqueada por validaciones/conflictos.", "warning")
    except Exception as exc:
        db.session.rollback()
        log_action(
            action_type="candidate_real_creation_failed",
            entity_type="BotCandidateDraft",
            entity_id=str(draft.id),
            summary="Error al crear candidata real",
            metadata={
                "actor_id": actor_id,
                "conversation_id": int(conversation.id),
                "draft_id": int(draft.id),
                "requires_human": True,
                "source": "bot_draft",
            },
            actor_user_id=actor_id,
            success=False,
            error=str(exc),
        )
        flash("No se pudo crear candidata real.", "warning")
    return redirect(url_for("admin.bot_conversation_detail", conversation_id=conversation.id))


@admin_bp.route("/bot/candidatas-creadas/<int:candidata_id>/review/take", methods=["POST"])
@staff_required
def bot_created_candidate_review_take(candidata_id: int):
    limited = _rate_limit_or_redirect("bot_created_candidate_review_take", redirect_endpoint="admin.bot_created_candidates_list")
    if limited:
        return limited
    candidata, draft = _get_bot_review_target_locked(candidata_id)
    if not candidata or not draft:
        flash("Candidata creada desde bot no encontrada.", "warning")
        return redirect(url_for("admin.bot_created_candidates_list"))
    actor_id = _protocol_actor_id()
    current_state = _get_bot_review_state(draft)
    previous_status = current_state["status"]
    if previous_status != "bot_pending_review":
        _log_bot_review_blocked(
            candidata_id=int(candidata.fila),
            actor_id=actor_id,
            attempted_action="take",
            current_status=previous_status,
            reason="state_changed_or_invalid",
        )
        db.session.rollback()
        flash(f"Transición bloqueada: la revisión cambió a '{previous_status}'. Recarga la página.", "warning")
        return redirect(url_for("admin.bot_created_candidates_list"))
    now_iso = utc_now_naive().isoformat()
    current_state.update(
        {
            "status": "bot_reviewing",
            "reviewer_id": actor_id,
            "review_taken_at": now_iso,
            "approved_at": None,
            "rejected_at": None,
            "rejection_reason": None,
        }
    )
    _set_bot_review_state(draft, current_state)
    draft.reviewed_by = actor_id
    draft.reviewed_at = utc_now_naive()
    db.session.commit()
    log_action(
        action_type="bot_candidate_review_taken",
        entity_type="Candidata",
        entity_id=str(candidata.fila),
        summary="Revisión manual tomada para candidata bot",
        metadata={
            "candidata_id": int(candidata.fila),
            "actor_id": actor_id,
            "previous_status": previous_status,
            "new_status": "bot_reviewing",
        },
        actor_user_id=actor_id,
        success=True,
    )
    flash("Revisión tomada.", "success")
    return redirect(url_for("admin.bot_created_candidates_list"))


@admin_bp.route("/bot/candidatas-creadas/<int:candidata_id>/review/approve", methods=["POST"])
@staff_required
def bot_created_candidate_review_approve(candidata_id: int):
    limited = _rate_limit_or_redirect("bot_created_candidate_review_approve", redirect_endpoint="admin.bot_created_candidates_list")
    if limited:
        return limited
    candidata, draft = _get_bot_review_target_locked(candidata_id)
    if not candidata or not draft:
        flash("Candidata creada desde bot no encontrada.", "warning")
        return redirect(url_for("admin.bot_created_candidates_list"))
    actor_id = _protocol_actor_id()
    current_state = _get_bot_review_state(draft)
    previous_status = current_state["status"]
    if previous_status != "bot_reviewing":
        _log_bot_review_blocked(
            candidata_id=int(candidata.fila),
            actor_id=actor_id,
            attempted_action="approve",
            current_status=previous_status,
            reason="state_changed_or_invalid",
        )
        db.session.rollback()
        flash(f"Transición bloqueada: estado actual '{previous_status}'. Recarga la página.", "warning")
        return redirect(url_for("admin.bot_created_candidates_list"))
    now_iso = utc_now_naive().isoformat()
    current_state.update(
        {
            "status": "bot_approved",
            "reviewer_id": actor_id,
            "approved_at": now_iso,
            "rejected_at": None,
            "rejection_reason": None,
        }
    )
    _set_bot_review_state(draft, current_state)
    draft.reviewed_by = actor_id
    draft.reviewed_at = utc_now_naive()
    db.session.commit()
    log_action(
        action_type="bot_candidate_review_approved",
        entity_type="Candidata",
        entity_id=str(candidata.fila),
        summary="Revisión manual aprobada para candidata bot",
        metadata={
            "candidata_id": int(candidata.fila),
            "actor_id": actor_id,
            "previous_status": previous_status,
            "new_status": "bot_approved",
        },
        actor_user_id=actor_id,
        success=True,
    )
    flash("Revisión aprobada (manual_only, sin publicación automática).", "success")
    return redirect(url_for("admin.bot_created_candidates_list"))


@admin_bp.route("/bot/candidatas-creadas/<int:candidata_id>/review/reject", methods=["POST"])
@staff_required
def bot_created_candidate_review_reject(candidata_id: int):
    limited = _rate_limit_or_redirect("bot_created_candidate_review_reject", redirect_endpoint="admin.bot_created_candidates_list")
    if limited:
        return limited
    candidata, draft = _get_bot_review_target_locked(candidata_id)
    if not candidata or not draft:
        flash("Candidata creada desde bot no encontrada.", "warning")
        return redirect(url_for("admin.bot_created_candidates_list"))
    actor_id = _protocol_actor_id()
    current_state = _get_bot_review_state(draft)
    previous_status = current_state["status"]
    if previous_status != "bot_reviewing":
        _log_bot_review_blocked(
            candidata_id=int(candidata.fila),
            actor_id=actor_id,
            attempted_action="reject",
            current_status=previous_status,
            reason="state_changed_or_invalid",
        )
        db.session.rollback()
        flash(f"Transición bloqueada: estado actual '{previous_status}'. Recarga la página.", "warning")
        return redirect(url_for("admin.bot_created_candidates_list"))
    reason = (request.form.get("reason") or "").strip() or None
    now_iso = utc_now_naive().isoformat()
    current_state.update(
        {
            "status": "bot_rejected",
            "reviewer_id": actor_id,
            "rejected_at": now_iso,
            "approved_at": None,
            "rejection_reason": reason,
        }
    )
    _set_bot_review_state(draft, current_state)
    draft.reviewed_by = actor_id
    draft.reviewed_at = utc_now_naive()
    db.session.commit()
    log_action(
        action_type="bot_candidate_review_rejected",
        entity_type="Candidata",
        entity_id=str(candidata.fila),
        summary="Revisión manual rechazada para candidata bot",
        metadata={
            "candidata_id": int(candidata.fila),
            "actor_id": actor_id,
            "previous_status": previous_status,
            "new_status": "bot_rejected",
            "reason": reason,
        },
        actor_user_id=actor_id,
        success=True,
    )
    flash("Revisión rechazada.", "success")
    return redirect(url_for("admin.bot_created_candidates_list"))


def _parse_sandbox_timestamp(raw_value: str | None):
    raw = str(raw_value or "").strip()
    if not raw:
        return None


def _verify_sandbox_signature(*, raw_body: bytes, header_signature: str | None, secret: str | None) -> bool:
    token = str(secret or "").strip()
    provided = str(header_signature or "").strip()
    if not token or not provided:
        return False
    digest = hmac.new(token.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided, digest)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


@admin_bp.route("/bot/sandbox/webhook/inbound", methods=["POST"])
@csrf.exempt
def bot_sandbox_webhook_inbound():
    raw_body = request.get_data(cache=True) or b""
    payload = request.get_json(silent=True) or {}
    if payload == {} and raw_body:
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except Exception:
            payload = None
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "invalid_payload"}), 400

    signature_required = _is_true_env(os.getenv("BOT_SANDBOX_WEBHOOK_SIGNATURE_REQUIRED"))
    if signature_required:
        if not _verify_sandbox_signature(
            raw_body=raw_body,
            header_signature=request.headers.get("X-Sandbox-Signature"),
            secret=os.getenv("BOT_SANDBOX_WEBHOOK_SECRET"),
        ):
            return jsonify({"ok": False, "error": "invalid_or_missing_signature"}), 403
    try:
        assert_staging_offline_security(provider="fake")
    except SandboxSafetyError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 403

    try:
        normalized = normalize_sandbox_webhook_payload(payload)
    except PayloadNormalizationError as exc:
        if str(exc.code) == "sandbox_security_block:real_phone_detected":
            log_bot_blocked("sandbox_inbound", reason="real_phone_detected", metadata={})
            return jsonify({"ok": False, "error": exc.code}), 403
        return jsonify({"ok": False, "error": exc.code}), 400

    existing = BotMessage.query.filter_by(wa_message_id=normalized.message_id).first()
    if existing:
        existing_raw = dict(existing.raw_payload_json or {})
        existing_raw["duplicate_webhook"] = True
        existing.raw_payload_json = existing_raw
        db.session.commit()
        review = BotSandboxReviewQueue.query.filter_by(inbound_message_id=int(existing.id)).first()
        return jsonify(
            {
                "ok": True,
                "idempotent": True,
                "duplicate_webhook": True,
                "conversation_id": int(existing.conversation_id),
                "review_id": int(review.id) if review else None,
            }
        ), 200

    normalized_phone = (
        normalized.from_number
        if str(normalized.from_number or "").startswith("+1999")
        else normalize_phone_to_e164(normalized.from_number, default_country="DO")
    )
    conversation = get_or_create_manual_conversation(
        phone_e164=normalized_phone,
        contact_name=normalized.display_name,
        autocommit=False,
    )
    meta = dict(getattr(conversation, "metadata_json", {}) or {})
    meta["sandbox_conversation"] = True
    conversation.metadata_json = meta
    created_at = _parse_sandbox_timestamp(normalized.timestamp)
    inbound = create_inbound_message(
        conversation=conversation,
        text=normalized.text_body,
        wa_message_id=normalized.message_id,
        payload=payload,
        created_at=created_at,
        message_type=normalized.message_type,
        media_id=normalized.media_id,
        media_mime_type=None,
        requires_human=normalized.requires_human,
    )
    inbound.raw_payload_json = {
        **dict(inbound.raw_payload_json or {}),
        "normalized": {
            "from_number": normalized.from_number,
            "display_name": normalized.display_name,
            "message_id": normalized.message_id,
            "timestamp": normalized.timestamp,
            "message_type": normalized.message_type,
            "text_body": normalized.text_body,
            "media_id": normalized.media_id,
            "requires_human": normalized.requires_human,
            "raw_payload_hash": normalized.raw_payload_hash,
        },
        "duplicate_webhook": False,
    }
    review = create_review_from_inbound(
        conversation=conversation,
        inbound_message=inbound,
        identity_status=str(getattr(conversation.identity, "identity_status", "") or "unknown"),
    )
    auto_reply = {"enabled": False, "sent": False, "blocked_reason": ""}
    if is_sandbox_auto_reply_enabled():
        if is_sandbox_auto_reply_active():
            try:
                auto_approve_review_in_sandbox(review=review)
                stats = run_sandbox_worker_once(batch_size=1, review_id=int(review.id))
                db.session.flush()
                auto_reply = {"enabled": True, "sent": int(stats.get("sent") or 0) == 1, "blocked_reason": ""}
            except Exception as exc:
                auto_reply = {"enabled": True, "sent": False, "blocked_reason": str(exc)}
        else:
            auto_reply = {"enabled": True, "sent": False, "blocked_reason": "auto_reply_guard_blocked"}
    db.session.commit()
    log_bot_event("sandbox_inbound_received", metadata={"conversation_id": conversation.id, "inbound_message_id": inbound.id})
    return jsonify(
        {
            "ok": True,
            "duplicate_webhook": False,
            "conversation_id": int(conversation.id),
            "inbound_message_id": int(inbound.id),
            "review_id": int(review.id),
            "raw_payload_hash": normalized.raw_payload_hash,
            "message_type": normalized.message_type,
            "media_id": normalized.media_id,
            "requires_human": normalized.requires_human,
            "auto_reply": auto_reply,
        }
    ), 200


@admin_bp.route("/bot/sandbox", methods=["GET"])
@staff_required
def bot_sandbox_dashboard():
    recent = BotConversation.query.order_by(BotConversation.updated_at.desc(), BotConversation.id.desc()).limit(300).all()
    conversations = [c for c in recent if bool(dict(getattr(c, "metadata_json", {}) or {}).get("sandbox_conversation", False) is True)][:100]
    outbox = BotSandboxOutbound.query.order_by(BotSandboxOutbound.id.desc()).limit(200).all()
    metrics = sandbox_metrics_snapshot()
    requires_human = (
        BotDecisionLog.query.filter(BotDecisionLog.decision_type == "ai_classification")
        .filter(BotDecisionLog.decision_result == DECISION_RESULT_MANUAL_ONLY)
        .count()
    )
    inbound = (
        BotMessage.query.filter(BotMessage.direction == MESSAGE_DIRECTION_INBOUND)
        .order_by(BotMessage.id.desc())
        .limit(200)
        .all()
    )
    sandbox_inbound = []
    for row in inbound:
        conv = row.conversation
        if conv is None:
            continue
        meta_conv = dict(getattr(conv, "metadata_json", {}) or {})
        if not bool(meta_conv.get("sandbox_conversation", False)):
            continue
        raw = dict(row.raw_payload_json or {})
        normalized = dict(raw.get("normalized") or {})
        msg_type = str(normalized.get("message_type") or row.message_type or "text")
        sandbox_inbound.append(
            {
                "id": int(row.id),
                "conversation_id": int(row.conversation_id),
                "message_type": msg_type,
                "media_id": str(normalized.get("media_id") or row.media_id or ""),
                "requires_human": bool(normalized.get("requires_human", True)),
                "raw_payload_hash": str(normalized.get("raw_payload_hash") or ""),
                "duplicate_webhook": bool(raw.get("duplicate_webhook", False)),
                "wa_message_id": str(row.wa_message_id or ""),
                "text_body": str(row.text_body or ""),
            }
        )
        if len(sandbox_inbound) >= 100:
            break
    return render_template(
        "admin/bot/sandbox_dashboard.html",
        conversations=conversations,
        outbox=outbox,
        sandbox_inbound=sandbox_inbound,
        metrics=metrics,
        requires_human=requires_human,
        offline_active=is_staging_offline_active(),
    )


@admin_bp.route("/bot/sandbox/revision", methods=["GET"])
def bot_sandbox_revision_dashboard():
    pending = (
        BotSandboxReviewQueue.query
        .order_by(BotSandboxReviewQueue.created_at.asc(), BotSandboxReviewQueue.id.asc())
        .limit(200)
        .all()
    )
    return render_template("admin/bot/sandbox_revision.html", pending=pending, offline_active=is_staging_offline_active())


@admin_bp.route("/bot/sandbox/revision/<int:review_id>/approve", methods=["POST"])
def bot_sandbox_revision_approve(review_id: int):
    review = BotSandboxReviewQueue.query.get(int(review_id))
    if review is None:
        flash("Review no encontrado.", "warning")
        return redirect(url_for("admin.bot_sandbox_revision_dashboard"))
    try:
        edited_text = (request.form.get("edited_text") or "").strip() or None
        approve_review(review=review, reviewer_id=int(getattr(current_user, "id", 0) or 0) or None, edited_text=edited_text)
        db.session.commit()
        flash("Review aprobado y encolado a sandbox outbox.", "success")
    except ReviewTransitionError as exc:
        db.session.commit()
        flash(
            f"Transición inválida: {exc.current_status} -> {exc.target_status}. Estado no mutado.",
            "warning",
        )
    except SandboxSafetyError as exc:
        db.session.rollback()
        flash(f"Bloqueado por seguridad: {exc}", "warning")
    return redirect(url_for("admin.bot_sandbox_revision_dashboard"))


@admin_bp.route("/bot/sandbox/revision/<int:review_id>/reject", methods=["POST"])
def bot_sandbox_revision_reject(review_id: int):
    review = BotSandboxReviewQueue.query.get(int(review_id))
    if review is None:
        flash("Review no encontrado.", "warning")
        return redirect(url_for("admin.bot_sandbox_revision_dashboard"))
    reason = (request.form.get("rejection_reason") or "").strip() or "rejected_by_reviewer"
    try:
        reject_review(review=review, reviewer_id=int(getattr(current_user, "id", 0) or 0) or None, reason=reason)
        db.session.commit()
        flash("Review rechazado.", "success")
    except ReviewTransitionError as exc:
        db.session.commit()
        flash(
            f"Transición inválida: {exc.current_status} -> {exc.target_status}. Estado no mutado.",
            "warning",
        )
    return redirect(url_for("admin.bot_sandbox_revision_dashboard"))


@admin_bp.route("/bot/sandbox/worker/run-once", methods=["POST"])
@staff_required
def bot_sandbox_worker_run_once():
    stats = run_sandbox_worker_once(batch_size=int(request.form.get("batch_size") or 20))
    flash(
        "Worker sandbox: "
        f"picked={stats['picked']} sent={stats['sent']} failed={stats['failed']} blocked={stats['blocked']} retried={stats['retried']}",
        "success",
    )
    return redirect(url_for("admin.bot_sandbox_dashboard"))


def _sandbox_assistant_guard() -> tuple[bool, tuple | None]:
    if not is_sandbox_assistant_allowed():
        return False, (jsonify({"ok": False, "error": "sandbox_mode_required"}), 403)
    if is_staging_offline_active():
        try:
            assert_staging_offline_security()
        except SandboxSafetyError as exc:
            return False, (jsonify({"ok": False, "error": str(exc)}), 403)
    return True, None


def _sandbox_assistant_table_guard() -> tuple[bool, tuple | None]:
    try:
        db.session.execute(text("SELECT 1 FROM bot_sandbox_review_queue LIMIT 1"))
        return True, None
    except ProgrammingError as exc:
        db.session.rollback()
        if "bot_sandbox_review_queue" in str(exc).lower():
            return (
                False,
                (
                    jsonify(
                        {
                            "ok": False,
                            "error": "missing_sandbox_review_table",
                            "hint": "Run: venv/bin/flask db upgrade",
                        }
                    ),
                    503,
                ),
            )
        return False, (jsonify({"ok": False, "error": "sandbox_assistant_db_error"}), 500)


def _sandbox_review_to_dict(row: BotSandboxReviewQueue) -> dict:
    inbound = row.inbound_message
    conv = row.conversation
    inbound_type = str((inbound.message_type if inbound else "text") or "text").strip().lower()
    metadata = dict(row.metadata_json or {})
    interview_flow = dict(metadata.get("interview_flow") or {})
    requires_human = bool(metadata.get("requires_human", True))
    events = list(metadata.get("review_events") or [])
    outbound = row.outbound_message
    outbox = None
    outbox_id = None
    if outbound is not None:
        outbox = BotSandboxOutbound.query.filter_by(bot_message_id=int(outbound.id)).first()
        outbox_id = int(outbox.id) if outbox else None
    outbox_payload = dict((outbox.payload_json if outbox else {}) or {})
    outbox_audit = dict(outbox_payload.get("audit") or {})
    delivery = dict(outbox_audit.get("delivery") or {})
    mode = str((dict(outbox_payload.get("metadata") or {})).get("mode") or "offline")
    review_mode = str((dict(row.metadata_json or {}).get("auto_reply") or {}).get("enabled") and "auto" or "manual")
    auto_sent_at = str((dict(row.metadata_json or {}).get("auto_reply") or {}).get("auto_sent_at") or "")
    draft = get_candidate_draft(int(row.conversation_id))
    draft_can_create = bool(draft is None)
    return {
        "id": int(row.id),
        "conversation_id": int(row.conversation_id),
        "conversation_phone": str((conv.phone_e164 if conv else "") or ""),
        "conversation_name": str((conv.contact_name if conv else "") or ""),
        "status": str(row.status or ""),
        "requires_human": requires_human,
        "message_type": inbound_type,
        "is_media": inbound_type in {"audio", "image", "document"},
        "media_id": str((inbound.media_id if inbound else "") or ""),
        "inbound_text": str((inbound.text_body if inbound else "") or ""),
        "base_protocol_reply": str(row.base_suggested_reply or ""),
        "ai_reply": str(row.ai_suggested_reply or ""),
        "final_suggested_reply": str(row.final_suggested_reply or ""),
        "safety_status": str(row.safety_status or "pending"),
        "fallback_reason": str(row.fallback_reason or ""),
        "validation_error": str(interview_flow.get("validation_error") or ""),
        "last_invalid_answer": str(interview_flow.get("last_invalid_answer") or ""),
        "interview_collected_data": dict(interview_flow.get("collected_data") or {}),
        "interview_detected_future_data": dict(interview_flow.get("detected_future_data") or {}),
        "rejection_reason": str(row.rejection_reason or ""),
        "edited_text": str(row.edited_text or ""),
        "reviewed_at": str(row.reviewed_at or ""),
        "review_mode": review_mode,
        "auto_sent_at": auto_sent_at,
        "created_at": str(row.created_at or ""),
        "events": events[-10:],
        "outbound_state": str((outbox.state if outbox else "queued") or "queued"),
        "outbound_provider": str((outbox.provider if outbox else "fake") or "fake"),
        "outbound_mode": mode,
        "outbound_phone_masked": ("***" + str((conv.phone_e164 if conv else "") or "")[-4:]) if conv else "",
        "outbound_failure_reason": str((outbox.failure_reason if outbox else "") or ""),
        "delivery_status": str(delivery.get("status") or "queued"),
        "last_webhook": str(delivery.get("last_webhook") or ""),
        "provider_message_id": str(outbox_audit.get("provider_message_id") or ""),
        "outbox_id": outbox_id,
        "wamid": str(outbox_audit.get("provider_message_id") or ""),
        "sent": bool((outbox is not None and str(outbox.state or "") == "simulated_sent") or (outbound is not None and outbound.sent_at is not None)),
        "can_send_real": bool(str(row.status or "") in {"approved", "edited"} and not ((outbox is not None and str(outbox.state or "") == "simulated_sent") or (outbound is not None and outbound.sent_at is not None))),
        "provider_response_summary": str((dict(outbox_audit.get("response_payload") or {}).get("error_message") or outbox.failure_reason if outbox else "") or ""),
        "last_http_status": (outbox.outbound_http_status if outbox else None),
        "meta_error_code": str((outbox.outbound_meta_error_code if outbox else "") or ""),
        "meta_error_message": str((outbox.outbound_meta_error_message if outbox else "") or ""),
        "draft_candidate_created": bool(draft is not None),
        "draft_candidate_id": int(draft.id) if draft is not None else None,
        "draft_candidate_can_create": draft_can_create,
    }


@admin_bp.route("/bot/sandbox/asistente", methods=["GET"])
def bot_sandbox_assistant():
    role = str(session.get("role") or "").strip().lower()
    if role in {"secre", "secretary", "secretaría"}:
        role = "secretaria"
    if role not in {"owner", "admin", "secretaria"}:
        flash("Debes iniciar sesión.", "warning")
        return redirect(url_for("admin.login", next=(request.full_path or request.path)))
    guard_ok, guard_resp = _sandbox_assistant_guard()
    if not guard_ok:
        return guard_resp
    table_ok, table_resp = _sandbox_assistant_table_guard()
    if not table_ok:
        return table_resp
    pending = (
        BotSandboxReviewQueue.query
        .filter(BotSandboxReviewQueue.status.in_(["pending_review", "edited", "approved"]))
        .order_by(BotSandboxReviewQueue.created_at.asc(), BotSandboxReviewQueue.id.asc())
        .limit(200)
        .all()
    )
    selected_id = request.args.get("review_id", type=int)
    selected = None
    if selected_id:
        selected = BotSandboxReviewQueue.query.get(int(selected_id))
    if selected is None and pending:
        selected = pending[0]
    return render_template(
        "admin/bot/sandbox_asistente.html",
        pending=pending,
        selected=selected,
        metrics=sandbox_metrics_snapshot(),
        offline_active=is_staging_offline_active(),
        real_owner_only_active=is_real_whatsapp_sandbox_owner_only_active(),
        realtime_send_mode="real_sandbox" if is_real_whatsapp_sandbox_owner_only_active() else "fake",
        safety=get_sensitive_flags_snapshot(),
        auto_reply_enabled=is_sandbox_auto_reply_enabled(),
        auto_reply_active=is_sandbox_auto_reply_active(),
        auto_reply_paused=is_sandbox_auto_reply_paused(),
    )


@admin_bp.route("/bot/sandbox/asistente/pending.json", methods=["GET"])
@roles_required("admin", "secretaria")
def bot_sandbox_assistant_pending_json():
    guard_ok, guard_resp = _sandbox_assistant_guard()
    if not guard_ok:
        return guard_resp
    table_ok, table_resp = _sandbox_assistant_table_guard()
    if not table_ok:
        return table_resp
    rows = (
        BotSandboxReviewQueue.query
        .filter(BotSandboxReviewQueue.status.in_(["pending_review", "edited", "approved"]))
        .order_by(BotSandboxReviewQueue.created_at.asc(), BotSandboxReviewQueue.id.asc())
        .limit(200)
        .all()
    )
    return jsonify({"ok": True, "items": [_sandbox_review_to_dict(r) for r in rows]}), 200


@admin_bp.route("/bot/sandbox/asistente/review/<int:review_id>.json", methods=["GET"])
@roles_required("admin", "secretaria")
def bot_sandbox_assistant_review_json(review_id: int):
    guard_ok, guard_resp = _sandbox_assistant_guard()
    if not guard_ok:
        return guard_resp
    table_ok, table_resp = _sandbox_assistant_table_guard()
    if not table_ok:
        return table_resp
    review = BotSandboxReviewQueue.query.get(int(review_id))
    if review is None:
        return jsonify({"ok": False, "error": "review_not_found"}), 404
    conv = review.conversation
    messages = []
    if conv is not None:
        rows = (
            BotMessage.query.filter_by(conversation_id=int(conv.id))
            .order_by(BotMessage.created_at.asc(), BotMessage.id.asc())
            .limit(60)
            .all()
        )
        messages = [
            {
                "id": int(m.id),
                "direction": str(m.direction or ""),
                "message_type": str(m.message_type or "text"),
                "text_body": str(m.text_body or ""),
                "created_at": str(m.created_at or ""),
                "source": str(m.source or ""),
            }
            for m in rows
        ]
    return jsonify({"ok": True, "review": _sandbox_review_to_dict(review), "messages": messages}), 200


@admin_bp.route("/bot/sandbox/asistente/review/<int:review_id>/approve", methods=["POST"])
@roles_required("admin", "secretaria")
def bot_sandbox_assistant_approve(review_id: int):
    guard_ok, guard_resp = _sandbox_assistant_guard()
    if not guard_ok:
        return guard_resp
    table_ok, table_resp = _sandbox_assistant_table_guard()
    if not table_ok:
        return table_resp
    review = BotSandboxReviewQueue.query.get(int(review_id))
    if review is None:
        return jsonify({"ok": False, "error": "review_not_found"}), 404
    try:
        log_bot_event(
            "sandbox_real_send_clicked",
            metadata={"review_id": int(review.id), "conversation_id": int(review.conversation_id)},
        )
        approve_review(review=review, reviewer_id=int(getattr(current_user, "id", 0) or 0) or None, edited_text=None)
        db.session.commit()
        return jsonify({"ok": True, "review": _sandbox_review_to_dict(review)}), 200
    except ReviewTransitionError as exc:
        db.session.commit()
        return jsonify({"ok": False, "error": exc.code, "current_status": exc.current_status}), 409
    except Exception as exc:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 400


@admin_bp.route("/bot/sandbox/asistente/review/<int:review_id>/edit-approve", methods=["POST"])
@roles_required("admin", "secretaria")
def bot_sandbox_assistant_edit_approve(review_id: int):
    guard_ok, guard_resp = _sandbox_assistant_guard()
    if not guard_ok:
        return guard_resp
    table_ok, table_resp = _sandbox_assistant_table_guard()
    if not table_ok:
        return table_resp
    review = BotSandboxReviewQueue.query.get(int(review_id))
    if review is None:
        return jsonify({"ok": False, "error": "review_not_found"}), 404
    body = request.get_json(silent=True) or {}
    edited_text = str(body.get("edited_text") or "").strip()
    if not edited_text:
        return jsonify({"ok": False, "error": "edited_text_required"}), 400
    try:
        approve_review(
            review=review,
            reviewer_id=int(getattr(current_user, "id", 0) or 0) or None,
            edited_text=edited_text,
        )
        db.session.commit()
        return jsonify({"ok": True, "review": _sandbox_review_to_dict(review)}), 200
    except ReviewTransitionError as exc:
        db.session.commit()
        return jsonify({"ok": False, "error": exc.code, "current_status": exc.current_status}), 409
    except Exception as exc:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 400


@admin_bp.route("/bot/sandbox/asistente/review/<int:review_id>/reject", methods=["POST"])
@roles_required("admin", "secretaria")
def bot_sandbox_assistant_reject(review_id: int):
    guard_ok, guard_resp = _sandbox_assistant_guard()
    if not guard_ok:
        return guard_resp
    table_ok, table_resp = _sandbox_assistant_table_guard()
    if not table_ok:
        return table_resp
    review = BotSandboxReviewQueue.query.get(int(review_id))
    if review is None:
        return jsonify({"ok": False, "error": "review_not_found"}), 404
    body = request.get_json(silent=True) or {}
    reason = str(body.get("reason") or "").strip()
    if not reason:
        return jsonify({"ok": False, "error": "reason_required"}), 400
    try:
        reject_review(review=review, reviewer_id=int(getattr(current_user, "id", 0) or 0) or None, reason=reason)
        db.session.commit()
        return jsonify({"ok": True, "review": _sandbox_review_to_dict(review)}), 200
    except ReviewTransitionError as exc:
        db.session.commit()
        return jsonify({"ok": False, "error": exc.code, "current_status": exc.current_status}), 409


@admin_bp.route("/bot/sandbox/asistente/review/<int:review_id>/block", methods=["POST"])
@roles_required("admin", "secretaria")
def bot_sandbox_assistant_block(review_id: int):
    guard_ok, guard_resp = _sandbox_assistant_guard()
    if not guard_ok:
        return guard_resp
    table_ok, table_resp = _sandbox_assistant_table_guard()
    if not table_ok:
        return table_resp
    review = BotSandboxReviewQueue.query.get(int(review_id))
    if review is None:
        return jsonify({"ok": False, "error": "review_not_found"}), 404
    body = request.get_json(silent=True) or {}
    reason = str(body.get("reason") or "").strip()
    if not reason:
        return jsonify({"ok": False, "error": "reason_required"}), 400
    if str(review.status or "") not in {"pending_review", "edited", "approved"}:
        return jsonify({"ok": False, "error": "invalid_review_transition"}), 409
    meta = dict(review.metadata_json or {})
    events = list(meta.get("review_events") or [])
    events.append({"ts": str(utc_now_naive()), "event_type": "manual_block", "actor_id": int(getattr(current_user, "id", 0) or 0), "reason": reason})
    meta["review_events"] = events[-100:]
    review.metadata_json = meta
    review.status = "blocked"
    review.safety_status = "blocked"
    review.fallback_reason = f"manual_block:{reason}"[:120]
    review.reviewer_id = int(getattr(current_user, "id", 0) or 0) or None
    review.reviewed_at = utc_now_naive()
    db.session.commit()
    return jsonify({"ok": True, "review": _sandbox_review_to_dict(review)}), 200


@admin_bp.route("/bot/sandbox/asistente/worker/run", methods=["POST"])
@roles_required("admin", "secretaria")
def bot_sandbox_assistant_worker_run():
    guard_ok, guard_resp = _sandbox_assistant_guard()
    if not guard_ok:
        return guard_resp
    table_ok, table_resp = _sandbox_assistant_table_guard()
    if not table_ok:
        return table_resp
    body = request.get_json(silent=True) or {}
    batch_size = int(body.get("batch_size") or 20)
    review_id = int(body.get("review_id") or 0)
    conversation_id = int(body.get("conversation_id") or 0)
    if review_id > 0:
        log_bot_event("sandbox_real_send_clicked", metadata={"review_id": review_id, "conversation_id": conversation_id})
    confirm_global = bool(body.get("confirm_global", False))
    if review_id <= 0 and not confirm_global:
        return jsonify({"ok": False, "error": "global_worker_confirmation_required", "warning": "Global worker can process old pending outbox."}), 409
    outbox_id = 0
    if review_id > 0:
        review = BotSandboxReviewQueue.query.get(review_id)
        if review is None or int(review.outbound_message_id or 0) <= 0:
            return jsonify({"ok": False, "error": "review_outbound_not_found"}), 404
        outbox = BotSandboxOutbound.query.filter_by(bot_message_id=int(review.outbound_message_id)).first()
        if outbox is None:
            return jsonify({"ok": False, "error": "outbox_not_found_for_review"}), 404
        outbox_id = int(outbox.id)
    stats = run_sandbox_worker_once(batch_size=batch_size, review_id=(review_id or None), outbox_id=(outbox_id or None))
    return jsonify({"ok": True, "stats": stats, "metrics": sandbox_metrics_snapshot()}), 200


@admin_bp.route("/bot/sandbox/asistente/outbox/housekeeping", methods=["POST"])
@roles_required("owner", "admin")
def bot_sandbox_assistant_outbox_housekeeping():
    guard_ok, guard_resp = _sandbox_assistant_guard()
    if not guard_ok:
        return guard_resp
    body = request.get_json(silent=True) or {}
    action = str(body.get("action") or "").strip().lower()
    if action == "archive_old_pending":
        result = archive_old_sandbox_outbox(
            older_than_hours=int(body.get("older_than_hours") or 6),
            limit=int(body.get("limit") or 500),
        )
        return jsonify({"ok": True, "action": action, **result}), 200
    if action == "cleanup_terminal":
        result = cleanup_sandbox_outbox_terminal(
            older_than_days=int(body.get("older_than_days") or 7),
            limit=int(body.get("limit") or 1000),
        )
        return jsonify({"ok": True, "action": action, **result}), 200
    return jsonify({"ok": False, "error": "invalid_action"}), 400


def _meta_base() -> str:
    return (os.getenv("WHATSAPP_GRAPH_BASE_URL") or "https://graph.facebook.com").strip().rstrip("/")


def _meta_version() -> str:
    return (os.getenv("WHATSAPP_API_VERSION") or "v23.0").strip()


def _meta_phone_number_id() -> str:
    return (os.getenv("WHATSAPP_PHONE_NUMBER_ID") or "").strip()


def _meta_token() -> str:
    return (os.getenv("WHATSAPP_ACCESS_TOKEN") or "").strip()


def _meta_get(path: str, *, params: dict | None = None, timeout: int = 8) -> tuple[int | None, str, dict]:
    token = _meta_token()
    url = f"{_meta_base()}/{_meta_version()}/{path.lstrip('/')}"
    try:
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, params=params or {}, timeout=timeout)
        txt = resp.text or ""
        try:
            js = resp.json() if resp.content else {}
        except Exception:
            js = {}
        return int(resp.status_code), txt, js
    except Exception as exc:
        return None, str(exc), {}


@admin_bp.route("/bot/debug/send-real-whatsapp", methods=["POST"])
@roles_required("owner", "admin")
def bot_debug_send_real_whatsapp():
    body = request.get_json(silent=True) or {}
    to = str(body.get("to") or "").strip()
    if not to.startswith("+"):
        return jsonify({"ok": False, "error": "to_must_be_e164"}), 400
    token = _meta_token()
    phone_number_id = _meta_phone_number_id()
    if not token or not phone_number_id:
        return jsonify({"ok": False, "error": "missing_meta_credentials"}), 400

    allowlisted = to in {x.strip() for x in str(os.getenv("BOT_REAL_WHATSAPP_ALLOWED_NUMBERS", "") or "").replace(";", ",").split(",") if x.strip()}
    app_mode_status, app_mode_raw, app_mode_json = _meta_get("app", params={"fields": "id,name"})
    sender_status, sender_raw, sender_json = _meta_get(phone_number_id, params={"fields": "id,verified_name,display_phone_number,quality_rating"})
    perms_status, perms_raw, perms_json = _meta_get("me/permissions")
    scopes = []
    if isinstance(perms_json, dict) and isinstance(perms_json.get("data"), list):
        scopes = [str(p.get("permission") or "") for p in perms_json.get("data") if isinstance(p, dict)]

    url = f"{_meta_base()}/{_meta_version()}/{phone_number_id}/messages"
    payload = {"messaging_product": "whatsapp", "to": to.lstrip("+"), "type": "text", "text": {"body": "TEST REAL META SANDBOX"}}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    log_bot_event(
        "meta_send_request_started",
        metadata={
            "endpoint": url,
            "phone_number_id": phone_number_id,
            "token_prefix_masked": token[:8] + "***",
            "payload_masked": payload,
            "timeout": 8,
            "headers_masked": {"Authorization": f"Bearer {token[:8]}***", "Content-Type": "application/json"},
        },
    )
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=8)
        raw = resp.text or ""
        try:
            parsed = resp.json() if resp.content else {}
        except Exception:
            parsed = {}
        wamid = ""
        if isinstance(parsed, dict) and isinstance(parsed.get("messages"), list) and parsed.get("messages"):
            wamid = str((parsed.get("messages")[0] or {}).get("id") or "")
        log_bot_event("meta_send_response", metadata={"http_status": int(resp.status_code), "raw_body": raw, "parsed_json": parsed, "wamid": wamid})
        return jsonify(
            {
                "ok": 200 <= int(resp.status_code) < 300,
                "request": {"endpoint": url, "phone_number_id": phone_number_id, "payload": payload},
                "response": {"http_status": int(resp.status_code), "raw_body": raw, "parsed_json": parsed, "wamid": wamid},
                "checks": {
                    "recipient_allowlisted": allowlisted,
                    "app_info_http_status": app_mode_status,
                    "app_info_raw": app_mode_raw,
                    "app_info_parsed": app_mode_json,
                    "sender_connected_http_status": sender_status,
                    "sender_connected_raw": sender_raw,
                    "sender_connected_parsed": sender_json,
                    "permissions_http_status": perms_status,
                    "permissions_raw": perms_raw,
                    "permissions_parsed": perms_json,
                    "scopes": scopes,
                    "required_scopes_present": {
                        "whatsapp_business_messaging": "whatsapp_business_messaging" in scopes,
                        "whatsapp_business_management": "whatsapp_business_management" in scopes,
                    },
                },
            }
        ), (200 if 200 <= int(resp.status_code) < 300 else 400)
    except Exception as exc:
        log_bot_event("network_exception", level="warning", metadata={"exception": exc.__class__.__name__, "endpoint": url})
        return jsonify({"ok": False, "error": "network_exception", "detail": str(exc)}), 500


@admin_bp.route("/bot/sandbox/asistente/metrics.json", methods=["GET"])
@roles_required("admin", "secretaria")
def bot_sandbox_assistant_metrics_json():
    guard_ok, guard_resp = _sandbox_assistant_guard()
    if not guard_ok:
        return guard_resp
    table_ok, table_resp = _sandbox_assistant_table_guard()
    if not table_ok:
        return table_resp
    review_stats = {
        "pending_review": BotSandboxReviewQueue.query.filter_by(status="pending_review").count(),
        "approved": BotSandboxReviewQueue.query.filter_by(status="approved").count(),
        "rejected": BotSandboxReviewQueue.query.filter_by(status="rejected").count(),
        "blocked": BotSandboxReviewQueue.query.filter_by(status="blocked").count(),
        "simulated_sent": BotSandboxReviewQueue.query.filter_by(status="simulated_sent").count(),
    }
    outbound_real_count = BotSandboxOutbound.query.filter(BotSandboxOutbound.provider != "fake").count()
    whatsapp_real_count = (
        BotMessage.query.filter(BotMessage.direction == "outbound")
        .filter(BotMessage.source != "admin_manual")
        .filter(BotMessage.source != "sandbox_review")
        .count()
    )
    return jsonify(
        {
            "ok": True,
            "review_stats": review_stats,
            "sandbox_metrics": sandbox_metrics_snapshot(),
            "outbound_real_count": int(outbound_real_count),
            "whatsapp_real_count": int(whatsapp_real_count),
            "outbound_real": False,
            "whatsapp_real": False,
            "production": False,
            "review_required": True,
            "real_sandbox_enabled": bool(is_real_whatsapp_sandbox_enabled()),
            "real_sandbox_paused": bool(is_real_sandbox_paused()),
            "real_sandbox_setting_key": REAL_SANDBOX_SETTING_KEY,
            "sandbox_auto_reply_enabled": bool(is_sandbox_auto_reply_enabled()),
            "sandbox_auto_reply_active": bool(is_sandbox_auto_reply_active()),
            "sandbox_auto_reply_paused": bool(is_sandbox_auto_reply_paused()),
            "sandbox_auto_reply_setting_key": AUTO_REPLY_PAUSED_SETTING_KEY,
        }
    ), 200


@admin_bp.route("/bot/sandbox/asistente/auto-reply/pause", methods=["POST"])
@roles_required("owner")
def bot_sandbox_assistant_auto_reply_pause():
    set_sandbox_auto_reply_paused(paused=True, actor_id=int(getattr(current_user, "id", 0) or 0) or None)
    db.session.commit()
    return jsonify({"ok": True, "paused": True}), 200


@admin_bp.route("/bot/sandbox/asistente/auto-reply/resume", methods=["POST"])
@roles_required("owner")
def bot_sandbox_assistant_auto_reply_resume():
    set_sandbox_auto_reply_paused(paused=False, actor_id=int(getattr(current_user, "id", 0) or 0) or None)
    db.session.commit()
    return jsonify({"ok": True, "paused": False}), 200


@admin_bp.route("/bot/sandbox/asistente/conversation/<int:conversation_id>/reset", methods=["POST"])
@roles_required("owner", "admin")
def bot_sandbox_assistant_conversation_reset(conversation_id: int):
    body = request.get_json(silent=True) or {}
    if not bool(body.get("confirm")):
        return jsonify({"ok": False, "error": "confirm_required"}), 400
    conversation = BotConversation.query.get(int(conversation_id))
    if conversation is None:
        return jsonify({"ok": False, "error": "conversation_not_found"}), 404
    meta = dict(getattr(conversation, "metadata_json", {}) or {})
    meta["sandbox_conversation"] = True
    interview_flow = dict(meta.get("interview_flow") or {})
    interview_flow.pop("validation_error", None)
    interview_flow.pop("last_invalid_answer", None)
    meta.pop("validation_error", None)
    meta.pop("last_invalid_answer", None)
    meta.pop("interview_flow", None)
    conversation.metadata_json = meta
    archived_reviews = 0
    archived_outbox = 0
    if bool(body.get("archive_pending", False)):
        reviews = BotSandboxReviewQueue.query.filter_by(conversation_id=int(conversation.id)).filter(
            BotSandboxReviewQueue.status.in_(["pending_review", "approved", "edited"])
        ).all()
        for r in reviews:
            r.status = "blocked"
            r.fallback_reason = "conversation_reset_archived"
            archived_reviews += 1
        outbox_rows = BotSandboxOutbound.query.filter_by(conversation_id=int(conversation.id)).filter(
            BotSandboxOutbound.state.in_(["queued", "failed", "processing"])
        ).all()
        for o in outbox_rows:
            o.state = "blocked"
            o.failure_reason = "conversation_reset_archived"
            o.blocked_at = utc_now_naive()
            archived_outbox += 1
    db.session.commit()
    return jsonify(
        {
            "ok": True,
            "conversation_id": int(conversation.id),
            "message": "Conversación reiniciada correctamente. Lista para nueva prueba.",
            "sandbox_conversation": True,
            "interview_flow_cleared": True,
            "archived_reviews": int(archived_reviews),
            "archived_outbox": int(archived_outbox),
        }
    ), 200


@admin_bp.route("/bot/sandbox/asistente/conversation/<int:conversation_id>/draft-candidate", methods=["POST"])
@roles_required("owner", "admin")
def bot_sandbox_assistant_create_draft_candidate(conversation_id: int):
    conversation = BotConversation.query.get(int(conversation_id))
    if conversation is None:
        return jsonify({"ok": False, "error": "conversation_not_found"}), 404
    try:
        draft, created = get_or_create_interview_flow_draft(conversation)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409
    return jsonify({"ok": True, "created": bool(created), "draft_id": int(draft.id)}), 200


@admin_bp.route("/bot/sandbox/asistente/real-sandbox/pause", methods=["POST"])
@roles_required("owner")
def bot_sandbox_assistant_real_sandbox_pause():
    set_real_sandbox_paused(paused=True, actor_id=int(getattr(current_user, "id", 0) or 0) or None)
    db.session.commit()
    log_bot_event("real_sandbox_pause", metadata={"actor_id": int(getattr(current_user, "id", 0) or 0)})
    return jsonify({"ok": True, "paused": True}), 200


@admin_bp.route("/bot/sandbox/asistente/real-sandbox/resume", methods=["POST"])
@roles_required("owner")
def bot_sandbox_assistant_real_sandbox_resume():
    set_real_sandbox_paused(paused=False, actor_id=int(getattr(current_user, "id", 0) or 0) or None)
    db.session.commit()
    log_bot_event("real_sandbox_resume", metadata={"actor_id": int(getattr(current_user, "id", 0) or 0)})
    return jsonify({"ok": True, "paused": False}), 200


@admin_bp.route("/bot/sandbox/asistente/real-sandbox/delivery-webhook", methods=["POST"])
@roles_required("owner", "admin")
def bot_sandbox_assistant_real_sandbox_delivery_webhook():
    body = request.get_json(silent=True) or {}
    result = apply_delivery_webhook_update(
        provider_message_id=str(body.get("provider_message_id") or ""),
        delivery_status=str(body.get("delivery_status") or ""),
        payload=dict(body.get("payload") or {}),
    )
    code = 200 if bool(result.get("ok")) else 400
    return jsonify(result), code
