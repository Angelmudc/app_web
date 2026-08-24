# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from sqlalchemy.orm import load_only

from models import Solicitud, SolicitudCandidata
from services.candidata_assignment_guard import validate_candidata_assignment_context
from utils.candidata_completitud_audit import entrevista_ok
from utils.candidata_readiness import candidata_is_ready_to_send, candidata_referencias_complete
from utils.guards import candidata_esta_descalificada


MATERIAL_REQUIREMENT_LABELS = {
    "codigo": "Código interno",
    "referencias_laboral": "Referencia laboral",
    "referencias_familiares": "Referencia familiar",
    "entrevista": "Entrevista",
    "depuracion": "Depuración",
    "perfil": "Perfil",
    "cedula1": "Cédula frontal",
    "cedula2": "Cédula trasera",
}

_PROCESS_LABELS = {
    "en_proceso": "En proceso",
    "proceso_inscripcion": "Proceso de inscripción",
    "inscrita": "Inscrita",
    "inscrita_incompleta": "Proceso incompleto",
    "lista_para_trabajar": "Inscripción completa",
    "trabajando": "Inscripción completa",
    "descalificada": "Proceso detenido",
}


def _snapshot(candidata: Any, *, entrevistas_count: int, doc_flags: dict[str, bool], estado: str | None = None):
    return SimpleNamespace(
        estado=estado if estado is not None else getattr(candidata, "estado", None),
        codigo=getattr(candidata, "codigo", None),
        entrevista=getattr(candidata, "entrevista", None),
        referencias_laboral=getattr(candidata, "referencias_laboral", None),
        referencias_familiares=getattr(candidata, "referencias_familiares", None),
        contactos_referencias_laborales=getattr(candidata, "contactos_referencias_laborales", None),
        referencias_familiares_detalle=getattr(candidata, "referencias_familiares_detalle", None),
        depuracion=1 if doc_flags.get("depuracion") else None,
        perfil=1 if doc_flags.get("perfil") else None,
        cedula1=1 if doc_flags.get("cedula1") else None,
        cedula2=1 if doc_flags.get("cedula2") else None,
        entrevistas_nuevas=SimpleNamespace(count=(lambda n=int(entrevistas_count or 0): n)),
    )


def _active_assignment_summary(guard, *, candidata_id: int) -> dict[str, Any]:
    if not getattr(guard, "solicitud_id", None):
        return {
            "has_active_assignment": bool(getattr(guard, "has_active_assignment", False)),
            "can_mark_working": bool(getattr(guard, "can_mark_working", False)),
            "reason_code": getattr(guard, "reason_code", "") or "",
            "reason_message": getattr(guard, "reason_message", "") or "",
            "matched_by": getattr(guard, "matched_by", None),
            "solicitud": None,
        }

    solicitud = Solicitud.query.options(
        load_only(Solicitud.id, Solicitud.codigo_solicitud, Solicitud.estado, Solicitud.cliente_id)
    ).filter(Solicitud.id == int(guard.solicitud_id)).first()
    link = None
    if solicitud is not None:
        link = SolicitudCandidata.query.options(
            load_only(SolicitudCandidata.id, SolicitudCandidata.status)
        ).filter(
            SolicitudCandidata.solicitud_id == int(solicitud.id),
            SolicitudCandidata.candidata_id == int(candidata_id),
        ).order_by(SolicitudCandidata.id.desc()).first()

    return {
        "has_active_assignment": bool(getattr(guard, "has_active_assignment", False)),
        "can_mark_working": bool(getattr(guard, "can_mark_working", False)),
        "reason_code": getattr(guard, "reason_code", "") or "",
        "reason_message": getattr(guard, "reason_message", "") or "",
        "matched_by": getattr(guard, "matched_by", None),
        "solicitud": (
            {
                "id": int(solicitud.id),
                "codigo": solicitud.codigo_solicitud or f"#{solicitud.id}",
                "estado": solicitud.estado or "",
                "status": getattr(link, "status", None) or "",
            }
            if solicitud is not None
            else None
        ),
    }


