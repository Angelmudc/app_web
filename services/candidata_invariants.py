# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy.orm import Query

from config_app import db
from models import Candidata, Solicitud, SolicitudCandidata
from services.candidata_assignment_guard import validate_candidata_assignment_context
from utils.timezone import iso_utc_z, utc_now_naive


_ACTIVE_ASSIGNMENT_STATUS = ("enviada", "vista", "seleccionada")
_ASSIGNMENT_CLOSEABLE_STATUS = ("sugerida", "enviada", "vista", "seleccionada")
_CANCEL_RELEASEABLE_STATUS = ("sugerida", "enviada", "vista", "seleccionada")
_ACTIVE_SOLICITUD_STATUS = ("proceso", "activa", "reemplazo", "espera_pago", "pagada")


@dataclass
class InvariantConflictError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


def _with_for_update_if_supported(query: Query | None) -> Query | None:
    if query is None or not hasattr(query, "with_for_update"):
        return query
    try:
        return query.with_for_update()
    except Exception:
        return query


def _lock_solicitud(solicitud_id: int) -> Solicitud | None:
    query = Solicitud.query.filter_by(id=int(solicitud_id))
    query = _with_for_update_if_supported(query)
    return query.first()


def _lock_candidata(candidata_id: int) -> Candidata | None:
    query = Candidata.query.filter_by(fila=int(candidata_id))
    query = _with_for_update_if_supported(query)
    if hasattr(query, "first"):
        return query.first()
    if hasattr(query, "first_or_404"):
        try:
            return query.first_or_404()
        except Exception:
            return None
    return None


def _lock_solicitud_candidata(sc_id: int) -> SolicitudCandidata | None:
    query = SolicitudCandidata.query.filter_by(id=int(sc_id))
    query = _with_for_update_if_supported(query)
    return query.first()


