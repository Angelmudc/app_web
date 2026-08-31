# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.exc import IntegrityError

from config_app import db
from models import Candidata, LlamadaCandidata
from core.services.date_utils import parse_date, parse_decimal
from utils.audit_entity import log_candidata_action
from utils.audit_logger import diff_snapshots, snapshot_model_fields
from utils.candidata_readiness import maybe_update_estado_por_completitud
from utils.cedula_guard import duplicate_cedula_message, find_duplicate_candidata_by_cedula
from utils.cedula_normalizer import normalize_cedula_for_compare, normalize_cedula_for_store
from utils.robust_save import execute_robust_save, legacy_text_is_useful
from utils.timezone import utc_now_naive


@dataclass
class CandidateEditResult:
    ok: bool
    message: str
    changes: dict[str, Any] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    status_code: int = 200
    attempts: int = 0
    error_code: str | None = None


PERSONAL_FIELD_SPECS = {
    "nombre": ("nombre_completo", 150, True),
    "edad": ("edad", 10, False),
    "telefono": ("numero_telefono", 30, False),
    "direccion": ("direccion_completa", 250, False),
}

LABOR_FIELD_SPECS = {
    "modalidad": ("modalidad_trabajo_preferida", 100, False),
    "rutas": ("rutas_cercanas", 150, False),
    "disponibilidad_inicio": ("disponibilidad_inicio", 80, False),
    "empleo_anterior": ("empleo_anterior", 150, False),
    "anos_experiencia": ("anos_experiencia", 50, False),
    "areas_experiencia": ("areas_experiencia", 200, False),
    "sueldo_esperado": ("sueldo_esperado", 80, False),
    "motivacion_trabajo": ("motivacion_trabajo", 350, False),
}

LABOR_BOOL_FIELDS = {
    "sabe_planchar": ("sabe_planchar", False),
    "trabaja_con_ninos": ("trabaja_con_ninos", True),
    "trabaja_con_mascotas": ("trabaja_con_mascotas", True),
    "puede_dormir_fuera": ("puede_dormir_fuera", True),
    "acepta_porcentaje_sueldo": ("acepta_porcentaje_sueldo", False),
}

BASIC_AUDIT_FIELDS = [
    "nombre_completo",
    "edad",
    "numero_telefono",
    "direccion_completa",
    "modalidad_trabajo_preferida",
    "rutas_cercanas",
    "empleo_anterior",
    "anos_experiencia",
    "areas_experiencia",
    "sabe_planchar",
    "trabaja_con_ninos",
    "trabaja_con_mascotas",
    "puede_dormir_fuera",
    "sueldo_esperado",
    "motivacion_trabajo",
    "disponibilidad_inicio",
    "acepta_porcentaje_sueldo",
    "calificacion",
    "cedula",
    "cedula_norm_digits",
    "telefono_e164",
]

REFERENCE_AUDIT_FIELDS = [
    "contactos_referencias_laborales",
    "referencias_familiares_detalle",
    "referencias_laboral",
    "referencias_familiares",
]

INSCRIPTION_AUDIT_FIELDS = [
    "codigo",
    "medio_inscripcion",
    "inscripcion",
    "monto",
    "fecha",
    "estado",
    "fecha_cambio_estado",
    "usuario_cambio_estado",
]


def _clean_text(value: Any, max_len: int) -> str:
    return str(value or "").strip()[:max_len]


def _parse_optional_bool(raw: Any) -> bool | None:
    val = str(raw or "").strip().lower().replace("í", "i")
    if val in {"si", "1", "true", "on"}:
        return True
    if val in {"no", "0", "false", "off"}:
        return False
    return None


def _actor_text(actor: Any) -> str:
    return str(actor or "").strip()[:100] or "desconocido"


def _parse_inscription_bool(raw: Any, current: bool) -> bool:
    val = str(raw or "").strip().lower().replace("í", "i")
    if val in {"si", "sí", "1", "true", "on", "yes"}:
        return True
    if val in {"no", "0", "false", "off"}:
        return False
    return bool(current)


