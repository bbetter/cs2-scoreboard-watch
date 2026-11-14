"""Resolve CS2 player nicknames to Steam + Leetify profiles and compute risk scores."""

from __future__ import annotations

import html
import json
import logging
import math
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

from storage.profile_cache import ProfileCache
from lookup.nick_match import best_match_index
from lookup.http_client import USER_AGENT, http_get as _http_get, http_get_status as _http_get_status  # noqa: F401
from vision.scoreboard_scan import Player
from scoring import compute_scores, compute_uncertainty_and_weights, recent_aim_signals, risk_level
from sources import REGISTRY
from storage.config_store import DEFAULT_AI_WEIGHTS
from storage.paths import DATA_DIR, LOG_DIR

import sources.cstracker as cstracker_source
import sources.faceit as faceit_source
import sources.leetify as leetify_source
import sources.steam_stats as steam_stats_source
from sources.leetify import _num  # noqa: F401  # re-exported: scoring/rules.py imports it from here
from storage.api_keys import (  # noqa: F401  # re-exported: app/server.py and tests import these names from here
    _get_faceit_api_key,
    _get_steam_api_key,
    reset_faceit_api_key_cache,
    reset_steam_api_key_cache,
)

_LOG = logging.getLogger("pton")


def _setup_pton_log() -> None:
    """Append INFO+ records to data/logs/pton.logs, once per process."""
    if any(isinstance(h, logging.FileHandler) for h in _LOG.handlers):
        return
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(LOG_DIR / "pton.logs", encoding="utf-8")
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _LOG.addHandler(handler)
    _LOG.setLevel(logging.INFO)
    _LOG.propagate = False



STEAMID64_BASE = 76561197960265728
CACHE_TTL_SEC = 86400
PROFILE_CACHE_FILE = DATA_DIR / "profile_cache.json"
# Leetify data lives only in memory, keyed by SteamID64, for leetify_cache_ttl_sec (0 = not reused).
_LEETIFY_LOCK = threading.Lock()
_LEETIFY_MEM: dict[str, tuple[float, dict]] = {}
_SEARCH_LOCK = threading.Lock()
_LAST_SEARCH_TS = 0.0
# Steam Community search queue: minimum spacing between search request starts,
# cooldown after HTTP 429 (doubles per consecutive 429, reset on 200), and a bounded
# retry of rate-limited lookups inside enrich_players. In-memory only.
STEAM_SEARCH_MIN_INTERVAL_SEC = 0.55
STEAM_COOLDOWN_BASE_SEC = 5.0
STEAM_COOLDOWN_MAX_SEC = 120.0
STEAM_RETRY_ROUNDS = 3
STEAM_RETRY_BUDGET_SEC = 30.0
_STEAM_RL_LOCK = threading.Lock()
_STEAM_RL_STATE: dict[str, float] = {"until": 0.0, "streak": 0.0}


def _strip_leetify(data: dict) -> dict:
    """Copy of a profile dict without Leetify data (Leetify guidelines: do not store API data).

    The derived `sources` view is dropped too: it would copy the Leetify payload. It is rebuilt on read.
    """
    out = dict(data)
    out["leetify"] = None
    out["sources"] = {}
    return out


PROFILE_CACHE = ProfileCache(PROFILE_CACHE_FILE, strip=_strip_leetify, ttl_sec=CACHE_TTL_SEC)


def _leetify_cache_ttl_sec() -> int:
    try:
        from storage.config_store import load_config

        return max(0, int(load_config().get("leetify_cache_ttl_sec") or 0))
    except Exception:
        return 0


def _leetify_mem_get(steamid64: Optional[str], ttl: int) -> Optional[dict]:
    if not steamid64 or ttl <= 0:
        return None
    with _LEETIFY_LOCK:
        item = _LEETIFY_MEM.get(steamid64)
    if item and (time.time() - item[0]) < ttl:
        return dict(item[1])
    return None


def _leetify_mem_put(steamid64: Optional[str], data: Optional[dict], ttl: int) -> None:
    if not steamid64 or ttl <= 0 or not data:
        return
    with _LEETIFY_LOCK:
        _LEETIFY_MEM[steamid64] = (time.time(), dict(data))


def _leetify_cached_or_fetch(steamid64: Optional[str], ttl: int) -> Optional[dict]:
    if not steamid64:
        return None
    data = _leetify_mem_get(steamid64, ttl)
    if data is None:
        data = leetify_source.fetch_leetify(steamid64)
        _leetify_mem_put(steamid64, data, ttl)
    return data


