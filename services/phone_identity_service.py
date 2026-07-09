"""Normalizacion telefonica deterministica para identidad bot (Fase 3)."""

from __future__ import annotations

import re

def sanitize_phone(raw_phone: str | None) -> str:
    return re.sub(r"\D+", "", str(raw_phone or ""))


def validate_possible_phone(raw_phone: str | None) -> bool:
    digits = sanitize_phone(raw_phone)
    if len(digits) < 10 or len(digits) > 15:
        return False
    if len(set(digits)) == 1:
        return False
    return True


def _strip_international_prefix(raw_phone: str | None) -> tuple[str, str]:
    raw = str(raw_phone or "").strip()
    if not raw:
        return "", ""
    if raw.startswith("+"):
        return "+", sanitize_phone(raw)
    digits = sanitize_phone(raw)
    if digits.startswith("00"):
        return "00", digits[2:]
    return "", digits


def normalize_phone_to_e164(raw_phone: str | None, default_country: str = "DO") -> str | None:
    _ = (default_country or "DO").strip().upper()
    prefix, digits = _strip_international_prefix(raw_phone)
    if not digits:
        return None
    if len(set(digits)) == 1:
        return None
    if prefix == "00" and (len(digits) < 8 or len(digits) > 15):
        return None
    if prefix != "00" and not validate_possible_phone(digits):
        return None

    if prefix == "+":
        return f"+{digits}"

    if prefix == "00":
        return f"+{digits}"

    if len(digits) == 10:
        # Mantiene compatibilidad con RD y cubre NANP (RD/US/Caribe) sin forzar DO.
        return f"+1{digits}"

    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"

    if len(digits) > 10 and not digits.startswith("1"):
        return f"+{digits}"
    return None
