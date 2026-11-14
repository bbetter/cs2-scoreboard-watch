#!/usr/bin/env python3
"""CS2 player telemetry hook used by steam_lookup.py, plus a standalone scraper CLI.

fetch_player_telemetry(steamid64) is called by steam_lookup.py. It returns a
multi-source telemetry dict (profile, steam, leetify, faceit, cstracker, scope_gg,
medals, matches) that external_parser.parse_multi_source_payload understands,
or None when unconfigured.

Configuration: set CSST_PROFILE_URL to a URL template containing {steamid64}, e.g.
    CSST_PROFILE_URL='https://csst.at/p/{steamid64}'
While it is unset, nothing is fetched and the hook returns None.

Standalone CLI (this was magic-legal.py):
    python api_fetcher.py https://csst.at/p/<name> -o profile.json
    python api_fetcher.py https://csst.at/p/<name> -o profile.json --headless
    python api_fetcher.py https://csst.at/p/<name> --state auth_state.json
    python api_fetcher.py https://your-server/test.html --no-browser -o profile.json
    python api_fetcher.py --from-html saved.html -o profile.json   # no network, no browser

Numeric values are {"value": float|None, "unit": str|None, "raw": str}.
"value" is None when the cell is not a single number (e.g. "1.00–1.28s", "5.6 / 0.1").
Sections still showing skeleton placeholders get "status": "pending".

Requires: playwright (with chromium installed) and beautifulsoup4.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

from bs4 import BeautifulSoup, Tag

PROFILE_URL_ENV = "CSST_PROFILE_URL"


def fetch_player_telemetry(steamid64: str) -> Optional[dict[str, Any]]:
    """Return a multi-source telemetry dict for the given SteamID64, or None when unconfigured."""
    template = os.environ.get(PROFILE_URL_ENV, "").strip()
    if not template or not steamid64:
        return None
    url = template.format(steamid64=steamid64)
    return parse_profile_html(fetch_rendered_html(url, headless=True))


# ============================================================== step 1: Playwright

SKELETON_SEL = ".data-skeleton, .provider-skeleton, .matches-skeleton"

# Visible skeletons only. Ignore the inventory (needs the Steam data-source preference)
# and the FACEIT game-select indicator, which never clear in a plain session.
SETTLED_JS = f"""() => [...document.querySelectorAll('{SKELETON_SEL}')]
    .filter(el => el.offsetParent !== null && !el.closest('.inventory, .htmx-indicator'))
    .length === 0"""


def fetch_rendered_html(
    url: str,
    *,
    storage_state: Optional[str] = None,
    headless: bool = False,
    timeout_s: float = 30.0,
) -> str:
    from playwright.sync_api import TimeoutError as PWTimeout
    from playwright.sync_api import sync_playwright

    timeout_ms = int(timeout_s * 1000)

    def settle(page, label: str) -> None:
        try:
            page.wait_for_function(SETTLED_JS, timeout=timeout_ms)
        except PWTimeout:
            print(f"warning: {label}: some sections still loading after {timeout_s}s", file=sys.stderr)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            viewport={"width": 1400, "height": 900},
            storage_state=storage_state,
        )
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_selector("[data-viewed-player]", state="attached", timeout=timeout_ms)
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
        settle(page, "initial load")

        # Match lists load when their tab is shown (hx-trigger="intersect once, change").
        tabs = page.locator('[role="tab"][data-match-source]')
        for i in range(tabs.count()):
            source = tabs.nth(i).get_attribute("data-match-source")
            tabs.nth(i).click()
            settle(page, f"{source} matches")

        # Scroll to the bottom so intersect-triggered blocks (Allstar, etc.) load.
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(500)
        settle(page, "after scroll")

        html = page.content()
        context.close()
        browser.close()
    return html


# ============================================================== step 2: parser

NUM_RE = re.compile(r"^(-?\d+(?:\.\d+)?)\s*(%|°|ms|s|/100|/30r|x)?$")


def norm(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", norm(text).lower()).strip("_")


def metric(raw: str) -> dict[str, Any]:
    raw = norm(raw)
    m = NUM_RE.match(raw)
    return {
        "value": float(m.group(1)) if m else None,
        "unit": (m.group(2) or None) if m else None,
        "raw": raw,
    }


def is_pending(el: Optional[Tag]) -> bool:
    return el is not None and el.select_one(SKELETON_SEL) is not None


def status_of(el: Optional[Tag]) -> str:
    if el is None:
        return "missing"
    return "pending" if is_pending(el) else "loaded"


def text_of(el: Optional[Tag]) -> Optional[str]:
    return (norm(el.get_text()) or None) if el is not None else None


def parse_profile(soup: BeautifulSoup) -> dict[str, Any]:
    body = soup.select_one("[data-viewed-player]")
    steamid = body.get("data-id") if body else None
    xp_el = soup.select_one(f"#xp-level-{steamid}") if steamid else None
    xp_text = xp_el.select_one(".tooltip") if xp_el else None
    return {
        "name": text_of(soup.select_one("h1.identity-card__name")),
        "steamid64": steamid,
        "xp_level": metric(xp_text.get_text()) if xp_text else None,
        "premier_ranks_status": status_of(soup.select_one("[data-identity-ranks]")),
        "title": text_of(soup.select_one("title")),
    }


def parse_steam(soup: BeautifulSoup) -> dict[str, Any]:
    slot = soup.select_one('[data-slot="steam"]')
    if slot is None:
        return {"status": "missing"}
    result: dict[str, Any] = {"status": "loaded"}  # the block itself; inventory tracked below
    block = slot.select_one(".steam[data-steamid64]")
    result["steamid64"] = block.get("data-steamid64") if block else None
    link = slot.select_one('a[href*="steamcommunity.com"]')
    result["profile_url"] = link.get("href") if link else None

    for label_el in slot.select("p.text-xs.uppercase"):
        label = slug(label_el.get_text())
        value_el = label_el.find_next_sibling()
        if value_el is None or label in result:
            continue
        if label == "registered":
            t = value_el.find("time")
            result[label] = t.get("datetime") if t else text_of(value_el)
        elif label == "country":
            img = value_el.find("img", alt=True)
            result[label] = img["alt"] if img else text_of(value_el)
        elif label in ("cs_friendcode", "vanity"):
            result[label] = text_of(value_el)

    commend = soup.select_one(f"#commendations-{result['steamid64']}") if result.get("steamid64") else None
    if commend:
        result["commendations"] = {
            norm(t.get("data-tip")).lower(): metric(t.get_text())
            for t in commend.select(".tooltip[data-tip]")
        }

    inv = slot.select_one(".inventory")
    result["inventory_value"] = None if is_pending(inv) else text_of(inv)
    result["inventory_status"] = status_of(inv)
    return result


def parse_leetify(soup: BeautifulSoup) -> dict[str, Any]:
    section = soup.select_one('[id^="section-leetify-"]')
    if section is None:
        return {"status": "missing"}
    result: dict[str, Any] = {
        "status": status_of(section),
        "name": text_of(section.select_one('[id^="leetify-name-"]')),
    }
    for label_el in section.select("p.text-xs.uppercase"):
        value_el = label_el.find_next_sibling()
        if value_el is None:
            continue
        entry = metric(text_of(value_el) or "")
        tip_el = value_el if value_el.get("data-tip") else value_el.select_one("[data-tip]")
        if tip_el is not None:
            entry["details"] = norm(tip_el.get("data-tip"))
        result[slug(label_el.get_text())] = entry
    return result


def parse_faceit(soup: BeautifulSoup) -> dict[str, Any]:
    slot = soup.select_one('[data-slot="faceit"]')
    if slot is None:
        return {"status": "missing"}
    card = slot.select_one(".faceit")
    link = card.select_one("a[href*='faceit.com']") if card else None
    time_el = card.select_one("time") if card else None
    flag = card.select_one("img[src*='/flags/']") if card else None
    lang = card.select_one(".tooltip[data-tip='Language'] .text-white") if card else None
    return {
        "status": status_of(card),
        "nickname": text_of(link),
        "profile_url": link.get("href") if link else None,
        "registered": time_el.get("datetime") if time_el else None,
        "country": flag.get("alt") if flag else None,
        "language": text_of(lang),
    }


def parse_cstracker(soup: BeautifulSoup) -> dict[str, Any]:
    card = soup.select_one("section.cstracker-card")
    if card is None:
        return {"status": "missing"}
    brand = card.select_one("a.cstracker-brand")
    subtitle = card.select_one(".cstracker-tooltip-subtitle")
    m = re.search(r"(\d+)", text_of(subtitle) or "")
    result: dict[str, Any] = {
        "status": status_of(card),
        "profile_url": brand.get("href") if brand else None,
        "trust": metric(text_of(card.select_one(".cstracker-trust-value")) or ""),
        "trust_based_on_matches": int(m.group(1)) if m else None,
    }
    for stat in card.select(".cstracker-stat"):
        label = stat.select_one(".cstracker-label")
        value = stat.select_one(".cstracker-stat-value")
        if label and value:
            result[slug(label.get_text())] = metric(value.get_text())
    return result


def _previous(tip: str) -> dict[str, Any]:
    m = re.search(r"Previous \d+ matches:\s*(.+)$", norm(tip))
    return metric(m.group(1)) if m else metric(norm(tip))


def table_records(table: Tag) -> list[dict[str, Any]]:
    headers = [slug(th.get_text()) or f"col{i}" for i, th in enumerate(table.select("thead th"))]
    records = []
    for tr in table.select("tbody tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) < 2:
            continue
        records.append({
            h: (norm(cell.get_text()) if i == 0 else metric(cell.get_text()))
            for i, (h, cell) in enumerate(zip(headers, cells))
        })
    return records


def parse_scope(soup: BeautifulSoup) -> dict[str, Any]:
    card = soup.select_one("section.scope-card")
    if card is None:
        return {"status": "missing"}
    link = card.select_one(".scope-card__id a, .scope-card__title a, a.scope-card__id")
    role = text_of(card.select_one(".scope-card__role"))
    period = text_of(card.select_one(".scope-card__period")) or ""
    if role and period.startswith(role):
        period = period[len(role):].strip()

    headline: dict[str, Any] = {}
    for item in card.select("dl.scope-scores .scope-score"):
        label = text_of(item.select_one("dt"))
        dd = item.select_one("dd.scope-score__value")
        if not label or dd is None:
            continue
        entry = metric(text_of(dd.select_one("span.text-white")) or "")
        delta = dd.select_one(".scope-delta[data-tip]")
        if delta:
            entry["previous_13"] = _previous(delta["data-tip"])
        headline[slug(label)] = entry

    performance: dict[str, Any] = {}
    for stat in card.select(".scope-stat"):
        label = text_of(stat.select_one("dt"))
        value = stat.select_one("dd .text-white")
        if not label or value is None:
            continue
        entry = metric(value.get_text())
        delta = stat.select_one(".scope-delta[data-tip]")
        if delta:
            entry["previous_13"] = _previous(delta["data-tip"])
        performance[slug(label)] = entry

    result: dict[str, Any] = {
        "status": status_of(card),
        "nickname": text_of(link),
        "profile_url": link.get("href") if link else None,
        "role": role,
        "period": period or None,
        "headline": headline,
        "performance": performance,
    }
    for table in card.select("table.scope-table"):
        caption = (text_of(table.select_one("caption")) or "").lower()
        if caption.startswith("stats by side"):
            result["by_side"] = table_records(table)
        elif caption.startswith("aim by weapon"):
            result["aim_by_weapon"] = table_records(table)
    return result


def parse_medals(soup: BeautifulSoup) -> dict[str, Any]:
    box = soup.select_one("#medals")
    if box is None:
        return {"status": "missing", "items": []}
    items: list[str] = []
    for img in box.select("img[alt]"):
        if img["alt"] not in items:
            items.append(img["alt"])
    return {"status": "loaded", "items": items}


def parse_matches(soup: BeautifulSoup) -> dict[str, Any]:
    browser = soup.select_one("section[data-match-browser]")
    if browser is None:
        return {"status": "missing", "sources": {}}
    sources: dict[str, Any] = {}
    for panel in browser.select('[role="tabpanel"][data-match-source]'):
        source = panel["data-match-source"]
        table = panel.select_one("table")
        if table is None:
            sources[source] = {"status": status_of(panel), "matches": []}
            continue
        headers = [slug(th.get_text()) for th in table.select("thead th")]
        matches = []
        for tr in table.select("tbody tr"):
            cells = tr.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            rec: dict[str, Any] = {}
            for h, cell in zip(headers, cells):
                if h == "link":
                    a = cell.find("a", href=True)
                    rec["link"] = a["href"] if a else None
                elif h == "rank":
                    img = cell.find("img", src=True)
                    sg = re.search(r"skillgroup(\d+)", img["src"]) if img else None
                    arrow = cell.find(string=re.compile(r"[↑↓]"))
                    rec["rank"] = {
                        "skillgroup": int(sg.group(1)) if sg else None,
                        "trend": norm(arrow) or None,
                    }
                elif h in ("map", "score", "role", "finished_at", "played_on", "when"):
                    rec[h] = norm(cell.get_text())
                elif h in ("kda", "k_d_a"):
                    parts = norm(cell.get_text()).split("/")
                    nums = [int(p) if p.strip().isdigit() else None for p in parts]
                    nums += [None] * (3 - len(nums))
                    rec["kills"], rec["deaths"], rec["assists"] = nums[:3]
                else:
                    rec[h] = metric(cell.get_text())
            matches.append(rec)
        sources[source] = {"status": status_of(panel), "matches": matches}
    return {"status": "loaded", "sources": sources}


def parse_profile_html(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    return {
        "profile": parse_profile(soup),
        "steam": parse_steam(soup),
        "leetify": parse_leetify(soup),
        "faceit": parse_faceit(soup),
        "cstracker": parse_cstracker(soup),
        "scope_gg": parse_scope(soup),
        "medals": parse_medals(soup),
        "matches": parse_matches(soup),
        "allstar": {"status": status_of(soup.select_one('[data-slot="allstar"]'))},
    }


# ============================================================== CLI

def fetch_html_http(url: str, timeout_s: float = 30.0) -> str:
    """Plain HTTP GET, no browser. Only works if the HTML already contains the data
    (lazy HTMX sections will still be skeletons)."""
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "profile-scraper/1.0", "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def check_url(value: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise argparse.ArgumentTypeError(f"not an http(s) URL: {value!r}")
    return value


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("url", nargs="?", type=check_url, help="http(s) URL of the profile or saved HTML page")
    src.add_argument("--from-html", type=Path, help="parse an already saved HTML file (no network, no browser)")
    ap.add_argument("--no-browser", action="store_true",
                    help="download the URL with plain HTTP instead of Playwright (no JS, no lazy sections)")
    ap.add_argument("-o", "--output", type=Path, default=Path("profile.json"), help="JSON output path")
    ap.add_argument("--html-out", type=Path, help="also save the fetched HTML here")
    ap.add_argument("--state", help="Playwright storage_state JSON (cookies/localStorage) to reuse")
    ap.add_argument("--headless", action="store_true", help="run the browser headless (default: visible)")
    ap.add_argument("--timeout", type=float, default=30.0, help="max seconds to wait per stage")
    args = ap.parse_args()

    if args.from_html:
        html = args.from_html.read_text(encoding="utf-8")
    elif args.no_browser:
        html = fetch_html_http(args.url, timeout_s=args.timeout)
    else:
        html = fetch_rendered_html(
            args.url,
            storage_state=args.state,
            headless=args.headless,
            timeout_s=args.timeout,
        )
    if args.html_out and not args.from_html:
        args.html_out.write_text(html, encoding="utf-8")
        print(f"Saved HTML to {args.html_out}", file=sys.stderr)

    data = parse_profile_html(html)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved JSON to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
