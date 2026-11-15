"""FACEIT: CS2 skill level, ELO and lifetime stats (needs a FACEIT API key)."""

from __future__ import annotations

import json
import urllib.parse
from datetime import UTC, datetime
from typing import Any

import storage.api_keys as api_keys
from lookup.http_client import http_get as _http_get
from sources._util import _safe_float, _safe_int
from sources.base import LegacySlotSource, SourceContext, SourceResult
from storage.config_fields import Secret


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def fetch_faceit_active_bans(player_id: str, headers: dict) -> list[dict] | None:
    """Bans on the FACEIT account whose `ends_at` is still in the future.

    Returns [] when the account has no active ban, and None when the bans endpoint could not be read. The two
    must stay apart: None means "unknown", not "clean".
    """
    raw = _http_get(
        f"https://open.faceit.com/data/v4/players/{urllib.parse.quote(player_id)}/bans",
        timeout=4.5, headers=headers,
    )
    if not raw:
        return None
    try:
        d = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None
    items = d.get("items") if isinstance(d, dict) else None
    if not isinstance(items, list):
        return None
    now = datetime.now(UTC)
    active: list[dict] = []
    for ban in items:
        if not isinstance(ban, dict):
            continue
        ends = _parse_ts(ban.get("ends_at"))
        if ends is None or ends <= now:
            continue
        active.append({"reason": str(ban.get("reason") or "")[:200], "ends_at": ends.isoformat(timespec="seconds")})
    return active


def fetch_faceit_stats(steamid64: str) -> dict:
    api_key = api_keys._get_faceit_api_key()
    if not api_key or not steamid64:
        return {}
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    player_url = (
        f"https://open.faceit.com/data/v4/players"
        f"?game=cs2&game_player_id={urllib.parse.quote(steamid64)}"
    )
    raw = _http_get(player_url, timeout=4.5, headers=headers)
    if not raw:
        return {}
    try:
        d = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(d, dict) or not d.get("player_id"):
        return {}

    player_id = str(d["player_id"])
    cs2_info = (d.get("games") or {}).get("cs2") or {}
    raw_url = str(d.get("faceit_url") or "")
    faceit_url = raw_url.replace("{lang}", "en") if raw_url else None

    result: dict = {
        "found": True,
        "player_id": player_id,
        "nickname": d.get("nickname"),
        "faceit_url": faceit_url,
        "skill_level": _safe_int(cs2_info.get("skill_level")),
        "faceit_elo": _safe_int(cs2_info.get("faceit_elo")),
        "matches": None,
        "winrate": None,
        "kd": None,
        "hs_pct": None,
        "active_bans": fetch_faceit_active_bans(player_id, headers),
    }

    stats_url = f"https://open.faceit.com/data/v4/players/{urllib.parse.quote(player_id)}/stats/cs2"
    raw_stats = _http_get(stats_url, timeout=4.5, headers=headers)
    if raw_stats:
        try:
            sd = json.loads(raw_stats.decode("utf-8", errors="replace"))
            lifetime = sd.get("lifetime") if isinstance(sd, dict) else None
            if isinstance(lifetime, dict):
                result["matches"] = _safe_int(lifetime.get("Matches"))
                wr = _safe_float(lifetime.get("Win Rate %"))
                kd = _safe_float(lifetime.get("Average K/D Ratio"))
                hs = _safe_float(lifetime.get("Average Headshots %"))
                result["winrate"] = round(wr, 1) if wr is not None else None
                result["kd"] = round(kd, 2) if kd is not None else None
                result["hs_pct"] = round(hs, 1) if hs is not None else None
        except json.JSONDecodeError:
            pass

    return result


class FaceitSource(LegacySlotSource):
    id = "faceit"
    label = "FACEIT"
    slot = ("faceit",)
    recheckable = False
    in_coverage = True
    payload_key = "faceit"
    settings = (
        Secret(
            name="faceit_api_key", type="text", default="", label="FACEIT API key", group="api_keys",
            help="Needed for FACEIT stats. Also read from FACEIT_API_KEY.", env=("FACEIT_API_KEY",),
        ),
    )

    def fetch_raw(self, steamid64: str, ctx: SourceContext) -> dict | None:
        return fetch_faceit_stats(steamid64)

    def is_enabled(self, ctx: SourceContext) -> bool:
        return bool(api_keys._get_faceit_api_key())

    def normalize(self, raw: Any) -> SourceResult:
        data = raw if isinstance(raw, dict) else {}
        return SourceResult("ok" if data.get("found") else "not_found", data)

    def ai_compact(self, result: SourceResult) -> dict | None:
        # ai_analyzer adds data["faceit"] only when the raw dict is truthy; this returns that dict or None.
        return result.data or None
