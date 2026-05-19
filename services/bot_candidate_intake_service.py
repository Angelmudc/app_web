from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from config_app import db
from models import BotCandidateDraft, BotConversation, Candidata
from sqlalchemy import inspect as sa_inspect, text
from services.bot_candidate_creation_service import create_candidate_from_draft
from services.bot_candidate_conversion_preview_service import detect_existing_candidate_conflicts, map_draft_to_candidate_fields
from services.bot_data_safety_helpers import as_dict, as_list, norm_text
from utils.timezone import utc_now_naive

INTAKE_PENDING_REVIEW = "pending_review"
INTAKE_APPROVED = "approved"
INTAKE_REJECTED = "rejected"
INTAKE_DUPLICATE = "duplicate"
INTAKE_NEEDS_FOLLOWUP = "needs_followup"
INTAKE_INCOMPLETE = "incomplete"

INTAKE_STATUSES = {
    INTAKE_PENDING_REVIEW,
    INTAKE_APPROVED,
    INTAKE_REJECTED,
    INTAKE_DUPLICATE,
    INTAKE_NEEDS_FOLLOWUP,
    INTAKE_INCOMPLETE,
}


def _utc_iso() -> str:
    return utc_now_naive().isoformat()


def _intake_meta(draft: BotCandidateDraft) -> dict[str, Any]:
    meta = dict(getattr(draft, "metadata_json", {}) or {})
    intake = dict(meta.get("intake") or {})
    meta["intake"] = intake
    return meta


def compute_intake_quality(draft: BotCandidateDraft, conversation: BotConversation | None) -> dict[str, Any]:
    score = 30
    flags: list[str] = []
    flow = as_dict(as_dict(getattr(conversation, "metadata_json", {}) or {}).get("interview_flow"))
    data = as_dict(flow.get("collected_data"))
    help_counts = as_dict(flow.get("help_repeat_count_by_step"))
    invalid_count = sum(int(v or 0) for v in help_counts.values() if str(v).strip().isdigit())
    summary = str(flow.get("summary") or "").strip()

    if bool(flow.get("completed")):
        score += 20
    else:
        flags.append("flow_incomplete")
    if str(data.get("references") or "").strip():
        score += 15
    else:
        flags.append("references_missing")
    skills = data.get("skills")
    if isinstance(skills, list) and skills:
        score += 10
    else:
        flags.append("skills_missing")
    if str(data.get("experience") or "").strip():
        score += 10
    else:
        flags.append("experience_missing")
    if str(data.get("availability") or "").strip():
        score += 10
    else:
        flags.append("availability_missing")
    if len(summary) >= 40:
        score += 5
    else:
        flags.append("summary_short")

    if invalid_count > 0:
        score -= min(35, invalid_count * 5)
        flags.append("has_invalid_answers")
    if invalid_count >= 4:
        flags.append("needs_extra_guidance")

    score = max(0, min(100, int(score)))
    return {"quality_score": score, "quality_flags": sorted(set(flags)), "invalid_answers_count": int(invalid_count)}


def detect_intake_duplicates(draft: BotCandidateDraft) -> list[dict[str, Any]]:
    mapped = map_draft_to_candidate_fields(draft)
    conflicts = list(detect_existing_candidate_conflicts(mapped))
    name = norm_text(mapped.get("nombre_completo"))
    city = norm_text(mapped.get("ciudad"))
    cols = {str(c.get("name")) for c in sa_inspect(db.session.get_bind()).get_columns("candidatas")}
    if name and city and "direccion_completa" in cols:
        rows = db.session.execute(
            text("SELECT fila, nombre_completo, direccion_completa FROM candidatas WHERE nombre_completo IS NOT NULL LIMIT 400")
        ).all()
        for row in rows:
            cand_name = norm_text(row[1] if len(row) > 1 else "")
            cand_city = norm_text(row[2] if len(row) > 2 else "")
            if not cand_name or not cand_city:
                continue
            if city.lower() in cand_city.lower() and SequenceMatcher(a=name.lower(), b=cand_name.lower()).ratio() >= 0.9:
                conflicts.append({"type": "name_city_similar", "candidate_id": int(row[0])})
                break
    return conflicts


def ensure_intake_fields(draft: BotCandidateDraft, conversation: BotConversation | None) -> dict[str, Any]:
    meta = _intake_meta(draft)
    intake = meta["intake"]
    if not intake.get("status"):
        intake["status"] = INTAKE_PENDING_REVIEW if str(draft.summary_status or "") != "incomplete" else INTAKE_INCOMPLETE
    quality = compute_intake_quality(draft, conversation)
    intake["quality_score"] = int(quality["quality_score"])
    intake["quality_flags"] = list(quality["quality_flags"])
    intake["invalid_answers_count"] = int(quality["invalid_answers_count"])
    dups = detect_intake_duplicates(draft)
    intake["duplicates"] = dups
    intake["origin"] = "whatsapp_bot"
    intake.setdefault("history", [])
    meta["intake"] = intake
    draft.metadata_json = meta
    return intake


def set_intake_status(draft: BotCandidateDraft, *, status: str, actor_id: int | None, note: str = "") -> dict[str, Any]:
    if status not in INTAKE_STATUSES:
        raise ValueError("invalid_intake_status")
    meta = _intake_meta(draft)
    intake = meta["intake"]
    intake["status"] = status
    intake["reviewed_by"] = actor_id
    intake["reviewed_at"] = _utc_iso()
    intake["review_note"] = (note or "").strip()
    history = as_list(intake.get("history"))
    history.append({"at": _utc_iso(), "actor_id": actor_id, "status": status, "note": (note or "").strip()})
    intake["history"] = history[-30:]
    meta["intake"] = intake
    draft.metadata_json = meta
    return intake


def edit_intake_before_approve(draft: BotCandidateDraft, payload: dict[str, Any]) -> None:
    entities = dict(getattr(draft, "source_protocol_entities", {}) or {})
    mapping = {
        "name": "name",
        "age": "age",
        "city": "city",
        "availability": "work_type",
        "references": "work_references",
        "experience": "experience_skills",
        "cedula": "cedula",
    }
    for src, dst in mapping.items():
        val = str(payload.get(src) or "").strip()
        if val:
            entities[dst] = val
    draft.source_protocol_entities = entities


def convert_intake_to_candidate(draft: BotCandidateDraft, *, actor_id: int | None, review_id: int) -> int:
    meta = _intake_meta(draft)
    if str(draft.draft_status or "") == "converted":
        candidate_id = int((meta.get("created_candidate_id") or 0))
        if candidate_id > 0:
            return candidate_id
        raise ValueError("converted_without_candidate_id")

    draft.draft_status = "approved_for_creation"
    db.session.flush()
    created = create_candidate_from_draft(draft, actor_id=actor_id)
    candidate_id = int(getattr(created, "fila"))

    refreshed_meta = dict(getattr(draft, "metadata_json", {}) or {})
    intake = dict(refreshed_meta.get("intake") or {})
    intake["status"] = INTAKE_APPROVED
    intake["review_id"] = int(review_id)
    intake["conversation_id"] = int(draft.conversation_id)
    intake["intake_id"] = int(draft.id)
    intake["converted_candidate_id"] = int(candidate_id)
    intake["converted_at"] = _utc_iso()
    intake["converted_by"] = actor_id
    refreshed_meta["intake"] = intake
    refreshed_meta["source"] = "whatsapp_bot"
    refreshed_meta["review_id"] = int(review_id)
    draft.metadata_json = refreshed_meta
    return candidate_id
