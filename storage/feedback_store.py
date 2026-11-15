"""Labelled feedback for scoring calibration: append-only data/feedback.json.

Each entry records the operator's label for one player from one scan, together with the
cheat/smurf probability the scorer gave at that moment and a small feature snapshot, so
thresholds in lookup/steam_lookup.py can be checked against real labels later.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sources import REGISTRY
from storage.atomic_io import atomic_write_text
from storage.paths import DATA_DIR

FEEDBACK_FILE = DATA_DIR / "feedback.json"
VALID_LABELS = ("cheater", "legit", "unsure")
MAX_NICK_LEN = 100
MAX_PLAYER_UID_LEN = 64

_FEEDBACK_LOCK = threading.Lock()


def validate_feedback_payload(payload: Any) -> tuple[dict | None, str | None]:
    """Return (clean_fields, None) or (None, error_message). Does not touch any state.

    A valid `nick` or a valid `player_uid` is enough; at least one of the two must be present. `nick` is returned as
    "" when only the uid was sent.
    """
    if not isinstance(payload, dict):
        return None, "Invalid feedback payload"
    scan_id = payload.get("scan_id")
    nick = payload.get("nick")
    player_uid = payload.get("player_uid")
    label = payload.get("label")
    if not isinstance(scan_id, str) or not scan_id.strip():
        return None, "scan_id is required"
    nick_ok = isinstance(nick, str) and bool(nick.strip()) and len(nick) <= MAX_NICK_LEN
    uid_ok = isinstance(player_uid, str) and 0 < len(player_uid.strip()) <= MAX_PLAYER_UID_LEN
    if not nick_ok and not uid_ok:
        return None, "nick or player_uid is required"
    if label not in VALID_LABELS:
        return None, "label must be one of: cheater, legit, unsure"
    return {
        "scan_id": scan_id.strip(),
        "nick": nick.strip() if nick_ok else "",
        "player_uid": player_uid.strip() if uid_ok else None,
        "label": label,
    }, None


def _num_or_none(val: Any) -> float | None:
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    return round(float(val), 2)


def build_feedback_entry(scan_id: str, label: str, player: dict) -> dict:
    """Build one feedback record from a stored player dict (PlayerReport as dict)."""
    steam = player.get("steam") or {}
    cst = (steam.get("extra_stats") or {}).get("cstracker") or {}
    features = {
        "kd": _num_or_none(player.get("kd")),
        "hs": _num_or_none(player.get("hs")),
        "level": player.get("level"),
        "anomaly_score": _num_or_none(player.get("anomaly_score")),
        "uncertainty": _num_or_none(player.get("uncertainty")),
        "account_age_years": _num_or_none(steam.get("account_age_years")),
        "cs2_hours": _num_or_none(steam.get("cs2_hours")),
        "vac_banned": bool(steam.get("vac_banned")),
        "privacy_state": steam.get("privacy_state"),
    }
    for src in REGISTRY:
        name = src.payload_name()
        if name is None:
            continue
        res = src.normalize(src.read_raw(steam))
        block = src.features(res)
        if src.payload_always or block:
            features[name] = {"present": res.ok, **block}
    features["cstracker_status"] = cst.get("status")
    return {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "scan_id": scan_id,
        "nick": player.get("nick"),
        "steamid64": steam.get("steamid64"),
        "label": label,
        "cheat_probability": _num_or_none(player.get("cheat_probability")),
        "smurf_probability": _num_or_none(player.get("smurf_probability")),
        "features": features,
    }


def append_feedback(entry: dict, path: Path = FEEDBACK_FILE) -> None:
    """Append one record to the JSON list at `path`. Thread-safe; writes atomically."""
    with _FEEDBACK_LOCK:
        records: list = []
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8") or "[]")
                if isinstance(loaded, list):
                    records = loaded
            except (OSError, json.JSONDecodeError):
                # Never overwrite a corrupt file silently: keep it aside and start over.
                path.replace(path.with_name(path.name + ".corrupt"))
                records = []
        records.append(entry)
        atomic_write_text(path, json.dumps(records, ensure_ascii=False, indent=2))
