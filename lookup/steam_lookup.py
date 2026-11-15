"""Resolve CS2 player nicknames to Steam + Leetify profiles and compute risk scores."""

from __future__ import annotations

import logging
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

import sources.cstracker as cstracker_source
import sources.faceit as faceit_source
import sources.leetify as leetify_source
import sources.steam_stats as steam_stats_source
from lookup import steam_search
from lookup.http_client import http_get as _http_get
from lookup.ttl_memo import TtlMemo
from scoring import (
    compute_scores,
    compute_uncertainty_and_weights,
    recent_aim_signals,
    risk_level,
)
from scoring import thresholds as _T
from sources import REGISTRY
from sources.base import DataSource, LegacySlotSource, SourceContext, SourceResult
from storage.api_keys import _get_steam_api_key
from storage.config_store import DEFAULT_AI_WEIGHTS
from storage.paths import DATA_DIR, LOG_DIR
from storage.profile_cache import ProfileCache
from vision.scoreboard_scan import Player

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


CACHE_TTL_SEC = 86400
PROFILE_CACHE_FILE = DATA_DIR / "profile_cache.json"
# Leetify data lives only in memory, keyed by SteamID64, for leetify_cache_ttl_sec (0 = not reused).
LEETIFY_MEMO = TtlMemo()
# Steam Community search queue: minimum spacing between search request starts,
# cooldown after HTTP 429 (doubles per consecutive 429, reset on 200), and a bounded
# retry of rate-limited lookups inside enrich_players. In-memory only.
STEAM_RETRY_ROUNDS = 3
STEAM_RETRY_BUDGET_SEC = 30.0


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


def _leetify_mem_get(steamid64: str | None, ttl: int) -> dict | None:
    return LEETIFY_MEMO.get(steamid64, ttl)


def _leetify_mem_put(steamid64: str | None, data: dict | None, ttl: int) -> None:
    LEETIFY_MEMO.put(steamid64, data, ttl)


def _leetify_cached_or_fetch(steamid64: str | None, ttl: int) -> dict | None:
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
    profile_name: str | None = None
    steamid64: str | None = None
    vanity: str | None = None
    profile_url: str | None = None
    avatar_url: str | None = None
    screenshot_avatar: str | None = None
    privacy_state: str | None = None
    online_state: str | None = None
    in_cs2: bool = False
    vac_banned: bool = False
    trade_ban: str | None = None
    is_limited: bool = False
    member_since: str | None = None
    account_age_years: float | None = None
    cs2_hours: float | None = None
    lookup_source: str = "none"
    rip_url: str = ""
    rip_nick_url: str = ""
    leetify: dict | None = None
    faceit: dict = field(default_factory=dict)
    cs2_stats: dict = field(default_factory=dict)
    extra_stats: dict = field(default_factory=dict)
    search_log: list[str] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)
    # How sure the automatic nick search is about the chosen profile: {"level": "high"|"low", "margin", "exact_name"}.
    search_confidence: dict = field(default_factory=dict)
    # Per-source flags: which sources returned data for this player (steam, leetify, faceit, cstracker).
    coverage: dict = field(default_factory=dict)
    # Derived uniform view {source_id: {status, data, fetched_at}} (see _refresh_sources_view).
    sources: dict = field(default_factory=dict)


def _source_coverage(profile: SteamProfile) -> dict:
    # Per-source flags come from the registry (sources/); "steam" is the SteamID64 presence flag.
    return {"steam": bool(profile.steamid64), **REGISTRY.coverage(asdict(profile))}


def _refresh_sources_view(profile: SteamProfile) -> None:
    """Set profile.sources to the registry's uniform view of every source that has data."""
    profile.sources = REGISTRY.sources_view(asdict(profile))


def _finalize_source_views(profile: SteamProfile) -> None:
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
    anomaly_score: float | None
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


def _cstracker_extra(steamid64: str | None) -> dict:
    """extra_stats for a fresh profile: cstracker data plus a marker that CS2 stats were requested.

    A public profile can still hide game stats, leaving cs2_stats empty for good; the marker
    stops _cache_entry_current from treating such entries as stale on every lookup.
    """
    if not steamid64:
        return {}
    return {"cstracker": cstracker_source.fetch_cstracker(steamid64), "_cs2_checked": bool(_get_steam_api_key())}


def _fetch_external_stats(steamid64: str | None) -> tuple[dict | None, dict, dict]:
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


def _extra_sources() -> list[DataSource]:
    """Registry sources without a legacy slot: third-party plugins and any new built-in source."""
    return [s for s in REGISTRY if not isinstance(s, LegacySlotSource)]


