"""Local web server + screenshot OCR scanner for CS2 scoreboard analysis."""

from __future__ import annotations

import argparse
import json
import queue
import shutil
import threading
import time
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

import storage.player_db as player_db
from lookup.ai_analyzer import analyze_players_with_ai
from storage.atomic_io import atomic_write_text
from storage.config_store import get_public_config, load_config, save_config
from app.console_parser import (
    correct_nick_from_console,
    find_cs2_game_dir,
    get_console_log_path,
    parse_latest_status,
    setup_autoexec,
)
from cs2platform.capture import SCREENSHOT_DIR, capture_screenshot, get_screenshot_dir
from cs2platform.focus import is_cs2_active as _is_cs2_active
from cs2platform.notify import send_notification
from app.events import EventBus
from app.pipeline import ScanPipeline
from app.web_router import (
    JsonBodyError,
    Router,
    check_request,
    read_json_body,
    resolve_static_file,
    static_content_type,
)
from sources import REGISTRY, SourceContext
from storage.config_schema import describe_schema
from storage.feedback_store import append_feedback, build_feedback_entry, validate_feedback_payload
from vision.scoreboard_scan import scan as scan_scoreboard
from lookup.steam_lookup import (
    _get_faceit_api_key,
    _get_steam_api_key,
    enrich_players,
    get_steam_search_status,
    recheck_player_report,
    reset_faceit_api_key_cache,
    reset_steam_api_key_cache,
)

from storage.paths import DATA_DIR, WEB_DIR

HISTORY_FILE = DATA_DIR / "scans_history.json"

_STATE_LOCK = threading.Lock()
_SCANS: list[dict] = []
_MAX_HISTORY = 100
_SUBSCRIBERS: list[queue.Queue[str]] = []
_TAB_LOCK = threading.Lock()
_LAST_TAB_TS = 0.0
_TAB_COOLDOWN_SEC = 4.0
_CS2_ONLY = True
_TAB_DELAY_SEC = 0.5
_NOTIFY_ENABLED = True


def _apply_runtime_config() -> dict:
    global _TAB_DELAY_SEC, _TAB_COOLDOWN_SEC, _CS2_ONLY, _NOTIFY_ENABLED
    cfg = load_config()
    _TAB_DELAY_SEC = float(cfg.get("tab_delay_sec", 0.5))
    _TAB_COOLDOWN_SEC = float(cfg.get("tab_cooldown_sec", 4.0))
    _CS2_ONLY = bool(cfg.get("cs2_only", True))
    _NOTIFY_ENABLED = bool(cfg.get("notify_enabled", True))
    return cfg


def init() -> None:
    """Load runtime settings from config.json. Called explicitly by main() and watch_cs2.main()."""
    _apply_runtime_config()


def configure_tab_watcher(delay_sec: float = 0.5, cooldown_sec: float = 4.0, cs2_only: bool = True) -> None:
    global _TAB_DELAY_SEC, _TAB_COOLDOWN_SEC, _CS2_ONLY
    _TAB_DELAY_SEC = delay_sec
    _TAB_COOLDOWN_SEC = cooldown_sec
    _CS2_ONLY = cs2_only


def _broadcast_event(event: str, payload: dict) -> None:
    msg = f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
    with _STATE_LOCK:
        dead: list[queue.Queue[str]] = []
        for q in _SUBSCRIBERS:
            try:
                q.put_nowait(msg)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _SUBSCRIBERS.remove(q)


def _adapt_legacy_scan(scan: dict) -> None:
    """In memory only: give player records written before `steam.sources` existed their uniform view."""
    for player in scan.get("players") or []:
        steam = player.get("steam") if isinstance(player, dict) else None
        if isinstance(steam, dict) and "sources" not in steam:
            steam["sources"] = REGISTRY.sources_view(steam)
    for idx, player in enumerate(scan.get("players") or []):
        if isinstance(player, dict) and not player.get("player_uid"):
            player["player_uid"] = f"{scan.get('id')}-{idx}"  # legacy record: assign once, persisted on next save


def _load_history() -> None:
    if not HISTORY_FILE.is_file():
        return
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            scans = data[:_MAX_HISTORY]
            for scan in scans:
                if isinstance(scan, dict):
                    _adapt_legacy_scan(scan)
            with _STATE_LOCK:
                _SCANS[:] = scans
    except Exception:
        pass