@dataclass
class SteamProfile:
    query_nick: str
    profile_name: Optional[str] = None
    steamid64: Optional[str] = None
    vanity: Optional[str] = None
    profile_url: Optional[str] = None
    avatar_url: Optional[str] = None
    screenshot_avatar: Optional[str] = None
    privacy_state: Optional[str] = None
    online_state: Optional[str] = None
    in_cs2: bool = False
    vac_banned: bool = False
    trade_ban: Optional[str] = None
    is_limited: bool = False
    member_since: Optional[str] = None
    account_age_years: Optional[float] = None
    cs2_hours: Optional[float] = None
    lookup_source: str = "none"
    rip_url: str = ""
    rip_nick_url: str = ""
    leetify: Optional[dict] = None
    faceit: dict = field(default_factory=dict)
    cs2_stats: dict = field(default_factory=dict)
    extra_stats: dict = field(default_factory=dict)
    search_log: list[str] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)
    # Per-source flags: which sources returned data for this player (steam, leetify, faceit, cstracker).
    coverage: dict = field(default_factory=dict)
    # Derived uniform view {source_id: {status, data, fetched_at}} (see _refresh_sources_view).
    sources: dict = field(default_factory=dict)


def _source_coverage(profile: "SteamProfile") -> dict:
    # Per-source flags come from the registry (sources/); "steam" is the SteamID64 presence flag.
    return {"steam": bool(profile.steamid64), **REGISTRY.coverage(asdict(profile))}


def _refresh_sources_view(profile: "SteamProfile") -> None:
    """Set profile.sources to the registry's uniform view of every source that has data."""
    profile.sources = REGISTRY.sources_view(asdict(profile))


def _finalize_source_views(profile: "SteamProfile") -> None:
    """Single place that derives a profile's coverage flags and uniform sources view."""
    profile.coverage = _source_coverage(profile)
    _refresh_sources_view(profile)


@dataclass
class PlayerReport:
    nick: str
    kills: int
    deaths: int
    kd: float
    hs: float
    level: int
    anomaly_score: Optional[float]
    cheat_probability: float
    risk_level: str
    reasons: list[str] = field(default_factory=list)
    steam: dict = field(default_factory=dict)
    smurf_probability: float = 0.0
    smurf_risk_level: str = "low"
    uncertainty: float = 50.0
    effective_weights: dict = field(default_factory=dict)
    # Every scoring rule hit: [{component, rule, points, reason}] (see scoring/engine.py).
    score_breakdown: list = field(default_factory=list)
    # Stable per-scan player id (see scoreboard_scan.Player.player_uid); "" for rows that predate it.
    player_uid: str = ""


def _parse_member_age_years(member_since: Optional[str]) -> Optional[float]:
    if not member_since:
        return None
    for fmt in ("%B %d, %Y", "%d %B, %Y", "%b %d, %Y"):
        try:
            dt = datetime.strptime(member_since.strip(), fmt)
            return round((datetime.now() - dt).days / 365.25, 1)
        except ValueError:
            continue
    m = re.search(r"(19|20)\d{2}", member_since)
    if m:
        return max(0.0, float(datetime.now().year - int(m.group())))
    return None


def _parse_steam_xml(xml_bytes: bytes, vanity: Optional[str] = None, source: str = "xml") -> Optional[dict]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None
    if root.find("error") is not None:
        return None
    steamid64 = (root.findtext("steamID64") or "").strip() or None
    profile_name = (root.findtext("steamID") or "").strip() or None
    if not steamid64 and not profile_name:
        return None
    privacy = (root.findtext("privacyState") or "").strip() or None
    online_state = (root.findtext("onlineState") or "").strip().lower() or None
    state_msg = (root.findtext("stateMessage") or "").strip().lower()
    in_cs2 = online_state == "in-game" and ("counter-strike" in state_msg or "cs2" in state_msg)
    vac = (root.findtext("vacBanned") or "0").strip() == "1"
    trade_ban = (root.findtext("tradeBanState") or "").strip() or None
    limited = (root.findtext("isLimitedAccount") or "0").strip() == "1"
    member_since = (root.findtext("memberSince") or "").strip() or None
    avatar = (
        (root.findtext("avatarMedium") or "").strip()
        or (root.findtext("avatarIcon") or "").strip()
        or None
    )

    cs2_hours = None
    for game in root.findall("./mostPlayedGames/mostPlayedGame"):
        gname = (game.findtext("gameName") or "").lower()
        if "counter-strike" in gname:
            hrs_raw = (game.findtext("hoursOnRecord") or "").replace(",", "").strip()
            try:
                cs2_hours = float(hrs_raw)
            except ValueError:
                pass
            break

    profile_url = (
        f"https://steamcommunity.com/id/{urllib.parse.quote(vanity)}"
        if vanity
        else (f"https://steamcommunity.com/profiles/{steamid64}" if steamid64 else None)
    )
    return {
        "profile_name": profile_name,
        "steamid64": steamid64,
        "vanity": vanity,
        "profile_url": profile_url,
        "avatar_url": avatar,
        "privacy_state": privacy,
        "online_state": online_state,
        "in_cs2": in_cs2,
        "vac_banned": vac,
        "trade_ban": trade_ban if trade_ban and trade_ban.lower() != "none" else None,
        "is_limited": limited,
        "member_since": member_since,
        "account_age_years": _parse_member_age_years(member_since),
        "cs2_hours": cs2_hours,
        "lookup_source": source,
    }


