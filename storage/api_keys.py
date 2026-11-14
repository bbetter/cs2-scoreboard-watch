"""API keys for the Steam Web API and FACEIT: config.json first, then the environment, then .env / shell rc files.

Lives outside lookup/ so that sources/* can read keys without importing the scanner module. lookup.steam_lookup
re-exports these names for app/server.py and the existing callers.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from storage.paths import ROOT

_STEAM_API_KEY_CACHE: Optional[str] = None
_FACEIT_API_KEY_CACHE: Optional[str] = None


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
        from storage.config_store import load_config

        cfg_key = str(load_config().get("faceit_api_key") or "").strip()
        if cfg_key:
            _FACEIT_API_KEY_CACHE = cfg_key
            return cfg_key
    except Exception:
        pass
    key = os.environ.get("FACEIT_API_KEY", "").strip()
    if not key:
        env_path = ROOT / ".env"
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
        from storage.config_store import load_config

        cfg_key = str(load_config().get("steam_api_key") or "").strip()
        if cfg_key:
            _STEAM_API_KEY_CACHE = cfg_key
            return cfg_key
    except Exception:
        pass
    key = os.environ.get("STEAM_API_KEY", "").strip()
    if not key:
        for rc_path in (ROOT / ".env", Path.home() / ".zshrc", Path.home() / ".bashrc"):
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
