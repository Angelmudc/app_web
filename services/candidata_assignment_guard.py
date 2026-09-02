# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import asdict, dataclass
from types import SimpleNamespace
from typing import Any

from flask import current_app, g, has_request_context
from sqlalchemy.orm import load_only

from config_app import db
from models import Solicitud, SolicitudCandidata

_ACTIVE_ASSIGNMENT_STATUS = ("enviada", "vista", "seleccionada")
_WORKING_ALLOWED_STATUS = {"proceso", "activa", "reemplazo", "espera_pago", "pagada"}
_CHARGE_ALLOWED_STATUS = {"activa", "espera_pago", "pagada"}
_BLOCKED_STATUS = {"cancelada", "pendiente_servicio", "finalizada", "cerrada"}
_PAYMENT_ELIGIBILITY_CACHE_ATTR = "_candidata_assignment_payment_eligibility_cache"


@dataclass
class CandidateAssignmentGuardResult:
    has_active_assignment: bool
    can_mark_working: bool
    can_charge: bool
    reason_code: str
    reason_message: str
    matched_by: str | None
    solicitud_id: int | None
    cliente_id: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ok_result(*, matched_by: str, solicitud: Solicitud, can_charge: bool, can_mark_working: bool) -> CandidateAssignmentGuardResult:
    return CandidateAssignmentGuardResult(
        has_active_assignment=True,
        can_mark_working=bool(can_mark_working),
        can_charge=bool(can_charge),
        reason_code="ok",
        reason_message="Asignación activa coherente.",
        matched_by=matched_by,
        solicitud_id=int(getattr(solicitud, "id", 0) or 0) or None,
        cliente_id=int(getattr(solicitud, "cliente_id", 0) or 0) or None,
    )


def _guard_logger_warning(msg: str, **extra):
    try:
        current_app.logger.warning(msg, extra=extra or None)
    except Exception:
        pass


def _guard_logger_exception(msg: str, **extra):
    try:
        current_app.logger.exception(msg, extra=extra or None)
    except Exception:
        pass


def _with_options_if_supported(query, *options):
    if hasattr(query, "options"):
        return query.options(*options)
    return query


def _load_only_if_supported(model, *names):
    try:
        attrs = [getattr(model, name) for name in names]
    except Exception:
        return None
    if len(attrs) != len(names):
        return None
    try:
        return load_only(*attrs)
    except Exception:
        return None


def _rollback_failed_session_if_needed() -> None:
    session = getattr(db, "session", None)
    if session is None or not hasattr(session, "rollback"):
        return
    if getattr(session, "is_active", True) is False:
        session.rollback()


def _assignment_guard_from_solicitud(
    solicitud,
    *,
    matched_by: str,
    has_active_assignment: bool,
    emit_warning: bool = False,
) -> CandidateAssignmentGuardResult:
    estado = str(getattr(solicitud, "estado", "") or "").strip().lower()
    solicitud_id = int(getattr(solicitud, "id", 0) or 0) or None
    cliente_id = int(getattr(solicitud, "cliente_id", 0) or 0) or None
    if has_active_assignment:
        if estado in _BLOCKED_STATUS:
            return CandidateAssignmentGuardResult(
                has_active_assignment=True,
                can_mark_working=False,
                can_charge=False,
                reason_code="solicitud_state_blocked",
                reason_message=f"La solicitud está en estado '{estado}' y no permite operación financiera/operativa.",
                matched_by=matched_by,
                solicitud_id=solicitud_id,
                cliente_id=cliente_id,
            )
        return CandidateAssignmentGuardResult(
            has_active_assignment=True,
            can_mark_working=estado in _WORKING_ALLOWED_STATUS,
            can_charge=estado in _CHARGE_ALLOWED_STATUS,
            reason_code="ok",
            reason_message="Asignación activa coherente.",
            matched_by=matched_by,
            solicitud_id=solicitud_id,
            cliente_id=cliente_id,
        )

    if estado in _WORKING_ALLOWED_STATUS:
        if emit_warning:
            _guard_logger_warning(
                "Inconsistencia detectada: solicitud.candidata_id sin fila en solicitudes_candidatas.",
                candidata_id=int(getattr(solicitud, "candidata_id", 0) or 0),
                solicitud_id=solicitud_id,
                estado=estado,
                matched_by=matched_by,
            )
        return CandidateAssignmentGuardResult(
            has_active_assignment=True,
            can_mark_working=True,
            can_charge=estado in _CHARGE_ALLOWED_STATUS,
            reason_code="fallback_without_solicitud_candidata",
            reason_message=(
                "Se usó compatibilidad temporal por falta de fila en solicitudes_candidatas. "
                "Debe corregirse la asignación canónica."
            ),
            matched_by=matched_by,
            solicitud_id=solicitud_id,
            cliente_id=cliente_id,
        )

    return CandidateAssignmentGuardResult(
        has_active_assignment=False,
        can_mark_working=False,
        can_charge=False,
        reason_code="fallback_state_not_operable",
        reason_message=f"Existe vínculo fallback pero el estado '{estado or 'desconocido'}' no es operable.",
        matched_by=matched_by,
        solicitud_id=solicitud_id,
        cliente_id=cliente_id,
    )


