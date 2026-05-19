from __future__ import annotations

import json
import os
import re
from typing import Any

import requests
from models import BotConversation
from services.bot_candidate_draft_service import get_or_create_interview_flow_draft
from services.bot_observability_service import log_bot_event
from utils.timezone import utc_now_naive

FLOW_KEY = "interview_flow"

STEP_ASK_NAME = "ask_name"
STEP_ASK_AGE = "ask_age"
STEP_ASK_CITY_SECTOR = "ask_city_sector"
STEP_ASK_EXPERIENCE = "ask_experience"
STEP_ASK_SKILLS = "ask_skills"
STEP_ASK_AVAILABILITY = "ask_availability"
STEP_ASK_REFERENCES = "ask_references"
STEP_COMPLETED = "completed"

GREETING = (
    "Hola, gracias por escribir a Agencia Doméstica del Cibao A&D. "
    "Te haré unas preguntas cortas para registrarte."
)
AI_CLASSIFIER_MIN_CONFIDENCE = 0.75
AI_COPY_MAX_CHARS = 280


def _is_true(value: str | None, *, default: bool = True) -> bool:
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def is_interview_flow_enabled() -> bool:
    return _is_true(os.getenv("BOT_INTERVIEW_FLOW_ENABLED"), default=True)


def is_interview_ai_classifier_enabled() -> bool:
    return _is_true(os.getenv("BOT_INTERVIEW_AI_CLASSIFIER_ENABLED"), default=False)


def is_interview_ai_copy_enabled() -> bool:
    return _is_true(os.getenv("BOT_INTERVIEW_AI_COPY_ENABLED"), default=False)


def _base_state() -> dict[str, Any]:
    return {
        "current_step": STEP_ASK_NAME,
        "collected_data": {},
        "detected_future_data": {},
        "data_sources": {},
        "completed": False,
        "last_question": _question_for_step(STEP_ASK_NAME, include_greeting=True),
        "summary": "",
        "greeting_sent": False,
        "last_updated_at": str(utc_now_naive()),
    }


def _normalize_text(text: str | None) -> str:
    clean = str(text or "").strip()
    clean = re.sub(r"\s+", " ", clean)
    return clean


def _normalize_token_text(text: str | None) -> str:
    low = _normalize_text(text).lower()
    low = (
        low.replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
    )
    return low


def _question_for_step(step: str, *, include_greeting: bool = False) -> str:
    question = {
        STEP_ASK_NAME: "¿Cuál es tu nombre completo?",
        STEP_ASK_AGE: "¿Qué edad tienes?",
        STEP_ASK_CITY_SECTOR: "¿En qué ciudad y sector vives?",
        STEP_ASK_EXPERIENCE: "Cuéntame tu experiencia laboral.",
        STEP_ASK_SKILLS: (
            "¿Qué sabes hacer? Puedes incluir: limpieza, cocinar, lavar, "
            "cuidar niños, cuidar envejecientes y otras funciones."
        ),
        STEP_ASK_AVAILABILITY: "¿Cuál es tu disponibilidad: con dormida, salida diaria o ambos?",
        STEP_ASK_REFERENCES: "¿Tienes referencias laborales o familiares? Compárteme nombres y teléfonos.",
        STEP_COMPLETED: "Gracias. Ya tenemos tus datos para revisión humana.",
    }.get(step, "")
    if include_greeting:
        return f"{GREETING}\n\n{question}".strip()
    return question


def _help_variants_for_step(step: str, *, include_example: bool = False) -> list[str]:
    variants = {
        STEP_ASK_NAME: [
            "Para continuar, necesito tu nombre completo.",
            "Solo escríbeme tu nombre y apellido.",
            "Compárteme tu nombre completo para seguir.",
        ],
        STEP_ASK_AGE: [
            "Necesito confirmar tu edad para continuar 😊",
            "Solo dime cuántos años tienes.",
            "Puedes responder solo con tu edad." + (" Ejemplo: 32." if include_example else ""),
        ],
        STEP_ASK_CITY_SECTOR: [
            "Ahora necesito saber dónde vives para buscar empleos cercanos 😊 Dime tu ciudad y el sector.",
            "Dime tu ciudad y el sector donde vives.",
            "Solo escríbeme la zona donde resides." + (" Ejemplo: Santiago, Gurabo." if include_example else ""),
        ],
        STEP_ASK_EXPERIENCE: [
            "Cuéntame un poco de tu experiencia laboral 😊",
            "Quiero saber en qué tipo de trabajo has estado.",
            "Puedes decirme si has trabajado en casas o en cuidado.",
        ],
        STEP_ASK_SKILLS: [
            "Necesito saber qué funciones manejas y qué sabes hacer 😊",
            "Quiero saber en qué puedes trabajar.",
            "Puedes decirme las funciones que manejas.",
        ],
        STEP_ASK_AVAILABILITY: [
            "Necesito confirmar tu disponibilidad para continuar 😊",
            "Dime si prefieres con dormida, salida diaria o ambos.",
            "Puedes responder con una opción de disponibilidad." + (" Ejemplo: salida diaria." if include_example else ""),
        ],
        STEP_ASK_REFERENCES: [
            "Ya casi terminamos 😊 para cerrar el registro necesito tus referencias. Puede ser referencia laboral o familiar.",
            "Si no las tienes ahora, también puedes decirme que las enviarás luego.",
            "Compárteme una referencia o dime que la enviarás después.",
        ],
    }
    return list(variants.get(step, [_question_for_step(step)]))


