from __future__ import annotations

from flask import current_app


FEATURE_NAMES = (
    "finalizar_proceso",
    "llamadas",
    "compat",
    "matching",
    "candidatas_web",
)


def feature_enabled(name: str) -> bool:
    try:
        flags = current_app.config.get("FEATURE_FLAGS", {})
    except Exception:
        flags = {}
    return bool(flags.get(str(name), False))
