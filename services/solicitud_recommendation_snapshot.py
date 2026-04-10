# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
from typing import Any


MODEL_VERSION = "rec-v1"
POLICY_VERSION = "policy-v1"


_FINGERPRINT_FIELDS = (
    "estado",
    "tipo_servicio",
    "ciudad_sector",
    "rutas_cercanas",
    "modalidad_trabajo",
    "horario",
    "funciones",
    "funciones_otro",
    "edad_requerida",
    "experiencia",
    "mascota",
    "detalles_servicio",
    "estado_actual_desde",
    "fecha_ultima_modificacion",
)


def _json_default(value: Any):
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def build_solicitud_fingerprint(solicitud) -> str:
    payload = {
        "solicitud_id": int(getattr(solicitud, "id", 0) or 0),
        "cliente_id": int(getattr(solicitud, "cliente_id", 0) or 0),
    }
    for field in _FINGERPRINT_FIELDS:
        payload[field] = getattr(solicitud, field, None)

    raw = json.dumps(payload, ensure_ascii=True, sort_keys=True, default=_json_default)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def run_is_stale(*, run_fingerprint: str, solicitud) -> bool:
    expected = build_solicitud_fingerprint(solicitud)
    current = str(run_fingerprint or "").strip().lower()
    return not current or current != expected
