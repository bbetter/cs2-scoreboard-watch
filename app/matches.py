"""Grouping of scans into matches. Pure functions: no HTTP, no store, no locks.

The caller holds whatever lock protects `scans` while it calls `build_matches`.
"""
from __future__ import annotations

from datetime import datetime


def build_matches(scans: list[dict]) -> list[dict]:
    """Group scan records (newest first) into matches by `match_id`.

    A scan without `match_id` falls back to `session_<date of created_at>`.
    """
    groups: dict[str, list[dict]] = {}
    for s in scans:
        mid = s.get("match_id") or f"session_{s['created_at'][:10]}"
        groups.setdefault(mid, []).append(s)

    matches: list[dict] = []
    for mid, group in groups.items():
        # scans are newest-first
        newest = group[0]
        oldest = group[-1]
        try:
            dur = max(0, round((datetime.fromisoformat(newest["created_at"]) - datetime.fromisoformat(oldest["created_at"])).total_seconds()))
        except Exception:
            dur = 0
        max_risk = max(
            (p.get("cheat_probability", 0) for s in group for p in s.get("players", [])),
            default=0.0,
        )
        map_name = next((s.get("map_name") for s in group if s.get("map_name")), None)
        matches.append({
            "match_id": mid,
            "map_name": map_name,
            "date": newest.get("date") or newest["created_at"][:10],
            "started_at": oldest["created_at"],
            "ended_at": newest["created_at"],
            "duration_sec": dur,
            "scan_count": len(group),
            "max_risk": round(max_risk, 1),
            "scans": [
                {
                    "id": s["id"],
                    "created_at": s["created_at"],
                    "player_count": s["player_count"],
                    "image_name": s["image_name"],
                }
                for s in group
            ],
        })
    return matches