def _save_history_locked() -> None:
    try:
        atomic_write_text(HISTORY_FILE, json.dumps(_SCANS, ensure_ascii=False))
    except Exception:
        pass


def _resolve_match_meta(now_dt: datetime, console_status) -> tuple[str, Optional[str]]:
    if console_status and console_status.match_id:
        return console_status.match_id, console_status.map_name
    date_str = now_dt.strftime("%Y-%m-%d")
    with _STATE_LOCK:
        if _SCANS:
            prev = _SCANS[0]
            try:
                prev_dt = datetime.fromisoformat(prev["created_at"])
                if prev.get("date", prev["created_at"][:10]) == date_str and (now_dt - prev_dt).total_seconds() <= 2400:
                    return prev.get("match_id") or f"session_{now_dt.strftime('%Y%m%d_%H%M')}", prev.get("map_name")
            except Exception:
                pass
    return f"session_{now_dt.strftime('%Y%m%d_%H%M')}", (console_status.map_name if console_status else None)


def _build_matches_locked() -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for s in _SCANS:
        mid = s.get("match_id") or f"session_{s['created_at'][:10]}"
        groups.setdefault(mid, []).append(s)

    matches: list[dict] = []
    for mid, scans in groups.items():
        # scans are newest-first
        newest = scans[0]
        oldest = scans[-1]
        try:
            dur = max(0, round((datetime.fromisoformat(newest["created_at"]) - datetime.fromisoformat(oldest["created_at"])).total_seconds()))
        except Exception:
            dur = 0
        max_risk = max(
            (p.get("cheat_probability", 0) for s in scans for p in s.get("players", [])),
            default=0.0,
        )
        map_name = next((s.get("map_name") for s in scans if s.get("map_name")), None)
        matches.append({
            "match_id": mid,
            "map_name": map_name,
            "date": newest.get("date") or newest["created_at"][:10],
            "started_at": oldest["created_at"],
            "ended_at": newest["created_at"],
            "duration_sec": dur,
            "scan_count": len(scans),
            "max_risk": round(max_risk, 1),
            "scans": [
                {
                    "id": s["id"],
                    "created_at": s["created_at"],
                    "player_count": s["player_count"],
                    "image_name": s["image_name"],
                }
                for s in scans
            ],
        })
    return matches


class _ServerRepo:
    """ScanRepo over this module's globals. Names are resolved at call time so tests can patch them."""

    @property
    def lock(self):
        return _STATE_LOCK

    @property
    def scans(self) -> list:
        return _SCANS

    @property
    def max_history(self) -> int:
        return _MAX_HISTORY

    def save_locked(self) -> None:
        _save_history_locked()


def _ocr_image(image_path: Path) -> tuple[list, object]:
    console_status = parse_latest_status()
    players = scan_scoreboard(str(image_path))
    if console_status and console_status.nicks:
        for p in players:
            p.nick = correct_nick_from_console(p.nick, console_status.nicks)
    return players, console_status


def _screenshot_url(image_path: Path) -> Optional[str]:
    if image_path.parent.resolve() == get_screenshot_dir().resolve():
        return f"/screenshots/{image_path.name}"
    return None


EVENTS = EventBus()
_WIRE_EVENT = {
    "scan.created": "scan",
    "scan.updated": "scan",
    "scan.finished": "scan",
    "player.enriched": "scan_update",
}


def _sink_player_db(topic: str, payload: dict) -> None:
    try:
        player_db.record_scan_sightings(payload)
    except Exception as exc:
        print(f"[player-db error] sightings not saved for scan {payload.get('id')}: {exc}")


def _sink_sse(topic: str, payload: dict) -> None:
    # Resolved at call time: tests patch server._broadcast_event.
    _broadcast_event(_WIRE_EVENT[topic], payload)


EVENTS.subscribe("scan.finished", _sink_player_db)  # before SSE, as before the refactor
for _topic in _WIRE_EVENT:
    EVENTS.subscribe(_topic, _sink_sse)

PIPELINE = ScanPipeline(
    _ServerRepo(),
    EVENTS,
    ocr=lambda path: _ocr_image(path),
    enrich=lambda *a, **k: enrich_players(*a, **k),
    analyze_ai=lambda dicts: analyze_players_with_ai(dicts),
    ai_enabled=lambda: bool(load_config().get("ai_enabled")),
    match_meta=lambda now, status: _resolve_match_meta(now, status),
    screenshot_url=lambda path: _screenshot_url(path),
)