def _fetch_registry_sources(steamid64: str | None) -> dict:
    """Fetch every extra registry source for one SteamID64; returns the stored `sources` block {id: result-dict}.

    Without this, a source that is not one of the legacy four never runs during a scan and only shows up after a
    manual recheck. A source that raises is recorded as status "error" and does not affect the others.
    """
    extra = _extra_sources()
    if not steamid64 or not extra:
        return {}
    ctx = SourceContext(steam_api_key=_get_steam_api_key())
    with ThreadPoolExecutor(max_workers=min(4, len(extra))) as pool:
        futures = {src.id: pool.submit(src.fetch, steamid64, ctx) for src in extra}
        stored: dict = {}
        for src in extra:
            try:
                result = futures[src.id].result()
            except Exception:
                _LOG.exception("source %s failed during scan", src.id)
                result = SourceResult("error", {}, datetime.now(UTC).isoformat(timespec="seconds"))
            container: dict = {}
            try:
                src.write_raw(container, result)
            except Exception:
                _LOG.exception("source %s result could not be stored", src.id)
                continue
            stored.update(container.get("sources") or {})
    return stored


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
    stored_sources = data.get("sources") or {}
    if data.get("steamid64") and any(src.id not in stored_sources for src in _extra_sources()):
        return False  # cached before a registry source (e.g. a plugin) was installed: fetch it once
    faceit = data.get("faceit")
    if isinstance(faceit, dict) and faceit.get("found") and "active_bans" not in faceit:
        return False  # cached before FACEIT bans were read: fetch once so the ban rule sees them
    # Profile cache entries never hold Leetify data; it is re-read via _leetify_cached_or_fetch.
    return True


def lookup_steam_profile(
    nick: str, avatar_thumb: object = None, screenshot_avatar: str | None = None, live: bool = True
) -> SteamProfile:
    """Resolve one nick. `live` is True only for a capture taken now; in-game signals are not used otherwise."""
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

    hit: dict | None = None
    stale: dict | None = None  # within TTL but due a refresh; used if the refresh is rate limited
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

    data: dict | None = None
    search_log: list[str] = []
    candidates: list[dict] = []
    if clean_nick:
        data, search_log, candidates = steam_search._search_steam_community(
            clean_nick, avatar_thumb=avatar_thumb, live=live
        )

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
        extra_sources = _fetch_registry_sources(steamid64)
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
            sources=extra_sources,
            search_log=search_log,
            candidates=candidates,
            search_confidence=data.get("search_confidence") or {},
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
    steam_by_idx: dict[int, SteamProfile | None],
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
        wait = min(max(steam_search._steam_cooldown_remaining(), 0.5), budget)
        time.sleep(wait)
        budget -= wait
        with ThreadPoolExecutor(max_workers=min(max_workers, len(pending))) as pool:
            results = list(pool.map(lookup, pending))
        for idx, st in zip(pending, results, strict=False):
            steam_by_idx[idx] = st
        pending = [i for i, st in zip(pending, results, strict=False) if st is not None and st.lookup_source == "rate_limited"]


def enrich_players(
    players: list[Player],
    fetch_steam: bool = True,
    on_player_ready=None,
    live: bool = True,
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

    def _lookup(idx: int) -> SteamProfile | None:
        p = players[idx]
        try:
            return lookup_steam_profile(p.nick, p.avatar_thumb, p.avatar_data_url, live=live)
        except Exception:
            return None

    # Rate-limited players are held back and reported once, after their retries,
    # so on_player_ready (scan progress) is called exactly once per player.
    steam_by_idx: dict[int, SteamProfile | None] = {}
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
    data: dict | None = None

    url_match = re.search(r"steamcommunity\.com/(id|profiles)/([^/?#]+)", q)
    if url_match:
        kind, ident = url_match.groups()
        xml_bytes = _http_get(f"https://steamcommunity.com/{kind}/{urllib.parse.quote(ident)}/?xml=1", timeout=4.0)
        if xml_bytes:
            data = steam_search._parse_steam_xml(xml_bytes, vanity=ident if kind == "id" else None, source="manual_url")
    elif re.fullmatch(r"7656119\d{10}", q):
        xml_bytes = _http_get(f"https://steamcommunity.com/profiles/{q}/?xml=1", timeout=4.0)
        if xml_bytes:
            data = steam_search._parse_steam_xml(xml_bytes, vanity=None, source="manual_id64")

    if data is not None:
        steamid64 = data.get("steamid64")
        leetify_data, faceit_data, cs2_stats = _fetch_external_stats(steamid64)
        extra_sources = _fetch_registry_sources(steamid64)
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
            sources=extra_sources,
        )
    else:
        steam = lookup_steam_profile(q, live=False)  # a manual re-check is about a row that already exists
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
