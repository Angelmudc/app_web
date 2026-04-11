# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import re
from typing import Any

from utils.matching_explain import client_bullets_from_breakdown


def _iso(value) -> str | None:
    if value is None:
        return None
    try:
        return value.isoformat()
    except Exception:
        return str(value)


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _safe_location_summary(breakdown: dict) -> str:
    bd = breakdown if isinstance(breakdown, dict) else {}
    city = _clean_text(bd.get("city_detectada"))
    return city if city else ""


def _safe_experience_summary(cand) -> str:
    if cand is None:
        return ""
    years = _clean_text(getattr(cand, "anos_experiencia", None))
    areas = _clean_text(getattr(cand, "areas_experiencia", None))
    summary = _clean_text(getattr(cand, "experiencia_resumen", None))
    motivation = _clean_text(getattr(cand, "motivacion_trabajo", None))

    if years and areas:
        return f"{years} de experiencia en {areas}"[:180]
    if summary:
        return summary[:180]
    if years:
        return f"Experiencia reportada: {years}"[:180]
    if areas:
        return f"Experiencia en {areas}"[:180]
    if motivation:
        return motivation[:180]
    return ""


def _to_blob_bytes(value: Any) -> bytes:
    if value is None:
        return b""
    if isinstance(value, memoryview):
        try:
            return value.tobytes()
        except Exception:
            return b""
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, bytes):
        return value
    try:
        return bytes(value)
    except Exception:
        return b""


def _detect_image_mimetype(data: bytes) -> str:
    if not data:
        return ""
    head = data[:12]
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"\xFF\xD8\xFF"):
        return "image/jpeg"
    if head[:4] == b"GIF8":
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return ""


def _safe_perfil_photo_data_url(cand) -> str | None:
    if cand is None:
        return None
    blob = _to_blob_bytes(getattr(cand, "perfil", None))
    if not blob:
        return None
    mimetype = _detect_image_mimetype(blob)
    if not mimetype:
        return None
    # Evita inflar el payload de shortlist con blobs demasiado pesados.
    if len(blob) > 1_500_000:
        return None
    encoded = base64.b64encode(blob).decode("ascii")
    return f"data:{mimetype};base64,{encoded}"


def _compatibility_badge(*, score_final: int, confidence_band: str) -> dict[str, str]:
    score = int(score_final or 0)
    band = str(confidence_band or "").strip().lower()
    if score >= 85 or band == "alta":
        return {"label": "Compatibilidad alta", "tone": "success"}
    if score >= 70 or band == "media":
        return {"label": "Compatibilidad media", "tone": "warning"}
    return {"label": "Compatibilidad base", "tone": "secondary"}


def present_shortlist_payload(
    *,
    solicitud,
    state_code: str,
    run=None,
    items: list | None = None,
    stale: bool = False,
    state_message: str = "",
) -> dict[str, Any]:
    rows = list(items or [])
    dto_items: list[dict[str, Any]] = []

    for item in rows:
        cand = getattr(item, "candidata", None)
        breakdown = dict(getattr(item, "breakdown_snapshot", None) or {})
        score_final = int(getattr(item, "score_final", 0) or 0)
        confidence_band = str(getattr(item, "confidence_band", "") or "")
        dto_items.append(
            {
                "item_id": int(getattr(item, "id", 0) or 0),
                "run_id": int(getattr(item, "run_id", 0) or 0),
                "solicitud_id": int(getattr(item, "solicitud_id", 0) or 0),
                "candidata": {
                    "id": int(getattr(item, "candidata_id", 0) or 0),
                    "codigo": str(getattr(cand, "codigo", "") or ""),
                    "nombre": str(getattr(cand, "nombre_completo", "") or ""),
                    "estado": str(getattr(cand, "estado", "") or ""),
                    "edad": str(getattr(cand, "edad", "") or ""),
                    "modalidad": str(getattr(cand, "modalidad_trabajo_preferida", "") or ""),
                },
                "rank": int(getattr(item, "rank_position", 0) or 0) or None,
                "is_eligible": bool(getattr(item, "is_eligible", False)),
                "hard_fail": bool(getattr(item, "hard_fail", False)),
                "hard_fail_codes": list(getattr(item, "hard_fail_codes", None) or []),
                "hard_fail_reasons": list(getattr(item, "hard_fail_reasons", None) or []),
                "soft_fail_codes": list(getattr(item, "soft_fail_codes", None) or []),
                "soft_fail_reasons": list(getattr(item, "soft_fail_reasons", None) or []),
                "score_final": score_final,
                "score_operational": int(getattr(item, "score_operational", 0) or 0),
                "confidence_band": confidence_band,
                "policy_snapshot": dict(getattr(item, "policy_snapshot", None) or {}),
                "breakdown_snapshot": breakdown,
                "reasons": client_bullets_from_breakdown(breakdown)[:3],
                "ubicacion_resumen": _safe_location_summary(breakdown),
                "experiencia_resumen": _safe_experience_summary(cand),
                "perfil_foto_data_url": _safe_perfil_photo_data_url(cand),
                "compatibility_badge": _compatibility_badge(
                    score_final=score_final,
                    confidence_band=confidence_band,
                ),
            }
        )

    run_dto = None
    if run is not None:
        run_dto = {
            "run_id": int(getattr(run, "id", 0) or 0),
            "status": str(getattr(run, "status", "") or ""),
            "trigger_source": str(getattr(run, "trigger_source", "") or ""),
            "fingerprint_hash": str(getattr(run, "fingerprint_hash", "") or ""),
            "model_version": str(getattr(run, "model_version", "") or ""),
            "policy_version": str(getattr(run, "policy_version", "") or ""),
            "requested_at": _iso(getattr(run, "requested_at", None)),
            "started_at": _iso(getattr(run, "started_at", None)),
            "completed_at": _iso(getattr(run, "completed_at", None)),
            "failed_at": _iso(getattr(run, "failed_at", None)),
            "error_code": str(getattr(run, "error_code", "") or ""),
            "error_message": str(getattr(run, "error_message", "") or ""),
            "counts": {
                "pool_size": int(getattr(run, "pool_size", 0) or 0),
                "items_count": int(getattr(run, "items_count", 0) or 0),
                "eligible_count": int(getattr(run, "eligible_count", 0) or 0),
                "hard_fail_count": int(getattr(run, "hard_fail_count", 0) or 0),
                "soft_fail_count": int(getattr(run, "soft_fail_count", 0) or 0),
            },
        }

    return {
        "solicitud_id": int(getattr(solicitud, "id", 0) or 0),
        "cliente_id": int(getattr(solicitud, "cliente_id", 0) or 0),
        "state": {
            "code": str(state_code or "pending"),
            "message": str(state_message or ""),
            "stale": bool(stale),
        },
        "run": run_dto,
        "items": dto_items,
    }
