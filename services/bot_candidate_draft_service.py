from __future__ import annotations

import copy
from typing import Any

from config_app import db
from models import BotCandidateDraft
from sqlalchemy.exc import IntegrityError
from services.bot_candidate_summary_service import (
    SUMMARY_STATUS_BLOCKED_PENDING_CORRECTIONS,
    SUMMARY_STATUS_INCOMPLETE,
    SUMMARY_STATUS_READY_FOR_REVIEW,
    SUMMARY_STATUS_REQUIRES_HUMAN,
    build_candidate_summary,
    get_candidate_summary_status,
)
from services.bot_data_safety_helpers import as_dict, as_list, mask_sensitive_doc_fields
from utils.audit_logger import log_action
from utils.timezone import utc_now_naive

DRAFT_STATUS_DRAFT = "draft"
DRAFT_STATUS_UNDER_REVIEW = "under_review"
DRAFT_STATUS_APPROVED_FOR_CREATION = "approved_for_creation"
DRAFT_STATUS_REJECTED = "rejected"

ALLOWED_SUMMARY_STATUSES = {SUMMARY_STATUS_READY_FOR_REVIEW, SUMMARY_STATUS_REQUIRES_HUMAN}


def _active_pending_corrections(conversation: Any) -> list[dict[str, Any]]:
    metadata = as_dict(getattr(conversation, "metadata_json", {}) or {})
    items = as_list(metadata.get("pending_corrections"))
    return [x for x in items if isinstance(x, dict) and str(x.get("status") or "") == "pending_human"]


def _safe_log_action(**kwargs: Any) -> None:
    try:
        log_action(**kwargs)
    except RuntimeError:
        # Permite uso en pruebas/unit fuera de request context sin afectar flujo principal.
        return


def get_candidate_draft(conversation_id: int) -> BotCandidateDraft | None:
    return BotCandidateDraft.query.filter_by(conversation_id=int(conversation_id)).first()


def can_create_candidate_draft(conversation: Any) -> dict[str, Any]:
    summary_status = get_candidate_summary_status(conversation)
    summary = build_candidate_summary(conversation)
    if get_candidate_draft(int(conversation.id)) is not None:
        return {"allowed": False, "reason": "draft_already_exists", "summary_status": summary_status}
    if _active_pending_corrections(conversation):
        return {"allowed": False, "reason": "pending_corrections", "summary_status": summary_status}
    if summary_status not in ALLOWED_SUMMARY_STATUSES:
        return {"allowed": False, "reason": "summary_status_not_allowed", "summary_status": summary_status}
    if summary.get("missing_required_fields"):
        return {"allowed": False, "reason": "missing_required_fields", "summary_status": summary_status}
    return {"allowed": True, "reason": "ok", "summary_status": summary_status}


def build_candidate_draft_payload(conversation: Any) -> dict[str, Any]:
    metadata = as_dict(getattr(conversation, "metadata_json", {}) or {})
    protocol_entities = as_dict(metadata.get("protocol_entities"))
    pending_corrections = as_list(metadata.get("pending_corrections"))
    summary = build_candidate_summary(conversation)
    summary_status = get_candidate_summary_status(conversation)

    masked_entities = mask_sensitive_doc_fields(copy.deepcopy(protocol_entities))
    masked_pending = mask_sensitive_doc_fields(copy.deepcopy(pending_corrections))

    return {
        "protocol_version": str((metadata.get("protocol_version") or "domesticas_v1")),
        "summary_status": summary_status,
        "requires_human": bool(summary_status == SUMMARY_STATUS_REQUIRES_HUMAN),
        "sensitive_detected": bool(summary.get("has_sensitive_fields")),
        "metadata_json": {
            "summary": summary,
            "summary_status": summary_status,
            "conversation_phone_e164": getattr(conversation, "phone_e164", None),
        },
        "source_protocol_entities": masked_entities,
        "source_pending_corrections_snapshot": masked_pending,
    }


def create_candidate_draft(conversation: Any, actor_id: int | None) -> BotCandidateDraft:
    check = can_create_candidate_draft(conversation)
    if not bool(check.get("allowed")):
        raise ValueError(str(check.get("reason") or "draft_not_allowed"))

    payload = build_candidate_draft_payload(conversation)
    draft = BotCandidateDraft(
        conversation_id=int(conversation.id),
        protocol_version=payload["protocol_version"],
        draft_status=DRAFT_STATUS_DRAFT,
        summary_status=payload["summary_status"],
        created_by=actor_id,
        metadata_json=payload["metadata_json"],
        source_protocol_entities=payload["source_protocol_entities"],
        source_pending_corrections_snapshot=payload["source_pending_corrections_snapshot"],
        requires_human=bool(payload["requires_human"]),
        sensitive_detected=bool(payload["sensitive_detected"]),
    )
    db.session.add(draft)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise ValueError("draft_already_exists")

    _safe_log_action(
        action_type="candidate_draft_created",
        entity_type="BotCandidateDraft",
        entity_id=str(draft.id),
        summary="Borrador de candidata creado desde conversación bot",
        metadata={
            "actor_id": actor_id,
            "draft_id": draft.id,
            "conversation_id": int(conversation.id),
            "summary_status": draft.summary_status,
            "requires_human": draft.requires_human,
            "sensitive_detected": draft.sensitive_detected,
        },
        actor_user_id=actor_id,
        success=True,
    )
    return draft


