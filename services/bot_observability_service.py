from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any

from flask import current_app

from utils.audit_logger import log_action


def _logger():
    try:
        return current_app.logger
    except Exception:
        return None


def log_bot_event(event: str, *, level: str = "info", metadata: dict[str, Any] | None = None) -> None:
    logger = _logger()
    msg = f"[bot_observability] {event}"
    if logger:
        getattr(logger, level, logger.info)("%s metadata=%s", msg, metadata or {})


def log_bot_blocked(event: str, *, reason: str, metadata: dict[str, Any] | None = None) -> None:
    payload = dict(metadata or {})
    payload["reason"] = reason
    log_bot_event(event, level="warning", metadata=payload)
    try:
        log_action(
            action_type=f"bot_blocked_{event}",
            entity_type="BotSafety",
            entity_id="0",
            summary=f"Bloqueo operacional bot: {event}",
            metadata=payload,
            success=False,
            error=reason,
        )
    except Exception:
        return


def log_bot_error(event: str, exc: Exception, *, metadata: dict[str, Any] | None = None) -> None:
    payload = dict(metadata or {})
    payload["error_class"] = exc.__class__.__name__
    payload["error"] = str(exc)
    log_bot_event(event, level="error", metadata=payload)


@contextmanager
def bot_timing(event: str, *, metadata: dict[str, Any] | None = None):
    started = time.perf_counter()
    try:
        yield
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        payload = dict(metadata or {})
        payload["elapsed_ms"] = elapsed_ms
        log_bot_error(f"{event}.failed", exc, metadata=payload)
        raise
    else:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        payload = dict(metadata or {})
        payload["elapsed_ms"] = elapsed_ms
        log_bot_event(f"{event}.ok", metadata=payload)
