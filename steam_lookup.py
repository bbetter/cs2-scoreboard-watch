"""Resolve CS2 player nicknames to Steam + Leetify profiles and compute risk scores."""

from __future__ import annotations

import html
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from scoreboard_scan import Player

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
STEAMID64_BASE = 76561197960265728
CACHE_TTL_SEC = 86400
PROFILE_CACHE_FILE = Path(__file__).resolve().parent / "profile_cache.json"
_CACHE_LOCK = threading.Lock()
_PROFILE_CACHE: dict[str, tuple[float, dict]] = {}
_SEARCH_LOCK = threading.Lock()
_LAST_SEARCH_TS = 0.0
_STEAM_API_KEY_CACHE: Optional[str] = None


def _load_profile_cache() -> None:
    if not PROFILE_CACHE_FILE.is_file():
        return
    try:
        raw = json.loads(PROFILE_CACHE_FILE.read_text(encoding="utf-8"))
        now = time.time()
        if isinstance(raw, dict):
            for k, item in raw.items():
                if isinstance(item, list) and len(item) == 2 and (now - float(item[0])) < CACHE_TTL_SEC:
                    _PROFILE_CACHE[k] = (float(item[0]), item[1])
    except Exception:
        pass


def _save_profile_cache_locked() -> None:
    try:
        persisted = {
            k: [ts, data]
            for k, (ts, data) in _PROFILE_CACHE.items()
            if data.get("lookup_source") not in {"not_found", "rate_limited"}
        }
        PROFILE_CACHE_FILE.write_text(json.dumps(persisted, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


_load_profile_cache()


def _get_steam_api_key() -> Optional[str]:
    global _STEAM_API_KEY_CACHE
    if _STEAM_API_KEY_CACHE is not None:
        return _STEAM_API_KEY_CACHE or None
    key = os.environ.get("STEAM_API_KEY", "").strip()
    if not key:
        for rc_path in (Path(__file__).resolve().parent / ".env", Path.home() / ".zshrc", Path.home() / ".bashrc"):
            if rc_path.is_file():
                try:
                    txt = rc_path.read_text(encoding="utf-8", errors="ignore")
                    m = re.search(r'(?:export\s+)?STEAM_API_KEY\s*=\s*["\']?([A-Za-z0-9]{16,64})["\']?', txt)
                    if m:
                        key = m.group(1).strip()
                        break
                except OSError:
                    pass
    _STEAM_API_KEY_CACHE = key
    return key or None


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
    search_log: list[str] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)


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


def _http_get_status(url: str, timeout: float = 4.0, cookies: Optional[str] = None) -> tuple[int, Optional[bytes]]:
    import shutil
    import subprocess

    if shutil.which("curl"):
        cmd = ["curl", "-sL", "--max-time", str(timeout), "-w", "\n%{http_code}"]
        if cookies:
            cmd.extend(["-b", cookies])
        cmd.append(url)
        try:
            res = subprocess.run(cmd, capture_output=True, timeout=timeout + 2)
            out = res.stdout
            if b"\n" in out:
                body, code_bytes = out.rsplit(b"\n", 1)
                try:
                    code = int(code_bytes.strip())
                    return code, (body if code == 200 else None)
                except ValueError:
                    pass
        except Exception:
            pass

    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    if cookies:
        headers["Cookie"] = cookies
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, TimeoutError, ValueError):
        return 0, None


def _http_get(url: str, timeout: float = 4.0, cookies: Optional[str] = None) -> Optional[bytes]:
    status, data = _http_get_status(url, timeout=timeout, cookies=cookies)
    return data if status == 200 else None


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