def _enrich_scan_async(scan_id: str, players: list, started: float) -> None:
    PIPELINE.enrich_scan(scan_id, players, started)


def run_scan(image_path: Optional[Path] = None, fetch_steam: bool = True) -> dict:
    if image_path is None:
        image_path = capture_screenshot()
    else:
        image_path = Path(image_path).resolve()
        if not image_path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")
    return PIPELINE.run_new(image_path, fetch_steam=fetch_steam)


def _send_notification(title: str, msg: str) -> None:
    if not _NOTIFY_ENABLED:
        return
    send_notification(title, msg)


def schedule_tab_scan() -> dict:
    if _CS2_ONLY and not _is_cs2_active():
        return {"status": "ignored", "reason": "cs2_not_focused"}
    now = time.time()
    with _TAB_LOCK:
        global _LAST_TAB_TS
        if now - _LAST_TAB_TS < _TAB_COOLDOWN_SEC:
            return {"status": "debounced"}
        _LAST_TAB_TS = now
    threading.Thread(target=_delayed_tab_scan, daemon=True).start()
    return {"status": "scheduled", "delay_sec": _TAB_DELAY_SEC}


def _delayed_tab_scan() -> None:
    time.sleep(_TAB_DELAY_SEC)
    try:
        if _CS2_ONLY and not _is_cs2_active():
            return
        result = run_scan()
        players = result.get("players", [])
        if players:
            top = players[0]
            msg = f"Знайдено {len(players)} гравців. Топ: {top['nick']} ({top['cheat_probability']}%)"
        else:
            msg = "Гравців не знайдено на скріншоті"
        _send_notification("CS2 Scoreboard Watch", msg)
    except Exception as exc:
        print(f"[tab-scan error] {exc}")


def _source_choices_text(ids: list[str]) -> str:
    """'a' або 'b' for two ids; 'a', 'b' або 'c' for more (matches the historical 400 text)."""
    quoted = [f"'{i}'" for i in ids]
    if len(quoted) <= 2:
        return " або ".join(quoted)
    return ", ".join(quoted[:-1]) + " або " + quoted[-1]


def _refresh_source_entry(steam: dict, src) -> None:
    """Update steam["sources"] for ONE source after its raw data was rewritten. Other entries and coverage stay as they are."""
    if "sources" not in steam:
        steam["sources"] = REGISTRY.sources_view(steam)
        return
    view = dict(steam.get("sources") or {})
    res = src.result_from(steam)
    if res is None:
        view.pop(src.id, None)
    else:
        view[src.id] = res.to_dict()
    steam["sources"] = view


def _player_matches(p: dict, uid: Optional[str], nick: Optional[str]) -> bool:
    """Match a stored player by its stable player_uid when the caller sent one, else by nick (legacy API)."""
    if uid:
        return p.get("player_uid") == uid
    return p.get("nick") == nick


def recheck_source_for_player(
    scan_id: Optional[str], nick: str, source: Optional[str], player_uid: Optional[str] = None
) -> tuple[int, dict]:
    """Re-fetch ONE recheckable external source for one player of a scan.

    Other sources and the scoring are left untouched. Returns (http_status, body):
    the updated scan on success, {"error": ...} otherwise.
    """
    # `source` comes from JSON, so only strings can be registry keys.
    src = REGISTRY.get(source) if isinstance(source, str) else None
    if src is None or not src.recheckable:
        return 400, {"error": f"source має бути {_source_choices_text(REGISTRY.recheckable_ids())}"}
    if not scan_id or not (nick or player_uid):
        return 400, {"error": "scan_id і nick є обов'язковими"}

    with _STATE_LOCK:
        scan_rec = next((s for s in _SCANS if s["id"] == scan_id), None)
        if not scan_rec:
            return 404, {"error": "Scan not found"}
        player = next((p for p in scan_rec.get("players", []) if _player_matches(p, player_uid, nick)), None)
        if player is None:
            return 404, {"error": "Player not found in scan"}
        steamid64 = (player.get("steam") or {}).get("steamid64")
    if not steamid64:
        return 409, {"error": "У гравця немає SteamID64: спочатку виправте нік або вставте Steam URL"}

    try:
        fresh = src.fetch_raw(steamid64, SourceContext())
    except Exception as exc:
        return 502, {"error": f"Запит до {source} не вдався: {exc}"}

    with _STATE_LOCK:
        scan_rec = next((s for s in _SCANS if s["id"] == scan_id), None)
        player = next((p for p in scan_rec.get("players", []) if _player_matches(p, player_uid, nick)), None) if scan_rec else None
        if scan_rec is None or player is None:
            return 404, {"error": "Scan or player was removed while refreshing"}
        steam = dict(player.get("steam") or {})
        src.write_raw(steam, fresh)  # copies the containers along the slot path; siblings are kept
        _refresh_source_entry(steam, src)
        player["steam"] = steam
        _save_history_locked()
        snapshot = dict(scan_rec)

    _broadcast_event("scan", snapshot)
    return 200, snapshot


