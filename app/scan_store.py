"""Scan records (newest first), their lock and their JSON history file. Implements app.pipeline.ScanRepo."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from storage.atomic_io import atomic_write_text

_LOG = logging.getLogger("cs2watch.scan_store")


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
        except (OSError, ValueError) as exc:
            self._move_corrupt_aside(f"not readable: {exc}")
            return
        if not isinstance(data, list):
            self._move_corrupt_aside("not a JSON list")
            return
        scans = data[: self.max_history]
        try:
            for scan in scans:
                if isinstance(scan, dict) and adapt is not None:
                    adapt(scan)
        except Exception:
            _LOG.exception("failed to adapt history from %s", self.history_file)
            return
        with self.lock:
            self.scans[:] = scans

    def save_locked(self) -> None:
        try:
            atomic_write_text(self.history_file, json.dumps(self.scans, ensure_ascii=False))
        except Exception:
            _LOG.exception("failed to save history to %s", self.history_file)

    def _move_corrupt_aside(self, reason: str) -> None:
        """Keep an unreadable history file as `<name>.corrupt` (timestamped if that exists) so the next save
        does not overwrite it silently."""
        target = self.history_file.with_name(self.history_file.name + ".corrupt")
        if target.exists():
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            target = self.history_file.with_name(f"{self.history_file.name}.corrupt.{stamp}")
        try:
            os.replace(self.history_file, target)
        except OSError as exc:
            _LOG.error("history %s is unreadable (%s) and could not be moved aside: %s",
                       self.history_file, reason, exc)
            return
        _LOG.warning("history %s is unreadable (%s); moved to %s", self.history_file, reason, target)

    def find_locked(self, scan_id: Optional[str]) -> Optional[dict]:
        return next((s for s in self.scans if s["id"] == scan_id), None)

    def latest_locked(self) -> Optional[dict]:
        return self.scans[0] if self.scans else None
