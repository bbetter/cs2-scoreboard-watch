"""Steam Community search and profile matching: candidate search, 429 cooldown and rate limit, Web API batch, scoring.

Kept apart from lookup/steam_lookup.py (orchestration) so the search path has no dependency on the scanner module.
"""

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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from lookup.http_client import http_get as _http_get
from lookup.http_client import http_get_status as _http_get_status
from lookup.nick_match import best_match_index
from storage.api_keys import _get_steam_api_key

_LOG = logging.getLogger("pton")

# Steam's grey "?" avatar. Its hash is in the URL of every account that never set a picture; two such accounts
# look alike, so this avatar says nothing about identity (verified: the URL serves that image).
DEFAULT_AVATAR_HASH = "fef49e7fa7e1997310d705b2a6158ff8dc1cdfeb"
AVATAR_MATCH_STRONG = 0.72
MAX_CANDIDATES = 8
# A chosen candidate that is neither an exact name nor a strong avatar match needs this lead over #2, or it is
# marked as a low-confidence pick (shown in the attention tab).
SEARCH_LOW_MARGIN = 150.0

# Scoring points. The in-game bonuses apply only to a live capture: an opponent playing right now says nothing
# about who was on the scoreboard in an old screenshot.
SCORE_AVATAR_MAX = 1500.0
SCORE_EXACT_NAME = 500.0
SCORE_NAME_CONTAINS = 80.0
SCORE_IN_CS2_LIVE = 300.0
SCORE_IN_GAME_LIVE = 150.0
SCORE_ONLINE_LIVE = 75.0
SCORE_CS2_HOURS = 120.0
SCORE_PUBLIC_PROFILE = 20.0


STEAMID64_BASE = 76561197960265728
STEAM_SEARCH_MIN_INTERVAL_SEC = 0.55
STEAM_COOLDOWN_BASE_SEC = 5.0
STEAM_COOLDOWN_MAX_SEC = 120.0


class SteamRateLimiter:
    """Steam Community search queue state: minimum spacing between request starts and a 429 cooldown.

    One injected clock drives both the spacing and the cooldown. In-memory only, thread-safe.
    """

    def __init__(
        self,
        min_interval_sec: float,
        cooldown_base_sec: float,
        cooldown_max_sec: float,
        *,
        clock=time.monotonic,
        sleep=time.sleep,
    ) -> None:
        self.min_interval_sec = min_interval_sec
        self.cooldown_base_sec = cooldown_base_sec
        self.cooldown_max_sec = cooldown_max_sec
        self._clock = clock
        self._sleep = sleep
        # _seq_lock serializes request starts and is held while waiting for the spacing;
        # _state_lock guards the cooldown fields and is never held while sleeping.
        self._seq_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._last_start: float | None = None
        self._until = 0.0
        self._streak = 0

    @property
    def streak(self) -> int:
        with self._state_lock:
            return self._streak

    def wait_turn(self) -> bool:
        """Block until the next search may start. False means cooling down: send no request."""
        with self._seq_lock:
            if self.cooldown_remaining() > 0:
                return False
            if self._last_start is not None:
                wait = self.min_interval_sec - (self._clock() - self._last_start)
                if wait > 0:
                    self._sleep(wait)
            self._last_start = self._clock()
            return True

    def cooldown_remaining(self) -> float:
        with self._state_lock:
            return max(0.0, self._until - self._clock())

    def record(self, status: int) -> None:
        with self._state_lock:
            if status == 429:
                self._streak += 1
                cooldown = min(self.cooldown_max_sec, self.cooldown_base_sec * (2 ** (self._streak - 1)))
                self._until = max(self._until, self._clock() + cooldown)
                _LOG.warning("Steam search: HTTP 429, cooldown %.0fs (streak %d)", cooldown, self._streak)
            elif status == 200:
                self._streak = 0

    def status(self) -> dict:
        """Cooldown state for the UI: {limited: bool, retry_in_sec: int}."""
        remaining = self.cooldown_remaining()
        return {"limited": remaining > 0, "retry_in_sec": int(math.ceil(remaining))}

    def reset(self) -> None:
        with self._seq_lock, self._state_lock:
            self._last_start = None
            self._until = 0.0
            self._streak = 0


RATE_LIMITER = SteamRateLimiter(STEAM_SEARCH_MIN_INTERVAL_SEC, STEAM_COOLDOWN_BASE_SEC, STEAM_COOLDOWN_MAX_SEC)


def _parse_member_age_years(member_since: str | None) -> float | None:
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
def _parse_steam_xml(xml_bytes: bytes, vanity: str | None = None, source: str = "xml") -> dict | None:
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
def _is_default_avatar(avatar_url: str | None) -> bool:
    return bool(avatar_url) and DEFAULT_AVATAR_HASH in avatar_url


