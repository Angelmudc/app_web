# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

from utils.audit_logger import log_action


def candidata_entity_id(candidata: Any) -> str | None:
    if candidata is None:
        return None
    try:
        cid = getattr(candidata, "id", None)
        if cid is not None and str(cid).strip() != "":
            return str(cid)
    except Exception:
        pass
    try:
        fila = getattr(candidata, "fila", None)
        if fila is not None and str(fila).strip() != "":
            return str(fila)
    except Exception:
        pass
    return None


def candidata_entity_meta(candidata: Any) -> dict[str, Any]:
    if candidata is None:
        return {}
    out: dict[str, Any] = {}
    for source, target in (
        ("codigo", "codigo"),
        ("cedula", "cedula"),
        ("nombre_completo", "nombre"),
        ("estado", "estado"),
    ):
        try:
            value = getattr(candidata, source, None)
        except Exception:
            value = None
        if value is None:
            continue
        txt = str(value).strip() if isinstance(value, str) else value
        if txt is None or txt == "":
            continue
        out[target] = txt
    return out


def _without_sensitive_phone_fields(payload: dict[str, Any] | None) -> dict[str, Any]:
    src = dict(payload or {})
    forbidden = {
        "telefono",
        "numero_telefono",
        "phone",
        "phone_number",
        "whatsapp",
    }
    return {k: v for k, v in src.items() if str(k or "").strip().lower() not in forbidden}


def log_candidata_action(
    action_type: str,
    candidata: Any,
    summary: str,
    metadata: dict[str, Any] | None = None,
    changes: dict[str, Any] | None = None,
    success: bool = True,
    error: str | None = None,
) -> None:
    cid = candidata_entity_id(candidata)
    meta = candidata_entity_meta(candidata)
    meta.update(_without_sensitive_phone_fields(metadata))
    log_action(
        action_type=action_type,
        entity_type="candidata",
        entity_id=cid,
        summary=summary,
        metadata=meta,
        changes=changes,
        success=success,
        error=error,
    )