def _fetch_leetify(steamid64: str) -> Optional[dict]:
    url = f"https://api-public.cs-prod.leetify.com/v3/profile?steam64_id={urllib.parse.quote(steamid64)}"
    raw = _http_get(url, timeout=4.5)
    if not raw:
        return None
    try:
        d = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None
    if not isinstance(d, dict) or not d.get("steam64_id"):
        return None

    ranks = d.get("ranks") or {}
    rating = d.get("rating") or {}
    stats = d.get("stats") or {}
    winrate_raw = d.get("winrate")
    winrate = round(float(winrate_raw) * 100.0, 1) if isinstance(winrate_raw, (int, float)) else None

    return {
        "url": f"https://leetify.com/app/profile/{steamid64}",
        "privacy_mode": d.get("privacy_mode"),
        "total_matches": d.get("total_matches"),
        "winrate": winrate,
        "first_match_date": (d.get("first_match_date") or "")[:10] or None,
        "bans": d.get("bans") or [],
        "premier": ranks.get("premier"),
        "faceit": ranks.get("faceit"),
        "faceit_elo": ranks.get("faceit_elo"),
        "leetify_rating": round(float(ranks["leetify"]), 2) if isinstance(ranks.get("leetify"), (int, float)) else None,
        "aim": round(float(rating["aim"]), 1) if isinstance(rating.get("aim"), (int, float)) else None,
        "positioning": round(float(rating["positioning"]), 1) if isinstance(rating.get("positioning"), (int, float)) else None,
        "reaction_time_ms": round(float(stats["reaction_time_ms"])) if isinstance(stats.get("reaction_time_ms"), (int, float)) else None,
        "preaim": round(float(stats["preaim"]), 1) if isinstance(stats.get("preaim"), (int, float)) else None,
        "accuracy_head": round(float(stats["accuracy_head"]), 1) if isinstance(stats.get("accuracy_head"), (int, float)) else None,
    }


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

    status = 0
    raw = None
    for attempt in range(2):
        with _SEARCH_LOCK:
            wait = 0.55 - (time.time() - _LAST_SEARCH_TS)
            if wait > 0:
                time.sleep(wait)
            _LAST_SEARCH_TS = time.time()
        status, raw = _http_get_status(url, timeout=4.5, cookies=f"sessionid={session_id}")
        if status != 429:
            break
        time.sleep(1.5)

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
    out: list[dict] = []

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


def lookup_steam_profile(nick: str, avatar_thumb: object = None, screenshot_avatar: Optional[str] = None) -> SteamProfile:
    clean_nick = nick.strip()
    cache_key = clean_nick.lower()
    now = time.time()

    with _CACHE_LOCK:
        cached = _PROFILE_CACHE.get(cache_key)
        if cached:
            ttl = CACHE_TTL_SEC if cached[1].get("lookup_source") != "not_found" else 30
            if (now - cached[0]) < ttl:
                data = dict(cached[1])
                if screenshot_avatar:
                    data["screenshot_avatar"] = screenshot_avatar
                return SteamProfile(**data)

    data: Optional[dict] = None
    search_log: list[str] = []
    candidates: list[dict] = []
    if clean_nick:
        data, search_log, candidates = _search_steam_community(clean_nick, avatar_thumb=avatar_thumb)

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
            search_log=search_log,
            candidates=candidates,
        )
    else:
        steamid64 = data.get("steamid64")
        leetify_data = _fetch_leetify(steamid64) if steamid64 else None
        if leetify_data:
            search_log.append(f"Leetify: знайдено ({leetify_data.get('total_matches') or 0} матчів)")
        elif steamid64:
            search_log.append("Leetify: профіль відсутній або закритий")
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
            search_log=search_log,
            candidates=candidates,
        )

    if profile.lookup_source != "rate_limited":
        with _CACHE_LOCK:
            _PROFILE_CACHE[cache_key] = (now, asdict(profile))
            if profile.lookup_source != "not_found":
                _save_profile_cache_locked()

    return profile