def build_solicitud_payment_eligibility_map(
    solicitudes,
    *,
    emit_warnings: bool = True,
) -> dict[int, CandidateAssignmentGuardResult]:
    solicitud_rows = []
    solicitud_ids: list[int] = []
    candidata_ids: list[int] = []
    for solicitud in (solicitudes or []):
        solicitud_id = int(getattr(solicitud, "id", 0) or 0)
        if solicitud_id <= 0:
            continue
        candidata_id = int(getattr(solicitud, "candidata_id", 0) or 0)
        solicitud_rows.append((solicitud_id, candidata_id, solicitud))
        solicitud_ids.append(solicitud_id)
        if candidata_id > 0:
            candidata_ids.append(candidata_id)

    if not solicitud_rows:
        return {}

    cache_key = tuple(sorted((sid, cid) for sid, cid, _ in solicitud_rows))
    if has_request_context():
        cache_obj = getattr(g, _PAYMENT_ELIGIBILITY_CACHE_ATTR, None)
        if not isinstance(cache_obj, dict):
            cache_obj = {}
            setattr(g, _PAYMENT_ELIGIBILITY_CACHE_ATTR, cache_obj)
        cached = cache_obj.get(cache_key)
        if cached is not None:
            return cached

    unique_candidata_ids = sorted({cid for cid in candidata_ids if cid > 0})
    active_rows: dict[tuple[int, int], object] = {}
    fallback_rows: dict[tuple[int, int], object] = {}

    try:
        if unique_candidata_ids:
            active_query = (
                db.session.query(
                    SolicitudCandidata.candidata_id.label("candidata_id"),
                    Solicitud.id.label("id"),
                    Solicitud.cliente_id.label("cliente_id"),
                    Solicitud.estado.label("estado"),
                )
                .join(Solicitud, Solicitud.id == SolicitudCandidata.solicitud_id)
                .filter(
                    SolicitudCandidata.candidata_id.in_(unique_candidata_ids),
                    SolicitudCandidata.solicitud_id.in_(solicitud_ids),
                    SolicitudCandidata.status.in_(_ACTIVE_ASSIGNMENT_STATUS),
                )
            )
            for row in (active_query.all() or []):
                sid = int(getattr(row, "id", 0) or 0)
                cid = int(getattr(row, "candidata_id", 0) or 0)
                if sid > 0 and cid > 0:
                    active_rows[(sid, cid)] = SimpleNamespace(
                        id=sid,
                        cliente_id=int(getattr(row, "cliente_id", 0) or 0) or None,
                        estado=getattr(row, "estado", None),
                        candidata_id=cid,
                    )

        if unique_candidata_ids:
            fallback_query = (
                Solicitud.query
                .options(
                    load_only(
                        Solicitud.id,
                        Solicitud.candidata_id,
                        Solicitud.cliente_id,
                        Solicitud.estado,
                    )
                )
                .filter(
                    Solicitud.id.in_(solicitud_ids),
                    Solicitud.candidata_id.in_(unique_candidata_ids),
                )
            )
            for row in (fallback_query.all() or []):
                sid = int(getattr(row, "id", 0) or 0)
                cid = int(getattr(row, "candidata_id", 0) or 0)
                if sid > 0 and cid > 0:
                    fallback_rows[(sid, cid)] = row
    except Exception as exc:
        _guard_logger_exception(
            "Error validando contexto de asignación de candidata en batch.",
            solicitud_ids=solicitud_ids,
            candidata_ids=unique_candidata_ids,
        )
        return {
            solicitud_id: CandidateAssignmentGuardResult(
                has_active_assignment=False,
                can_mark_working=False,
                can_charge=False,
                reason_code="validation_error",
                reason_message=f"Error validando asignación: {exc}",
                matched_by=None,
                solicitud_id=solicitud_id,
                cliente_id=None,
            )
            for solicitud_id, _candidata_id, _solicitud in solicitud_rows
        }

    result_by_solicitud_id: dict[int, CandidateAssignmentGuardResult] = {}
    for solicitud_id, candidata_id, solicitud in solicitud_rows:
        if candidata_id <= 0:
            result_by_solicitud_id[solicitud_id] = CandidateAssignmentGuardResult(
                has_active_assignment=False,
                can_mark_working=False,
                can_charge=False,
                reason_code="invalid_candidate_id",
                reason_message="Candidata inválida para validar asignación.",
                matched_by=None,
                solicitud_id=solicitud_id,
                cliente_id=int(getattr(solicitud, "cliente_id", 0) or 0) or None,
            )
            continue

        active_row = active_rows.get((solicitud_id, candidata_id))
        if active_row is not None:
            result_by_solicitud_id[solicitud_id] = _assignment_guard_from_solicitud(
                active_row,
                matched_by="solicitudes_candidatas",
                has_active_assignment=True,
            )
            continue

        fallback_row = fallback_rows.get((solicitud_id, candidata_id))
        if fallback_row is not None:
            result_by_solicitud_id[solicitud_id] = _assignment_guard_from_solicitud(
                fallback_row,
                matched_by="solicitud_candidata_id_fallback",
                has_active_assignment=False,
                emit_warning=bool(emit_warnings),
            )
            continue

        result_by_solicitud_id[solicitud_id] = CandidateAssignmentGuardResult(
            has_active_assignment=False,
            can_mark_working=False,
            can_charge=False,
            reason_code="no_active_assignment",
            reason_message="No existe una asignación activa coherente para esta candidata.",
            matched_by=None,
            solicitud_id=solicitud_id,
            cliente_id=int(getattr(solicitud, "cliente_id", 0) or 0) or None,
        )

    if has_request_context():
        cache_obj = getattr(g, _PAYMENT_ELIGIBILITY_CACHE_ATTR, None)
        if not isinstance(cache_obj, dict):
            cache_obj = {}
            setattr(g, _PAYMENT_ELIGIBILITY_CACHE_ATTR, cache_obj)
        cache_obj[cache_key] = result_by_solicitud_id

    return result_by_solicitud_id


