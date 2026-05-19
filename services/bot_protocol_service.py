"""Motor simple de protocolo conversacional por etapas (manual-only)."""

from __future__ import annotations

import json
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "bot_protocol_domesticas_v1.json"
SUPPORTED_CITIES = ("santiago", "puerto plata")
YES_WORDS = ("si", "sí", "yes", "ok", "claro")
NO_WORDS = ("no", "nop", "negativo")
POSITIVE_CONTINUE_HINTS = (
    "dale",
    "vamos",
    "correcto",
    "si soy yo",
    "soy yo",
    "hazme las preguntas",
    "hasme las preguntas",
    "quiero registrarme",
    "kiero registrarme",
    "quiero trabajar",
    "kiero trabajar",
)
PERSONAL_CONFIRMATION_POSITIVE = (
    "si",
    "sí",
    "sii",
    "soy yo",
    "si soy yo",
    "correcto",
    "claro",
    "dale",
    "quiero trabajar",
    "kiero trabajar",
    "quiero registrarme",
    "kiero registrarme",
    "hazme las preguntas",
    "hasme las preguntas",
)
PERSONAL_CONFIRMATION_NEGATIVE = (
    "no",
    "no soy yo",
    "no soy",
    "numero equivocado",
    "numero incorrecto",
    "se equivoco",
    "te equivocaste",
)
GREETING_ONLY_PATTERNS = (
    "hola",
    "hols",
    "ola",
    "buenas",
    "buenas tardes",
    "buen dia",
    "hey",
    "saludos",
    "???",
)
GREETING_TOKENS = {"hola", "hols", "ola", "buenas", "buen", "dia", "dias", "tardes", "saludos", "hey", "otra", "vez"}
NON_NAME_TOKENS = {
    "tal",
    "vez",
    "no",
    "se",
    "nose",
    "quizas",
    "quiza",
    "ok",
    "claro",
    "dale",
    "gracias",
    "bueno",
}
CONFUSION_PREFIXES = ("que", "quien", "como", "donde", "cuando", "por que", "porque")
CONFUSION_PATTERNS = (
    "no entiendo",
    "no entendi",
    "explica",
    "explicame",
    "explícame",
    "no se",
    "nose",
    "otra vez",
    "ayuda",
    "que significa",
    "quien pregunta",
    "quien e",
    "que tengo que hacer",
)
WELCOME_FASTPATH_DENY_PATTERNS = (
    "vendo",
    "promo",
    "tenis",
    "barato",
    "oferta",
    "quien pregunta",
    "quien e",
    "numero equivocado",
    "no soy",
    "no entiendo",
    "que es esto",
    "spam-like",
)
STOPWORDS_NAME = {
    "me",
    "llamo",
    "mi",
    "nombre",
    "es",
    "soy",
    "tengo",
    "anos",
    "año",
    "años",
    "edad",
    "y",
}
WORK_TYPE_HINTS = ("dormida", "domida", "dormia", "salida", "diaria", "fija", "interna", "externo", "externa")
TRANSPORT_HINTS = (
    "ruta",
    "concho",
    "carro",
    "guagua",
    "motoconcho",
    "taxi",
    "uber",
    "camino",
    "parada",
    "me llevan",
    "voy en",
    "me lleva",
    "me llevan",
    "llevar",
    "motor",
)
COMMON_SECTORS = ("gurabo",)
REFERENCE_HINTS = ("referencia", "jefa", "patrona", "senora", "doña", "dona", "hermana", "mama", "madre")
SKILL_HINTS = ("cocinar", "limpiar", "ninos", "niños", "envejecientes", "lavar", "planchar", "cuidar")
SKILL_EXPERIENCE_HINTS = ("se ", "trabaje", "trabajé", "experiencia", "hago", "he hecho", "puedo", "yo cocino", "yo limpio")
CEDULA_CONTEXT_HINTS = ("cedula", "cédula", "documento", "identidad")


@lru_cache(maxsize=1)
def load_protocol() -> dict[str, Any]:
    try:
        with DATA_PATH.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except FileNotFoundError as exc:
        raise ValueError(f"Protocolo no encontrado: {DATA_PATH}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Protocolo inválido (JSON): {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Protocolo inválido: raíz debe ser objeto")
    steps = payload.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("Protocolo inválido: steps vacío")
    required = ("step_code", "messages", "validations", "expected_answers", "fallback")
    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError(f"Protocolo inválido: step en índice {idx} no es objeto")
        missing = [key for key in required if key not in step]
        if missing:
            raise ValueError(f"Protocolo inválido: step {step.get('step_code') or idx} sin campos {missing}")
        if not str(step.get("step_code") or "").strip():
            raise ValueError(f"Protocolo inválido: step en índice {idx} sin step_code válido")
        messages = step.get("messages")
        if not isinstance(messages, dict):
            raise ValueError(f"Protocolo inválido: step {step.get('step_code')} con messages inválido")
        for msg_key in ("primary", "secondary", "warnings"):
            msg_list = messages.get(msg_key)
            if not isinstance(msg_list, list):
                raise ValueError(f"Protocolo inválido: step {step.get('step_code')} con messages.{msg_key} inválido")
        validations = step.get("validations")
        if not isinstance(validations, list):
            raise ValueError(f"Protocolo inválido: step {step.get('step_code')} con validations inválido")
        expected_answers = step.get("expected_answers")
        if not isinstance(expected_answers, list):
            raise ValueError(f"Protocolo inválido: step {step.get('step_code')} con expected_answers inválido")
    return payload


