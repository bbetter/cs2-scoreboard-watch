"""In-memory TTL memo for dict values: thread-safe, returns copies, never written to disk.

An entry is a hit while its age is strictly less than the ttl passed to get(). ttl <= 0 disables the memo for that call.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class TtlMemo:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._items: dict[str, tuple[float, dict]] = {}

    def get(self, key: str | None, ttl: float) -> dict | None:
        if not key or ttl <= 0:
            return None
        with self._lock:
            item = self._items.get(key)
        if item and (self._clock() - item[0]) < ttl:
            return dict(item[1])
        return None

    def put(self, key: str | None, value: dict | None, ttl: float) -> None:
        if not key or ttl <= 0 or not value:
            return
        with self._lock:
            self._items[key] = (self._clock(), dict(value))

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