def validate_candidata_assignment_context(*, candidata_id: int, solicitud_id: int | None = None) -> CandidateAssignmentGuardResult:
    try:
        cand_id = int(candidata_id)
        if cand_id <= 0:
            return CandidateAssignmentGuardResult(
                has_active_assignment=False,
                can_mark_working=False,
                can_charge=False,
                reason_code="invalid_candidate_id",
                reason_message="Candidata inválida para validar asignación.",
                matched_by=None,
                solicitud_id=None,
                cliente_id=None,
            )

        sc_query = db.session.query(SolicitudCandidata, Solicitud)
        sc_options = [
            opt
            for opt in (
                _load_only_if_supported(
                    SolicitudCandidata,
                    "id",
                    "solicitud_id",
                    "candidata_id",
                    "status",
                ),
                _load_only_if_supported(
                    Solicitud,
                    "id",
                    "cliente_id",
                    "estado",
                    "candidata_id",
                ),
            )
            if opt is not None
        ]
        sc_query = (
            _with_options_if_supported(
                sc_query,
                *sc_options,
            )
            .join(Solicitud, Solicitud.id == SolicitudCandidata.solicitud_id)
            .filter(
                SolicitudCandidata.candidata_id == cand_id,
                SolicitudCandidata.status.in_(_ACTIVE_ASSIGNMENT_STATUS),
            )
        )
        if solicitud_id is not None:
            sc_query = sc_query.filter(Solicitud.id == int(solicitud_id))
        sc_row = sc_query.order_by(SolicitudCandidata.id.desc()).first()

        if sc_row:
            _sc, solicitud = sc_row
            return _assignment_guard_from_solicitud(
                solicitud,
                matched_by="solicitudes_candidatas",
                has_active_assignment=True,
            )

        # Fallback controlado por compatibilidad legacy.
        fallback_query = _with_options_if_supported(
            Solicitud.query,
            *[
                opt
                for opt in (
                    _load_only_if_supported(
                        Solicitud,
                        "id",
                        "cliente_id",
                        "estado",
                        "candidata_id",
                    ),
                )
                if opt is not None
            ],
        ).filter(Solicitud.candidata_id == cand_id)
        if solicitud_id is not None:
            fallback_query = fallback_query.filter(Solicitud.id == int(solicitud_id))
        fallback = fallback_query.order_by(Solicitud.id.desc()).first()
        if fallback:
            return _assignment_guard_from_solicitud(
                fallback,
                matched_by="solicitud_candidata_id_fallback",
                has_active_assignment=False,
                emit_warning=True,
            )

        return CandidateAssignmentGuardResult(
            has_active_assignment=False,
            can_mark_working=False,
            can_charge=False,
            reason_code="no_active_assignment",
            reason_message="No existe una asignación activa coherente para esta candidata.",
            matched_by=None,
            solicitud_id=None,
            cliente_id=None,
        )
    except Exception as exc:
        _rollback_failed_session_if_needed()
        _guard_logger_exception(
            "Error validando contexto de asignación de candidata.",
            candidata_id=candidata_id,
            solicitud_id=solicitud_id,
        )
        return CandidateAssignmentGuardResult(
            has_active_assignment=False,
            can_mark_working=False,
            can_charge=False,
            reason_code="validation_error",
            reason_message=f"Error validando asignación: {exc}",
            matched_by=None,
            solicitud_id=int(solicitud_id) if solicitud_id else None,
            cliente_id=None,
        )
