from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


ALLOWED_MEDIA_TYPES = {"audio", "image", "document"}


@dataclass
class NormalizedInbound:
    from_number: str
    display_name: str | None
    message_id: str
    timestamp: str | None
    message_type: str
    text_body: str
    media_id: str | None
    requires_human: bool
    raw_payload_hash: str
    duplicate_hint: bool
    raw_payload: dict[str, Any]


class PayloadNormalizationError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _stable_hash(payload: dict[str, Any]) -> str:
    stable = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def _is_sandbox_phone(value: str | None) -> bool:
    return str(value or "").strip().startswith("+1999")


def _to_sandbox_phone(raw: str | None) -> str:
    value = str(raw or "").strip()
    if value.startswith("+"):
        return value
    if value.isdigit():
        return f"+{value}"
    return value


def _placeholder_for_media(message_type: str) -> str:
    if message_type == "audio":
        return "[audio recibido - requiere transcripcion manual]"
    if message_type == "image":
        return "[imagen recibida - revision manual]"
    if message_type == "document":
        return "[documento recibido - revision manual]"
    return ""


def _normalize_simple(payload: dict[str, Any]) -> NormalizedInbound:
    message_id = str(payload.get("message_id") or "").strip()
    if not message_id:
        raise PayloadNormalizationError("missing_message_id")

    from_number = _to_sandbox_phone(payload.get("from"))
    if not _is_sandbox_phone(from_number):
        raise PayloadNormalizationError("sandbox_security_block:real_phone_detected")

    raw_type = str(payload.get("type") or "text").strip().lower()
    message_type = raw_type if raw_type in {"text"} | ALLOWED_MEDIA_TYPES else "text"
    media_id = str(payload.get("media_id") or "").strip() or None
    text_body = str(payload.get("message") or payload.get("text") or "").strip()
    requires_human = False
    if message_type in ALLOWED_MEDIA_TYPES:
        text_body = _placeholder_for_media(message_type)
        requires_human = True
    if message_type == "text" and not text_body:
        raise PayloadNormalizationError("empty_message")

    return NormalizedInbound(
        from_number=from_number,
        display_name=str(payload.get("name") or "").strip() or None,
        message_id=message_id,
        timestamp=str(payload.get("timestamp") or "").strip() or None,
        message_type=message_type,
        text_body=text_body,
        media_id=media_id,
        requires_human=requires_human,
        raw_payload_hash=_stable_hash(payload),
        duplicate_hint=False,
        raw_payload=payload,
    )


def _normalize_cloud(payload: dict[str, Any]) -> NormalizedInbound:
    entries = payload.get("entry")
    if not isinstance(entries, list) or not entries:
        raise PayloadNormalizationError("invalid_payload")
    first_entry = entries[0] if isinstance(entries[0], dict) else None
    if not isinstance(first_entry, dict):
        raise PayloadNormalizationError("invalid_payload")

    changes = first_entry.get("changes")
    if not isinstance(changes, list) or not changes:
        raise PayloadNormalizationError("invalid_payload")
    first_change = changes[0] if isinstance(changes[0], dict) else None
    value = (first_change or {}).get("value") if isinstance(first_change, dict) else None
    if not isinstance(value, dict):
        raise PayloadNormalizationError("invalid_payload")

    messages = value.get("messages")
    if not isinstance(messages, list) or not messages:
        raise PayloadNormalizationError("invalid_payload")
    msg = messages[0] if isinstance(messages[0], dict) else None
    if not isinstance(msg, dict):
        raise PayloadNormalizationError("invalid_payload")

    message_id = str(msg.get("id") or "").strip()
    if not message_id:
        raise PayloadNormalizationError("missing_message_id")

    from_number = _to_sandbox_phone(msg.get("from"))
    if not _is_sandbox_phone(from_number):
        raise PayloadNormalizationError("sandbox_security_block:real_phone_detected")

    contacts = value.get("contacts")
    display_name = None
    if isinstance(contacts, list) and contacts:
        c0 = contacts[0] if isinstance(contacts[0], dict) else {}
        profile = c0.get("profile") if isinstance(c0.get("profile"), dict) else {}
        display_name = str(profile.get("name") or "").strip() or None

    message_type = str(msg.get("type") or "text").strip().lower() or "text"
    if message_type not in {"text"} | ALLOWED_MEDIA_TYPES:
        raise PayloadNormalizationError("unsupported_message_type")

    text_body = ""
    media_id = None
    requires_human = False
    if message_type == "text":
        text_node = msg.get("text") if isinstance(msg.get("text"), dict) else {}
        text_body = str(text_node.get("body") or "").strip()
        if not text_body:
            raise PayloadNormalizationError("empty_message")
    else:
        media_node = msg.get(message_type) if isinstance(msg.get(message_type), dict) else {}
        media_id = str(media_node.get("id") or "").strip() or None
        text_body = _placeholder_for_media(message_type)
        requires_human = True

    return NormalizedInbound(
        from_number=from_number,
        display_name=display_name,
        message_id=message_id,
        timestamp=str(msg.get("timestamp") or "").strip() or None,
        message_type=message_type,
        text_body=text_body,
        media_id=media_id,
        requires_human=requires_human,
        raw_payload_hash=_stable_hash(payload),
        duplicate_hint=False,
        raw_payload=payload,
    )


def normalize_sandbox_webhook_payload(payload: dict[str, Any] | None) -> NormalizedInbound:
    if not isinstance(payload, dict):
        raise PayloadNormalizationError("invalid_payload")

    is_cloud = str(payload.get("object") or "").strip() == "whatsapp_business_account"
    normalized = _normalize_cloud(payload) if is_cloud else _normalize_simple(payload)
    return normalized