# ---------------------------------------------------------------- HTTP routes
# Each route receives the CsWatchHandler instance `h` and the captured path params.
# Path params are the RAW (undecoded) URL segments, as the handler always received them.

router = Router()


@router.route("GET", "/api/steam-status")
def _get_steam_status(h: CsWatchHandler) -> None:
    h._send_json(get_steam_search_status())


@router.route("GET", "/api/latest")
def _get_latest(h: CsWatchHandler) -> None:
    with _STATE_LOCK:
        latest = _SCANS[0] if _SCANS else None
        matches = _build_matches_locked()
        history = [
            {
                "id": s["id"],
                "created_at": s["created_at"],
                "date": s.get("date") or s["created_at"][:10],
                "match_id": s.get("match_id"),
                "map_name": s.get("map_name"),
                "player_count": s["player_count"],
                "image_name": s["image_name"],
            }
            for s in _SCANS
        ]
    h._send_json({"latest": latest, "history": history, "matches": matches})


@router.route("GET", "/api/config")
def _get_config(h: CsWatchHandler) -> None:
    cs2_dir = find_cs2_game_dir()
    resolved = {
        "cs2_game_dir": str(cs2_dir) if cs2_dir else "",
        "console_log_path": str(get_console_log_path()),
        "screenshots_dir": str(get_screenshot_dir()),
        "claude_cli_available": bool(shutil.which("claude")),
    }
    h._send_json(
        get_public_config(
            resolved=resolved,
            env_key_present=bool(_get_steam_api_key()),
            env_faceit_key_present=bool(_get_faceit_api_key()),
        )
    )


@router.route("GET", "/api/config/schema")
def _get_config_schema(h: CsWatchHandler) -> None:
    # Descriptors only (names, types, limits, defaults, env var names). Never any stored value.
    h._send_json(describe_schema())


@router.route("GET", "/api/matches")
def _get_matches(h: CsWatchHandler) -> None:
    with _STATE_LOCK:
        matches = _build_matches_locked()
    h._send_json({"matches": matches})


@router.route("GET", "/api/scan/{scan_id:path}")
def _get_scan(h: CsWatchHandler, scan_id: str) -> None:
    scan_id = scan_id.strip("/")
    with _STATE_LOCK:
        match = next((s for s in _SCANS if s["id"] == scan_id), None)
    if not match:
        h._send_json({"error": "Scan not found"}, status=404)
    else:
        h._send_json(match)


@router.route("GET", "/api/players/{steamid:path}")
def _get_player(h: CsWatchHandler, steamid: str) -> None:
    steamid = steamid.strip("/")
    if not player_db.is_valid_steamid64(steamid):
        h._send_json({"error": "Invalid steamid64"}, status=400)
        return
    try:
        limit = min(max(int(parse_qs(urlparse(h.path).query).get("limit", ["50"])[0]), 1), 500)
    except ValueError:
        h._send_json({"error": "limit must be an integer"}, status=400)
        return
    try:
        profile = player_db.get_db().get_player(steamid, sightings_limit=limit)
    except Exception as exc:
        h._send_json({"error": str(exc)}, status=500)
        return
    if profile is None:
        h._send_json({"error": "Player not found"}, status=404)
    else:
        h._send_json(profile)


@router.route("GET", "/screenshots/{name:path}")
def _get_screenshot(h: CsWatchHandler, name: str) -> None:
    filename = Path(name).name
    sdir = get_screenshot_dir().resolve()
    target = (sdir / filename).resolve()
    if target.parent != sdir or not target.is_file():
        target = (SCREENSHOT_DIR.resolve() / filename).resolve()
        if target.parent != SCREENSHOT_DIR.resolve() or not target.is_file():
            h.send_error(HTTPStatus.NOT_FOUND)
            return
    ctype = "image/jpeg" if target.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    h._send_file(target, ctype)


