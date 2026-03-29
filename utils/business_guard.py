# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import time
from typing import Any

from utils.audit_logger import log_security_event
from utils.distributed_backplane import bp_get, bp_incr, bp_set
from utils.enterprise_layer import emit_warning_alert


def _is_true_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    txt = str(raw).strip().lower()
    if not txt:
        return default
    return txt in {"1", "true", "yes", "on"}


def business_guard_enabled() -> bool:
    explicit = os.getenv("ENABLE_BUSINESS_ABUSE_PROTECTION")
    if explicit is not None and str(explicit).strip() != "":
        return _is_true_env("ENABLE_BUSINESS_ABUSE_PROTECTION", default=False)

    fallback = os.getenv("ENABLE_OPERATIONAL_RATE_LIMITS")
    if fallback is not None and str(fallback).strip() != "":
        return _is_true_env("ENABLE_OPERATIONAL_RATE_LIMITS", default=False)

    run_env = (os.getenv("APP_ENV", os.getenv("FLASK_ENV", "development")) or "").strip().lower()
    return run_env in {"prod", "production"}


def _safe_inc(cache_obj: Any, key: str, *, window_seconds: int) -> int:
    return int(
        bp_incr(
            key,
            delta=1,
            timeout=max(1, int(window_seconds)),
            context="business_guard_inc",
        ) or 0
    )


def _safe_get(cache_obj: Any, key: str, default: Any = None) -> Any:
    return bp_get(key, default=default, context="business_guard_get")


def _safe_set(cache_obj: Any, key: str, value: Any, timeout: int) -> bool:
    return bool(
        bp_set(
            key,
            value,
            timeout=max(1, int(timeout)),
            context="business_guard_set",
        )
    )


def _emit_abuse_warning(cache_obj: Any, scope: str, actor: str, summary: str, metadata: dict[str, Any]) -> None:
    dedupe_key = f"bizguard:alert:{scope}:{actor}"
    if cache_obj is not None and _safe_get(cache_obj, dedupe_key):
        return
    if cache_obj is not None:
        _safe_set(cache_obj, dedupe_key, 1, timeout=90)
    try:
        emit_warning_alert(
            rule="business_abuse_pattern",
            summary=summary[:255],
            entity_type="security",
            entity_id=f"{scope}:{actor}"[:64],
            metadata=metadata,
            dedupe_seconds=90,
            telegram=True,
        )
    except Exception:
        return


def enforce_business_limit(
    *,
    cache_obj: Any,
    scope: str,
    actor: str,
    limit: int,
    window_seconds: int,
    reason: str,
    summary: str,
    metadata: dict[str, Any] | None = None,
    alert_on_block: bool = True,
) -> tuple[bool, int]:
    """Devuelve (blocked, count_actual)."""
    if not business_guard_enabled():
        return False, 0
    scope_txt = (scope or "unknown").strip().lower()[:80]
    actor_txt = (actor or "anonymous").strip().lower()[:120]
    try:
        limit_i = max(1, int(limit))
    except Exception:
        limit_i = 1
    try:
        win_i = max(1, int(window_seconds))
    except Exception:
        win_i = 60

    key = f"bizguard:limit:{scope_txt}:{actor_txt}"
    count = _safe_inc(cache_obj, key, window_seconds=win_i)
    blocked = bool(count > limit_i and count > 0)
    if not blocked:
        return False, count

    event_meta = {
        "scope": scope_txt,
        "actor": actor_txt[:64],
        "limit": limit_i,
        "window_seconds": win_i,
        "count": int(count),
        "reason": (reason or "limit_exceeded")[:120],
    }
    event_meta.update(dict(metadata or {}))
    log_security_event(
        event="BUSINESS_ABUSE_BLOCKED",
        status="fail",
        entity_type="business_flow",
        entity_id=scope_txt,
        summary=summary,
        reason=reason,
        metadata=event_meta,
    )
    if alert_on_block:
        _emit_abuse_warning(
            cache_obj,
            scope_txt,
            actor_txt,
            summary=summary,
            metadata=event_meta,
        )
    return True, count


def enforce_min_human_interval(
    *,
    cache_obj: Any,
    scope: str,
    actor: str,
    min_seconds: int,
    reason: str,
    summary: str,
    metadata: dict[str, Any] | None = None,
) -> tuple[bool, int]:
    """Bloquea actividad con timing inusualmente rápido.

    Retorna (blocked, elapsed_seconds).
    """
    if not business_guard_enabled():
        return False, 0

    scope_txt = (scope or "unknown").strip().lower()[:80]
    actor_txt = (actor or "anonymous").strip().lower()[:120]
    try:
        min_sec = max(1, int(min_seconds))
    except Exception:
        min_sec = 1

    now_ts = int(time.time())
    key = f"bizguard:last:{scope_txt}:{actor_txt}"
    last_raw = _safe_get(cache_obj, key, default=0)
    try:
        last_ts = int(last_raw or 0)
    except Exception:
        last_ts = 0

    elapsed = (now_ts - last_ts) if last_ts else 999999
    _safe_set(cache_obj, key, now_ts, timeout=max(60, min_sec * 12))
    if elapsed >= min_sec:
        return False, int(elapsed)

    event_meta = {
        "scope": scope_txt,
        "actor": actor_txt[:64],
        "elapsed_seconds": int(elapsed),
        "min_seconds": int(min_sec),
        "reason": (reason or "timing_too_fast")[:120],
    }
    event_meta.update(dict(metadata or {}))
    log_security_event(
        event="BUSINESS_NON_HUMAN_PATTERN",
        status="fail",
        entity_type="business_flow",
        entity_id=scope_txt,
        summary=summary,
        reason=reason,
        metadata=event_meta,
    )
    _emit_abuse_warning(
        cache_obj,
        scope_txt,
        actor_txt,
        summary=summary,
        metadata=event_meta,
    )
    return True, int(elapsed)
