"""Manual identity corrections: when a user replaces the SteamID that the automatic nickname search chose.

Each record is one labelled example for judging the search: the nick the OCR read, the profile the search chose,
the profile the user chose instead, how the user identified it (`method`), and where the chosen profile ranked in
the candidate list (`chosen_rank`, None when the search never offered it). Records stay in
data/identity_corrections.json on this machine and are never sent anywhere.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from storage.atomic_io import atomic_write_text
from storage.paths import DATA_DIR

IDENTITY_FILE = DATA_DIR / "identity_corrections.json"
MAX_RECORDS = 5000
_LOCK = threading.Lock()


def query_method(query: str) -> str:
    """How the user identified the profile: a Steam URL, a SteamID64, or a nickname (the last is not a ground truth)."""
    q = query.strip()
    if re.search(r"steamcommunity\.com/(id|profiles)/", q):
        return "url"
    if re.fullmatch(r"7656119\d{10}", q):
        return "steamid64"
    return "nick"


def build_correction(
    *, scan_id: str, ocr_nick: str, before: dict, after: dict, query: str, now: datetime | None = None
) -> dict | None:
    """One record, or None when the chosen SteamID did not change."""
    after_id = after.get("steamid64")
    before_id = before.get("steamid64")
    if not after_id or after_id == before_id:
        return None
    candidates = before.get("candidates") or []
    rank = next((i for i, c in enumerate(candidates) if c.get("steamid64") == after_id), None)
    stamp = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    return {
        "ts": stamp,
        "scan_id": scan_id,
        "ocr_nick": ocr_nick,
        "before_steamid64": before_id,
        "after_steamid64": after_id,
        "method": query_method(query),
        "chosen_rank": rank,
        "candidates": [
            {
                "steamid64": c.get("steamid64"),
                "profile_name": c.get("profile_name"),
                "score": c.get("score"),
                "avatar_match": c.get("avatar_match"),
                "in_cs2": bool(c.get("in_cs2")),
            }
            for c in candidates
        ],
    }


def record(entry: dict, path: Path = IDENTITY_FILE) -> None:
    """Append one record. Thread-safe and atomic; a corrupt file is moved aside, never silently overwritten."""
    with _LOCK:
        records: list[Any] = []
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8") or "[]")
                if isinstance(loaded, list):
                    records = loaded
            except (OSError, json.JSONDecodeError):
                path.replace(path.with_name(path.name + ".corrupt"))
        records.append(entry)
        atomic_write_text(path, json.dumps(records[-MAX_RECORDS:], ensure_ascii=False, indent=2))
