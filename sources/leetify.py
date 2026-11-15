"""Leetify: lifetime and recent-window aim/winrate stats for a SteamID64."""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from lookup.http_client import http_get_status as _http_get_status
from sources._util import num_or_none, round_num
from storage.config_fields import Secret
from sources.base import LegacySlotSource, SourceContext, SourceResult

_LOG = logging.getLogger("pton")


LEETIFY_API_BASE = "https://api-public.cs-prod.leetify.com"
LEETIFY_PROFILE_URL = "https://leetify.com/app/profile/{}"
LEETIFY_TIMEOUT_SEC = 8.0


def _leetify_api_key() -> str:
    try:
        from storage.config_store import load_config

        return str(load_config().get("leetify_api_key") or "").strip()
    except Exception:
        return ""


def _leetify_get(path: str, steamid64: str, api_key: str) -> tuple[int, Any]:
    """GET a Leetify public API path. Returns (http_status, parsed_json_or_None)."""
    url = f"{LEETIFY_API_BASE}{path}?steam64_id={urllib.parse.quote(steamid64)}"
    headers = {"Accept": "application/json"}
    if api_key:
        headers["_leetify_key"] = api_key
    status, raw = _http_get_status(url, timeout=LEETIFY_TIMEOUT_SEC, headers=headers)
    if status != 200 or not raw:
        return status, None
    try:
        return status, json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return status, None


_num = round_num  # module-local name used by fetch_leetify and the Leetify payload helpers


def _round_stats(d: dict) -> dict:
    out: dict = {}
    for k, v in d.items():
        out[k] = round(float(v), 4) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
    return out


def fetch_leetify(steamid64: str) -> Optional[dict]:
    """Fetch the full Leetify picture: profile, stats, ranks, and per-match history.

    Uses the API key from config.json (leetify_api_key) when set. Without a key the
    API still answers, under lower rate limits. Returns None when Leetify has no profile.
    """
    if not steamid64:
        return None
    api_key = _leetify_api_key()
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_profile = pool.submit(_leetify_get, "/v3/profile", steamid64, api_key)
        f_matches = pool.submit(_leetify_get, "/v3/profile/matches", steamid64, api_key)
        p_status, d = f_profile.result()
        m_status, matches_raw = f_matches.result()
    elapsed = time.monotonic() - started

    if not isinstance(d, dict) or not d.get("steam64_id"):
        _LOG.info("leetify steamid64=%s no profile (status=%s key=%s) in %.2fs",
                  steamid64, p_status, bool(api_key), elapsed)
        return None

    ranks = d.get("ranks") or {}
    rating = d.get("rating") or {}
    stats = d.get("stats") or {}
    winrate_raw = d.get("winrate")
    winrate = _num(winrate_raw, 3)
    winrate = round(winrate * 100.0, 1) if winrate is not None else None

    matches: list[dict] = []
    if isinstance(matches_raw, list):
        for m in matches_raw:
            if not isinstance(m, dict):
                continue
            mine = next(
                (x for x in (m.get("stats") or []) if isinstance(x, dict) and x.get("steam64_id") == steamid64),
                None,
            )
            matches.append({
                "id": m.get("id"),
                "finished_at": m.get("finished_at"),
                "data_source": m.get("data_source"),
                "data_source_match_id": m.get("data_source_match_id"),
                "map_name": m.get("map_name"),
                "has_banned_player": m.get("has_banned_player"),
                "team_scores": m.get("team_scores") or [],
                "player": _round_stats(mine) if mine else None,
            })

    def _sum(key: str) -> float:
        return sum(_num(m["player"].get(key)) or 0 for m in matches if m["player"])

    kills = int(_sum("total_kills"))
    deaths = int(_sum("total_deaths"))
    assists = int(_sum("total_assists"))
    hs_kills = int(_sum("total_hs_kills"))
    kd = round(kills / deaths, 2) if deaths else None
    hs_pct = round(hs_kills / kills * 100.0, 1) if kills else None

    result: dict = {
        "url": LEETIFY_PROFILE_URL.format(steamid64),
        "privacy_mode": d.get("privacy_mode"),
        "name": d.get("name"),
        "total_matches": d.get("total_matches"),
        "winrate": winrate,
        "first_match_date": (d.get("first_match_date") or "")[:10] or None,
        "bans": d.get("bans") or [],
        "ranks": {k: ranks.get(k) for k in ("leetify", "premier", "faceit", "faceit_elo", "wingman", "renown", "competitive")},
        "rating": {k: _num(rating.get(k), 2) for k in ("aim", "positioning", "utility", "clutch", "opening", "ct_leetify", "t_leetify")},
        "stats": _round_stats(stats),
        "recent_teammates": d.get("recent_teammates") or [],
        "matches": matches,
        "aggregate": {
            "kills": kills, "deaths": deaths, "assists": assists, "hs_kills": hs_kills,
            "kd": kd, "hs_pct": hs_pct,
        },
        # Flat keys read by scoring and ai_analyzer.
        "leetify_rating": _num(ranks.get("leetify"), 2),
        "premier": ranks.get("premier"),
        "faceit": ranks.get("faceit"),
        "faceit_elo": ranks.get("faceit_elo"),
        "aim": _num(rating.get("aim"), 1),
        "positioning": _num(rating.get("positioning"), 1),
        "reaction_time_ms": _num(stats.get("reaction_time_ms"), 0),
        "preaim": _num(stats.get("preaim"), 1),
        "accuracy_head": _num(stats.get("accuracy_head"), 1),
        "kd": kd,
        "hs_pct": hs_pct,
        # full: profile + match history + stats; partial: profile without them.
        "coverage": "full" if (matches and stats) else "partial",
        "fetch": {
            "profile_status": p_status,
            "matches_status": m_status,
            "matches_count": len(matches),
            "key_used": bool(api_key),
        },
    }
    _LOG.info("leetify steamid64=%s ok matches=%d key=%s in %.2fs",
              steamid64, len(matches), bool(api_key), elapsed)
    return result


class LeetifySource(LegacySlotSource):
    id = "leetify"
    label = "Leetify"
    slot = ("leetify",)
    recheckable = True
    in_coverage = True
    payload_key = "leetify"
    payload_always = True
    # Masked like the other API keys: the public view shows only source/has/masked. An empty value in
    # POST /api/config keeps the stored key; `clear_leetify_api_key: true` removes it.
    settings = (
        Secret(
            name="leetify_api_key", type="text", default="", label="Leetify API key", group="api_keys",
            help="Optional Leetify key; Leetify data is fetched without it when not set.",
        ),
    )

    def fetch_raw(self, steamid64: str, ctx: SourceContext) -> Optional[dict]:
        return fetch_leetify(steamid64)

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
