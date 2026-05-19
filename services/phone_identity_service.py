"""Normalizacion telefonica deterministica para identidad bot (Fase 3)."""

from __future__ import annotations

import re


_RD_AREA_CODES = {"809", "829", "849"}


def sanitize_phone(raw_phone: str | None) -> str:
    return re.sub(r"\D+", "", str(raw_phone or ""))


def validate_possible_phone(raw_phone: str | None) -> bool:
    digits = sanitize_phone(raw_phone)
    if len(digits) < 10 or len(digits) > 15:
        return False
    if len(set(digits)) == 1:
        return False
    return True


def normalize_phone_to_e164(raw_phone: str | None, default_country: str = "DO") -> str | None:
    digits = sanitize_phone(raw_phone)
    if not validate_possible_phone(digits):
        return None

    country = (default_country or "DO").strip().upper()
    if country == "DO":
        # RD local: 809/829/849 + 7 digitos.
        if len(digits) == 10 and digits[:3] in _RD_AREA_CODES:
            return f"+1{digits}"
        # RD con prefijo 1.
        if len(digits) == 11 and digits.startswith("1") and digits[1:4] in _RD_AREA_CODES:
            return f"+{digits}"
        return None

    # Fallback general: mantener digitos como E.164 con +.
    if 10 <= len(digits) <= 15:
        return f"+{digits}"
    return None
