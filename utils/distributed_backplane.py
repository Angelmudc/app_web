# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import current_app

from config_app import cache


class BackplaneUnavailable(RuntimeError):
    """Raised when a strict backplane operation cannot be completed."""


_WARNED_CODES: set[str] = set()


@dataclass(frozen=True)
class BackplaneStatus:
    configured: bool
    required: bool
    strict_runtime: bool
    cache_type: str


def _is_true(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def backplane_status() -> BackplaneStatus:
    cfg = getattr(current_app, "config", {}) or {}
    cache_type = str(cfg.get("CACHE_TYPE") or "").strip()
    configured = bool(cfg.get("DISTRIBUTED_BACKPLANE_ENABLED", False))
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


def _handle_unavailable(*, context: str, strict: bool, fallback: Any, exc: Exception):
    status = backplane_status()
    hard_fail = bool(strict or status.strict_runtime)
    msg = (
        f"[backplane:{context}] unavailable ({type(exc).__name__}: {exc}); "
        f"cache_type={status.cache_type or 'unknown'} configured={status.configured} required={status.required}"
    )
    _warn_once(f"{context}:{type(exc).__name__}", msg)
    if hard_fail:
        raise BackplaneUnavailable(msg) from exc
    return fallback


def bp_get(key: str, *, default: Any = None, strict: bool = False, context: str = "get"):
    try:
        value = cache.get(key)
        return default if value is None else value
    except Exception as exc:
        return _handle_unavailable(context=context, strict=strict, fallback=default, exc=exc)


def bp_set(key: str, value: Any, *, timeout: int, strict: bool = False, context: str = "set") -> bool:
    try:
        cache.set(key, value, timeout=max(1, int(timeout)))
        return True
    except Exception as exc:
        return bool(_handle_unavailable(context=context, strict=strict, fallback=False, exc=exc))


def bp_delete(key: str, *, strict: bool = False, context: str = "delete") -> bool:
    try:
        cache.delete(key)
        return True
    except Exception as exc:
        return bool(_handle_unavailable(context=context, strict=strict, fallback=False, exc=exc))


def bp_add(key: str, value: Any, *, timeout: int, strict: bool = False, context: str = "add") -> bool:
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


def bp_healthcheck(*, strict: bool = False) -> bool:
    key = "backplane:health:ping"
    try:
        cache.set(key, "1", timeout=10)
        return str(cache.get(key) or "") == "1"
    except Exception as exc:
        return bool(_handle_unavailable(context="healthcheck", strict=strict, fallback=False, exc=exc))
