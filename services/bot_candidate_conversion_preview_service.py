from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from config_app import db
from sqlalchemy import text
from services.bot_data_safety_helpers import as_dict, as_list, first_non_empty, mask_cedula_like, norm_text
from services.bot_candidate_draft_service import DRAFT_STATUS_DRAFT, DRAFT_STATUS_UNDER_REVIEW

PREVIEW_STATUS_READY_TO_CONVERT = "ready_to_convert"
PREVIEW_STATUS_BLOCKED_MISSING_FIELDS = "blocked_missing_fields"
PREVIEW_STATUS_BLOCKED_DRAFT_STATUS = "blocked_draft_status"
PREVIEW_STATUS_BLOCKED_CONFLICTS = "blocked_conflicts"
PREVIEW_STATUS_REQUIRES_HUMAN_REVIEW = "requires_human_review"

_ALLOWED_DRAFT_STATUSES = {DRAFT_STATUS_DRAFT, DRAFT_STATUS_UNDER_REVIEW}
_REQUIRED_PREVIEW_FIELDS = ("nombre_completo", "edad", "numero_telefono", "direccion_completa", "modalidad_trabajo_preferida")


def _cedula_digits(value: Any) -> str:
    return re.sub(r"\D+", "", str(value or ""))


def map_draft_to_candidate_fields(draft: Any) -> dict[str, Any]:
    entities = as_dict(getattr(draft, "source_protocol_entities", {}) or {})
    summary_fields = as_dict(as_dict(getattr(draft, "metadata_json", {}) or {}).get("summary")).get("fields") or {}

    phone = norm_text(first_non_empty(entities, "phone_e164", "phone", "telefono")) or norm_text(summary_fields.get("phone"))
    city = norm_text(first_non_empty(entities, "city", "ciudad")) or norm_text(summary_fields.get("city"))
    sector = norm_text(first_non_empty(entities, "sector_address", "sector", "address", "direccion")) or norm_text(
        summary_fields.get("sector_address")
    )
    direccion = norm_text(", ".join([x for x in [city, sector] if x])) or city or sector

    cedula_raw = norm_text(first_non_empty(entities, "cedula", "dni", "documento", "cedula_masked"))
    cedula_masked = "<redacted>" if cedula_raw == "<redacted>" else mask_cedula_like(cedula_raw)

    return {
        "nombre_completo": norm_text(first_non_empty(entities, "name", "nombre")) or norm_text(summary_fields.get("name")),
        "edad": norm_text(first_non_empty(entities, "age", "edad")) or norm_text(summary_fields.get("age")),
        "numero_telefono": phone,
        "telefono_e164": phone if (phone or "").startswith("+") else None,
        "ciudad": city,
        "sector": sector,
        "direccion_completa": direccion,
        "modalidad_trabajo_preferida": norm_text(first_non_empty(entities, "work_type", "modalidad_deseada", "modalidad"))
        or norm_text(summary_fields.get("work_type")),
        "rutas_cercanas": norm_text(first_non_empty(entities, "route", "transport_route", "ruta", "transporte"))
        or norm_text(summary_fields.get("route")),
        "areas_experiencia": norm_text(first_non_empty(entities, "experience_skills", "experiencia_habilidades", "experiencia", "habilidades"))
        or norm_text(summary_fields.get("experience_skills")),
        "contactos_referencias_laborales": norm_text(
            first_non_empty(entities, "work_references", "referencias_laborales", "contactos_referencias_laborales")
        )
        or norm_text(summary_fields.get("work_references")),
        "referencias_familiares_detalle": norm_text(
            first_non_empty(entities, "family_references", "referencias_familiares", "referencias_familiares_detalle")
        )
        or norm_text(summary_fields.get("family_references")),
        "acepta_porcentaje_sueldo": norm_text(
            first_non_empty(entities, "acceptance_25", "aceptacion_25", "acepta_porcentaje_sueldo")
        )
        or norm_text(summary_fields.get("acceptance_25")),
        "observaciones_internas_preview": norm_text(first_non_empty(entities, "observations", "observaciones", "notes"))
        or norm_text(summary_fields.get("observations")),
        "cedula_masked": cedula_masked,
    }


