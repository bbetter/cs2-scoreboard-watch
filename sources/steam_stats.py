"""Steam CS2 lifetime stats (GetUserStatsForGame, appid 730). Needs a Steam Web API key."""

from __future__ import annotations

from typing import Any

from sources.base import LegacySlotSource, SourceContext, SourceResult


class SteamStatsSource(LegacySlotSource):
    id = "steam_stats"
    label = "Steam CS2 stats"
    slot = ("cs2_stats",)
    recheckable = False
    in_coverage = False  # the legacy steam.coverage map has no such key

    def fetch_raw(self, steamid64: str, ctx: SourceContext) -> Any:
        import steam_lookup

        key = ctx.steam_api_key or steam_lookup._get_steam_api_key()
        return steam_lookup._fetch_steam_cs2_stats(steamid64, key)

    def is_enabled(self, ctx: SourceContext) -> bool:
        import steam_lookup

        return bool(ctx.steam_api_key or steam_lookup._get_steam_api_key())

    def normalize(self, raw: Any) -> SourceResult:
        if isinstance(raw, dict) and raw:
            return SourceResult("ok", raw)
        return SourceResult("not_found", raw if isinstance(raw, dict) else {})
