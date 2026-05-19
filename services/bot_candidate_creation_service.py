from __future__ import annotations

import os
import re
from difflib import SequenceMatcher
from typing import Any

from flask import current_app
from config_app import db
from models import BotConversation, BotCandidateDraft, Candidata
from sqlalchemy import text
from sqlalchemy import inspect as sa_inspect
from services.bot_candidate_conversion_preview_service import map_draft_to_candidate_fields
from services.bot_candidate_draft_service import DRAFT_STATUS_APPROVED_FOR_CREATION, DRAFT_STATUS_REJECTED
from services.bot_data_safety_helpers import as_dict, as_list, norm_text
from services.environment_guard_service import (
    EnvironmentSafetyError,
    assert_real_creation_allowed,
    get_sensitive_flags_snapshot,
)
from services.bot_sandbox_service import is_staging_offline_active
from utils.cedula_normalizer import normalize_cedula_for_store, normalize_cedula_for_compare
from utils.timezone import utc_now_naive


def _table_columns(table_name: str) -> set[str]:
    try:
        insp = sa_inspect(db.session.get_bind())
        return {str(col.get("name")) for col in insp.get_columns(table_name)}
    except Exception:
        return set()


def _is_true_env(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def evaluate_real_creation_guardrails() -> dict[str, Any]:
    snap = get_sensitive_flags_snapshot()
    allow_flag = _is_true_env(os.getenv("BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL", "false"))
    env_ok = bool(snap.get("is_local_environment"))
    db_ok = bool(snap.get("is_safe_local_database"))
    app_env = str(snap.get("app_env") or "")
    allowed = bool(snap.get("real_creation_allowed"))
    return {
        "allowed": allowed,
        "app_env": app_env,
        "db_url_masked": str(snap.get("db_url_masked") or ""),
        "env_ok": env_ok,
        "db_ok": db_ok,
        "flag_ok": allow_flag,
        "flag_name": "BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL",
    }


def _cedula_duplicate_exists(cedula_digits: str) -> int | None:
    cols = _table_columns("candidatas")
    if "cedula_norm_digits" in cols:
        row = db.session.execute(
            text("SELECT fila FROM candidatas WHERE cedula_norm_digits = :c LIMIT 1"),
            {"c": str(cedula_digits)},
        ).first()
        return int(row[0]) if row else None
    if "cedula" in cols:
        rows = db.session.execute(text("SELECT fila, cedula FROM candidatas WHERE cedula IS NOT NULL LIMIT 500")).all()
        for row in rows:
            if normalize_cedula_for_compare(str(row[1] or "")) == str(cedula_digits):
                return int(row[0])
    return None


def normalize_candidate_phone(raw_phone: Any) -> dict[str, str | None]:
    original = norm_text(raw_phone)
    if not original:
        return {"original": None, "normalized": None}
    cleaned = re.sub(r"[\s\-\(\)\.]+", "", original)
    cleaned = cleaned.replace("+", "")
    digits = re.sub(r"\D+", "", cleaned)
    if digits.startswith("1") and len(digits) == 11:
        digits = digits[1:]
    if len(digits) != 10:
        return {"original": original, "normalized": None}
    if digits[:3] not in {"809", "829", "849"}:
        return {"original": original, "normalized": None}
    return {"original": original, "normalized": f"+1{digits}"}


def build_candidate_creation_payload(draft: BotCandidateDraft) -> dict[str, Any]:
    preview = map_draft_to_candidate_fields(draft)
    conv = db.session.get(BotConversation, int(draft.conversation_id))
    entities = as_dict(as_dict(getattr(conv, "metadata_json", {}) or {}).get("protocol_entities"))

    phone_source = (
        entities.get("phone_e164")
        or entities.get("phone")
        or entities.get("telefono")
        or preview.get("numero_telefono")
        or getattr(conv, "phone_e164", None)
    )
    phone = normalize_candidate_phone(phone_source)

    cedula_raw = norm_text(entities.get("cedula") or entities.get("documento") or entities.get("dni"))
    cedula_store = normalize_cedula_for_store(cedula_raw or "")
    acceptance_raw = entities.get("acceptance_25")
    acepta_pct = acceptance_raw if isinstance(acceptance_raw, bool) else str(acceptance_raw or "").strip().lower() in {"1", "true", "si", "sí", "acepta"}

    return {
        "nombre_completo": norm_text(preview.get("nombre_completo")),
        "edad": norm_text(preview.get("edad")),
        "numero_telefono": phone["original"] or norm_text(preview.get("numero_telefono")),
        "telefono_e164": phone["normalized"],
        "direccion_completa": norm_text(preview.get("direccion_completa")),
        "modalidad_trabajo_preferida": norm_text(preview.get("modalidad_trabajo_preferida")),
        "rutas_cercanas": norm_text(preview.get("rutas_cercanas")),
        "areas_experiencia": norm_text(preview.get("areas_experiencia")),
        "contactos_referencias_laborales": norm_text(preview.get("contactos_referencias_laborales")),
        "referencias_familiares_detalle": norm_text(preview.get("referencias_familiares_detalle")),
        "acepta_porcentaje_sueldo": bool(acepta_pct),
        "cedula": cedula_store,
        "normalized_phone": phone["normalized"],
        "cedula_compare": normalize_cedula_for_compare(cedula_store or ""),
        "source_conversation_id": int(draft.conversation_id),
    }


def detect_candidate_creation_conflicts(draft: BotCandidateDraft) -> dict[str, list[dict[str, Any]]]:
    payload = build_candidate_creation_payload(draft)
    blocking: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    conv = db.session.get(BotConversation, int(draft.conversation_id))
    pending = as_list(as_dict(getattr(conv, "metadata_json", {}) or {}).get("pending_corrections"))
    pending_active = [x for x in pending if isinstance(x, dict) and str(x.get("status") or "") == "pending_human"]

    if str(draft.draft_status) == DRAFT_STATUS_REJECTED:
        blocking.append({"type": "draft_rejected"})
    if pending_active:
        blocking.append({"type": "pending_corrections_active", "count": len(pending_active)})
    if str(draft.draft_status) == "converted":
        blocking.append({"type": "draft_already_converted"})

    if payload.get("normalized_phone"):
        by_phone = db.session.execute(
            text("SELECT fila FROM candidatas WHERE telefono_e164 = :p OR numero_telefono = :p LIMIT 1"),
            {"p": str(payload["normalized_phone"])},
        ).first()
        if by_phone:
            blocking.append({"type": "phone_duplicate", "candidate_id": int(by_phone[0])})
    else:
        warnings.append({"type": "phone_missing"})

    cedula_cmp = payload.get("cedula_compare")
    if cedula_cmp and len(str(cedula_cmp)) == 11:
        by_ced_id = _cedula_duplicate_exists(str(cedula_cmp))
        if by_ced_id:
            blocking.append({"type": "cedula_duplicate", "candidate_id": int(by_ced_id)})

    if not payload.get("contactos_referencias_laborales") or not payload.get("referencias_familiares_detalle"):
        warnings.append({"type": "references_incomplete"})

    name = norm_text(payload.get("nombre_completo"))
    city = norm_text(as_dict(as_dict(getattr(conv, "metadata_json", {}) or {}).get("protocol_entities")).get("city"))
    if name:
        for cand in db.session.execute(text("SELECT fila, nombre_completo FROM candidatas WHERE nombre_completo IS NOT NULL LIMIT 250")).all():
            cand_name = norm_text(cand[1])
            if not cand_name:
                continue
            if SequenceMatcher(a=name.lower(), b=cand_name.lower()).ratio() >= 0.9:
                warnings.append({"type": "name_similar", "candidate_id": int(cand[0])})
                break
    cols = _table_columns("candidatas")
    if city and "direccion_completa" in cols:
        by_city = db.session.execute(
            text("SELECT fila FROM candidatas WHERE direccion_completa LIKE :q LIMIT 1"),
            {"q": f"%{city}%"},
        ).first()
        if by_city:
            warnings.append({"type": "city_similar", "candidate_id": int(by_city[0])})

    return {"blocking_conflicts": blocking, "warning_conflicts": warnings}


def validate_candidate_creation(draft: BotCandidateDraft, *, require_approved: bool = False) -> dict[str, Any]:
    payload = build_candidate_creation_payload(draft)
    conflicts = detect_candidate_creation_conflicts(draft)
    blocking = list(conflicts["blocking_conflicts"])
    required = ("nombre_completo", "edad", "direccion_completa", "modalidad_trabajo_preferida")
    missing = [k for k in required if not norm_text(payload.get(k))]
    if missing:
        blocking.append({"type": "draft_incomplete", "missing": missing})
    if require_approved and str(draft.draft_status) != DRAFT_STATUS_APPROVED_FOR_CREATION:
        blocking.append({"type": "draft_not_approved_for_creation"})
    return {
        "valid": len(blocking) == 0,
        "blocking_conflicts": blocking,
        "warning_conflicts": conflicts["warning_conflicts"],
        "payload": payload,
    }


def create_candidate_from_draft(draft: BotCandidateDraft, *, actor_id: int | None = None) -> Candidata:
    if is_staging_offline_active():
        raise ValueError("candidate_real_creation_blocked_staging_offline")
    try:
        assert_real_creation_allowed()
    except EnvironmentSafetyError:
        raise ValueError("candidate_real_creation_blocked_guardrails")
    draft_row = (
        db.session.query(BotCandidateDraft)
        .filter(BotCandidateDraft.id == int(draft.id))
        .with_for_update()
        .first()
    )
    if not draft_row:
        raise ValueError("candidate_real_creation_blocked")
    if str(draft_row.draft_status) == "converted":
        raise ValueError("candidate_real_creation_blocked")
    validation = validate_candidate_creation(draft_row, require_approved=True)
    if not validation["valid"]:
        raise ValueError("candidate_real_creation_blocked")
    payload = validation["payload"]
    cols = _table_columns("candidatas")
    insert_payload = {
        "nombre_completo": payload["nombre_completo"],
        "edad": payload["edad"],
        "numero_telefono": payload["numero_telefono"],
        "telefono_e164": payload["telefono_e164"],
        "direccion_completa": payload["direccion_completa"],
        "modalidad_trabajo_preferida": payload["modalidad_trabajo_preferida"],
        "rutas_cercanas": payload["rutas_cercanas"],
        "areas_experiencia": payload["areas_experiencia"],
        "contactos_referencias_laborales": payload["contactos_referencias_laborales"],
        "referencias_familiares_detalle": payload["referencias_familiares_detalle"],
        "acepta_porcentaje_sueldo": bool(payload["acepta_porcentaje_sueldo"]),
        "cedula": payload["cedula"],
        "origen_registro": "bot_draft",
        "creado_desde_ruta": f"bot_draft:{draft.id}",
        "creado_por_staff": str(actor_id or ""),
        "marca_temporal": utc_now_naive(),
        "fecha_cambio_estado": utc_now_naive(),
        "estado": "en_proceso",
    }
    filtered = {k: v for k, v in insert_payload.items() if k in cols}
    names = list(filtered.keys())
    bind = db.session.get_bind()
    dialect_name = str(getattr(getattr(bind, "dialect", None), "name", "") or "").lower()
    if dialect_name == "postgresql":
        sql = f"INSERT INTO candidatas ({', '.join(names)}) VALUES ({', '.join([f':{n}' for n in names])}) RETURNING fila"
        candidate_id = int(db.session.execute(text(sql), filtered).scalar() or 0)
    else:
        sql = f"INSERT INTO candidatas ({', '.join(names)}) VALUES ({', '.join([f':{n}' for n in names])})"
        db.session.execute(text(sql), filtered)
        candidate_id = int(db.session.execute(text("SELECT last_insert_rowid()")).scalar() or 0)

    metadata = dict(draft_row.metadata_json or {})
    metadata["created_candidate_id"] = int(candidate_id)
    metadata["converted_at"] = utc_now_naive().isoformat()
    metadata["converted_by"] = actor_id
    metadata["source"] = "bot_draft"
    now = utc_now_naive()
    db.session.query(BotCandidateDraft).filter(BotCandidateDraft.id == int(draft_row.id)).update(
        {
            BotCandidateDraft.metadata_json: metadata,
            BotCandidateDraft.draft_status: "converted",
            BotCandidateDraft.reviewed_by: actor_id,
            BotCandidateDraft.reviewed_at: now,
            BotCandidateDraft.updated_at: now,
        },
        synchronize_session=False,
    )
    return type("CreatedCandidate", (), {"fila": candidate_id})()
