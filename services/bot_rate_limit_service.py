from __future__ import annotations

import threading
import time
from collections import deque


_LOCK = threading.Lock()
_BUCKETS: dict[str, deque[float]] = {}


def _bucket(key: str) -> deque[float]:
    with _LOCK:
        return _BUCKETS.setdefault(key, deque())


def allow_action(*, actor_key: str, action_key: str, limit: int = 4, window_seconds: int = 20) -> tuple[bool, int]:
    now = time.monotonic()
    key = f"{action_key}:{actor_key}"
    b = _bucket(key)
    with _LOCK:
        while b and (now - b[0]) > float(window_seconds):
            b.popleft()
        if len(b) >= int(limit):
            retry_after = int(max(1, window_seconds - (now - b[0])))
            return False, retry_after
        b.append(now)
        return True, 0


def reset_rate_limits() -> None:
    with _LOCK:
        _BUCKETS.clear()