def _coerce_decimal(raw: Any) -> Decimal | None:
    if isinstance(raw, Decimal):
        return raw
    return parse_decimal(str(raw or ""))


def _coerce_date(raw: Any) -> date | None:
    if isinstance(raw, date):
        return raw
    return parse_date(str(raw or ""))


def _derive_inscription_state(candidata: Candidata, requested_inscription: bool) -> str:
    current_state = str(getattr(candidata, "estado", "") or "").strip().lower()
    if current_state in {"descalificada", "trabajando"}:
        return current_state
    if not requested_inscription:
        return "proceso_inscripcion"
    if getattr(candidata, "monto", None) and getattr(candidata, "fecha", None):
        return "inscrita"
    return "inscrita_incompleta"


def _expected_basic_values(candidata: Candidata, changed_fields: set[str]) -> dict[str, Any]:
    expected: dict[str, Any] = {}
    for field in changed_fields:
        expected[field] = getattr(candidata, field, None)
    if "numero_telefono" in changed_fields:
        expected["telefono_e164"] = getattr(candidata, "telefono_e164", None)
    if "cedula" in changed_fields:
        expected["cedula"] = getattr(candidata, "cedula", None)
        expected["cedula_norm_digits"] = getattr(candidata, "cedula_norm_digits", None)
    return expected


def _integrity_error_mentions_constraint(error: Exception, *constraint_names: str) -> bool:
    haystack_parts = [str(error or "")]
    orig = getattr(error, "orig", None)
    if orig is not None:
        haystack_parts.append(str(orig))
        if getattr(orig, "diag", None) is not None:
            haystack_parts.append(str(getattr(orig.diag, "constraint_name", "") or ""))
    for arg in getattr(error, "args", ()) or ():
        haystack_parts.append(str(arg))
    haystack = " ".join(part for part in haystack_parts if part).lower()
    return any((constraint or "").strip().lower() in haystack for constraint in constraint_names)


def _duplicate_cedula_error_message(existing: Candidata | None) -> str:
    if existing is not None:
        return duplicate_cedula_message(existing)
    return "Esta cédula ya está registrada en otra candidata. Verifique el expediente duplicado antes de continuar."


