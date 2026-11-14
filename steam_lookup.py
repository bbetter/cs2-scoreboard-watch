"""Resolve CS2 player nicknames to Steam + Leetify profiles and compute risk scores."""

from __future__ import annotations

import html
import json
import logging
import math
import os
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
from pathlib import Path
from typing import Any, Optional

from nick_match import best_match_index
from scoreboard_scan import Player

_LOG = logging.getLogger("pton")


def _setup_pton_log() -> None:
    """Append INFO+ records to pton.logs next to this file, once per process."""
    if any(isinstance(h, logging.FileHandler) for h in _LOG.handlers):
        return
    try:
        handler = logging.FileHandler(Path(__file__).resolve().parent / "pton.logs", encoding="utf-8")
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _LOG.addHandler(handler)
    _LOG.setLevel(logging.INFO)
    _LOG.propagate = False


_setup_pton_log()

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
STEAMID64_BASE = 76561197960265728
CACHE_TTL_SEC = 86400
PROFILE_CACHE_FILE = Path(__file__).resolve().parent / "profile_cache.json"
_CACHE_LOCK = threading.Lock()
_PROFILE_CACHE: dict[str, tuple[float, dict]] = {}
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
_STEAM_API_KEY_CACHE: Optional[str] = None
_FACEIT_API_KEY_CACHE: Optional[str] = None


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


def reset_steam_api_key_cache() -> None:
    global _STEAM_API_KEY_CACHE
    _STEAM_API_KEY_CACHE = None


def reset_faceit_api_key_cache() -> None:
    global _FACEIT_API_KEY_CACHE
    _FACEIT_API_KEY_CACHE = None


def _get_faceit_api_key() -> Optional[str]:
    global _FACEIT_API_KEY_CACHE
    if _FACEIT_API_KEY_CACHE is not None:
        return _FACEIT_API_KEY_CACHE or None
    try:
        from config_store import load_config

        cfg_key = str(load_config().get("faceit_api_key") or "").strip()
        if cfg_key:
            _FACEIT_API_KEY_CACHE = cfg_key
            return cfg_key
    except Exception:
        pass
    key = os.environ.get("FACEIT_API_KEY", "").strip()
    if not key:
        env_path = Path(__file__).resolve().parent / ".env"
        if env_path.is_file():
            try:
                txt = env_path.read_text(encoding="utf-8", errors="ignore")
                m = re.search(r'(?:export\s+)?FACEIT_API_KEY\s*=\s*["\']?([A-Za-z0-9_-]{16,128})["\']?', txt)
                if m:
                    key = m.group(1).strip()
            except OSError:
                pass
    _FACEIT_API_KEY_CACHE = key
    return key or None


def _get_steam_api_key() -> Optional[str]:
    global _STEAM_API_KEY_CACHE
    if _STEAM_API_KEY_CACHE is not None:
        return _STEAM_API_KEY_CACHE or None
    try:
        from config_store import load_config

        cfg_key = str(load_config().get("steam_api_key") or "").strip()
        if cfg_key:
            _STEAM_API_KEY_CACHE = cfg_key
            return cfg_key
    except Exception:
        pass
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
    faceit: dict = field(default_factory=dict)
    cs2_stats: dict = field(default_factory=dict)
    extra_stats: dict = field(default_factory=dict)
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
    smurf_probability: float = 0.0
    smurf_risk_level: str = "low"
    uncertainty: float = 50.0
    effective_weights: dict = field(default_factory=dict)


def _http_get_status(
    url: str,
    timeout: float = 4.0,
    cookies: Optional[str] = None,
    headers: Optional[dict[str, str]] = None,
) -> tuple[int, Optional[bytes]]:
    import shutil
    import subprocess

    if shutil.which("curl"):
        cmd = ["curl", "-sL", "--max-time", str(timeout), "-w", "\n%{http_code}"]
        if cookies:
            cmd.extend(["-b", cookies])
        if headers:
            for k, v in headers.items():
                cmd.extend(["-H", f"{k}: {v}"])
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

    req_headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    if cookies:
        req_headers["Cookie"] = cookies
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, TimeoutError, ValueError):
        return 0, None