def _avatar_similarity(thumb: object, avatar_url: str | None) -> float:
    if thumb is None or not avatar_url or _is_default_avatar(avatar_url):
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
def _score_candidate(data: dict, target_nick: str, live: bool = True) -> float:
    score = 0.0
    av_sim = data.get("avatar_match") or 0.0
    if av_sim >= AVATAR_MATCH_STRONG:
        score += av_sim * SCORE_AVATAR_MAX
    pname = (data.get("profile_name") or "").strip().lower()
    tnick = target_nick.strip().lower()
    if pname == tnick:
        score += SCORE_EXACT_NAME
    elif tnick and tnick in pname:
        score += SCORE_NAME_CONTAINS
    if live:
        if data.get("in_cs2"):
            score += SCORE_IN_CS2_LIVE
        elif data.get("online_state") == "in-game":
            score += SCORE_IN_GAME_LIVE
        elif data.get("online_state") == "online":
            score += SCORE_ONLINE_LIVE
    if data.get("cs2_hours") is not None:
        score += SCORE_CS2_HOURS
    if data.get("privacy_state") == "public":
        score += SCORE_PUBLIC_PROFILE
    return score


def _nick_variants(nick: str) -> list[str]:
    """The OCR nick plus the 1/l and 0/o (and I/1, O/0) readings the OCR may have confused, without repeats."""
    variants = [nick]
    for alt in (
        nick.replace("1", "l").replace("0", "o"),
        nick.replace("l", "1").replace("I", "1").replace("O", "0"),
    ):
        if alt not in variants:
            variants.append(alt)
    return variants
def _search_candidates(query: str) -> tuple[int, list[tuple[str, str | None, str | None, str]]]:
    session_id = "a1b2c3d4e5f6789012345678"
    q = urllib.parse.quote(query)
    url = (
        f"https://steamcommunity.com/search/SearchCommunityAjax"
        f"?text={q}&filter=users&sessionid={session_id}&steamid_user=false&page=1"
    )

    if not RATE_LIMITER.wait_turn():
        return 429, []  # cooling down after a 429: do not send another request
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
    return RATE_LIMITER.cooldown_remaining()
def _steam_record_search_status(status: int) -> None:
    RATE_LIMITER.record(status)
def get_steam_search_status() -> dict:
    """Current Steam search cooldown state for the UI: {limited: bool, retry_in_sec: int}."""
    return RATE_LIMITER.status()
def _fetch_batch_via_web_api(
    candidates: list[tuple[str, str | None, str | None, str]],
    api_key: str,
    avatar_thumb: object,
) -> list[dict] | None:
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
def _fetch_cs2_hours_web_api(steamid64: str, api_key: str) -> float | None:
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
def _search_steam_community(
    nick: str, avatar_thumb: object = None, live: bool = True
) -> tuple[dict | None, list[str], list[dict]]:
    log: list[str] = [f"OCR нік: {nick!r}"]
    target = nick.strip().lower()

    candidates: list[tuple] = []
    seen_urls: set[str] = set()
    rate_limited = False
    for q in _nick_variants(nick):
        status, found = _search_candidates(q)
        if status == 429:
            rate_limited = True
            log.append(f"Пошук Steam '{q}': HTTP 429 — спрацював rate limit Steam (потрібен повторний аналіз)")
            break  # no point trying alternative spellings while Steam is limiting us
        elif status != 200:
            log.append(f"Пошук Steam '{q}': помилка HTTP {status} (не вдалося звернутися до Steam)")
        else:
            log.append(f"Пошук Steam '{q}': знайдено {len(found)} кандидатів")
        for cand in found:
            if cand[0] not in seen_urls:  # merge by profile URL, keep the first query's order
                seen_urls.add(cand[0])
                candidates.append(cand)
        # Alternative spellings only add noise once an exact name is already in hand.
        if any(c[3].strip().lower() == target for c in candidates):
            break
    candidates = candidates[:MAX_CANDIDATES]
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
        d["match_score"] = round(_score_candidate(d, nick, live=live), 1)
    enriched.sort(key=lambda d: d["match_score"], reverse=True)
    _prefer_unambiguous_name_match(enriched, nick, log)

    chosen = enriched[0]
    margin = chosen["match_score"] - enriched[1]["match_score"] if len(enriched) > 1 else None
    exact = (chosen.get("profile_name") or "").strip().lower() == target
    strong_avatar = (chosen.get("avatar_match") or 0.0) >= AVATAR_MATCH_STRONG
    low = not exact and not strong_avatar and (margin is None or margin < SEARCH_LOW_MARGIN)
    chosen["search_confidence"] = {
        "level": "low" if low else "high",
        "margin": None if margin is None else round(margin, 1),
        "exact_name": exact,
    }

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
