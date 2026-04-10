# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import joinedload

from config_app import db
from models import (
    Candidata,
    Solicitud,
    SolicitudRecommendationItem,
    SolicitudRecommendationRun,
    SolicitudRecommendationSelection,
)
from services.solicitud_recommendation_policy import SolicitudRecommendationPolicy
from services.solicitud_recommendation_presenter import present_shortlist_payload
from services.solicitud_recommendation_snapshot import (
    MODEL_VERSION,
    POLICY_VERSION,
    build_solicitud_fingerprint,
    run_is_stale,
)
from utils.matching_service import _score_candidate, candidate_query_prefilter
from utils.timezone import utc_now_naive


class SolicitudRecommendationService:
    def __init__(self, *, policy: SolicitudRecommendationPolicy | None = None):
        self.policy = policy or SolicitudRecommendationPolicy()

    def request_generation(
        self,
        solicitud_id: int,
        *,
        trigger_source: str = "manual",
        requested_by: str = "system",
        synchronous: bool = True,
        best_effort: bool = False,
        commit: bool = True,
    ) -> SolicitudRecommendationRun | None:
        solicitud = Solicitud.query.filter_by(id=int(solicitud_id)).first()
        if solicitud is None:
            return None

        fingerprint = build_solicitud_fingerprint(solicitud)
        self._deactivate_active_runs(solicitud_id=int(solicitud.id))

        run = SolicitudRecommendationRun(
            solicitud_id=int(solicitud.id),
            trigger_source=str(trigger_source or "manual")[:40],
            status="pending",
            fingerprint_hash=fingerprint,
            model_version=MODEL_VERSION,
            policy_version=POLICY_VERSION,
            requested_by=str(requested_by or "system")[:120],
            requested_at=utc_now_naive(),
            is_active=True,
        )
        db.session.add(run)
        db.session.flush()

        if not synchronous:
            if commit:
                db.session.commit()
            return run

        # Si vamos a ejecutar generación síncrona y con commit, persistimos primero
        # el RUN en estado pending para poder marcar error de forma consistente.
        if commit:
            db.session.commit()

        try:
            return self.generate_snapshot(run_id=int(run.id), commit=commit)
        except Exception:
            if best_effort:
                return self._mark_run_error(
                    run_id=int(run.id),
                    code="generation_error",
                    message="No se pudo generar snapshot de recomendaciones.",
                )
            raise

    def generate_snapshot(
        self,
        *,
        run_id: int | None = None,
        solicitud_id: int | None = None,
        commit: bool = True,
    ) -> SolicitudRecommendationRun | None:
        run: SolicitudRecommendationRun | None = None
        solicitud: Solicitud | None = None

        if run_id is not None:
            run = SolicitudRecommendationRun.query.filter_by(id=int(run_id)).first()
            if run is None:
                return None
            solicitud = Solicitud.query.filter_by(id=int(run.solicitud_id)).first()
        else:
            solicitud = Solicitud.query.filter_by(id=int(solicitud_id or 0)).first()
            if solicitud is None:
                return None
            run = self.request_generation(
                int(solicitud.id),
                trigger_source="generate_snapshot",
                requested_by="system",
                synchronous=False,
                commit=False,
            )

        if run is None or solicitud is None:
            return None

        try:
            now_value = utc_now_naive()
            run.status = "running"
            run.started_at = now_value
            run.error_code = None
            run.error_message = None
            run.failed_at = None

            pool = list(candidate_query_prefilter(solicitud))
            run.pool_size = int(len(pool))

            evaluated: list[dict[str, Any]] = []
            for cand in pool:
                if not isinstance(cand, Candidata):
                    continue
                score_row = _score_candidate(solicitud, cand)
                policy_row = self.policy.evaluate(solicitud=solicitud, candidata=cand, score_row=score_row)
                evaluated.append(
                    {
                        "candidata": cand,
                        "score_row": score_row,
                        "policy": policy_row,
                    }
                )

            eligible_rows = [row for row in evaluated if bool(row["policy"].get("is_eligible"))]
            eligible_rows.sort(
                key=lambda row: (
                    int(row["policy"].get("score_final") or 0),
                    int(row["policy"].get("score_operational") or 0),
                    int(getattr(row["candidata"], "fila", 0) or 0),
                ),
                reverse=True,
            )

            rank_by_candidata: dict[int, int] = {}
            for idx, row in enumerate(eligible_rows, start=1):
                cand_id = int(getattr(row["candidata"], "fila", 0) or 0)
                if cand_id > 0:
                    rank_by_candidata[cand_id] = idx

            (
                SolicitudRecommendationItem.query
                .filter(SolicitudRecommendationItem.run_id == int(run.id))
                .delete(synchronize_session=False)
            )

            hard_fail_count = 0
            soft_fail_count = 0
            for row in evaluated:
                cand = row["candidata"]
                policy_row = dict(row["policy"] or {})
                score_row = dict(row["score_row"] or {})
                cand_id = int(getattr(cand, "fila", 0) or 0)

                if policy_row.get("hard_fail"):
                    hard_fail_count += 1
                if policy_row.get("soft_fail_codes"):
                    soft_fail_count += 1

                db.session.add(
                    SolicitudRecommendationItem(
                        run_id=int(run.id),
                        solicitud_id=int(solicitud.id),
                        candidata_id=cand_id,
                        rank_position=rank_by_candidata.get(cand_id),
                        is_eligible=bool(policy_row.get("is_eligible")),
                        hard_fail=bool(policy_row.get("hard_fail")),
                        hard_fail_codes=list(policy_row.get("hard_fail_codes") or []),
                        hard_fail_reasons=list(policy_row.get("hard_fail_reasons") or []),
                        soft_fail_codes=list(policy_row.get("soft_fail_codes") or []),
                        soft_fail_reasons=list(policy_row.get("soft_fail_reasons") or []),
                        score_final=int(policy_row.get("score_final") or 0),
                        score_operational=int(policy_row.get("score_operational") or 0),
                        confidence_band=str(policy_row.get("confidence_band") or "baja"),
                        policy_snapshot=policy_row,
                        breakdown_snapshot=dict(score_row.get("breakdown_snapshot") or {}),
                        created_at=now_value,
                    )
                )

            run.items_count = int(len(evaluated))
            run.eligible_count = int(len(eligible_rows))
            run.hard_fail_count = int(hard_fail_count)
            run.soft_fail_count = int(soft_fail_count)
            run.status = "completed"
            run.completed_at = utc_now_naive()
            run.meta = {
                "pool_size": int(len(pool)),
                "evaluated_count": int(len(evaluated)),
                "eligible_count": int(len(eligible_rows)),
            }

            if commit:
                db.session.commit()
            else:
                db.session.flush()
            return run
        except Exception:
            db.session.rollback()
            return self._mark_run_error(
                run_id=int(run.id),
                code="generation_error",
                message="No se pudo generar snapshot de recomendaciones.",
            )

    def get_active_shortlist(
        self,
        solicitud_id: int,
        *,
        include_ineligible: bool = False,
    ) -> dict[str, Any]:
        solicitud = Solicitud.query.filter_by(id=int(solicitud_id)).first()
        if solicitud is None:
            return {
                "state": {"code": "error", "message": "Solicitud no encontrada.", "stale": False},
                "run": None,
                "items": [],
            }

        run = (
            SolicitudRecommendationRun.query
            .filter_by(solicitud_id=int(solicitud.id), is_active=True)
            .order_by(SolicitudRecommendationRun.requested_at.desc(), SolicitudRecommendationRun.id.desc())
            .first()
        )
        if run is None:
            return present_shortlist_payload(
                solicitud=solicitud,
                state_code="pending",
                run=None,
                items=[],
                stale=False,
                state_message="Shortlist pendiente de generación.",
            )

        status = str(getattr(run, "status", "") or "").strip().lower()
        if status in {"pending", "running"}:
            return present_shortlist_payload(
                solicitud=solicitud,
                state_code="pending",
                run=run,
                items=[],
                stale=False,
                state_message="Generación de shortlist en progreso.",
            )

        if status == "error":
            return present_shortlist_payload(
                solicitud=solicitud,
                state_code="error",
                run=run,
                items=[],
                stale=False,
                state_message="No se pudo generar shortlist.",
            )

        stale = run_is_stale(run_fingerprint=str(getattr(run, "fingerprint_hash", "") or ""), solicitud=solicitud)

        item_query = (
            SolicitudRecommendationItem.query
            .options(joinedload(SolicitudRecommendationItem.candidata))
            .filter_by(run_id=int(run.id), solicitud_id=int(solicitud.id))
        )
        if not include_ineligible:
            item_query = item_query.filter_by(is_eligible=True)

        items = (
            item_query
            .order_by(
                SolicitudRecommendationItem.rank_position.asc(),
                SolicitudRecommendationItem.score_final.desc(),
                SolicitudRecommendationItem.id.asc(),
            )
            .all()
        )

        state_code = "stale" if stale else "ready"
        message = "Snapshot desactualizado: requiere regeneración." if stale else "Shortlist disponible."
        return present_shortlist_payload(
            solicitud=solicitud,
            state_code=state_code,
            run=run,
            items=items,
            stale=stale,
            state_message=message,
        )

    def validate_client_selection(
        self,
        *,
        solicitud_id: int,
        candidata_id: int,
        selected_by: str = "cliente",
    ) -> dict[str, Any]:
        solicitud = Solicitud.query.filter_by(id=int(solicitud_id)).first()
        if solicitud is None:
            return {"ok": False, "code": "solicitud_not_found", "message": "Solicitud no encontrada."}

        shortlist = self.get_active_shortlist(int(solicitud.id), include_ineligible=True)
        run_payload = shortlist.get("run") or {}
        run_id = int(run_payload.get("run_id") or 0)
        state_code = str((shortlist.get("state") or {}).get("code") or "pending").strip().lower()

        if run_id <= 0 or state_code in {"pending", "error"}:
            return {
                "ok": False,
                "code": "shortlist_unavailable",
                "message": "Shortlist no disponible para validación.",
            }
        if state_code == "stale":
            return {
                "ok": False,
                "code": "shortlist_stale",
                "message": "Shortlist desactualizado; regenera antes de validar selección.",
            }

        item = (
            SolicitudRecommendationItem.query
            .filter_by(
                run_id=run_id,
                solicitud_id=int(solicitud.id),
                candidata_id=int(candidata_id),
            )
            .first()
        )

        if item is None:
            self._persist_selection(
                solicitud_id=int(solicitud.id),
                run_id=run_id,
                item_id=None,
                candidata_id=int(candidata_id),
                selected_by=selected_by,
                status="invalidated",
                validation_code="candidate_not_in_snapshot",
                validation_message="La candidata no está en el snapshot activo.",
            )
            return {
                "ok": False,
                "code": "candidate_not_in_snapshot",
                "message": "La candidata no está en el snapshot activo.",
            }

        is_valid = bool(getattr(item, "is_eligible", False)) and not bool(getattr(item, "hard_fail", False))
        code = "valid" if is_valid else "candidate_hard_failed"
        message = "Selección válida contra snapshot activo." if is_valid else "Selección inválida por hard fail vigente."

        self._persist_selection(
            solicitud_id=int(solicitud.id),
            run_id=run_id,
            item_id=int(getattr(item, "id", 0) or 0),
            candidata_id=int(candidata_id),
            selected_by=selected_by,
            status="valid" if is_valid else "invalidated",
            validation_code=code,
            validation_message=message,
        )

        return {
            "ok": is_valid,
            "code": code,
            "message": message,
            "run_id": run_id,
            "item_id": int(getattr(item, "id", 0) or 0),
            "hard_fail_codes": list(getattr(item, "hard_fail_codes", None) or []),
        }

    def _persist_selection(
        self,
        *,
        solicitud_id: int,
        run_id: int,
        item_id: int | None,
        candidata_id: int,
        selected_by: str,
        status: str,
        validation_code: str,
        validation_message: str,
    ) -> None:
        existing = (
            SolicitudRecommendationSelection.query
            .filter_by(
                solicitud_id=int(solicitud_id),
                run_id=int(run_id),
                candidata_id=int(candidata_id),
            )
            .first()
        )
        if existing is None:
            existing = SolicitudRecommendationSelection(
                solicitud_id=int(solicitud_id),
                run_id=int(run_id),
                recommendation_item_id=(int(item_id) if item_id else None),
                candidata_id=int(candidata_id),
                status=str(status or "pending_validation")[:30],
                validation_code=str(validation_code or "")[:80],
                validation_message=str(validation_message or "")[:300],
                validated_at=utc_now_naive(),
                selected_by=str(selected_by or "system")[:120],
                created_at=utc_now_naive(),
                meta={},
            )
            db.session.add(existing)
        else:
            existing.recommendation_item_id = int(item_id) if item_id else None
            existing.status = str(status or "pending_validation")[:30]
            existing.validation_code = str(validation_code or "")[:80]
            existing.validation_message = str(validation_message or "")[:300]
            existing.validated_at = utc_now_naive()
            existing.selected_by = str(selected_by or "system")[:120]
        db.session.commit()

    @staticmethod
    def _deactivate_active_runs(*, solicitud_id: int) -> None:
        (
            SolicitudRecommendationRun.query
            .filter(
                SolicitudRecommendationRun.solicitud_id == int(solicitud_id),
                SolicitudRecommendationRun.is_active.is_(True),
            )
            .update({"is_active": False}, synchronize_session=False)
        )

    @staticmethod
    def _mark_run_error(*, run_id: int, code: str, message: str) -> SolicitudRecommendationRun | None:
        run = SolicitudRecommendationRun.query.filter_by(id=int(run_id)).first()
        if run is None:
            return None

        run.status = "error"
        run.error_code = str(code or "generation_error")[:80]
        run.error_message = str(message or "Error generando snapshot.")[:500]
        run.failed_at = utc_now_naive()
        run.is_active = True
        db.session.commit()
        return run
