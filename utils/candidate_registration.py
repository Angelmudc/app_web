# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from flask import request, session
from sqlalchemy.exc import OperationalError

from config_app import db
from models import Candidata
from utils.audit_entity import log_candidata_action
from utils.audit_logger import log_action
from utils.robust_save import RobustSaveResult, execute_robust_save


def normalize_person_name(raw: str | None) -> str:
    return " ".join((raw or "").strip().split())[:150]


def normalize_phone(raw: str | None) -> str:
    return " ".join((raw or "").strip().split())[:30]


def phone_has_valid_digits(raw: str | None, *, min_digits: int = 10, max_digits: int = 15) -> bool:
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    return min_digits <= len(digits) <= max_digits


def value_matches_expected(current: Any, expected: Any) -> bool:
    if isinstance(expected, str):
        return str(current or "").strip() == expected.strip()
    if isinstance(expected, bool):
        return bool(current) is expected
    return current == expected


def verify_candidata_saved(candidata_id: int, expected_fields: dict[str, Any]) -> bool:
    cid = int(candidata_id or 0)
    if cid <= 0:
        return False
    saved = Candidata.query.filter(Candidata.fila == cid).first()
    if not saved:
        return False
    for field_name, expected in (expected_fields or {}).items():
        if not value_matches_expected(getattr(saved, field_name, None), expected):
            return False
    return True


def error_looks_like_duplicate_cedula(error_message: str | None) -> bool:
    msg = (error_message or "").strip().lower()
    if not msg:
        return False
    keywords = ("integrity", "unique", "duplicate", "duplic", "cedula", "uq_", "ix_")
    return any(k in msg for k in keywords)


@dataclass
class CandidateCreateState:
    candidate: Candidata | None = None
    candidate_id: int | None = None


def robust_create_candidata(
    *,
    build_candidate: Callable[[int], Candidata],
    expected_fields: dict[str, Any],
    max_retries: int = 2,
    retry_sleep_seconds: float = 0.2,
    dispose_pool_fn: Callable[[], None] | None = None,
) -> tuple[RobustSaveResult, CandidateCreateState]:
    state = CandidateCreateState()

    def _persist(attempt: int) -> None:
        if attempt > 1 and dispose_pool_fn is not None:
            try:
                dispose_pool_fn()
            except Exception:
                pass
        cand = build_candidate(attempt)
        state.candidate = cand
        db.session.add(cand)

    def _verify() -> bool:
        candidate = state.candidate
        cid = int(getattr(candidate, "fila", 0) or 0) if candidate is not None else 0
        if cid <= 0:
            state.candidate_id = None
            return False
        state.candidate_id = cid
        return verify_candidata_saved(cid, expected_fields)

    result = execute_robust_save(
        session=db.session,
        persist_fn=_persist,
        verify_fn=_verify,
        max_retries=max_retries,
        retry_sleep_seconds=retry_sleep_seconds,
        retryable_exceptions=(OperationalError,),
    )
    return result, state


def log_candidate_create_ok(
    *,
    registration_type: str,
    candidate: Candidata,
    attempt_count: int,
) -> None:
    log_candidata_action(
        action_type="CANDIDATA_CREATE_OK",
        candidata=candidate,
        summary=f"Alta candidata {registration_type} OK",
        metadata={
            "registration_type": registration_type,
            "attempt_count": int(attempt_count),
            "source_route": (request.path or "").strip(),
        },
        success=True,
    )


def log_candidate_create_fail(
    *,
    registration_type: str,
    candidate: Candidata | None,
    attempt_count: int,
    error_message: str,
    nombre: str | None = None,
    cedula: str | None = None,
) -> None:
    clean_error = (error_message or "").strip()[:4000]
    candidate_id = getattr(candidate, "fila", None) if candidate is not None else None
    actor_user = None
    if "usuario" in session:
        actor_user = str(session.get("usuario") or "").strip() or None

    log_action(
        action_type="CANDIDATA_CREATE_FAIL",
        entity_type="candidata",
        entity_id=str(candidate_id) if candidate_id is not None else None,
        summary=f"Fallo alta candidata {registration_type}",
        metadata={
            "registration_type": registration_type,
            "attempt_count": int(attempt_count),
            "source_route": (request.path or "").strip(),
            "actor_usuario": actor_user,
            "nombre": (nombre or "").strip()[:150] or None,
            "cedula": (cedula or "").strip()[:50] or None,
        },
        success=False,
        error=clean_error or "candidate_create_failed",
    )