def build_candidata_state_capabilities(
    candidata: Any,
    *,
    entrevistas_count: int,
    doc_flags: dict[str, bool],
) -> dict[str, Any]:
    estado = (getattr(candidata, "estado", None) or "").strip().lower()
    refs = candidata_referencias_complete(candidata)
    requirements = {
        "codigo": bool((getattr(candidata, "codigo", None) or "").strip()),
        "referencias_laboral": bool(refs.get("referencias_laboral")),
        "referencias_familiares": bool(refs.get("referencias_familiares")),
        "entrevista": entrevista_ok(getattr(candidata, "entrevista", None), int(entrevistas_count or 0)),
        "depuracion": bool(doc_flags.get("depuracion")),
        "perfil": bool(doc_flags.get("perfil")),
        "cedula1": bool(doc_flags.get("cedula1")),
        "cedula2": bool(doc_flags.get("cedula2")),
    }
    missing = [key for key, ok in requirements.items() if not ok]
    completed = len(requirements) - len(missing)

    material_snapshot = _snapshot(candidata, entrevistas_count=entrevistas_count, doc_flags=doc_flags, estado="inscrita")
    material_ready, material_reasons = candidata_is_ready_to_send(material_snapshot)
    current_snapshot = _snapshot(candidata, entrevistas_count=entrevistas_count, doc_flags=doc_flags)
    canonical_ready, canonical_reasons = candidata_is_ready_to_send(current_snapshot)

    guard = validate_candidata_assignment_context(candidata_id=int(getattr(candidata, "fila", 0) or 0))
    assignment = _active_assignment_summary(guard, candidata_id=int(getattr(candidata, "fila", 0) or 0))

    operational_blockers: list[str] = []
    if candidata_esta_descalificada(candidata):
        operational_blockers.append("Estado descalificada.")
    if estado == "trabajando":
        operational_blockers.append("Estado trabajando.")
    if estado not in {"inscrita", "lista_para_trabajar"}:
        if estado:
            operational_blockers.append(f"Estado base no permite envío: {estado}.")
        else:
            operational_blockers.append("Estado base no definido.")
    if assignment["has_active_assignment"]:
        operational_blockers.append("Tiene una asignación activa.")

    can_mark_ready = bool(
        canonical_ready
        and estado != "lista_para_trabajar"
        and not candidata_esta_descalificada(candidata)
        and estado != "trabajando"
        and not assignment["has_active_assignment"]
    )
    can_mark_working = bool(
        estado == "lista_para_trabajar"
        and not candidata_esta_descalificada(candidata)
        and assignment["can_mark_working"]
    )
    can_disqualify = bool(not candidata_esta_descalificada(candidata) and not assignment["has_active_assignment"])
    can_reactivate = bool(candidata_esta_descalificada(candidata) and not assignment["has_active_assignment"])

    reasons = {
        "can_mark_ready": [] if can_mark_ready else [],
        "can_mark_working": [] if can_mark_working else [],
        "can_disqualify": [] if can_disqualify else [],
        "can_reactivate": [] if can_reactivate else [],
    }
    if not can_mark_ready:
        if not canonical_ready:
            reasons["can_mark_ready"].extend([r for r in canonical_reasons if not str(r).lower().startswith("advertencia:")])
        if assignment["has_active_assignment"]:
            reasons["can_mark_ready"].append("Libera/cierra la asignación activa antes de cambiar este estado.")
        if estado == "lista_para_trabajar":
            reasons["can_mark_ready"].append("Ya está lista para trabajar.")
    if not can_mark_working:
        if estado != "lista_para_trabajar":
            reasons["can_mark_working"].append("Debe estar lista para trabajar.")
        if not assignment["can_mark_working"]:
            reasons["can_mark_working"].append(assignment["reason_message"] or "No existe una asignación activa válida.")
    if not can_disqualify:
        if candidata_esta_descalificada(candidata):
            reasons["can_disqualify"].append("Ya está descalificada.")
        if assignment["has_active_assignment"]:
            reasons["can_disqualify"].append("Libera/cierra la asignación activa antes de descalificar.")
    if not can_reactivate:
        if not candidata_esta_descalificada(candidata):
            reasons["can_reactivate"].append("No está descalificada.")
        if assignment["has_active_assignment"]:
            reasons["can_reactivate"].append("Libera/cierra la asignación activa antes de reactivar.")

    situacion = "Descalificada" if candidata_esta_descalificada(candidata) else ("Trabajando" if estado == "trabajando" else ("Lista para trabajar" if estado == "lista_para_trabajar" else "No disponible para enviar"))
    return {
        "estado": estado,
        "process": {
            "label": _PROCESS_LABELS.get(estado, estado or "Sin estado"),
            "inscripcion": bool(getattr(candidata, "inscripcion", False)),
        },
        "preparation": {
            "material_ready": bool(material_ready),
            "canonical_ready": bool(canonical_ready),
            "completed": completed,
            "total": len(requirements),
            "label": f"{completed}/{len(requirements)}",
            "requirements": requirements,
            "labels": MATERIAL_REQUIREMENT_LABELS,
            "missing": missing,
            "material_reasons": list(material_reasons or []),
            "canonical_reasons": list(canonical_reasons or []),
            "operational_blockers": operational_blockers,
        },
        "assignment": assignment,
        "situation": {
            "label": situacion,
            "descalificada": bool(candidata_esta_descalificada(candidata)),
            "trabajando": estado == "trabajando",
            "nota_descalificacion": getattr(candidata, "nota_descalificacion", None) or "",
        },
        "actions": {
            "can_mark_ready": can_mark_ready,
            "can_mark_working": can_mark_working,
            "can_disqualify": can_disqualify,
            "can_reactivate": can_reactivate,
        },
        "reasons": reasons,
    }