def update_candidate_basic_fields(
    candidata: Candidata,
    data: dict[str, Any],
    *,
    section: str,
    allow_clear_optional: bool,
    audit_action: str = "CANDIDATA_EDIT",
) -> CandidateEditResult:
    if section not in {"personal", "labor"}:
        return CandidateEditResult(False, "Sección inválida.", errors={"section": "Sección inválida."}, status_code=400, error_code="invalid_section")

    specs = PERSONAL_FIELD_SPECS if section == "personal" else LABOR_FIELD_SPECS
    allowed = set(specs)
    if section == "personal":
        allowed.add("cedula")
    if section == "labor":
        allowed.update(LABOR_BOOL_FIELDS)

    submitted = {key: data.get(key) for key in allowed if key in data}
    errors: dict[str, str] = {}
    if not submitted:
        return CandidateEditResult(False, "No hay campos editables en la solicitud.", errors, status_code=400, error_code="empty_payload")

    before = snapshot_model_fields(candidata, BASIC_AUDIT_FIELDS)
    changed_fields: set[str] = set()
    cedula_normalized_store: str | None = None

    if section == "personal" and "cedula" in submitted:
        cedula_raw = _clean_text(submitted.get("cedula"), 50)
        if not cedula_raw:
            errors["cedula"] = "La cédula es obligatoria."
        else:
            digits = normalize_cedula_for_compare(cedula_raw)
            if len(digits) != 11:
                errors["cedula"] = "Cédula inválida."
            else:
                with db.session.no_autoflush:
                    duplicate, _ = find_duplicate_candidata_by_cedula(
                        cedula_raw,
                        exclude_fila=getattr(candidata, "fila", None),
                    )
                if duplicate:
                    message = _duplicate_cedula_error_message(duplicate)
                    log_candidata_action(
                        action_type=audit_action,
                        candidata=candidata,
                        summary=f"Fallo edición rápida de candidata {getattr(candidata, 'fila', '')}",
                        metadata={"section": section, "fields": sorted(submitted.keys()), "duplicate_fila": getattr(duplicate, "fila", None)},
                        success=False,
                        error="Conflicto de cédula duplicada.",
                    )
                    return CandidateEditResult(
                        False,
                        message,
                        errors={"cedula": message},
                        status_code=409,
                        error_code="conflict",
                    )
                cedula_normalized_store = normalize_cedula_for_store(cedula_raw)

    for form_key, (attr, max_len, required) in specs.items():
        if form_key not in submitted:
            continue
        value = _clean_text(submitted.get(form_key), max_len)
        if required and not value:
            errors[form_key] = "Este campo es obligatorio."
            continue
        if not value and not allow_clear_optional:
            continue
        new_value = value or None
        if getattr(candidata, attr, None) != new_value:
            setattr(candidata, attr, new_value)
            changed_fields.add(attr)

    if section == "labor":
        for form_key, (attr, nullable) in LABOR_BOOL_FIELDS.items():
            if form_key not in submitted:
                continue
            raw = submitted.get(form_key)
            parsed = _parse_optional_bool(raw)
            if parsed is None and not nullable:
                parsed = False
            if getattr(candidata, attr, None) != parsed:
                setattr(candidata, attr, parsed)
                changed_fields.add(attr)

    if section == "personal" and cedula_normalized_store is not None:
        if getattr(candidata, "cedula", None) != cedula_normalized_store:
            candidata.cedula = cedula_normalized_store
            changed_fields.add("cedula")

    if errors:
        db.session.rollback()
        log_candidata_action(
            action_type=audit_action,
            candidata=candidata,
            summary=f"Fallo edición rápida de candidata {getattr(candidata, 'fila', '')}",
            metadata={"section": section, "fields": sorted(submitted.keys())},
            success=False,
            error="Error de validación en edición rápida.",
        )
        return CandidateEditResult(False, "Corrige los campos marcados.", errors=errors, status_code=400, error_code="validation_error")

    if not changed_fields:
        return CandidateEditResult(True, "Sin cambios.", {}, status_code=200)

    try:
        db.session.flush()
    except IntegrityError as exc:
        db.session.rollback()
        if _integrity_error_mentions_constraint(
            exc,
            "ux_candidatas_cedula_norm_digits",
            "candidatas_cedula_key",
        ):
            message = _duplicate_cedula_error_message(None)
            log_candidata_action(
                action_type=audit_action,
                candidata=candidata,
                summary=f"Fallo edición rápida de candidata {getattr(candidata, 'fila', '')}",
                metadata={"section": section, "constraint": "ux_candidatas_cedula_norm_digits"},
                success=False,
                error="Conflicto de cédula duplicada por flush.",
            )
            return CandidateEditResult(
                False,
                message,
                errors={"cedula": message},
                status_code=409,
                error_code="conflict",
            )
        log_candidata_action(
            action_type=audit_action,
            candidata=candidata,
            summary=f"Fallo edición rápida de candidata {getattr(candidata, 'fila', '')}",
            metadata={"section": section, "error_type": "IntegrityError"},
            success=False,
            error=str(exc),
        )
        return CandidateEditResult(False, "No se pudo guardar. Intenta de nuevo.", {}, status_code=500, error_code="persist_error")

    expected = _expected_basic_values(candidata, changed_fields)
    result = execute_robust_save(
        session=db.session,
        persist_fn=lambda _attempt: None,
        verify_fn=lambda: _verify_candidata_fields_saved(int(candidata.fila), expected),
    )
    if not result.ok:
        db.session.rollback()
        if isinstance(result.exception, IntegrityError) and _integrity_error_mentions_constraint(
            result.exception,
            "ux_candidatas_cedula_norm_digits",
            "candidatas_cedula_key",
        ):
            message = _duplicate_cedula_error_message(None)
            log_candidata_action(
                action_type=audit_action,
                candidata=candidata,
                summary=f"Fallo edición rápida de candidata {getattr(candidata, 'fila', '')}",
                metadata={"section": section, "attempt_count": int(result.attempts), "constraint": "ux_candidatas_cedula_norm_digits"},
                success=False,
                error="Conflicto de cédula duplicada por constraint.",
            )
            return CandidateEditResult(
                False,
                message,
                errors={"cedula": message},
                status_code=409,
                error_code="conflict",
            )
        log_candidata_action(
            action_type=audit_action,
            candidata=candidata,
            summary=f"Fallo edición rápida de candidata {getattr(candidata, 'fila', '')}",
            metadata={"section": section, "attempt_count": int(result.attempts)},
            success=False,
            error=result.error_message or "No se pudo verificar la persistencia.",
        )
        return CandidateEditResult(False, "No se pudo guardar. Intenta de nuevo.", {}, status_code=500, error_code="persist_error")

    after = snapshot_model_fields(candidata, BASIC_AUDIT_FIELDS)
    changes = diff_snapshots(before, after)
    log_candidata_action(
        action_type=audit_action,
        candidata=candidata,
        summary=f"Edición rápida de candidata {candidata.nombre_completo or candidata.fila}",
        metadata={"section": section, "attempt_count": int(result.attempts)},
        changes=changes,
        success=True,
    )
    return CandidateEditResult(True, "Guardado.", changes, attempts=int(result.attempts))