def build_step_help_message(step: str, reason: str, repeat_count: int = 0) -> str:
    reason_low = _normalize_token_text(reason)
    repeated_confusion = "confusion" in reason_low or "offtopic" in reason_low
    include_example = repeat_count >= 2 or (repeated_confusion and repeat_count >= 1)
    variants = _help_variants_for_step(step, include_example=include_example)
    idx = max(0, min(repeat_count, len(variants) - 1))
    return str(variants[idx]).strip()


def _extract_age(text: str) -> int | None:
    match = re.search(r"\b(1[89]|[2-5][0-9]|6[0-9]|70)\b", text)
    if not match:
        return None
    return int(match.group(1))


def _extract_full_name(text: str) -> str:
    normalized = _normalize_token_text(text)
    normalized = re.sub(r"^(soy|me llamo|mi nombre es)\s+", "", normalized).strip()
    normalized = re.sub(r"\b(tengo|edad)\b.*$", "", normalized).strip()
    normalized = re.sub(r"\d+", " ", normalized)
    normalized = re.sub(r"[^a-zñ\s]+", " ", normalized)
    tokens = [t for t in normalized.split() if t and t not in {"me", "llamo", "nombre", "es"}]
    if len(tokens) > 4:
        tokens = tokens[:4]
    return " ".join(tokens).strip()


def _extract_skills(text: str) -> dict[str, Any]:
    low = _normalize_token_text(text)
    found = []
    mapping = {
        "limpieza": ["limpieza", "limpiar", "limpio"],
        "cocinar": ["cocinar", "cocina", "cosino", "cocino"],
        "lavar": ["lavar", "lavado", "labo", "lavo"],
        "planchar": ["planchar", "plancha"],
        "cuidar niños": ["niños", "ninos", "niño", "nino"],
        "cuidar envejecientes": ["envejec", "ancian", "adulto mayor"],
        "doméstica": ["domestica", "domestico"],
        "cuidado": ["cuidado", "cuidar"],
    }
    for label, hints in mapping.items():
        if any(h in low for h in hints):
            found.append(label)
    other = ""
    if "otra" in low or "tambien" in low or "también" in low:
        other = text
    return {"skills": found, "other": other}


def _extract_availability(text: str) -> str:
    low = _normalize_token_text(text)
    has_dormida = "dormida" in low or "dormir" in low or "domida" in low
    has_salida = "salida" in low or "diaria" in low
    has_por_dia = ("por dia" in low) or ("por-dia" in low) or ("diario" in low)
    has_fines = ("fines de semana" in low) or ("fin de semana" in low) or ("fines semana" in low)
    if "amb" in low or (has_dormida and has_salida):
        return "ambos"
    if has_dormida:
        return "con dormida"
    if has_salida:
        return "salida diaria"
    if has_por_dia:
        return "por día"
    if has_fines:
        return "fines de semana"
    return ""


def _extract_city_sector_quick(text: str) -> str:
    low = _normalize_token_text(text)
    city_hints = ("vivo en", "soy de", "de ")
    if "," in text and len(text.split(",")) >= 2:
        return _normalize_text(text)
    for hint in city_hints:
        if hint in low:
            idx = low.find(hint)
            frag = _normalize_text(text[idx + len(hint) :])
            if len(frag) >= 5:
                return frag
    if len(text.split()) >= 2 and any(x in low for x in {"gurabo", "santiago", "puerto plata", "la vega", "moca"}):
        return _normalize_text(text)
    return ""


