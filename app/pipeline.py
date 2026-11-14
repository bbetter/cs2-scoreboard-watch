"""ScanPipeline: the single path for scan / rescan / upload.

    OCR (+console nick correction) -> initial reports -> scan record -> background enrichment
    (Steam/sources, then optional AI) -> final record.

It owns no global state. The scan list, its lock and persistence come from a `ScanRepo`; progress is
reported through an EventBus (topics in app/events.py); OCR/enrichment/AI are injected callables. app/server.py wires
the real ones, tests wire fakes.
"""

from __future__ import annotations

import threading
import time
import uuid
from contextlib import AbstractContextManager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from app.events import EventBus


class ScanRepo(Protocol):
    @property
    def lock(self) -> AbstractContextManager: ...

    @property
    def scans(self) -> list[dict]: ...

    max_history: int

    def save_locked(self) -> None: ...


def assign_player_uids(scan_id: str, players: list) -> None:
    """Give each OCR row a stable id, independent of its nick (duplicates and renames are fine)."""
    for idx, p in enumerate(players):
        p.player_uid = f"{scan_id}-{idx}"


class ScanPipeline:
    def __init__(
        self,
        repo: ScanRepo,
        bus: EventBus,
        *,
        ocr: Callable[[Path], tuple[list, Any]],          # image -> (players, console_status)
        enrich: Callable[..., list],                       # steam_lookup.enrich_players
        analyze_ai: Callable[[list], tuple[list, Optional[str]]],
        ai_enabled: Callable[[], bool],
        match_meta: Callable[[datetime, Any], tuple[str, Optional[str]]],
        screenshot_url: Callable[[Path], Optional[str]],
        spawn: Optional[Callable[[Callable[[], None]], None]] = None,  # tests pass a synchronous runner
    ) -> None:
        self.repo = repo
        self.bus = bus
        self._ocr = ocr
        self._enrich = enrich
        self._analyze_ai = analyze_ai
        self._ai_enabled = ai_enabled
        self._match_meta = match_meta
        self._screenshot_url = screenshot_url
        self._spawn = spawn or (lambda fn: threading.Thread(target=fn, daemon=True).start())

    # ------------------------------------------------------------------ shared building blocks
    def _initial_dicts(self, players: list, fetch_steam: bool) -> list[dict]:
        out = []
        for r in self._enrich(players, fetch_steam=False):
            d = asdict(r)
            streaming = bool(fetch_steam and players)
            d["loading"] = streaming
            d["loading_stage"] = "steam" if streaming else "done"
            out.append(d)
        return out

    def _start_enrichment(self, scan_id: str, players: list, started: float) -> None:
        self._spawn(lambda: self.enrich_scan(scan_id, players, started))

    # ------------------------------------------------------------------ entry points
    def run_new(self, image_path: Path, fetch_steam: bool = True) -> dict:
        """Create a new scan record from an image already on disk."""
        started = time.time()
        scan_id = uuid.uuid4().hex[:10]
        players, console_status = self._ocr(image_path)
        assign_player_uids(scan_id, players)
        initial = self._initial_dicts(players, fetch_steam)

        now_dt = datetime.now()
        match_id, map_name = self._match_meta(now_dt, console_status)
        will_stream = bool(fetch_steam and players)
        record = {
            "id": scan_id,
            "created_at": now_dt.isoformat(timespec="seconds"),
            "date": now_dt.strftime("%Y-%m-%d"),
            "match_id": match_id,
            "map_name": map_name,
            "image_name": image_path.name,
            "image_url": self._screenshot_url(image_path),
            "duration_ms": round((time.time() - started) * 1000),
            "player_count": len(initial),
            "players": initial,
            "ai_error": None,
            "scan_status": "loading_steam" if will_stream else "done",
            "progress": {"done": 0 if will_stream else len(initial), "total": len(initial)},
        }
        with self.repo.lock:
            self.repo.scans.insert(0, record)
            del self.repo.scans[self.repo.max_history:]
            self.repo.save_locked()
        self.bus.publish("scan.created", record)
        if will_stream:
            self._start_enrichment(scan_id, players, started)
        return record

    def rescan(self, scan_rec: dict, image_path: Path) -> dict:
        """Re-run OCR + enrichment for an existing scan record (same id, new players)."""
        started = time.time()
        scan_id = scan_rec["id"]
        players, console_status = self._ocr(image_path)
        assign_player_uids(scan_id, players)
        initial = self._initial_dicts(players, True)
        with self.repo.lock:
            scan_rec["players"] = initial
            scan_rec["player_count"] = len(initial)
            scan_rec["duration_ms"] = round((time.time() - started) * 1000)
            scan_rec["ai_error"] = None
            scan_rec["scan_status"] = "loading_steam" if players else "done"
            scan_rec["progress"] = {"done": 0, "total": len(initial)}
            self.repo.save_locked()
            snapshot = dict(scan_rec)
        self.bus.publish("scan.created", snapshot)
        if players:
            self._start_enrichment(scan_id, players, started)
        return snapshot

    # ------------------------------------------------------------------ background enrichment
    def _find(self, scan_id: str) -> Optional[dict]:
        return next((s for s in self.repo.scans if s["id"] == scan_id), None)

    def enrich_scan(self, scan_id: str, players: list, started: float) -> None:
        ai_enabled = bool(self._ai_enabled())
        total = len(players)
        done_count = 0
        progress_lock = threading.Lock()

        def _on_player(rep) -> None:
            nonlocal done_count
            pdict = asdict(rep)
            pdict["loading"] = False
            pdict["loading_stage"] = "ai" if ai_enabled else "done"
            with progress_lock:
                done_count += 1
                cur_done = done_count
            with self.repo.lock:
                scan_rec = self._find(scan_id)
                if not scan_rec:
                    return
                uid = pdict.get("player_uid")
                for i, existing in enumerate(scan_rec.get("players", [])):
                    same = (existing.get("player_uid") == uid) if uid and existing.get("player_uid") else (
                        existing.get("nick") == pdict.get("nick")
                    )
                    if same:
                        scan_rec["players"][i] = pdict
                        break
                scan_rec["progress"] = {"done": cur_done, "total": total}
                scan_rec["duration_ms"] = round((time.time() - started) * 1000)
            self.bus.publish(
                "player.enriched",
                {
                    "scan_id": scan_id,
                    "player": pdict,
                    "progress": {"done": cur_done, "total": total},
                    "scan_status": "loading_steam",
                },
            )

        reports = self._enrich(players, fetch_steam=True, on_player_ready=_on_player)
        player_dicts = []
        for r in reports:
            d = asdict(r)
            d["loading"] = False
            d["loading_stage"] = "ai" if ai_enabled else "done"
            player_dicts.append(d)

        ai_error = None
        if ai_enabled and player_dicts:
            with self.repo.lock:
                scan_rec = self._find(scan_id)
                if scan_rec:
                    scan_rec["players"] = player_dicts
                    scan_rec["scan_status"] = "loading_ai"
                    scan_rec["duration_ms"] = round((time.time() - started) * 1000)
                    snapshot = dict(scan_rec)
                else:
                    snapshot = None
            if snapshot:
                self.bus.publish("scan.updated", snapshot)
            player_dicts, ai_error = self._analyze_ai(player_dicts)

        for d in player_dicts:
            d["loading"] = False
            d["loading_stage"] = "done"

        with self.repo.lock:
            scan_rec = self._find(scan_id)
            if not scan_rec:
                return
            scan_rec["players"] = player_dicts
            scan_rec["player_count"] = len(player_dicts)
            scan_rec["duration_ms"] = round((time.time() - started) * 1000)
            scan_rec["ai_error"] = ai_error
            scan_rec["scan_status"] = "done"
            scan_rec["progress"] = {"done": total, "total": total}
            self.repo.save_locked()
            final_snapshot = dict(scan_rec)
        self.bus.publish("scan.finished", final_snapshot)