def get_step(step_code: str) -> dict[str, Any] | None:
    normalized = (step_code or "").strip().upper()
    if not normalized:
        return None
    for step in load_protocol()["steps"]:
        if str(step.get("step_code") or "").upper() == normalized:
            return step
    return None


def get_next_step(step_code: str) -> dict[str, Any] | None:
    normalized = (step_code or "").strip().upper()
    steps = load_protocol()["steps"]
    for idx, step in enumerate(steps):
        if str(step.get("step_code") or "").upper() == normalized:
            next_idx = idx + 1
            return steps[next_idx] if next_idx < len(steps) else None
    return None


def build_step_prompt(step_code: str) -> str:
    step = get_step(step_code)
    if not step:
        return "Etapa no encontrada."
    messages = step.get("messages") or {}
    primary = [str(x).strip() for x in (messages.get("primary") or []) if str(x).strip()]
    secondary = [str(x).strip() for x in (messages.get("secondary") or []) if str(x).strip()]
    warnings = [str(x).strip() for x in (messages.get("warnings") or []) if str(x).strip()]

    lines: list[str] = []
    lines.extend(primary)
    lines.extend(secondary)
    if warnings:
        lines.append("Advertencia: " + " ".join(warnings))
    return "\n".join(lines).strip() or "Sin mensaje configurado para esta etapa."


def detect_expected_answer(step_code: str, user_text: str) -> dict[str, Any]:
    text = _normalize_text(user_text)
    step = get_step(step_code)
    if not step:
        return {"matched": False, "reason": "step_not_found"}
    extraction = extract_step_entities(step_code, user_text)
    if extraction.get("schema_mode"):
        return {
            "matched": bool(extraction.get("matched")),
            "checks": extraction.get("checks") or {},
            "expected_answers": step.get("expected_answers") or [],
            "fallback": step.get("fallback") or "",
            "missing_fields": extraction.get("missing_fields") or [],
            "entities": extraction.get("entities") or {},
            "required_fields": extraction.get("required_fields") or [],
            "optional_fields": extraction.get("optional_fields") or [],
            "requires_human": bool(extraction.get("requires_human", False)),
        }
    out_of_step = detect_out_of_step_answer(step_code, user_text)
    if out_of_step.get("out_of_step"):
        return {
            "matched": False,
            "checks": {"out_of_step": True},
            "expected_answers": step.get("expected_answers") or [],
            "fallback": step.get("fallback") or "",
            "out_of_step": out_of_step,
        }

    validations = [str(x).strip().lower() for x in (step.get("validations") or [])]
    if (step_code or "").strip().upper() == "TRANSPORT_ROUTE" and "transport_route" not in validations:
        validations = ["transport_route"]
    checks: dict[str, bool] = {}

    for rule in validations:
        if (step_code or "").strip().upper() == "PERCENTAGE_ACCEPTANCE" and rule == "yes_no":
            if re.search(r"\b(ta bien|okey|okay|aja|ok)\b", text):
                checks[rule] = True
                continue
            if re.search(r"\b(acepto|aceptar|aceptado)\b", text) and (not re.search(r"\bno\b", text)):
                checks[rule] = True
                continue
        checks[rule] = _run_validation(rule, text)

    matched = all(checks.values()) if checks else bool(text)
    return {
        "matched": matched,
        "checks": checks,
        "expected_answers": step.get("expected_answers") or [],
        "fallback": step.get("fallback") or "",
    }


def detect_out_of_step_answer(step_code: str, user_text: str) -> dict[str, Any]:
    normalized = (step_code or "").strip().upper()
    text = _normalize_text(user_text)
    has_work_type = _has_work_type(text)
    has_address = any(city in text for city in SUPPORTED_CITIES)
    has_age = _extract_age(text) is not None
    if normalized == "TRANSPORT_ROUTE" and has_work_type and not _has_transport_route(text):
        return {
            "out_of_step": True,
            "detected_topic": "work_type",
            "suggested_step_code": "WORK_TYPE",
            "reason": "El usuario respondió sobre modalidad, no sobre transporte.",
        }
    if normalized == "TRANSPORT_ROUTE" and has_age and not _has_transport_route(text):
        return {
            "out_of_step": True,
            "detected_topic": "age",
            "suggested_step_code": "BASIC_INFO",
            "reason": "El usuario respondió sobre edad, no sobre transporte.",
        }
    if normalized == "ADDRESS" and has_work_type:
        return {
            "out_of_step": True,
            "detected_topic": "work_type",
            "suggested_step_code": "WORK_TYPE",
            "reason": "El usuario respondió sobre modalidad, no sobre dirección.",
        }
    if normalized == "PERCENTAGE_ACCEPTANCE" and has_address:
        return {
            "out_of_step": True,
            "detected_topic": "address",
            "suggested_step_code": "ADDRESS",
            "reason": "El usuario respondió sobre ubicación, no sobre aceptación de porcentaje.",
        }
    if normalized == "BASIC_INFO" and has_work_type:
        return {
            "out_of_step": True,
            "detected_topic": "work_type",
            "suggested_step_code": "WORK_TYPE",
            "reason": "El usuario respondió sobre modalidad, no sobre datos básicos.",
        }
    return {"out_of_step": False}