def _cstracker_extra(steamid64: Optional[str]) -> dict:
    """extra_stats for a fresh profile: cstracker data plus a marker that CS2 stats were requested.

    A public profile can still hide game stats, leaving cs2_stats empty for good; the marker
    stops _cache_entry_current from treating such entries as stale on every lookup.
    """
    if not steamid64:
        return {}
    return {"cstracker": cstracker_source.fetch_cstracker(steamid64), "_cs2_checked": bool(_get_steam_api_key())}


def _fetch_external_stats(steamid64: Optional[str]) -> tuple[Optional[dict], dict, dict]:
    if not steamid64:
        return None, {}, {}
    steam_api_key = _get_steam_api_key()
    with ThreadPoolExecutor(max_workers=3) as pool:
        f_leetify = pool.submit(leetify_source.fetch_leetify, steamid64)
        f_faceit = pool.submit(faceit_source.fetch_faceit_stats, steamid64)
        f_cs2 = pool.submit(steam_stats_source.fetch_steam_cs2_stats, steamid64, steam_api_key) if steam_api_key else None
        leetify_data = f_leetify.result()
        faceit_data = f_faceit.result()
        cs2_stats = f_cs2.result() if f_cs2 else {}
    return leetify_data, faceit_data, cs2_stats


def _avatar_similarity(thumb: object, avatar_url: Optional[str]) -> float:
    if thumb is None or not avatar_url:
        return 0.0
    raw = _http_get(avatar_url, timeout=3.0)
    if not raw:
        return 0.0
    try:
        import io
        import numpy as np
        from PIL import Image

        resample = Image.Resampling.BILINEAR if hasattr(Image, "Resampling") else 2
        im = Image.open(io.BytesIO(raw)).convert("RGB").resize((16, 16), resample)
        cand = np.asarray(im, dtype=np.float32) / 255.0
        a = thumb.reshape(-1)
        b = cand.reshape(-1)
        l1_sim = max(0.0, 1.0 - float(np.mean(np.abs(a - b))) * 2.2)
        na = float(np.linalg.norm(a))
        nb = float(np.linalg.norm(b))
        cos_sim = float(np.dot(a, b) / (na * nb)) if na > 1e-6 and nb > 1e-6 else 0.0
        return 0.55 * l1_sim + 0.45 * max(0.0, cos_sim)
    except Exception:
        return 0.0


def _score_candidate(data: dict, target_nick: str) -> float:
    score = 0.0
    av_sim = data.get("avatar_match") or 0.0
    if av_sim >= 0.72:
        score += av_sim * 1500.0
    pname = (data.get("profile_name") or "").strip().lower()
    tnick = target_nick.strip().lower()
    if pname == tnick:
        score += 250.0
    elif tnick and tnick in pname:
        score += 80.0
    if data.get("in_cs2"):
        score += 1000.0
    elif data.get("online_state") == "in-game":
        score += 350.0
    elif data.get("online_state") == "online":
        score += 150.0
    if data.get("cs2_hours") is not None:
        score += 120.0
    if data.get("privacy_state") == "public":
        score += 20.0
    return score


def _search_candidates(query: str) -> tuple[int, list[tuple[str, Optional[str], Optional[str], str]]]:
    global _LAST_SEARCH_TS
    session_id = "a1b2c3d4e5f6789012345678"
    q = urllib.parse.quote(query)
    url = (
        f"https://steamcommunity.com/search/SearchCommunityAjax"
        f"?text={q}&filter=users&sessionid={session_id}&steamid_user=false&page=1"
    )

    with _SEARCH_LOCK:
        if _steam_cooldown_remaining() > 0:
            return 429, []  # cooling down after a 429: do not send another request
        wait = STEAM_SEARCH_MIN_INTERVAL_SEC - (time.time() - _LAST_SEARCH_TS)
        if wait > 0:
            time.sleep(wait)
        _LAST_SEARCH_TS = time.time()
    status, raw = _http_get_status(url, timeout=4.5, cookies=f"sessionid={session_id}")
    _steam_record_search_status(status)

    if status != 200 or not raw:
        return status, []
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return status, []
    html_snippet = payload.get("html") or ""
    if not html_snippet:
        return status, []

    blocks = re.split(r'<div class="search_row"', html_snippet)
    candidates = []
    for blk in blocks[1:]:
        row_m = re.search(
            r'<a[^>]+class="searchPersonaName"[^>]+href="(https://steamcommunity\.com/(id|profiles)/([^"/]+))"[^>]*>(.*?)</a>',
            blk,
            re.DOTALL,
        )
        if not row_m:
            continue
        profile_url, kind, ident, raw_name = row_m.groups()
        found_name = html.unescape(re.sub(r"<[^>]+>", "", raw_name)).strip()
        vanity = ident if kind == "id" else None
        steamid64 = ident if kind == "profiles" else None
        if not steamid64:
            mp = re.search(r'data-miniprofile="(\d+)"', blk)
            if mp:
                steamid64 = str(STEAMID64_BASE + int(mp.group(1)))
        candidates.append((profile_url, vanity, steamid64, found_name))
    ql = query.strip().lower()
    candidates.sort(key=lambda c: (0 if c[3].lower() == ql else 1, 0 if ql in c[3].lower() else 1))
    return status, candidates[:5]


