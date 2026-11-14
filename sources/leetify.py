"""Leetify: lifetime and recent-window aim/winrate stats for a SteamID64."""

from __future__ import annotations

from typing import Any, Optional

from sources._util import num_or_none
from config_fields import Secret
from sources.base import LegacySlotSource, SourceContext, SourceResult


class LeetifySource(LegacySlotSource):
    id = "leetify"
    label = "Leetify"
    slot = ("leetify",)
    recheckable = True
    in_coverage = True
    # Raw key in config (not masked by the public view); an empty value clears it, as it always has.
    settings = (
        Secret(
            name="leetify_api_key", type="text", default="", label="Leetify API key", group="api_keys",
            help="Optional Leetify key; Leetify data is fetched without it when not set.",
            keep_on_empty=False, masked=False,
        ),
    )

    def fetch_raw(self, steamid64: str, ctx: SourceContext) -> Optional[dict]:
        import steam_lookup  # late import: keeps sources importable without the scanner stack

        return steam_lookup._fetch_leetify(steamid64)

    def normalize(self, raw: Any) -> SourceResult:
        if isinstance(raw, dict) and raw:
            return SourceResult("ok", raw)
        return SourceResult("not_found", raw if isinstance(raw, dict) else {})

    def features(self, result: SourceResult) -> dict[str, Any]:
        # Same fields and rounding as feedback_store.build_feedback_entry["features"]["leetify"],
        # except `present`, which is simply result.ok.
        lf = result.data
        return {
            "aim": num_or_none(lf.get("aim")),
            "reaction_time_ms": num_or_none(lf.get("reaction_time_ms")),
            "preaim": num_or_none(lf.get("preaim")),
            "accuracy_head": num_or_none(lf.get("accuracy_head")),
            "winrate": num_or_none(lf.get("winrate")),
            "total_matches": lf.get("total_matches"),
        }

    def ai_compact(self, result: SourceResult) -> dict[str, Any]:
        # Verbatim the "leetify" block of ai_analyzer._compact_player_for_ai.
        lf = result.data
        return {
            "found": bool(lf),
            "total_matches": lf.get("total_matches"),
            "aim": lf.get("aim"),
            "reaction_time_ms": lf.get("reaction_time_ms"),
            "preaim": lf.get("preaim"),
            "kd": lf.get("kd"),
            "hs_pct": lf.get("hs_pct"),
            "winrate": lf.get("winrate"),
            "leetify_rating": lf.get("leetify_rating"),
            "premier_rank": lf.get("premier") if lf.get("premier") is not None else lf.get("premier_rank"),
            "faceit_level": lf.get("faceit") if lf.get("faceit") is not None else lf.get("faceit_level"),
            "bans": lf.get("bans") or [],
        }
