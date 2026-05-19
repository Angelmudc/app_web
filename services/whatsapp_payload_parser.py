"""Parser tolerante para payloads de webhook WhatsApp Cloud API."""

from __future__ import annotations

from datetime import datetime


def _as_int_epoch(value) -> int | None:
    try:
        return int(str(value).strip())
    except Exception:
        return None


def _normalize_phone(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if raw.startswith("+"):
        return raw
    if raw.isdigit():
        return f"+{raw}"
    return raw


def _extract_contact_name_by_wa_id(value: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    contacts = value.get("contacts") if isinstance(value, dict) else None
    if not isinstance(contacts, list):
        return out
    for contact in contacts:
        if not isinstance(contact, dict):
            continue
        wa_id = _normalize_phone(contact.get("wa_id"))
        if not wa_id:
            continue
        profile = contact.get("profile") if isinstance(contact.get("profile"), dict) else {}
        out[wa_id] = str(profile.get("name") or "").strip()
    return out


def parse_webhook_payload(payload: dict | None) -> dict:
    parsed = {"messages": [], "statuses": [], "errors": []}
    if not isinstance(payload, dict):
        parsed["errors"].append("payload_not_dict")
        return parsed

    try:
        entries = payload.get("entry")
        if not isinstance(entries, list):
            return parsed
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            changes = entry.get("changes")
            if not isinstance(changes, list):
                continue
            for change in changes:
                if not isinstance(change, dict):
                    continue
                value = change.get("value")
                if not isinstance(value, dict):
                    continue

                contact_names = _extract_contact_name_by_wa_id(value)
                metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
                phone_number_id = str(metadata.get("phone_number_id") or "").strip() or None

                messages = value.get("messages")
                if isinstance(messages, list):
                    for msg in messages:
                        if not isinstance(msg, dict):
                            continue
                        msg_type = str(msg.get("type") or "unknown").strip().lower()
                        from_phone = _normalize_phone(msg.get("from"))
                        wa_message_id = str(msg.get("id") or "").strip() or None
                        ts_epoch = _as_int_epoch(msg.get("timestamp"))
                        text_body = None
                        media = {}
                        if msg_type == "text":
                            text = msg.get("text") if isinstance(msg.get("text"), dict) else {}
                            text_body = str(text.get("body") or "").strip() or None
                        elif msg_type in {"image", "audio", "document"}:
                            node = msg.get(msg_type) if isinstance(msg.get(msg_type), dict) else {}
                            media = {
                                "id": str(node.get("id") or "").strip() or None,
                                "mime_type": str(node.get("mime_type") or "").strip() or None,
                                "sha256": str(node.get("sha256") or "").strip() or None,
                                "filename": str(node.get("filename") or "").strip() or None,
                                "caption": str(node.get("caption") or "").strip() or None,
                            }
                        parsed["messages"].append(
                            {
                                "wa_message_id": wa_message_id,
                                "from_phone_e164": from_phone,
                                "profile_name": contact_names.get(from_phone) or None,
                                "message_type": msg_type,
                                "text_body": text_body,
                                "media": media,
                                "timestamp_epoch": ts_epoch,
                                "phone_number_id": phone_number_id,
                                "raw_message": msg,
                            }
                        )

                statuses = value.get("statuses")
                if isinstance(statuses, list):
                    for status_row in statuses:
                        if not isinstance(status_row, dict):
                            continue
                        status = str(status_row.get("status") or "").strip().lower() or "unknown"
                        parsed["statuses"].append(
                            {
                                "wa_message_id": str(status_row.get("id") or "").strip() or None,
                                "status": status,
                                "recipient_phone_e164": _normalize_phone(status_row.get("recipient_id")),
                                "timestamp_epoch": _as_int_epoch(status_row.get("timestamp")),
                                "conversation_id": (
                                    str((status_row.get("conversation") or {}).get("id") or "").strip() or None
                                    if isinstance(status_row.get("conversation"), dict)
                                    else None
                                ),
                                "error_code": None,
                                "error_message": None,
                                "raw_status": status_row,
                            }
                        )
                        errors = status_row.get("errors")
                        if isinstance(errors, list) and errors:
                            first = errors[0] if isinstance(errors[0], dict) else {}
                            parsed["statuses"][-1]["error_code"] = str(first.get("code") or "").strip() or None
                            parsed["statuses"][-1]["error_message"] = str(first.get("title") or first.get("message") or "").strip() or None
    except Exception as exc:
        parsed["errors"].append(f"unexpected_parser_error:{exc.__class__.__name__}")
    return parsed


def epoch_to_datetime_utc(epoch_seconds: int | None):
    if not epoch_seconds:
        return None
    try:
        return datetime.utcfromtimestamp(int(epoch_seconds))
    except Exception:
        return None
