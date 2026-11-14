"""SQLite player database (players.db): cross-scan history per SteamID64.

Additive to the JSON persistence (scans_history.json, profile_cache.json); nothing
here reads or migrates those files. One connection is shared behind a lock
(check_same_thread=False), so the threaded HTTP server can call it safely.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from storage.paths import DATA_DIR

DB_FILE = DATA_DIR / "players.db"

STEAMID64_RE = re.compile(r"^\d{17}$")
TAG_MAX_LEN = 64
NOTES_MAX_LEN = 4000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    steamid64  TEXT PRIMARY KEY,
    last_nick  TEXT,
    first_seen TEXT NOT NULL,
    last_seen  TEXT,
    tag        TEXT,
    notes      TEXT
);
CREATE TABLE IF NOT EXISTS sightings (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    steamid64          TEXT NOT NULL REFERENCES players(steamid64),
    scan_id            TEXT NOT NULL,
    nick               TEXT,
    seen_at            TEXT NOT NULL,
    cheat_probability  REAL,
    smurf_probability  REAL,
    risk_level         TEXT,
    UNIQUE (scan_id, steamid64)
);
CREATE INDEX IF NOT EXISTS idx_sightings_steamid_seen ON sightings (steamid64, seen_at);
"""


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def is_valid_steamid64(value: str) -> bool:
    return bool(STEAMID64_RE.match(value or ""))


class PlayerDB:
    def __init__(self, path: Path | str = DB_FILE) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def upsert_player(self, steamid64: str, nick: Optional[str], seen_at: Optional[str] = None) -> None:
        """Create the player or refresh last_nick/last_seen; first_seen is never overwritten."""
        seen_at = seen_at or _now_iso()
        with self._lock, self._conn:
            self._upsert_player_locked(steamid64, nick, seen_at)

    def _upsert_player_locked(self, steamid64: str, nick: Optional[str], seen_at: str) -> None:
        self._conn.execute(
            """
            INSERT INTO players (steamid64, last_nick, first_seen, last_seen)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(steamid64) DO UPDATE SET
                last_nick = excluded.last_nick,
                last_seen = excluded.last_seen
            """,
            (steamid64, nick, seen_at, seen_at),
        )

    def record_sightings(self, scan_id: str, players: list[dict], seen_at: Optional[str] = None) -> int:
        """Store one sighting per player that has a SteamID64, in a single transaction.

        Re-recording the same (scan_id, steamid64) is ignored. Returns the number of
        new sightings inserted.
        """
        seen_at = seen_at or _now_iso()
        inserted = 0
        with self._lock, self._conn:
            for p in players:
                steamid64 = ((p.get("steam") or {}).get("steamid64") or "").strip()
                if not is_valid_steamid64(steamid64):
                    continue
                nick = p.get("nick")
                self._upsert_player_locked(steamid64, nick, seen_at)
                cur = self._conn.execute(
                    """
                    INSERT OR IGNORE INTO sightings
                        (steamid64, scan_id, nick, seen_at, cheat_probability, smurf_probability, risk_level)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        steamid64,
                        scan_id,
                        nick,
                        seen_at,
                        p.get("cheat_probability"),
                        p.get("smurf_probability"),
                        p.get("risk_level"),
                    ),
                )
                inserted += cur.rowcount
        return inserted

    def set_player_meta(self, steamid64: str, **fields: Optional[str]) -> bool:
        """Update tag and/or notes of an existing player; fields not passed are left as-is.
        Returns False if the player is unknown."""
        unknown = set(fields) - {"tag", "notes"}
        if unknown:
            raise ValueError(f"unsupported fields: {sorted(unknown)}")
        if not fields:
            return self.get_player(steamid64, sightings_limit=0) is not None
        assignments = ", ".join(f"{name} = ?" for name in fields)
        with self._lock, self._conn:
            cur = self._conn.execute(
                f"UPDATE players SET {assignments} WHERE steamid64 = ?",
                (*fields.values(), steamid64),
            )
            return cur.rowcount > 0

    def get_player(self, steamid64: str, sightings_limit: int = 50) -> Optional[dict[str, Any]]:
        """Return the player profile plus the newest `sightings_limit` sightings, or None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT steamid64, last_nick, first_seen, last_seen, tag, notes FROM players WHERE steamid64 = ?",
                (steamid64,),
            ).fetchone()
            if row is None:
                return None
            rows = self._conn.execute(
                """
                SELECT scan_id, nick, seen_at, cheat_probability, smurf_probability, risk_level
                FROM sightings WHERE steamid64 = ?
                ORDER BY seen_at DESC, id DESC LIMIT ?
                """,
                (steamid64, int(sightings_limit)),
            ).fetchall()
            total = self._conn.execute(
                "SELECT COUNT(*) FROM sightings WHERE steamid64 = ?", (steamid64,)
            ).fetchone()[0]
        profile = dict(row)
        profile["sighting_count"] = total
        profile["sightings"] = [dict(r) for r in rows]
        return profile


_default_db: Optional[PlayerDB] = None
_default_lock = threading.Lock()


def get_db() -> PlayerDB:
    """Process-wide PlayerDB on data/players.db (created on first use)."""
    global _default_db
    with _default_lock:
        if _default_db is None:
            _default_db = PlayerDB(DB_FILE)
        return _default_db


def record_scan_sightings(scan_record: dict) -> int:
    """Record sightings for a finalized scan. Raises sqlite3.Error on DB failure;
    the caller decides how to handle it (the scan itself must not fail)."""
    return get_db().record_sightings(
        scan_id=scan_record["id"],
        players=scan_record.get("players") or [],
        seen_at=scan_record.get("created_at") or None,
    )
