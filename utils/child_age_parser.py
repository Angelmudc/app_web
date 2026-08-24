# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import unicodedata
from typing import Any, List


_NUMBER_WORDS = {
    "un": 1,
    "una": 1,
    "uno": 1,
    "dos": 2,
    "tres": 3,
}


_CARE_WEIGHTS = {
    "intensive_baby": 1.0,
    "intensive_toddler": 0.9,
    "active_small_child": 0.6,
    "light_small_child": 0.3,
    "supervision_moderate": 0.15,
    "supervision_light": 0.05,
    "supervision_minimal": 0.0,
}

CHILD_CARE_HELP_CHOICES = {
    "sin_ayuda": 1.00,
    "con_ayuda": 0.30,
}

_LEGACY_CHILD_CARE_HELP_ALIASES = {
    "ayuda_ocasional": "con_ayuda",
    "ayuda_quehaceres": "con_ayuda",
    "ayuda_parcial": "con_ayuda",
    "ayuda_mayor": "con_ayuda",
    "solo_supervision": "con_ayuda",
    "otro": "con_ayuda",
}


def _normalize_text(value: str) -> str:
    txt = (value or "").strip().lower()
    if not txt:
        return ""
    txt = unicodedata.normalize("NFD", txt)
    txt = "".join(ch for ch in txt if unicodedata.category(ch) != "Mn")
    txt = txt.replace(";", ",")
    txt = re.sub(r"\s+", " ", txt)
    return txt


def _to_int(value: Any, default: int | None = None) -> int | None:
    try:
        if value is None or str(value).strip() == "":
            return default
        return int(float(value))
    except Exception:
        return default


def _age_payload(total_months: int | None, source_text: str, *, category_hint: str | None = None) -> dict[str, Any]:
    if total_months is None:
        return {
            "years": None,
            "months": None,
            "total_months": None,
            "source_text": source_text.strip(),
            "category_hint": category_hint,
        }
    total_months = max(0, int(total_months))
    return {
        "years": total_months // 12,
        "months": total_months % 12,
        "total_months": total_months,
        "source_text": source_text.strip(),
    }


def _care_category(total_months: int | None, category_hint: str | None = None) -> str | None:
    if total_months is None:
        if category_hint == "small":
            return "active_small_child"
        if category_hint == "older":
            return "supervision_light"
        return None
    if total_months < 12:
        return "intensive_baby"
    if total_months < 36:
        return "intensive_toddler"
    if total_months < 60:
        return "active_small_child"
    if total_months < 72:
        return "light_small_child"
    if total_months < 108:
        return "supervision_moderate"
    if total_months < 156:
        return "supervision_light"
    if total_months < 216:
        return "supervision_minimal"
    return None


def _aggregate_child_care_load(categories: list[str]) -> float:
    weights = sorted((_CARE_WEIGHTS.get(category, 0.0) for category in categories), reverse=True)
    total = 0.0
    for idx, weight in enumerate(weights):
        total += weight * (0.75 ** idx)
    return round(total, 3)


def normalize_child_care_help(value: Any) -> str:
    code = _normalize_text(str(value or "")).replace(" ", "_")
    code = _LEGACY_CHILD_CARE_HELP_ALIASES.get(code, code)
    return code if code in CHILD_CARE_HELP_CHOICES else "sin_ayuda"


def child_care_help_factor(value: Any, age_summary: dict[str, Any] | None = None) -> dict[str, Any]:
    code = normalize_child_care_help(value)
    factor = float(CHILD_CARE_HELP_CHOICES.get(code, 1.0))
    summary = age_summary or {}
    warnings: list[str] = []
    small_count = int(summary.get("small_child_count") or summary.get("small_count") or 0)
    if small_count <= 0:
        return {
            "code": code,
            "factor": 1.0,
            "warnings": [],
        }
    if code == "con_ayuda":
        supervision_count = int(summary.get("supervision_count") or 0)
        if small_count >= 2:
            factor = 0.35 + min(0.05, max(0, small_count - 2) * 0.025)
        elif supervision_count > 0:
            factor = 0.28
        else:
            factor = 0.22
    min_factor = 0.0
    if int(summary.get("baby_count") or 0) > 0:
        min_factor = max(min_factor, 0.30)
    if int(summary.get("toddler_count") or 0) > 0:
        min_factor = max(min_factor, 0.22)
    if min_factor and factor < min_factor:
        factor = min_factor
        warnings.append("La ayuda no elimina completamente la carga de bebés o niños de 1 a 2 años.")
    return {
        "code": code,
        "factor": round(float(factor), 2),
        "warnings": warnings,
    }


