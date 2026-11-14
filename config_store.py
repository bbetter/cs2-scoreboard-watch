"""Persistent user configuration store (config.json) for CS2 Scoreboard Watch."""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Optional

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"

ALLOWED_SCREENSHOT_TOOLS = ("auto", "grim", "imagemagick", "mss", "screencapture", "pil")

DEFAULT_CONFIG: dict[str, Any] = {
    "tab_delay_sec": 0.5,
    "tab_cooldown_sec": 4.0,
    "cs2_only": True,
    "cs2_game_dir": "",
    "console_log_path": "",
    "screenshots_dir": "",
    "screenshot_tool": "auto",
    "notify_enabled": True,
    "steam_api_key": "",
}

_CONFIG_LOCK = threading.Lock()


def _clamp_float(val: Any, default: float, min_val: float, max_val: float) -> float:
    try:
        num = float(val)
    except (TypeError, ValueError):
        return default
    return round(max(min_val, min(max_val, num)), 2)


def _normalize_config(raw: dict[str, Any]) -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    cfg["tab_delay_sec"] = _clamp_float(raw.get("tab_delay_sec"), 0.5, 0.0, 5.0)
    cfg["tab_cooldown_sec"] = _clamp_float(raw.get("tab_cooldown_sec"), 4.0, 0.5, 30.0)
    cfg["cs2_only"] = bool(raw.get("cs2_only", True))
    cfg["notify_enabled"] = bool(raw.get("notify_enabled", True))
    for key in ("cs2_game_dir", "console_log_path", "screenshots_dir", "steam_api_key"):
        val = raw.get(key, "")
        cfg[key] = str(val).strip() if val is not None else ""
    tool = str(raw.get("screenshot_tool", "auto") or "auto").strip().lower()
    cfg["screenshot_tool"] = tool if tool in ALLOWED_SCREENSHOT_TOOLS else "auto"
    return cfg


def load_config() -> dict[str, Any]:
    with _CONFIG_LOCK:
        if not CONFIG_FILE.is_file():
            return dict(DEFAULT_CONFIG)
        try:
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return _normalize_config(raw)
        except Exception:
            pass
        return dict(DEFAULT_CONFIG)


def save_config(updates: dict[str, Any]) -> dict[str, Any]:
    with _CONFIG_LOCK:
        current = dict(DEFAULT_CONFIG)
        if CONFIG_FILE.is_file():
            try:
                raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    current = _normalize_config(raw)
            except Exception:
                pass

        merged = dict(current)
        for key in DEFAULT_CONFIG:
            if key not in updates:
                continue
            if key == "steam_api_key":
                # Keep existing key unless explicitly cleared or replaced with a non-empty value.
                if updates.get("clear_steam_api_key"):
                    merged["steam_api_key"] = ""
                else:
                    new_key = str(updates.get("steam_api_key") or "").strip()
                    if new_key:
                        merged["steam_api_key"] = new_key
            else:
                merged[key] = updates[key]

        if updates.get("clear_steam_api_key"):
            merged["steam_api_key"] = ""

        normalized = _normalize_config(merged)
        CONFIG_FILE.write_text(
            json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return normalized


def _mask_key(key: str) -> str:
    if len(key) <= 6:
        return "****" if key else ""
    return f"{'*' * (len(key) - 4)}{key[-4:]}"


def get_public_config(resolved: Optional[dict[str, Any]] = None, env_key_present: bool = False) -> dict[str, Any]:
    cfg = load_config()
    cfg_key = cfg.pop("steam_api_key", "")
    if cfg_key:
        key_source = "config"
        masked = _mask_key(cfg_key)
    elif env_key_present or os.environ.get("STEAM_API_KEY", "").strip():
        key_source = "env"
        masked = "**** (з середовища)"
    else:
        key_source = "none"
        masked = ""

    return {
        "config": cfg,
        "has_steam_api_key": key_source != "none",
        "steam_api_key_source": key_source,
        "steam_api_key_masked": masked,
        "allowed_screenshot_tools": list(ALLOWED_SCREENSHOT_TOOLS),
        "platform": sys.platform,
        "resolved": resolved or {},
    }
