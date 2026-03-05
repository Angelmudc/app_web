# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_rule_text(value: Any) -> str:
    txt = _as_text(value)
    if not txt:
        return ""

    # Eliminar wrappers frecuentes: {"..."}, {'...'}, [...]
    txt = txt.strip()
    txt = txt.replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")
    txt = re.sub(r"^[\{\[\(]+", "", txt)
    txt = re.sub(r"[\}\]\)]+$", "", txt)
    txt = txt.strip().strip('"').strip("'").strip()

    txt = txt.lower()
    txt = txt.replace("años", "anos")
    txt = txt.replace("año", "ano")
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def _split_possible_rules(raw: Any) -> List[str]:
    txt = _normalize_rule_text(raw)
    if not txt:
        return []

    # Casos: '{"25 a 45 anos"}', '{31-45}', '"Mayor de 45"'
    # Si viene con varias entradas CSV en un string, separa suavemente.
    parts = [p.strip() for p in re.split(r"\s*,\s*", txt) if p.strip()]
    if not parts:
        return []
    return parts


@dataclass(frozen=True)
class AgeRule:
    min_age: Optional[int]
    max_age: Optional[int]
    raw: str

    def matches(self, age: Optional[int]) -> bool:
        if age is None:
            return False
        if self.min_age is not None and age < self.min_age:
            return False
        if self.max_age is not None and age > self.max_age:
            return False
        return True

    def label(self) -> str:
        if self.min_age is not None and self.max_age is not None:
            return f"{self.min_age}-{self.max_age}"
        if self.min_age is not None and self.max_age is None:
            return f">={self.min_age}"
        if self.min_age is None and self.max_age is not None:
            return f"<={self.max_age}"
        return self.raw


def parse_candidata_age_int(edad_text: str) -> Optional[int]:
    txt = _normalize_rule_text(edad_text)
    if not txt:
        return None

    nums = [int(x) for x in re.findall(r"\d{1,2}", txt)]
    if not nums:
        return None

    # Rango: "de 30 a 35", "30-35"
    has_range = bool(re.search(r"\bde\s*\d{1,2}\s*a\s*\d{1,2}\b", txt) or re.search(r"\d{1,2}\s*-\s*\d{1,2}", txt))
    if has_range and len(nums) >= 2:
        lo, hi = min(nums), max(nums)
        return int(round((lo + hi) / 2.0))

    return nums[0]


def _parse_rule_one(raw_rule: str) -> Optional[AgeRule]:
    txt = _normalize_rule_text(raw_rule)
    if not txt:
        return None

    # 1) Rango: "26 - 45 anos", "25 a 45 anos", "de 45 a 50 anos", "31-45"
    m_range = re.search(r"(?:de\s*)?(\d{1,2})\s*(?:-|a)\s*(\d{1,2})", txt)
    if m_range:
        a = int(m_range.group(1))
        b = int(m_range.group(2))
        lo, hi = min(a, b), max(a, b)
        return AgeRule(min_age=lo, max_age=hi, raw=txt)

    # 2) En adelante: "25 en adelante", "25 anos en adelante"
    m_forward = re.search(r"(\d{1,2})\s*(?:anos\s*)?en\s+adelante", txt)
    if m_forward:
        lo = int(m_forward.group(1))
        return AgeRule(min_age=lo, max_age=None, raw=txt)

    # 3) Mayor de: "mayor de 45", "mayor de 35 anos"
    m_mayor = re.search(r"mayor\s+de\s+(\d{1,2})", txt)
    if m_mayor:
        n = int(m_mayor.group(1))
        return AgeRule(min_age=n + 1, max_age=None, raw=txt)

    # 4) Plus: "45+"
    m_plus = re.search(r"(\d{1,2})\s*\+", txt)
    if m_plus:
        n = int(m_plus.group(1))
        return AgeRule(min_age=n, max_age=None, raw=txt)

    return None


def parse_solicitud_age_rules(edad_requerida_list: Sequence[Any], otro_text: Optional[str] = None) -> List[AgeRule]:
    raws: List[str] = []

    for item in list(edad_requerida_list or []):
        raws.extend(_split_possible_rules(item))

    if otro_text:
        raws.extend(_split_possible_rules(otro_text))

    rules: List[AgeRule] = []
    seen = set()
    for raw in raws:
        rule = _parse_rule_one(raw)
        if not rule:
            continue
        key = (rule.min_age, rule.max_age, rule.raw)
        if key in seen:
            continue
        seen.add(key)
        rules.append(rule)

    return rules
