"""Labelled feedback for scoring calibration: append-only data/feedback.json.

Each entry records the operator's label for one player from one scan, together with the
cheat/smurf probability the scorer gave at that moment and a small feature snapshot, so
thresholds in lookup/steam_lookup.py can be checked against real labels later.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from storage.atomic_io import atomic_write_text
from sources import REGISTRY

from storage.paths import DATA_DIR

FEEDBACK_FILE = DATA_DIR / "feedback.json"
VALID_LABELS = ("cheater", "legit", "unsure")
MAX_NICK_LEN = 100

_FEEDBACK_LOCK = threading.Lock()


def validate_feedback_payload(payload: Any) -> tuple[Optional[dict], Optional[str]]:
    """Return (clean_fields, None) or (None, error_message). Does not touch any state."""
    if not isinstance(payload, dict):
        return None, "Invalid feedback payload"
    scan_id = payload.get("scan_id")
    nick = payload.get("nick")
    label = payload.get("label")
    if not isinstance(scan_id, str) or not scan_id.strip():
        return None, "scan_id is required"
    if not isinstance(nick, str) or not nick.strip() or len(nick) > MAX_NICK_LEN:
        return None, "nick is required"
    if label not in VALID_LABELS:
        return None, "label must be one of: cheater, legit, unsure"
    return {"scan_id": scan_id.strip(), "nick": nick.strip(), "label": label}, None


def _num_or_none(val: Any) -> Optional[float]:
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    return round(float(val), 2)


def build_feedback_entry(scan_id: str, label: str, player: dict) -> dict:
    """Build one feedback record from a stored player dict (PlayerReport as dict)."""
    steam = player.get("steam") or {}
    cst = (steam.get("extra_stats") or {}).get("cstracker") or {}
    leetify = REGISTRY.get("leetify")
    lf_result = leetify.normalize(steam.get("leetify"))
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
        "leetify": {"present": lf_result.ok, **leetify.features(lf_result)},
        "cstracker_status": cst.get("status"),
    }
    return {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
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
