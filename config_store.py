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
ALLOWED_AI_PROVIDERS = ("claude_cli", "anthropic_api")

DEFAULT_AI_PROMPT = (
    "Ти аналітик античіт- і смурф-телеметрії CS2. Оціни кожного гравця за двома осями: "
    "1) Чітерство (cheat_probability: 0..100 — аномальний aim, reaction_time_ms / cstracker.ttd_ms, HS%, підозрілий K/D, VAC/Game/Leetify бани, cstracker.vaclive > 0 або низький cstracker.trust); "
    "2) Смурфінг (smurf_probability: 0..100 — молодий акаунт / мало годин в CS2 / відсутність ветеранських медалей / чисті бани у поєднанні з високим рівнем гри, FACEIT ELO, scope_gg.hltv_2_1 або високим K/D). "
    "Використовуй усі доступні джерела: поточний рахунок у матчі, Steam API, Leetify, FACEIT та extra_stats (CSTracker trust/vaclive/ttd/hltv, Scope.gg role/hltv_2_1/adr, medals_count, has_veteran_coin, xp_level). "
    "Крос-перевіряй метрики між Leetify, CSTracker і Scope.gg. Враховуй невизначеність (uncertainty) та ефективні ваги (effective_weights): "
    "коли зовнішні джерела закриті чи відсутні, спирайся на доступні дані за effective_weights і знижуй confidence при високій uncertainty. "
    "Опиши покроковий аналіз (analysis_steps) українською мовою, зроби короткий висновок (summary) "
    "і вистав рішення decision одне з: LEGIT, UNCERTAIN, SMURF, SUSPICIOUS або BLATANT."
)

DEFAULT_AI_WEIGHTS: dict[str, float] = {
    "scoreboard": 0.30,
    "leetify": 0.40,
    "steam_account": 0.30,
}

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
    "faceit_api_key": "",
    "ai_enabled": False,
    "ai_provider": "claude_cli",
    "ai_model": "sonnet",
    "ai_prompt": DEFAULT_AI_PROMPT,
    "ai_weights": dict(DEFAULT_AI_WEIGHTS),
    "anthropic_api_key": "",
    "leetify_api_key": "",
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
    for key in ("cs2_game_dir", "console_log_path", "screenshots_dir", "steam_api_key", "faceit_api_key", "leetify_api_key"):
        val = raw.get(key, "")
        cfg[key] = str(val).strip() if val is not None else ""
    tool = str(raw.get("screenshot_tool", "auto") or "auto").strip().lower()
    cfg["screenshot_tool"] = tool if tool in ALLOWED_SCREENSHOT_TOOLS else "auto"

    cfg["ai_enabled"] = bool(raw.get("ai_enabled", False))
    provider = str(raw.get("ai_provider", "claude_cli") or "claude_cli").strip().lower()
    cfg["ai_provider"] = provider if provider in ALLOWED_AI_PROVIDERS else "claude_cli"
    cfg["ai_model"] = str(raw.get("ai_model", "sonnet") or "sonnet").strip() or "sonnet"
    prompt = str(raw.get("ai_prompt") or "").strip()
    cfg["ai_prompt"] = prompt if prompt else DEFAULT_AI_PROMPT
    cfg["anthropic_api_key"] = str(raw.get("anthropic_api_key") or "").strip()

    raw_weights = raw.get("ai_weights") if isinstance(raw.get("ai_weights"), dict) else {}
    cfg["ai_weights"] = {
        "scoreboard": _clamp_float(raw_weights.get("scoreboard"), DEFAULT_AI_WEIGHTS["scoreboard"], 0.0, 1.0),
        "leetify": _clamp_float(raw_weights.get("leetify"), DEFAULT_AI_WEIGHTS["leetify"], 0.0, 1.0),
        "steam_account": _clamp_float(raw_weights.get("steam_account"), DEFAULT_AI_WEIGHTS["steam_account"], 0.0, 1.0),
    }
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
            elif key == "faceit_api_key":
                if updates.get("clear_faceit_api_key"):
                    merged["faceit_api_key"] = ""
                else:
                    new_key = str(updates.get("faceit_api_key") or "").strip()
                    if new_key:
                        merged["faceit_api_key"] = new_key
            elif key == "anthropic_api_key":
                if updates.get("clear_anthropic_api_key"):
                    merged["anthropic_api_key"] = ""
                else:
                    new_key = str(updates.get("anthropic_api_key") or "").strip()
                    if new_key:
                        merged["anthropic_api_key"] = new_key
            else:
                merged[key] = updates[key]

        if updates.get("clear_steam_api_key"):
            merged["steam_api_key"] = ""
        if updates.get("clear_faceit_api_key"):
            merged["faceit_api_key"] = ""
        if updates.get("clear_anthropic_api_key"):
            merged["anthropic_api_key"] = ""

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


def get_public_config(
    resolved: Optional[dict[str, Any]] = None,
    env_key_present: bool = False,
    env_faceit_key_present: bool = False,
) -> dict[str, Any]:
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

    faceit_key = cfg.pop("faceit_api_key", "")
    if faceit_key:
        faceit_source = "config"
        faceit_masked = _mask_key(faceit_key)
    elif env_faceit_key_present or os.environ.get("FACEIT_API_KEY", "").strip():
        faceit_source = "env"
        faceit_masked = "**** (з середовища)"
    else:
        faceit_source = "none"
        faceit_masked = ""

    ant_key = cfg.pop("anthropic_api_key", "")
    if ant_key:
        ant_source = "config"
        ant_masked = _mask_key(ant_key)
    elif os.environ.get("ANTHROPIC_API_KEY", "").strip():
        ant_source = "env"
        ant_masked = "**** (з середовища)"
    else:
        ant_source = "none"
        ant_masked = ""

    return {
        "config": cfg,
        "has_steam_api_key": key_source != "none",
        "steam_api_key_source": key_source,
        "steam_api_key_masked": masked,
        "has_faceit_api_key": faceit_source != "none",
        "faceit_api_key_source": faceit_source,
        "faceit_api_key_masked": faceit_masked,
        "has_anthropic_api_key": ant_source != "none",
        "anthropic_api_key_source": ant_source,
        "anthropic_api_key_masked": ant_masked,
        "allowed_screenshot_tools": list(ALLOWED_SCREENSHOT_TOOLS),
        "allowed_ai_providers": list(ALLOWED_AI_PROVIDERS),
        "platform": sys.platform,
        "resolved": resolved or {},
    }