def _steam_cooldown_remaining() -> float:
    with _STEAM_RL_LOCK:
        return max(0.0, _STEAM_RL_STATE["until"] - time.monotonic())


def _steam_record_search_status(status: int) -> None:
    with _STEAM_RL_LOCK:
        if status == 429:
            streak = _STEAM_RL_STATE["streak"] + 1
            cooldown = min(STEAM_COOLDOWN_MAX_SEC, STEAM_COOLDOWN_BASE_SEC * (2 ** (int(streak) - 1)))
            _STEAM_RL_STATE["streak"] = streak
            _STEAM_RL_STATE["until"] = max(_STEAM_RL_STATE["until"], time.monotonic() + cooldown)
            _LOG.warning("Steam search: HTTP 429, cooldown %.0fs (streak %d)", cooldown, int(streak))
        elif status == 200:
            _STEAM_RL_STATE["streak"] = 0.0


def get_steam_search_status() -> dict:
    """Current Steam search cooldown state for the UI: {limited: bool, retry_in_sec: int}."""
    remaining = _steam_cooldown_remaining()
    return {"limited": remaining > 0, "retry_in_sec": int(math.ceil(remaining))}


def _fetch_batch_via_web_api(
    candidates: list[tuple[str, Optional[str], Optional[str], str]],
    api_key: str,
    avatar_thumb: object,
) -> Optional[list[dict]]:
    steamids = [c[2] for c in candidates if c[2]]
    if not steamids:
        return None
    ids_csv = ",".join(steamids)
    sum_url = f"https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/?key={api_key}&steamids={ids_csv}"
    ban_url = f"https://api.steampowered.com/ISteamUser/GetPlayerBans/v1/?key={api_key}&steamids={ids_csv}"

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_sum = pool.submit(_http_get, sum_url, 4.0)
        f_ban = pool.submit(_http_get, ban_url, 4.0)
        raw_sum = f_sum.result()
        raw_ban = f_ban.result()

    if not raw_sum:
        return None
    try:
        players_json = json.loads(raw_sum.decode("utf-8", errors="replace")).get("response", {}).get("players", [])
    except json.JSONDecodeError:
        return None
    if not players_json:
        return None

    bans_by_id: dict[str, dict] = {}
    if raw_ban:
        try:
            for b in json.loads(raw_ban.decode("utf-8", errors="replace")).get("players", []):
                if b.get("SteamId"):
                    bans_by_id[b["SteamId"]] = b
        except json.JSONDecodeError:
            pass

    by_id = {p.get("steamid"): p for p in players_json if p.get("steamid")}
    now_ts = time.time()

    def _build(cand):
        profile_url, vanity, sid, found_name = cand
        p = by_id.get(sid) if sid else None
        if not p:
            return None
        b = bans_by_id.get(sid, {})
        vis = p.get("communityvisibilitystate")
        privacy = "public" if vis == 3 else "private"
        pstate = p.get("personastate", 0)
        gameid = str(p.get("gameid") or "")
        in_cs2 = gameid == "730"
        online_state = "in-game" if gameid else ("online" if pstate and pstate > 0 else "offline")
        created = p.get("timecreated")
        age_years = round((now_ts - float(created)) / (365.25 * 86400), 1) if created else None
        av_url = p.get("avatarmedium") or p.get("avatar")
        av_match = round(_avatar_similarity(avatar_thumb, av_url), 3) if avatar_thumb is not None else 0.0
        vac = bool(b.get("VACBanned")) or (b.get("NumberOfGameBans", 0) > 0)
        econ = b.get("EconomyBan")
        return {
            "profile_name": p.get("personaname") or found_name,
            "steamid64": sid,
            "vanity": vanity,
            "profile_url": p.get("profileurl") or profile_url,
            "avatar_url": av_url,
            "avatar_match": av_match,
            "privacy_state": privacy,
            "online_state": online_state,
            "in_cs2": in_cs2,
            "vac_banned": vac,
            "trade_ban": econ if econ and econ != "none" else None,
            "is_limited": False,
            "member_since": None,
            "account_age_years": age_years,
            "cs2_hours": None,
            "lookup_source": "steam_web_api",
        }

    with ThreadPoolExecutor(max_workers=min(5, len(candidates))) as pool:
        built = [r for r in pool.map(_build, candidates) if r is not None]
    return built or None


def _fetch_cs2_hours_web_api(steamid64: str, api_key: str) -> Optional[float]:
    url = (
        f"https://api.steampowered.com/IPlayerService/GetOwnedGames/v0001/"
        f"?key={api_key}&steamid={steamid64}&include_played_free_games=1&appids_filter[0]=730"
    )
    raw = _http_get(url, timeout=3.5)
    if not raw:
        return None
    try:
        games = json.loads(raw.decode("utf-8", errors="replace")).get("response", {}).get("games", [])
        for g in games:
            if g.get("appid") == 730:
                return round(float(g.get("playtime_forever", 0)) / 60.0, 1)
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    return None