@router.route("GET", "/api/events")
def _get_events(h: CsWatchHandler) -> None:
    # Long-lived SSE stream: writes straight to the socket, so it must not be wrapped.
    h.send_response(HTTPStatus.OK)
    h.send_header("Content-Type", "text/event-stream")
    h.send_header("Cache-Control", "no-cache")
    h.send_header("Connection", "keep-alive")
    h.end_headers()

    q: queue.Queue[str] = queue.Queue(maxsize=20)
    with _STATE_LOCK:
        _SUBSCRIBERS.append(q)
    try:
        h.wfile.write(b": connected\n\n")
        h.wfile.flush()
        while True:
            try:
                msg = q.get(timeout=20)
                h.wfile.write(msg.encode("utf-8"))
                h.wfile.flush()
            except queue.Empty:
                h.wfile.write(b": ping\n\n")
                h.wfile.flush()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        with _STATE_LOCK:
            if q in _SUBSCRIBERS:
                _SUBSCRIBERS.remove(q)


@router.fallback("GET")
def _get_static(h: CsWatchHandler, path: str) -> bool:
    target = resolve_static_file(WEB_DIR, path)
    if target is None:
        return False
    h._send_file(target, static_content_type(target))
    return True


@router.route("POST", "/api/tab-pressed")
def _post_tab_pressed(h: CsWatchHandler) -> None:
    h._send_json(schedule_tab_scan())


@router.route("POST", "/api/scan")
def _post_scan(h: CsWatchHandler) -> None:
    try:
        payload = read_json_body(h)
        img_path = payload.get("image_path")
        fetch_steam = payload.get("fetch_steam", True)
        result = run_scan(
            image_path=Path(img_path) if img_path else None,
            fetch_steam=bool(fetch_steam),
        )
        h._send_json(result)
    except Exception as exc:
        h._send_json({"error": str(exc)}, status=500)


@router.route("POST", "/api/rescan")
def _post_rescan(h: CsWatchHandler) -> None:
    try:
        payload = read_json_body(h)
        scan_id = payload.get("scan_id")
        with _STATE_LOCK:
            scan_rec = next((s for s in _SCANS if s["id"] == scan_id), None) if scan_id else (_SCANS[0] if _SCANS else None)
            if not scan_rec:
                h._send_json({"error": "Scan not found"}, status=404)
                return
        img_name = scan_rec.get("image_name")
        sdir = get_screenshot_dir().resolve()
        img_path = (sdir / Path(img_name).name).resolve()
        if not img_path.is_file():
            img_path = (SCREENSHOT_DIR.resolve() / Path(img_name).name).resolve()
        if not img_path.is_file():
            h._send_json({"error": "Screenshot file not found"}, status=404)
            return

        snapshot = PIPELINE.rescan(scan_rec, img_path)
        h._send_json(snapshot)
    except Exception as exc:
        h._send_json({"error": str(exc)}, status=500)


@router.route("POST", "/api/setup-cs2")
def _post_setup_cs2(h: CsWatchHandler) -> None:
    try:
        h._send_json(setup_autoexec())
    except Exception as exc:
        h._send_json({"error": str(exc)}, status=500)


@router.route("POST", "/api/recheck")
def _post_recheck(h: CsWatchHandler) -> None:
    try:
        payload = read_json_body(h)
        scan_id = payload.get("scan_id")
        old_nick = payload.get("old_nick")
        player_uid = payload.get("player_uid") or None
        query = (payload.get("query") or "").strip()
        if not scan_id or not (old_nick or player_uid) or not query:
            h._send_json({"error": "scan_id, old_nick, and query are required"}, status=400)
            return

        with _STATE_LOCK:
            scan_rec = next((s for s in _SCANS if s["id"] == scan_id), None)
            if not scan_rec:
                h._send_json({"error": "Scan not found"}, status=404)
                return
            idx = next((i for i, p in enumerate(scan_rec["players"]) if _player_matches(p, player_uid, old_nick)), None)
            if idx is None:
                h._send_json({"error": "Player not found in scan"}, status=404)
                return
            player_copy = dict(scan_rec["players"][idx])

        updated_player = recheck_player_report(player_copy, query)
        analyzed_list, _ai_err = analyze_players_with_ai([updated_player])
        updated_player = analyzed_list[0]

        with _STATE_LOCK:
            scan_rec["players"][idx] = updated_player
            scan_rec["players"].sort(key=lambda r: r.get("cheat_probability", 0), reverse=True)
            _save_history_locked()
            snapshot = dict(scan_rec)

        h._send_json(snapshot)
    except Exception as exc:
        h._send_json({"error": str(exc)}, status=500)


