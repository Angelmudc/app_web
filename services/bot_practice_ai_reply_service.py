# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
from typing import Any

import requests

from services.whatsapp_cloud_service import is_whatsapp_enabled

_MAX_REPLY_CHARS = 250
_MAX_SENTENCES = 2
_PROHIBITED_STYLE_PHRASES = ("mi amor", "bb", "bebé", "bebe", "mami", "papi")
_STYLE_FORMAL_PHRASES = ("procederemos", "conforme", "expediente", "validación documental")
_STEP_VARIANTS = {
    "PERSONAL_CONFIRMATION": [
        "Confirma por favor con SI o NO.",
        "Necesito que respondas SI o NO para continuar.",
        "Por favor indica SI o NO para seguir.",
    ],
    "BASIC_INFO": [
        "Compárteme tu nombre completo y edad, por favor.",
        "Para avanzar, necesito tu nombre completo y tu edad.",
        "Indica tu nombre completo y edad para continuar.",
    ],
    "ADDRESS": [
        "Compárteme tu ciudad y sector, por favor.",
        "Necesito tu ciudad y sector para continuar.",
        "Indica ciudad y sector donde vives.",
    ],
    "WORK_TYPE": [
        "¿Prefieres salida diaria o dormida?",
        "Confírmame si buscas salida diaria o dormida.",
        "Indica si prefieres salida diaria o dormida.",
    ],
    "TRANSPORT_ROUTE": [
        "Cuéntame tu ruta o transporte habitual para llegar al trabajo.",
        "Necesito tu ruta de transporte para continuar.",
        "Indica cómo te trasladas normalmente al trabajo.",
    ],
}


class PracticeAIReplyError(Exception):
    """Controlled error for practice reply generation."""



def _is_true(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}



def _app_env() -> str:
    return str(os.getenv("APP_ENV") or "").strip().lower()



def _conversation_type(conversation: Any) -> str:
    metadata = dict(getattr(conversation, "metadata_json", {}) or {})
    return str(metadata.get("conversation_type") or "").strip().lower()



def can_use_practice_ai_reply(conversation: Any) -> tuple[bool, str]:
    if not _is_true(os.getenv("BOT_PRACTICE_AI_REPLY_ENABLED")):
        return False, "feature_flag_disabled"
    if _app_env() not in {"local", "development", "dev", "test", "testing"}:
        return False, "app_env_not_local"
    if is_whatsapp_enabled() or _is_true(os.getenv("WHATSAPP_ENABLED")):
        return False, "whatsapp_enabled"
    if _is_true(os.getenv("BOT_AUTOREPLY_ENABLED")):
        return False, "autoreply_enabled"
    if _is_true(os.getenv("BOT_PRACTICE_REAL_OUTBOUND_ENABLED")):
        return False, "real_outbound_enabled"
    if _conversation_type(conversation) != "local_practice":
        return False, "not_local_practice"
    return True, "ok"



def build_practice_ai_reply_prompt(
    *,
    base_suggested_reply: str,
    current_step: str,
    candidate_message: str,
    context: dict[str, Any] | None = None,
    requires_human: bool = False,
) -> str:
    safe_context = dict(context or {})
    parts = [
        "Reescribe SOLO la respuesta base para que suene humana y breve.",
        "No cambies la intención ni agregues datos.",
        "No prometas empleo ni aprobación.",
        "No digas que fue enviado por WhatsApp.",
        "Máximo 250 caracteres.",
        f"current_step={str(current_step or '').strip()}",
        f"requires_human={str(bool(requires_human)).lower()}",
        f"candidate_last_message={str(candidate_message or '').strip()}",
        f"base_reply={str(base_suggested_reply or '').strip()}",
    ]
    if safe_context:
        parts.append(f"contexto_limitado={safe_context}")
    parts.append("Devuelve SOLO texto final.")
    return "\n".join(parts)



def _call_provider(*, prompt: str, timeout_seconds: int, max_tokens: int) -> str:
    provider = str(os.getenv("BOT_PRACTICE_AI_REPLY_PROVIDER") or "").strip().lower()
    if provider == "fake":
        fake = str(os.getenv("BOT_PRACTICE_AI_REPLY_FAKE_RESPONSE") or "").strip()
        if fake:
            return fake
        raise PracticeAIReplyError("provider_fake_empty")

    if provider != "openai":
        raise PracticeAIReplyError("provider_not_configured")

    api_key = str(os.getenv("BOT_PRACTICE_AI_REPLY_API_KEY") or os.getenv("BOT_AI_API_KEY") or "").strip()
    model = str(os.getenv("BOT_PRACTICE_AI_REPLY_MODEL") or "gpt-4.1-mini").strip()
    if not api_key:
        raise PracticeAIReplyError("provider_not_configured")

    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "temperature": 0,
            "max_tokens": int(max_tokens),
            "messages": [
                {"role": "system", "content": "Reescribe texto breve sin alterar intención."},
                {"role": "user", "content": prompt},
            ],
        },
        timeout=int(timeout_seconds),
    )
    response.raise_for_status()
    body = response.json() if response.content else {}
    choices = body.get("choices") if isinstance(body, dict) else []
    if not choices:
        raise PracticeAIReplyError("provider_empty")
    content = (((choices[0] or {}).get("message") or {}).get("content") or "") if isinstance(choices[0], dict) else ""
    text = str(content or "").strip()
    if not text:
        raise PracticeAIReplyError("provider_empty")
    return text



