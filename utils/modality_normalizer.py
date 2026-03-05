# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Optional


_DORMIDA_SOLICITUD_PATTERNS = (
    r"\bdormida\b",
    r"\bcon\s+dormida\b",
    r"\bcon\s+dormir\b",
    r"\bdurmiendo\b",
    r"\bse\s+queda\b",
    r"\bse\s+queda\s+a\s+dormir\b",
    r"\binterna\b",
)

_LUNES_VIERNES_PATTERNS = (
    r"\blunes\s+a\s+viernes\b",
    r"\blunes\s+viernes\b",
    r"\blun\s+a\s+vie\b",
    r"\blun\s+vie\b",
    r"\bl\s+a\s+v\b",
    r"\bl\s+v\b",
)

_LUNES_SABADO_PATTERNS = (
    r"\blunes\s+a\s+sabado\b",
    r"\blunes\s+sabado\b",
    r"\blun\s+a\s+sab\b",
    r"\blun\s+sab\b",
    r"\bl\s+a\s+s\b",
    r"\bl\s+s\b",
)

_MODALIDAD_KEYWORDS = (
    "dormida",
    "dormir",
    "interna",
    "salida",
    "lunes",
    "martes",
    "miercoles",
    "jueves",
    "viernes",
    "sabado",
    "domingo",
    "fin",
    "semana",
    "dias",
)


def _normalize_free_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in text)
    return re.sub(r"\s+", " ", text).strip()


def is_gibberish(text: Any) -> bool:
    raw = str(text or "").strip().lower()
    if len(raw) < 8:
        return False

    norm = _normalize_free_text(raw)
    if not norm:
        return False

    non_alnum_count = sum(1 for ch in raw if not (ch.isalnum() or ch.isspace()))
    non_alnum_ratio = non_alnum_count / max(1, len(raw))

    letters = "".join(ch for ch in norm if "a" <= ch <= "z")
    if letters:
        consonant_runs = re.findall(r"[bcdfghjklmnpqrstvwxyz]{4,}", letters)
        run_chars = sum(len(run) for run in consonant_runs)
        consonant_run_ratio = run_chars / max(1, len(letters))
    else:
        consonant_run_ratio = 0.0

    has_known_keyword = any(kw in norm for kw in _MODALIDAD_KEYWORDS)

    return (
        consonant_run_ratio >= 0.45
        or non_alnum_ratio >= 0.35
        or not has_known_keyword
    )


def normalize_solicitud_modalidad(value: Any) -> tuple[Optional[str], str]:
    raw = str(value or "").strip()
    norm = _normalize_free_text(value)
    if not norm:
        return None, "sin texto de modalidad en solicitud"
    if is_gibberish(raw):
        return None, "gibberish"

    for pattern in _DORMIDA_SOLICITUD_PATTERNS:
        if re.search(pattern, norm):
            return "dormida", f"detectado dormida por patron '{pattern}'"

    if "salida diaria" in norm:
        return "salida_diaria", "detectado salida_diaria por texto 'salida diaria'"

    match_days = re.search(r"\b([123])\s*dias?\b", norm)
    if match_days:
        return "salida_diaria", f"detectado salida_diaria por patron '{match_days.group(0)}'"

    if re.search(r"\bdias?\s+a\s+la\s+semana\b", norm):
        return "salida_diaria", "detectado salida_diaria por patron 'dias a la semana'"

    for pattern in _LUNES_VIERNES_PATTERNS:
        if re.search(pattern, norm):
            return "salida_diaria", f"detectado salida_diaria por patron '{pattern}'"

    for pattern in _LUNES_SABADO_PATTERNS:
        if re.search(pattern, norm):
            return "salida_diaria", f"detectado salida_diaria por patron '{pattern}'"

    return None, "no se pudo inferir modalidad en solicitud"


def normalize_candidata_modalidad(value: Any) -> tuple[Optional[str], str]:
    norm = _normalize_free_text(value)
    if not norm:
        return None, "sin texto de modalidad en candidata"

    if re.search(r"\bsin\s+dormida\b", norm):
        return "salida_diaria", "detectado salida_diaria por patron 'sin dormida'"

    if re.search(r"\b(con\s+dormida|dormida|interna)\b", norm):
        return "dormida", "detectado dormida por patron 'con dormida/dormida/interna'"

    if "salida" in norm:
        return "salida_diaria", "detectado salida_diaria por palabra 'salida'"

    for pattern in _LUNES_VIERNES_PATTERNS + _LUNES_SABADO_PATTERNS:
        if re.search(pattern, norm):
            return "salida_diaria", f"detectado salida_diaria por patron '{pattern}'"

    return None, "no se pudo inferir modalidad en candidata"


def evaluate_modalidad_match(solicitud_value: Any, candidata_value: Any, *, max_points: int = 20) -> Dict[str, Any]:
    solicitud_raw = str(solicitud_value or "").strip() or None
    candidata_raw = str(candidata_value or "").strip() or None

    solicitud_norm, solicitud_reason = normalize_solicitud_modalidad(solicitud_value)
    candidata_norm, candidata_reason = normalize_candidata_modalidad(candidata_value)

    if solicitud_norm and candidata_norm:
        if solicitud_norm == candidata_norm:
            return {
                "solicitud_modalidad_raw": solicitud_raw,
                "solicitud_modalidad_norm": solicitud_norm,
                "candidata_modalidad_raw": candidata_raw,
                "candidata_modalidad_norm": candidata_norm,
                "modalidad_match": True,
                "modalidad_pts": max_points,
                "modalidad_reason": f"modalidad compatible: {solicitud_reason}",
            }
        return {
            "solicitud_modalidad_raw": solicitud_raw,
            "solicitud_modalidad_norm": solicitud_norm,
            "candidata_modalidad_raw": candidata_raw,
            "candidata_modalidad_norm": candidata_norm,
            "modalidad_match": False,
            "modalidad_pts": 0,
            "modalidad_reason": (
                "modalidad no compatible: "
                f"solicitud={solicitud_norm}, candidata={candidata_norm}"
            ),
        }

    missing_reason = solicitud_reason if not solicitud_norm else candidata_reason
    return {
        "solicitud_modalidad_raw": solicitud_raw,
        "solicitud_modalidad_norm": solicitud_norm,
        "candidata_modalidad_raw": candidata_raw,
        "candidata_modalidad_norm": candidata_norm,
        "modalidad_match": None,
        "modalidad_pts": 0,
        "modalidad_reason": f"modalidad no evaluable: {missing_reason}",
    }