@router.route("POST", "/api/feedback")
def _post_feedback(h: CsWatchHandler) -> None:
    try:
        try:
            payload = read_json_body(h, empty_json=lambda: None)
        except JsonBodyError:
            payload = None  # validate_feedback_payload reports the 400
        clean, err = validate_feedback_payload(payload)
        if err:
            h._send_json({"error": err}, status=400)
            return
        with _STATE_LOCK:
            scan_rec = next((s for s in _SCANS if s["id"] == clean["scan_id"]), None)
            if not scan_rec:
                h._send_json({"error": "Scan not found"}, status=404)
                return
            player = next(
                (dict(p) for p in scan_rec["players"] if _player_matches(p, payload.get("player_uid") or None, clean["nick"])),
                None,
            )
        if player is None:
            h._send_json({"error": "Player not found in scan"}, status=404)
            return
        entry = build_feedback_entry(clean["scan_id"], clean["label"], player)
        append_feedback(entry)
        h._send_json({"ok": True, "entry": entry})
    except Exception as exc:
        h._send_json({"error": str(exc)}, status=500)


@router.route("POST", "/api/recheck-source")
def _post_recheck_source(h: CsWatchHandler) -> None:
    try:
        try:
            payload = read_json_body(h)
        except JsonBodyError:
            h._send_json({"error": "Тіло запиту має бути JSON"}, status=400)
            return
        if not isinstance(payload, dict):
            h._send_json({"error": "Тіло запиту має бути JSON-об'єктом"}, status=400)
            return
        status, body = recheck_source_for_player(
            payload.get("scan_id"),
            str(payload.get("nick") or "").strip(),
            payload.get("source"),
            player_uid=payload.get("player_uid") or None,
        )
        h._send_json(body, status=status)
    except Exception as exc:
        h._send_json({"error": str(exc)}, status=500)


@router.route("POST", "/api/ai-analyze")
def _post_ai_analyze(h: CsWatchHandler) -> None:
    try:
        payload = read_json_body(h)
        scan_id = payload.get("scan_id")
        target_nick = (payload.get("nick") or "").strip()
        target_uid = payload.get("player_uid") or None
        with _STATE_LOCK:
            scan_rec = next((s for s in _SCANS if s["id"] == scan_id), None) if scan_id else (_SCANS[0] if _SCANS else None)
            if not scan_rec:
                h._send_json({"error": "Scan not found"}, status=404)
                return
            players_copy = [dict(p) for p in scan_rec.get("players", [])]

        if target_nick or target_uid:
            subset = [p for p in players_copy if _player_matches(p, target_uid, target_nick)]
            if not subset:
                h._send_json({"error": "Player not found in scan"}, status=404)
                return
            analyzed, ai_error = analyze_players_with_ai(subset, force=True)
            if ai_error:
                h._send_json({"error": ai_error}, status=500)
                return
            key = (lambda p: p.get("player_uid")) if target_uid else (lambda p: p["nick"])
            updated_map = {key(p): p for p in analyzed}
            new_players = [updated_map.get(key(p), p) for p in players_copy]
        else:
            new_players, ai_error = analyze_players_with_ai(players_copy, force=True)
            if ai_error:
                h._send_json({"error": ai_error}, status=500)
                return

        with _STATE_LOCK:
            scan_rec["players"] = new_players
            scan_rec["ai_error"] = None
            _save_history_locked()
            snapshot = dict(scan_rec)

        h._send_json(snapshot)
    except Exception as exc:
        h._send_json({"error": str(exc)}, status=500)


