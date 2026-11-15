"""Persistent user configuration store (config.json). The key list, types and limits live in config_schema."""

from __future__ import annotations

import json
import sys
import threading
from typing import Any

import storage.config_schema as _schema
from storage.config_schema import (  # noqa: F401  (re-exported: other modules import these names from config_store)
    ALLOWED_AI_PROVIDERS,
    ALLOWED_SCREENSHOT_TOOLS,
    DEFAULT_AI_PROMPT,
    DEFAULT_AI_WEIGHTS,
)
from storage.paths import DATA_DIR

CONFIG_FILE = DATA_DIR / "config.json"

DEFAULT_CONFIG: dict[str, Any] = _schema.default_config()

_CONFIG_LOCK = threading.Lock()

# Compatibility aliases for the previous private helpers.
_clamp_float = _schema.clamp_float
_normalize_config = _schema.normalize
_mask_key = _schema.mask_key


def load_config() -> dict[str, Any]:
    with _CONFIG_LOCK:
        if not CONFIG_FILE.is_file():
            return dict(DEFAULT_CONFIG)
        try:
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return _schema.normalize(raw)
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
                    current = _schema.normalize(raw)
            except Exception:
                pass

        normalized = _schema.normalize(_schema.apply_updates(current, updates))
        CONFIG_FILE.write_text(
            json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return normalized


def get_public_config(
    resolved: dict[str, Any] | None = None,
    env_key_present: bool = False,
    env_faceit_key_present: bool = False,
) -> dict[str, Any]:
    return _schema.public_view(
        load_config(),
        {"steam_api_key": env_key_present, "faceit_api_key": env_faceit_key_present},
        resolved=resolved,
        platform=sys.platform,
    )
