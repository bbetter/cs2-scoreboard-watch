"""FACEIT: CS2 skill level, ELO and lifetime stats (needs a FACEIT API key)."""

from __future__ import annotations

from typing import Any, Optional

from config_fields import Secret
from sources.base import LegacySlotSource, SourceContext, SourceResult


class FaceitSource(LegacySlotSource):
    id = "faceit"
    label = "FACEIT"
    slot = ("faceit",)
    recheckable = False
    in_coverage = True
    settings = (
        Secret(
            name="faceit_api_key", type="text", default="", label="FACEIT API key", group="api_keys",
            help="Needed for FACEIT stats. Also read from FACEIT_API_KEY.", env=("FACEIT_API_KEY",),
        ),
    )

    def fetch_raw(self, steamid64: str, ctx: SourceContext) -> Optional[dict]:
        import steam_lookup

        return steam_lookup._fetch_faceit_stats(steamid64)

    def is_enabled(self, ctx: SourceContext) -> bool:
        import steam_lookup

        return bool(steam_lookup._get_faceit_api_key())

    def normalize(self, raw: Any) -> SourceResult:
        data = raw if isinstance(raw, dict) else {}
        return SourceResult("ok" if data.get("found") else "not_found", data)

    def ai_compact(self, result: SourceResult) -> Optional[dict]:
        # ai_analyzer adds data["faceit"] only when the raw dict is truthy; this returns that dict or None.
        return result.data or None