def update_candidate_form_references(
    candidata: Candidata,
    data: dict[str, Any],
    *,
    audit_action: str = "CANDIDATA_FORM_REFERENCES_EDIT",
) -> CandidateEditResult:
    laboral = _clean_text(data.get("contactos_referencias_laborales"), 5000)
    familiar = _clean_text(data.get("referencias_familiares_detalle"), 5000)
    errors: dict[str, str] = {}
    if not legacy_text_is_useful(laboral):
        errors["contactos_referencias_laborales"] = "Usa texto real para la referencia laboral."
    if not legacy_text_is_useful(familiar):
        errors["referencias_familiares_detalle"] = "Usa texto real para la referencia familiar."
    if errors:
        log_candidata_action(
            action_type=audit_action,
            candidata=candidata,
            summary=f"Fallo edición de referencias candidata {getattr(candidata, 'fila', '')}",
            metadata={"fields": sorted(errors.keys())},
            success=False,
            error="Referencias inválidas.",
        )
        return CandidateEditResult(False, "Referencias inválidas. Usa texto real.", errors=errors, status_code=400, error_code="validation_error")

    before = snapshot_model_fields(candidata, REFERENCE_AUDIT_FIELDS)
    candidata.contactos_referencias_laborales = laboral
    candidata.referencias_familiares_detalle = familiar
    db.session.flush()

    expected = {
        "contactos_referencias_laborales": laboral,
        "referencias_familiares_detalle": familiar,
    }
    result = execute_robust_save(
        session=db.session,
        persist_fn=lambda _attempt: None,
        verify_fn=lambda: _verify_candidata_fields_saved(int(candidata.fila), expected),
    )
    if not result.ok:
        db.session.rollback()
        log_candidata_action(
            action_type=audit_action,
            candidata=candidata,
            summary=f"Fallo edición de referencias candidata {getattr(candidata, 'fila', '')}",
            metadata={"attempt_count": int(result.attempts)},
            success=False,
            error=result.error_message or "No se pudo verificar la persistencia.",
        )
        return CandidateEditResult(False, "No se pudo guardar. Intenta de nuevo.", {}, status_code=500, error_code="persist_error")

    after = snapshot_model_fields(candidata, REFERENCE_AUDIT_FIELDS)
    changes = diff_snapshots(before, after)
    log_candidata_action(
        action_type=audit_action,
        candidata=candidata,
        summary=f"Edición de referencias candidata {candidata.nombre_completo or candidata.fila}",
        metadata={
            "changed_reference_fields": sorted(changes.keys()),
            "attempt_count": int(result.attempts),
        },
        changes=changes,
        success=True,
    )
    return CandidateEditResult(True, "Referencias actualizadas.", changes, attempts=int(result.attempts))