def _prefer_unambiguous_name_match(enriched: list[dict], nick: str, log: list[str]) -> None:
    """If no candidate's profile name equals the OCR nick, move the unique fuzzy
    name match (nick_match) to the front. Otherwise keep the score order."""
    if not enriched:
        return
    names = [d.get("profile_name") or "" for d in enriched]
    if any(n.strip().lower() == nick.strip().lower() for n in names):
        return
    idx = best_match_index(nick, names)
    if idx is None or idx == 0:
        return
    chosen = enriched.pop(idx)
    enriched.insert(0, chosen)
    log.append(f"Нечітке співпадіння імені: {names[idx]!r} обрано для OCR-ніка {nick!r}")


def _search_steam_community(nick: str, avatar_thumb: object = None) -> tuple[Optional[dict], list[str], list[dict]]:
    log: list[str] = [f"OCR нік: {nick!r}"]
    variants = [nick]
    for alt in (
        nick.replace("1", "l").replace("0", "o"),
        nick.replace("l", "1").replace("I", "1").replace("O", "0"),
    ):
        if alt not in variants:
            variants.append(alt)

    candidates = []
    rate_limited = False
    for q in variants:
        status, found = _search_candidates(q)
        if status == 429:
            rate_limited = True
            log.append(f"Пошук Steam '{q}': HTTP 429 — спрацював rate limit Steam (потрібен повторний аналіз)")
            break  # no point trying alternative spellings while Steam is limiting us
        elif status != 200:
            log.append(f"Пошук Steam '{q}': помилка HTTP {status} (не вдалося звернутися до Steam)")
        else:
            log.append(f"Пошук Steam '{q}': знайдено {len(found)} кандидатів")
        if found:
            candidates = found
            break
    if not candidates:
        if rate_limited:
            log.append("Результат: кандидатів не отримано через rate limit Steam (HTTP 429)")
        return ({"_rate_limited": True} if rate_limited else None), log, []

    api_key = _get_steam_api_key()
    enriched = None
    if api_key:
        enriched = _fetch_batch_via_web_api(candidates, api_key, avatar_thumb)
        if enriched:
            log.append(f"Steam Web API: пакетно перевірено {len(enriched)} кандидатів")

    if not enriched:
        def _fetch_one(cand):
            profile_url, vanity, steamid64, found_name = cand
            xml_bytes = _http_get(f"{profile_url}/?xml=1", timeout=3.5)
            if xml_bytes:
                parsed = _parse_steam_xml(xml_bytes, vanity=vanity, source="search+xml")
                if parsed:
                    if not parsed.get("profile_name") and found_name:
                        parsed["profile_name"] = found_name
                    parsed["avatar_match"] = round(_avatar_similarity(avatar_thumb, parsed.get("avatar_url")), 3)
                    return parsed
            return {
                "profile_name": found_name or nick,
                "steamid64": steamid64,
                "vanity": vanity,
                "profile_url": profile_url,
                "avatar_url": None,
                "avatar_match": 0.0,
                "privacy_state": None,
                "online_state": None,
                "in_cs2": False,
                "vac_banned": False,
                "trade_ban": None,
                "is_limited": False,
                "member_since": None,
                "account_age_years": None,
                "cs2_hours": None,
                "lookup_source": "search",
            }

        with ThreadPoolExecutor(max_workers=len(candidates)) as pool:
            enriched = list(pool.map(_fetch_one, candidates))

    for d in enriched:
        d["match_score"] = round(_score_candidate(d, nick), 1)
    enriched.sort(key=lambda d: d["match_score"], reverse=True)
    _prefer_unambiguous_name_match(enriched, nick, log)

    if api_key and enriched[0].get("steamid64") and enriched[0].get("cs2_hours") is None:
        hrs = _fetch_cs2_hours_web_api(enriched[0]["steamid64"], api_key)
        if hrs is not None:
            enriched[0]["cs2_hours"] = hrs

    for idx, d in enumerate(enriched, 1):
        status = "In CS2" if d.get("in_cs2") else (d.get("online_state") or "?")
        av = d.get("avatar_match") or 0.0
        log.append(
            f"  #{idx} {d.get('profile_name')!r} [{status}, av={av:.2f}, score={d['match_score']}]"
        )

    cand_summaries = [
        {
            "profile_name": d.get("profile_name"),
            "profile_url": d.get("profile_url"),
            "steamid64": d.get("steamid64"),
            "avatar_url": d.get("avatar_url"),
            "online_state": d.get("online_state"),
            "in_cs2": bool(d.get("in_cs2")),
            "avatar_match": d.get("avatar_match") or 0.0,
            "cs2_hours": d.get("cs2_hours"),
            "score": d["match_score"],
        }
        for d in enriched
    ]
    return enriched[0], log, cand_summaries


def _cache_entry_current(data: dict) -> bool:
    """Refetch when Leetify data is missing or predates the full fetch.

    An entry with a SteamID64 but no Leetify block was cached while Leetify had no
    profile (or before the key was in use). Refetching is cheap, so do it.
    """
    if (
        data.get("steamid64")
        and not data.get("cs2_stats")
        and _get_steam_api_key()
        and not (data.get("extra_stats") or {}).get("_cs2_checked")
    ):
        return False  # Steam CS2 stats were never requested (cached before a key was configured)
    ct = (data.get("extra_stats") or {}).get("cstracker")
    if data.get("steamid64") and (not isinstance(ct, dict) or ct.get("status") == "error"):
        return False  # cstracker data missing or failed; 'blocked'/'not_found' stay cached
    # Profile cache entries never hold Leetify data; it is re-read via _leetify_cached_or_fetch.
    return True


