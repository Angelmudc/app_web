# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Optional


def _normalize_free_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_solicitud_modalidad(value: Any) -> tuple[Optional[str], str]:
    norm = _normalize_free_text(value)
    if not norm:
        return None, "sin texto de modalidad en solicitud"

    if "dormida" in norm:
        return "dormida", "detectado dormida por palabra 'dormida'"

    if "salida diaria" in norm:
        return "salida_diaria", "detectado salida_diaria por texto 'salida diaria'"

    match_days = re.search(r"\b([123])\s*dias?\b", norm)
    if match_days:
        return "salida_diaria", f"detectado salida_diaria por patron '{match_days.group(0)}'"

    if re.search(r"\bdias?\s+a\s+la\s+semana\b", norm):
        return "salida_diaria", "detectado salida_diaria por patron 'dias a la semana'"

    if re.search(r"\blunes\s+a\s+viernes\b", norm):
        return "salida_diaria", "detectado salida_diaria por patron 'lunes a viernes'"

    if re.search(r"\blunes\s+a\s+sabado\b", norm):
        return "salida_diaria", "detectado salida_diaria por patron 'lunes a sabado'"

    return None, "no se pudo inferir modalidad en solicitud"


def normalize_candidata_modalidad(value: Any) -> tuple[Optional[str], str]:
    norm = _normalize_free_text(value)
    if not norm:
        return None, "sin texto de modalidad en candidata"

    if "dormida" in norm:
        return "dormida", "detectado dormida por palabra 'dormida'"

    if "salida" in norm:
        return "salida_diaria", "detectado salida_diaria por palabra 'salida'"

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
