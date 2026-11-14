"""Scan records (newest first), their lock and their JSON history file. Implements app.pipeline.ScanRepo."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Callable, Optional

from storage.atomic_io import atomic_write_text


class ScanStore:
    def __init__(self, history_file: Path, max_history: int = 100) -> None:
        self.lock = threading.Lock()
        self.scans: list[dict] = []
        self.history_file = history_file
        self.max_history = max_history

    def load(self, adapt: Optional[Callable[[dict], None]] = None) -> None:
        if not self.history_file.is_file():
            return
        try:
            data = json.loads(self.history_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                scans = data[: self.max_history]
                for scan in scans:
                    if isinstance(scan, dict) and adapt is not None:
                        adapt(scan)
                with self.lock:
                    self.scans[:] = scans
        except Exception:
            pass

    def save_locked(self) -> None:
        try:
            atomic_write_text(self.history_file, json.dumps(self.scans, ensure_ascii=False))
        except Exception:
            pass

    def find_locked(self, scan_id: Optional[str]) -> Optional[dict]:
        return next((s for s in self.scans if s["id"] == scan_id), None)

    def latest_locked(self) -> Optional[dict]:
        return self.scans[0] if self.scans else None
