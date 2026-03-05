# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Optional


def cedula_digits(raw: str) -> str:
    return re.sub(r"\D+", "", raw or "")


def format_cedula(digits11: str) -> str:
    d = digits11 or ""
    return f"{d[:3]}-{d[3:10]}-{d[10:]}"


def normalize_cedula_for_store(raw: str) -> Optional[str]:
    digits = cedula_digits(raw)
    if not digits:
        return None
    if len(digits) == 11:
        return format_cedula(digits)
    return (raw or "").strip()


def normalize_cedula_for_compare(raw: str) -> str:
    return cedula_digits(raw)
