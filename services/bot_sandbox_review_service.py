from __future__ import annotations

import re
from typing import Any

from config_app import db
from models import BotConversation, BotMessage, BotSandboxReviewQueue
from sqlalchemy.exc import IntegrityError
from services.bot_constants import (
    MESSAGE_DIRECTION_INBOUND,
    MESSAGE_DIRECTION_OUTBOUND,
    MESSAGE_SOURCE_ADMIN_MANUAL,
    MESSAGE_SOURCE_WHATSAPP_USER,
    MESSAGE_STATUS_INBOUND_RECEIVED,
    MESSAGE_STATUS_OUTBOUND_QUEUED,
)
from services.bot_conversation_service import get_protocol_state
from services.bot_inbound_pipeline_service import process_inbound_ai_pipeline
from services.bot_message_service import create_manual_message
from services.bot_observability_service import log_bot_event
from services.bot_interview_flow_service import is_interview_flow_enabled, process_interview_inbound
from services.bot_practice_ai_reply_service import validate_ai_reply
from services.bot_protocol_service import build_step_prompt
from services.bot_sandbox_service import (
    SandboxSafetyError,
    enqueue_sandbox_outbound,
    is_sandbox_auto_reply_active,
    is_real_whatsapp_sandbox_enabled,
    is_staging_offline_active,
)
from utils.timezone import utc_now_naive


REVIEW_STATUS_PENDING = "pending_review"
REVIEW_STATUS_APPROVED = "approved"
REVIEW_STATUS_REJECTED = "rejected"
REVIEW_STATUS_EDITED = "edited"
REVIEW_STATUS_SIMULATED_SENT = "simulated_sent"
REVIEW_STATUS_BLOCKED = "blocked"
REVIEW_STATUS_FAILED = "failed"
_REVIEW_ALLOWED_TRANSITIONS = {
    REVIEW_STATUS_PENDING: {REVIEW_STATUS_APPROVED, REVIEW_STATUS_REJECTED, REVIEW_STATUS_EDITED, REVIEW_STATUS_BLOCKED},
    REVIEW_STATUS_EDITED: {REVIEW_STATUS_APPROVED, REVIEW_STATUS_BLOCKED},
    REVIEW_STATUS_APPROVED: {REVIEW_STATUS_SIMULATED_SENT, REVIEW_STATUS_BLOCKED, REVIEW_STATUS_FAILED},
}


class ReviewTransitionError(RuntimeError):
    def __init__(self, code: str, *, current_status: str, target_status: str, review_id: int):
        super().__init__(code)
        self.code = code
        self.current_status = current_status
        self.target_status = target_status
        self.review_id = review_id


def _safe_phone(phone: str | None) -> bool:
    return str(phone or "").strip().startswith("+1999")


def _safety_validate(*, text: str, base_text: str, current_step: str, candidate_message: str) -> tuple[bool, str]:
    ok, reason = validate_ai_reply(
        ai_suggested_reply=text,
        base_suggested_reply=base_text,
        current_step=current_step,
        candidate_message=candidate_message,
        requires_human=False,
    )
    return bool(ok), str(reason or "ok")


def _validate_edited_text_policy(text: str) -> tuple[bool, str]:
    clean = str(text or "").strip()
    if not clean:
        return False, "edited_text_empty"
    if len(clean) > 600:
        return False, "edited_text_too_long"
    normalized = clean.lower()
    checks = [
        (r"\b(empleo|trabajo)\b.*\b(seguro|garantizado|hoy|inmediato)\b", "edited_text_employment_promise"),
        (r"\bya\s+estas?\s+aprobad[ao]\b", "edited_text_false_approved_claim"),
        (r"\b(whatsapp|meta)\b.*\b(oficial|verificado|real)\b", "edited_text_real_whatsapp_claim"),
        (r"\b(datos?\s+confirmados?\s+en\s+sistema|ya\s+validamos)\b", "edited_text_invented_data_claim"),
        (r"\b(idiota|estupida|estupido|bruta|callate)\b", "edited_text_inappropriate_tone"),
    ]
    for pattern, reason in checks:
        if re.search(pattern, normalized):
            return False, reason
    return True, "ok"


