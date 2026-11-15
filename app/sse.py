"""Server-sent events hub: the subscriber queues behind `GET /api/events`, with a connection limit."""

from __future__ import annotations

import json
import queue
import threading


def _signal_close(q: queue.Queue[str | None]) -> None:
    """Drop what is still queued for a removed subscriber and leave only the close marker."""
    while True:
        try:
            q.get_nowait()
        except queue.Empty:
            break
    q.put_nowait(None)


class SseHub:
    """Tracks open SSE subscribers. Each subscriber owns a bounded queue of pre-formatted messages.

    `subscribe()` returns None once `max_subscribers` streams are open, so the route can answer 503 before it
    writes any SSE headers. A subscriber whose queue overflows is dropped by `broadcast()`, which also puts a
    `None` close marker in its queue: the route reads it and ends the stream, freeing the connection.
    """

    def __init__(self, max_subscribers: int = 8, queue_size: int = 20):
        self._max_subscribers = max_subscribers
        self._queue_size = queue_size
        self._lock = threading.Lock()
        self._queues: list[queue.Queue[str | None]] = []

    def subscribe(self) -> queue.Queue[str | None] | None:
        with self._lock:
            if len(self._queues) >= self._max_subscribers:
                return None
            q: queue.Queue[str | None] = queue.Queue(maxsize=self._queue_size)
            self._queues.append(q)
            return q

    def unsubscribe(self, q: queue.Queue[str | None] | None) -> None:
        if q is None:
            return
        with self._lock:
            if q in self._queues:
                self._queues.remove(q)

    def broadcast(self, event: str, payload) -> None:
        msg = f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        with self._lock:
            dead: list[queue.Queue[str | None]] = []
            for q in self._queues:
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self._queues.remove(q)
                _signal_close(q)

    def count(self) -> int:
        with self._lock:
            return len(self._queues)
