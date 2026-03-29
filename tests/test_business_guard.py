# -*- coding: utf-8 -*-

from utils import business_guard


class _FakeCache:
    def __init__(self):
        self._store = {}

    def get(self, key):
        return self._store.get(key)

    def set(self, key, value, timeout=None):
        self._store[key] = value
        return True


def test_enforce_business_limit_blocks_after_threshold(monkeypatch):
    cache = _FakeCache()
    monkeypatch.setenv("ENABLE_BUSINESS_ABUSE_PROTECTION", "1")
    monkeypatch.setattr(business_guard, "log_security_event", lambda **kwargs: None)
    monkeypatch.setattr(business_guard, "emit_warning_alert", lambda **kwargs: None)

    blocked_1, _ = business_guard.enforce_business_limit(
        cache_obj=cache,
        scope="unit_limit",
        actor="ip:1.1.1.1",
        limit=2,
        window_seconds=60,
        reason="burst",
        summary="unit test",
    )
    blocked_2, _ = business_guard.enforce_business_limit(
        cache_obj=cache,
        scope="unit_limit",
        actor="ip:1.1.1.1",
        limit=2,
        window_seconds=60,
        reason="burst",
        summary="unit test",
    )
    blocked_3, _ = business_guard.enforce_business_limit(
        cache_obj=cache,
        scope="unit_limit",
        actor="ip:1.1.1.1",
        limit=2,
        window_seconds=60,
        reason="burst",
        summary="unit test",
    )

    assert blocked_1 is False
    assert blocked_2 is False
    assert blocked_3 is True


def test_enforce_min_human_interval_blocks_rapid_replay(monkeypatch):
    cache = _FakeCache()
    monkeypatch.setenv("ENABLE_BUSINESS_ABUSE_PROTECTION", "1")
    monkeypatch.setattr(business_guard, "log_security_event", lambda **kwargs: None)
    monkeypatch.setattr(business_guard, "emit_warning_alert", lambda **kwargs: None)

    blocked_1, _ = business_guard.enforce_min_human_interval(
        cache_obj=cache,
        scope="unit_interval",
        actor="user:10",
        min_seconds=5,
        reason="rapid",
        summary="unit test",
    )
    blocked_2, elapsed_2 = business_guard.enforce_min_human_interval(
        cache_obj=cache,
        scope="unit_interval",
        actor="user:10",
        min_seconds=5,
        reason="rapid",
        summary="unit test",
    )

    assert blocked_1 is False
    assert blocked_2 is True
    assert elapsed_2 < 5
