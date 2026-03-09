# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Tuple

from utils.candidata_completitud_audit import entrevista_ok, referencias_ok
from utils.guards import candidata_esta_descalificada
from utils.timezone import utc_now_naive

_READY_BASE_STATES = {"lista_para_trabajar", "inscrita"}
_NOT_READY_STATES = {"en_proceso", "proceso_inscripcion", "inscrita_incompleta"}


def _has_blob(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, memoryview):
        try:
            return len(value.tobytes()) > 0
        except Exception:
            return False
    if isinstance(value, (bytes, bytearray)):
        return len(value) > 0
    try:
        return bool(value)
    except Exception:
        return False


def candidata_has_interview(candidata) -> bool:
    legacy = (getattr(candidata, "entrevista", None) or "").strip()
    entrevistas_rel = getattr(candidata, "entrevistas_nuevas", None)
    entrevistas_count = 0
    if entrevistas_rel is not None:
        # Relación dynamic -> query.count()
        if hasattr(entrevistas_rel, "count"):
            try:
                entrevistas_count = int(entrevistas_rel.count() or 0)
            except Exception:
                entrevistas_count = 0
        else:
            # Relación eager/lista
            try:
                entrevistas_count = len(list(entrevistas_rel or []))
            except Exception:
                entrevistas_count = 0
    return entrevista_ok(legacy, entrevistas_count)


def candidata_referencias_complete(candidata) -> Dict[str, bool]:
    ref_laboral = (
        getattr(candidata, "referencias_laborales_texto", None)
        or getattr(candidata, "contactos_referencias_laborales", None)
        or getattr(candidata, "referencias_laboral", None)
    )
    ref_familiar = (
        getattr(candidata, "referencias_familiares_texto", None)
        or getattr(candidata, "referencias_familiares_detalle", None)
        or getattr(candidata, "referencias_familiares", None)
    )
    return {
        "referencias_laboral": referencias_ok(ref_laboral),
        "referencias_familiares": referencias_ok(ref_familiar),
    }


def candidata_docs_complete(candidata) -> Dict[str, Any]:
    depuracion = _has_blob(getattr(candidata, "depuracion", None))
    perfil = _has_blob(getattr(candidata, "perfil", None))
    cedula1 = _has_blob(getattr(candidata, "cedula1", None))
    cedula2 = _has_blob(getattr(candidata, "cedula2", None))

    required = {
        "depuracion": True,
        "perfil": True,
        "cedula1": True,
        "cedula2": True,
    }
    flags = {
        "depuracion": depuracion,
        "perfil": perfil,
        "cedula1": cedula1,
        "cedula2": cedula2,
    }

    missing_required = [
        key
        for key, req in required.items()
        if req and not flags.get(key, False)
    ]
    return {
        "complete": len(missing_required) == 0,
        "required": required,
        "flags": flags,
        "missing_required": missing_required,
        "warnings": [],
    }


def candidata_is_ready_to_send(candidata) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    estado = (getattr(candidata, "estado", None) or "").strip().lower()
    codigo = (getattr(candidata, "codigo", None) or "").strip()

    if candidata_esta_descalificada(candidata):
        reasons.append("Estado descalificada.")
    if estado == "trabajando":
        reasons.append("Estado trabajando.")
    if not codigo:
        reasons.append("Falta código interno.")
    if estado not in _READY_BASE_STATES:
        if estado in _NOT_READY_STATES:
            reasons.append(f"Estado no listo: {estado}.")
        elif not estado:
            reasons.append("Estado no definido.")
        else:
            reasons.append(f"Estado no permitido para envío: {estado}.")

    if not candidata_has_interview(candidata):
        reasons.append("Falta entrevista (legacy o nueva).")

    referencias = candidata_referencias_complete(candidata)
    if not referencias.get("referencias_laboral"):
        reasons.append("Falta referencias_laboral válida.")
    if not referencias.get("referencias_familiares"):
        reasons.append("Falta referencias_familiares válida.")

    docs = candidata_docs_complete(candidata)
    for key in docs.get("missing_required", []):
        reasons.append(f"Falta documento requerido: {key}.")
    for key in docs.get("warnings", []):
        reasons.append(f"Advertencia: falta {key} (no bloqueante).")

    has_blocking = any(not x.lower().startswith("advertencia:") for x in reasons)
    return (not has_blocking), reasons


def maybe_update_estado_por_completitud(candidata, actor: str | None = None) -> bool:
    if not candidata:
        return False
    if candidata_esta_descalificada(candidata):
        return False

    estado_actual = (getattr(candidata, "estado", None) or "").strip().lower()
    if estado_actual == "trabajando":
        return False

    ready, reasons = candidata_is_ready_to_send(candidata)
    now = utc_now_naive()
    actor_text = (actor or "").strip()[:100] or None

    if ready:
        if estado_actual in ("inscrita", "inscrita_incompleta", "proceso_inscripcion", "en_proceso"):
            candidata.estado = "lista_para_trabajar"
            if hasattr(candidata, "fecha_cambio_estado"):
                candidata.fecha_cambio_estado = now
            if actor_text and hasattr(candidata, "usuario_cambio_estado"):
                candidata.usuario_cambio_estado = actor_text
            return True
        return False

    blocking = [r for r in reasons if not r.lower().startswith("advertencia:")]
    if estado_actual == "lista_para_trabajar" and blocking:
        candidata.estado = "inscrita_incompleta"
        if hasattr(candidata, "fecha_cambio_estado"):
            candidata.fecha_cambio_estado = now
        if actor_text and hasattr(candidata, "usuario_cambio_estado"):
            candidata.usuario_cambio_estado = actor_text
        return True
    return False