def _append_review_event(review: BotSandboxReviewQueue, *, event_type: str, actor_id: int | None, metadata: dict[str, Any] | None = None) -> None:
    meta = dict(review.metadata_json or {})
    events = list(meta.get("review_events") or [])
    events.append(
        {
            "ts": str(utc_now_naive()),
            "event_type": str(event_type),
            "actor_id": actor_id,
            "status": str(review.status or ""),
            "metadata": dict(metadata or {}),
        }
    )
    meta["review_events"] = events[-100:]
    review.metadata_json = meta


def _record_invalid_transition(
    *, review: BotSandboxReviewQueue, from_status: str, to_status: str, actor_id: int | None, reason: str
) -> None:
    _append_review_event(
        review,
        event_type="invalid_transition",
        actor_id=actor_id,
        metadata={"from": from_status, "to": to_status, "reason": reason},
    )
    log_bot_event(
        "sandbox_review_invalid_transition",
        level="warning",
        metadata={"review_id": review.id, "from": from_status, "to": to_status, "reason": reason, "actor_id": actor_id},
    )


def _ensure_transition_or_raise(*, review: BotSandboxReviewQueue, target_status: str, actor_id: int | None, reason: str) -> None:
    current = str(review.status or "")
    allowed = _REVIEW_ALLOWED_TRANSITIONS.get(current, set())
    if target_status not in allowed:
        _record_invalid_transition(review=review, from_status=current, to_status=target_status, actor_id=actor_id, reason=reason)
        raise ReviewTransitionError(
            "invalid_review_transition",
            current_status=current,
            target_status=target_status,
            review_id=int(review.id),
        )


def _claim_transition(review_id: int, *, from_statuses: set[str], to_status: str, reviewer_id: int | None) -> bool:
    now = utc_now_naive()
    updated = (
        BotSandboxReviewQueue.query.filter(BotSandboxReviewQueue.id == int(review_id))
        .filter(BotSandboxReviewQueue.status.in_(tuple(from_statuses)))
        .filter(BotSandboxReviewQueue.outbound_message_id.is_(None))
        .update(
            {
                BotSandboxReviewQueue.status: to_status,
                BotSandboxReviewQueue.reviewer_id: reviewer_id,
                BotSandboxReviewQueue.reviewed_at: now,
                BotSandboxReviewQueue.updated_at: now,
            },
            synchronize_session=False,
        )
    )
    return int(updated or 0) == 1


def _build_review_suggestion(*, conversation: BotConversation, inbound_message: BotMessage, pipeline_result: dict[str, Any]) -> dict[str, Any]:
    msg_type = str(getattr(inbound_message, "message_type", "text") or "text").strip().lower()
    if msg_type in {"audio", "image", "document"}:
        safe_label = {"audio": "audio", "image": "imagen", "document": "documento"}.get(msg_type, "mensaje")
        final = f"Recibimos tu {safe_label}. Un miembro del equipo lo revisara."
        return {
            "base_suggested_reply": final,
            "ai_suggested_reply": "",
            "final_suggested_reply": final,
            "safety_status": "ok",
            "fallback_reason": "media_requires_manual_review",
            "requires_human": True,
            "current_step": str(get_protocol_state(conversation).get("current_step_code") or "WELCOME").strip().upper(),
        }

    interview_enabled = is_interview_flow_enabled()
    if interview_enabled:
        interview_result = process_interview_inbound(
            conversation=conversation,
            inbound_text=str(inbound_message.text_body or ""),
            message_type=msg_type,
        )
        if bool(interview_result.get("active")):
            db.session.flush()
            interview_state = dict(interview_result.get("state") or {})
            final = str(interview_result.get("reply") or "").strip()
            if not final:
                final = "Gracias por escribir. Un asesor humano revisará y te responderá."
            return {
                "base_suggested_reply": final,
                "ai_suggested_reply": "",
                "final_suggested_reply": final,
                "safety_status": "ok",
                "fallback_reason": "interview_flow",
                "requires_human": True,
                "current_step": str(interview_state.get("current_step") or ""),
                "interview_flow": interview_state,
                "interview_flow_enabled": True,
                "interview_flow_used": True,
            }

    ai_result = dict((pipeline_result or {}).get("ai_pipeline") or {})
    protocol_auto = dict((pipeline_result or {}).get("protocol_auto_advance") or {})
    state = get_protocol_state(conversation)
    current_step = str(state.get("current_step_code") or "WELCOME").strip().upper()
    base = str(
        protocol_auto.get("next_step_prompt")
        or protocol_auto.get("clarification_prompt")
        or build_step_prompt(current_step)
        or ""
    ).strip()
    ai = str(ai_result.get("suggested_reply") or "").strip()
    final = ai or base
    if not final:
        final = "Gracias por escribir. Un asesor humano revisará y te responderá."
        base = final

    safety_status = "ok"
    fallback_reason = ""
    if ai:
        valid, reason = _safety_validate(
            text=ai,
            base_text=base,
            current_step=current_step,
            candidate_message=str(inbound_message.text_body or ""),
        )
        if not valid:
            final = base
            safety_status = "fallback"
            fallback_reason = reason
    else:
        safety_status = "fallback"
        fallback_reason = "ai_empty"

    return {
        "base_suggested_reply": base,
        "ai_suggested_reply": ai,
        "final_suggested_reply": final,
        "safety_status": safety_status,
        "fallback_reason": fallback_reason,
        "requires_human": True,
        "current_step": current_step,
        "interview_flow_enabled": bool(interview_enabled),
        "interview_flow_used": False,
    }


