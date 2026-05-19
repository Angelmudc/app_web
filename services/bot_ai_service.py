"""Servicio de IA controlado para FAQ seguras del bot WhatsApp (Fase 4)."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import requests
from requests import HTTPError, RequestException
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import SSLError, Timeout

from services.bot_constants import (
    INTENT_FAQ_CONTACTO,
    INTENT_FAQ_ESTADO_GENERAL,
    INTENT_FAQ_HORARIOS,
    INTENT_FAQ_REQUISITOS,
    INTENT_FAQ_UBICACION,
    INTENT_HUMAN_REQUEST,
    INTENT_UNKNOWN,
    SAFE_INTENTS,
)
from services.bot_ai_limits_service import (
    ai_max_context_messages,
    ai_max_input_chars,
    ai_max_output_chars,
    try_reserve_ai_session_request,
)

PROMPT_VERSION = "phase4_v2"
MIN_CONFIDENCE = 0.75
MAX_CONTEXT_MESSAGES = 3

SAFE_FAQ_TEMPLATES = {
    INTENT_FAQ_HORARIOS: "Nuestro horario de atención es de lunes a viernes en horario laboral. Si deseas, un asesor te confirma el horario exacto por esta vía.",
    INTENT_FAQ_REQUISITOS: "Podemos orientarte con los requisitos generales del proceso. Para confirmar tu caso específico, te apoyará un asesor humano.",
    INTENT_FAQ_UBICACION: "Estamos en Santiago, República Dominicana. Si gustas, te compartimos ubicación exacta por atención humana.",
    INTENT_FAQ_CONTACTO: "Puedes escribirnos por este WhatsApp y un asesor te atiende. También podemos compartirte otros canales oficiales de contacto.",
    INTENT_FAQ_ESTADO_GENERAL: "Puedo ayudarte con un estado general. Para validaciones específicas del caso, un asesor humano debe confirmarlo.",
}


ESCALATION_KEYWORDS = {
    "pago",
    "pagos",
    "transferencia",
    "legal",
    "demanda",
    "reclamacion",
    "reclamación",
    "queja",
    "reclamo",
    "empleo",
    "vacante",
    "precio",
    "cuanto cuesta",
    "cuánto cuesta",
    "cedula",
    "cédula",
}


def _is_true(value: str | None, *, default: bool = False) -> bool:
    raw = (value or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def is_ai_enabled() -> bool:
    return _is_true(os.getenv("BOT_AI_ENABLED"), default=False)


def is_autoreply_enabled() -> bool:
    return _is_true(os.getenv("BOT_AUTOREPLY_ENABLED"), default=False)


def ai_config() -> dict[str, Any]:
    return {
        "provider": (os.getenv("BOT_AI_PROVIDER") or "openai").strip().lower(),
        "model": (os.getenv("BOT_AI_MODEL") or "gpt-4.1-mini").strip(),
        "api_key": (os.getenv("BOT_AI_API_KEY") or "").strip(),
        "timeout_seconds": int((os.getenv("BOT_AI_TIMEOUT_SECONDS") or "8").strip() or "8"),
        "max_tokens": int((os.getenv("BOT_AI_MAX_TOKENS") or "220").strip() or "220"),
        "temperature": float((os.getenv("BOT_AI_TEMPERATURE") or "0").strip() or "0"),
    }


def redact_sensitive_text(text: str | None, *, max_chars: int | None = None) -> str:
    value = (text or "").strip()
    if not value:
        return ""
    value = re.sub(r"\b\d{3}-?\d{7}-?\d\b", "[REDACTED_CEDULA]", value)
    value = re.sub(r"\b\d{11}\b", "[REDACTED_NUMBER]", value)
    value = re.sub(
        r"\b(calle|av\.?|avenida|sector|residencial|direccion|dirección)\b[^\n,;.]*",
        "[REDACTED_ADDRESS]",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\b(cuenta|tarjeta|iban|swift)\b[^\n,;.]*", "[REDACTED_FINANCIAL]", value, flags=re.IGNORECASE)
    max_len = int(max_chars or ai_max_input_chars())
    return value[:max_len]


def build_safe_context(*, message_text: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    ctx = context or {}
    history = ctx.get("history") or []
    max_ctx_msgs = int(ai_max_context_messages())
    max_input_chars = int(ai_max_input_chars())
    trimmed_history = history[-max_ctx_msgs:]
    safe_history = []
    for item in trimmed_history:
        safe_history.append(
            {
                "role": (item.get("role") or "user")[:20],
                "text": redact_sensitive_text(item.get("text") or "", max_chars=max_input_chars),
            }
        )
    raw_protocol_ctx = ctx.get("protocol_context") or {}
    safe_protocol_ctx = {
        "protocol_version": str(raw_protocol_ctx.get("protocol_version") or "")[:40],
        "current_step_code": str(raw_protocol_ctx.get("current_step_code") or "")[:60],
        "step_title": str(raw_protocol_ctx.get("step_title") or "")[:120],
        "step_prompt": redact_sensitive_text(raw_protocol_ctx.get("step_prompt") or "", max_chars=max_input_chars),
        "expected_answers": [str(x)[:60] for x in (raw_protocol_ctx.get("expected_answers") or [])[:8]],
        "validations": [str(x)[:60] for x in (raw_protocol_ctx.get("validations") or [])[:8]],
        "requires_human": bool(raw_protocol_ctx.get("requires_human", False)),
    }
    return {
        "identity_role": (ctx.get("identity_role") or "unknown")[:40],
        "history": safe_history,
        "latest_user_text": redact_sensitive_text(message_text, max_chars=max_input_chars),
        "allowed_intents": sorted(SAFE_INTENTS),
        "protocol_context": safe_protocol_ctx,
    }


def _system_prompt() -> str:
    return (
        "Eres un clasificador y redactor SEGURO para un bot interno. "
        "Responde SIEMPRE en JSON válido con llaves exactas: intent, answer_text, confidence, requires_human. "
        "Nunca incluyas texto fuera del JSON.\n"
        "Debes responder principalmente al último mensaje del usuario (latest_user_text). "
        "Usa history solo como contexto secundario y nunca dejes que history contradiga latest_user_text.\n"
        "Usa protocol_context para alinear la sugerencia a la etapa actual del protocolo.\n"
        "Reglas de protocolo:\n"
        "- Responde considerando current_step_code, step_prompt, validations y expected_answers.\n"
        "- NO avances etapas ni digas que el proceso avanzó.\n"
        "- Si protocol_context.requires_human=true entonces requires_human=true.\n"
        "- Si el usuario responde fuera de etapa, mantén intent seguro y sugiere volver al flujo de forma amable.\n"
        "- Si el tema es sensible o fuera del protocolo, escalar a humano.\n"
        "Catálogo de intents permitidos:\n"
        "- FAQ_HORARIOS: pregunta directa sobre horario/atención.\n"
        "- FAQ_REQUISITOS: pregunta general de requisitos del proceso.\n"
        "- FAQ_UBICACION: pregunta de ubicación física.\n"
        "- FAQ_CONTACTO: pregunta por canales de contacto oficiales.\n"
        "- FAQ_ESTADO_GENERAL: pregunta general de estado, sin detalles sensibles.\n"
        "- HUMAN_REQUEST: cuando la persona pide explícitamente hablar con humano/asesor.\n"
        "- UNKNOWN: ambiguo, fuera de catálogo o sensible.\n"
        "Cuándo requires_human=false: SOLO FAQ claras y no sensibles del catálogo, con respuesta breve y segura.\n"
        "Cuándo requires_human=true: cualquier duda, ambigüedad, sensible o fuera de catálogo.\n"
        "Temas sensibles (siempre escalar): precios/costos no configurados, quejas, legal, pagos, empleos/vacantes, datos privados, conflictos.\n"
        "Bandas de confidence:\n"
        "- FAQ clara del catálogo: 0.80-0.95.\n"
        "- HUMAN_REQUEST explícito: 0.85-0.98 y requires_human=true.\n"
        "- Ambiguo/sensible/fuera de catálogo: 0.35-0.69 y requires_human=true.\n"
        "Ejemplos mínimos:\n"
        "1) '¿Cuál es su horario?' -> FAQ_HORARIOS, requires_human=false, confidence 0.9.\n"
        "2) '¿Qué requisitos necesito?' -> FAQ_REQUISITOS, requires_human=false, confidence 0.88.\n"
        "3) 'Quiero hablar con una persona' -> HUMAN_REQUEST, requires_human=true, confidence 0.92.\n"
        "4) '¿Cuánto cuesta?' -> UNKNOWN, requires_human=true, confidence 0.5.\n"
        "5) 'Tengo una queja legal y pago pendiente' -> UNKNOWN, requires_human=true, confidence 0.45.\n"
        "No inventes datos ni ejecutes acciones administrativas. Español dominicano neutral, profesional, breve."
    )


def _user_prompt(safe_context: dict[str, Any]) -> str:
    return json.dumps(
        {
            "task": "Clasifica intención segura y redacta respuesta breve o escalar a humano.",
            "safe_context": safe_context,
        },
        ensure_ascii=False,
    )


def _openai_chat_completion(*, model: str, api_key: str, timeout_seconds: int, max_tokens: int, temperature: float, safe_context: dict[str, Any]) -> dict[str, Any]:
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": _user_prompt(safe_context)},
        ],
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout_seconds)
    resp.raise_for_status()
    body = resp.json() if resp.content else {}
    choices = body.get("choices") if isinstance(body, dict) else None
    if not isinstance(choices, list) or not choices:
        raise ValueError("empty_choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else {}
    content = (message or {}).get("content")
    if isinstance(content, list):
        text_parts = [part.get("text") for part in content if isinstance(part, dict) and isinstance(part.get("text"), str)]
        content = "\n".join(text_parts)
    if not isinstance(content, str) or not content.strip():
        raise ValueError("empty_content")
    return {"raw_text": content.strip()}


def _extract_provider_error(resp: Any) -> tuple[str, str]:
    try:
        body = resp.json() if resp is not None else {}
    except Exception:
        body = {}
    node = body.get("error") if isinstance(body, dict) else {}
    if not isinstance(node, dict):
        return "", ""
    error_type = str(node.get("type") or "").strip().lower()
    message = str(node.get("message") or "").strip().lower()
    code = str(node.get("code") or "").strip().lower()
    joined = " ".join(x for x in [error_type, code, message] if x)
    return error_type, joined


def _map_http_error(exc: HTTPError) -> tuple[str, str]:
    resp = getattr(exc, "response", None)
    status = int(getattr(resp, "status_code", 0) or 0)
    error_type, joined = _extract_provider_error(resp)

    if status in {401, 403}:
        return "invalid_api_key", error_type or "auth_error"
    if status == 429:
        return "rate_limit", error_type or "rate_limit_error"
    if "model" in joined and ("not found" in joined or "does not exist" in joined):
        return "model_not_found", error_type or "invalid_request_error"
    if status >= 500:
        return "provider_bad_response", error_type or "provider_server_error"
    if status >= 400:
        return "provider_bad_response", error_type or "provider_http_error"
    return "unknown_provider_error", error_type or "unknown_http_error"


def _result_error(*, code: str, model: str, safe_context: dict[str, Any] | None = None, error_type: str | None = None) -> dict[str, Any]:
    out = {
        "ok": False,
        "error_code": code,
        "intent": INTENT_UNKNOWN,
        "confidence": 0.0,
        "requires_human": True,
        "answer_text": "",
        "prompt_version": PROMPT_VERSION,
        "ai_model": model,
    }
    if safe_context is not None:
        out["safe_context"] = safe_context
    if error_type:
        out["error_type"] = error_type
    return out


def _coerce_ai_json(raw_text: str) -> dict[str, Any]:
    data = json.loads(raw_text)
    intent = str(data.get("intent") or "").strip().upper() or INTENT_UNKNOWN
    max_output = int(ai_max_output_chars())
    answer_text = str(data.get("answer_text") or "").strip()[:max_output]
    confidence_raw = data.get("confidence", 0)
    try:
        confidence = float(confidence_raw)
    except Exception:
        confidence = 0.0
    requires_human = bool(data.get("requires_human", False))
    return {
        "intent": intent,
        "answer_text": answer_text,
        "confidence": confidence,
        "requires_human": requires_human,
    }


def should_escalate(ai_response: dict[str, Any]) -> tuple[bool, str]:
    intent = str(ai_response.get("intent") or "").strip().upper()
    answer_text = str(ai_response.get("answer_text") or "").strip()
    confidence = float(ai_response.get("confidence") or 0)

    if intent not in SAFE_INTENTS:
        return True, "AI_INTENT_UNKNOWN"
    if confidence < MIN_CONFIDENCE:
        return True, "AI_LOW_CONFIDENCE"
    if not answer_text and intent != INTENT_HUMAN_REQUEST:
        return True, "AI_EMPTY_ANSWER"
    if any(k in answer_text.lower() for k in ESCALATION_KEYWORDS):
        return True, "AI_SENSITIVE_TOPIC"
    if bool(ai_response.get("requires_human")):
        return True, "AI_REQUIRES_HUMAN"
    if intent in {INTENT_HUMAN_REQUEST, INTENT_UNKNOWN}:
        return True, "AI_HUMAN_OR_UNKNOWN"
    return False, "AI_SAFE"


def classify_intent(message_text: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    config = ai_config()
    if not is_ai_enabled():
        return _result_error(code="ai_disabled", model=config["model"])

    provider = config["provider"]
    if provider != "openai":
        return _result_error(code="provider_not_supported", model=config["model"], error_type="unknown_provider_error")
    if not config["api_key"]:
        return _result_error(code="api_key_missing", model=config["model"], error_type="missing_api_key")
    if not try_reserve_ai_session_request():
        return _result_error(code="session_limit_reached", model=config["model"], error_type="safety_limit")

    safe_context = build_safe_context(message_text=message_text, context=context)
    try:
        response = _openai_chat_completion(
            model=config["model"],
            api_key=config["api_key"],
            timeout_seconds=config["timeout_seconds"],
            max_tokens=config["max_tokens"],
            temperature=config["temperature"],
            safe_context=safe_context,
        )
        parsed = _coerce_ai_json(response["raw_text"])
        escalate, reason = should_escalate(parsed)
        out = {
            "ok": True,
            "intent": parsed["intent"],
            "answer_text": parsed["answer_text"],
            "confidence": parsed["confidence"],
            "requires_human": escalate,
            "escalation_reason": reason,
            "prompt_version": PROMPT_VERSION,
            "ai_model": config["model"],
            "safe_context": safe_context,
        }
        return out
    except json.JSONDecodeError:
        return _result_error(
            code="json_parse_error",
            model=config["model"],
            safe_context=safe_context,
            error_type="provider_bad_response",
        )
    except Timeout:
        return _result_error(code="timeout", model=config["model"], safe_context=safe_context, error_type="timeout")
    except SSLError:
        return _result_error(code="ssl_error", model=config["model"], safe_context=safe_context, error_type="ssl_error")
    except RequestsConnectionError:
        return _result_error(
            code="network_error",
            model=config["model"],
            safe_context=safe_context,
            error_type="network_error",
        )
    except HTTPError as exc:
        code, err_type = _map_http_error(exc)
        return _result_error(code=code, model=config["model"], safe_context=safe_context, error_type=err_type)
    except RequestException:
        return _result_error(
            code="unknown_provider_error",
            model=config["model"],
            safe_context=safe_context,
            error_type="request_exception",
        )
    except Exception:
        return _result_error(code="ai_error", model=config["model"], safe_context=safe_context, error_type="unknown_error")


def generate_safe_reply(intent: str, context: dict[str, Any] | None = None) -> str:
    normalized = (intent or "").strip().upper()
    if normalized in SAFE_FAQ_TEMPLATES:
        return SAFE_FAQ_TEMPLATES[normalized]
    return "Gracias por escribirnos. Para ayudarte correctamente, te pasamos con un asesor humano."