@router.route("POST", "/api/config")
def _post_config(h: CsWatchHandler) -> None:
    try:
        payload = read_json_body(h)
        if not isinstance(payload, dict):
            h._send_json({"error": "Invalid config payload"}, status=400)
            return
        save_config(payload)
        _apply_runtime_config()
        reset_steam_api_key_cache()
        reset_faceit_api_key_cache()
        cs2_dir = find_cs2_game_dir()
        resolved = {
            "cs2_game_dir": str(cs2_dir) if cs2_dir else "",
            "console_log_path": str(get_console_log_path()),
            "screenshots_dir": str(get_screenshot_dir()),
            "claude_cli_available": bool(shutil.which("claude")),
        }
        h._send_json(
            get_public_config(
                resolved=resolved,
                env_key_present=bool(_get_steam_api_key()),
                env_faceit_key_present=bool(_get_faceit_api_key()),
            )
        )
    except Exception as exc:
        h._send_json({"error": str(exc)}, status=500)


@router.route("POST", "/api/upload")
def _post_upload(h: CsWatchHandler) -> None:
    try:
        raw = h._read_body()
        if not raw:
            h._send_json({"error": "Empty upload"}, status=400)
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        out_path = get_screenshot_dir() / f"upload_{ts}.png"
        out_path.write_bytes(raw)
        result = run_scan(image_path=out_path, fetch_steam=True)
        h._send_json(result)
    except Exception as exc:
        h._send_json({"error": str(exc)}, status=500)


@router.route("POST", "/api/players/{steamid:path}")
def _post_player(h: CsWatchHandler, steamid: str) -> None:
    steamid = steamid.strip("/")
    if not player_db.is_valid_steamid64(steamid):
        h._send_json({"error": "Invalid steamid64"}, status=400)
        return
    try:
        payload = read_json_body(h)
        if not isinstance(payload, dict):
            h._send_json({"error": "Invalid player payload"}, status=400)
            return
        fields: dict[str, Optional[str]] = {}
        for name, max_len in (("tag", player_db.TAG_MAX_LEN), ("notes", player_db.NOTES_MAX_LEN)):
            if name in payload:
                value = payload[name]
                if value is not None and not isinstance(value, str):
                    h._send_json({"error": f"{name} must be a string"}, status=400)
                    return
                if value and len(value) > max_len:
                    h._send_json({"error": f"{name} is too long (max {max_len})"}, status=400)
                    return
                fields[name] = value or None
        db = player_db.get_db()
        if not db.set_player_meta(steamid, **fields):
            h._send_json({"error": "Player not found"}, status=404)
            return
        h._send_json(db.get_player(steamid, sightings_limit=50))
    except Exception as exc:
        h._send_json({"error": str(exc)}, status=500)


class CsWatchHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args) -> None:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {self.address_string()} - {format % args}")

    def _send_json(self, data: dict | list, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self, max_bytes: int = 25 * 1024 * 1024) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return b""
        if length > max_bytes:
            raise ValueError("Payload too large")
        return self.rfile.read(length)

    def _guard(self, method: str) -> bool:
        """Reject DNS-rebinding and cross-site requests before routing. False means a response was sent."""
        try:
            body_length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            body_length = 0
        rejection = check_request(method, urlparse(self.path).path, self.headers, body_length)
        if rejection is None:
            return True
        status, message = rejection
        # A rejected request may leave its body unread; do not let it bleed into the next keep-alive request.
        self.close_connection = True
        self._send_json({"error": message}, status=status)
        return False

    def do_GET(self) -> None:
        if not self._guard("GET"):
            return
        path = urlparse(self.path).path
        if not router.dispatch(self, "GET", path):
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if not self._guard("POST"):
            return
        path = urlparse(self.path).path
        if not router.dispatch(self, "POST", path):
            self.send_error(HTTPStatus.NOT_FOUND)



def main() -> None:
    parser = argparse.ArgumentParser(description="CS2 Scoreboard Scanner Web Server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--delay", type=float, default=0.5, help="Seconds to wait after TAB before screenshot")
    parser.add_argument("--cooldown", type=float, default=4.0, help="Minimum seconds between TAB scans")
    parser.add_argument("--all-windows", action="store_true", help="Trigger TAB scan even when CS2 is not focused")
    args = parser.parse_args()

    init()
    configure_tab_watcher(delay_sec=args.delay, cooldown_sec=args.cooldown, cs2_only=not args.all_windows)
    _load_history()
    httpd = ThreadingHTTPServer((args.host, args.port), CsWatchHandler)
    print(f"CS2 Anti-Cheat Scanner running at http://{args.host}:{args.port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        httpd.server_close()


if __name__ == "__main__":
    main()
