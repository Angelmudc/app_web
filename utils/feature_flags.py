from __future__ import annotations

from flask import current_app


FEATURE_NAMES = (
    "finalizar_proceso",
    "llamadas",
    "compat",
    "matching",
    "bot_candidatas_legacy",
    "candidatas_web",
    "candidatas_dashboard",
    "reemplazos_panel",
    "tareas_seguimiento",
    "solicitudes_bandeja",
    "solicitudes_prioridad",
)


def feature_enabled(name: str) -> bool:
    try:
        flags = current_app.config.get("FEATURE_FLAGS", {})
    except Exception:
        flags = {}
    return bool(flags.get(str(name), False))
