# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import os
import threading
import time
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from flask import current_app, has_app_context
from sqlalchemy.orm import joinedload

from config_app import cache, db
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
from utils.enterprise_layer import bump_operational_counter
from utils.matching_service import DEFAULT_PREFILTER_LIMIT, _score_candidate, candidate_query_prefilter
from utils.timezone import utc_now_naive

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, *, min_value: int | None = None, max_value: int | None = None) -> int:
    raw = str(os.getenv(name, str(default)) or str(default)).strip()
    try:
        value = int(raw)
    except Exception:
        value = int(default)
    if min_value is not None:
        value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value


_ASYNC_WORKERS = _env_int("SOL_REC_ASYNC_WORKERS", 2, min_value=1, max_value=8)
_ASYNC_EXECUTOR = ThreadPoolExecutor(max_workers=_ASYNC_WORKERS, thread_name_prefix="sol-rec")
_ASYNC_IN_FLIGHT: set[int] = set()
_ASYNC_LOCK = threading.Lock()

_DTO_CACHE_TTL_SECONDS = _env_int("SOL_REC_DTO_CACHE_TTL_SECONDS", 120, min_value=20, max_value=900)
_RECOVERY_TIMEOUT_SECONDS = _env_int("SOL_REC_RECOVERY_TIMEOUT_SECONDS", 120, min_value=30, max_value=900)
_RECOVERY_COOLDOWN_SECONDS = _env_int("SOL_REC_RECOVERY_COOLDOWN_SECONDS", 30, min_value=10, max_value=300)
_AUTO_RETRY_COOLDOWN_SECONDS = _env_int("SOL_REC_AUTO_RETRY_COOLDOWN_SECONDS", 120, min_value=30, max_value=1800)
_AUTO_RETRY_MAX_ATTEMPTS = _env_int("SOL_REC_AUTO_RETRY_MAX_ATTEMPTS", 2, min_value=0, max_value=8)
_PREFILTER_LIMIT = _env_int(
    "SOL_REC_PREFILTER_LIMIT",
    DEFAULT_PREFILTER_LIMIT,
    min_value=25,
    max_value=DEFAULT_PREFILTER_LIMIT,
)


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
        dispatch_async: bool = True,
    ) -> SolicitudRecommendationRun | None:
        solicitud = Solicitud.query.filter_by(id=int(solicitud_id)).first()
        if solicitud is None:
            return None

        fingerprint = build_solicitud_fingerprint(solicitud)
        self._deactivate_active_runs(solicitud_id=int(solicitud.id), requested_at=utc_now_naive())

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
        self._obs_counter("rec:gen:triggered_count")
        self._obs_event(
            "generation_triggered",
            solicitud_id=int(solicitud.id),
            run_id=int(getattr(run, "id", 0) or 0),
            trigger_source=str(trigger_source or "manual"),
            requested_by=str(requested_by or "system"),
            synchronous=bool(synchronous),
        )

        if not synchronous:
            if commit:
                db.session.commit()
            if dispatch_async:
                self._dispatch_async_run(int(getattr(run, "id", 0) or 0))
            return run

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
            if not self._claim_pending_run(run_id=int(run.id)):
                db.session.expire_all()
                return SolicitudRecommendationRun.query.filter_by(id=int(run.id)).first()
        else:
            solicitud = Solicitud.query.filter_by(id=int(solicitud_id or 0)).first()
            if solicitud is None:
                return None
            run = self.request_generation(
                int(solicitud.id),
                trigger_source="generate_snapshot",
                requested_by="system",
                synchronous=False,
                commit=commit,
                dispatch_async=False,
            )
            if run is None:
                return None
            if not self._claim_pending_run(run_id=int(run.id)):
                db.session.expire_all()
                return SolicitudRecommendationRun.query.filter_by(id=int(run.id)).first()

        run = SolicitudRecommendationRun.query.filter_by(id=int(getattr(run, "id", 0) or 0)).first()
        if run is None:
            return None

        solicitud = Solicitud.query.filter_by(id=int(run.solicitud_id)).first()
        if solicitud is None:
            return self._mark_run_error(
                run_id=int(run.id),
                code="solicitud_not_found",
                message="La solicitud asociada no existe.",
            )

        try:
            now_value = utc_now_naive()
            run.started_at = run.started_at or now_value
            run.error_code = None
            run.error_message = None
            run.failed_at = None

            pool = list(candidate_query_prefilter(solicitud))
            if len(pool) > _PREFILTER_LIMIT:
                pool = pool[:_PREFILTER_LIMIT]
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
                "prefilter_limit": int(_PREFILTER_LIMIT),
            }
            latency_ms = self._latency_ms(
                requested_at=getattr(run, "requested_at", None),
                completed_at=getattr(run, "completed_at", None),
            )
            state_bucket = "ready" if int(run.eligible_count or 0) > 0 else "empty"
            self._obs_counter(f"rec:gen:{state_bucket}_count")
            self._obs_counter("rec:gen:latency_ready_count")
            self._obs_counter("rec:gen:latency_ready_sum_ms", delta=int(latency_ms))
            self._obs_event(
                "generation_completed",
                solicitud_id=int(getattr(solicitud, "id", 0) or 0),
                run_id=int(getattr(run, "id", 0) or 0),
                outcome=state_bucket,
                pool_size=int(run.pool_size or 0),
                eligible_count=int(run.eligible_count or 0),
                items_count=int(run.items_count or 0),
                latency_ms=int(latency_ms),
            )

            if commit:
                db.session.commit()
            else:
                db.session.flush()
            return run
        except Exception:
            db.session.rollback()
            logger.exception(
                "solicitud_recommendation.generate_snapshot_failed run_id=%s solicitud_id=%s",
                int(getattr(run, "id", 0) or 0),
                int(getattr(run, "solicitud_id", 0) or 0),
            )
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

        fingerprint = build_solicitud_fingerprint(solicitud)
        run = self._active_run_for_solicitud(int(solicitud.id))

        if run is None:
            self._schedule_generation_if_allowed(
                solicitud=solicitud,
                fingerprint=fingerprint,
                reason="missing",
            )
            payload = present_shortlist_payload(
                solicitud=solicitud,
                state_code="pending",
                run=None,
                items=[],
                stale=False,
                state_message="Shortlist pendiente de generación.",
            )
            self._attach_poll_hints(payload, state_code="pending")
            return payload

        status = str(getattr(run, "status", "") or "").strip().lower()
        if status in {"pending", "running"}:
            self._recover_pending_run_if_needed(run=run, solicitud=solicitud, fingerprint=fingerprint)
            payload = present_shortlist_payload(
                solicitud=solicitud,
                state_code="pending",
                run=run,
                items=[],
                stale=False,
                state_message="Generación de shortlist en progreso.",
            )
            self._attach_poll_hints(payload, state_code="pending")
            return payload

        if status == "error":
            retried = self._schedule_generation_if_allowed(
                solicitud=solicitud,
                fingerprint=fingerprint,
                reason="error",
            )
            if retried:
                payload = present_shortlist_payload(
                    solicitud=solicitud,
                    state_code="pending_refresh",
                    run=None,
                    items=[],
                    stale=False,
                    state_message="Tuvimos un error y estamos reintentando en segundo plano.",
                )
                self._attach_poll_hints(payload, state_code="pending_refresh")
                return payload
            payload = present_shortlist_payload(
                solicitud=solicitud,
                state_code="error",
                run=run,
                items=[],
                stale=False,
                state_message="No se pudo generar shortlist.",
            )
            self._attach_poll_hints(payload, state_code="error")
            return payload

        stale = run_is_stale(run_fingerprint=str(getattr(run, "fingerprint_hash", "") or ""), solicitud=solicitud)
        if stale:
            self._obs_counter_once(
                name="rec:quality:stale_count",
                once_key=f"solrec:obs:stale:run:{int(getattr(run, 'id', 0) or 0)}",
                timeout=3600,
            )
            refreshed = self._schedule_generation_if_allowed(
                solicitud=solicitud,
                fingerprint=fingerprint,
                reason="stale",
            )
            if refreshed:
                payload = present_shortlist_payload(
                    solicitud=solicitud,
                    state_code="pending_refresh",
                    run=run,
                    items=[],
                    stale=True,
                    state_message="Shortlist desactualizado: estamos actualizándolo.",
                )
                self._attach_poll_hints(payload, state_code="pending_refresh")
                return payload

        cache_key = self._dto_cache_key(
            solicitud_id=int(solicitud.id),
            run=run,
            fingerprint=fingerprint,
            include_ineligible=include_ineligible,
            stale=stale,
        )
        cached_payload = self._cache_get(cache_key)
        if isinstance(cached_payload, dict):
            return cached_payload

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
        payload = present_shortlist_payload(
            solicitud=solicitud,
            state_code=state_code,
            run=run,
            items=items,
            stale=stale,
            state_message=message,
        )
        self._attach_poll_hints(payload, state_code=state_code)

        if state_code in {"ready", "stale"}:
            self._cache_set(cache_key, payload, timeout=_DTO_CACHE_TTL_SECONDS)

        return payload

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

        if run_id <= 0 or state_code in {"pending", "pending_refresh", "error"}:
            self._obs_counter("rec:quality:selection_revalidation_fail_count")
            self._obs_event(
                "selection_revalidation_failed",
                solicitud_id=int(solicitud.id),
                candidata_id=int(candidata_id),
                code="shortlist_unavailable",
                state_code=state_code,
            )
            return {
                "ok": False,
                "code": "shortlist_unavailable",
                "message": "Shortlist no disponible para validación.",
            }
        if state_code == "stale":
            self._obs_counter("rec:quality:selection_revalidation_fail_count")
            self._obs_event(
                "selection_revalidation_failed",
                solicitud_id=int(solicitud.id),
                candidata_id=int(candidata_id),
                code="shortlist_stale",
                state_code=state_code,
            )
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
            self._obs_counter("rec:quality:selection_revalidation_fail_count")
            self._obs_event(
                "selection_revalidation_failed",
                solicitud_id=int(solicitud.id),
                run_id=int(run_id),
                candidata_id=int(candidata_id),
                code="candidate_not_in_snapshot",
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
        if not is_valid:
            self._obs_counter("rec:quality:selection_revalidation_fail_count")
            self._obs_event(
                "selection_revalidation_failed",
                solicitud_id=int(solicitud.id),
                run_id=int(run_id),
                candidata_id=int(candidata_id),
                code=code,
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
    def _active_run_for_solicitud(solicitud_id: int) -> SolicitudRecommendationRun | None:
        return (
            SolicitudRecommendationRun.query
            .filter_by(solicitud_id=int(solicitud_id), is_active=True)
            .order_by(SolicitudRecommendationRun.requested_at.desc(), SolicitudRecommendationRun.id.desc())
            .first()
        )

    def _deactivate_active_runs(self, *, solicitud_id: int, requested_at: datetime | None = None) -> None:
        active_runs = (
            SolicitudRecommendationRun.query
            .filter(
                SolicitudRecommendationRun.solicitud_id == int(solicitud_id),
                SolicitudRecommendationRun.is_active.is_(True),
            )
            .all()
        )
        for old_run in active_runs:
            old_run.is_active = False
            if str(getattr(old_run, "status", "") or "").strip().lower() != "completed":
                continue
            has_valid_selection = (
                SolicitudRecommendationSelection.query
                .filter_by(run_id=int(getattr(old_run, "id", 0) or 0), status="valid")
                .first()
                is not None
            )
            if has_valid_selection:
                continue
            req_at = requested_at or utc_now_naive()
            comp_at = getattr(old_run, "completed_at", None)
            if comp_at is None:
                continue
            if self._age_seconds(comp_at, req_at) > 172800:
                continue
            self._obs_counter("rec:quality:unused_recommendation_count")
            self._obs_event(
                "recommendation_unused_superseded",
                solicitud_id=int(getattr(old_run, "solicitud_id", 0) or 0),
                run_id=int(getattr(old_run, "id", 0) or 0),
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
        SolicitudRecommendationService._obs_counter("rec:gen:error_count")
        SolicitudRecommendationService._obs_event(
            "generation_error",
            run_id=int(getattr(run, "id", 0) or 0),
            solicitud_id=int(getattr(run, "solicitud_id", 0) or 0),
            error_code=str(run.error_code or ""),
        )
        return run

    @staticmethod
    def _claim_pending_run(*, run_id: int) -> bool:
        now_value = utc_now_naive()
        rows = (
            SolicitudRecommendationRun.query
            .filter(
                SolicitudRecommendationRun.id == int(run_id),
                SolicitudRecommendationRun.status == "pending",
            )
            .update(
                {
                    "status": "running",
                    "started_at": now_value,
                    "error_code": None,
                    "error_message": None,
                    "failed_at": None,
                },
                synchronize_session=False,
            )
        )
        if int(rows or 0) > 0:
            db.session.flush()
            return True

        existing = SolicitudRecommendationRun.query.filter_by(id=int(run_id)).first()
        return bool(existing and str(getattr(existing, "status", "") or "").strip().lower() == "running")

    @staticmethod
    def _dto_cache_key(*, solicitud_id: int, run, fingerprint: str, include_ineligible: bool, stale: bool) -> str:
        return (
            f"solrec:dto:v2:sol:{int(solicitud_id)}:run:{int(getattr(run, 'id', 0) or 0)}"
            f":st:{str(getattr(run, 'status', '') or '')[:12]}:fp:{str(fingerprint or '')[:20]}"
            f":elig:{1 if include_ineligible else 0}:stale:{1 if stale else 0}"
        )

    @staticmethod
    def _cache_get(key: str):
        try:
            return cache.get(key)
        except Exception:
            return None

    @staticmethod
    def _cache_set(key: str, value: Any, *, timeout: int) -> None:
        try:
            cache.set(key, value, timeout=int(timeout))
        except Exception:
            return None

    @staticmethod
    def _obs_counter(name: str, *, delta: int = 1) -> int:
        try:
            return int(bump_operational_counter(name, delta=int(delta)))
        except Exception:
            return 0

    @staticmethod
    def _obs_counter_once(*, name: str, once_key: str, timeout: int = 300) -> int:
        marker = SolicitudRecommendationService._cache_get(str(once_key or "").strip())
        if marker:
            return 0
        SolicitudRecommendationService._cache_set(str(once_key or "").strip(), 1, timeout=max(60, int(timeout)))
        return SolicitudRecommendationService._obs_counter(str(name or "").strip(), delta=1)

    @staticmethod
    def _obs_event(event: str, **fields: Any) -> None:
        try:
            payload = {
                "component": "solicitud_recommendation",
                "event": str(event or "").strip().lower()[:80],
                "ts": utc_now_naive().isoformat(),
            }
            for key, value in (fields or {}).items():
                if value is None:
                    continue
                payload[str(key)[:60]] = value
            logger.info("[solrec-obs] %s", json.dumps(payload, ensure_ascii=True, sort_keys=True))
        except Exception:
            return

    @staticmethod
    def _latency_ms(*, requested_at: datetime | None, completed_at: datetime | None) -> int:
        if requested_at is None or completed_at is None:
            return 0
        try:
            return max(0, int((completed_at - requested_at).total_seconds() * 1000))
        except Exception:
            return 0

    def _recover_pending_run_if_needed(self, *, run, solicitud, fingerprint: str) -> None:
        status = str(getattr(run, "status", "") or "").strip().lower()
        now_dt = utc_now_naive()

        if status == "running":
            started_at = getattr(run, "started_at", None) or getattr(run, "requested_at", None)
            if self._age_seconds(started_at, now_dt) > _RECOVERY_TIMEOUT_SECONDS:
                self._mark_run_error(
                    run_id=int(getattr(run, "id", 0) or 0),
                    code="generation_timeout",
                    message="La generación tardó más de lo esperado.",
                )
                self._schedule_generation_if_allowed(
                    solicitud=solicitud,
                    fingerprint=fingerprint,
                    reason="timeout",
                )
            return

        if status != "pending":
            return

        requested_at = getattr(run, "requested_at", None)
        if self._age_seconds(requested_at, now_dt) <= _RECOVERY_TIMEOUT_SECONDS:
            return

        throttle_key = f"solrec:recovery:kick:run:{int(getattr(run, 'id', 0) or 0)}"
        if self._cache_get(throttle_key):
            return
        self._cache_set(throttle_key, 1, timeout=_RECOVERY_COOLDOWN_SECONDS)
        self._dispatch_async_run(int(getattr(run, "id", 0) or 0))

    def _schedule_generation_if_allowed(self, *, solicitud, fingerprint: str, reason: str) -> bool:
        if _AUTO_RETRY_MAX_ATTEMPTS <= 0:
            return False

        meta_key = f"solrec:auto:v1:sol:{int(getattr(solicitud, 'id', 0) or 0)}:fp:{str(fingerprint or '')[:20]}"
        now_ts = int(time.time())
        meta = self._cache_get(meta_key)
        if not isinstance(meta, dict):
            meta = {"count": 0, "next_ts": 0}

        count = int(meta.get("count") or 0)
        next_ts = int(meta.get("next_ts") or 0)
        if count >= _AUTO_RETRY_MAX_ATTEMPTS or now_ts < next_ts:
            return False

        run = self.request_generation(
            int(getattr(solicitud, "id", 0) or 0),
            trigger_source=f"auto_{str(reason or 'refresh')[:20]}",
            requested_by="system:auto",
            synchronous=False,
            best_effort=True,
            commit=True,
            dispatch_async=True,
        )
        if run is None:
            return False

        meta["count"] = count + 1
        meta["next_ts"] = now_ts + _AUTO_RETRY_COOLDOWN_SECONDS
        self._cache_set(
            meta_key,
            meta,
            timeout=max(_AUTO_RETRY_COOLDOWN_SECONDS * (_AUTO_RETRY_MAX_ATTEMPTS + 1), 3600),
        )
        return True

    @staticmethod
    def _age_seconds(value: datetime | None, now_dt: datetime) -> int:
        if value is None:
            return 0
        try:
            return int((now_dt - value).total_seconds())
        except Exception:
            return 0

    @staticmethod
    def _attach_poll_hints(payload: dict[str, Any], *, state_code: str) -> None:
        state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
        state["polling"] = {
            "enabled": state_code in {"pending", "pending_refresh", "stale"},
            "backoff_base_ms": 4000,
            "max_attempts": 6,
        }
        payload["state"] = state

    @staticmethod
    def _dispatch_async_run(run_id: int) -> bool:
        run_id = int(run_id or 0)
        if run_id <= 0:
            return False

        app = current_app._get_current_object() if has_app_context() else None
        if app is None:
            return False

        with _ASYNC_LOCK:
            if run_id in _ASYNC_IN_FLIGHT:
                return False
            _ASYNC_IN_FLIGHT.add(run_id)

        def _worker(target_run_id: int):
            try:
                with app.app_context():
                    SolicitudRecommendationService().generate_snapshot(run_id=int(target_run_id), commit=True)
            except Exception:
                logger.exception("solicitud_recommendation.async_failed run_id=%s", int(target_run_id))
            finally:
                with _ASYNC_LOCK:
                    _ASYNC_IN_FLIGHT.discard(int(target_run_id))

        try:
            _ASYNC_EXECUTOR.submit(_worker, run_id)
            return True
        except Exception:
            with _ASYNC_LOCK:
                _ASYNC_IN_FLIGHT.discard(run_id)
            logger.exception("solicitud_recommendation.async_submit_failed run_id=%s", run_id)
            return False
