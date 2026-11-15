"""Steam CS2 lifetime stats (GetUserStatsForGame, appid 730). Needs a Steam Web API key."""

from __future__ import annotations

import json
import urllib.parse
from typing import Any, Optional

import storage.api_keys as api_keys
from lookup.http_client import http_get as _http_get
from sources._util import _safe_float, _safe_int
from storage.config_fields import Secret
from sources.base import LegacySlotSource, SourceContext, SourceResult


def fetch_steam_cs2_stats(steamid64: str, api_key: str) -> dict:
    if not steamid64 or not api_key:
        return {}
    url = (
        f"https://api.steampowered.com/ISteamUserStats/GetUserStatsForGame/v2/"
        f"?appid=730&key={urllib.parse.quote(api_key)}&steamid={urllib.parse.quote(steamid64)}"
    )
    raw = _http_get(url, timeout=4.0)
    if not raw:
        return {}
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {}
    stats_list = (payload.get("playerstats") or {}).get("stats")
    if not isinstance(stats_list, list) or not stats_list:
        return {}

    raw_map: dict[str, float] = {}
    for item in stats_list:
        if isinstance(item, dict) and item.get("name"):
            val = _safe_float(item.get("value"))
            if val is not None:
                raw_map[str(item["name"])] = val

    total_kills = _safe_int(raw_map.get("total_kills"))
    total_deaths = _safe_int(raw_map.get("total_deaths"))
    total_hs = _safe_int(raw_map.get("total_kills_headshot"))
    total_wins = _safe_int(raw_map.get("total_wins"))
    total_matches = _safe_int(raw_map.get("total_matches_played"))

    if total_kills is None and total_deaths is None and total_matches is None:
        return {}

    kd: Optional[float] = None
    if total_kills is not None and total_deaths is not None:
        kd = round(total_kills / total_deaths, 2) if total_deaths > 0 else float(total_kills)

    hs_pct: Optional[float] = None
    if total_kills is not None and total_kills > 0 and total_hs is not None:
        hs_pct = round((total_hs / total_kills) * 100.0, 1)

    return {
        "total_kills": total_kills,
        "total_deaths": total_deaths,
        "total_kills_headshot": total_hs,
        "total_wins": total_wins,
        "total_matches_played": total_matches,
        "kd": kd,
        "hs_pct": hs_pct,
    }


class SteamStatsSource(LegacySlotSource):
    id = "steam_stats"
    label = "Steam CS2 stats"
    slot = ("cs2_stats",)
    recheckable = False
    in_coverage = False  # the legacy steam.coverage map has no such key
    payload_key = None  # lives in the AI "steam" block as cs2_stats
    # Steam Web API key: also used for profile lookups; declared here because the CS2-stats source needs it.
    settings = (
        Secret(
            name="steam_api_key", type="text", default="", label="Steam Web API key", group="api_keys",
            help="Steam Web API key. Also read from STEAM_API_KEY.", env=("STEAM_API_KEY",),
        ),
    )

    def fetch_raw(self, steamid64: str, ctx: SourceContext) -> Any:
        key = ctx.steam_api_key or api_keys._get_steam_api_key()
        return fetch_steam_cs2_stats(steamid64, key)

    def is_enabled(self, ctx: SourceContext) -> bool:
        return bool(ctx.steam_api_key or api_keys._get_steam_api_key())

    def normalize(self, raw: Any) -> SourceResult:
        if isinstance(raw, dict) and raw:
            return SourceResult("ok", raw)
        return SourceResult("not_found", raw if isinstance(raw, dict) else {})