def detect_existing_candidate_conflicts(preview: dict[str, Any]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    phone = norm_text(preview.get("telefono_e164") or preview.get("numero_telefono"))
    name = norm_text(preview.get("nombre_completo"))
    cedula_masked = norm_text(preview.get("cedula_masked"))
    cedula_digits = _cedula_digits(cedula_masked)

    if phone:
        try:
            by_phone = db.session.execute(
                text("SELECT fila FROM candidatas WHERE telefono_e164 = :p OR numero_telefono = :p LIMIT 1"),
                {"p": phone},
            ).first()
            if by_phone:
                conflicts.append({"type": "phone_duplicate", "candidate_id": int(by_phone[0]), "value_masked": phone})
        except Exception:
            pass

    if name:
        try:
            rows = db.session.execute(text("SELECT fila, nombre_completo FROM candidatas WHERE nombre_completo IS NOT NULL LIMIT 200")).all()
            for row in rows:
                cand_name = norm_text(row[1])
                if not cand_name:
                    continue
                ratio = SequenceMatcher(a=name.lower(), b=cand_name.lower()).ratio()
                if ratio >= 0.9:
                    conflicts.append({"type": "name_similar", "candidate_id": int(row[0]), "value_masked": cand_name, "score": round(ratio, 3)})
                    break
        except Exception:
            pass

    if len(cedula_digits) == 11:
        try:
            by_cedula = db.session.execute(
                text("SELECT fila FROM candidatas WHERE cedula_norm_digits = :c LIMIT 1"),
                {"c": cedula_digits},
            ).first()
            if by_cedula:
                conflicts.append({"type": "cedula_duplicate", "candidate_id": int(by_cedula[0]), "value_masked": mask_cedula_like(cedula_masked)})
        except Exception:
            pass

    return conflicts


def validate_candidate_conversion_preview(draft: Any) -> dict[str, Any]:
    preview_fields = map_draft_to_candidate_fields(draft)
    missing_required_fields = [x for x in _REQUIRED_PREVIEW_FIELDS if not norm_text(preview_fields.get(x))]
    pending_snapshot = as_list(getattr(draft, "source_pending_corrections_snapshot", []) or [])
    pending_human = [x for x in pending_snapshot if isinstance(x, dict) and str(x.get("status") or "") == "pending_human"]
    status_allowed = str(getattr(draft, "draft_status", "") or "") in _ALLOWED_DRAFT_STATUSES

    conflicts = detect_existing_candidate_conflicts(preview_fields)
    warnings: list[str] = []
    if bool(getattr(draft, "sensitive_detected", False)):
        warnings.append("sensitive_data_detected")
    if pending_human:
        warnings.append("pending_corrections_in_snapshot")
    if norm_text(preview_fields.get("cedula_masked")) in {"<redacted>", None}:
        warnings.append("cedula_not_available_or_masked")

    if not status_allowed:
        preview_status = PREVIEW_STATUS_BLOCKED_DRAFT_STATUS
    elif missing_required_fields:
        preview_status = PREVIEW_STATUS_BLOCKED_MISSING_FIELDS
    elif conflicts:
        preview_status = PREVIEW_STATUS_BLOCKED_CONFLICTS
    elif warnings:
        preview_status = PREVIEW_STATUS_REQUIRES_HUMAN_REVIEW
    else:
        preview_status = PREVIEW_STATUS_READY_TO_CONVERT

    return {
        "preview_status": preview_status,
        "missing_required_fields": missing_required_fields,
        "pending_human_corrections": pending_human,
        "warnings": warnings,
        "conflicts": conflicts,
    }


def build_candidate_conversion_preview(draft: Any) -> dict[str, Any]:
    preview_fields = map_draft_to_candidate_fields(draft)
    validation = validate_candidate_conversion_preview(draft)
    return {
        "status": validation["preview_status"],
        "mapped_fields": preview_fields,
        "missing_required_fields": validation["missing_required_fields"],
        "conflicts": validation["conflicts"],
        "warnings": validation["warnings"],
        "pending_human_corrections": validation["pending_human_corrections"],
        "is_preview_only": True,
    }