def _http_get(
    url: str,
    timeout: float = 4.0,
    cookies: Optional[str] = None,
    headers: Optional[dict[str, str]] = None,
) -> Optional[bytes]:
    status, data = _http_get_status(url, timeout=timeout, cookies=cookies, headers=headers)
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


LEETIFY_API_BASE = "https://api-public.cs-prod.leetify.com"
LEETIFY_PROFILE_URL = "https://leetify.com/app/profile/{}"
LEETIFY_TIMEOUT_SEC = 8.0


def _leetify_api_key() -> str:
    try:
        from config_store import load_config

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


def _num(val: Any, nd: Optional[int] = None) -> Optional[Any]:
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    if nd == 0:
        return int(round(val))
    return round(float(val), nd) if nd is not None else float(val)


def _round_stats(d: dict) -> dict:
    out: dict = {}
    for k, v in d.items():
        out[k] = round(float(v), 4) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
    return out


def _fetch_leetify(steamid64: str) -> Optional[dict]:
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


CSTRACKER_BASE = "https://cstracker.gg/players/{}"
CSTRACKER_MIN_INTERVAL_SEC = 1.0  # cstracker.gg robots.txt: Crawl-delay: 1
CSTRACKER_USER_AGENT = "pton-cs2-lookup/1.0 (personal analysis, low rate)"
_CSTRACKER_LOCK = threading.Lock()
_CSTRACKER_LAST_REQUEST = [0.0]


def _cstracker_throttle() -> None:
    """Serialize cstracker.gg requests and keep at least CSTRACKER_MIN_INTERVAL_SEC between them."""
    with _CSTRACKER_LOCK:
        wait = CSTRACKER_MIN_INTERVAL_SEC - (time.monotonic() - _CSTRACKER_LAST_REQUEST[0])
        if wait > 0:
            time.sleep(wait)
        _CSTRACKER_LAST_REQUEST[0] = time.monotonic()


def _num_or_raw(text: str) -> Any:
    """'16.1%' -> 16.1, '3.5°' -> 3.5, '1,132ms' -> 1132.0; anything else is returned as-is."""
    cleaned = text.replace(",", "").strip()
    m = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)\s*(%|°|ms|s)?", cleaned)
    return float(m.group(1)) if m else text


def _norm_text(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _norm_text(text).lower()).strip("_")


