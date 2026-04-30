# -*- coding: utf-8 -*-
from __future__ import annotations

import re


def norm_email(value: str | None) -> str:
    return (value or "").strip().lower()


def only_digits(value: str | None) -> str:
    return re.sub(r"\D+", "", value or "")


def norm_phone_rd(value: str | None) -> str:
    """
    Canonical para telefonos RD:
    - deja solo digitos
    - si llega en 11 digitos con prefijo pais 1 + 809/829/849 -> reduce a 10
    - si no aplica RD, conserva hasta 15 (E.164 sin '+')
    """
    raw = only_digits(value)
    if not raw:
        return ""
    if len(raw) == 11 and raw.startswith("1") and raw[1:4] in {"809", "829", "849"}:
        return raw[1:]
    if len(raw) == 10 and raw[:3] in {"809", "829", "849"}:
        return raw
    return raw[:15]


def is_invalid_phone_placeholder(value: str | None) -> bool:
    raw = only_digits(value)
    if not raw:
        return True
    if len(raw) < 10:
        return True
    if len(raw) == 10 and raw in {"0000000000", "1111111111", "1234567890"}:
        return True
    if len(raw) == 10 and len(set(raw)) == 1:
        return True
    return False


def nullable_norm_email(value: str | None) -> str | None:
    out = norm_email(value)
    return out or None


def nullable_norm_phone_rd(value: str | None) -> str | None:
    if is_invalid_phone_placeholder(value):
        return None
    out = norm_phone_rd(value)
    return out or None
