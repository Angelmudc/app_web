# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

from services.candidata_invariants import (
    candidate_blocked_by_other_client,
    candidate_has_active_assignment,
)
from utils.candidata_readiness import candidata_is_ready_to_send


class SolicitudRecommendationPolicy:
    version = "policy-v1"

    def evaluate(self, *, solicitud, candidata, score_row: dict[str, Any]) -> dict[str, Any]:
        hard_fail_codes: list[str] = []
        hard_fail_reasons: list[str] = []
        soft_fail_codes: list[str] = []
        soft_fail_reasons: list[str] = []

        estado = str(getattr(candidata, "estado", "") or "").strip().lower()
        if estado in {"descalificada", "en_proceso", "proceso_inscripcion", "trabajando"}:
            hard_fail_codes.append("candidate_not_operable")
            hard_fail_reasons.append("Candidata fuera de circulación operativa por estado.")
        elif estado != "lista_para_trabajar":
            hard_fail_codes.append("candidate_not_ready_list")
            hard_fail_reasons.append("Candidata no está en estado lista_para_trabajar.")

        ready_ok, ready_reasons = candidata_is_ready_to_send(candidata)
        if not ready_ok:
            hard_fail_codes.append("insufficient_readiness")
            reason_txt = "; ".join([str(x) for x in (ready_reasons or [])][:3]) or "Readiness insuficiente"
            hard_fail_reasons.append(f"Readiness insuficiente: {reason_txt}")

        if candidate_blocked_by_other_client(candidata_id=int(candidata.fila), solicitud=solicitud):
            hard_fail_codes.append("blocked_other_client")
            hard_fail_reasons.append("Candidata bloqueada por otra solicitud activa de otro cliente.")

        # Conflicto operativo real: solo asignaciones activas en estados vigentes.
        if candidate_has_active_assignment(
            candidata_id=int(candidata.fila),
            exclude_solicitud_id=int(getattr(solicitud, "id", 0) or 0),
        ):
            hard_fail_codes.append("active_operational_conflict")
            hard_fail_reasons.append("Conflicto operativo activo: candidata ya comprometida en otra solicitud vigente.")

        breakdown_snapshot = dict(score_row.get("breakdown_snapshot") or {})
        modalidad_match = bool(breakdown_snapshot.get("modalidad_match"))
        if not modalidad_match:
            hard_fail_codes.append("modalidad_incompatible")
            hard_fail_reasons.append("Modalidad incompatible con la solicitud.")

        edad_match = breakdown_snapshot.get("edad_match")
        if edad_match is False:
            soft_fail_codes.append("edad_mismatch")
            soft_fail_reasons.append("Edad fuera del rango solicitado.")

        risks = [str(x).strip() for x in (score_row.get("risks") or []) if str(x).strip()]
        if risks:
            soft_fail_codes.append("operational_risks")
            soft_fail_reasons.append("Riesgos operativos detectados.")

        hard_fail_codes = sorted(set(hard_fail_codes))
        soft_fail_codes = sorted(set(soft_fail_codes))

        score_final = int(score_row.get("score") or 0)
        score_operational = int(score_row.get("operational_score") or 0)
        confidence_band = self._confidence_band(
            score_final=score_final,
            has_hard_fail=bool(hard_fail_codes),
            soft_fail_count=len(soft_fail_codes),
        )

        return {
            "is_eligible": not bool(hard_fail_codes),
            "hard_fail": bool(hard_fail_codes),
            "hard_fail_codes": hard_fail_codes,
            "hard_fail_reasons": hard_fail_reasons,
            "soft_fail_codes": soft_fail_codes,
            "soft_fail_reasons": soft_fail_reasons,
            "score_final": max(0, min(100, score_final)),
            "score_operational": max(0, min(100, score_operational)),
            "confidence_band": confidence_band,
            "policy_version": self.version,
            "ready_reasons": ready_reasons,
            "modalidad_match": modalidad_match,
            "edad_match": edad_match,
        }

    @staticmethod
    def _confidence_band(*, score_final: int, has_hard_fail: bool, soft_fail_count: int) -> str:
        if has_hard_fail:
            return "baja"
        if score_final >= 80 and soft_fail_count == 0:
            return "alta"
        if score_final >= 60:
            return "media"
        return "baja"