def extract_step_entities(step_code: str, user_text: str, existing_entities: dict[str, Any] | None = None) -> dict[str, Any]:
    normalized = (step_code or "").strip().upper()
    step = get_step(normalized)
    if not step:
        return {"matched": False, "reason": "step_not_found", "entities": {}, "missing_fields": []}

    text_raw = str(user_text or "").strip()
    text = _normalize_text(text_raw)
    previous = dict(existing_entities or {})

    if normalized == "PERSONAL_CONFIRMATION":
        if is_negative_confirmation(text):
            return {
                "schema_mode": True,
                "matched": False,
                "required_fields": [],
                "optional_fields": [],
                "missing_fields": [],
                "entities": {},
                "future_entities": {},
                "merged_entities": dict(previous),
                "checks": {"identity_confirmed": False},
                "requires_human": True,
            }
        if is_positive_confirmation(text, step_code=normalized):
            return {
                "schema_mode": True,
                "matched": True,
                "required_fields": [],
                "optional_fields": [],
                "missing_fields": [],
                "entities": {},
                "future_entities": {},
                "merged_entities": dict(previous),
                "checks": {"identity_confirmed": True},
                "requires_human": False,
            }
        if has_personal_data_signal(text):
            return {
                "schema_mode": True,
                "matched": True,
                "required_fields": [],
                "optional_fields": [],
                "missing_fields": [],
                "entities": {},
                "future_entities": {},
                "merged_entities": dict(previous),
                "checks": {"identity_confirmed": True, "implicit_personal_data": True},
                "requires_human": False,
            }
        if is_greeting_only(text) or not text:
            return {
                "schema_mode": True,
                "matched": False,
                "required_fields": [],
                "optional_fields": [],
                "missing_fields": [],
                "entities": {},
                "future_entities": {},
                "merged_entities": dict(previous),
                "checks": {"identity_confirmed": False, "greeting_only": True},
                "requires_human": False,
            }
        return {
            "schema_mode": True,
            "matched": False,
            "required_fields": [],
            "optional_fields": [],
            "missing_fields": [],
            "entities": {},
            "future_entities": {},
            "merged_entities": dict(previous),
            "checks": {"identity_confirmed": False},
            "requires_human": False,
        }

    if normalized == "BASIC_INFO":
        entities_now: dict[str, Any] = {}
        future_entities: dict[str, Any] = {}
        short_name_age = _extract_name_age_compact(text)
        if short_name_age:
            entities_now.update(short_name_age)
        name = _extract_name(text)
        if name and "name" not in entities_now:
            entities_now["name"] = name
        age = _extract_age(text)
        if age is not None and "age" not in entities_now:
            entities_now["age"] = age
        cedula_raw = _extract_cedula(text) if _has_cedula_context(text) else None
        if cedula_raw:
            entities_now["cedula_masked"] = mask_sensitive_cedula(cedula_raw)
            entities_now["cedula_detected"] = True
        city = _extract_city(text)
        if city:
            future_entities["city"] = city
        work_type = _extract_work_type(text)
        if work_type:
            future_entities["work_type"] = work_type
        route = _extract_route(text)
        if route:
            future_entities["route"] = route

        merged = dict(previous)
        merged.update(entities_now)
        required_fields = ["name", "age"]
        optional_fields = ["cedula"]
        missing_fields = [field for field in required_fields if not merged.get(field)]
        checks = {"required_complete": len(missing_fields) == 0}
        return {
            "schema_mode": True,
            "matched": len(missing_fields) == 0,
            "required_fields": required_fields,
            "optional_fields": optional_fields,
            "missing_fields": missing_fields,
            "entities": entities_now,
            "future_entities": future_entities,
            "merged_entities": merged,
            "checks": checks,
            "requires_human": bool(merged.get("cedula_detected")),
        }

    if normalized in {"LABOR_REFERENCES", "FAMILY_REFERENCES"}:
        relation_key = "work_references" if normalized == "LABOR_REFERENCES" else "family_references"
        has_phone = _extract_phone_like(text) is not None
        has_reference = any(h in text for h in REFERENCE_HINTS)
        continuation_with_phone = has_phone and bool(re.search(r"\b(su|el)\s+numero\s+es\b", text))
        refused = _is_reference_refusal(text)
        missing_phone = _is_reference_missing_phone(text)
        if refused or (has_reference and not has_phone) or missing_phone:
            return {
                "schema_mode": True,
                "matched": False,
                "required_fields": [relation_key, "phone"],
                "optional_fields": [],
                "missing_fields": ["phone"],
                "entities": {},
                "future_entities": {},
                "merged_entities": dict(previous),
                "checks": {"required_complete": False},
                "requires_human": True,
            }
        if (has_reference and has_phone) or continuation_with_phone:
            entities_now = {relation_key: text_raw}
            merged = dict(previous)
            merged.update(entities_now)
            return {
                "schema_mode": True,
                "matched": True,
                "required_fields": [relation_key, "phone"],
                "optional_fields": [],
                "missing_fields": [],
                "entities": entities_now,
                "future_entities": {},
                "merged_entities": merged,
                "checks": {"required_complete": True},
                "requires_human": False,
            }
        return {
            "schema_mode": True,
            "matched": False,
            "required_fields": [relation_key, "phone"],
            "optional_fields": [],
            "missing_fields": [relation_key],
            "entities": {},
            "future_entities": {},
            "merged_entities": dict(previous),
            "checks": {"required_complete": False},
            "requires_human": False,
        }

    if normalized == "SKILLS":
        no_experience = ("no tengo experiencia" in text) or ("sin experiencia" in text)
        has_skills = _has_skill_experience_signal(text)
        if no_experience:
            return {
                "schema_mode": True,
                "matched": False,
                "required_fields": ["skills"],
                "optional_fields": ["experience_years"],
                "missing_fields": ["skills"],
                "entities": {},
                "future_entities": {},
                "merged_entities": dict(previous),
                "checks": {"required_complete": False},
                "requires_human": True,
            }
        if has_skills:
            entities_now = {"skills": text_raw}
            merged = dict(previous)
            merged.update(entities_now)
            return {
                "schema_mode": True,
                "matched": True,
                "required_fields": ["skills"],
                "optional_fields": ["experience_years"],
                "missing_fields": [],
                "entities": entities_now,
                "future_entities": {},
                "merged_entities": merged,
                "checks": {"required_complete": True},
                "requires_human": False,
            }
        return {
            "schema_mode": True,
            "matched": False,
            "required_fields": ["skills"],
            "optional_fields": ["experience_years"],
            "missing_fields": ["skills"],
            "entities": {},
            "future_entities": {},
            "merged_entities": dict(previous),
            "checks": {"required_complete": False},
            "requires_human": False,
        }

    return {"schema_mode": False, "matched": False, "entities": {}, "future_entities": {}, "missing_fields": []}


