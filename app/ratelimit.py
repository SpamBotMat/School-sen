"""Простые лимиты в памяти (скользящее окно). Перезапуск их сбрасывает — это нормально."""
from __future__ import annotations

import time
from collections import deque


class RateLimits:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str, limit: int, per_seconds: float) -> bool:
        t = time.monotonic()
        q = self._hits.setdefault(key, deque())
        while q and q[0] <= t - per_seconds:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(t)
        return True

    def retry_after(self, key: str, limit: int, per_seconds: float) -> int:
        q = self._hits.get(key)
        if not q or len(q) < limit:
            return 0
        return max(1, int(q[0] + per_seconds - time.monotonic()) + 1)

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)

    def gc(self, max_age: float = 86400) -> None:
        t = time.monotonic()
        for k in [k for k, q in self._hits.items() if not q or q[-1] < t - max_age]:
            self._hits.pop(k, None)
