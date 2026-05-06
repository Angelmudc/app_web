# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import unicodedata
from typing import Any, List


def _normalize_text(value: str) -> str:
    txt = (value or "").strip().lower()
    if not txt:
        return ""
    txt = unicodedata.normalize("NFD", txt)
    txt = "".join(ch for ch in txt if unicodedata.category(ch) != "Mn")
    txt = re.sub(r"\s+", " ", txt)
    return txt


def extract_child_ages_from_text(raw_text: str) -> List[int]:
    summary = parse_child_age_summary(raw_text)
    return sorted(summary["ages_years"])


def parse_child_age_summary(raw_text: str) -> dict[str, Any]:
    txt = _normalize_text(raw_text)
    if not txt:
        return {
            "ages_years": [],
            "small_count": 0,
            "big_count": 0,
            "teen_count": 0,
            "adult_count": 0,
            "total_children": 0,
        }

    ages: list[int] = []
    consumed: list[tuple[int, int]] = []

    def _is_consumed(start: int, end: int) -> bool:
        for c_start, c_end in consumed:
            if start < c_end and end > c_start:
                return True
        return False

    # "1 ano y 5 meses" es un solo nino de 1 ano.
    for match in re.finditer(r"\b(\d{1,2})\s*anos?\s*(?:,|\by\b)?\s*(\d{1,2})\s*mes(?:es)?\b", txt):
        years = int(match.group(1))
        months = int(match.group(2))
        if 0 <= years <= 17 and 0 <= months <= 11:
            ages.append(years)
            consumed.append((match.start(), match.end()))

    # "5 meses" tambien cuenta como nino pequeno.
    for match in re.finditer(r"\b(\d{1,2})\s*mes(?:es)?\b", txt):
        if _is_consumed(match.start(), match.end()):
            continue
        months = int(match.group(1))
        if 0 <= months <= 11:
            ages.append(0)
            consumed.append((match.start(), match.end()))

    # Edades explicitas en anos.
    for match in re.finditer(r"\b(\d{1,2})\s*anos?\b", txt):
        if _is_consumed(match.start(), match.end()):
            continue
        years = int(match.group(1))
        if 0 <= years <= 99:
            ages.append(years)
            consumed.append((match.start(), match.end()))

    # Numeros sueltos: "2", "2 y 4", "2, 7 y 14", "2/4".
    # Heuristica anti-falsos-positivos para texto largo: solo si es corto
    # o si hay contexto claro de ninos/edad.
    has_age_context = bool(re.search(r"\b(nin(?:o|a)s?|hij(?:o|a)s?|edad(?:es)?|ano?s?|mes(?:es)?|bebe)\b", txt))
    if not ages:
        compact = txt.strip()
        if len(compact) <= 40 or has_age_context:
            if re.fullmatch(r"[\d\s,./y]+", compact):
                for num in re.findall(r"\b(\d{1,2})\b", compact):
                    ages.append(int(num))

    small_count = sum(1 for age in ages if 0 <= age <= 5)
    big_count = sum(1 for age in ages if 6 <= age <= 12)
    teen_count = sum(1 for age in ages if 13 <= age <= 17)
    adult_count = sum(1 for age in ages if age >= 18)
    total_children = small_count + big_count + teen_count

    return {
        "ages_years": ages,
        "small_count": small_count,
        "big_count": big_count,
        "teen_count": teen_count,
        "adult_count": adult_count,
        "total_children": total_children,
    }


def has_child_age_five_or_less(raw_text: str) -> bool:
    summary = parse_child_age_summary(raw_text)
    return summary["small_count"] > 0