def create_review_from_inbound(
    *,
    conversation: BotConversation,
    inbound_message: BotMessage,
    identity_status: str,
) -> BotSandboxReviewQueue:
    existing = BotSandboxReviewQueue.query.filter_by(inbound_message_id=int(inbound_message.id)).first()
    if existing:
        return existing

    msg_type = str(getattr(inbound_message, "message_type", "text") or "text").strip().lower()
    pipeline_result = {}
    if msg_type == "text":
        pipeline_result = process_inbound_ai_pipeline(
            conversation=conversation,
            inbound_message=inbound_message,
            identity_status=str(identity_status or "unknown"),
            message_type="text",
            phone_e164=str(conversation.phone_e164 or ""),
            allow_autoreply_send=False,
        )
    suggestion = _build_review_suggestion(
        conversation=conversation,
        inbound_message=inbound_message,
        pipeline_result=dict(pipeline_result or {}),
    )

    row = BotSandboxReviewQueue(
        conversation_id=int(conversation.id),
        inbound_message_id=int(inbound_message.id),
        base_suggested_reply=str(suggestion["base_suggested_reply"] or ""),
        ai_suggested_reply=str(suggestion["ai_suggested_reply"] or ""),
        final_suggested_reply=str(suggestion["final_suggested_reply"] or ""),
        status=REVIEW_STATUS_PENDING,
        safety_status=str(suggestion["safety_status"] or "pending"),
        fallback_reason=str(suggestion["fallback_reason"] or "") or None,
        metadata_json={
            "requires_human": True,
            "current_step": str(suggestion["current_step"] or ""),
            "interview_flow": dict(suggestion.get("interview_flow") or {}),
            "interview_flow_enabled": bool(suggestion.get("interview_flow_enabled", False)),
            "interview_flow_used": bool(suggestion.get("interview_flow_used", False)),
            "pipeline": dict(pipeline_result or {}),
        },
    )
    db.session.add(row)
    db.session.flush()
    log_bot_event("sandbox_review_created", metadata={"review_id": row.id, "conversation_id": conversation.id})
    return row


def reject_review(*, review: BotSandboxReviewQueue, reviewer_id: int | None, reason: str) -> BotSandboxReviewQueue:
    if not _claim_transition(
        int(review.id),
        from_statuses={REVIEW_STATUS_PENDING},
        to_status=REVIEW_STATUS_REJECTED,
        reviewer_id=reviewer_id,
    ):
        db.session.refresh(review)
        _ensure_transition_or_raise(review=review, target_status=REVIEW_STATUS_REJECTED, actor_id=reviewer_id, reason="reject_not_allowed")
    db.session.refresh(review)
    review.rejection_reason = str(reason or "rejected_by_reviewer")[:255]
    _append_review_event(review, event_type="transition_rejected", actor_id=reviewer_id, metadata={"reason": review.rejection_reason})
    log_bot_event("sandbox_review_rejected", metadata={"review_id": review.id, "reviewer_id": reviewer_id})
    return review


