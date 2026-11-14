"""Steam profile cache persisted as JSON: key -> (timestamp, profile dict).

The file is read on first access, not at import time. Callers hold `lock` around every call;
the methods do not take it themselves, so a get-then-put sequence can be made atomic by the caller.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable

from storage.atomic_io import atomic_write_text


class ProfileCache:
    def __init__(
        self,
        path: Path,
        strip: Callable[[dict], dict] = lambda d: d,
        ttl_sec: float = 86400,
    ) -> None:
        self.path = Path(path)
        self.lock = threading.Lock()
        self._strip = strip
        self._ttl_sec = ttl_sec
        self._entries: dict[str, tuple[float, dict]] = {}
        self._loaded = False

    def entries(self) -> dict[str, tuple[float, dict]]:
        self._ensure_loaded()
        return self._entries

    def get(self, key: str) -> tuple[float, dict] | None:
        self._ensure_loaded()
        return self._entries.get(key)

    def put_locked(self, key: str, ts: float, data: dict) -> None:
        self._ensure_loaded()
        self._entries[key] = (ts, self._strip(data))

    def save_locked(self) -> None:
        self._ensure_loaded()
        try:
            persisted = {
                k: [ts, self._strip(data)]
                for k, (ts, data) in self._entries.items()
                if data.get("lookup_source") not in {"not_found", "rate_limited"}
            }
            atomic_write_text(self.path, json.dumps(persisted, ensure_ascii=False))
        except Exception:
            pass

    def clear(self) -> None:
        """Drop all entries and mark the cache as loaded, so tests never read the file afterwards."""
        self._entries.clear()
        self._loaded = True

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            now = time.time()
            if isinstance(raw, dict):
                for k, item in raw.items():
                    if isinstance(item, list) and len(item) == 2 and (now - float(item[0])) < self._ttl_sec:
                        if isinstance(item[1], dict):
                            self._entries[k] = (float(item[0]), self._strip(item[1]))
        except Exception:
            pass
