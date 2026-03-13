# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import unicodedata


_SPACE_RE = re.compile(r"\s+")
_LEAD_OTHER_RE = re.compile(r"^\s*otro\s*[:\-]?\s*", re.IGNORECASE)
_GROUP_LABELS = {
    "con_dormida": "Con dormida 💤",
    "con_salida_diaria": "Salida diaria",
}


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
    return _GROUP_LABELS.get(group, "")


def _group_label_values() -> set[str]:
    return {v for v in _GROUP_LABELS.values() if v}


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


def should_preserve_existing_modalidad_on_edit(
    existing_value: str | None,
    submitted_value: str | None,
    submitted_group: str | None = None,
    submitted_specific: str | None = None,
    submitted_other: str | None = None,
) -> bool:
    """
    Evita pérdida silenciosa en edición cuando la UI guiada reenvía un valor
    degradado (vacío o solo etiqueta de grupo) y la solicitud ya tenía
    modalidad específica guardada.
    """
    existing = _clean_spaces(existing_value or "")
    submitted = _clean_spaces(submitted_value or "")
    if not existing:
        return False
    if not submitted:
        return True

    group = (submitted_group or "").strip()
    specific = _clean_spaces(submitted_specific or "")
    other = _clean_spaces(submitted_other or "")

    # Si el usuario envió una selección específica o texto "otro", no preservar.
    if specific or other:
        return False

    submitted_norm = _norm(submitted)
    existing_norm = _norm(existing)
    group_labels_norm = {_norm(lbl) for lbl in _group_label_values()}

    # Solo proteger cuando lo enviado es una etiqueta de grupo "pura".
    if submitted_norm not in group_labels_norm:
        return False

    # Si lo existente ya era exactamente el grupo, no hay nada que preservar.
    if existing_norm == submitted_norm:
        return False

    # Si en el POST no viene grupo/específica, también consideramos degradación.
    if not group and not specific:
        return True

    return True