def approve_review(
    *,
    review: BotSandboxReviewQueue,
    reviewer_id: int | None,
    edited_text: str | None = None,
) -> tuple[BotSandboxReviewQueue, BotMessage | None]:
    if edited_text is None and review.outbound_message_id:
        existing_outbound = BotMessage.query.get(int(review.outbound_message_id))
        if existing_outbound is not None:
            _append_review_event(review, event_type="approve_idempotent_existing_outbound", actor_id=reviewer_id)
            log_bot_event("sandbox_review_approve_idempotent", metadata={"review_id": review.id, "outbound_message_id": review.outbound_message_id})
            return review, existing_outbound
    target_status = REVIEW_STATUS_EDITED if edited_text is not None else REVIEW_STATUS_APPROVED
    if edited_text is not None:
        final_text_precheck = str(edited_text).strip()
        valid_policy, reason_policy = _validate_edited_text_policy(final_text_precheck)
        if not valid_policy:
            _ensure_transition_or_raise(review=review, target_status=REVIEW_STATUS_BLOCKED, actor_id=reviewer_id, reason=reason_policy)
            review.status = REVIEW_STATUS_BLOCKED
            review.safety_status = "blocked"
            review.fallback_reason = reason_policy
            _append_review_event(review, event_type="unsafe_edit_blocked", actor_id=reviewer_id, metadata={"reason": reason_policy})
            return review, None
    claim_from = {REVIEW_STATUS_PENDING, REVIEW_STATUS_EDITED} if target_status == REVIEW_STATUS_APPROVED else {REVIEW_STATUS_PENDING}
    if not _claim_transition(
        int(review.id),
        from_statuses=claim_from,
        to_status=target_status,
        reviewer_id=reviewer_id,
    ):
        db.session.refresh(review)
        if edited_text is None and review.outbound_message_id:
            existing_outbound = BotMessage.query.get(int(review.outbound_message_id))
            if existing_outbound is not None:
                _append_review_event(review, event_type="approve_idempotent_existing_outbound", actor_id=reviewer_id)
                return review, existing_outbound
        _ensure_transition_or_raise(review=review, target_status=target_status, actor_id=reviewer_id, reason="approve_not_allowed")
    db.session.refresh(review)
    conversation = BotConversation.query.get(int(review.conversation_id))
    inbound = BotMessage.query.get(int(review.inbound_message_id))
    if conversation is None or inbound is None:
        _ensure_transition_or_raise(
            review=review,
            target_status=REVIEW_STATUS_BLOCKED,
            actor_id=reviewer_id,
            reason="missing_conversation_or_inbound",
        )
        review.status = REVIEW_STATUS_BLOCKED
        review.safety_status = "blocked"
        review.fallback_reason = "missing_conversation_or_inbound"
        _append_review_event(review, event_type="transition_blocked", actor_id=reviewer_id, metadata={"reason": review.fallback_reason})
        return review, None

    real_mode = bool(is_real_whatsapp_sandbox_enabled() and not is_staging_offline_active())
    if not is_staging_offline_active() and not real_mode:
        raise SandboxSafetyError("sandbox_security_block:offline_not_active")
    if not real_mode and not _safe_phone(conversation.phone_e164):
        raise SandboxSafetyError("sandbox_security_block:real_phone_detected")

    final_text = str(edited_text if edited_text is not None else (review.final_suggested_reply or "")).strip()
    if not final_text:
        _ensure_transition_or_raise(review=review, target_status=REVIEW_STATUS_BLOCKED, actor_id=reviewer_id, reason="empty_final_reply")
        review.status = REVIEW_STATUS_BLOCKED
        review.safety_status = "blocked"
        review.fallback_reason = "empty_final_reply"
        _append_review_event(review, event_type="transition_blocked", actor_id=reviewer_id, metadata={"reason": review.fallback_reason})
        return review, None

    if edited_text is not None:
        current_step = str((dict(review.metadata_json or {})).get("current_step") or "").strip().upper()
        base_text = str(review.base_suggested_reply or final_text)
        if final_text != str(review.final_suggested_reply or "").strip():
            valid, reason = _safety_validate(
                text=final_text,
                base_text=base_text,
                current_step=current_step,
                candidate_message=str(inbound.text_body or ""),
            )
            if not valid:
                _ensure_transition_or_raise(review=review, target_status=REVIEW_STATUS_BLOCKED, actor_id=reviewer_id, reason=str(reason or "unsafe_reply"))
                review.status = REVIEW_STATUS_BLOCKED
                review.safety_status = "blocked"
                review.fallback_reason = str(reason or "unsafe_reply")
                _append_review_event(review, event_type="unsafe_edit_blocked", actor_id=reviewer_id, metadata={"reason": review.fallback_reason})
                return review, None

    outbound = None
    if review.outbound_message_id:
        outbound = BotMessage.query.get(int(review.outbound_message_id))
    if outbound is None:
        outbound = create_manual_message(
            conversation=conversation,
            text_body=final_text,
            direction=MESSAGE_DIRECTION_OUTBOUND,
            source=MESSAGE_SOURCE_ADMIN_MANUAL,
            status=MESSAGE_STATUS_OUTBOUND_QUEUED,
        )
        try:
            real_mode = bool(is_real_whatsapp_sandbox_enabled() and not is_staging_offline_active())
            mode = "real_sandbox" if real_mode else "offline"
            provider = "fake"
            if real_mode:
                import os

                provider = str(os.getenv("BOT_REAL_WHATSAPP_PROVIDER", "fake") or "fake").strip().lower()
            phone = str(conversation.phone_e164 or "")
            masked = ("***" + phone[-4:]) if phone else ""
            log_bot_event(
                "sandbox_review_approved_dispatch",
                metadata={
                    "review_id": int(review.id),
                    "mode": mode,
                    "provider_requested": provider,
                    "to": masked,
                },
            )
            enqueue_sandbox_outbound(
                conversation=conversation,
                message=outbound,
                provider=provider,
                metadata={
                    "review_id": int(review.id),
                    "mode": mode,
                    "review_approved": True,
                    "reviewer": reviewer_id,
                    "approved_by": reviewer_id,
                    "manual_review_required": True,
                    "owner_only": True,
                    "auto_send_allowed": False,
                },
            )
        except IntegrityError:
            db.session.flush()

    review.outbound_message_id = int(outbound.id)
    review.reviewer_id = reviewer_id
    review.reviewed_at = utc_now_naive()
    review.edited_text = str(edited_text or "").strip() or None
    review.status = target_status
    review.final_suggested_reply = final_text
    review.safety_status = "ok"
    review.fallback_reason = None
    _append_review_event(
        review,
        event_type="transition_approved",
        actor_id=reviewer_id,
        metadata={"edited": bool(edited_text is not None), "outbound_message_id": review.outbound_message_id},
    )
    if edited_text is not None:
        review.status = REVIEW_STATUS_APPROVED
        _append_review_event(
            review,
            event_type="transition_edited_to_approved",
            actor_id=reviewer_id,
            metadata={"outbound_message_id": review.outbound_message_id},
        )

    log_bot_event(
        "sandbox_review_edited" if edited_text is not None else "sandbox_review_approved",
        metadata={"review_id": review.id, "reviewer_id": reviewer_id},
    )
    log_bot_event(
        "sandbox_outbox_enqueued",
        metadata={
            "review_id": review.id,
            "outbound_message_id": outbound.id,
            "mode": "real_sandbox" if bool(is_real_whatsapp_sandbox_enabled() and not is_staging_offline_active()) else "offline",
        },
    )
    return review, outbound


