"""Reglas de aplicabilidad para la composicion del hogar de una solicitud."""

GENERAL_HOUSEHOLD_FUNCIONES = frozenset({"limpieza", "cocinar", "lavar", "planchar"})


def normalized_funciones(values) -> set[str]:
    if values is None:
        return set()
    if isinstance(values, str):
        values = [values]
    try:
        items = values
    except Exception:
        items = []
    return {str(value or "").strip().lower() for value in items if str(value or "").strip()}


def solicitud_composition_flags(funciones) -> dict[str, bool]:
    selected = normalized_funciones(funciones)
    has_general = bool(selected & GENERAL_HOUSEHOLD_FUNCIONES)
    return {
        "has_general_household": has_general,
        "show_house": has_general,
        "show_adultos": has_general or "envejeciente" in selected,
        "show_ninos": "ninos" in selected,
        "show_child_help": "ninos" in selected and has_general,
        "show_envejeciente": "envejeciente" in selected,
    }