def generate_practice_ai_reply(
    *,
    base_suggested_reply: str,
    current_step: str,
    candidate_message: str,
    context: dict[str, Any] | None = None,
    requires_human: bool = False,
) -> dict[str, Any]:
    timeout_seconds = int(str(os.getenv("BOT_PRACTICE_AI_REPLY_TIMEOUT_SECONDS") or "3").strip() or "3")
    max_tokens = int(str(os.getenv("BOT_PRACTICE_AI_REPLY_MAX_TOKENS") or "90").strip() or "90")
    prompt = build_practice_ai_reply_prompt(
        base_suggested_reply=base_suggested_reply,
        current_step=current_step,
        candidate_message=candidate_message,
        context=context,
        requires_human=requires_human,
    )
    try:
        text = _call_provider(prompt=prompt, timeout_seconds=timeout_seconds, max_tokens=max_tokens)
        return {"ok": True, "ai_suggested_reply": text, "provider": str(os.getenv("BOT_PRACTICE_AI_REPLY_PROVIDER") or "")}
    except requests.Timeout:
        return {"ok": False, "error": "timeout", "ai_suggested_reply": ""}
    except requests.RequestException:
        return {"ok": False, "error": "provider_error", "ai_suggested_reply": ""}
    except PracticeAIReplyError as exc:
        return {"ok": False, "error": str(exc), "ai_suggested_reply": ""}


def normalize_ai_reply_style(reply: str, *, max_chars: int = _MAX_REPLY_CHARS) -> str:
    text = str(reply or "").strip()
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"([!?.,])\1{1,}", r"\1", text)
    text = text.replace(" ,", ",").replace(" .", ".")
    text = text.replace(" ;", ";").replace(" :", ":")
    text = re.sub(r"\s*([!?.,;:])\s*", r"\1 ", text).strip()

    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]
    if len(parts) > _MAX_SENTENCES:
        parts = parts[:_MAX_SENTENCES]
    dedup_parts: list[str] = []
    for p in parts:
        if not dedup_parts or p.lower() != dedup_parts[-1].lower():
            dedup_parts.append(p)
    text = " ".join(dedup_parts).strip()
    if not text:
        return ""
    text = text[0].upper() + text[1:] if len(text) > 1 else text.upper()
    if len(text) > int(max_chars):
        text = text[: int(max_chars)].rstrip(" ,;:")
    return text


def _contains_prohibited_style(text: str) -> bool:
    low = str(text or "").lower()
    if any(x in low for x in _PROHIBITED_STYLE_PHRASES):
        return True
    if low.count("!") > 1 or low.count("?") > 2:
        return True
    if len(re.findall(r"[😂🤣😊😍🔥💥🎉]", low)) >= 2:
        return True
    return False


def _build_step_variant(*, current_step: str, base_suggested_reply: str, history: list[str]) -> str:
    step_code = str(current_step or "").strip().upper()
    options = list(_STEP_VARIANTS.get(step_code) or [])
    if not options:
        return str(base_suggested_reply or "").strip()
    idx_seed = len([x for x in history if str(x or "").strip()]) + len(str(base_suggested_reply or ""))
    return options[idx_seed % len(options)]


def _avoid_threepeat(*, candidate_reply: str, current_step: str, base_suggested_reply: str, history: list[str]) -> str:
    norm_history = [str(x or "").strip().lower() for x in history if str(x or "").strip()]
    if len(norm_history) < 2:
        return candidate_reply
    candidate_norm = str(candidate_reply or "").strip().lower()
    if not candidate_norm:
        return candidate_reply
    if norm_history[-1] == candidate_norm and norm_history[-2] == candidate_norm:
        return _build_step_variant(
            current_step=current_step,
            base_suggested_reply=base_suggested_reply,
            history=history,
        )
    return candidate_reply



