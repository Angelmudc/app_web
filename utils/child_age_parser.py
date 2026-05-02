# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import unicodedata
from typing import List


def _normalize_text(value: str) -> str:
    txt = (value or "").strip().lower()
    if not txt:
        return ""
    txt = unicodedata.normalize("NFD", txt)
    txt = "".join(ch for ch in txt if unicodedata.category(ch) != "Mn")
    txt = re.sub(r"\s+", " ", txt)
    return txt


def extract_child_ages_from_text(raw_text: str) -> List[int]:
    txt = _normalize_text(raw_text)
    if not txt:
        return []

    ages = set()

    for match in re.finditer(r"\b(\d{1,2})\s*anos?\b", txt):
        ages.add(int(match.group(1)))

    for match in re.finditer(r"\banos?\b", txt):
        start = max(0, match.start() - 28)
        chunk = txt[start:match.start()]
        if not re.search(r"(,|\by\b)", chunk):
            continue
        for n in re.findall(r"\b(\d{1,2})\b", chunk):
            ages.add(int(n))

    return sorted(ages)


def has_child_age_five_or_less(raw_text: str) -> bool:
    ages = extract_child_ages_from_text(raw_text)
    return any(age <= 5 for age in ages)
