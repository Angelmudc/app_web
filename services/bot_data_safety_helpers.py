from __future__ import annotations

import re
from typing import Any


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def first_non_empty(entities: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = entities.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def norm_text(value: Any) -> str | None:
    if value is None:
        return None
    txt = str(value).strip()
    return txt or None


def mask_cedula_like(value: Any) -> Any:
    if isinstance(value, str):
        return re.sub(r"\b(\d{3})[-\s]?(\d{7})[-\s]?(\d)\b", r"\1-2***-***", value)
    return value


def mask_sensitive_doc_fields(value: Any) -> Any:
    if isinstance(value, str):
        return mask_cedula_like(value)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            key = str(k)
            if any(tok in key.lower() for tok in ("cedula", "documento", "dni")):
                out[key] = "<redacted>"
            else:
                out[key] = mask_sensitive_doc_fields(v)
        return out
    if isinstance(value, list):
        return [mask_sensitive_doc_fields(v) for v in value]
    return value