def detect_pending_correction(step_code: str, user_text: str, protocol_entities: dict[str, Any] | None) -> dict[str, Any]:
    normalized_payload = normalize_correction_text(user_text)
    text = str(normalized_payload.get("analysis_text") or "")
    normalized_text = str(normalized_payload.get("normalized_text") or "")
    original_text = str(normalized_payload.get("original_text") or "")
    correction_cue = bool(normalized_payload.get("has_correction_cue"))
    entities = dict(protocol_entities or {})
    old_name = entities.get("name")
    old_age = entities.get("age")
    old_address = entities.get("address") or entities.get("city")
    old_work_type = entities.get("work_type")
    old_phone = entities.get("phone")
    old_route = entities.get("route") or entities.get("transport_route")

    new_age = _extract_age(text)
    if new_age is None and correction_cue:
        bare_age = re.search(r"\b([1-9][0-9])\b", text)
        if bare_age:
            maybe_age = int(bare_age.group(1))
            if 18 <= maybe_age <= 75:
                new_age = maybe_age
    if correction_cue and new_age is not None and (("edad" in text) or ("tengo" in text)):
        pass
    elif correction_cue and new_age is not None and re.fullmatch(r"[1-9][0-9]", text):
        pass
    else:
        new_age = None
    if new_age is not None:
        return {
            "has_correction": True,
            "field": "age",
            "new_value": str(new_age),
            "old_value": old_age,
            "requires_human": True,
            "suggested_step_code": "BASIC_INFO",
            "normalized_text": normalized_text,
            "original_text": original_text,
        }

    new_name = _extract_name(text)
    if correction_cue and new_name and (("me llamo" in text) or ("mi nombre es" in text)):
        if new_name != _normalize_text(old_name):
            return {
                "has_correction": True,
                "field": "name",
                "new_value": new_name,
                "old_value": old_name,
                "requires_human": True,
                "suggested_step_code": "BASIC_INFO",
                "normalized_text": normalized_text,
                "original_text": original_text,
            }

    if correction_cue and (("vivo en" in text) or ("direccion" in text)):
        city = _extract_city(text)
        if city:
            return {
                "has_correction": True,
                "field": "address",
                "new_value": city,
                "old_value": old_address,
                "requires_human": True,
                "suggested_step_code": "ADDRESS",
                "normalized_text": normalized_text,
                "original_text": original_text,
            }

    new_work_type = _extract_work_type(text)
    if new_work_type and (("mejor" in text) or text.startswith("no")):
        return {
            "has_correction": True,
            "field": "work_type",
            "new_value": new_work_type,
            "old_value": old_work_type,
            "requires_human": True,
            "suggested_step_code": "WORK_TYPE",
            "normalized_text": normalized_text,
            "original_text": original_text,
        }

    new_route = _extract_route(text)
    if new_route and (("mi ruta es" in text) or (correction_cue and "ruta " in text)):
        return {
            "has_correction": True,
            "field": "route",
            "new_value": new_route,
            "old_value": old_route,
            "requires_human": True,
            "suggested_step_code": "TRANSPORT_ROUTE",
            "normalized_text": normalized_text,
            "original_text": original_text,
        }

    if ("cambie de numero" in text) or ("ese no es mi numero" in text) or ("no es mi numero" in text):
        return {
            "has_correction": True,
            "field": "phone",
            "new_value": "",
            "old_value": old_phone,
            "requires_human": True,
            "suggested_step_code": "BASIC_INFO",
            "normalized_text": normalized_text,
            "original_text": original_text,
        }

    return {"has_correction": False}


