# -*- coding: utf-8 -*-
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Iterable

from sqlalchemy.exc import OperationalError, SQLAlchemyError


_LEGACY_PLACEHOLDERS = {
    "none",
    "node",
    "no",
    "n/a",
    "na",
    "null",
    "sin",
    "pendiente",
    "vacio",
    "vacío",
    "--",
    "-",
}


class SaveVerificationError(Exception):
    """La escritura no pudo verificarse después de commit."""


@dataclass
class RobustSaveResult:
    ok: bool
    attempts: int
    error_message: str = ""


def safe_bytes_length(value) -> int:
    if value is None:
        return 0
    if isinstance(value, memoryview):
        try:
            return len(value.tobytes())
        except Exception:
            return 0
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    try:
        return len(value)  # type: ignore[arg-type]
    except Exception:
        return 0


def binary_has_content(value) -> bool:
    return safe_bytes_length(value) > 0


def legacy_text_is_useful(value: str | None) -> bool:
    txt = (value or "").strip()
    if not txt:
        return False
    return txt.lower() not in _LEGACY_PLACEHOLDERS


def execute_robust_save(
    *,
    session,
    persist_fn: Callable[[int], None],
    verify_fn: Callable[[], bool],
    max_retries: int = 2,
    retry_sleep_seconds: float = 0.2,
    retryable_exceptions: Iterable[type[Exception]] = (OperationalError, SQLAlchemyError),
) -> RobustSaveResult:
    """
    Ejecuta persistencia + verificación post-commit con reintentos controlados.

    Política:
    - intento 1: reintento inmediato ante error/verificación fallida
    - intento 2 fallido: espera corta y último intento
    """
    retryable = tuple(retryable_exceptions) + (SaveVerificationError,)
    total_attempts = max(1, int(max_retries) + 1)
    last_error = ""

    for attempt in range(1, total_attempts + 1):
        try:
            persist_fn(attempt)
            session.flush()
            session.commit()
            if not bool(verify_fn()):
                raise SaveVerificationError("No se pudo verificar la escritura en base de datos.")
            return RobustSaveResult(ok=True, attempts=attempt, error_message="")
        except Exception as exc:
            try:
                session.rollback()
            except Exception:
                pass
            last_error = str(exc) or exc.__class__.__name__
            should_retry = (attempt < total_attempts) and isinstance(exc, retryable)
            if not should_retry:
                return RobustSaveResult(ok=False, attempts=attempt, error_message=last_error)
            if attempt == 2 and retry_sleep_seconds > 0:
                time.sleep(float(retry_sleep_seconds))

    return RobustSaveResult(ok=False, attempts=total_attempts, error_message=last_error)
