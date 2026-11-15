"""Tiny in-process event bus.

Publishers (the scan pipeline) emit domain events; sinks (SSE stream, history persistence, player DB,
notifications) subscribe. Handlers run synchronously in the publisher's thread and must not raise:
a failing handler is logged and never breaks the others or the publisher.

Topics used by the pipeline:
  scan.created    payload: scan record dict (initial players, loading)
  scan.updated    payload: scan record dict (stage change, e.g. loading_ai)
  player.enriched payload: {"scan_id", "player", "progress", "scan_status"}
  scan.finished   payload: final scan record dict
"""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable
from typing import Any

Handler = Callable[[str, Any], None]

WILDCARD = "*"


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handlers: dict[str, list[Handler]] = {}

    def subscribe(self, topic: str, handler: Handler) -> Callable[[], None]:
        """Register `handler(topic, payload)` for a topic ("*" = all). Returns an unsubscribe function."""
        with self._lock:
            self._handlers.setdefault(topic, []).append(handler)

        def _unsubscribe() -> None:
            with self._lock:
                lst = self._handlers.get(topic) or []
                if handler in lst:
                    lst.remove(handler)

        return _unsubscribe

    def publish(self, topic: str, payload: Any = None) -> None:
        with self._lock:
            handlers = list(self._handlers.get(topic, ())) + list(self._handlers.get(WILDCARD, ()))
        for h in handlers:
            try:
                h(topic, payload)
            except Exception:
                traceback.print_exc()