def upsert_pending_correction(metadata: dict[str, Any], pending_payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str]:
    meta = dict(metadata or {})
    now = _utc_now_iso()
    items = list(meta.get("pending_corrections") or [])
    field = str(pending_payload.get("field") or "").strip()
    old_value = pending_payload.get("old_value")
    new_value = pending_payload.get("new_value")
    suggested_step_code = str(pending_payload.get("suggested_step_code") or "")
    source_message_id = int(pending_payload.get("source_message_id") or 0)
    normalized_text = str(pending_payload.get("normalized_text") or "")
    original_text = str(pending_payload.get("original_text") or "")

    active_idx = None
    for idx, item in enumerate(items):
        if str(item.get("field") or "") == field and str(item.get("status") or "") == "pending_human":
            active_idx = idx
            break

    if active_idx is not None:
        active = dict(items[active_idx] or {})
        if _same_value(active.get("old_value"), old_value) and _same_value(active.get("new_value"), new_value):
            active["last_seen_at"] = now
            active["updated_at"] = now
            active["duplicate_count"] = int(active.get("duplicate_count") or 1) + 1
            active["source_message_id"] = source_message_id or active.get("source_message_id")
            active["normalized_text"] = normalized_text or str(active.get("normalized_text") or "")
            active["original_text"] = original_text or str(active.get("original_text") or "")
            items[active_idx] = active
            meta["pending_corrections"] = items
            return meta, active, "duplicate_updated"

        new_id = _next_correction_id(items)
        new_item = _build_correction_item(
            correction_id=new_id,
            field=field,
            old_value=old_value,
            new_value=new_value,
            suggested_step_code=suggested_step_code,
            source_message_id=source_message_id,
            normalized_text=normalized_text,
            original_text=original_text,
            now=now,
        )
        active["status"] = "superseded"
        active["updated_at"] = now
        active["superseded_by_id"] = new_id
        items[active_idx] = active
        items.append(new_item)
        meta["pending_corrections"] = items
        return meta, new_item, "superseded_created"

    new_id = _next_correction_id(items)
    new_item = _build_correction_item(
        correction_id=new_id,
        field=field,
        old_value=old_value,
        new_value=new_value,
        suggested_step_code=suggested_step_code,
        source_message_id=source_message_id,
        normalized_text=normalized_text,
        original_text=original_text,
        now=now,
    )
    items.append(new_item)
    meta["pending_corrections"] = items
    return meta, new_item, "created"


def approve_pending_correction(metadata: dict[str, Any], correction_id: int, actor_id: int | None) -> tuple[dict[str, Any], dict[str, Any]]:
    meta = dict(metadata or {})
    now = _utc_now_iso()
    items = list(meta.get("pending_corrections") or [])
    idx = _find_correction_index(items, correction_id)
    if idx is None:
        raise ValueError("Corrección no encontrada.")
    item = dict(items[idx] or {})
    status = str(item.get("status") or "")
    if status == "approved":
        raise ValueError("La corrección ya fue aprobada.")
    if status == "rejected":
        raise ValueError("No se puede aprobar una corrección rechazada.")
    if status != "pending_human":
        raise ValueError("Solo se pueden aprobar correcciones pendientes.")

    item["status"] = "approved"
    item["approved_by"] = actor_id
    item["approved_at"] = now
    item["updated_at"] = now
    items[idx] = item
    meta["pending_corrections"] = items

    entities = dict(meta.get("protocol_entities") or {})
    entities[str(item.get("field") or "")] = item.get("new_value")
    meta["protocol_entities"] = entities
    return meta, item