def lookup_steam_profile(nick: str, avatar_thumb: object = None, screenshot_avatar: Optional[str] = None) -> SteamProfile:
    _setup_pton_log()
    clean_nick = nick.strip()
    cache_key = clean_nick.lower()
    now = time.time()
    lf_ttl = _leetify_cache_ttl_sec()

    def _from_cache_entry(entry: dict) -> SteamProfile:
        entry = dict(entry)
        if screenshot_avatar:
            entry["screenshot_avatar"] = screenshot_avatar
        entry["leetify"] = _leetify_cached_or_fetch(entry.get("steamid64"), lf_ttl)
        cached_profile = SteamProfile(**entry)
        _finalize_source_views(cached_profile)
        return cached_profile

    hit: Optional[dict] = None
    stale: Optional[dict] = None  # within TTL but due a refresh; used if the refresh is rate limited
    with PROFILE_CACHE.lock:
        cached = PROFILE_CACHE.get(cache_key)
        if cached:
            ttl = CACHE_TTL_SEC if cached[1].get("lookup_source") != "not_found" else 30
            if (now - cached[0]) < ttl:
                if _cache_entry_current(cached[1]):
                    hit = dict(cached[1])
                elif cached[1].get("steamid64"):
                    stale = dict(cached[1])
    if hit is not None:
        return _from_cache_entry(hit)

    data: Optional[dict] = None
    search_log: list[str] = []
    candidates: list[dict] = []
    if clean_nick:
        data, search_log, candidates = _search_steam_community(clean_nick, avatar_thumb=avatar_thumb)

    if stale is not None and data is not None and data.get("_rate_limited"):
        _LOG.info("steam lookup %r: refresh rate limited, serving cached profile", clean_nick)
        return _from_cache_entry(stale)

    nick_encoded = urllib.parse.quote(clean_nick, safe="")
    rip_nick_url = f"https://steamcommunity.rip/id/{nick_encoded}/"

    if data is None or data.get("_rate_limited"):
        is_rl = bool(data and data.get("_rate_limited"))
        if not is_rl:
            search_log.append("Результат: профіль у Steam не знайдено")
        profile = SteamProfile(
            query_nick=clean_nick,
            screenshot_avatar=screenshot_avatar,
            lookup_source="rate_limited" if is_rl else "not_found",
            rip_url=rip_nick_url,
            rip_nick_url=rip_nick_url,
            extra_stats={},
            search_log=search_log,
            candidates=candidates,
        )
    else:
        steamid64 = data.get("steamid64")
        leetify_data, faceit_data, cs2_stats = _fetch_external_stats(steamid64)
        if leetify_data:
            search_log.append(f"Leetify: знайдено ({leetify_data.get('total_matches') or 0} матчів)")
        elif steamid64:
            search_log.append("Leetify: профіль відсутній або закритий")
        if faceit_data:
            search_log.append(
                f"FACEIT: lvl {faceit_data.get('skill_level') or '?'} ({faceit_data.get('faceit_elo') or '?'} ELO)"
            )
        if cs2_stats:
            search_log.append(
                f"Steam CS2 stats: K/D {cs2_stats.get('kd')}, HS {cs2_stats.get('hs_pct')}%"
            )
        rip_target = data.get("vanity") or steamid64 or nick_encoded
        profile = SteamProfile(
            query_nick=clean_nick,
            profile_name=data.get("profile_name"),
            steamid64=steamid64,
            vanity=data.get("vanity"),
            profile_url=data.get("profile_url"),
            avatar_url=data.get("avatar_url"),
            screenshot_avatar=screenshot_avatar,
            privacy_state=data.get("privacy_state"),
            online_state=data.get("online_state"),
            in_cs2=bool(data.get("in_cs2")),
            vac_banned=bool(data.get("vac_banned")),
            trade_ban=data.get("trade_ban"),
            is_limited=bool(data.get("is_limited")),
            member_since=data.get("member_since"),
            account_age_years=data.get("account_age_years"),
            cs2_hours=data.get("cs2_hours"),
            lookup_source=data.get("lookup_source", "steam"),
            rip_url=f"https://steamcommunity.rip/id/{urllib.parse.quote(str(rip_target), safe='')}/",
            rip_nick_url=rip_nick_url,
            leetify=leetify_data,
            faceit=faceit_data,
            cs2_stats=cs2_stats,
            extra_stats=_cstracker_extra(steamid64),
            search_log=search_log,
            candidates=candidates,
        )

    _finalize_source_views(profile)
    if profile.lookup_source != "rate_limited":
        _leetify_mem_put(profile.steamid64, profile.leetify, lf_ttl)
        with PROFILE_CACHE.lock:
            PROFILE_CACHE.put_locked(cache_key, now, asdict(profile))
            if profile.lookup_source != "not_found":
                PROFILE_CACHE.save_locked()

    return profile


