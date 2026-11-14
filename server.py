"""Local web server + screenshot OCR scanner for CS2 scoreboard analysis."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from ai_analyzer import analyze_players_with_ai
from config_store import get_public_config, load_config, save_config
from console_parser import (
    correct_nick_from_console,
    find_cs2_game_dir,
    get_console_log_path,
    parse_latest_status,
    setup_autoexec,
)
from scoreboard_scan import scan as scan_scoreboard
from steam_lookup import (
    _get_faceit_api_key,
    _get_steam_api_key,
    enrich_players,
    recheck_player_report,
    reset_faceit_api_key_cache,
    reset_steam_api_key_cache,
)

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
SCREENSHOT_DIR = BASE_DIR / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)
HISTORY_FILE = BASE_DIR / "scans_history.json"

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


def get_screenshot_dir() -> Path:
    custom = str(load_config().get("screenshots_dir") or "").strip()
    if custom:
        p = Path(custom).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        return p
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    return SCREENSHOT_DIR


def _apply_runtime_config() -> dict:
    global _TAB_DELAY_SEC, _TAB_COOLDOWN_SEC, _CS2_ONLY, _NOTIFY_ENABLED
    cfg = load_config()
    _TAB_DELAY_SEC = float(cfg.get("tab_delay_sec", 0.5))
    _TAB_COOLDOWN_SEC = float(cfg.get("tab_cooldown_sec", 4.0))
    _CS2_ONLY = bool(cfg.get("cs2_only", True))
    _NOTIFY_ENABLED = bool(cfg.get("notify_enabled", True))
    return cfg


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


def _is_cs2_client(c: dict) -> bool:
    cls = (c.get("class") or "").strip().lower()
    init_cls = (c.get("initialClass") or "").strip().lower()
    return cls in {"cs2", "steam_app_730"} or init_cls in {"cs2", "steam_app_730"}


def _get_cs2_monitor_name() -> Optional[str]:
    if not shutil.which("hyprctl"):
        return None
    try:
        mon_res = subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True, text=True, timeout=1.5)
        if mon_res.returncode != 0 or not mon_res.stdout.strip():
            return None
        monitors = json.loads(mon_res.stdout)
        id_to_name = {m["id"]: m["name"] for m in monitors if "id" in m and "name" in m}

        cli_res = subprocess.run(["hyprctl", "clients", "-j"], capture_output=True, text=True, timeout=1.5)
        if cli_res.returncode == 0 and cli_res.stdout.strip():
            clients = json.loads(cli_res.stdout)
            for c in clients:
                if _is_cs2_client(c):
                    mon_id = c.get("monitor")
                    if mon_id in id_to_name:
                        return id_to_name[mon_id]

        # Fallback: currently focused monitor
        for m in monitors:
            if m.get("focused") and "name" in m:
                return m["name"]
    except Exception:
        return None
    return None


def _win32_enable_dpi_awareness() -> None:
    try:
        ctypes.windll.user32.SetProcessDPIAware()  # type: ignore[attr-defined]
    except Exception:
        pass


def _win32_get_window_title(hwnd: int) -> str:
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _win32_get_process_name(hwnd: int) -> str:
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    pid = ctypes.c_ulong(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        size = ctypes.c_ulong(512)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _win32_is_cs2_hwnd(hwnd: int) -> bool:
    if not hwnd:
        return False
    title = _win32_get_window_title(hwnd).strip().lower()
    if "counter-strike 2" in title or title == "cs2":
        return True
    return _win32_get_process_name(hwnd) == "cs2.exe"


def _win32_find_cs2_hwnd() -> int:
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    fg = user32.GetForegroundWindow()
    if _win32_is_cs2_hwnd(fg):
        return fg
    return int(user32.FindWindowW(None, "Counter-Strike 2") or 0)


def _win32_get_window_center(hwnd: int) -> Optional[tuple[int, int]]:
    if not hwnd:
        return None
    from ctypes import wintypes

    rect = wintypes.RECT()
    if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):  # type: ignore[attr-defined]
        return None
    return (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2


def _capture_with_mss_or_pil(out_path: Path, center: Optional[tuple[int, int]] = None) -> None:
    try:
        import mss
        import mss.tools

        with mss.mss() as sct:
            monitors = sct.monitors
            target = monitors[1] if len(monitors) > 1 else monitors[0]
            if center is not None:
                cx, cy = center
                for mon in monitors[1:]:
                    if mon["left"] <= cx < mon["left"] + mon["width"] and mon["top"] <= cy < mon["top"] + mon["height"]:
                        target = mon
                        break
            img = sct.grab(target)
            mss.tools.to_png(img.rgb, img.size, output=str(out_path))
            return
    except ImportError:
        pass

    from PIL import ImageGrab

    ImageGrab.grab().save(out_path)


def capture_screenshot() -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    out_path = get_screenshot_dir() / f"cs2_{ts}.png"
    tool = str(load_config().get("screenshot_tool") or "auto").strip().lower()

    if tool == "mss":
        _capture_with_mss_or_pil(out_path)
        if not out_path.exists():
            raise RuntimeError("Screenshot failed using mss.")
        return out_path
    if tool == "pil":
        from PIL import ImageGrab

        ImageGrab.grab().save(out_path)
        if not out_path.exists():
            raise RuntimeError("Screenshot failed using PIL ImageGrab.")
        return out_path
    if tool == "screencapture":
        cmd = ["screencapture", "-x", str(out_path)]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if res.returncode != 0 or not out_path.exists():
            raise RuntimeError(f"Screenshot failed ({' '.join(cmd)}): {res.stderr.strip()}")
        return out_path
    if tool == "grim":
        monitor_name = _get_cs2_monitor_name()
        cmd = ["grim", "-o", monitor_name, str(out_path)] if monitor_name else ["grim", str(out_path)]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if res.returncode != 0 or not out_path.exists():
            raise RuntimeError(f"Screenshot failed ({' '.join(cmd)}): {res.stderr.strip()}")
        return out_path
    if tool == "imagemagick":
        cmd = ["import", "-window", "root", str(out_path)]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if res.returncode != 0 or not out_path.exists():
            raise RuntimeError(f"Screenshot failed ({' '.join(cmd)}): {res.stderr.strip()}")
        return out_path

    if sys.platform == "win32":
        _win32_enable_dpi_awareness()
        hwnd = _win32_find_cs2_hwnd()
        center = _win32_get_window_center(hwnd) if hwnd else None
        _capture_with_mss_or_pil(out_path, center=center)
        if not out_path.exists():
            raise RuntimeError("Screenshot failed on Windows.")
        return out_path

    if sys.platform == "darwin":
        if shutil.which("screencapture"):
            cmd = ["screencapture", "-x", str(out_path)]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if res.returncode != 0 or not out_path.exists():
                raise RuntimeError(f"Screenshot failed ({' '.join(cmd)}): {res.stderr.strip()}")
            return out_path
        _capture_with_mss_or_pil(out_path)
        if not out_path.exists():
            raise RuntimeError("Screenshot failed on macOS.")
        return out_path

    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("grim"):
        monitor_name = _get_cs2_monitor_name()
        cmd = ["grim", "-o", monitor_name, str(out_path)] if monitor_name else ["grim", str(out_path)]
    elif shutil.which("import"):
        cmd = ["import", "-window", "root", str(out_path)]
    else:
        raise RuntimeError("No screenshot tool found (need 'grim' on Wayland or ImageMagick 'import').")

    res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if res.returncode != 0 or not out_path.exists():
        raise RuntimeError(f"Screenshot failed ({' '.join(cmd)}): {res.stderr.strip()}")
    return out_path


def _load_history() -> None:
    if not HISTORY_FILE.is_file():
        return
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            with _STATE_LOCK:
                _SCANS[:] = data[:_MAX_HISTORY]
    except Exception:
        pass


def _save_history_locked() -> None:
    try:
        HISTORY_FILE.write_text(json.dumps(_SCANS, ensure_ascii=False), encoding="utf-8")
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


def _enrich_scan_async(scan_id: str, players: list, started: float) -> None:
    ai_enabled = bool(load_config().get("ai_enabled"))
    total = len(players)
    done_count = 0
    progress_lock = threading.Lock()

    def _on_player(rep) -> None:
        nonlocal done_count
        pdict = asdict(rep)
        pdict["loading"] = False
        pdict["loading_stage"] = "ai" if ai_enabled else "done"
        with progress_lock:
            done_count += 1
            cur_done = done_count
        with _STATE_LOCK:
            scan_rec = next((s for s in _SCANS if s["id"] == scan_id), None)
            if not scan_rec:
                return
            for i, existing in enumerate(scan_rec.get("players", [])):
                if existing.get("nick") == pdict.get("nick"):
                    scan_rec["players"][i] = pdict
                    break
            scan_rec["progress"] = {"done": cur_done, "total": total}
            scan_rec["duration_ms"] = round((time.time() - started) * 1000)
        _broadcast_event(
            "scan_update",
            {
                "scan_id": scan_id,
                "player": pdict,
                "progress": {"done": cur_done, "total": total},
                "scan_status": "loading_steam",
            },
        )

    reports = enrich_players(players, fetch_steam=True, on_player_ready=_on_player)
    player_dicts = []
    for r in reports:
        d = asdict(r)
        d["loading"] = False
        d["loading_stage"] = "ai" if ai_enabled else "done"
        player_dicts.append(d)

    if ai_enabled and player_dicts:
        with _STATE_LOCK:
            scan_rec = next((s for s in _SCANS if s["id"] == scan_id), None)
            if scan_rec:
                scan_rec["players"] = player_dicts
                scan_rec["scan_status"] = "loading_ai"
                scan_rec["duration_ms"] = round((time.time() - started) * 1000)
                snapshot = dict(scan_rec)
            else:
                snapshot = None
        if snapshot:
            _broadcast_event("scan", snapshot)
        player_dicts, ai_error = analyze_players_with_ai(player_dicts)
    else:
        ai_error = None

    for d in player_dicts:
        d["loading"] = False
        d["loading_stage"] = "done"

    with _STATE_LOCK:
        scan_rec = next((s for s in _SCANS if s["id"] == scan_id), None)
        if not scan_rec:
            return
        scan_rec["players"] = player_dicts
        scan_rec["player_count"] = len(player_dicts)
        scan_rec["duration_ms"] = round((time.time() - started) * 1000)
        scan_rec["ai_error"] = ai_error
        scan_rec["scan_status"] = "done"
        scan_rec["progress"] = {"done": total, "total": total}
        _save_history_locked()
        final_snapshot = dict(scan_rec)
    _broadcast_event("scan", final_snapshot)


def run_scan(image_path: Optional[Path] = None, fetch_steam: bool = True) -> dict:
    started = time.time()
    if image_path is None:
        image_path = capture_screenshot()
    else:
        image_path = Path(image_path).resolve()
        if not image_path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

    console_status = parse_latest_status()
    players = scan_scoreboard(str(image_path))
    if console_status and console_status.nicks:
        for p in players:
            p.nick = correct_nick_from_console(p.nick, console_status.nicks)

    initial_reports = enrich_players(players, fetch_steam=False)
    initial_dicts = []
    for r in initial_reports:
        d = asdict(r)
        if fetch_steam:
            d["loading"] = True
            d["loading_stage"] = "steam"
        else:
            d["loading"] = False
            d["loading_stage"] = "done"
        initial_dicts.append(d)

    duration_ms = round((time.time() - started) * 1000)
    now_dt = datetime.now()
    match_id, map_name = _resolve_match_meta(now_dt, console_status)
    scan_id = uuid.uuid4().hex[:10]
    will_stream = bool(fetch_steam and players)

    scan_record = {
        "id": scan_id,
        "created_at": now_dt.isoformat(timespec="seconds"),
        "date": now_dt.strftime("%Y-%m-%d"),
        "match_id": match_id,
        "map_name": map_name,
        "image_name": image_path.name,
        "image_url": f"/screenshots/{image_path.name}" if image_path.parent.resolve() == get_screenshot_dir().resolve() else None,
        "duration_ms": duration_ms,
        "player_count": len(initial_dicts),
        "players": initial_dicts,
        "ai_error": None,
        "scan_status": "loading_steam" if will_stream else "done",
        "progress": {"done": 0 if will_stream else len(initial_dicts), "total": len(initial_dicts)},
    }

    with _STATE_LOCK:
        _SCANS.insert(0, scan_record)
        del _SCANS[_MAX_HISTORY:]
        _save_history_locked()

    _broadcast_event("scan", scan_record)
    if will_stream:
        threading.Thread(
            target=_enrich_scan_async,
            args=(scan_id, players, started),
            daemon=True,
        ).start()
    return scan_record


def _is_cs2_active() -> bool:
    if sys.platform == "win32":
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()  # type: ignore[attr-defined]
            return _win32_is_cs2_hwnd(hwnd)
        except Exception:
            return True

    if sys.platform == "darwin":
        if not shutil.which("osascript"):
            return True
        try:
            res = subprocess.run(
                [
                    "osascript",
                    "-e",
                    'tell application "System Events" to get name of first application process whose frontmost is true',
                ],
                capture_output=True,
                text=True,
                timeout=1.5,
            )
            if res.returncode != 0 or not res.stdout.strip():
                return True
            app_name = res.stdout.strip().lower()
            return "counter-strike" in app_name or "cs2" in app_name
        except Exception:
            return True

    if not shutil.which("hyprctl"):
        return True
    try:
        res = subprocess.run(["hyprctl", "activewindow", "-j"], capture_output=True, text=True, timeout=1.5)
        if res.returncode != 0 or not res.stdout.strip():
            return True
        info = json.loads(res.stdout)
        return _is_cs2_client(info)
    except Exception:
        return True


def _send_notification(title: str, msg: str) -> None:
    if not _NOTIFY_ENABLED:
        return
    try:
        if sys.platform.startswith("linux"):
            if shutil.which("notify-send"):
                clean_env = {k: v for k, v in os.environ.items() if k != "LD_LIBRARY_PATH"}
                subprocess.run(["notify-send", title, msg], env=clean_env, check=False)
        elif sys.platform == "darwin":
            if shutil.which("osascript"):
                safe_msg = msg.replace("\\", "\\\\").replace('"', '\\"')
                safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
                subprocess.run(
                    ["osascript", "-e", f'display notification "{safe_msg}" with title "{safe_title}"'],
                    check=False,
                )
        # Windows: safe no-op
    except Exception:
        pass


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

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            self._send_file(WEB_DIR / "index.html", "text/html; charset=utf-8")
            return
        if path == "/styles.css":
            self._send_file(WEB_DIR / "styles.css", "text/css; charset=utf-8")
            return
        if path == "/app.js":
            self._send_file(WEB_DIR / "app.js", "application/javascript; charset=utf-8")
            return

        if path == "/api/latest":
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
            self._send_json({"latest": latest, "history": history, "matches": matches})
            return

        if path == "/api/config":
            cs2_dir = find_cs2_game_dir()
            resolved = {
                "cs2_game_dir": str(cs2_dir) if cs2_dir else "",
                "console_log_path": str(get_console_log_path()),
                "screenshots_dir": str(get_screenshot_dir()),
                "claude_cli_available": bool(shutil.which("claude")),
            }
            self._send_json(
                get_public_config(
                    resolved=resolved,
                    env_key_present=bool(_get_steam_api_key()),
                    env_faceit_key_present=bool(_get_faceit_api_key()),
                )
            )
            return

        if path == "/api/matches":
            with _STATE_LOCK:
                matches = _build_matches_locked()
            self._send_json({"matches": matches})
            return

        if path.startswith("/api/scan/"):
            scan_id = path.split("/api/scan/", 1)[1].strip("/")
            with _STATE_LOCK:
                match = next((s for s in _SCANS if s["id"] == scan_id), None)
            if not match:
                self._send_json({"error": "Scan not found"}, status=404)
            else:
                self._send_json(match)
            return

        if path.startswith("/screenshots/"):
            filename = Path(path.split("/screenshots/", 1)[1]).name
            sdir = get_screenshot_dir().resolve()
            target = (sdir / filename).resolve()
            if target.parent != sdir or not target.is_file():
                target = (SCREENSHOT_DIR.resolve() / filename).resolve()
                if target.parent != SCREENSHOT_DIR.resolve() or not target.is_file():
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
            ctype = "image/jpeg" if target.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
            self._send_file(target, ctype)
            return

        if path == "/api/events":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            q: queue.Queue[str] = queue.Queue(maxsize=20)
            with _STATE_LOCK:
                _SUBSCRIBERS.append(q)
            try:
                self.wfile.write(b": connected\n\n")
                self.wfile.flush()
                while True:
                    try:
                        msg = q.get(timeout=20)
                        self.wfile.write(msg.encode("utf-8"))
                        self.wfile.flush()
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                with _STATE_LOCK:
                    if q in _SUBSCRIBERS:
                        _SUBSCRIBERS.remove(q)
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/tab-pressed":
            self._send_json(schedule_tab_scan())
            return

        if path == "/api/scan":
            try:
                raw = self._read_body()
                payload = json.loads(raw.decode("utf-8")) if raw else {}
                img_path = payload.get("image_path")
                fetch_steam = payload.get("fetch_steam", True)
                result = run_scan(
                    image_path=Path(img_path) if img_path else None,
                    fetch_steam=bool(fetch_steam),
                )
                self._send_json(result)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if path == "/api/rescan":
            try:
                raw = self._read_body()
                payload = json.loads(raw.decode("utf-8")) if raw else {}
                scan_id = payload.get("scan_id")
                with _STATE_LOCK:
                    scan_rec = next((s for s in _SCANS if s["id"] == scan_id), None) if scan_id else (_SCANS[0] if _SCANS else None)
                    if not scan_rec:
                        self._send_json({"error": "Scan not found"}, status=404)
                        return
                img_name = scan_rec.get("image_name")
                sdir = get_screenshot_dir().resolve()
                img_path = (sdir / Path(img_name).name).resolve()
                if not img_path.is_file():
                    img_path = (SCREENSHOT_DIR.resolve() / Path(img_name).name).resolve()
                if not img_path.is_file():
                    self._send_json({"error": "Screenshot file not found"}, status=404)
                    return

                started = time.time()
                console_status = parse_latest_status()
                players = scan_scoreboard(str(img_path))
                if console_status and console_status.nicks:
                    for p in players:
                        p.nick = correct_nick_from_console(p.nick, console_status.nicks)

                initial_reports = enrich_players(players, fetch_steam=False)
                initial_dicts = []
                for r in initial_reports:
                    d = asdict(r)
                    d["loading"] = bool(players)
                    d["loading_stage"] = "steam" if players else "done"
                    initial_dicts.append(d)
                duration_ms = round((time.time() - started) * 1000)

                with _STATE_LOCK:
                    scan_rec["players"] = initial_dicts
                    scan_rec["player_count"] = len(initial_dicts)
                    scan_rec["duration_ms"] = duration_ms
                    scan_rec["ai_error"] = None
                    scan_rec["scan_status"] = "loading_steam" if players else "done"
                    scan_rec["progress"] = {"done": 0, "total": len(initial_dicts)}
                    _save_history_locked()
                    snapshot = dict(scan_rec)
                    target_scan_id = scan_rec["id"]

                _broadcast_event("scan", snapshot)
                if players:
                    threading.Thread(
                        target=_enrich_scan_async,
                        args=(target_scan_id, players, started),
                        daemon=True,
                    ).start()
                self._send_json(snapshot)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if path == "/api/setup-cs2":
            try:
                self._send_json(setup_autoexec())
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if path == "/api/recheck":
            try:
                raw = self._read_body()
                payload = json.loads(raw.decode("utf-8")) if raw else {}
                scan_id = payload.get("scan_id")
                old_nick = payload.get("old_nick")
                query = (payload.get("query") or "").strip()
                if not scan_id or not old_nick or not query:
                    self._send_json({"error": "scan_id, old_nick, and query are required"}, status=400)
                    return

                with _STATE_LOCK:
                    scan_rec = next((s for s in _SCANS if s["id"] == scan_id), None)
                    if not scan_rec:
                        self._send_json({"error": "Scan not found"}, status=404)
                        return
                    idx = next((i for i, p in enumerate(scan_rec["players"]) if p["nick"] == old_nick), None)
                    if idx is None:
                        self._send_json({"error": "Player not found in scan"}, status=404)
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

                self._send_json(snapshot)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if path == "/api/ai-analyze":
            try:
                raw = self._read_body()
                payload = json.loads(raw.decode("utf-8")) if raw else {}
                scan_id = payload.get("scan_id")
                target_nick = (payload.get("nick") or "").strip()
                with _STATE_LOCK:
                    scan_rec = next((s for s in _SCANS if s["id"] == scan_id), None) if scan_id else (_SCANS[0] if _SCANS else None)
                    if not scan_rec:
                        self._send_json({"error": "Scan not found"}, status=404)
                        return
                    players_copy = [dict(p) for p in scan_rec.get("players", [])]

                if target_nick:
                    subset = [p for p in players_copy if p.get("nick") == target_nick]
                    if not subset:
                        self._send_json({"error": "Player not found in scan"}, status=404)
                        return
                    analyzed, ai_error = analyze_players_with_ai(subset, force=True)
                    if ai_error:
                        self._send_json({"error": ai_error}, status=500)
                        return
                    updated_map = {p["nick"]: p for p in analyzed}
                    new_players = [updated_map.get(p["nick"], p) for p in players_copy]
                else:
                    new_players, ai_error = analyze_players_with_ai(players_copy, force=True)
                    if ai_error:
                        self._send_json({"error": ai_error}, status=500)
                        return

                with _STATE_LOCK:
                    scan_rec["players"] = new_players
                    scan_rec["ai_error"] = None
                    _save_history_locked()
                    snapshot = dict(scan_rec)

                self._send_json(snapshot)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if path == "/api/config":
            try:
                raw = self._read_body()
                payload = json.loads(raw.decode("utf-8")) if raw else {}
                if not isinstance(payload, dict):
                    self._send_json({"error": "Invalid config payload"}, status=400)
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
                self._send_json(
                    get_public_config(
                        resolved=resolved,
                        env_key_present=bool(_get_steam_api_key()),
                        env_faceit_key_present=bool(_get_faceit_api_key()),
                    )
                )
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if path == "/api/upload":
            try:
                raw = self._read_body()
                if not raw:
                    self._send_json({"error": "Empty upload"}, status=400)
                    return
                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
                out_path = get_screenshot_dir() / f"upload_{ts}.png"
                out_path.write_bytes(raw)
                result = run_scan(image_path=out_path, fetch_steam=True)
                self._send_json(result)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        self.send_error(HTTPStatus.NOT_FOUND)


def main() -> None:
    parser = argparse.ArgumentParser(description="CS2 Scoreboard Scanner Web Server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--delay", type=float, default=0.5, help="Seconds to wait after TAB before screenshot")
    parser.add_argument("--cooldown", type=float, default=4.0, help="Minimum seconds between TAB scans")
    parser.add_argument("--all-windows", action="store_true", help="Trigger TAB scan even when CS2 is not focused")
    args = parser.parse_args()

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