def update_candidate_references(
    candidata: Candidata,
    data: dict[str, Any],
    *,
    audit_action: str = "CANDIDATA_REFERENCES_EDIT",
) -> CandidateEditResult:
    laboral = _clean_text(data.get("referencias_laboral"), 5000)
    familiar = _clean_text(data.get("referencias_familiares"), 5000)
    errors: dict[str, str] = {}
    if not legacy_text_is_useful(laboral):
        errors["referencias_laboral"] = "Usa texto real para la referencia laboral."
    if not legacy_text_is_useful(familiar):
        errors["referencias_familiares"] = "Usa texto real para la referencia familiar."
    if errors:
        log_candidata_action(
            action_type=audit_action,
            candidata=candidata,
            summary=f"Fallo edición de referencias candidata {getattr(candidata, 'fila', '')}",
            metadata={"fields": sorted(errors.keys())},
            success=False,
            error="Referencias inválidas.",
        )
        return CandidateEditResult(False, "Referencias inválidas. Usa texto real.", errors=errors, status_code=400, error_code="validation_error")

    before = snapshot_model_fields(candidata, REFERENCE_AUDIT_FIELDS)
    candidata.referencias_laboral = laboral
    candidata.referencias_familiares = familiar
    db.session.flush()

    expected = {
        "referencias_laboral": laboral,
        "referencias_familiares": familiar,
    }
    result = execute_robust_save(
        session=db.session,
        persist_fn=lambda _attempt: None,
        verify_fn=lambda: _verify_candidata_fields_saved(int(candidata.fila), expected),
    )
    if not result.ok:
        db.session.rollback()
        log_candidata_action(
            action_type=audit_action,
            candidata=candidata,
            summary=f"Fallo edición de referencias candidata {getattr(candidata, 'fila', '')}",
            metadata={"attempt_count": int(result.attempts)},
            success=False,
            error=result.error_message or "No se pudo verificar la persistencia.",
        )
        return CandidateEditResult(False, "No se pudo guardar. Intenta de nuevo.", {}, status_code=500, error_code="persist_error")

    after = snapshot_model_fields(candidata, REFERENCE_AUDIT_FIELDS)
    changes = diff_snapshots(before, after)
    log_candidata_action(
        action_type=audit_action,
        candidata=candidata,
        summary=f"Edición de referencias candidata {candidata.nombre_completo or candidata.fila}",
        metadata={
            "changed_reference_fields": sorted(changes.keys()),
            "attempt_count": int(result.attempts),
        },
        changes=changes,
        success=True,
    )
    return CandidateEditResult(True, "Referencias actualizadas.", changes, attempts=int(result.attempts))


