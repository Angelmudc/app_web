from __future__ import annotations

from typing import Any

from services.bot_data_safety_helpers import as_dict, as_list, first_non_empty, mask_cedula_like


SUMMARY_STATUS_INCOMPLETE = "incomplete"
SUMMARY_STATUS_READY_FOR_REVIEW = "ready_for_review"
SUMMARY_STATUS_BLOCKED_PENDING_CORRECTIONS = "blocked_pending_corrections"
SUMMARY_STATUS_REQUIRES_HUMAN = "requires_human"

_REQUIRED_FIELDS = (
    "name",
    "age",
    "city",
    "work_type",
    "route",
    "acceptance_25",
    "references_any",
)


def _normalize_field_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    if isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(x).strip() for x in value if str(x).strip()) or None
    if isinstance(value, dict):
        return ", ".join(f"{k}: {v}" for k, v in value.items()) or None
    return str(value)


def _mask_cedula_like_in_text(value: Any) -> Any:
    text = _normalize_field_value(value)
    if not isinstance(text, str):
        return text
    return mask_cedula_like(text)


def _normalize_acceptance_25(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "acepta" if value else "no_acepta"
    raw = str(value).strip().lower()
    if not raw:
        return None
    if raw in {"si", "sí", "acepto", "acepta", "true", "1", "25", "25%"}:
        return "acepta"
    if raw in {"no", "false", "0", "no_acepta", "rechaza"}:
        return "no_acepta"
    return raw


def _active_pending_corrections(conversation: Any) -> list[dict[str, Any]]:
    metadata = as_dict(getattr(conversation, "metadata_json", {}) or {})
    items = as_list(metadata.get("pending_corrections"))
    return [x for x in items if isinstance(x, dict) and str(x.get("status") or "") == "pending_human"]


def _has_sensitive_fields(entities: dict[str, Any]) -> bool:
    sensitive_keys = {
        "cedula",
        "cedula_masked",
        "cedula_detected",
        "documentos",
        "documentos_indicados",
        "foto",
        "foto_indicada",
        "photo_indicated",
    }
    for key in sensitive_keys:
        value = entities.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if key == "cedula_detected":
            if bool(value):
                return True
            continue
        return True
    return False


def build_candidate_summary(conversation: Any) -> dict[str, Any]:
    metadata = as_dict(getattr(conversation, "metadata_json", {}) or {})
    entities = as_dict(metadata.get("protocol_entities"))
    phone_value = first_non_empty(entities, "phone", "telefono", "phone_e164")
    if phone_value is None:
        phone_value = getattr(conversation, "phone_e164", None)

    summary_fields = {
        "name": _normalize_field_value(first_non_empty(entities, "name", "nombre")),
        "age": _normalize_field_value(first_non_empty(entities, "age", "edad")),
        "phone": _normalize_field_value(phone_value),
        "city": _normalize_field_value(first_non_empty(entities, "city", "ciudad", "address", "direccion")),
        "sector_address": _normalize_field_value(first_non_empty(entities, "sector_address", "sector", "direccion", "address")),
        "work_type": _normalize_field_value(first_non_empty(entities, "work_type", "modalidad_deseada", "modalidad")),
        "route": _normalize_field_value(first_non_empty(entities, "route", "transport_route", "ruta", "transporte")),
        "experience_skills": _normalize_field_value(first_non_empty(entities, "experience_skills", "experiencia_habilidades", "experiencia", "habilidades")),
        "work_references": _mask_cedula_like_in_text(
            first_non_empty(entities, "work_references", "referencias_laborales", "contactos_referencias_laborales")
        ),
        "family_references": _mask_cedula_like_in_text(
            first_non_empty(entities, "family_references", "referencias_familiares", "referencias_familiares_detalle")
        ),
        "acceptance_25": _normalize_acceptance_25(
            first_non_empty(entities, "acceptance_25", "aceptacion_25", "percentage_acceptance", "acepta_porcentaje_sueldo")
        ),
        "documents_indicated": _mask_cedula_like_in_text(
            first_non_empty(entities, "documents_indicated", "documentos_indicados", "documentos")
        ),
        "photo_indicated": _normalize_field_value(first_non_empty(entities, "photo_indicated", "foto_indicada", "foto")),
        "observations": _mask_cedula_like_in_text(first_non_empty(entities, "observations", "observaciones", "notes")),
    }

    active_pending_corrections = _active_pending_corrections(conversation)
    missing_required_fields = get_missing_required_candidate_fields(conversation)

    return {
        "fields": summary_fields,
        "missing_required_fields": missing_required_fields,
        "pending_corrections_active": active_pending_corrections,
        "has_sensitive_fields": _has_sensitive_fields(entities),
    }


def get_missing_required_candidate_fields(conversation: Any) -> list[str]:
    metadata = as_dict(getattr(conversation, "metadata_json", {}) or {})
    entities = as_dict(metadata.get("protocol_entities"))
    phone_value = first_non_empty(entities, "phone", "telefono", "phone_e164") or getattr(conversation, "phone_e164", None)
    has_any_phone_source = phone_value is not None
    work_references = _normalize_field_value(first_non_empty(entities, "work_references", "referencias_laborales", "contactos_referencias_laborales"))
    family_references = _normalize_field_value(first_non_empty(entities, "family_references", "referencias_familiares", "referencias_familiares_detalle"))
    fields = {
        "name": _normalize_field_value(first_non_empty(entities, "name", "nombre")),
        "age": _normalize_field_value(first_non_empty(entities, "age", "edad")),
        "phone": _normalize_field_value(phone_value),
        "city": _normalize_field_value(first_non_empty(entities, "city", "ciudad", "address", "direccion", "sector", "sector_address")),
        "work_type": _normalize_field_value(first_non_empty(entities, "work_type", "modalidad_deseada", "modalidad")),
        "route": _normalize_field_value(first_non_empty(entities, "route", "transport_route", "ruta", "transporte")),
        "acceptance_25": _normalize_acceptance_25(
            first_non_empty(entities, "acceptance_25", "aceptacion_25", "percentage_acceptance", "acepta_porcentaje_sueldo")
        ),
        "references_any": work_references or family_references,
    }

    missing: list[str] = []
    for key in _REQUIRED_FIELDS:
        value = fields.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(key)
    if not has_any_phone_source and "phone" in missing:
        missing.remove("phone")
    if has_any_phone_source and (fields.get("phone") is None) and ("phone" not in missing):
        missing.append("phone")
    return missing


def get_candidate_summary_status(conversation: Any) -> str:
    summary = build_candidate_summary(conversation)
    if summary["pending_corrections_active"]:
        return SUMMARY_STATUS_BLOCKED_PENDING_CORRECTIONS
    if summary["has_sensitive_fields"]:
        return SUMMARY_STATUS_REQUIRES_HUMAN
    if summary["missing_required_fields"]:
        return SUMMARY_STATUS_INCOMPLETE
    return SUMMARY_STATUS_READY_FOR_REVIEW