def _extract_future_data(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    age = _extract_age(text)
    if age is not None:
        out["age"] = age
    city = _extract_city_sector_quick(text)
    if city:
        out["city_sector"] = city
    exp_low = _normalize_token_text(text)
    if any(x in exp_low for x in {"experiencia", "trabaj", "casas de familia", "cuidando"}):
        if len(_normalize_text(text)) >= 8:
            out["experience"] = _normalize_text(text)
    parsed = _extract_skills(text)
    if parsed.get("skills"):
        out["skills"] = parsed.get("skills") or []
        if parsed.get("other"):
            out["skills_other"] = parsed.get("other") or ""
    avail = _extract_availability(text)
    if avail:
        out["availability"] = avail
    if _looks_like_reference(text):
        out["references"] = _normalize_text(text)
    return out


def _step_field(step: str) -> str | None:
    return {
        STEP_ASK_NAME: "full_name",
        STEP_ASK_AGE: "age",
        STEP_ASK_CITY_SECTOR: "city_sector",
        STEP_ASK_EXPERIENCE: "experience",
        STEP_ASK_SKILLS: "skills",
        STEP_ASK_AVAILABILITY: "availability",
        STEP_ASK_REFERENCES: "references",
    }.get(step)


def _is_affirmative(text: str) -> bool:
    return _normalize_token_text(text) in {"si", "sí", "correcto", "exacto", "asi es", "así es", "yes"}


def _is_negative(text: str) -> bool:
    return _normalize_token_text(text) in {"no", "incorrecto", "negativo"}


def _confidence_for_value(field: str, value: Any) -> str:
    if field == "age":
        return "high" if isinstance(value, int) else "low"
    if field == "city_sector":
        return "medium" if isinstance(value, str) and len(value) >= 6 else "low"
    if field in {"skills", "availability", "references", "experience"}:
        return "medium"
    return "low"


def _register_field(
    *,
    state: dict[str, Any],
    field: str,
    value: Any,
    source: str,
    confirmed: bool,
    overwrite_confirmed: bool = False,
) -> None:
    now = str(utc_now_naive())
    collected = dict(state.get("collected_data") or {})
    sources = dict(state.get("data_sources") or {})
    detected = dict(state.get("detected_future_data") or {})
    existing_source = dict(sources.get(field) or {})
    if bool(existing_source.get("confirmed")) and not overwrite_confirmed:
        return
    if confirmed:
        collected[field] = value
        detected.pop(field, None)
    else:
        if field not in collected:
            detected[field] = {"value": value, "confidence": _confidence_for_value(field, value), "source": source}
    sources[field] = {
        "source": source,
        "confirmed": bool(confirmed),
        "confidence": _confidence_for_value(field, value),
        "updated_at": now,
    }
    state["collected_data"] = collected
    state["detected_future_data"] = detected
    state["data_sources"] = sources
    state["last_updated_at"] = now


def _confirm_prompt_for_field(field: str, value: Any) -> str:
    if field == "age":
        return f"Tengo que tienes {value} años, ¿es correcto?"
    if field == "city_sector":
        return f"Tengo que vives en {value}, ¿es correcto?"
    if field == "availability":
        return f"Tengo que tu disponibilidad es {value}, ¿es correcto?"
    return "Tengo este dato adelantado, ¿es correcto?"


_CONFIRMABLE_FUTURE_FIELDS = {"age", "city_sector"}


def _is_invalid_exact(text: str, invalid_values: set[str]) -> bool:
    low = _normalize_token_text(text)
    return low in invalid_values


def is_offtopic_smalltalk(text: str) -> bool:
    low = _normalize_token_text(text)
    low_compact = re.sub(r"[^\w\s]+", "", low).strip()
    if not low:
        return True
    if re.fullmatch(r"[^\w]+", low):
        return True
    short_generic = {
        "ok",
        "hola",
        "hello",
        "buenas",
        "buen dia",
        "buenas tardes",
        "buenas noches",
        "como estas",
        "como te va",
        "que tal",
        "todo bien",
        "todo bn",
        "si",
        "no",
        "no se",
        "tal vez",
        "quizas",
        "necesito esto",
        "ajaj",
        "jaja",
        "jeje",
        "lol",
        "xd",
        "?",
    }
    if low in short_generic or low_compact in short_generic:
        return True
    if len(low) <= 3 and re.search(r"[a-z]", low):
        return True
    return False


def _is_confusion_or_offtopic(text: str) -> bool:
    low = _normalize_token_text(text)
    compact = re.sub(r"[^\w\s]+", "", low).strip()
    confusion_tokens = {
        "que",
        "que?",
        "q",
        "k",
        "no entiendo",
        "como asi",
        "explicame",
        "que no entiendo",
        "hola",
        "ok",
        "si",
        "aja",
    }
    if low in confusion_tokens or compact in confusion_tokens:
        return True
    if re.fullmatch(r"[^\w]+", low):
        return True
    if compact.isdigit():
        return False
    if len(compact) <= 2 and compact and not re.search(r"\d", compact):
        return True
    return is_offtopic_smalltalk(text)


def _redirect_for_step(step: str) -> str:
    return {
        STEP_ASK_AGE: "Entiendo 👍 pero necesito confirmar tu edad para continuar el registro. ¿Qué edad tienes en números?",
        STEP_ASK_CITY_SECTOR: "Entiendo 👍 para continuar necesito tu ubicación. ¿En qué ciudad y sector vives?",
        STEP_ASK_EXPERIENCE: "Entiendo 👍 pero para seguir necesito tu experiencia laboral. Cuéntame si has trabajado en casas o en cuidado.",
        STEP_ASK_SKILLS: "Entiendo 👍 para continuar necesito saber qué funciones manejas. ¿Sabes limpiar, cocinar, lavar, planchar o cuidar?",
        STEP_ASK_AVAILABILITY: "Entiendo 👍 pero necesito confirmar tu disponibilidad para continuar el registro. ¿Prefieres con dormida, salida diaria o ambos?",
        STEP_ASK_REFERENCES: "Entiendo 👍 para cerrar el registro necesito tus referencias. Compárteme nombre y teléfono, o dime explícitamente que las enviarás luego.",
    }.get(step, _question_for_step(step))


def _openai_chat_json(*, system_prompt: str, user_payload: dict[str, Any]) -> dict[str, Any]:
    api_key = str(os.getenv("BOT_AI_API_KEY") or "").strip()
    model = str(os.getenv("BOT_AI_MODEL") or "gpt-4.1-mini").strip()
    timeout_seconds = int(str(os.getenv("BOT_AI_TIMEOUT_SECONDS") or "8").strip() or "8")
    max_tokens = int(str(os.getenv("BOT_AI_MAX_TOKENS") or "180").strip() or "180")
    if not api_key:
        raise ValueError("missing_api_key")
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "temperature": 0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
        },
        timeout=timeout_seconds,
    )
    resp.raise_for_status()
    body = resp.json() if resp.content else {}
    choices = body.get("choices") if isinstance(body, dict) else None
    if not isinstance(choices, list) or not choices:
        raise ValueError("empty_choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else {}
    content = (message or {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("empty_content")
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("invalid_json_payload")
    return {"data": data, "raw": content.strip()}


def _classifier_system_prompt() -> str:
    return (
        "Eres un clasificador de validacion por paso de un flujo rigidamente controlado. "
        "Responde SIEMPRE JSON con llaves exactas: is_valid_for_step, normalized_value, confidence, reason. "
        "No inventes datos. No extraigas ni propongas datos de pasos futuros. "
        "Si no estas seguro devuelve is_valid_for_step=false y confidence baja."
    )


def _copy_system_prompt() -> str:
    return (
        "Eres redactor seguro para un bot de entrevista paso a paso. "
        "Responde SOLO JSON con llave exacta: text. "
        "No cambies objetivo del paso. No agregues promesas, precios, empleos ni datos no provistos. "
        "Maximo 280 caracteres."
    )


def _try_ai_classifier(*, step: str, inbound_text: str, fallback_reason: str, conversation: BotConversation) -> dict[str, Any]:
    conv_id = int(getattr(conversation, "id", 0) or 0)
    if not is_interview_ai_classifier_enabled():
        log_bot_event("interview_ai_classifier_skipped", metadata={"conversation_id": conv_id, "step": step, "reason": "flag_disabled"})
        return {"used": False}
    if not str(os.getenv("BOT_AI_API_KEY") or "").strip():
        log_bot_event("interview_ai_classifier_skipped", metadata={"conversation_id": conv_id, "step": step, "reason": "missing_api_key"})
        return {"used": False}
    payload = {
        "step": step,
        "user_answer": inbound_text,
        "fallback_validation_reason": fallback_reason,
    }
    log_bot_event("interview_ai_classifier_attempt", metadata={"conversation_id": conv_id, "step": step})
    try:
        raw = _openai_chat_json(system_prompt=_classifier_system_prompt(), user_payload=payload)
        data = dict(raw.get("data") or {})
        valid = bool(data.get("is_valid_for_step", False))
        normalized_value = _normalize_text(str(data.get("normalized_value") or ""))
        confidence = float(data.get("confidence") or 0.0)
        reason = _normalize_text(str(data.get("reason") or ""))[:240]
        result = {
            "used": True,
            "is_valid_for_step": valid,
            "normalized_value": normalized_value,
            "confidence": confidence,
            "reason": reason,
            "raw": str(raw.get("raw") or "")[:2000],
        }
        log_bot_event(
            "interview_ai_classifier_result",
            metadata={"conversation_id": conv_id, "step": step, "is_valid_for_step": valid, "confidence": confidence},
        )
        return result
    except Exception as exc:
        log_bot_event(
            "interview_ai_classifier_result",
            level="warning",
            metadata={"conversation_id": conv_id, "step": step, "error": str(exc)[:180]},
        )
        return {"used": False}


def _safe_ai_copy(*, base_text: str, step: str, mode: str, conversation: BotConversation) -> str:
    conv_id = int(getattr(conversation, "id", 0) or 0)
    if not is_interview_ai_copy_enabled():
        return base_text
    if not str(os.getenv("BOT_AI_API_KEY") or "").strip():
        log_bot_event("interview_ai_copy_fallback", metadata={"conversation_id": conv_id, "step": step, "mode": mode, "reason": "missing_api_key"})
        return base_text
    log_bot_event("interview_ai_copy_attempt", metadata={"conversation_id": conv_id, "step": step, "mode": mode})
    try:
        raw = _openai_chat_json(
            system_prompt=_copy_system_prompt(),
            user_payload={"step": step, "mode": mode, "base_text": base_text},
        )
        data = dict(raw.get("data") or {})
        text = _normalize_text(str(data.get("text") or ""))
        if not text or len(text) > AI_COPY_MAX_CHARS:
            log_bot_event(
                "interview_ai_copy_fallback",
                metadata={"conversation_id": conv_id, "step": step, "mode": mode, "reason": "invalid_or_too_long"},
            )
            return base_text
        log_bot_event(
            "interview_ai_copy_result",
            metadata={"conversation_id": conv_id, "step": step, "mode": mode, "chars": len(text)},
        )
        return text
    except Exception as exc:
        log_bot_event(
            "interview_ai_copy_fallback",
            metadata={"conversation_id": conv_id, "step": step, "mode": mode, "reason": str(exc)[:120]},
        )
        return base_text


def _looks_like_reference(text: str) -> bool:
    low = _normalize_token_text(text)
    if is_offtopic_smalltalk(text):
        return False
    explicit_later = [
        "te las envio luego",
        "te la envio luego",
        "puedo conseguir referencias",
        "no las tengo ahora pero puedo enviarlas",
        "puedo enviarla luego",
        "las envio despues",
        "las mando despues",
        "puedo enviarlas luego",
    ]
    if any(p in low for p in explicit_later):
        return True
    has_phone = bool(re.search(r"\b\d{7,}\b", low))
    has_relation = any(x in low for x in {"referencia", "familiar", "laboral", "jefa", "supervisor", "vecina", "tio", "tia", "prima", "primo"})
    words = [w for w in re.split(r"[\s,.;:/-]+", low) if w and w.isalpha()]
    has_name_like = len(words) >= 2 and all(len(w) >= 2 for w in words[:2])
    has_single_name = len(words) >= 1 and len(words[0]) >= 3
    if has_phone and has_name_like:
        return True
    if has_phone and has_single_name:
        return True
    if has_phone and has_relation:
        return True
    if has_relation and has_name_like:
        return True
    return False


def _build_summary(data: dict[str, Any]) -> str:
    skills = data.get("skills") or []
    other = str(data.get("skills_other") or "").strip()
    skills_line = ", ".join(skills) if skills else "No especificado"
    if other:
        skills_line = f"{skills_line}. Otras funciones: {other}"
    return (
        "Resumen para revisión humana:\n"
        f"- Nombre: {data.get('full_name') or 'No especificado'}\n"
        f"- Edad: {data.get('age') or 'No especificada'}\n"
        f"- Ciudad y sector: {data.get('city_sector') or 'No especificado'}\n"
        f"- Experiencia laboral: {data.get('experience') or 'No especificada'}\n"
        f"- Funciones: {skills_line}\n"
        f"- Disponibilidad: {data.get('availability') or 'No especificada'}\n"
        f"- Referencias: {data.get('references') or 'No especificadas'}"
    )


def _validate_and_capture(step: str, text: str) -> tuple[bool, dict[str, Any], str]:
    if step == STEP_ASK_NAME:
        if _is_invalid_exact(text, {"hola", "si", "no", "no tengo", "ok", "dale", "que", "q", "yo"}):
            return False, {}, "Por favor, indícame tu nombre completo con nombre y apellido."
        if re.fullmatch(r"[^\w]+", text):
            return False, {}, "Por favor, indícame tu nombre completo con nombre y apellido."
        low = _normalize_token_text(text)
        if any(x in low for x in {"solo nombre", "domestica"}):
            return False, {}, "Por favor, indícame tu nombre completo con nombre y apellido."
        full_name = _normalize_text(text)
        low = _normalize_token_text(text)
        if any(x in low for x in {"me llamo", "soy", "mi nombre es", " tengo ", " edad "}) or bool(re.search(r"\d", low)):
            full_name = _extract_full_name(text)
        if len(full_name) < 5 or len(full_name.split()) < 2:
            return False, {}, "Por favor, indícame tu nombre completo con nombre y apellido."
        return True, {"full_name": full_name}, ""

    if step == STEP_ASK_AGE:
        if _is_confusion_or_offtopic(text):
            return False, {}, "confusion_offtopic"
        age = _extract_age(text)
        if age is None:
            return False, {}, "invalid_format"
        return True, {"age": age}, ""

    if step == STEP_ASK_CITY_SECTOR:
        if _is_confusion_or_offtopic(text):
            return False, {}, "confusion_offtopic"
        if _is_invalid_exact(text, {"no tengo", "no se", "ninguno", "no", "si", "ok", "que", "por ahi", "cerca", "en mi casa"}):
            return False, {}, "invalid_format"
        low = _normalize_token_text(text)
        if any(x in low for x in {"por ahi", "cerca", "en mi casa"}):
            return False, {}, "invalid_format"
        has_location_signal = ("," in text) or ("vivo en" in low) or len(text.split()) >= 2
        if len(text) < 6 or not has_location_signal:
            return False, {}, "invalid_format"
        return True, {"city_sector": text}, ""

    if step == STEP_ASK_EXPERIENCE:
        if _is_confusion_or_offtopic(text):
            return False, {}, "confusion_offtopic"
        low = _normalize_token_text(text)
        if low in {"que es eso", "normal"}:
            return False, {}, "invalid_format"
        if low == "no tengo":
            return False, {}, "invalid_format"
        if "no tengo experiencia" in low:
            if "pero" in low or "quiero aprender" in low:
                return True, {"experience": "Sin experiencia laboral previa (dispuesta a aprender)."}, ""
            return True, {"experience": "Sin experiencia laboral previa."}, ""
        if len(text) < 8:
            return False, {}, "invalid_format"
        return True, {"experience": text}, ""

    if step == STEP_ASK_SKILLS:
        if _is_confusion_or_offtopic(text):
            return False, {}, "confusion_offtopic"
        parsed = _extract_skills(text)
        if not parsed.get("skills"):
            return False, {}, "invalid_format"
        return True, {"skills": parsed.get("skills") or [], "skills_other": parsed.get("other") or ""}, ""

    if step == STEP_ASK_AVAILABILITY:
        if _is_confusion_or_offtopic(text):
            return False, {}, "confusion_offtopic"
        availability = _extract_availability(text)
        if not availability:
            return False, {}, "invalid_format"
        return True, {"availability": availability}, ""

    if step == STEP_ASK_REFERENCES:
        if _is_invalid_exact(text, {"no", "ninguna"}):
            return False, {}, "invalid_format"
        if _is_confusion_or_offtopic(text):
            return False, {}, "confusion_offtopic"
        if not _looks_like_reference(text):
            return False, {}, "invalid_format"
        return True, {"references": text}, ""

    return False, {}, ""


def _next_step(step: str) -> str:
    order = [
        STEP_ASK_NAME,
        STEP_ASK_AGE,
        STEP_ASK_CITY_SECTOR,
        STEP_ASK_EXPERIENCE,
        STEP_ASK_SKILLS,
        STEP_ASK_AVAILABILITY,
        STEP_ASK_REFERENCES,
        STEP_COMPLETED,
    ]
    try:
        idx = order.index(step)
    except ValueError:
        return STEP_ASK_NAME
    if idx >= len(order) - 1:
        return STEP_COMPLETED
    return order[idx + 1]


def _read_state(conversation: BotConversation) -> dict[str, Any]:
    metadata = dict(getattr(conversation, "metadata_json", {}) or {})
    raw = metadata.get(FLOW_KEY)
    if not isinstance(raw, dict):
        state = _base_state()
        log_bot_event(
            "interview_flow_initialized",
            metadata={"conversation_id": int(getattr(conversation, "id", 0) or 0), "current_step": state.get("current_step")},
        )
        return state
    state = _base_state()
    state.update(raw)
    if not isinstance(state.get("collected_data"), dict):
        state["collected_data"] = {}
    if not isinstance(state.get("detected_future_data"), dict):
        state["detected_future_data"] = {}
    if not isinstance(state.get("data_sources"), dict):
        state["data_sources"] = {}
    if not isinstance(state.get("help_repeat_count_by_step"), dict):
        state["help_repeat_count_by_step"] = {}
    if not isinstance(state.get("last_help_message_by_step"), dict):
        state["last_help_message_by_step"] = {}
    current_step = str(state.get("current_step") or "").strip()
    if current_step not in {
        STEP_ASK_NAME,
        STEP_ASK_AGE,
        STEP_ASK_CITY_SECTOR,
        STEP_ASK_EXPERIENCE,
        STEP_ASK_SKILLS,
        STEP_ASK_AVAILABILITY,
        STEP_ASK_REFERENCES,
        STEP_COMPLETED,
    }:
        log_bot_event(
            "interview_flow_validation_failed",
            level="warning",
            metadata={
                "conversation_id": int(getattr(conversation, "id", 0) or 0),
                "invalid_current_step": current_step,
            },
        )
        state = _base_state()
    else:
        log_bot_event(
            "interview_flow_loaded",
            metadata={
                "conversation_id": int(getattr(conversation, "id", 0) or 0),
                "current_step": state.get("current_step"),
                "completed": bool(state.get("completed")),
            },
        )
    return state


def _write_state(conversation: BotConversation, state: dict[str, Any]) -> None:
    metadata = dict(getattr(conversation, "metadata_json", {}) or {})
    metadata[FLOW_KEY] = {
        "current_step": str(state.get("current_step") or STEP_ASK_NAME),
        "collected_data": dict(state.get("collected_data") or {}),
        "detected_future_data": dict(state.get("detected_future_data") or {}),
        "data_sources": dict(state.get("data_sources") or {}),
        "completed": bool(state.get("completed")),
        "last_question": str(state.get("last_question") or ""),
        "summary": str(state.get("summary") or ""),
        "last_updated_at": str(state.get("last_updated_at") or str(utc_now_naive())),
        "validation_error": str(state.get("validation_error") or ""),
        "last_invalid_answer": str(state.get("last_invalid_answer") or ""),
        "greeting_sent": bool(state.get("greeting_sent", False)),
        "ai_classifier_used": bool(state.get("ai_classifier_used", False)),
        "ai_classifier_confidence": float(state.get("ai_classifier_confidence") or 0.0),
        "ai_classifier_reason": str(state.get("ai_classifier_reason") or ""),
        "ai_classifier_raw": str(state.get("ai_classifier_raw") or ""),
        "help_repeat_count_by_step": dict(state.get("help_repeat_count_by_step") or {}),
        "last_help_message_by_step": dict(state.get("last_help_message_by_step") or {}),
    }
    conversation.metadata_json = metadata
    log_bot_event(
        "interview_flow_state_saved",
        metadata={
            "conversation_id": int(getattr(conversation, "id", 0) or 0),
            "current_step": str(state.get("current_step") or STEP_ASK_NAME),
            "completed": bool(state.get("completed")),
        },
    )


def process_interview_inbound(*, conversation: BotConversation, inbound_text: str, message_type: str = "text") -> dict[str, Any]:
    state = _read_state(conversation)
    text = _normalize_text(inbound_text)

    if str(message_type or "text").strip().lower() != "text":
        return {"active": False, "reason": "non_text"}

    if bool(state.get("completed")):
        summary = str(state.get("summary") or "")
        reply = "Gracias. Tu registro ya está completo y quedará para revisión humana."
        if summary:
            reply = f"{reply}\n\n{summary}"
        state["last_question"] = reply
        _write_state(conversation, state)
        return {"active": True, "advanced": False, "reply": reply, "state": state}

    current_step = str(state.get("current_step") or STEP_ASK_NAME)
    collected = dict(state.get("collected_data") or {})
    detected_future = dict(state.get("detected_future_data") or {})
    current_field = _step_field(current_step)
    if bool((dict(getattr(conversation, "metadata_json", {}) or {})).get(FLOW_KEY)):
        log_bot_event(
            "interview_flow_resume_existing",
            metadata={"conversation_id": int(getattr(conversation, "id", 0) or 0), "current_step": current_step},
        )
    low = text.lower()
    if current_step == STEP_ASK_NAME and not collected and not bool(state.get("greeting_sent")):
        if low in {"hola", "buenas", "hello", "ola", "holi"}:
            question = _question_for_step(STEP_ASK_NAME, include_greeting=True)
            state["greeting_sent"] = True
            state["last_question"] = question
            _write_state(conversation, state)
            return {"active": True, "advanced": False, "reply": question, "state": state}

    if not text:
        question = _safe_ai_copy(
            base_text=_question_for_step(current_step),
            step=current_step,
            mode="question",
            conversation=conversation,
        )
        state["last_question"] = question
        _write_state(conversation, state)
        return {"active": True, "advanced": False, "reply": question, "state": state}

    extracted_future = _extract_future_data(text)
    for f, v in extracted_future.items():
        if f == current_field:
            continue
        _register_field(state=state, field=f, value=v, source="multi_detected", confirmed=False)
    detected_future = dict(state.get("detected_future_data") or {})

    if current_field and current_field in detected_future and current_field in _CONFIRMABLE_FUTURE_FIELDS:
        candidate = dict(detected_future.get(current_field) or {})
        value = candidate.get("value")
        if _is_affirmative(text):
            _register_field(state=state, field=current_field, value=value, source="future_confirmation", confirmed=True, overwrite_confirmed=True)
            collected = dict(state.get("collected_data") or {})
            captured = {current_field: collected.get(current_field)}
            valid = True
            clarification = ""
        elif _is_negative(text):
            detected_future.pop(current_field, None)
            state["detected_future_data"] = detected_future
            reply = _question_for_step(current_step)
            state["last_question"] = reply
            state["validation_error"] = ""
            state["last_invalid_answer"] = ""
            _write_state(conversation, state)
            return {"active": True, "advanced": False, "reply": reply, "state": state}
        else:
            reply = _confirm_prompt_for_field(current_field, value)
            state["last_question"] = reply
            _write_state(conversation, state)
            return {"active": True, "advanced": False, "reply": reply, "state": state}
    else:
        valid, captured, clarification = _validate_and_capture(current_step, text)

    ai_meta = {"used": False, "confidence": 0.0, "reason": "", "raw": ""}
    if not valid:
        ai_cls = _try_ai_classifier(
            step=current_step,
            inbound_text=text,
            fallback_reason=clarification,
            conversation=conversation,
        )
        if bool(ai_cls.get("used")):
            ai_meta = {
                "used": True,
                "confidence": float(ai_cls.get("confidence") or 0.0),
                "reason": str(ai_cls.get("reason") or ""),
                "raw": str(ai_cls.get("raw") or ""),
            }
            ai_valid = bool(ai_cls.get("is_valid_for_step", False))
            ai_conf = float(ai_cls.get("confidence") or 0.0)
            ai_value = _normalize_text(str(ai_cls.get("normalized_value") or ""))
            if ai_valid and ai_conf >= AI_CLASSIFIER_MIN_CONFIDENCE and ai_value:
                ai_valid_hard, ai_captured_hard, _ = _validate_and_capture(current_step, ai_value)
                if ai_valid_hard:
                    valid = True
                    captured = ai_captured_hard
    if not valid:
        help_counts = dict(state.get("help_repeat_count_by_step") or {})
        last_help = dict(state.get("last_help_message_by_step") or {})
        repeat_count = int(help_counts.get(current_step) or 0)
        base_help = build_step_help_message(current_step, clarification or "invalid", repeat_count=repeat_count)
        prev = str(last_help.get(current_step) or "")
        if base_help == prev:
            base_help = build_step_help_message(current_step, clarification or "invalid", repeat_count=repeat_count + 1)
        reply = _safe_ai_copy(
            base_text=base_help,
            step=current_step,
            mode="redirect",
            conversation=conversation,
        )
        if reply == prev:
            fallback = build_step_help_message(current_step, clarification or "invalid", repeat_count=repeat_count + 2)
            reply = fallback if fallback != prev else _question_for_step(current_step)
        help_counts[current_step] = repeat_count + 1
        last_help[current_step] = reply
        state["help_repeat_count_by_step"] = help_counts
        state["last_help_message_by_step"] = last_help
        state["ai_classifier_used"] = bool(ai_meta.get("used"))
        state["ai_classifier_confidence"] = float(ai_meta.get("confidence") or 0.0)
        state["ai_classifier_reason"] = str(ai_meta.get("reason") or "")
        state["ai_classifier_raw"] = str(ai_meta.get("raw") or "")
        state["validation_error"] = reply
        state["last_invalid_answer"] = text
        state["last_question"] = reply
        _write_state(conversation, state)
        return {"active": True, "advanced": False, "reply": reply, "state": state}

    for f, v in dict(captured or {}).items():
        _register_field(state=state, field=f, value=v, source="step_answer", confirmed=True, overwrite_confirmed=True)
    collected = dict(state.get("collected_data") or {})
    next_step = _next_step(current_step)
    state["validation_error"] = ""
    state["last_invalid_answer"] = ""
    help_counts = dict(state.get("help_repeat_count_by_step") or {})
    last_help = dict(state.get("last_help_message_by_step") or {})
    if current_step in help_counts:
        help_counts.pop(current_step, None)
    if current_step in last_help:
        last_help.pop(current_step, None)
    state["help_repeat_count_by_step"] = help_counts
    state["last_help_message_by_step"] = last_help
    state["ai_classifier_used"] = bool(ai_meta.get("used"))
    state["ai_classifier_confidence"] = float(ai_meta.get("confidence") or 0.0)
    state["ai_classifier_reason"] = str(ai_meta.get("reason") or "")
    state["ai_classifier_raw"] = str(ai_meta.get("raw") or "")

    if next_step == STEP_COMPLETED:
        summary = _build_summary(collected)
        state["current_step"] = STEP_COMPLETED
        state["completed"] = True
        state["summary"] = summary
        reply = _safe_ai_copy(
            base_text="Gracias. Completamos el registro y lo pasaremos a revisión humana.",
            step=STEP_COMPLETED,
            mode="question",
            conversation=conversation,
        )
        state["last_question"] = reply
        _write_state(conversation, state)
        draft_meta = {"created": False, "draft_id": None, "error": ""}
        try:
            draft, created = get_or_create_interview_flow_draft(conversation)
            draft_meta = {"created": bool(created), "draft_id": int(draft.id), "error": ""}
        except Exception as exc:
            draft_meta = {"created": False, "draft_id": None, "error": str(exc)[:120]}
        return {"active": True, "advanced": True, "reply": reply, "state": state, "summary": summary, "draft_candidate": draft_meta}

    next_field = _step_field(next_step)
    confirm_question = ""
    if next_field and next_field in _CONFIRMABLE_FUTURE_FIELDS and next_field in dict(state.get("detected_future_data") or {}):
        fv = dict(state.get("detected_future_data") or {}).get(next_field) or {}
        confirm_question = _confirm_prompt_for_field(next_field, fv.get("value"))
    question_base = confirm_question or _question_for_step(next_step, include_greeting=False)
    question = _safe_ai_copy(base_text=question_base, step=next_step, mode="question", conversation=conversation)
    state["current_step"] = next_step
    state["completed"] = False
    state["summary"] = ""
    state["last_question"] = question
    log_bot_event(
        "interview_flow_step_advanced",
        metadata={
            "conversation_id": int(getattr(conversation, "id", 0) or 0),
            "from_step": current_step,
            "to_step": next_step,
        },
    )
    _write_state(conversation, state)
    return {"active": True, "advanced": True, "reply": question, "state": state}