def _parse_count_token(raw: str) -> int | None:
    token = _normalize_text(raw)
    if token in _NUMBER_WORDS:
        return _NUMBER_WORDS[token]
    return _to_int(token)


def _parse_age_fragment(raw: str) -> dict[str, Any] | None:
    txt = _normalize_text(raw)
    if not txt:
        return None
    if re.search(r"\brecien nacido\b|\bneonato\b", txt):
        return _age_payload(0, raw)
    if re.search(r"\bano y medio\b|\bano\s+1/2\b", txt):
        return _age_payload(18, raw)

    match = re.search(
        r"\b(\d{1,2})\s*(?:anos?|a)\s*(?:,|\by\b)?\s*(\d{1,2})\s*(?:mes(?:es)?|m)\b",
        txt,
    )
    if match:
        years = int(match.group(1))
        months = int(match.group(2))
        if 0 <= years <= 17 and 0 <= months <= 35:
            return _age_payload((years * 12) + months, raw)

    match = re.search(r"\b(\d{1,2})\s*(?:mes(?:es)?|m)\b", txt)
    if match:
        months = int(match.group(1))
        if 0 <= months <= 35:
            return _age_payload(months, raw)

    match = re.search(r"\b(\d{1,2})\s*(?:anos?|a)\b", txt)
    if match:
        years = int(match.group(1))
        if 0 <= years <= 17:
            return _age_payload(years * 12, raw)

    match = re.fullmatch(r"\D*(\d{1,2})\D*", txt)
    if match:
        years = int(match.group(1))
        if 0 <= years <= 17:
            return _age_payload(years * 12, raw)
    return None


def extract_child_ages_from_text(raw_text: str) -> List[int]:
    summary = parse_child_age_summary(raw_text)
    return sorted(summary["ages_years"])