def mark_review_simulated_sent(*, outbound_message_id: int) -> int:
    rows = BotSandboxReviewQueue.query.filter_by(outbound_message_id=int(outbound_message_id)).all()
    for row in rows:
        current = str(row.status or "")
        allowed = _REVIEW_ALLOWED_TRANSITIONS.get(current, set())
        if REVIEW_STATUS_SIMULATED_SENT not in allowed:
            _record_invalid_transition(
                review=row,
                from_status=current,
                to_status=REVIEW_STATUS_SIMULATED_SENT,
                actor_id=None,
                reason="worker_transition_not_allowed",
            )
            continue
        row.status = REVIEW_STATUS_SIMULATED_SENT
        row.updated_at = utc_now_naive()
        meta = dict(row.metadata_json or {})
        auto = dict(meta.get("auto_reply") or {})
        if bool(auto.get("enabled", False)):
            auto["auto_sent_at"] = str(utc_now_naive())
            meta["auto_reply"] = auto
            row.metadata_json = meta
        _append_review_event(
            row,
            event_type="transition_simulated_sent",
            actor_id=None,
            metadata={"outbound_message_id": outbound_message_id},
        )
        log_bot_event("sandbox_simulated_sent", metadata={"review_id": row.id, "outbound_message_id": outbound_message_id})
    return len(rows)


