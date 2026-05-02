from __future__ import annotations

from typing import Any

PISOS_VALIDOS = {"1", "2", "3+"}
PISOS_MARKER = "Pisos reportados: 3+."


def _to_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_pisos_value(raw_value: Any, *, default_value: str = "1") -> str:
    value = _to_text(raw_value)
    if value in PISOS_VALIDOS:
        return value
    return default_value if default_value in PISOS_VALIDOS else "1"


def read_pisos_value(*, dos_pisos: Any, detalles_servicio: Any, nota_cliente: Any) -> str:
    default_value = "2" if bool(dos_pisos) else "1"
    pisos = default_value

    if isinstance(detalles_servicio, dict):
        pisos_stored = detalles_servicio.get("cantidad_pisos")
        pisos = normalize_pisos_value(pisos_stored, default_value=pisos)

    if pisos != "3+" and PISOS_MARKER in _to_text(nota_cliente):
        pisos = "3+"

    return pisos


def apply_pisos_to_solicitud(solicitud: Any, *, pisos_raw: Any, default_value: str = "1") -> str:
    current = getattr(solicitud, "detalles_servicio", None)
    details = dict(current) if isinstance(current, dict) else {}
    default_from_s = "2" if bool(getattr(solicitud, "dos_pisos", False)) else default_value
    pisos = normalize_pisos_value(pisos_raw, default_value=default_from_s)

    if hasattr(solicitud, "dos_pisos"):
        solicitud.dos_pisos = pisos in {"2", "3+"}

    details["cantidad_pisos"] = pisos
    if hasattr(solicitud, "detalles_servicio"):
        solicitud.detalles_servicio = details or None

    return pisos
