# -*- coding: utf-8 -*-

import re


_FUNCTION_LABEL_MAP = {
    "cuidar ninos": "Cuidar niños",
    "ninos": "Cuidar niños",
    "niños": "Cuidar niños",
    "cuidar envejecientes": "Cuidar envejecientes",
    "envejeciente": "Cuidar envejecientes",
    "envejecientes": "Cuidar envejecientes",
    "limpiar": "Limpieza",
    "limpieza": "Limpieza",
    "cocinar": "Cocina",
    "cocina": "Cocina",
    "lavar": "Lavado",
    "lavado": "Lavado",
    "planchar": "Planchar",
    "plachar": "Planchar",
}

_ORDER_INDEX = {
    "Limpieza": 1,
    "Cocina": 2,
    "Lavado": 3,
    "Planchar": 4,
    "Cuidar niños": 5,
    "Cuidar envejecientes": 6,
}


def _split_values(values):
    if values is None:
        return []
    if isinstance(values, str):
        return [x.strip() for x in values.split(",") if x and x.strip()]

    out = []
    try:
        iterable = list(values)
    except Exception:
        iterable = [values]

    for val in iterable:
        if val is None:
            continue
        if isinstance(val, str):
            out.extend([x.strip() for x in val.split(",") if x and x.strip()])
        else:
            txt = str(val).strip()
            if txt:
                out.append(txt)
    return out


def _normalize_text(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return ""

    k = s.lower()
    if k in {"otro", "otro...", "otro…"}:
        return ""

    s = s.replace("_", " ")
    s = re.sub(r"\s+", " ", s).strip().lower()
    if not s:
        return ""

    if s in _FUNCTION_LABEL_MAP:
        return _FUNCTION_LABEL_MAP[s]

    # Capitaliza solo primera letra para mantener estilo natural
    return s[:1].upper() + s[1:]


def format_funciones(funciones, extra_text=None) -> str:
    """
    Recibe funciones como string CSV o lista y devuelve texto humano.
    También incorpora `extra_text` (funciones_otro) si llega.
    """
    raw_items = _split_values(funciones)
    raw_items.extend(_split_values(extra_text))

    out = []
    seen = set()
    for item in raw_items:
        fmt = _normalize_text(item)
        if not fmt:
            continue
        k = fmt.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(fmt)

    out.sort(key=lambda x: (_ORDER_INDEX.get(x, 999), x.lower()))
    return ", ".join(out)


def format_funciones_display(values, extra_text=None) -> str:
    """Alias de compatibilidad."""
    return format_funciones(values, extra_text=extra_text)
