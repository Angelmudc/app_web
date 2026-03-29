# -*- coding: utf-8 -*-
from __future__ import annotations

from flask import current_app, jsonify

from config_app import db
from decorators import roles_required
from services.candidata_invariants import InvariantConflictError, change_candidate_state as invariant_change_candidate_state
from utils.timezone import utc_now_naive

from core import legacy_handlers as legacy_h


@roles_required("admin", "secretaria")
def auto_actualizar_estados():
    """
    Revisa candidatas en 'inscrita_incompleta' y promueve a 'lista_para_trabajar'
    si ya tienen todos los documentos/datos requeridos.
    """
    try:
        pendientes = legacy_h.Candidata.query.filter_by(estado="inscrita_incompleta").all()
        actualizadas = []

        for c in pendientes:
            if (
                c.codigo
                and c.entrevista
                and c.referencias_laboral
                and c.referencias_familiares
                and c.perfil
                and c.cedula1
                and c.cedula2
                and c.depuracion
            ):
                try:
                    invariant_change_candidate_state(
                        candidata_id=int(c.fila),
                        new_state="lista_para_trabajar",
                        actor="sistema",
                        reason="auto_completitud",
                        candidata_obj=c,
                    )
                    c.fecha_cambio_estado = utc_now_naive()
                    c.usuario_cambio_estado = "sistema"
                    actualizadas.append(c.fila)
                except InvariantConflictError:
                    # Si está ocupada/activa en otra transición, no forzamos autopromoción.
                    continue

        if actualizadas:
            db.session.commit()

        return jsonify({"conteo_actualizadas": len(actualizadas), "filas_actualizadas": actualizadas})
    except Exception:
        db.session.rollback()
        current_app.logger.exception("❌ Error auto_actualizando estados")
        return jsonify({"error": "No se pudo actualizar estados automáticamente"}), 500