def _snapshot_to_dict(raw_value: object) -> dict:
    if isinstance(raw_value, dict):
        return dict(raw_value)
    if isinstance(raw_value, str):
        try:
            parsed = json.loads(raw_value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _snapshot_for_storage(snapshot: dict) -> object:
    if str(getattr(db.engine.dialect, "name", "") or "").strip().lower() == "postgresql":
        return snapshot
    return json.dumps(snapshot, ensure_ascii=False)


def candidate_has_active_assignment(*, candidata_id: int, exclude_solicitud_id: int | None = None) -> bool:
    if exclude_solicitud_id is not None:
        try:
            query = (
                db.session.query(SolicitudCandidata.id)
                .join(Solicitud, Solicitud.id == SolicitudCandidata.solicitud_id)
                .filter(
                    SolicitudCandidata.candidata_id == int(candidata_id),
                    SolicitudCandidata.status.in_(_ACTIVE_ASSIGNMENT_STATUS),
                    Solicitud.estado.in_(_ACTIVE_SOLICITUD_STATUS),
                    SolicitudCandidata.solicitud_id != int(exclude_solicitud_id),
                )
            )
            return query.first() is not None
        except Exception:
            return False

    result = validate_candidata_assignment_context(candidata_id=int(candidata_id))
    return bool(result.has_active_assignment)


def candidate_blocked_by_other_client(*, candidata_id: int, solicitud: Solicitud) -> bool:
    row = (
        db.session.query(SolicitudCandidata.id)
        .join(Solicitud, Solicitud.id == SolicitudCandidata.solicitud_id)
        .filter(
            SolicitudCandidata.candidata_id == int(candidata_id),
            SolicitudCandidata.status.in_(_ACTIVE_ASSIGNMENT_STATUS),
            SolicitudCandidata.solicitud_id != int(solicitud.id),
            Solicitud.cliente_id != int(getattr(solicitud, "cliente_id", 0) or 0),
            Solicitud.estado.in_(_ACTIVE_SOLICITUD_STATUS),
        )
        .first()
    )
    return row is not None


def sync_solicitud_candidatas_after_assignment(
    *,
    solicitud: Solicitud,
    assigned_candidata_id: int,
    actor: str = "",
) -> dict:
    now_iso = iso_utc_z(utc_now_naive())
    actor_value = (actor or "sistema")[:120]
    assigned_id = int(assigned_candidata_id)

    assigned_row = (
        SolicitudCandidata.query
        .filter_by(solicitud_id=int(solicitud.id), candidata_id=assigned_id)
        .first()
    )
    if assigned_row:
        assigned_row.status = "seleccionada"
        assigned_row.created_by = actor_value
    else:
        assigned_row = SolicitudCandidata(
            solicitud_id=int(solicitud.id),
            candidata_id=assigned_id,
            status="seleccionada",
            created_by=actor_value,
        )
        db.session.add(assigned_row)

    released_ids: list[int] = []
    rows = (
        SolicitudCandidata.query
        .filter_by(solicitud_id=int(solicitud.id))
        .all()
    )
    for row in rows:
        if int(getattr(row, "candidata_id", 0) or 0) == assigned_id:
            continue
        row_status = (getattr(row, "status", None) or "").strip().lower()
        if row_status not in _ASSIGNMENT_CLOSEABLE_STATUS:
            continue
        row.status = "liberada"
        snapshot = _snapshot_to_dict(row.breakdown_snapshot)
        snapshot["client_action"] = "liberada_por_asignacion"
        snapshot["client_action_at"] = now_iso
        snapshot["assigned_candidata_id"] = assigned_id
        row.breakdown_snapshot = _snapshot_for_storage(snapshot)
        try:
            released_ids.append(int(getattr(row, "candidata_id", 0) or 0))
        except Exception:
            continue

    return {
        "assigned_candidata_id": assigned_id,
        "released_ids": sorted({x for x in released_ids if int(x or 0) > 0}),
    }


def release_solicitud_candidatas_on_cancel(
    *,
    solicitud: Solicitud,
    actor: str = "",
    motivo: str = "",
) -> dict:
    now_iso = iso_utc_z(utc_now_naive())
    actor_value = (actor or "sistema")[:120]
    motivo_value = (motivo or "").strip()[:255]
    released_ids: list[int] = []

    rows = (
        SolicitudCandidata.query
        .filter(
            SolicitudCandidata.solicitud_id == int(solicitud.id),
            SolicitudCandidata.status.in_(_CANCEL_RELEASEABLE_STATUS),
        )
        .all()
    )
    for row in rows:
        row.status = "liberada"
        row.created_by = actor_value
        snapshot = _snapshot_to_dict(row.breakdown_snapshot)
        snapshot["client_action"] = "liberada_por_cancelacion_solicitud"
        snapshot["client_action_at"] = now_iso
        if motivo_value:
            snapshot["release_reason"] = motivo_value
        row.breakdown_snapshot = _snapshot_for_storage(snapshot)
        try:
            released_ids.append(int(getattr(row, "candidata_id", 0) or 0))
        except Exception:
            continue

    return {
        "released_count": len(released_ids),
        "candidata_ids": sorted({x for x in released_ids if int(x or 0) > 0}),
    }


def change_candidate_state(
    *,
    candidata_id: int,
    new_state: str,
    actor: str = "",
    nota_descalificacion: str | None = None,
    reason: str = "",
    candidata_obj: Candidata | None = None,
    enforce_working_requires_assignment: bool = True,
    exclude_solicitud_id_for_active_check: int | None = None,
) -> Candidata:
    cand = candidata_obj or _lock_candidata(int(candidata_id))
    if cand is None:
        raise InvariantConflictError("conflict", "La candidata ya no existe.")

    target = (new_state or "").strip().lower()
    if target not in {"descalificada", "lista_para_trabajar", "trabajando"}:
        raise InvariantConflictError("conflict", "Estado de candidata no soportado.")

    has_active_assignment = candidate_has_active_assignment(
        candidata_id=int(cand.fila),
        exclude_solicitud_id=(
            int(exclude_solicitud_id_for_active_check)
            if int(exclude_solicitud_id_for_active_check or 0) > 0
            else None
        ),
    )
    if target in {"descalificada", "lista_para_trabajar"} and has_active_assignment:
        raise InvariantConflictError(
            "conflict",
            "La candidata tiene una asignación activa. Libera/cierra la asignación antes de cambiar este estado.",
        )
    if target == "trabajando" and enforce_working_requires_assignment and not has_active_assignment:
        guard = validate_candidata_assignment_context(candidata_id=int(cand.fila))
        raise InvariantConflictError(
            "conflict",
            guard.reason_message or "No se puede marcar trabajando sin una asignación activa coherente.",
        )

    cand.estado = target
    cand.fecha_cambio_estado = utc_now_naive()
    cand.usuario_cambio_estado = (actor or "sistema")[:100]
    if target == "descalificada":
        cand.nota_descalificacion = (nota_descalificacion or reason or "").strip()[:500] or None
    elif target == "lista_para_trabajar":
        cand.nota_descalificacion = None
    return cand


def transition_solicitud_candidata_status(
    *,
    solicitud_id: int,
    sc_id: int,
    to_status: str,
    actor: str = "",
    reason: str = "",
) -> SolicitudCandidata:
    solicitud = _lock_solicitud(int(solicitud_id))
    sc = _lock_solicitud_candidata(int(sc_id))
    if solicitud is None or sc is None or int(getattr(sc, "solicitud_id", 0) or 0) != int(solicitud.id):
        raise InvariantConflictError("conflict", "Registro de candidata en solicitud no disponible.")

    current_status = (getattr(sc, "status", "") or "").strip().lower()
    target_status = (to_status or "").strip().lower()
    valid = {
        "vista": {"enviada"},
        "seleccionada": {"enviada", "vista"},
        "descartada": {"enviada", "vista", "seleccionada"},
    }
    if target_status not in valid:
        raise InvariantConflictError("conflict", "Transición de estado no permitida.")
    if current_status not in valid[target_status]:
        raise InvariantConflictError("conflict", "La candidata no está en un estado válido para esta acción.")

    if target_status == "seleccionada":
        if candidate_blocked_by_other_client(candidata_id=int(sc.candidata_id), solicitud=solicitud):
            raise InvariantConflictError(
                "blocked_other_client",
                "La candidata está bloqueada por otro cliente en una solicitud activa.",
            )

    sc.status = target_status
    snapshot = _snapshot_to_dict(sc.breakdown_snapshot)
    if target_status == "vista":
        snapshot["client_action"] = "vista"
    elif target_status == "seleccionada":
        snapshot["client_action"] = "solicitar_entrevista"
    elif target_status == "descartada":
        snapshot["client_action"] = "rechazada"
        if reason:
            snapshot["client_reason"] = reason[:500]
    snapshot["client_action_at"] = iso_utc_z(utc_now_naive())
    sc.breakdown_snapshot = _snapshot_for_storage(snapshot)
    sc.created_by = (actor or "cliente")[:120]
    return sc