def parse_child_age_summary(raw_text: str, declared_count: Any = None) -> dict[str, Any]:
    txt = _normalize_text(raw_text)
    declared = _to_int(declared_count)
    warnings: list[str] = []
    children: list[dict[str, Any]] = []
    consumed: list[tuple[int, int]] = []

    def _empty() -> dict[str, Any]:
        unknown = max(int(declared or 0), 0) if declared else 0
        confidence = "medium" if declared and unknown else "low"
        return {
            "children": [],
            "ages_years": [],
            "small_count": 0,
            "big_count": 0,
            "teen_count": 0,
            "adult_count": 0,
            "total_children": 0,
            "baby_count": 0,
            "toddler_count": 0,
            "active_small_child_count": 0,
            "light_small_child_count": 0,
            "small_child_count": 0,
            "moderate_supervision_count": 0,
            "light_supervision_count": 0,
            "minimal_supervision_count": 0,
            "supervision_count": 0,
            "child_care_load": 0.0,
            "declared_count": declared,
            "parsed_count": 0,
            "unknown_count": unknown,
            "confidence": confidence,
            "warnings": ["Falta una edad para completar la cantidad declarada"] if unknown else [],
        }

    if not txt:
        return _empty()

    def _is_consumed(start: int, end: int) -> bool:
        return any(start < c_end and end > c_start for c_start, c_end in consumed)

    def _add(child: dict[str, Any] | None, start: int | None = None, end: int | None = None) -> None:
        if child is None:
            return
        children.append(child)
        if start is not None and end is not None:
            consumed.append((start, end))

    def _add_many(child: dict[str, Any] | None, count: int, start: int, end: int) -> None:
        if child is None or count <= 0:
            return
        for _ in range(count):
            children.append(dict(child))
        consumed.append((start, end))

    # Multiplicidad explicita: gemelos/mellizos/trillizos o "dos ninos de 5 anos".
    for match in re.finditer(
        r"\b(gemelos|mellizos|trillizos|dos|tres|2|3)\s*(?:ninos?|ninas?|hijos?|hijas?)?\s*(?:de|:)?\s*"
        r"((?:recien nacido)|(?:ano y medio)|(?:\d{1,2}\s*(?:anos?|a)\s*(?:,|\by\b)?\s*\d{1,2}\s*(?:mes(?:es)?|m))|(?:\d{1,2}\s*(?:mes(?:es)?|m))|(?:\d{1,2}\s*(?:anos?|a)))",
        txt,
    ):
        if _is_consumed(match.start(), match.end()):
            continue
        token = match.group(1)
        count = 2 if token in {"gemelos", "mellizos"} else 3 if token == "trillizos" else int(_parse_count_token(token) or 0)
        _add_many(_parse_age_fragment(match.group(2)), count, match.start(), match.end())

    # Rangos: se registran como una edad aproximada, no como dos ninos.
    for match in re.finditer(r"\b(?:entre|de)\s+(\d{1,2})\s+(?:y|a)\s+(\d{1,2})\s*anos?\b", txt):
        if _is_consumed(match.start(), match.end()):
            continue
        first = int(match.group(1))
        second = int(match.group(2))
        approx = min(first, second)
        if 0 <= approx <= 17:
            _add(_age_payload(approx * 12, match.group(0)), match.start(), match.end())
            warnings.append("Edad expresada como rango; se tomó como aproximada")

    compound_patterns = [
        r"\b(\d{1,2})\s*(?:anos?|a)\s*(?:,|\by\b)?\s*(\d{1,2})\s*(?:mes(?:es)?|m)\b",
        r"\bano y medio\b",
    ]
    for pattern in compound_patterns:
        for match in re.finditer(pattern, txt):
            if _is_consumed(match.start(), match.end()):
                continue
            _add(_parse_age_fragment(match.group(0)), match.start(), match.end())

    # Listas con unidad al final: "7, 9, 10 anos", "4/6/9", "4-6-9 anos".
    list_pattern = r"(?<!entre\s)(?<!de\s)\b(\d{1,2}(?:\s*(?:,|/|-|\by\b)\s*\d{1,2})+)\s*((?:anos?|a)?)\b"
    for match in re.finditer(list_pattern, txt):
        if _is_consumed(match.start(), match.end()):
            continue
        before = txt[max(0, match.start() - 10):match.start()]
        after = txt[match.end():match.end() + 10]
        if re.search(r"(?:entre|de)\s*$", before) or re.match(r"\s*(?:anos?)?\s*(?:a|hasta)\b", after):
            continue
        nums = [int(num) for num in re.findall(r"\d{1,2}", match.group(1))]
        if len(nums) >= 2 and all(0 <= num <= 17 for num in nums):
            unit = match.group(2).strip()
            for idx, num in enumerate(nums):
                source = f"{num} {unit}" if unit and idx == len(nums) - 1 else str(num)
                children.append(_age_payload(num * 12, source))
            consumed.append((match.start(), match.end()))

    # "uno de 3 y otro de 7".
    for match in re.finditer(r"\buno\s+de\s+(\d{1,2})\s+y\s+otro\s+de\s+(\d{1,2})\b", txt):
        if _is_consumed(match.start(), match.end()):
            continue
        for group in (1, 2):
            years = int(match.group(group))
            if 0 <= years <= 17:
                children.append(_age_payload(years * 12, match.group(group)))
        consumed.append((match.start(), match.end()))

    for match in re.finditer(r"\b(\d{1,2})\s*(?:mes(?:es)?|m)\b", txt):
        if _is_consumed(match.start(), match.end()):
            continue
        months = int(match.group(1))
        if 0 <= months <= 35:
            _add(_age_payload(months, match.group(0)), match.start(), match.end())

    for match in re.finditer(r"\b(\d{1,2})\s*(?:anos?|a)\b", txt):
        if _is_consumed(match.start(), match.end()):
            continue
        years = int(match.group(1))
        if 0 <= years <= 17:
            _add(_age_payload(years * 12, match.group(0)), match.start(), match.end())
        elif years >= 18:
            _add({"years": years, "months": 0, "total_months": years * 12, "source_text": match.group(0)}, match.start(), match.end())

    # Numeros sueltos solo cuando el campo parece ser una lista de edades o trae contexto claro.
    if not children:
        compact = txt.strip()
        has_age_context = bool(re.search(r"\b(nin(?:o|a)s?|hij(?:o|a)s?|edad(?:es)?|bebe|adolescente)\b", txt))
        numeric_list = re.fullmatch(r"[\d\s,./y-]+", compact)
        if numeric_list or has_age_context:
            nums = [int(num) for num in re.findall(r"\b(\d{1,2})\b", compact)]
            if has_age_context and len(nums) > 1:
                child_count_prefix = re.match(r"^\s*(\d{1,2})\s*nin(?:o|a)s?\b", compact)
                if child_count_prefix:
                    nums = nums[1:]
            for years in nums:
                if 0 <= years <= 17:
                    children.append(_age_payload(years * 12, str(years)))
                elif years >= 18:
                    children.append({"years": years, "months": 0, "total_months": years * 12, "source_text": str(years)})

    if re.search(r"\brecien nacido\b", txt) and not children:
        children.append(_age_payload(0, "recien nacido"))
    if re.search(r"\bbebe\b", txt) and not children:
        children.append(_age_payload(None, "bebe", category_hint="small"))
    if re.search(r"\badolescente\b", txt) and not children:
        children.append(_age_payload(None, "adolescente", category_hint="older"))
    if re.search(r"\bninos?\s+grandes\b|\bindependientes\b|\bsolo supervision\b|\bse atienden solos\b", txt) and not children:
        children.append(_age_payload(None, raw_text, category_hint="older"))

    parsed_count = len(children)
    unknown_count = 0
    if declared is not None and declared >= 0:
        unknown_count = max(declared - parsed_count, 0)
        if unknown_count:
            warnings.append("Falta una edad para completar la cantidad declarada")
        elif parsed_count > declared:
            warnings.append("Se detectaron mas edades que la cantidad declarada")

    known_months = [child["total_months"] for child in children if child.get("total_months") is not None]
    adult_count = sum(1 for months in known_months if months >= 18 * 12)
    child_months = [months for months in known_months if 0 <= months < 18 * 12]
    hint_small = sum(1 for child in children if child.get("total_months") is None and child.get("category_hint") == "small")
    hint_older = sum(1 for child in children if child.get("total_months") is None and child.get("category_hint") == "older")
    small_count = sum(1 for months in child_months if months < 72) + hint_small
    big_count = sum(1 for months in child_months if 72 <= months <= 12 * 12) + hint_older
    teen_count = sum(1 for months in child_months if 13 * 12 <= months <= 17 * 12 + 11)
    total_children = small_count + big_count + teen_count
    ages_years = [int(child["years"]) for child in children if child.get("years") is not None]
    categories = [
        category
        for child in children
        for category in [_care_category(child.get("total_months"), child.get("category_hint"))]
        if category
    ]
    baby_count = categories.count("intensive_baby")
    toddler_count = categories.count("intensive_toddler")
    active_small_child_count = categories.count("active_small_child")
    light_small_child_count = categories.count("light_small_child")
    moderate_supervision_count = categories.count("supervision_moderate")
    light_supervision_count = categories.count("supervision_light")
    minimal_supervision_count = categories.count("supervision_minimal")
    supervision_count = moderate_supervision_count + light_supervision_count + minimal_supervision_count

    if parsed_count == 0:
        confidence = "low"
    elif declared is None:
        confidence = "medium"
    elif parsed_count == declared:
        confidence = "high"
    else:
        confidence = "medium"

    return {
        "children": children,
        "ages_years": ages_years,
        "small_count": small_count,
        "big_count": big_count,
        "teen_count": teen_count,
        "adult_count": adult_count,
        "total_children": total_children,
        "baby_count": baby_count,
        "toddler_count": toddler_count,
        "active_small_child_count": active_small_child_count,
        "light_small_child_count": light_small_child_count,
        "small_child_count": baby_count + toddler_count + active_small_child_count + light_small_child_count,
        "moderate_supervision_count": moderate_supervision_count,
        "light_supervision_count": light_supervision_count,
        "minimal_supervision_count": minimal_supervision_count,
        "supervision_count": supervision_count,
        "child_care_load": _aggregate_child_care_load(categories),
        "declared_count": declared,
        "parsed_count": parsed_count,
        "unknown_count": unknown_count,
        "confidence": confidence,
        "warnings": warnings,
    }


def has_child_age_five_or_less(raw_text: str) -> bool:
    summary = parse_child_age_summary(raw_text)
    return summary["small_count"] > 0