def _risk_level(prob: float) -> str:
    return risk_level(prob)


def _load_base_weights() -> dict[str, float]:
    defaults = dict(DEFAULT_AI_WEIGHTS)  # single source of truth: config_store / config_schema
    try:
        from storage.config_store import load_config

        w = load_config().get("ai_weights")
        if isinstance(w, dict):
            return {
                "scoreboard": max(0.0, float(w.get("scoreboard", defaults["scoreboard"]))),
                "leetify": max(0.0, float(w.get("leetify", defaults["leetify"]))),
                "steam_account": max(0.0, float(w.get("steam_account", defaults["steam_account"]))),
            }
    except Exception:
        pass
    return defaults


def _compute_uncertainty_and_weights(steam: SteamProfile) -> tuple[float, dict[str, float]]:
    return compute_uncertainty_and_weights(steam, _load_base_weights())


# Thresholds and rules live in scoring/ (thresholds.py, rules.py, engine.py).
from scoring import thresholds as _T  # noqa: E402
from scoring.rules import _pick_recent_metric  # noqa: E402,F401

AIM_ELITE_RECENT_POINTS = _T.AIM_ELITE_RECENT_POINTS
AIM_ELITE_SMURF_BONUS = _T.AIM_ELITE_SMURF_BONUS
_recent_aim_signals = recent_aim_signals


def _compute_scores(
    player: Player, steam: SteamProfile
) -> tuple[float, str, float, str, float, dict[str, float], list[str]]:
    return compute_scores(player, steam, _load_base_weights()).as_tuple()


def _build_player_report(p: Player, steam: SteamProfile) -> PlayerReport:
    # Only the sources view here; coverage is set by the lookup/recheck paths that build the profile.
    _refresh_sources_view(steam)
    scored = compute_scores(p, steam, _load_base_weights())
    prob, risk, smurf_prob, smurf_risk, uncertainty, eff_weights, reasons = scored.as_tuple()
    anomaly = None if math.isnan(p.score) else round(p.score, 1)
    return PlayerReport(
        nick=p.nick,
        kills=p.kills,
        deaths=p.deaths,
        kd=round(p.kd, 2),
        hs=round(p.hs, 1),
        level=p.level,
        anomaly_score=anomaly,
        cheat_probability=prob,
        risk_level=risk,
        reasons=reasons,
        steam=asdict(steam),
        smurf_probability=smurf_prob,
        smurf_risk_level=smurf_risk,
        uncertainty=uncertainty,
        effective_weights=eff_weights,
        score_breakdown=scored.breakdown,
        player_uid=p.player_uid,
    )


def _retry_rate_limited(
    players: list[Player],
    steam_by_idx: dict[int, Optional[SteamProfile]],
    lookup,
    max_workers: int,
) -> None:
    """Retry lookups that ended as 'rate_limited', waiting out the Steam cooldown between rounds.

    Bounded by STEAM_RETRY_ROUNDS and STEAM_RETRY_BUDGET_SEC total sleep, so a scan never
    stalls for long. Players still limited after the budget keep lookup_source='rate_limited'
    (they are not cached, so the next scan retries them).
    """
    pending = [i for i, st in steam_by_idx.items() if st is not None and st.lookup_source == "rate_limited"]
    budget = STEAM_RETRY_BUDGET_SEC
    for _ in range(STEAM_RETRY_ROUNDS):
        if not pending or budget <= 0:
            break
        wait = min(max(_steam_cooldown_remaining(), 0.5), budget)
        time.sleep(wait)
        budget -= wait
        with ThreadPoolExecutor(max_workers=min(max_workers, len(pending))) as pool:
            results = list(pool.map(lookup, pending))
        for idx, st in zip(pending, results):
            steam_by_idx[idx] = st
        pending = [i for i, st in zip(pending, results) if st is not None and st.lookup_source == "rate_limited"]


