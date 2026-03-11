# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import unicodedata


_SPACE_RE = re.compile(r"\s+")
_LEAD_OTHER_RE = re.compile(r"^\s*otro\s*[:\-]?\s*", re.IGNORECASE)


def _clean_spaces(text: str) -> str:
    return _SPACE_RE.sub(" ", (text or "").strip())


def _strip_accents(text: str) -> str:
    base = unicodedata.normalize("NFD", text or "")
    return "".join(ch for ch in base if unicodedata.category(ch) != "Mn")


def _norm(text: str) -> str:
    lowered = _strip_accents(_clean_spaces(text)).lower()
    lowered = re.sub(r"[.,;:()]+", " ", lowered)
    return _SPACE_RE.sub(" ", lowered).strip()


def _detect_group(raw: str) -> str:
    n = _norm(raw)
    if not n:
        return ""
    if (
        n.startswith("con dormida")
        or n.startswith("dormida")
        or n.startswith("interna")
        or " con dormida" in n
        or "dormida" in n
    ):
        return "con_dormida"
    if n.startswith("con salida diaria") or n.startswith("salida diaria") or "salida diaria" in n:
        return "con_salida_diaria"
    return ""


def _group_label(group: str) -> str:
    if group == "con_dormida":
        return "Con dormida 💤"
    if group == "con_salida_diaria":
        return "Salida diaria"
    return ""


def _strip_group_prefix(text: str, group: str) -> str:
    raw = _clean_spaces(text)
    if not raw or not group:
        return raw
    if group == "con_dormida":
        return re.sub(r"^(?:con\s+dormida(?:\s*💤)?|dormida|interna)\s*-?\s*", "", raw, flags=re.IGNORECASE).strip()
    if group == "con_salida_diaria":
        return re.sub(r"^(?:con\s+)?salida\s+diaria\s*-?\s*", "", raw, flags=re.IGNORECASE).strip()
    return raw


def _sanitize_other_detail(detail: str, group: str) -> str:
    txt = _clean_spaces(detail)
    txt = _LEAD_OTHER_RE.sub("", txt).strip()
    txt = _strip_group_prefix(txt, group)
    txt_norm = _norm(txt)
    if txt_norm == "viernes a lunes":
        txt = "fin de semana"
    if group == "con_salida_diaria":
        txt = re.sub(r"^con\s+", "", txt, flags=re.IGNORECASE).strip()
    return _clean_spaces(txt)


def canonicalize_modalidad_trabajo(raw_value: str | None) -> str:
    """
    Normaliza modalidad para evitar redundancias de "Otro":
    - "Salida diaria otro: salida diaria con lunes a viernes"
      -> "Salida diaria lunes a viernes"
    - "Con dormida otro: con dormida quincenal"
      -> "Con dormida 💤 quincenal"

    Mantiene compatibilidad: valores sin patrón redundante se preservan.
    """
    raw = _clean_spaces(raw_value or "")
    if not raw:
        return ""

    group = _detect_group(raw)
    if not group:
        return raw

    raw_norm = _norm(raw)
    has_other_marker = bool(re.search(r"\botro\b", raw_norm))

    rest = _strip_group_prefix(raw, group)
    duplicated_group_in_rest = bool(_strip_group_prefix(rest, group) != rest)

    # Extrae detalle después de "otro" cuando existe.
    detail = rest
    if has_other_marker:
        m = re.search(r"(?i)\botro\b\s*[:\-]?\s*(.*)$", rest)
        if m:
            detail = (m.group(1) or "").strip()
        else:
            detail = _LEAD_OTHER_RE.sub("", rest).strip()

    detail = _sanitize_other_detail(detail, group)

    if has_other_marker or duplicated_group_in_rest:
        prefix = _group_label(group)
        return _clean_spaces(f"{prefix} {detail}".strip()) if detail else prefix

    if group == "con_dormida":
        dormida_detail = _sanitize_other_detail(_strip_group_prefix(raw, group), group)
        prefix = _group_label(group)
        return _clean_spaces(f"{prefix} {dormida_detail}".strip()) if dormida_detail else prefix
    if group == "con_salida_diaria":
        salida_detail = _sanitize_other_detail(_strip_group_prefix(raw, group), group)
        if _norm(salida_detail) == "fin de semana":
            return "Salida diaria - fin de semana"

    return raw
