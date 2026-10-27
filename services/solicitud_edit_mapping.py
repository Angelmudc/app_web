"""Pure form-to-domain value mappings shared by Admin edit adapters.

These helpers deliberately do not read Flask's request, mutate ORM objects,
or perform persistence. Their behavior is kept compatible with the legacy
Admin mappers while making the transformations independently testable.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any


def only_digits(value: Any) -> str:
    return re.sub(r"\D+", "", value or "")


def normalize_numeric_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return only_digits(text) or None


def clean_list(seq: Any) -> list[str]:
    bad = {"-", "–", "—"}
    out: list[str] = []
    seen: set[str] = set()
    for value in (seq or []):
        text = str(value).strip()
        if not text or text in bad or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def choices_maps(choices: Any) -> tuple[dict[str, str], dict[str, str]]:
    code_to_label: dict[str, str] = {}
    label_to_code: dict[str, str] = {}
    for code, label in (choices or []):
        code_text, label_text = str(code).strip(), str(label).strip()
        if code_text and label_text:
            code_to_label[code_text] = label_text
            label_to_code[label_text] = code_text
    return code_to_label, label_to_code


def allowed_codes_from_choices(choices: Any) -> set[str]:
    try:
        return {str(value).strip() for value, _label in (choices or []) if str(value).strip()}
    except Exception:
        return set()


def map_age_choices(codes_selected: Any, edad_choices: Any, otro_text: Any) -> list[str]:
    codes = clean_list([str(value) for value in (codes_selected or [])])
    code_to_label, _label_to_code = choices_maps(edad_choices)
    result = [code_to_label[code] for code in codes if code != "otro" and code in code_to_label]
    if "otro" in codes:
        result.extend(part.strip() for part in (otro_text or "").split(",") if part.strip())
    return clean_list(result)


def split_age_for_form(stored_list: Any, edad_choices: Any) -> tuple[list[str], str]:
    stored = clean_list(stored_list)
    _code_to_label, label_to_code = choices_maps(edad_choices)
    selected: list[str] = []
    others: list[str] = []
    for label in stored:
        code = label_to_code.get(label)
        if code:
            selected.append(code)
        else:
            others.append(label)
    if others:
        selected = clean_list(selected + ["otro"])
    return selected, ", ".join(others) if others else ""


def map_tipo_lugar(value: Any, extra: Any) -> str:
    text = (value or "").strip()
    return (extra or "").strip() or text if text == "otro" else text


def normalize_banos_value(raw_value: Any) -> float | None:
    if raw_value is None:
        return None
    text = str(raw_value).strip()
    if not text:
        return None
    try:
        number = Decimal(text.replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if number < 0:
        return None
    return float(number)


def normalize_areas_comunes_selected(selected_vals: Any, choices: Any) -> list[str]:
    values = clean_list(selected_vals)
    allowed = allowed_codes_from_choices(choices)
    values = [value for value in values if value in allowed]
    if "todas_anteriores" in values:
        all_codes = [
            str(code).strip() for code, _label in (choices or [])
            if str(code).strip() and str(code).strip() not in {"todas_anteriores", "otro"}
        ]
        values = clean_list([value for value in values if value != "todas_anteriores"] + all_codes)
    return [value for value in values if value not in {"todas_anteriores", "otro"}]
