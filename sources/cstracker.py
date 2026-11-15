"""CSTracker.gg: trust rating, aim/utility metrics and recent matches (scraped; may be blocked)."""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any

from lookup.http_client import http_get_status as _http_get_status
from sources.base import LegacySlotSource, SourceContext, SourceResult

_LOG = logging.getLogger("pton")


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


def _norm_text(text: str | None) -> str:
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
            rec = dict(zip(headers, cells, strict=False))
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


def fetch_cstracker(steamid64: str) -> dict:
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


_STATUS_MAP = {"ok": "ok", "not_found": "not_found", "blocked": "blocked"}


class CsTrackerSource(LegacySlotSource):
    id = "cstracker"
    label = "CSTracker"
    slot = ("extra_stats", "cstracker")
    recheckable = True
    in_coverage = True
    payload_key = None  # rides inside the AI "extra_stats" block, not a block of its own

    def fetch_raw(self, steamid64: str, ctx: SourceContext) -> dict | None:
        return fetch_cstracker(steamid64)

    def normalize(self, raw: Any) -> SourceResult:
        if not raw:  # None or {} : the fetch was skipped (no SteamID64) or nothing stored
            return SourceResult("unavailable", {})
        if not isinstance(raw, dict):
            return SourceResult("error", {})
        raw_status = raw.get("status")
        status = _STATUS_MAP.get(raw_status, "error") if isinstance(raw_status, str) else "error"
        return SourceResult(status, raw)

    def ai_compact(self, result: SourceResult) -> dict | None:
        # ai_analyzer today sends the whole extra_stats dict; this is the cstracker part of it.
        return result.data or None