def enrich_players(
    players: list[Player],
    fetch_steam: bool = True,
    on_player_ready=None,
) -> list[PlayerReport]:
    _setup_pton_log()
    if not players:
        return []

    if not fetch_steam:
        reports = [
            _build_player_report(
                p,
                SteamProfile(
                    query_nick=p.nick,
                    screenshot_avatar=p.avatar_data_url,
                    rip_url=f"https://steamcommunity.rip/id/{urllib.parse.quote(p.nick, safe='')}/",
                    rip_nick_url=f"https://steamcommunity.rip/id/{urllib.parse.quote(p.nick, safe='')}/",
                ),
            )
            for p in players
        ]
        reports.sort(key=lambda r: r.cheat_probability, reverse=True)
        return reports

    reports_by_idx: dict[int, PlayerReport] = {}
    max_workers = min(6, len(players))

    def _emit(idx: int, steam: SteamProfile) -> None:
        rep = _build_player_report(players[idx], steam)
        reports_by_idx[idx] = rep
        if on_player_ready is not None:
            try:
                on_player_ready(rep)
            except Exception:
                pass

    def _lookup(idx: int) -> Optional[SteamProfile]:
        p = players[idx]
        try:
            return lookup_steam_profile(p.nick, p.avatar_thumb, p.avatar_data_url)
        except Exception:
            return None

    # Rate-limited players are held back and reported once, after their retries,
    # so on_player_ready (scan progress) is called exactly once per player.
    steam_by_idx: dict[int, Optional[SteamProfile]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {pool.submit(_lookup, idx): idx for idx in range(len(players))}
        for fut in as_completed(future_map):
            steam_by_idx[future_map[fut]] = fut.result()
    _retry_rate_limited(players, steam_by_idx, _lookup, max_workers)

    for idx, p in enumerate(players):
        steam = steam_by_idx.get(idx)
        if steam is None:
            steam = SteamProfile(
                query_nick=p.nick,
                screenshot_avatar=p.avatar_data_url,
                rip_url=f"https://steamcommunity.rip/id/{urllib.parse.quote(p.nick, safe='')}/",
                rip_nick_url=f"https://steamcommunity.rip/id/{urllib.parse.quote(p.nick, safe='')}/",
            )
        _emit(idx, steam)

    reports = [reports_by_idx[i] for i in range(len(players))]
    reports.sort(key=lambda r: r.cheat_probability, reverse=True)
    return reports


def recheck_player_report(player_dict: dict, query: str) -> dict:
    """Re-resolve a single player by corrected nickname, SteamID64, or Steam profile URL."""
    _setup_pton_log()
    q = query.strip()
    data: Optional[dict] = None

    url_match = re.search(r"steamcommunity\.com/(id|profiles)/([^/?#]+)", q)
    if url_match:
        kind, ident = url_match.groups()
        xml_bytes = _http_get(f"https://steamcommunity.com/{kind}/{urllib.parse.quote(ident)}/?xml=1", timeout=4.0)
        if xml_bytes:
            data = _parse_steam_xml(xml_bytes, vanity=ident if kind == "id" else None, source="manual_url")
    elif re.fullmatch(r"7656119\d{10}", q):
        xml_bytes = _http_get(f"https://steamcommunity.com/profiles/{q}/?xml=1", timeout=4.0)
        if xml_bytes:
            data = _parse_steam_xml(xml_bytes, vanity=None, source="manual_id64")

    if data is not None:
        steamid64 = data.get("steamid64")
        leetify_data, faceit_data, cs2_stats = _fetch_external_stats(steamid64)
        rip_target = data.get("vanity") or steamid64 or urllib.parse.quote(q, safe="")
        steam = SteamProfile(
            query_nick=q,
            profile_name=data.get("profile_name"),
            steamid64=steamid64,
            vanity=data.get("vanity"),
            profile_url=data.get("profile_url"),
            avatar_url=data.get("avatar_url"),
            privacy_state=data.get("privacy_state"),
            online_state=data.get("online_state"),
            in_cs2=bool(data.get("in_cs2")),
            vac_banned=bool(data.get("vac_banned")),
            trade_ban=data.get("trade_ban"),
            is_limited=bool(data.get("is_limited")),
            member_since=data.get("member_since"),
            account_age_years=data.get("account_age_years"),
            cs2_hours=data.get("cs2_hours"),
            lookup_source=data.get("lookup_source", "manual"),
            rip_url=f"https://steamcommunity.rip/id/{urllib.parse.quote(str(rip_target), safe='')}/",
            rip_nick_url=f"https://steamcommunity.rip/id/{urllib.parse.quote(q, safe='')}/",
            leetify=leetify_data,
            faceit=faceit_data,
            cs2_stats=cs2_stats,
            extra_stats=_cstracker_extra(steamid64),
        )
    else:
        steam = lookup_steam_profile(q)
    _finalize_source_views(steam)

    anomaly = player_dict.get("anomaly_score")
    p = Player(
        nick=q if not (url_match or re.fullmatch(r"7656119\d{10}", q)) else player_dict["nick"],
        kills=int(player_dict["kills"]),
        deaths=int(player_dict["deaths"]),
        kd=float(player_dict["kd"]),
        hs=float(player_dict["hs"]),
        level=int(player_dict.get("level", -1)),
        score=float(anomaly) if anomaly is not None else float("nan"),
    )
    scored = compute_scores(p, steam, _load_base_weights())
    prob, risk, smurf_prob, smurf_risk, uncertainty, eff_weights, reasons = scored.as_tuple()
    return asdict(
        PlayerReport(
            nick=p.nick,
            kills=p.kills,
            deaths=p.deaths,
            kd=round(p.kd, 2),
            hs=round(p.hs, 1),
            level=p.level,
            anomaly_score=anomaly,
            cheat_probability=prob,
            risk_level=risk,
            reasons=reasons,
            steam=asdict(steam),
            smurf_probability=smurf_prob,
            smurf_risk_level=smurf_risk,
            uncertainty=uncertainty,
            effective_weights=eff_weights,
            score_breakdown=scored.breakdown,
            player_uid=player_dict.get("player_uid") or "",
        )
    )
