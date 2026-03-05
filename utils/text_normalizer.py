# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import unicodedata
from typing import List, Optional, Set

_ALIAS_REPLACEMENTS = {
    r"\bstgo\b": "santiago",
    r"\bsto\s*dgo\b": "santo domingo",
    r"\bsd\b": "santo domingo",
    r"\bav\.?\b": "avenida",
    r"\bave\.?\b": "avenida",
    r"\bc/\b": "calle",
}

_STOPWORDS = {
    "de",
    "la",
    "el",
    "los",
    "las",
    "avenida",
    "calle",
    "km",
    "cerca",
    "proximo",
    "frente",
    "detras",
}

_LOCATION_STOPWORDS = _STOPWORDS | {
    "av",
    "res",
    "urb",
    "sector",
    "carretera",
}

_SKILL_SYNONYMS = {
    "ninos": "cuidar_ninos",
    "ninas": "cuidar_ninos",
    "nino": "cuidar_ninos",
    "nina": "cuidar_ninos",
    "niñera": "cuidar_ninos",
    "ninera": "cuidar_ninos",
    "cuidar ninos": "cuidar_ninos",
    "cuidar niños": "cuidar_ninos",
    "ancianos": "cuidar_envejecientes",
    "envejecientes": "cuidar_envejecientes",
    "cuidar ancianos": "cuidar_envejecientes",
    "cuidado de ancianos": "cuidar_envejecientes",
    "limpieza": "limpieza",
    "limpiar": "limpieza",
    "cocina": "cocina",
    "cocinar": "cocina",
    "lavar": "lavado",
    "lavado": "lavado",
    "planchar": "planchar",
    "plachar": "planchar",
    "enfermeria": "enfermeria",
    "enfermería": "enfermeria",
}
_SKILL_ALL = {"limpieza", "cocina", "cuidar_ninos", "cuidar_envejecientes", "enfermeria"}

_CITY_PATTERNS = {
    "santiago": ["santiago"],
    "puerto plata": ["puerto plata", "puertoplata"],
    "moca": ["moca"],
    "la vega": ["la vega", "lavega"],
    "santo domingo": ["santo domingo", "sto dgo", "sd"],
    "san francisco de macoris": ["san francisco de macoris", "san francisco"],
}


def _strip_accents(txt: str) -> str:
    value = unicodedata.normalize("NFKD", txt or "")
    return "".join(ch for ch in value if not unicodedata.combining(ch))


def normalize_text(s) -> str:
    txt = _strip_accents(str(s or "").strip().lower())
    for patt, repl in _ALIAS_REPLACEMENTS.items():
        txt = re.sub(patt, repl, txt)
    txt = re.sub(r"[^a-z0-9\s]", " ", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def tokens(s) -> Set[str]:
    norm = normalize_text(s)
    if not norm:
        return set()
    out = []
    for tk in norm.split(" "):
        if not tk or tk in _STOPWORDS:
            continue
        out.append(tk)
    return set(out)


def location_tokens(s) -> Set[str]:
    norm = normalize_text(s)
    if not norm:
        return set()
    out = set()
    for tk in norm.split(" "):
        if not tk or tk in _LOCATION_STOPWORDS:
            continue
        out.add(tk)
    return out


def _split_skill_chunks(value) -> List[str]:
    txt = str(value or "").strip().replace("_", " ")
    if not txt:
        return []
    out = []
    for raw in re.split(r"[\n,;/\-\u2022]+", txt):
        norm = normalize_text(raw)
        if norm:
            out.append(norm)
    return out


def skill_tokens(value) -> Set[str]:
    out: Set[str] = set()
    chunks = _split_skill_chunks(value)
    for chunk in chunks:
        if "todas las anteriores" in chunk:
            out |= _SKILL_ALL
            continue

        mapped_direct = _SKILL_SYNONYMS.get(chunk)
        if mapped_direct:
            out.add(mapped_direct)
            continue

        # Match de sinonimos por presencia parcial
        for raw, canonical in _SKILL_SYNONYMS.items():
            raw_norm = normalize_text(raw)
            if raw_norm and raw_norm in chunk:
                out.add(canonical)
                break

    # Fallback por tokens sueltos cuando no hubo match de frases
    if not out:
        for tk in tokens(value):
            mapped = _SKILL_SYNONYMS.get(tk)
            if mapped:
                out.add(mapped)
    return out


def infer_city(text) -> Optional[str]:
    norm = normalize_text(text)
    if not norm:
        return None

    for city, variants in _CITY_PATTERNS.items():
        for v in variants:
            if normalize_text(v) in norm:
                return city
    return None