def update_candidate_inscription(
    candidata: Candidata,
    data: dict[str, Any],
    *,
    actor: str | None,
    code_generator,
    now_fn=utc_now_naive,
    readiness_updater=maybe_update_estado_por_completitud,
    audit_action: str = "CANDIDATA_INSCRIPTION_EDIT",
) -> CandidateEditResult:
    if not candidata:
        return CandidateEditResult(False, "Candidata no encontrada.", errors={"candidata": "No existe."}, status_code=404, error_code="not_found")

    before = snapshot_model_fields(candidata, INSCRIPTION_AUDIT_FIELDS)
    generated_code = False
    actor_value = _actor_text(actor)

    errors: dict[str, str] = {}
    raw_monto = data.get("monto")
    monto_text = str(raw_monto or "").strip()
    monto = _coerce_decimal(raw_monto)
    if monto_text and monto is None:
        errors["monto"] = "Monto inválido."

    raw_fecha = data.get("fecha")
    fecha_text = str(raw_fecha or "").strip()
    fecha = _coerce_date(raw_fecha)
    if fecha_text and fecha is None:
        errors["fecha"] = "Fecha inválida."

    if errors:
        log_candidata_action(
            action_type=audit_action,
            candidata=candidata,
            summary=f"Fallo inscripción candidata {getattr(candidata, 'fila', '')}",
            metadata={"fields": sorted(errors.keys())},
            success=False,
            error="Error de validación en inscripción.",
        )
        return CandidateEditResult(False, "Corrige los campos marcados.", errors=errors, status_code=400, error_code="validation_error")

    try:
        if not getattr(candidata, "codigo", None):
            candidata.codigo = str(code_generator() or "").strip()[:50]
            generated_code = bool(candidata.codigo)
            if not candidata.codigo:
                raise RuntimeError("Código vacío.")
    except Exception as exc:
        db.session.rollback()
        log_candidata_action(
            action_type=audit_action,
            candidata=candidata,
            summary=f"Fallo inscripción candidata {getattr(candidata, 'fila', '')}",
            metadata={"stage": "code_generation", "generated_code": False},
            success=False,
            error="No se pudo generar el código.",
        )
        return CandidateEditResult(False, "No se pudo generar el código.", status_code=500, error_code="code_generation_error")

    medio = str(data.get("medio") or data.get("medio_inscripcion") or "").strip()[:60]
    if medio:
        candidata.medio_inscripcion = medio

    candidata.inscripcion = _parse_inscription_bool(
        data.get("estado", data.get("inscripcion")),
        bool(getattr(candidata, "inscripcion", False)),
    )

    if monto is not None:
        candidata.monto = monto

    if fecha is not None:
        candidata.fecha = fecha

    previous_state = str(before.get("estado") or "").strip().lower()
    derived_state = _derive_inscription_state(candidata, bool(candidata.inscripcion))
    if derived_state and derived_state != previous_state:
        candidata.estado = derived_state
        if hasattr(candidata, "fecha_cambio_estado"):
            candidata.fecha_cambio_estado = now_fn()
        if hasattr(candidata, "usuario_cambio_estado"):
            candidata.usuario_cambio_estado = actor_value[:64]

    try:
        readiness_updater(candidata, actor=actor_value)
    except Exception:
        pass

    if not isinstance(candidata, Candidata):
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            return CandidateEditResult(False, "No se pudo guardar. Intenta de nuevo.", status_code=500, error_code="persist_error")
        after = snapshot_model_fields(candidata, INSCRIPTION_AUDIT_FIELDS)
        return CandidateEditResult(True, "Inscripción actualizada.", diff_snapshots(before, after), attempts=1)

    db.session.flush()
    expected = {
        "codigo": getattr(candidata, "codigo", None),
        "medio_inscripcion": getattr(candidata, "medio_inscripcion", None),
        "inscripcion": getattr(candidata, "inscripcion", None),
        "monto": getattr(candidata, "monto", None),
        "fecha": getattr(candidata, "fecha", None),
        "estado": getattr(candidata, "estado", None),
    }
    result = execute_robust_save(
        session=db.session,
        persist_fn=lambda _attempt: None,
        verify_fn=lambda: _verify_candidata_fields_saved(int(candidata.fila), expected),
    )
    if not result.ok:
        db.session.rollback()
        log_candidata_action(
            action_type=audit_action,
            candidata=candidata,
            summary=f"Fallo inscripción candidata {getattr(candidata, 'fila', '')}",
            metadata={
                "attempt_count": int(result.attempts),
                "previous_state": before.get("estado"),
                "new_state": getattr(candidata, "estado", None),
                "generated_code": generated_code,
            },
            success=False,
            error=result.error_message or "No se pudo verificar la persistencia.",
        )
        return CandidateEditResult(False, "No se pudo guardar. Intenta de nuevo.", status_code=500, error_code="persist_error")

    after = snapshot_model_fields(candidata, INSCRIPTION_AUDIT_FIELDS)
    changes = diff_snapshots(before, after)
    log_candidata_action(
        action_type=audit_action,
        candidata=candidata,
        summary=f"Inscripción actualizada candidata {candidata.nombre_completo or candidata.fila}",
        metadata={
            "attempt_count": int(result.attempts),
            "previous_state": before.get("estado"),
            "new_state": after.get("estado"),
            "generated_code": generated_code,
        },
        changes=changes,
        success=True,
    )
    return CandidateEditResult(True, "Inscripción actualizada.", changes, attempts=int(result.attempts))


