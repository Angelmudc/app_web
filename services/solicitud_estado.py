# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
from typing import Any

from utils.timezone import utc_now_naive


PRIORIDAD_BAND_ORDER: dict[str, int] = {
    "normal": 1,
    "atencion": 2,
    "urgente": 3,
    "critica": 4,
}


def _normalize_estado(value: Any) -> str:
    return str(value or "").strip().lower()


def set_solicitud_estado(
    solicitud,
    nuevo_estado: str,
    *,
    now_dt: datetime | None = None,
    reset_estado_actual_desde_on_same: bool = False,
) -> dict[str, Any]:
    """Transición centralizada de estado para Solicitud.

    Regla clave:
    - Solo resetea ``estado_actual_desde`` cuando hay transición real de estado.
    - Entra a ``activa``: reinicia seguimiento e incrementa ``veces_activada``.
    - Mantiene timestamps operativos de actividad/modificación.
    """
    now_value = now_dt or utc_now_naive()
    prev_estado = _normalize_estado(getattr(solicitud, "estado", ""))
    next_estado = _normalize_estado(nuevo_estado)
    changed = prev_estado != next_estado

    if changed:
        solicitud.estado = next_estado
        if hasattr(solicitud, "fecha_ultimo_estado"):
            solicitud.fecha_ultimo_estado = now_value
        if hasattr(solicitud, "estado_actual_desde"):
            solicitud.estado_actual_desde = now_value
    elif reset_estado_actual_desde_on_same and hasattr(solicitud, "estado_actual_desde"):
        solicitud.estado_actual_desde = now_value

    if changed and next_estado == "activa":
        if hasattr(solicitud, "fecha_inicio_seguimiento"):
            solicitud.fecha_inicio_seguimiento = now_value
        if hasattr(solicitud, "veces_activada"):
            solicitud.veces_activada = int(getattr(solicitud, "veces_activada", 0) or 0) + 1

    if hasattr(solicitud, "fecha_ultima_actividad"):
        solicitud.fecha_ultima_actividad = now_value
    if hasattr(solicitud, "fecha_ultima_modificacion"):
        solicitud.fecha_ultima_modificacion = now_value

    return {
        "from": prev_estado,
        "to": next_estado,
        "changed": changed,
        "at": now_value,
    }


def _active_reemplazo_inicio(solicitud) -> datetime | None:
    best = None
    for repl in (getattr(solicitud, "reemplazos", None) or []):
        inicio = getattr(repl, "fecha_inicio_reemplazo", None)
        fin = getattr(repl, "fecha_fin_reemplazo", None)
        if not inicio or fin is not None:
            continue
        if best is None or inicio > best:
            best = inicio
    return best


def resolve_solicitud_estado_priority_anchor(solicitud) -> tuple[datetime | None, str, bool]:
    """Fuente para prioridad operativa por estado real (activa/reemplazo)."""
    estado = _normalize_estado(getattr(solicitud, "estado", ""))

    if estado == "reemplazo":
        repl_inicio = _active_reemplazo_inicio(solicitud)
        if repl_inicio is not None:
            return repl_inicio, "reemplazo.fecha_inicio_reemplazo", False

    estado_desde = getattr(solicitud, "estado_actual_desde", None)
    if estado_desde is not None:
        return estado_desde, "solicitud.estado_actual_desde", False

    if estado == "activa":
        for attr, source in (
            ("fecha_inicio_seguimiento", "fallback.fecha_inicio_seguimiento"),
            ("fecha_ultima_modificacion", "fallback.fecha_ultima_modificacion"),
            ("fecha_solicitud", "fallback.fecha_solicitud"),
        ):
            val = getattr(solicitud, attr, None)
            if val is not None:
                return val, source, True
        return None, "sin_fuente", True

    if estado == "reemplazo":
        for attr, source in (
            ("fecha_ultima_modificacion", "fallback.fecha_ultima_modificacion"),
            ("fecha_solicitud", "fallback.fecha_solicitud"),
        ):
            val = getattr(solicitud, attr, None)
            if val is not None:
                return val, source, True
        return None, "sin_fuente", True

    return None, "estado_no_prioritario", True


def days_in_state(from_dt: datetime | None, *, now_dt: datetime | None = None) -> int | None:
    if from_dt is None:
        return None
    now_value = now_dt or utc_now_naive()
    return max(0, int((now_value - from_dt).total_seconds() // 86400))


def priority_band_for_days(days_in_current_state: int | None) -> str:
    days = int(days_in_current_state or 0)
    if days >= 15:
        return "critica"
    if days >= 10:
        return "urgente"
    if days >= 7:
        return "atencion"
    return "normal"


def priority_band_rank(band: str) -> int:
    return int(PRIORIDAD_BAND_ORDER.get(_normalize_estado(band), 0))


def priority_message_for_solicitud(*, estado: str, days_in_current_state: int) -> str:
    estado_norm = _normalize_estado(estado)
    days = max(0, int(days_in_current_state or 0))
    band = priority_band_for_days(days)

    if estado_norm == "reemplazo":
        if band == "critica":
            return f"Crítica: reemplazo abierto hace {days} días"
        if band == "urgente":
            return f"Urgente: reemplazo abierto hace {days} días"
        return f"Reemplazo pendiente desde hace {days} días"

    if band == "critica":
        return f"Crítica: lleva {days} días activa sin resolverse"
    if band == "urgente":
        return f"Urgente: lleva {days} días activa"
    if days <= 6:
        if days == 1:
            return "Activa hace 1 día"
        return f"Activa hace {days} días"
    return f"Lleva {days} días en estado activo"
