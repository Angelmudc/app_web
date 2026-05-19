"""Seguridad para webhook de WhatsApp Cloud API."""

from __future__ import annotations

import hashlib
import hmac


def safe_compare(left: str, right: str) -> bool:
    return hmac.compare_digest(str(left or ""), str(right or ""))


def verify_webhook_token(mode: str | None, token: str | None, challenge: str | None, verify_token: str | None) -> tuple[bool, str]:
    normalized_mode = (mode or "").strip()
    normalized_token = (token or "").strip()
    expected_token = (verify_token or "").strip()
    challenge_text = str(challenge or "")
    if normalized_mode != "subscribe":
        return False, ""
    if not expected_token or not safe_compare(normalized_token, expected_token):
        return False, ""
    return True, challenge_text


def validate_whatsapp_signature(raw_body: bytes, signature_header: str | None, app_secret: str | None) -> bool:
    secret = (app_secret or "").strip()
    provided = (signature_header or "").strip()
    if not secret or not provided.startswith("sha256="):
        return False
    expected_hex = hmac.new(secret.encode("utf-8"), raw_body or b"", hashlib.sha256).hexdigest()
    provided_hex = provided.split("=", 1)[1].strip()
    return safe_compare(provided_hex, expected_hex)
