# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Any

from flask import Request


def clean_spaces(raw: str | None, *, max_len: int = 255) -> str:
    return " ".join((raw or "").strip().split())[:max_len]


def digits_only(raw: str | None) -> str:
    return re.sub(r"\D+", "", raw or "")


def has_min_real_chars(name: str | None, *, min_chars: int = 6) -> bool:
    letters = re.sub(r"[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ]", "", name or "")
    return len(letters) >= int(min_chars)


def normalize_phone_for_store(raw: str | None) -> str:
    return digits_only(raw)[:15]


def get_request_ip(request: Request) -> str:
    xff = (request.headers.get("X-Forwarded-For") or "").strip()
    if xff:
        return xff.split(",")[0].strip()[:64]
    return (request.remote_addr or "").strip()[:64] or "0.0.0.0"


def hit_rate_limit(
    *,
    cache: Any,
    scope: str,
    actor: str,
    limit: int,
    window_seconds: int,
) -> bool:
    key = f"public_intake:{scope}:{actor}"
    try:
        current = int(cache.get(key) or 0) + 1
        cache.set(key, current, timeout=max(1, int(window_seconds)))
        return current > int(limit)
    except Exception:
        return False