def _compute_probability(player: Player, steam: SteamProfile) -> tuple[float, str, list[str]]:
    reasons: list[str] = []

    # 1. Current lobby contribution (kept low: ~20% max weight)
    has_lobby_score = not math.isnan(player.score)
    if has_lobby_score:
        lobby_raw = float(player.score)
    else:
        lobby_raw = min(100.0, 15.0 + max(0.0, (player.kd - 1.0) * 12.0) + max(0.0, (player.hs - 45.0) * 0.7))

    prob = 8.0 + 0.20 * lobby_raw
    if lobby_raw >= 70.0:
        reasons.append(f"Високі цифри в цьому лобі ({lobby_raw:.0f}%)")

    if 0 <= player.level <= 3 and (player.kd >= 1.8 or player.hs >= 65):
        prob += 8.0
        reasons.append(f"Низький рівень у грі (lvl {player.level})")

    # 2. External Steam account signals (~35% weight)
    if steam.vac_banned:
        prob += 45.0
        reasons.append("VAC-бан у Steam")
    if steam.is_limited:
        prob += 14.0
        reasons.append("Обмежений акаунт Steam")
    if steam.account_age_years is not None:
        if steam.account_age_years < 1.0:
            prob += 14.0
            reasons.append(f"Новий акаунт ({steam.account_age_years} р.)")
        elif steam.account_age_years < 2.0:
            prob += 7.0
            reasons.append(f"Молодий акаунт ({steam.account_age_years} р.)")
        elif steam.account_age_years >= 6.0:
            prob -= 5.0
    if steam.cs2_hours is not None:
        if steam.cs2_hours < 100:
            prob += 16.0
            reasons.append(f"Мало годин у CS2 ({steam.cs2_hours:.0f} год)")
        elif steam.cs2_hours < 300:
            prob += 8.0
            reasons.append(f"Небагато годин у CS2 ({steam.cs2_hours:.0f} год)")
        elif steam.cs2_hours >= 1500:
            prob -= 6.0
    if steam.privacy_state and steam.privacy_state != "public":
        prob += 8.0
        reasons.append(f"Закритий профіль ({steam.privacy_state})")
    elif steam.lookup_source == "not_found":
        prob += 6.0
        reasons.append("Steam-профіль не знайдено")

    # 3. External Leetify CS2 history & mechanics (~45% weight)
    lf = steam.leetify
    if lf:
        bans = lf.get("bans") or []
        if bans:
            prob += 45.0
            reasons.append(f"Бани на Leetify/платформах ({len(bans)})")

        matches = lf.get("total_matches") or 0
        winrate = lf.get("winrate")
        aim = lf.get("aim")
        rt = lf.get("reaction_time_ms")
        preaim = lf.get("preaim")
        faceit_lvl = lf.get("faceit") or 0
        premier = lf.get("premier") or 0
        high_elo = faceit_lvl >= 8 or premier >= 20000

        if rt is not None:
            if rt < 320:
                prob += 25.0
                reasons.append(f"Надлюдська реакція Leetify ({rt} мс)")
            elif rt < 380:
                prob += 14.0
                reasons.append(f"Дуже швидка реакція ({rt} мс)")
            elif rt < 420 and not high_elo:
                prob += 6.0
                reasons.append(f"Швидка реакція ({rt} мс)")

        if aim is not None:
            if aim >= 95 and matches < 150:
                prob += 16.0
                reasons.append(f"Aim {aim} при {matches} матчах")
            elif aim >= 92 and not high_elo:
                prob += 10.0
                reasons.append(f"Дуже високий Aim ({aim})")

        if winrate is not None and matches >= 5:
            if winrate >= 72 and matches < 120:
                prob += 18.0
                reasons.append(f"Вінрейт {winrate}% за {matches} матчів")
            elif winrate >= 64 and matches < 100:
                prob += 12.0
                reasons.append(f"Високий вінрейт {winrate}% ({matches} матчів)")

        if preaim is not None and preaim < 5.0 and matches < 100 and not high_elo:
            prob += 8.0
            reasons.append(f"Низький preaim ({preaim}°) на новому акаунті")

        if matches >= 400 and (winrate is None or winrate <= 57) and not bans:
            prob -= 8.0

    prob = max(1.0, min(99.0, round(prob, 1)))

    if not reasons:
        reasons.append("Зовнішні та лобі-показники в нормі")

    if prob >= 75:
        risk = "critical"
    elif prob >= 55:
        risk = "high"
    elif prob >= 30:
        risk = "medium"
    else:
        risk = "low"

    return prob, risk, reasons


def enrich_players(players: list[Player], fetch_steam: bool = True) -> list[PlayerReport]:
    if not players:
        return []

    if fetch_steam:
        with ThreadPoolExecutor(max_workers=2) as pool:
            profiles = list(
                pool.map(lambda p: lookup_steam_profile(p.nick, p.avatar_thumb, p.avatar_data_url), players)
            )
    else:
        profiles = [
            SteamProfile(
                query_nick=p.nick,
                screenshot_avatar=p.avatar_data_url,
                rip_url=f"https://steamcommunity.rip/id/{urllib.parse.quote(p.nick, safe='')}/",
                rip_nick_url=f"https://steamcommunity.rip/id/{urllib.parse.quote(p.nick, safe='')}/",
            )
            for p in players
        ]

    reports: list[PlayerReport] = []
    for p, steam in zip(players, profiles):
        prob, risk, reasons = _compute_probability(p, steam)
        anomaly = None if math.isnan(p.score) else round(p.score, 1)
        reports.append(
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
            )
        )

    reports.sort(key=lambda r: r.cheat_probability, reverse=True)
    return reports


def recheck_player_report(player_dict: dict, query: str) -> dict:
    """Re-resolve a single player by corrected nickname, SteamID64, or Steam profile URL."""
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
        leetify_data = _fetch_leetify(steamid64) if steamid64 else None
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
        )
    else:
        steam = lookup_steam_profile(q)

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
    prob, risk, reasons = _compute_probability(p, steam)
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
        )
    )