def _parse_cstracker_html(html_text: str) -> dict:
    """Parse a cstracker.gg player page into trust, aim/utility/behaviour metrics and recent matches."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_text, "html.parser")
    title = _norm_text(soup.title.get_text()) if soup.title else ""
    if "Just a moment" in title or soup.select_one("#challenge-form, .cf-challenge"):
        return {"status": "blocked"}

    trust = None
    label = soup.find(string=re.compile(r"^\s*trust rating\s*$", re.I))
    if label is not None:
        value_box = label.parent.find_next_sibling()
        span = value_box.find("span") if value_box is not None else None
        if span is not None:
            trust = _num_or_raw(_norm_text(span.get_text()))

    sections: dict[str, dict] = {}
    for eyebrow in soup.select(".eyebrow"):
        name = _slug(eyebrow.get_text()).replace("_and_", "_")
        rows: dict[str, Any] = {}
        for row in eyebrow.parent.find_all("div"):
            if "justify-between" not in (row.get("class") or []):
                continue
            spans = row.find_all("span", recursive=False)
            if len(spans) == 2:
                rows[_norm_text(spans[0].get_text())] = _num_or_raw(_norm_text(spans[1].get_text()))
        if rows:
            sections[name] = rows

    matches: list[dict] = []
    header_cell = soup.find("th", string=re.compile(r"K\s*/\s*D\s*/\s*A"))
    table = header_cell.find_parent("table") if header_cell is not None else None
    if table is not None:
        headers = [_norm_text(th.get_text()) for th in table.select("thead th")]
        for tr in table.select("tbody tr"):
            cells = [_norm_text(td.get_text(" ")) for td in tr.find_all("td")]
            if len(cells) != len(headers) or not cells:
                continue
            rec = dict(zip(headers, cells))
            kda = [int(x) for x in re.findall(r"\d+", rec.get("K / D / A", ""))[:3]]
            kills, deaths, assists = (kda + [None, None, None])[:3]
            matches.append({
                "match": rec.get("Match"),
                "rank": rec.get("Rank"),
                "kills": kills,
                "deaths": deaths,
                "assists": assists,
                "kd": _num_or_raw(rec.get("K/D", "")),
                "adr": _num_or_raw(rec.get("ADR", "")),
                "rating": _num_or_raw(rec.get("Rating", "")),
                "kast": _num_or_raw(rec.get("KAST", "")),
                "acc": _num_or_raw(rec.get("ACC", "")),
                "preaim": _num_or_raw(rec.get("Preaim", "")),
                "ttd": _num_or_raw(rec.get("TTD", "")),
                "when": rec.get("When"),
            })
        matches = matches[:10]

    status = "ok" if (trust is not None or sections or matches) else "not_found"
    return {"status": status, "trust": trust, "sections": sections, "matches": matches}


def _fetch_cstracker(steamid64: str) -> dict:
    """Fetch cstracker.gg player data. One request per player, throttled, honest user agent.

    Never bypasses a Cloudflare challenge: a 403/429/503 or challenge page is reported as
    status 'blocked' and cached like any other result.
    """
    if not steamid64:
        return {}
    url = CSTRACKER_BASE.format(steamid64)
    _cstracker_throttle()
    started = time.monotonic()
    status_code, raw = _http_get_status(url, timeout=10.0, headers={"User-Agent": CSTRACKER_USER_AGENT})
    elapsed = time.monotonic() - started
    if status_code == 404:
        result = {"status": "not_found"}
    elif raw is None:
        result = {"status": "blocked" if status_code in (403, 429, 503) else "error"}
    else:
        try:
            result = _parse_cstracker_html(raw.decode("utf-8", errors="replace"))
        except Exception:
            _LOG.exception("cstracker steamid64=%s parse failed", steamid64)
            result = {"status": "error"}
    result["url"] = url
    result["http_status"] = status_code
    _LOG.info("cstracker steamid64=%s status=%s http=%s in %.2fs", steamid64, result["status"], status_code, elapsed)
    return result


def _cstracker_extra(steamid64: Optional[str]) -> dict:
    return {"cstracker": _fetch_cstracker(steamid64)} if steamid64 else {}


def _safe_float(val: object) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        return float(str(val).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _safe_int(val: object) -> Optional[int]:
    num = _safe_float(val)
    return int(round(num)) if num is not None else None


def _fetch_faceit_stats(steamid64: str) -> dict:
    api_key = _get_faceit_api_key()
    if not api_key or not steamid64:
        return {}
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    player_url = (
        f"https://open.faceit.com/data/v4/players"
        f"?game=cs2&game_player_id={urllib.parse.quote(steamid64)}"
    )
    raw = _http_get(player_url, timeout=4.5, headers=headers)
    if not raw:
        return {}
    try:
        d = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(d, dict) or not d.get("player_id"):
        return {}

    player_id = str(d["player_id"])
    cs2_info = (d.get("games") or {}).get("cs2") or {}
    raw_url = str(d.get("faceit_url") or "")
    faceit_url = raw_url.replace("{lang}", "en") if raw_url else None

    result: dict = {
        "found": True,
        "player_id": player_id,
        "nickname": d.get("nickname"),
        "faceit_url": faceit_url,
        "skill_level": _safe_int(cs2_info.get("skill_level")),
        "faceit_elo": _safe_int(cs2_info.get("faceit_elo")),
        "matches": None,
        "winrate": None,
        "kd": None,
        "hs_pct": None,
    }

    stats_url = f"https://open.faceit.com/data/v4/players/{urllib.parse.quote(player_id)}/stats/cs2"
    raw_stats = _http_get(stats_url, timeout=4.5, headers=headers)
    if raw_stats:
        try:
            sd = json.loads(raw_stats.decode("utf-8", errors="replace"))
            lifetime = sd.get("lifetime") if isinstance(sd, dict) else None
            if isinstance(lifetime, dict):
                result["matches"] = _safe_int(lifetime.get("Matches"))
                wr = _safe_float(lifetime.get("Win Rate %"))
                kd = _safe_float(lifetime.get("Average K/D Ratio"))
                hs = _safe_float(lifetime.get("Average Headshots %"))
                result["winrate"] = round(wr, 1) if wr is not None else None
                result["kd"] = round(kd, 2) if kd is not None else None
                result["hs_pct"] = round(hs, 1) if hs is not None else None
        except json.JSONDecodeError:
            pass

    return result


def _fetch_steam_cs2_stats(steamid64: str, api_key: str) -> dict:
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


def _fetch_external_stats(steamid64: Optional[str]) -> tuple[Optional[dict], dict, dict]:
    if not steamid64:
        return None, {}, {}
    steam_api_key = _get_steam_api_key()
    with ThreadPoolExecutor(max_workers=3) as pool:
        f_leetify = pool.submit(_fetch_leetify, steamid64)
        f_faceit = pool.submit(_fetch_faceit_stats, steamid64)
        f_cs2 = pool.submit(_fetch_steam_cs2_stats, steamid64, steam_api_key) if steam_api_key else None
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
    if data.get("steamid64") and not data.get("cs2_stats") and _get_steam_api_key():
        return False  # Steam CS2 stats were not fetched while no key was configured
    ct = (data.get("extra_stats") or {}).get("cstracker")
    if data.get("steamid64") and (not isinstance(ct, dict) or ct.get("status") == "error"):
        return False  # cstracker data missing or failed; 'blocked'/'not_found' stay cached
    lf = data.get("leetify")
    if not isinstance(lf, dict):
        return not data.get("steamid64")
    return "fetch" in lf


def lookup_steam_profile(nick: str, avatar_thumb: object = None, screenshot_avatar: Optional[str] = None) -> SteamProfile:
    clean_nick = nick.strip()
    cache_key = clean_nick.lower()
    now = time.time()

    with _CACHE_LOCK:
        cached = _PROFILE_CACHE.get(cache_key)
        if cached:
            ttl = CACHE_TTL_SEC if cached[1].get("lookup_source") != "not_found" else 30
            if (now - cached[0]) < ttl and _cache_entry_current(cached[1]):
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

    if profile.lookup_source != "rate_limited":
        with _CACHE_LOCK:
            _PROFILE_CACHE[cache_key] = (now, asdict(profile))
            if profile.lookup_source != "not_found":
                _save_profile_cache_locked()

    return profile


def _risk_level(prob: float) -> str:
    if prob >= 75:
        return "critical"
    if prob >= 55:
        return "high"
    if prob >= 30:
        return "medium"
    return "low"


def _load_base_weights() -> dict[str, float]:
    defaults = {"scoreboard": 0.30, "leetify": 0.40, "steam_account": 0.30}
    try:
        from config_store import load_config

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
    base = _load_base_weights()

    if steam.steamid64:
        has_steam = 1.0 if steam.privacy_state == "public" else 0.45
    else:
        has_steam = 0.0

    has_faceit = bool(steam.faceit and steam.faceit.get("found"))
    has_leetify = bool(steam.leetify)
    has_cs2_stats = bool(steam.cs2_stats)
    has_extra = bool(((steam.extra_stats or {}).get("cstracker") or {}).get("status") == "ok")

    if has_leetify or has_faceit or has_extra:
        has_leetify_or_faceit = 1.0
    elif has_cs2_stats:
        has_leetify_or_faceit = 0.5
    else:
        has_leetify_or_faceit = 0.0

    raw_sb = base["scoreboard"] * 1.0
    raw_steam = base["steam_account"] * has_steam
    raw_ext = base["leetify"] * has_leetify_or_faceit
    total = raw_sb + raw_steam + raw_ext

    if (has_steam == 0.0 and has_leetify_or_faceit == 0.0) or total <= 0.0:
        effective_weights = {"scoreboard": 1.0, "leetify": 0.0, "steam_account": 0.0}
    else:
        effective_weights = {
            "scoreboard": round(raw_sb / total, 3),
            "leetify": round(raw_ext / total, 3),
            "steam_account": round(raw_steam / total, 3),
        }

    uncertainty = 100.0
    uncertainty -= 35.0 * has_steam
    if has_leetify:
        uncertainty -= 35.0
    if has_faceit:
        uncertainty -= 15.0
    if has_cs2_stats:
        uncertainty -= 15.0
    if has_extra:
        uncertainty -= 20.0
    uncertainty = round(max(0.0, min(100.0, uncertainty)), 1)

    return uncertainty, effective_weights


# Евристичні пороги для недавнього вікна (Leetify stats ~останні матчі, cstracker aim_reactions).
# ЕВРИСТИКИ: не калібровані на розміченій вибірці. Перевіряти через feedback.json (POST /api/feedback).
# Значення cstracker/Leetify вважаються у відсотках (0..100), як і в web/app.js.
AIM_COUNTER_STRAFE_STRONG = 95.0      # % добрих counter-strafe
AIM_COUNTER_STRAFE_WEAK = 90.0
AIM_COUNTER_STRAFE_POINTS = (12.0, 6.0)
AIM_ACCURACY_HEAD_STRONG = 50.0       # % попадань у голову
AIM_ACCURACY_HEAD_WEAK = 40.0
AIM_ACCURACY_HEAD_POINTS = (15.0, 7.0)
AIM_SPRAY_STRONG = 55.0               # % попадань при спреї
AIM_SPRAY_WEAK = 45.0
AIM_SPRAY_POINTS = (10.0, 5.0)
AIM_SPOT_TO_KILL_MS_STRONG = 250.0    # cstracker "Spot to kill"; лише коли немає Leetify reaction_time_ms
AIM_SPOT_TO_KILL_MS_WEAK = 320.0
AIM_SPOT_TO_KILL_POINTS = (20.0, 10.0)
# Сумарні бали з недавнього вікна, з яких вважаємо, що аім "елітний" для смурф-бонусу.
AIM_ELITE_RECENT_POINTS = 20.0
AIM_ELITE_SMURF_BONUS = 8.0


def _pick_recent_metric(
    lf: Optional[dict], cst_sections: dict, leetify_key: str, cst_key: Optional[str] = None
) -> tuple[Optional[float], str]:
    """Return (value, source) for one metric: Leetify first, cstracker as fallback.

    Using one source per metric avoids double-counting the same aim metric.
    """
    if lf:
        # Flat keys (accuracy_head, preaim, reaction_time_ms) and raw stats (counter-strafe, spray).
        raw = lf.get(leetify_key)
        if raw is None:
            raw = (lf.get("stats") or {}).get(leetify_key)
        val = _num(raw, 1)
        if val is not None:
            return float(val), "Leetify"
    if cst_key:
        val = (cst_sections.get("aim_reactions") or {}).get(cst_key)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return float(val), "cstracker"
    return None, ""


def _recent_aim_signals(
    lf: Optional[dict], extra_stats: dict, high_elo: bool
) -> tuple[float, bool, list[str]]:
    """Score recent-window aim metrics from Leetify and cstracker.

    Returns (points added to the external component, elite_recent flag, reasons).
    Only adds points, never subtracts: missing data contributes nothing. Players with
    high_elo are skipped for the aim metrics, matching the existing reaction/aim rules.
    Reasons name the source (Leetify or cstracker) and the window; lifetime Steam
    cs2_stats are not used here.
    """
    cst = (extra_stats or {}).get("cstracker") or {}
    cst_sections = (cst.get("sections") or {}) if cst.get("status") == "ok" else {}
    points = 0.0
    reasons: list[str] = []
    if high_elo:
        return 0.0, False, reasons

    cs_val, cs_src = _pick_recent_metric(lf, cst_sections, "counter_strafing_good_shots_ratio", "Counter-strafing")
    if cs_val is not None:
        if cs_val >= AIM_COUNTER_STRAFE_STRONG:
            points += AIM_COUNTER_STRAFE_POINTS[0]
            reasons.append(f"Counter-strafing {cs_val:.0f}% ({cs_src}, недавні матчі)")
        elif cs_val >= AIM_COUNTER_STRAFE_WEAK:
            points += AIM_COUNTER_STRAFE_POINTS[1]
            reasons.append(f"Високий counter-strafing {cs_val:.0f}% ({cs_src}, недавні матчі)")

    head_val, head_src = _pick_recent_metric(lf, cst_sections, "accuracy_head")
    if head_val is not None:
        if head_val >= AIM_ACCURACY_HEAD_STRONG:
            points += AIM_ACCURACY_HEAD_POINTS[0]
            reasons.append(f"Точність у голову {head_val:.0f}% ({head_src}, недавні матчі)")
        elif head_val >= AIM_ACCURACY_HEAD_WEAK:
            points += AIM_ACCURACY_HEAD_POINTS[1]
            reasons.append(f"Висока точність у голову {head_val:.0f}% ({head_src}, недавні матчі)")

    spray_val, spray_src = _pick_recent_metric(lf, cst_sections, "spray_accuracy", "Spray accuracy")
    if spray_val is not None:
        if spray_val >= AIM_SPRAY_STRONG:
            points += AIM_SPRAY_POINTS[0]
            reasons.append(f"Точність спрею {spray_val:.0f}% ({spray_src}, недавні матчі)")
        elif spray_val >= AIM_SPRAY_WEAK:
            points += AIM_SPRAY_POINTS[1]
            reasons.append(f"Висока точність спрею {spray_val:.0f}% ({spray_src}, недавні матчі)")

    # Spot-to-kill is a fallback for reaction time only when Leetify reaction_time_ms is absent.
    if not (lf and lf.get("reaction_time_ms") is not None):
        stk = (cst_sections.get("aim_reactions") or {}).get("Spot to kill")
        if isinstance(stk, (int, float)) and not isinstance(stk, bool) and stk > 0:
            if stk < AIM_SPOT_TO_KILL_MS_STRONG:
                points += AIM_SPOT_TO_KILL_POINTS[0]
                reasons.append(f"Spot-to-kill {stk:.0f} мс (cstracker, недавні матчі)")
            elif stk < AIM_SPOT_TO_KILL_MS_WEAK:
                points += AIM_SPOT_TO_KILL_POINTS[1]
                reasons.append(f"Швидкий spot-to-kill {stk:.0f} мс (cstracker, недавні матчі)")

    return points, points >= AIM_ELITE_RECENT_POINTS, reasons


def _compute_scores(
    player: Player, steam: SteamProfile
) -> tuple[float, str, float, str, float, dict[str, float], list[str]]:
    reasons: list[str] = []
    uncertainty, effective_weights = _compute_uncertainty_and_weights(steam)

    # 1. Scoreboard component (0..100)
    has_lobby_score = not math.isnan(player.score)
    if has_lobby_score:
        lobby_raw = float(player.score)
    else:
        lobby_raw = min(100.0, 15.0 + max(0.0, (player.kd - 1.0) * 12.0) + max(0.0, (player.hs - 45.0) * 0.7))

    sb_score = lobby_raw * 0.85
    if lobby_raw >= 70.0:
        reasons.append(f"Високі цифри в цьому лобі ({lobby_raw:.0f}%)")
    if 0 <= player.level <= 3 and (player.kd >= 1.8 or player.hs >= 65):
        sb_score += 18.0
        reasons.append(f"Низький рівень у грі (lvl {player.level})")
    sb_score = max(0.0, min(100.0, sb_score))

    # 2. Steam account component (0..100)
    steam_score = 10.0
    if steam.vac_banned:
        steam_score += 75.0
        reasons.append("VAC-бан у Steam")
    if steam.trade_ban:
        steam_score += 12.0
        reasons.append(f"Трейд-бан у Steam ({steam.trade_ban})")
    if steam.is_limited:
        steam_score += 22.0
        reasons.append("Обмежений акаунт Steam")
    if steam.account_age_years is not None:
        if steam.account_age_years < 1.0:
            steam_score += 24.0
            reasons.append(f"Новий акаунт ({steam.account_age_years} р.)")
        elif steam.account_age_years < 2.0:
            steam_score += 12.0
            reasons.append(f"Молодий акаунт ({steam.account_age_years} р.)")
        elif steam.account_age_years >= 6.0:
            steam_score -= 10.0
    if steam.cs2_hours is not None:
        if steam.cs2_hours < 100:
            steam_score += 28.0
            reasons.append(f"Мало годин у CS2 ({steam.cs2_hours:.0f} год)")
        elif steam.cs2_hours < 300:
            steam_score += 14.0
            reasons.append(f"Небагато годин у CS2 ({steam.cs2_hours:.0f} год)")
        elif steam.cs2_hours >= 1500:
            steam_score -= 12.0
    if steam.privacy_state and steam.privacy_state != "public":
        steam_score += 16.0
        reasons.append(f"Закритий профіль ({steam.privacy_state})")
    elif steam.lookup_source == "not_found":
        steam_score += 12.0
        reasons.append("Steam-профіль не знайдено")
    steam_score = max(0.0, min(100.0, steam_score))

    # 3. External stats component: Leetify + FACEIT + Steam CS2 stats (0..100)
    ext_score = 10.0
    lf = steam.leetify
    faceit = steam.faceit or {}
    cs2 = steam.cs2_stats or {}

    faceit_lvl = faceit.get("skill_level") or (lf.get("faceit") if lf else None) or 0
    faceit_elo = faceit.get("faceit_elo") or (lf.get("faceit_elo") if lf else None) or 0
    premier = (lf.get("premier") if lf else None) or 0
    high_elo = faceit_lvl >= 8 or faceit_elo >= 1800 or premier >= 20000

    lf_bans = (lf.get("bans") or []) if lf else []
    if lf:
        if lf_bans:
            ext_score += 80.0
            reasons.append(f"Бани на Leetify/платформах ({len(lf_bans)})")

        matches = lf.get("total_matches") or 0
        winrate = lf.get("winrate")
        aim = lf.get("aim")
        rt = lf.get("reaction_time_ms")
        preaim = lf.get("preaim")

        if rt is not None:
            if rt < 320:
                ext_score += 45.0
                reasons.append(f"Надлюдська реакція Leetify ({rt} мс)")
            elif rt < 380:
                ext_score += 25.0
                reasons.append(f"Дуже швидка реакція ({rt} мс)")
            elif rt < 420 and not high_elo:
                ext_score += 12.0
                reasons.append(f"Швидка реакція ({rt} мс)")

        if aim is not None:
            if aim >= 95 and matches < 150:
                ext_score += 30.0
                reasons.append(f"Aim {aim} при {matches} матчах")
            elif aim >= 92 and not high_elo:
                ext_score += 18.0
                reasons.append(f"Дуже високий Aim ({aim})")

        if winrate is not None and matches >= 5:
            if winrate >= 72 and matches < 120:
                ext_score += 32.0
                reasons.append(f"Вінрейт {winrate}% за {matches} матчів")
            elif winrate >= 64 and matches < 100:
                ext_score += 20.0
                reasons.append(f"Високий вінрейт {winrate}% ({matches} матчів)")

        if preaim is not None and preaim < 5.0 and matches < 100 and not high_elo:
            ext_score += 15.0
            reasons.append(f"Низький preaim ({preaim}°) на новому акаунті")

        if matches >= 400 and (winrate is None or winrate <= 57) and not lf_bans:
            ext_score -= 15.0

    recent_pts, recent_elite, recent_reasons = _recent_aim_signals(lf, steam.extra_stats, high_elo)
    ext_score += recent_pts
    reasons.extend(recent_reasons)

    if faceit.get("found"):
        f_matches = faceit.get("matches") or 0
        f_kd = faceit.get("kd")
        f_hs = faceit.get("hs_pct")
        if f_matches >= 300 and (f_kd is None or f_kd <= 1.45) and (f_hs is None or f_hs <= 62.0):
            ext_score -= 10.0

    if cs2:
        c_matches = cs2.get("total_matches_played") or 0
        c_kd = cs2.get("kd")
        c_hs = cs2.get("hs_pct")
        if c_kd is not None and c_kd >= 2.3 and c_matches < 150:
            ext_score += 22.0
            reasons.append(f"Високий загальний K/D у CS2 ({c_kd})")
        if c_hs is not None and c_hs >= 72.0:
            ext_score += 18.0
            reasons.append(f"Високий загальний HS% у CS2 ({c_hs}%)")
        if c_matches >= 300 and (c_kd is None or c_kd <= 1.35):
            ext_score -= 10.0

    ext_score = max(0.0, min(100.0, ext_score))

    # Weighted cheat probability
    cheat_prob = (
        effective_weights["scoreboard"] * sb_score
        + effective_weights["steam_account"] * steam_score
        + effective_weights["leetify"] * ext_score
    )

    has_ban = bool(steam.vac_banned or lf_bans)
    has_extreme_rage = player.kd >= 3.0 and player.hs >= 75.0
    if has_ban:
        cheat_prob = max(cheat_prob, 78.0)
    elif not has_extreme_rage and uncertainty >= 70.0:
        damp = 1.0 - 0.25 * ((uncertainty - 70.0) / 30.0)
        cheat_prob *= damp
        if uncertainty >= 85.0:
            cheat_prob = min(cheat_prob, 68.0)

    cheat_prob = max(1.0, min(99.0, round(cheat_prob, 1)))
    cheat_risk = _risk_level(cheat_prob)

    # 4. Smurf probability (0..100)
    smurf_prob = 5.0
    young_strong = steam.account_age_years is not None and steam.account_age_years < 1.5
    young_mod = steam.account_age_years is not None and 1.5 <= steam.account_age_years < 3.0
    low_hrs_strong = steam.cs2_hours is not None and steam.cs2_hours < 150
    low_hrs_mod = steam.cs2_hours is not None and 150 <= steam.cs2_hours < 350
    low_lvl = 0 <= player.level <= 5

    account_smurf_signal = young_strong or young_mod or low_hrs_strong or low_hrs_mod or low_lvl or steam.is_limited

    high_lobby_perf = (player.kd >= 1.35 and player.kills >= 15) or player.kd >= 1.8
    high_leetify_perf = bool(lf and ((lf.get("aim") or 0) >= 75 or (lf.get("winrate") or 0) > 58))
    high_faceit_perf = bool(faceit_lvl >= 7 or faceit_elo >= 1500)
    high_cs2_perf = bool(cs2 and (cs2.get("kd") or 0) >= 1.35)
    strong_perf = high_lobby_perf or high_leetify_perf or high_faceit_perf or high_cs2_perf

    rage_hs = player.hs > 75.0 and 0 <= player.level <= 3

    if has_ban:
        smurf_prob = 10.0
    elif rage_hs:
        smurf_prob = 18.0
    elif account_smurf_signal and strong_perf:
        if young_strong:
            smurf_prob += 26.0
        elif young_mod:
            smurf_prob += 15.0
        if low_hrs_strong:
            smurf_prob += 26.0
        elif low_hrs_mod:
            smurf_prob += 15.0
        if low_lvl:
            smurf_prob += 14.0
        if steam.is_limited:
            smurf_prob += 8.0

        if high_lobby_perf:
            smurf_prob += 20.0
        if high_leetify_perf:
            smurf_prob += 15.0
        if high_faceit_perf:
            smurf_prob += 18.0
        if high_cs2_perf:
            smurf_prob += 10.0

        # Clean-ban bonus for smurf classification
        smurf_prob += 8.0

        if recent_elite:
            smurf_prob += AIM_ELITE_SMURF_BONUS
            reasons.append("Смурф-сигнал: елітний аім у недавніх матчах (Leetify/cstracker)")

        if young_strong or young_mod:
            reasons.append("Смурф-сигнал: молодий акаунт + високий K/D")
        if low_hrs_strong or low_hrs_mod:
            reasons.append("Смурф-сигнал: мало годин у CS2 + сильна гра")
        if high_faceit_perf and (young_strong or young_mod or low_hrs_strong or low_hrs_mod):
            reasons.append(f"Смурф-сигнал: високий FACEIT (lvl {faceit_lvl}) на новому акаунті")
    elif strong_perf:
        smurf_prob = 22.0 if uncertainty >= 60.0 else 15.0

    smurf_prob = max(0.0, min(99.0, round(smurf_prob, 1)))
    smurf_risk = _risk_level(smurf_prob)

    if not reasons:
        reasons.append("Зовнішні та лобі-показники в нормі")

    return cheat_prob, cheat_risk, smurf_prob, smurf_risk, uncertainty, effective_weights, reasons


def _build_player_report(p: Player, steam: SteamProfile) -> PlayerReport:
    prob, risk, smurf_prob, smurf_risk, uncertainty, eff_weights, reasons = _compute_scores(p, steam)
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
    prob, risk, smurf_prob, smurf_risk, uncertainty, eff_weights, reasons = _compute_scores(p, steam)
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
        )
    )
