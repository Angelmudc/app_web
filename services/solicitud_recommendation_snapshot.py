# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
from typing import Any

from utils.candidata_readiness import candidata_docs_complete, candidata_referencias_complete
from utils.guards import candidata_esta_descalificada

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


def _to_bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def build_solicitud_fingerprint(solicitud) -> str:
    payload = {
        "solicitud_id": int(getattr(solicitud, "id", 0) or 0),
        "cliente_id": int(getattr(solicitud, "cliente_id", 0) or 0),
    }
    for field in _FINGERPRINT_FIELDS:
        payload[field] = getattr(solicitud, field, None)

    raw = json.dumps(payload, ensure_ascii=True, sort_keys=True, default=_json_default)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def candidate_guard_payload(candidata, *, has_interview: bool | None = None) -> dict[str, Any]:
    docs = candidata_docs_complete(candidata)
    refs = candidata_referencias_complete(candidata)
    required_flags = dict(docs.get("flags") or {})
    payload = {
        "candidata_id": int(getattr(candidata, "fila", 0) or 0),
        "estado": str(getattr(candidata, "estado", "") or "").strip().lower(),
        "codigo_present": bool(str(getattr(candidata, "codigo", "") or "").strip()),
        "descalificada": bool(candidata_esta_descalificada(candidata)),
        "modalidad_trabajo_preferida": str(getattr(candidata, "modalidad_trabajo_preferida", "") or "").strip().lower(),
        "edad": str(getattr(candidata, "edad", "") or "").strip().lower(),
        "trabaja_con_ninos": _to_bool_or_none(getattr(candidata, "trabaja_con_ninos", None)),
        "trabaja_con_mascotas": _to_bool_or_none(getattr(candidata, "trabaja_con_mascotas", None)),
        "puede_dormir_fuera": _to_bool_or_none(getattr(candidata, "puede_dormir_fuera", None)),
        "referencias_laboral_ok": bool(refs.get("referencias_laboral")),
        "referencias_familiares_ok": bool(refs.get("referencias_familiares")),
        "doc_depuracion": bool(required_flags.get("depuracion")),
        "doc_perfil": bool(required_flags.get("perfil")),
        "doc_cedula1": bool(required_flags.get("cedula1")),
        "doc_cedula2": bool(required_flags.get("cedula2")),
    }
    if has_interview is None:
        payload["has_interview"] = bool((getattr(candidata, "entrevista", None) or "").strip())
    else:
        payload["has_interview"] = bool(has_interview)
    return payload


def build_candidate_guard_from_payload(payload: dict[str, Any] | None) -> str:
    raw = json.dumps(dict(payload or {}), ensure_ascii=True, sort_keys=True, default=_json_default)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_candidate_guard(candidata, *, has_interview: bool | None = None) -> str:
    payload = candidate_guard_payload(candidata, has_interview=has_interview)
    return build_candidate_guard_from_payload(payload)


def build_pool_guard_hash(guard_by_candidata: dict[int, str] | None) -> str:
    rows: list[str] = []
    for cand_id, guard in sorted((guard_by_candidata or {}).items(), key=lambda x: int(x[0])):
        cid = int(cand_id or 0)
        g = str(guard or "").strip().lower()
        if cid <= 0 or not g:
            continue
        rows.append(f"{cid}:{g}")
    raw = json.dumps(rows, ensure_ascii=True, sort_keys=False, default=_json_default)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def run_is_stale(*, run_fingerprint: str, solicitud) -> bool:
    expected = build_solicitud_fingerprint(solicitud)
    current = str(run_fingerprint or "").strip().lower()
    return not current or current != expected