CALL_RESULT_CHOICES = {"no_contesta", "inscripcion", "rechaza", "voicemail", "informada", "exitosa", "otro"}


def register_candidate_call(
    candidata: Candidata,
    data: dict[str, Any],
    *,
    actor: str | None,
    audit_action: str = "CANDIDATA_CALL_REGISTER",
) -> CandidateEditResult:
    if not candidata:
        return CandidateEditResult(False, "Candidata no encontrada.", errors={"candidata": "No existe."}, status_code=404, error_code="not_found")

    resultado = str(data.get("resultado") or "").strip()[:200]
    errors: dict[str, str] = {}
    if resultado not in CALL_RESULT_CHOICES:
        errors["resultado"] = "Resultado inválido."

    minutos_raw = str(data.get("duracion_minutos") or "").strip()
    segundos = None
    if minutos_raw:
        try:
            minutos = int(minutos_raw)
            if minutos < 0:
                raise ValueError("negative")
            segundos = minutos * 60
        except Exception:
            errors["duracion_minutos"] = "Duración inválida."

    if errors:
        log_candidata_action(
            action_type=audit_action,
            candidata=candidata,
            summary=f"Fallo registro de llamada candidata {getattr(candidata, 'fila', '')}",
            metadata={"fields": sorted(errors.keys())},
            success=False,
            error="Error de validación en llamada.",
        )
        return CandidateEditResult(False, "Corrige los campos marcados.", errors=errors, status_code=400, error_code="validation_error")

    llamada = LlamadaCandidata(
        candidata_id=candidata.fila,
        fecha_llamada=utc_now_naive(),
        agente=_actor_text(actor)[:64],
        resultado=resultado,
        duracion_segundos=segundos,
        notas=str(data.get("notas") or "").strip()[:2000],
        created_at=utc_now_naive(),
    )
    db.session.add(llamada)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        log_candidata_action(
            action_type=audit_action,
            candidata=candidata,
            summary=f"Fallo registro de llamada candidata {getattr(candidata, 'fila', '')}",
            metadata={"resultado": resultado},
            success=False,
            error="No se pudo guardar la llamada.",
        )
        return CandidateEditResult(False, "No se pudo registrar la llamada.", status_code=500, error_code="persist_error")

    log_candidata_action(
        action_type=audit_action,
        candidata=candidata,
        summary=f"Llamada registrada candidata {candidata.nombre_completo or candidata.fila}",
        metadata={"resultado": resultado, "duracion_segundos": segundos, "llamada_id": getattr(llamada, "id", None)},
        success=True,
    )
    return CandidateEditResult(True, "Llamada registrada.", {"llamada_id": getattr(llamada, "id", None)})


def _verify_candidata_fields_saved(fila: int, expected: dict[str, Any]) -> bool:
    row = Candidata.query.filter(Candidata.fila == int(fila)).first()
    if not row:
        return False
    for field, expected_value in (expected or {}).items():
        current = getattr(row, field, None)
        if isinstance(current, str) or isinstance(expected_value, str):
            if (current or "") != (expected_value or ""):
                return False
        elif current != expected_value:
            return False
    return True
