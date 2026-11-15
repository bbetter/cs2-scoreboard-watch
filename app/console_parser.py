"""Parse CS2 console.log (-condebug) status output for match ID, map, and exact player nicknames."""

from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from lookup.nick_match import pick_best
from storage.config_store import load_config

DEFAULT_CONSOLE_LOG = Path(
    "/media/andrii/Entertainment/SteamLibrary/steamapps/common/Counter-Strike Global Offensive/game/csgo/console.log"
)


@dataclass
class ConsoleStatus:
    match_id: str | None = None
    map_name: str | None = None
    server_addr: str | None = None
    nicks: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.nicks is None:
            self.nicks = []


def find_cs2_game_dir() -> Path | None:
    configured_dir = load_config().get("cs2_game_dir", "").strip()
    if configured_dir:
        p = Path(configured_dir).expanduser()
        if p.is_dir():
            return p

    candidates: list[Path] = []
    vdf_paths = [
        Path.home() / ".local/share/Steam/steamapps/libraryfolders.vdf",
        Path.home() / ".steam/steam/steamapps/libraryfolders.vdf",
    ]
    if sys.platform == "win32":
        try:
            import winreg  # type: ignore[import-not-found]

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                steam_path, _ = winreg.QueryValueEx(key, "SteamPath")
                if steam_path:
                    sp = Path(steam_path)
                    vdf_paths.append(sp / "steamapps/libraryfolders.vdf")
                    candidates.append(sp / "steamapps/common/Counter-Strike Global Offensive/game/csgo")
        except OSError:
            pass
        vdf_paths.extend([
            Path(r"C:\Program Files (x86)\Steam\steamapps\libraryfolders.vdf"),
            Path(r"C:\Program Files\Steam\steamapps\libraryfolders.vdf"),
        ])
    elif sys.platform == "darwin":
        mac_steam = Path.home() / "Library/Application Support/Steam"
        vdf_paths.append(mac_steam / "steamapps/libraryfolders.vdf")
        candidates.append(mac_steam / "steamapps/common/Counter-Strike Global Offensive/game/csgo")

    for vdf in vdf_paths:
        if vdf.is_file():
            try:
                txt = vdf.read_text(encoding="utf-8", errors="ignore")
                for m in re.finditer(r'"path"\s+"([^"]+)"', txt):
                    lib_path = m.group(1).replace("\\\\", "\\")
                    candidates.append(Path(lib_path) / "steamapps/common/Counter-Strike Global Offensive/game/csgo")
            except OSError:
                pass
    candidates.extend([
        DEFAULT_CONSOLE_LOG.parent,
        Path.home() / ".local/share/Steam/steamapps/common/Counter-Strike Global Offensive/game/csgo",
    ])
    for d in candidates:
        if d.is_dir():
            return d
    return None


def get_console_log_path() -> Path:
    configured_log = load_config().get("console_log_path", "").strip()
    if configured_log:
        return Path(configured_log).expanduser()
    env_path = os.environ.get("CS2_CONSOLE_LOG", "").strip()
    if env_path:
        return Path(env_path)
    cs2_dir = find_cs2_game_dir()
    return (cs2_dir / "console.log") if cs2_dir else DEFAULT_CONSOLE_LOG


def setup_autoexec() -> dict:
    cs2_dir = find_cs2_game_dir()
    if not cs2_dir:
        raise FileNotFoundError("Не знайдено папку Counter-Strike 2 у бібліотеках Steam.")
    cfg_dir = cs2_dir / "cfg"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    autoexec = cfg_dir / "autoexec.cfg"
    bind_line = 'bind "TAB" "+showscores; status"'
    existing = autoexec.read_text(encoding="utf-8", errors="ignore") if autoexec.is_file() else ""
    already_present = bind_line in existing
    if not already_present:
        sep = "\n" if existing and not existing.endswith("\n") else ""
        with autoexec.open("a", encoding="utf-8") as f:
            f.write(f"{sep}{bind_line}\n")
    return {
        "cs2_dir": str(cs2_dir),
        "autoexec_path": str(autoexec),
        "already_present": already_present,
        "bind_line": bind_line,
        "launch_options": "-condebug +exec autoexec.cfg",
    }


def parse_latest_status(max_age_sec: float = 120.0) -> ConsoleStatus | None:
    log_path = get_console_log_path()
    if not log_path.is_file():
        return None
    try:
        st = log_path.stat()
        if time.time() - st.st_mtime > max_age_sec:
            return None
        with log_path.open("rb") as f:
            size = st.st_size
            f.seek(max(0, size - 65536))
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None

    lines = text.splitlines()
    # Find the last status header block ("Server:" or "hostname:" or "udp/ip" or "---------")
    start_idx = 0
    for i in range(len(lines) - 1, -1, -1):
        low = lines[i].lower()
        if "hostname:" in low or "udp/ip  :" in low or "steamid :" in low or "---------" in low:
            # Walk back a few lines to catch Server/Client header
            start_idx = max(0, i - 6)
            break

    block = lines[start_idx:]
    map_name = None
    server_addr = None
    server_steamid = None
    match_id = None
    nicks: list[str] = []

    for line in block:
        m_map = re.search(r"^\s*map\s*:\s*([^\s]+)", line, re.IGNORECASE)
        if m_map:
            map_name = m_map.group(1).strip()

        m_ip = re.search(r"^\s*(?:udp/ip|source)\s*:\s*([^\s(]+)", line, re.IGNORECASE)
        if m_ip:
            server_addr = m_ip.group(1).strip()

        m_sid = re.search(r"^\s*steamid\s*:\s*(\[[^\]]+\]|\d+)", line, re.IGNORECASE)
        if m_sid:
            server_steamid = m_sid.group(1).strip()

        m_mid = re.search(r"(?:match_?id|reservation)\s*[:=]\s*([A-Za-z0-9_-]+)", line, re.IGNORECASE)
        if m_mid:
            match_id = m_mid.group(1).strip()

        # CS2 status player lines typically end with quoted nick: 'PlayerName'
        m_nick = re.search(r"^\s*(?:#?\s*\d+|\d+)\s+\d+:\d+\s+\d+\s+\d+\s+\w+.*?'([^']+)'\s*$", line)
        if m_nick:
            nick = m_nick.group(1).strip()
            if nick and nick not in nicks:
                nicks.append(nick)

    if not (match_id or server_steamid or server_addr or map_name or nicks):
        return None

    if not match_id:
        parts = [p for p in (server_steamid, server_addr, map_name) if p]
        match_id = "_".join(parts) if parts else None

    return ConsoleStatus(
        match_id=match_id,
        map_name=map_name,
        server_addr=server_addr,
        nicks=nicks,
    )


def correct_nick_from_console(ocr_nick: str, console_nicks: list[str]) -> str:
    if not console_nicks:
        return ocr_nick
    for cn in console_nicks:
        if cn.lower() == ocr_nick.lower():
            return cn
    # Fuzzy fallback: only an unambiguous match replaces the OCR nick.
    best = pick_best(ocr_nick, console_nicks)
    return best if best is not None else ocr_nick
