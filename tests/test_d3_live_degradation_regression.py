from pathlib import Path


def _read(rel_path: str) -> str:
    return Path(rel_path).read_text(encoding="utf-8", errors="ignore")


def test_live_invalidation_runtime_has_sse_cooldown_circuit_breaker():
    txt = _read("static/js/core/live_invalidation.js")
    assert "admin_live_invalidation_sse_cooldown_until" in txt
    assert "SSE_FAIL_COOLDOWN_MS = 5 * 60 * 1000" in txt
    assert "if (sseErrorStreak >= 2)" in txt
    assert "if (isSseCoolingDown())" in txt


def test_admin_chat_runtimes_share_sse_cooldown_key():
    badge_txt = _read("static/js/chat/chat_global_badge.js")
    chat_txt = _read("static/js/chat/admin_chat.js")
    shared_key = "admin_live_invalidation_sse_cooldown_until"
    assert shared_key in badge_txt
    assert shared_key in chat_txt
    assert "SSE_FAIL_COOLDOWN_MS = 5 * 60 * 1000" in badge_txt
    assert "SSE_FAIL_COOLDOWN_MS = 5 * 60 * 1000" in chat_txt


def test_live_stream_redis_warning_dedupe_not_keyed_by_message_text():
    txt = _read("admin/routes.py")
    assert 'dedupe_key = f"f4_live_warn_boot:{type(exc).__name__}"' in txt
    assert 'dedupe_key = f"f4_live_warn_read:{type(exc).__name__}"' in txt