def validate_ai_reply(
    *,
    ai_suggested_reply: str,
    base_suggested_reply: str,
    current_step: str,
    candidate_message: str,
    requires_human: bool,
) -> tuple[bool, str]:
    text = str(ai_suggested_reply or "").strip()
    if not text:
        return False, "empty_ai_reply"
    if len(text) > _MAX_REPLY_CHARS:
        return False, "reply_too_long"
    if _contains_prohibited_style(text):
        return False, "unprofessional_tone"
    if len([p for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]) > _MAX_SENTENCES:
        return False, "too_many_sentences"

    low = text.lower()
    blocked_phrases = (
        "te conseguimos empleo",
        "estas aprobada",
        "estás aprobada",
        "ya estas inscrita",
        "ya estás inscrita",
    )
    if any(p in low for p in blocked_phrases):
        return False, "dangerous_promise"

    if "whatsapp" in low and ("enviado" in low or "te escribimos" in low):
        return False, "claims_whatsapp_sent"
    if "whatsapp" in low and any(token in low for token in ("envié", "envie", "te lo envie", "te lo envié")):
        return False, "claims_whatsapp_sent"

    base_low = str(base_suggested_reply or "").strip().lower()
    if "si o no" in base_low and "si o no" not in low:
        return False, "changed_binary_intent"

    if str(current_step or "").strip().upper() == "PERSONAL_CONFIRMATION" and ("si o no" not in low):
        return False, "out_of_step_request"

    if any(x in low for x in ("direccion", "dirección", "ciudad", "sector")) and str(current_step or "").strip().upper() == "PERSONAL_CONFIRMATION":
        return False, "out_of_step_request"

    if requires_human:
        if not any(k in low for k in ("revisión humana", "equipo", "asesor", "validación manual")):
            return False, "requires_human_notice_missing"

    candidate_low = str(candidate_message or "").lower()
    if any(k in low for k in ("tu cédula", "numero de cuenta", "tarjeta")) and all(
        token not in candidate_low for token in ("cedula", "cédula", "cuenta", "tarjeta")
    ):
        return False, "invented_sensitive_request"

    if any(k in low for k in ("mi amor", "mami", "manda eso rapido", "manda eso rápido", "apurate", "apúrate")):
        return False, "unprofessional_tone"
    if any(k in low for k in _STYLE_FORMAL_PHRASES):
        return False, "overly_formal_tone"

    name_match = re.search(r"\bgracias\s+([a-záéíóúñ]+)\b", low)
    if name_match:
        extracted_name = str(name_match.group(1) or "").strip()
        if extracted_name and extracted_name not in candidate_low:
            return False, "invented_candidate_data"
    if re.search(r"\btienes?\s+\d{1,2}\s+años\b", low) and not re.search(r"\b\d{1,2}\s*(años|ano|años)\b", candidate_low):
        return False, "invented_candidate_data"

    return True, "ok"



def get_practice_reply_with_ai_fallback(
    *,
    conversation: Any,
    base_suggested_reply: str,
    current_step: str,
    candidate_message: str,
    context: dict[str, Any] | None = None,
    requires_human: bool = False,
) -> dict[str, Any]:
    base_text = str(base_suggested_reply or "").strip()
    can_use, reason = can_use_practice_ai_reply(conversation)
    out = {
        "suggested_reply": base_text,
        "suggested_reply_source": "protocol",
        "base_suggested_reply": base_text,
        "ai_suggested_reply": "",
        "ai_reply_used": False,
        "ai_reply_safety_status": "disabled",
        "ai_reply_fallback_reason": reason,
        "fallback_used": True,
    }
    if not can_use:
        return out

    safe_context = dict(context or {})
    recent_suggestions = [str(x or "").strip() for x in list(safe_context.get("recent_bot_suggestions") or []) if str(x or "").strip()]

    ai_result = generate_practice_ai_reply(
        base_suggested_reply=base_text,
        current_step=current_step,
        candidate_message=candidate_message,
        context=safe_context,
        requires_human=requires_human,
    )
    ai_text_raw = str(ai_result.get("ai_suggested_reply") or "").strip()
    if len(ai_text_raw) > _MAX_REPLY_CHARS:
        out["ai_reply_safety_status"] = "fallback"
        out["ai_reply_fallback_reason"] = "reply_too_long"
        return out
    ai_text = normalize_ai_reply_style(ai_text_raw, max_chars=_MAX_REPLY_CHARS)
    ai_text = _avoid_threepeat(
        candidate_reply=ai_text,
        current_step=current_step,
        base_suggested_reply=base_text,
        history=recent_suggestions,
    )
    ai_text = normalize_ai_reply_style(ai_text, max_chars=_MAX_REPLY_CHARS)
    out["ai_suggested_reply"] = ai_text

    if not bool(ai_result.get("ok")):
        out["ai_reply_safety_status"] = "fallback"
        out["ai_reply_fallback_reason"] = str(ai_result.get("error") or "ai_generation_failed")
        return out

    valid, safety = validate_ai_reply(
        ai_suggested_reply=ai_text,
        base_suggested_reply=base_text,
        current_step=current_step,
        candidate_message=candidate_message,
        requires_human=requires_human,
    )
    if not valid:
        out["ai_reply_safety_status"] = "fallback"
        out["ai_reply_fallback_reason"] = safety
        return out

    out["suggested_reply"] = ai_text
    out["suggested_reply_source"] = "practice_ai"
    out["ai_reply_used"] = True
    out["ai_reply_safety_status"] = "ok"
    out["ai_reply_fallback_reason"] = ""
    out["fallback_used"] = False
    return out
