# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
import os
import time
from typing import Any

from flask import current_app

from config_app import cache


class BackplaneUnavailable(RuntimeError):
    """Raised when a strict backplane operation cannot be completed."""


_WARNED_CODES: set[str] = set()
_BACKPLANE_DOWN_UNTIL_MONO: float = 0.0


@dataclass(frozen=True)
class BackplaneStatus:
    configured: bool
    required: bool
    strict_runtime: bool
    cache_type: str


def _is_true(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _resolve_config(app_obj: Any | None = None) -> dict[str, Any]:
    if app_obj is not None:
        return getattr(app_obj, "config", {}) or {}
    try:
        return getattr(current_app, "config", {}) or {}
    except Exception:
        return {}


def backplane_status(*, app_obj: Any | None = None) -> BackplaneStatus:
    cfg = _resolve_config(app_obj=app_obj)
    cache_type = str(cfg.get("CACHE_TYPE") or "").strip()
    mode = str(cfg.get("DISTRIBUTED_BACKPLANE_MODE") or "").strip().lower()
    configured_flag = bool(cfg.get("DISTRIBUTED_BACKPLANE_ENABLED", False))
    startup_healthy = bool(cfg.get("DISTRIBUTED_BACKPLANE_HEALTHY_AT_STARTUP", configured_flag))
    disabled_modes = {"disabled", "degraded_unavailable", "local_only", "simple"}
    configured = bool(configured_flag and startup_healthy and (mode not in disabled_modes))
    required = bool(cfg.get("DISTRIBUTED_BACKPLANE_REQUIRED", False))
    strict_runtime = _is_true(cfg.get("DISTRIBUTED_BACKPLANE_STRICT_RUNTIME", "0"))
    return BackplaneStatus(
        configured=configured,
        required=required,
        strict_runtime=strict_runtime,
        cache_type=cache_type,
    )


def _warn_once(code: str, message: str) -> None:
    if code in _WARNED_CODES:
        return
    _WARNED_CODES.add(code)
    try:
        current_app.logger.error(message)
    except Exception:
        pass


def _degraded_cooldown_seconds() -> int:
    try:
        return max(1, min(120, int((os.getenv("DISTRIBUTED_BACKPLANE_DEGRADED_COOLDOWN_SECONDS") or "8").strip())))
    except Exception:
        return 8


def _mark_temporarily_unavailable() -> None:
    global _BACKPLANE_DOWN_UNTIL_MONO
    _BACKPLANE_DOWN_UNTIL_MONO = time.monotonic() + float(_degraded_cooldown_seconds())


def _temporarily_unavailable() -> bool:
    return time.monotonic() < float(_BACKPLANE_DOWN_UNTIL_MONO or 0.0)


def _fallback_without_cache(*, status: BackplaneStatus, strict: bool, fallback: Any, context: str):
    msg = (
        f"[backplane:{context}] disabled/degraded; "
        f"cache_type={status.cache_type or 'unknown'} configured={status.configured} required={status.required}"
    )
    if strict or status.strict_runtime or status.required:
        _warn_once(f"{context}:disabled_strict", msg)
        raise BackplaneUnavailable(msg)
    return fallback


def _is_redis_cache_type(status: BackplaneStatus) -> bool:
    cache_type = str(status.cache_type or "").strip().lower()
    return "redis" in cache_type


def _handle_unavailable(*, context: str, strict: bool, fallback: Any, exc: Exception):
    status = backplane_status()
    hard_fail = bool(strict or status.strict_runtime)
    msg = (
        f"[backplane:{context}] unavailable ({type(exc).__name__}: {exc}); "
        f"cache_type={status.cache_type or 'unknown'} configured={status.configured} required={status.required}"
    )
    _warn_once(f"{context}:{type(exc).__name__}", msg)
    _mark_temporarily_unavailable()
    if hard_fail:
        raise BackplaneUnavailable(msg) from exc
    return fallback


def bp_get(key: str, *, default: Any = None, strict: bool = False, context: str = "get"):
    status = backplane_status()
    using_redis = _is_redis_cache_type(status)
    if using_redis and not status.configured:
        return _fallback_without_cache(status=status, strict=strict, fallback=default, context=context)
    if using_redis and _temporarily_unavailable():
        return _fallback_without_cache(status=status, strict=strict, fallback=default, context=context)
    try:
        value = cache.get(key)
        return default if value is None else value
    except Exception as exc:
        return _handle_unavailable(context=context, strict=strict, fallback=default, exc=exc)


def bp_set(key: str, value: Any, *, timeout: int, strict: bool = False, context: str = "set") -> bool:
    status = backplane_status()
    using_redis = _is_redis_cache_type(status)
    if using_redis and not status.configured:
        return bool(_fallback_without_cache(status=status, strict=strict, fallback=False, context=context))
    if using_redis and _temporarily_unavailable():
        return bool(_fallback_without_cache(status=status, strict=strict, fallback=False, context=context))
    try:
        cache.set(key, value, timeout=max(1, int(timeout)))
        return True
    except Exception as exc:
        return bool(_handle_unavailable(context=context, strict=strict, fallback=False, exc=exc))


def bp_delete(key: str, *, strict: bool = False, context: str = "delete") -> bool:
    status = backplane_status()
    using_redis = _is_redis_cache_type(status)
    if using_redis and not status.configured:
        return bool(_fallback_without_cache(status=status, strict=strict, fallback=False, context=context))
    if using_redis and _temporarily_unavailable():
        return bool(_fallback_without_cache(status=status, strict=strict, fallback=False, context=context))
    try:
        cache.delete(key)
        return True
    except Exception as exc:
        return bool(_handle_unavailable(context=context, strict=strict, fallback=False, exc=exc))


def bp_add(key: str, value: Any, *, timeout: int, strict: bool = False, context: str = "add") -> bool:
    status = backplane_status()
    using_redis = _is_redis_cache_type(status)
    if using_redis and not status.configured:
        return bool(_fallback_without_cache(status=status, strict=strict, fallback=False, context=context))
    if using_redis and _temporarily_unavailable():
        return bool(_fallback_without_cache(status=status, strict=strict, fallback=False, context=context))
    try:
        if hasattr(cache, "add"):
            return bool(cache.add(key, value, timeout=max(1, int(timeout))))
        if cache.get(key) is None:
            cache.set(key, value, timeout=max(1, int(timeout)))
            return True
        return False
    except Exception as exc:
        return bool(_handle_unavailable(context=context, strict=strict, fallback=False, exc=exc))


def bp_incr(key: str, *, delta: int = 1, timeout: int, strict: bool = False, context: str = "incr") -> int:
    status = backplane_status()
    using_redis = _is_redis_cache_type(status)
    if using_redis and not status.configured:
        return int(_fallback_without_cache(status=status, strict=strict, fallback=0, context=context) or 0)
    if using_redis and _temporarily_unavailable():
        return int(_fallback_without_cache(status=status, strict=strict, fallback=0, context=context) or 0)
    try:
        inc_delta = int(delta or 1)
        ttl = max(1, int(timeout))
        if hasattr(cache, "inc"):
            out = cache.inc(key, inc_delta)
            cache.set(key, int(out), timeout=ttl)
            return int(out)
        current = int(cache.get(key) or 0) + inc_delta
        cache.set(key, current, timeout=ttl)
        return int(current)
    except Exception as exc:
        return int(_handle_unavailable(context=context, strict=strict, fallback=0, exc=exc) or 0)


def bp_healthcheck(*, strict: bool = False, app_obj: Any | None = None) -> bool:
    status = backplane_status(app_obj=app_obj)
    using_redis = _is_redis_cache_type(status)
    if using_redis and not status.configured:
        return False
    if using_redis and _temporarily_unavailable():
        return False
    key = "backplane:health:ping"
    try:
        cache.set(key, "1", timeout=10)
        return str(cache.get(key) or "") == "1"
    except Exception as exc:
        return bool(_handle_unavailable(context="healthcheck", strict=strict, fallback=False, exc=exc))