def mark_candidate_draft_under_review(conversation_id: int, actor_id: int | None) -> BotCandidateDraft:
    draft = get_candidate_draft(int(conversation_id))
    if not draft:
        raise ValueError("draft_not_found")
    if draft.draft_status == DRAFT_STATUS_REJECTED:
        raise ValueError("draft_rejected")

    draft.draft_status = DRAFT_STATUS_UNDER_REVIEW
    draft.reviewed_by = actor_id
    draft.reviewed_at = utc_now_naive()
    draft.updated_at = utc_now_naive()
    db.session.commit()

    _safe_log_action(
        action_type="candidate_draft_reviewed",
        entity_type="BotCandidateDraft",
        entity_id=str(draft.id),
        summary="Borrador de candidata marcado bajo revisión",
        metadata={
            "actor_id": actor_id,
            "draft_id": draft.id,
            "conversation_id": int(conversation_id),
            "summary_status": draft.summary_status,
            "requires_human": draft.requires_human,
            "sensitive_detected": draft.sensitive_detected,
        },
        actor_user_id=actor_id,
        success=True,
    )
    return draft


def reject_candidate_draft(conversation_id: int, actor_id: int | None, notes: str | None = None) -> BotCandidateDraft:
    draft = get_candidate_draft(int(conversation_id))
    if not draft:
        raise ValueError("draft_not_found")
    if draft.draft_status == DRAFT_STATUS_REJECTED:
        raise ValueError("draft_already_rejected")

    draft.draft_status = DRAFT_STATUS_REJECTED
    draft.reviewed_by = actor_id
    draft.reviewed_at = utc_now_naive()
    draft.notes = (notes or "").strip() or draft.notes
    draft.updated_at = utc_now_naive()
    db.session.commit()

    _safe_log_action(
        action_type="candidate_draft_rejected",
        entity_type="BotCandidateDraft",
        entity_id=str(draft.id),
        summary="Borrador de candidata rechazado",
        metadata={
            "actor_id": actor_id,
            "draft_id": draft.id,
            "conversation_id": int(conversation_id),
            "summary_status": draft.summary_status,
            "requires_human": draft.requires_human,
            "sensitive_detected": draft.sensitive_detected,
        },
        actor_user_id=actor_id,
        success=True,
    )
    return draft


def get_or_create_interview_flow_draft(conversation: Any) -> tuple[BotCandidateDraft, bool]:
    existing = get_candidate_draft(int(conversation.id))
    if existing is not None:
        return existing, False

    metadata = as_dict(getattr(conversation, "metadata_json", {}) or {})
    flow = as_dict(metadata.get("interview_flow"))
    if not bool(flow.get("completed")):
        raise ValueError("interview_not_completed")

    data = as_dict(flow.get("collected_data"))
    summary_text = str(flow.get("summary") or "").strip()
    protocol_entities = {
        "name": str(data.get("full_name") or "").strip(),
        "age": data.get("age"),
        "city": str(data.get("city_sector") or "").strip(),
        "experience_skills": str(data.get("experience") or "").strip(),
        "work_type": str(data.get("availability") or "").strip(),
        "work_references": str(data.get("references") or "").strip(),
        "phone_e164": str(getattr(conversation, "phone_e164", "") or "").strip(),
    }
    skills = data.get("skills")
    if isinstance(skills, list) and skills:
        protocol_entities["experience_skills"] = ", ".join(str(x) for x in skills if str(x).strip())

    payload = {
        "summary": {
            "status": "pendiente_revision_bot",
            "source": "whatsapp_bot",
            "conversation_id": int(conversation.id),
            "telefono": str(getattr(conversation, "phone_e164", "") or "").strip(),
            "nombre": str(data.get("full_name") or "").strip(),
            "edad": data.get("age"),
            "ciudad_sector": str(data.get("city_sector") or "").strip(),
            "experiencia": str(data.get("experience") or "").strip(),
            "funciones": data.get("skills") or [],
            "disponibilidad": str(data.get("availability") or "").strip(),
            "referencias": str(data.get("references") or "").strip(),
            "resumen": summary_text,
        },
        "summary_status": "requires_human",
        "conversation_phone_e164": getattr(conversation, "phone_e164", None),
        "intake": {
            "status": "pending_review",
            "origin": "whatsapp_bot",
            "quality_score": 0,
            "quality_flags": [],
            "duplicates": [],
            "history": [],
        },
    }
    draft = BotCandidateDraft(
        conversation_id=int(conversation.id),
        protocol_version="interview_flow_v1",
        draft_status=DRAFT_STATUS_DRAFT,
        summary_status="requires_human",
        created_by=None,
        metadata_json=payload,
        source_protocol_entities=protocol_entities,
        source_pending_corrections_snapshot=[],
        requires_human=True,
        sensitive_detected=False,
    )
    db.session.add(draft)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        again = get_candidate_draft(int(conversation.id))
        if again is None:
            raise
        return again, False
    return draft, True
