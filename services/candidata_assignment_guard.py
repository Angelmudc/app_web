# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from flask import current_app

from config_app import db
from models import Solicitud, SolicitudCandidata

_ACTIVE_ASSIGNMENT_STATUS = ("enviada", "vista", "seleccionada")
_WORKING_ALLOWED_STATUS = {"proceso", "activa", "reemplazo", "espera_pago", "pagada"}
_CHARGE_ALLOWED_STATUS = {"activa", "espera_pago", "pagada"}
_BLOCKED_STATUS = {"cancelada", "pendiente_servicio", "finalizada", "cerrada"}


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

        sc_query = (
            db.session.query(SolicitudCandidata, Solicitud)
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
            estado = str(getattr(solicitud, "estado", "") or "").strip().lower()
            if estado in _BLOCKED_STATUS:
                return CandidateAssignmentGuardResult(
                    has_active_assignment=True,
                    can_mark_working=False,
                    can_charge=False,
                    reason_code="solicitud_state_blocked",
                    reason_message=f"La solicitud está en estado '{estado}' y no permite operación financiera/operativa.",
                    matched_by="solicitudes_candidatas",
                    solicitud_id=int(getattr(solicitud, "id", 0) or 0) or None,
                    cliente_id=int(getattr(solicitud, "cliente_id", 0) or 0) or None,
                )
            return _ok_result(
                matched_by="solicitudes_candidatas",
                solicitud=solicitud,
                can_mark_working=estado in _WORKING_ALLOWED_STATUS,
                can_charge=estado in _CHARGE_ALLOWED_STATUS,
            )

        # Fallback controlado por compatibilidad legacy.
        fallback_query = Solicitud.query.filter(Solicitud.candidata_id == cand_id)
        if solicitud_id is not None:
            fallback_query = fallback_query.filter(Solicitud.id == int(solicitud_id))
        fallback = fallback_query.order_by(Solicitud.id.desc()).first()
        if fallback:
            estado = str(getattr(fallback, "estado", "") or "").strip().lower()
            if estado in _WORKING_ALLOWED_STATUS:
                _guard_logger_warning(
                    "Inconsistencia detectada: solicitud.candidata_id sin fila en solicitudes_candidatas.",
                    candidata_id=cand_id,
                    solicitud_id=int(getattr(fallback, "id", 0) or 0),
                    estado=estado,
                    matched_by="solicitud_candidata_id_fallback",
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
                    matched_by="solicitud_candidata_id_fallback",
                    solicitud_id=int(getattr(fallback, "id", 0) or 0) or None,
                    cliente_id=int(getattr(fallback, "cliente_id", 0) or 0) or None,
                )

            return CandidateAssignmentGuardResult(
                has_active_assignment=False,
                can_mark_working=False,
                can_charge=False,
                reason_code="fallback_state_not_operable",
                reason_message=f"Existe vínculo fallback pero el estado '{estado or 'desconocido'}' no es operable.",
                matched_by="solicitud_candidata_id_fallback",
                solicitud_id=int(getattr(fallback, "id", 0) or 0) or None,
                cliente_id=int(getattr(fallback, "cliente_id", 0) or 0) or None,
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
