"""Controles de consumo para IA del bot (límites diarios/sesión/contexto)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from models import BotDecisionLog

DEFAULT_BOT_AI_DAILY_REQUEST_LIMIT = 50
DEFAULT_BOT_AI_SESSION_REQUEST_LIMIT = 20
DEFAULT_BOT_AI_MAX_CONTEXT_MESSAGES = 3
DEFAULT_BOT_AI_MAX_INPUT_CHARS = 800
DEFAULT_BOT_AI_MAX_OUTPUT_CHARS = 700
DEFAULT_BOT_AI_EVAL_MAX_CASES = 20

_AI_SESSION_REQUEST_COUNT = 0


def _int_env(name: str, default: int, *, min_value: int = 0, max_value: int = 100000) -> int:
    raw = (os.getenv(name) or "").strip()
    try:
        val = int(raw) if raw else int(default)
    except Exception:
        val = int(default)
    if val < min_value:
        return min_value
    if val > max_value:
        return max_value
    return val


def ai_daily_request_limit() -> int:
    return _int_env("BOT_AI_DAILY_REQUEST_LIMIT", DEFAULT_BOT_AI_DAILY_REQUEST_LIMIT, min_value=0, max_value=100000)


def ai_session_request_limit() -> int:
    return _int_env("BOT_AI_SESSION_REQUEST_LIMIT", DEFAULT_BOT_AI_SESSION_REQUEST_LIMIT, min_value=1, max_value=100000)


def ai_max_context_messages() -> int:
    return _int_env("BOT_AI_MAX_CONTEXT_MESSAGES", DEFAULT_BOT_AI_MAX_CONTEXT_MESSAGES, min_value=1, max_value=10)


def ai_max_input_chars() -> int:
    return _int_env("BOT_AI_MAX_INPUT_CHARS", DEFAULT_BOT_AI_MAX_INPUT_CHARS, min_value=10, max_value=4000)


def ai_max_output_chars() -> int:
    return _int_env("BOT_AI_MAX_OUTPUT_CHARS", DEFAULT_BOT_AI_MAX_OUTPUT_CHARS, min_value=10, max_value=4000)


def ai_eval_max_cases() -> int:
    return _int_env("BOT_AI_EVAL_MAX_CASES", DEFAULT_BOT_AI_EVAL_MAX_CASES, min_value=1, max_value=5000)


def get_ai_daily_usage_count(*, at_utc: datetime | None = None) -> int:
    now = at_utc or datetime.utcnow()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return (
        BotDecisionLog.query.filter(BotDecisionLog.decision_type == "ai_classification")
        .filter(BotDecisionLog.ai_used.is_(True))
        .filter(BotDecisionLog.created_at >= start, BotDecisionLog.created_at < end)
        .count()
    )


def get_ai_daily_usage_summary(*, at_utc: datetime | None = None) -> dict[str, int | bool]:
    used = int(get_ai_daily_usage_count(at_utc=at_utc))
    limit = int(ai_daily_request_limit())
    reached = used >= limit
    remaining = 0 if reached else max(0, limit - used)
    return {"used": used, "limit": limit, "remaining": remaining, "reached": reached}


def is_ai_daily_limit_reached(*, at_utc: datetime | None = None) -> bool:
    summary = get_ai_daily_usage_summary(at_utc=at_utc)
    return bool(summary["reached"])


def get_ai_session_request_count() -> int:
    global _AI_SESSION_REQUEST_COUNT
    return int(_AI_SESSION_REQUEST_COUNT)


def try_reserve_ai_session_request() -> bool:
    global _AI_SESSION_REQUEST_COUNT
    limit = ai_session_request_limit()
    if _AI_SESSION_REQUEST_COUNT >= limit:
        return False
    _AI_SESSION_REQUEST_COUNT += 1
    return True


def reset_ai_session_request_count() -> None:
    global _AI_SESSION_REQUEST_COUNT
    _AI_SESSION_REQUEST_COUNT = 0