def auto_approve_review_in_sandbox(*, review: BotSandboxReviewQueue) -> tuple[BotSandboxReviewQueue, BotMessage | None]:
    if not is_sandbox_auto_reply_active():
        raise SandboxSafetyError("sandbox_auto_reply_not_active")
    if review.outbound_message_id:
        existing_outbound = BotMessage.query.get(int(review.outbound_message_id))
        if existing_outbound is not None:
            return review, existing_outbound
    conversation = BotConversation.query.get(int(review.conversation_id))
    inbound = BotMessage.query.get(int(review.inbound_message_id))
    if conversation is None or inbound is None:
        raise SandboxSafetyError("sandbox_auto_reply_missing_context")
    final_text = str(review.final_suggested_reply or "").strip()
    if not final_text:
        raise SandboxSafetyError("sandbox_auto_reply_empty_reply")
    outbound = create_manual_message(
        conversation=conversation,
        text_body=final_text,
        direction=MESSAGE_DIRECTION_OUTBOUND,
        source=MESSAGE_SOURCE_ADMIN_MANUAL,
        status=MESSAGE_STATUS_OUTBOUND_QUEUED,
    )
    enqueue_sandbox_outbound(
        conversation=conversation,
        message=outbound,
        provider="meta_sandbox",
        metadata={
            "review_id": int(review.id),
            "mode": "real_sandbox",
            "review_approved": True,
            "reviewer": 0,
            "approved_by": 0,
            "manual_review_required": False,
            "owner_only": True,
            "auto_send_allowed": True,
            "auto_reply_sandbox": True,
            "inbound_wa_message_id": str(inbound.wa_message_id or ""),
        },
    )
    meta = dict(review.metadata_json or {})
    meta["auto_reply"] = {"enabled": True, "auto_approved_at": str(utc_now_naive())}
    review.metadata_json = meta
    review.status = REVIEW_STATUS_APPROVED
    review.reviewer_id = None
    review.reviewed_at = utc_now_naive()
    review.outbound_message_id = int(outbound.id)
    _append_review_event(
        review,
        event_type="transition_approved_auto_sandbox",
        actor_id=None,
        metadata={"outbound_message_id": int(outbound.id)},
    )
    return review, outbound


def create_inbound_message(
    *,
    conversation: BotConversation,
    text: str,
    wa_message_id: str,
    payload: dict[str, Any],
    created_at=None,
    message_type: str = "text",
    media_id: str | None = None,
    media_mime_type: str | None = None,
    requires_human: bool = True,
) -> BotMessage:
    row = BotMessage(
        conversation_id=int(conversation.id),
        direction=MESSAGE_DIRECTION_INBOUND,
        source=MESSAGE_SOURCE_WHATSAPP_USER,
        message_type=str(message_type or "text").strip().lower() or "text",
        wa_message_id=str(wa_message_id or "").strip() or None,
        text_body=str(text or "").strip(),
        media_id=str(media_id or "").strip() or None,
        media_mime_type=str(media_mime_type or "").strip() or None,
        status=MESSAGE_STATUS_INBOUND_RECEIVED,
        raw_payload_json={**dict(payload or {}), "requires_human": bool(requires_human)},
    )
    if created_at is not None:
        row.created_at = created_at
    db.session.add(row)
    db.session.flush()
    now = created_at or utc_now_naive()
    conversation.last_inbound_at = now
    conversation.last_message_at = now
    conversation.updated_at = now
    conversation.unread_count_admin = int(conversation.unread_count_admin or 0) + 1
    return row