def reject_pending_correction(
    metadata: dict[str, Any], correction_id: int, actor_id: int | None, rejection_reason: str | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    meta = dict(metadata or {})
    now = _utc_now_iso()
    items = list(meta.get("pending_corrections") or [])
    idx = _find_correction_index(items, correction_id)
    if idx is None:
        raise ValueError("Corrección no encontrada.")
    item = dict(items[idx] or {})
    status = str(item.get("status") or "")
    if status == "rejected":
        raise ValueError("La corrección ya fue rechazada.")
    if status == "approved":
        raise ValueError("No se puede rechazar una corrección aprobada.")
    if status != "pending_human":
        raise ValueError("Solo se pueden rechazar correcciones pendientes.")

    item["status"] = "rejected"
    item["rejected_by"] = actor_id
    item["rejected_at"] = now
    item["rejection_reason"] = str(rejection_reason or "").strip() or None
    item["updated_at"] = now
    items[idx] = item
    meta["pending_corrections"] = items
    return meta, item


def normalize_correction_text(user_text: str) -> dict[str, Any]:
    original_text = str(user_text or "").strip()
    normalized_text = _normalize_text(original_text)
    text = normalized_text
    cue_patterns = [
        r"^no\b",
        r"^nop\b",
        r"^perdon\b",
        r"^corrijo\b",
        r"^quise\s+decir\b",
        r"^digo\b",
        r"^realmente\b",
        r"^me\s+equivoque\b",
        r"^mejor\b",
    ]
    has_cue = any(re.search(pattern, text) for pattern in cue_patterns)
    # Limpiamos prefijos conversacionales al inicio para analizar el contenido real.
    prefix_re = re.compile(
        r"^(?:no|nop|perdon|corrijo|quise\s+decir|digo|realmente|me\s+equivoque)\b(?:[\s,:-]+)?",
    )
    analysis_text = text
    while True:
        new_text = re.sub(prefix_re, "", analysis_text, count=1).strip()
        if new_text == analysis_text:
            break
        analysis_text = new_text
    return {
        "original_text": original_text,
        "normalized_text": normalized_text,
        "analysis_text": analysis_text,
        "has_correction_cue": has_cue,
    }


def _utc_now_iso() -> str:
    return datetime.utcnow().isoformat()


def _same_value(a: Any, b: Any) -> bool:
    return ("" if a is None else str(a)) == ("" if b is None else str(b))


def _next_correction_id(items: list[dict[str, Any]]) -> int:
    max_id = 0
    for item in items:
        try:
            max_id = max(max_id, int(item.get("id") or 0))
        except Exception:
            continue
    return max_id + 1


def _find_correction_index(items: list[dict[str, Any]], correction_id: int) -> int | None:
    for idx, item in enumerate(items):
        try:
            if int(item.get("id") or 0) == int(correction_id):
                return idx
        except Exception:
            continue
    return None


def _build_correction_item(
    *,
    correction_id: int,
    field: str,
    old_value: Any,
    new_value: Any,
    suggested_step_code: str,
    source_message_id: int,
    normalized_text: str,
    original_text: str,
    now: str,
) -> dict[str, Any]:
    return {
        "id": int(correction_id),
        "field": field,
        "old_value": old_value,
        "new_value": new_value,
        "status": "pending_human",
        "requires_human": True,
        "created_at": now,
        "updated_at": now,
        "last_seen_at": now,
        "duplicate_count": 1,
        "source_message_id": int(source_message_id or 0),
        "approved_by": None,
        "approved_at": None,
        "rejected_by": None,
        "rejected_at": None,
        "rejection_reason": None,
        "superseded_by_id": None,
        "suggested_step_code": suggested_step_code,
        "normalized_text": normalized_text,
        "original_text": original_text,
    }


def mask_sensitive_cedula(raw_value: str) -> str:
    digits = re.sub(r"\D", "", str(raw_value or ""))
    if len(digits) < 6:
        return "***"
    return f"{digits[:3]}-2***-***" if len(digits) >= 11 else f"{digits[:3]}***"


def _extract_cedula(text: str) -> str | None:
    m = re.search(r"\b(\d{3})[-\s]?(\d{7})[-\s]?(\d)\b", text)
    if not m:
        return None
    return "".join(m.groups())


def _extract_age(text: str) -> int | None:
    patterns = [
        r"\btengo\s+([1-9][0-9])\s*(?:anos|año|años)?\b",
        r"\b([1-9][0-9])\s*(?:anos|año|años)\b",
        r"\bedad\s*(?:es|:)?\s*([1-9][0-9])\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            try:
                value = int(m.group(1))
                if 18 <= value <= 75:
                    return value
            except Exception:
                continue
    age_words = parse_spanish_age_words(text)
    if age_words is not None:
        return age_words
    return None


def _extract_name(text: str) -> str | None:
    patterns = [
        r"\bme\s+(?:llamo|yamo)\s+([a-zñ]+(?:\s+[a-zñ]+){0,3})(?=\s+tengo\b|\s+edad\b|$)",
        r"\bme llamo\s+([a-zñ]+(?:\s+[a-zñ]+){0,3})",
        r"\bmi nombre es\s+([a-zñ]+(?:\s+[a-zñ]+){0,3})",
        r"\bsoy\s+([a-zñ]+(?:\s+[a-zñ]+){0,3})",
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return _clean_name(m.group(1))
    tokens = [tok for tok in re.findall(r"[a-zñ]+", text) if tok not in STOPWORDS_NAME and tok not in SUPPORTED_CITIES]
    # Evita tomar modalidad/rutas/transportes como nombre en respuestas cortas.
    tokens = [tok for tok in tokens if tok not in WORK_TYPE_HINTS and tok not in TRANSPORT_HINTS and tok != "ruta"]
    tokens = [tok for tok in tokens if tok not in GREETING_TOKENS]
    if len(tokens) >= 2:
        return _clean_name(" ".join(tokens[:3]))
    return None


def _clean_name(name: str) -> str:
    parts = [p for p in re.findall(r"[a-zñ]+", name or "") if p and p not in STOPWORDS_NAME]
    return " ".join(parts[:3]).strip()


def _normalize_text(value: str) -> str:
    txt = (value or "").strip().lower()
    txt = txt.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
    return re.sub(r"\s+", " ", txt)


def _is_yes_no(text: str) -> bool:
    return _yes_no_value(text) is not None


def _yes_no_value(text: str) -> bool | None:
    if not text:
        return None
    if re.search(r"\b(si|sí)\b", text) and re.search(r"\b(no)\b", text):
        return None
    normalized_for_typos = (
        text.replace("kiero", "quiero")
        .replace("sii", "si")
        .replace("hazme", "hasme")
    )
    tokens = set(re.findall(r"[a-z0-9]+", normalized_for_typos))
    has_yes = any(w in tokens for w in YES_WORDS)
    has_no = any(w in tokens for w in NO_WORDS)
    if (not has_no) and any(h in normalized_for_typos for h in POSITIVE_CONTINUE_HINTS):
        has_yes = True
    if has_yes and not has_no:
        return True
    if has_no and not has_yes:
        return False
    return None


def is_greeting_only(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return True
    if normalized in GREETING_ONLY_PATTERNS:
        return True
    if re.sub(r"[\s\W_]+", "", str(text or ""), flags=re.UNICODE) == "":
        return True
    tokens = re.findall(r"[a-z0-9?]+", normalized)
    if not tokens:
        return True
    if len(tokens) == 1 and tokens[0] in GREETING_ONLY_PATTERNS:
        return True
    # Permite tratar combinaciones repetidas de saludo/ruido como saludo puro.
    return all(tok in GREETING_TOKENS or tok == "?" for tok in tokens)


def is_positive_confirmation(text: str, step_code: str = "") -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    if str(step_code or "").strip().upper() == "PERSONAL_CONFIRMATION":
        normalized_for_typos = normalized.replace("kiero", "quiero").replace("hazme", "hasme")
        if normalized_for_typos in {"si", "sii"}:
            return True
        return any(_contains_phrase(normalized_for_typos, h) for h in PERSONAL_CONFIRMATION_POSITIVE if h not in {"si", "sí", "sii"})
    return _yes_no_value(normalized) is True


def is_negative_confirmation(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    if normalized in {"no", "nop", "negativo"}:
        return True
    return any(_contains_phrase(normalized, h) for h in PERSONAL_CONFIRMATION_NEGATIVE if h != "no")


def has_personal_data_signal(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized or is_greeting_only(normalized):
        return False
    if is_welcome_fastpath_denied(normalized):
        return False
    if "?" in str(text or ""):
        return False
    if any(normalized.startswith(prefix + " ") or normalized == prefix for prefix in CONFUSION_PREFIXES):
        return False
    if any(_contains_phrase(normalized, p) for p in CONFUSION_PATTERNS):
        return False
    if any(
        hint in normalized
        for hint in ("me llamo", "mi nombre", "tengo", "anos", "año", "años", "edad", "cedula", "cédula", "telefono", "teléfono")
    ):
        return True
    if _extract_age(normalized) is not None:
        return True
    if _has_cedula_context(normalized) and _extract_cedula(normalized):
        return True
    if _extract_phone_like(normalized):
        return True
    if _looks_like_name_phrase(normalized):
        return True
    return False


def is_welcome_fastpath_denied(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return True
    return any(_contains_phrase(normalized, p) for p in WELCOME_FASTPATH_DENY_PATTERNS)


def _looks_like_name_phrase(text: str) -> bool:
    tokens = [tok for tok in re.findall(r"[a-zñ]+", _normalize_text(text)) if tok]
    tokens = [tok for tok in tokens if tok not in STOPWORDS_NAME and tok not in GREETING_TOKENS]
    if len(tokens) < 2:
        return False
    if any(tok in NON_NAME_TOKENS for tok in tokens):
        return False
    if any(tok in WORK_TYPE_HINTS or tok in TRANSPORT_HINTS for tok in tokens):
        return False
    if any(tok in SUPPORTED_CITIES or tok in COMMON_SECTORS for tok in tokens):
        return False
    return True


def _contains_phrase(text: str, phrase: str) -> bool:
    p = _normalize_text(phrase)
    if not p:
        return False
    if " " in p:
        return p in text
    return bool(re.search(rf"\b{re.escape(p)}\b", text))


def _run_validation(rule: str, text: str) -> bool:
    if rule == "non_empty":
        return bool(text)
    if rule == "yes_no":
        return _is_yes_no(text)
    if rule == "contains_name":
        return len([x for x in text.split() if x.isalpha()]) >= 2
    if rule == "contains_age":
        return bool(re.search(r"\b([1-9][0-9])\b", text))
    if rule == "cedula_like":
        return bool(re.search(r"\b\d{3}[-\s]?\d{7}[-\s]?\d\b", text))
    if rule == "city_supported":
        if not _has_address_context(text):
            return False
        if any(city in text for city in SUPPORTED_CITIES):
            return True
        return any(sector in text for sector in COMMON_SECTORS)
    if rule == "work_modality":
        return _has_work_type(text)
    if rule == "transport_route":
        return _has_transport_route(text) and (not _has_work_type(text))
    if rule == "percentage_acceptance":
        yn = _yes_no_value(text)
        if yn is None and re.search(r"\b(ta bien|okey|okay|aja)\b", text):
            yn = True
        if yn is None and re.search(r"\b(acepto|aceptar|aceptado)\b", text):
            yn = True
        if re.search(r"\b(no acepto|no voy a aceptar|no quiero aceptar|no el 25|no 25|no al 25)\b", text):
            yn = False
        has_25 = bool(
            re.search(r"\b25\b", text)
            or "25%" in text
            or re.search(r"\b25\s*por\s*ciento\b", text)
            or re.search(r"\bveinti\s*cinco\b", text)
            or re.search(r"\bveinticinco\b", text)
        )
        # Aceptamos confirmaciones cortas solo en la etapa de porcentaje.
        if yn is True and has_25:
            return True
        if yn is True and re.search(r"\b(ta bien|okey|okay|aja|ok)\b", text):
            return True
        return False
    if rule == "references_not_empty":
        return len(text) >= 12 and ("-" in text or "," in text)
    if rule == "phone_like":
        return bool(re.search(r"\b\d{7,11}\b", re.sub(r"[^0-9 ]", " ", text)))
    if rule == "mentions_cedula":
        return "cedula" in text
    if rule == "mentions_photo":
        return "foto" in text and "perfil" in text
    return bool(text)


def _has_work_type(text: str) -> bool:
    if "salida diaria" in text:
        return True
    return any(h in text for h in WORK_TYPE_HINTS)


def _has_transport_route(text: str) -> bool:
    negative_only = text in {"no", "si", "sí", "no se", "nose", "no sé"}
    if negative_only:
        return False
    if _has_work_type(text):
        return False
    if any(h in text for h in TRANSPORT_HINTS):
        return True
    if re.search(r"\bruta\s+[a-z0-9]+\b", text):
        return True
    return bool(re.search(r"\b(voy|llego|transporte)\b", text))


def _extract_city(text: str) -> str | None:
    for city in SUPPORTED_CITIES:
        if city in text:
            return city.title()
    for sector in COMMON_SECTORS:
        if sector in text:
            return "Santiago"
    return None


def _extract_phone_like(text: str) -> str | None:
    digits = re.sub(r"\D", "", text or "")
    if len(digits) < 10:
        return None
    for prefix in ("809", "829", "849"):
        idx = digits.find(prefix)
        if idx >= 0 and len(digits[idx:]) >= 10:
            return digits[idx : idx + 10]
    return None


def _has_cedula_context(text: str) -> bool:
    return any(h in text for h in CEDULA_CONTEXT_HINTS)


def _has_skill_experience_signal(text: str) -> bool:
    if not any(h in text for h in SKILL_HINTS):
        return False
    return any(h in text for h in SKILL_EXPERIENCE_HINTS)


def _has_address_context(text: str) -> bool:
    if any(x in text for x in ("mi hermana vive", "mi mama vive", "mi madre vive", "mi hijo vive")):
        return False
    if any(city in text for city in SUPPORTED_CITIES):
        return True
    if any(x in text for x in ("vivo", "bibo", "soy de", "estoy en", "resido", "direccion", "dirección")):
        return True
    txt = text.strip()
    if txt in SUPPORTED_CITIES:
        return True
    if txt in COMMON_SECTORS:
        return True
    return False


def _is_reference_missing_phone(text: str) -> bool:
    patterns = (
        "no tengo numero",
        "no tengo telefono",
        "no se el numero",
        "no se su numero",
        "sin numero",
    )
    return any(p in text for p in patterns)


def _is_reference_refusal(text: str) -> bool:
    patterns = (
        "no quiero dar referencia",
        "no quiero dar referencias",
        "no tengo referencia",
        "no tengo referencias",
        "prefiero no dar referencia",
    )
    return any(p in text for p in patterns)


def parse_spanish_age_words(text: str) -> int | None:
    units = {
        "uno": 1,
        "dos": 2,
        "tres": 3,
        "cuatro": 4,
        "cinco": 5,
        "seis": 6,
        "siete": 7,
        "ocho": 8,
        "nueve": 9,
    }
    specials = {
        "dieciocho": 18,
        "diecinueve": 19,
        "veinte": 20,
        "veintiuno": 21,
        "veintidos": 22,
        "veintitres": 23,
        "veinticuatro": 24,
        "veinticinco": 25,
        "veintiseis": 26,
        "veintisiete": 27,
        "veintiocho": 28,
        "veintinueve": 29,
        "treinta": 30,
        "cuarenta": 40,
        "cincuenta": 50,
        "sesenta": 60,
        "setenta": 70,
    }
    txt = _normalize_text(text)
    txt = txt.replace("treintai ", "treinta y ")
    txt = txt.replace("cuarentai ", "cuarenta y ")
    m = re.search(r"\b(?:tengo|edad\s*(?:es|:)?\s*)?((?:dieciocho|diecinueve|veinte|veinti[a-z]+|treinta(?:\s+y\s+[a-z]+)?|cuarenta(?:\s+y\s+[a-z]+)?|cincuenta|sesenta|setenta))(?:\s+anos|\s+ano|\s+años)?\b", txt)
    if not m:
        return None
    phrase = m.group(1).strip()
    if phrase in specials:
        value = specials[phrase]
        return value if 18 <= value <= 70 else None
    m2 = re.fullmatch(r"(treinta|cuarenta)\s+y\s+([a-z]+)", phrase)
    if m2:
        tens = 30 if m2.group(1) == "treinta" else 40
        unit = units.get(m2.group(2))
        if unit is None:
            return None
        value = tens + unit
        return value if 18 <= value <= 70 else None
    return None


def _extract_name_age_compact(text: str) -> dict[str, Any]:
    # Casos: "yulisa 28", "juana perez 32", "yulisa 28 santiago salida diaria"
    m = re.match(r"\s*([a-zñ]+(?:\s+[a-zñ]+){0,2})\s+([1-9][0-9])(?:\s+|$)", text)
    if not m:
        return {}
    raw_name = _clean_name(m.group(1))
    try:
        age = int(m.group(2))
    except Exception:
        return {}
    if not raw_name or not (18 <= age <= 70):
        return {}
    bad_tokens = set(raw_name.split()) & (set(SUPPORTED_CITIES) | set(COMMON_SECTORS) | set(WORK_TYPE_HINTS) | {"ruta"})
    if bad_tokens:
        return {}
    return {"name": raw_name, "age": age}


def _extract_work_type(text: str) -> str | None:
    if "dormida" in text or "domida" in text or "dormia" in text or "interna" in text:
        return "dormida"
    if "salida diaria" in text or "salida" in text or "diaria" in text or "externa" in text:
        return "salida diaria"
    return None


def _extract_route(text: str) -> str | None:
    m2 = re.search(r"\bmi\s+ruta\s+es\s+(?:la\s+)?([a-z0-9]+)\b", text)
    if m2:
        return f"ruta {m2.group(1).upper()}"
    m = re.search(r"\bruta\s+([a-z0-9]+)\b", text)
    if m:
        return f"ruta {m.group(1).upper()}"
    if "parada" in text:
        return "parada"
    return None
