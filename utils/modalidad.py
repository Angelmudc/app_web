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
_OTHER_LABEL_BY_GROUP = {
    "con_dormida": "Con dormida 💤 otro",
    "con_salida_diaria": "Salida diaria otro",
}
_MODALIDAD_SPECS = {
    "con_salida_diaria": [
        {
            "label": "Salida diaria - 1 día a la semana",
            "aliases": ["1 día a la semana", "Un día a la semana"],
        },
        {
            "label": "Salida diaria - 2 días a la semana",
            "aliases": ["2 días a la semana", "Dos días a la semana"],
        },
        {
            "label": "Salida diaria - 3 días a la semana",
            "aliases": ["3 días a la semana", "Tres días a la semana"],
        },
        {
            "label": "Salida diaria - 4 días a la semana",
            "aliases": ["4 días a la semana", "Cuatro días a la semana"],
        },
        {
            "label": "Salida diaria - lunes a viernes",
            "aliases": ["Lunes a Viernes", "Salida diaria lunes a viernes"],
        },
        {
            "label": "Salida diaria - lunes a sábado",
            "aliases": ["Lunes a Sábado"],
        },
        {
            "label": "Salida diaria - fin de semana",
            "aliases": ["Sábado y Domingo", "Viernes a Lunes", "Fin de semana"],
        },
        {
            "label": "Salida diaria otro",
            "aliases": ["Otro", "Salida diaria - Otro"],
        },
    ],
    "con_dormida": [
        {
            "label": "Con dormida 💤 lunes a viernes",
            "aliases": ["Lunes a Viernes", "Con dormida - Lunes a Viernes"],
        },
        {
            "label": "Con dormida 💤 lunes a sábado",
            "aliases": ["Lunes a sábado", "Lunes a sábado, sale sábado después del medio día"],
        },
        {
            "label": "Con dormida 💤 quincenal",
            "aliases": [
                "Quincenal",
                "Con dormida quincenal",
                "Con dormida 💤 quincenal",
                "Salida Quincenal, sale viernes después del medio día",
            ],
        },
        {
            "label": "Con dormida 💤 fin de semana",
            "aliases": ["Sábado y Domingo", "Viernes a Lunes"],
        },
        {
            "label": "Con dormida 💤 otro",
            "aliases": ["Otro", "Con dormida - Otro"],
        },
    ],
}


def _clean_spaces(text: str) -> str:
    return _SPACE_RE.sub(" ", (text or "").strip())


def _strip_accents(text: str) -> str:
    base = unicodedata.normalize("NFD", text or "")
    return "".join(ch for ch in base if unicodedata.category(ch) != "Mn")


def _norm(text: str) -> str:
    lowered = _strip_accents(_clean_spaces(text)).lower()
    lowered = lowered.replace("💤", " ")
    lowered = re.sub(r"[\-–—_/]+", " ", lowered)
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
        return re.sub(r"^(?:con\s+dormida(?:\s*💤)?|dormida|interna)\s*[\-–—]?\s*", "", raw, flags=re.IGNORECASE).strip()
    if group == "con_salida_diaria":
        return re.sub(r"^(?:con\s+)?salida\s+diaria\s*[\-–—]?\s*", "", raw, flags=re.IGNORECASE).strip()
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


def split_modalidad_for_ui(raw_value: str | None) -> dict[str, str]:
    """
    Devuelve una descomposición estable para precargar UI guiada:
    {group, specific, other}
    """
    raw = _clean_spaces(raw_value or "")
    if not raw:
        return {"group": "", "specific": "", "other": ""}

    opt_map: dict[str, list[tuple[str, str]]] = {}
    for group, specs in _MODALIDAD_SPECS.items():
        for spec in specs:
            label = _clean_spaces(spec.get("label") or "")
            if not label:
                continue
            opt_map.setdefault(_norm(label), []).append((group, label))
            for alias in spec.get("aliases") or []:
                a = _clean_spaces(alias or "")
                if a:
                    opt_map.setdefault(_norm(a), []).append((group, label))

    def _resolve_hit(key_norm: str, prefer_group: str, strict_prefer: bool = False) -> tuple[str, str] | None:
        candidates = opt_map.get(key_norm) or []
        if not candidates:
            return None
        if prefer_group:
            for g, lbl in candidates:
                if g == prefer_group:
                    return (g, lbl)
            if strict_prefer:
                return None
        return candidates[0]

    raw_norm = _norm(raw)
    group = _detect_group(raw)
    rest = _strip_group_prefix(raw, group) if group else raw
    rest_norm = _norm(rest)

    # Match directo por valor completo o por resto sin prefijo de grupo.
    hit = (
        _resolve_hit(rest_norm, group, strict_prefer=True)
        or _resolve_hit(raw_norm, group, strict_prefer=True)
        or _resolve_hit(rest_norm, group)
        or _resolve_hit(raw_norm, group)
    )
    if hit:
        g, label = hit
        if _norm(label) in {_norm(v) for v in _OTHER_LABEL_BY_GROUP.values()}:
            other = _sanitize_other_detail(rest if group == g else raw, g)
            return {"group": g, "specific": label, "other": other}
        return {"group": g, "specific": label, "other": ""}

    resolved_group = group or _detect_group(raw)
    if not resolved_group:
        return {"group": "", "specific": "", "other": ""}

    detail = _sanitize_other_detail(rest, resolved_group)
    if not detail:
        detail = _sanitize_other_detail(raw, resolved_group)

    # Si solo era etiqueta de grupo, dejamos específica vacía.
    if not detail or _norm(detail) == _norm(_group_label(resolved_group)):
        return {"group": resolved_group, "specific": "", "other": ""}

    return {
        "group": resolved_group,
        "specific": _OTHER_LABEL_BY_GROUP.get(resolved_group, ""),
        "other": detail,
    }
