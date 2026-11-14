"""CSTracker.gg: trust rating, aim/utility metrics and recent matches (scraped; may be blocked)."""

from __future__ import annotations

from typing import Any, Optional

from sources.base import LegacySlotSource, SourceContext, SourceResult

_STATUS_MAP = {"ok": "ok", "not_found": "not_found", "blocked": "blocked"}


class CsTrackerSource(LegacySlotSource):
    id = "cstracker"
    label = "CSTracker"
    slot = ("extra_stats", "cstracker")
    recheckable = True
    in_coverage = True

    def fetch_raw(self, steamid64: str, ctx: SourceContext) -> Optional[dict]:
        import lookup.steam_lookup as steam_lookup

        return steam_lookup._fetch_cstracker(steamid64)

    def normalize(self, raw: Any) -> SourceResult:
        if not raw:  # None or {} : the fetch was skipped (no SteamID64) or nothing stored
            return SourceResult("unavailable", {})
        if not isinstance(raw, dict):
            return SourceResult("error", {})
        raw_status = raw.get("status")
        status = _STATUS_MAP.get(raw_status, "error") if isinstance(raw_status, str) else "error"
        return SourceResult(status, raw)

    def ai_compact(self, result: SourceResult) -> Optional[dict]:
        # ai_analyzer today sends the whole extra_stats dict; this is the cstracker part of it.
        return result.data or None
