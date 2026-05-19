"""Cliente seguro para WhatsApp Cloud API con feature flags."""

from __future__ import annotations

import os
from typing import Any

import requests
from services.bot_sandbox_service import SandboxSafetyError, assert_no_real_outbound_allowed, is_staging_offline_active
from services.bot_observability_service import log_bot_event


def _is_true(value: str | None, *, default: bool = False) -> bool:
    raw = (value or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def is_whatsapp_enabled() -> bool:
    return _is_true(os.getenv("WHATSAPP_ENABLED"), default=False)


def is_bot_dry_run() -> bool:
    return _is_true(os.getenv("BOT_DRY_RUN"), default=True)


def build_graph_url() -> str:
    base = (os.getenv("WHATSAPP_GRAPH_BASE_URL") or "https://graph.facebook.com").strip().rstrip("/")
    version = (os.getenv("WHATSAPP_API_VERSION") or "v23.0").strip()
    phone_number_id = (os.getenv("WHATSAPP_PHONE_NUMBER_ID") or "").strip()
    return f"{base}/{version}/{phone_number_id}/messages"


def _mask_token(token: str) -> str:
    raw = str(token or "").strip()
    if not raw:
        return ""
    return f"{raw[:8]}***"


def _masked_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_mask_token(access_token)}",
        "Content-Type": "application/json",
    }


def _classify_meta_error(http_status: int | None, body: dict[str, Any]) -> str:
    err = body.get("error") if isinstance(body, dict) else {}
    if not isinstance(err, dict):
        return "meta_delivery_failed"
    code = str(err.get("code") or "")
    subcode = str(err.get("error_subcode") or "")
    message = str(err.get("message") or "").lower()
    if http_status == 401 or code in {"190"}:
        return "invalid_token"
    if "recipient" in message and "allow" in message:
        return "sandbox_recipient_not_whitelisted"
    if "24" in message and ("window" in message or "hours" in message):
        return "outside_24h_window"
    if "phone number id" in message and ("invalid" in message or "does not exist" in message):
        return "invalid_phone_number_id"
    if "business" in message and ("account" in message and ("invalid" in message or "not" in message)):
        return "invalid_business_account"
    if "development mode" in message or subcode in {"2018278"}:
        return "app_in_development_block"
    if "recipient" in message and ("not valid" in message or "not found" in message):
        return "recipient_not_allowed"
    return "meta_delivery_failed"


def send_text_message(to_phone_e164: str, text: str, *, timeout_seconds: int = 8) -> dict[str, Any]:
    to_phone = (to_phone_e164 or "").strip()
    text_body = (text or "").strip()
    if not to_phone or not text_body:
        return {"ok": False, "status": "failed", "error_code": "invalid_input", "error_message": "to_phone/text requeridos"}

    if is_staging_offline_active():
        try:
            assert_no_real_outbound_allowed()
        except SandboxSafetyError as exc:
            return {"ok": False, "status": "blocked", "error_code": "sandbox_security_block", "error_message": str(exc)}

    if not is_whatsapp_enabled():
        return {"ok": False, "skipped": True, "status": "queued", "reason": "whatsapp_disabled", "http_status": None}
    if is_bot_dry_run():
        return {"ok": False, "skipped": True, "status": "queued", "reason": "dry_run", "http_status": None}

    access_token = (os.getenv("WHATSAPP_ACCESS_TOKEN") or "").strip()
    phone_number_id = (os.getenv("WHATSAPP_PHONE_NUMBER_ID") or "").strip()
    if not access_token or not phone_number_id:
        return {
            "ok": False,
            "status": "failed",
            "error_code": "misconfigured",
            "error_message": "Faltan credenciales WhatsApp",
            "http_status": None,
        }

    url = build_graph_url()
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone.lstrip("+"),
        "type": "text",
        "text": {"body": text_body},
    }
    log_bot_event(
        "meta_send_request_started",
        metadata={
            "endpoint": url,
            "phone_number_id": phone_number_id,
            "token_prefix_masked": _mask_token(access_token),
            "payload_masked": payload,
            "timeout": int(timeout_seconds),
            "headers_masked": _masked_headers(access_token),
        },
    )
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout_seconds)
    except requests.Timeout:
        log_bot_event("network_exception", level="warning", metadata={"exception": "Timeout", "endpoint": url})
        return {"ok": False, "status": "failed", "error_code": "timeout", "error_message": "Timeout al llamar Graph API", "http_status": None}
    except Exception as exc:
        log_bot_event(
            "network_exception",
            level="warning",
            metadata={"exception": f"{exc.__class__.__name__}", "endpoint": url},
        )
        return {
            "ok": False,
            "status": "failed",
            "error_code": "network_error",
            "error_message": f"{exc.__class__.__name__}",
            "http_status": None,
        }

    body = {}
    raw_body = ""
    try:
        raw_body = resp.text or ""
        body = resp.json() if resp.content else {}
    except Exception:
        body = {}
        raw_body = resp.text or ""

    log_bot_event(
        "meta_send_response",
        metadata={
            "http_status": int(resp.status_code),
            "raw_body": raw_body,
            "parsed_json": body,
            "wamid": (
                str(((body.get("messages") or [{}])[0] or {}).get("id") or "")
                if isinstance(body, dict) and isinstance(body.get("messages"), list) and body.get("messages")
                else ""
            ),
        },
    )

    if 200 <= int(resp.status_code) < 300:
        msg_id = None
        messages = body.get("messages") if isinstance(body, dict) else None
        if isinstance(messages, list) and messages:
            first = messages[0] if isinstance(messages[0], dict) else {}
            msg_id = str(first.get("id") or "").strip() or None
        return {
            "ok": True,
            "status": "sent",
            "wa_message_id": msg_id,
            "raw_response": body,
            "raw_response_text": raw_body,
            "http_status": int(resp.status_code),
        }

    error_node = body.get("error") if isinstance(body, dict) and isinstance(body.get("error"), dict) else {}
    error_code = str(error_node.get("code") or resp.status_code)
    error_message = str(error_node.get("message") or "Graph API error").strip()
    error_kind = _classify_meta_error(int(resp.status_code), body if isinstance(body, dict) else {})
    log_bot_event(error_kind, level="warning", metadata={"http_status": int(resp.status_code), "error_code": error_code, "message": error_message})
    return {
        "ok": False,
        "status": "failed",
        "error_code": error_code,
        "error_kind": error_kind,
        "error_message": error_message[:255],
        "raw_response": body,
        "raw_response_text": raw_body,
        "http_status": int(resp.status_code),
    }
