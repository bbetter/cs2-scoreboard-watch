"""Declarative configuration schema (config.json): every key, its type/limits/default and key sources.

Core settings live here; each external source declares its own API-key settings on the class
(`DataSource.settings`). SCHEMA is the union, in the presentation/storage order below.
normalize / apply_updates / public_view are generic and driven by SCHEMA; config_store wraps them.
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Mapping
from typing import Any

from sources import REGISTRY
from sources.base import SourceRegistry
from storage.config_fields import Field, Secret, describe

DEFAULT_AI_PROMPT = (
    "Ти аналітик античіт- і смурф-телеметрії CS2. Оціни кожного гравця за двома осями: "
    "1) Чітерство (cheat_probability: 0..100 — аномальний aim, reaction_time_ms / cstracker.matches[].ttd, HS%, підозрілий K/D, VAC/Game/Leetify бани або низький cstracker.trust); "
    "2) Смурфінг (smurf_probability: 0..100 — молодий акаунт / мало годин в CS2 / чисті бани у поєднанні з високим рівнем гри, FACEIT ELO або високим K/D). "
    "Використовуй усі доступні джерела: поточний рахунок у матчі, Steam API, Leetify, FACEIT та extra_stats (CSTracker trust і cstracker.matches[].ttd). "
    "Крос-перевіряй метрики між Leetify і CSTracker. Враховуй невизначеність (uncertainty) та ефективні ваги (effective_weights): "
    "коли зовнішні джерела закриті чи відсутні, спирайся на доступні дані за effective_weights і знижуй confidence при високій uncertainty. "
    "Опиши покроковий аналіз (analysis_steps) українською мовою, зроби короткий висновок (summary) "
    "і вистав рішення decision одне з: LEGIT, UNCERTAIN, SMURF, SUSPICIOUS або BLATANT."
)

ALLOWED_SCREENSHOT_TOOLS = ("auto", "grim", "imagemagick", "mss", "screencapture", "pil")
ALLOWED_AI_PROVIDERS = ("claude_cli", "anthropic_api")
DEFAULT_AI_WEIGHTS: dict[str, float] = {
    "scoreboard": 0.30,
    "leetify": 0.40,
    "steam_account": 0.30,
}
MASKED_FROM_ENV = "**** (з середовища)"

CORE_FIELDS: list = [
    Field("tab_delay_sec", "float", 0.5, label="Delay after TAB (s)", group="scan", min=0.0, max=5.0),
    Field("tab_cooldown_sec", "float", 4.0, label="Cooldown between scans (s)", group="scan", min=0.5, max=30.0),
    Field("cs2_only", "bool", True, label="Only when CS2 is focused", group="scan"),
    Field("cs2_game_dir", "text", "", label="CS2 game folder", group="paths"),
    Field("console_log_path", "text", "", label="console.log path", group="paths"),
    Field("screenshots_dir", "text", "", label="Screenshots folder", group="paths"),
    Field("screenshot_tool", "enum", "auto", label="Screenshot tool", group="scan", choices=ALLOWED_SCREENSHOT_TOOLS,
          allowed_key="allowed_screenshot_tools"),
    Field("notify_enabled", "bool", True, label="Desktop notifications", group="scan"),
    Field("ai_enabled", "bool", False, label="AI analysis", group="ai"),
    Field("ai_provider", "enum", "claude_cli", label="AI provider", group="ai", choices=ALLOWED_AI_PROVIDERS,
          allowed_key="allowed_ai_providers"),
    Field("ai_model", "str", "sonnet", label="AI model", group="ai"),
    Field("ai_prompt", "str", DEFAULT_AI_PROMPT, label="AI prompt", group="ai"),
    Field("ai_weights", "weights", dict(DEFAULT_AI_WEIGHTS), label="Score weights", group="ai", min=0.0, max=1.0),
    Secret("anthropic_api_key", "text", "", label="Anthropic API key", group="api_keys",
           env=("ANTHROPIC_API_KEY",), falsy_empty=True),
    Field("leetify_cache_ttl_sec", "int", 0, label="Leetify memory cache (s)", group="cache", min=0, max=86400,
          help="Seconds Leetify data may be reused from memory (never written to profile_cache.json). 0 = no reuse."),
]
# Steam/FACEIT/Leetify key settings are declared by their sources (sources/*.py).
STEAM_KEY_FIELD = "steam_api_key"
FACEIT_KEY_FIELD = "faceit_api_key"

# Historical key order: it is part of the API response and of config.json.
ORDER: tuple[str, ...] = (
    "tab_delay_sec", "tab_cooldown_sec", "cs2_only", "cs2_game_dir", "console_log_path", "screenshots_dir",
    "screenshot_tool", "notify_enabled", "steam_api_key", "faceit_api_key", "ai_enabled", "ai_provider",
    "ai_model", "ai_prompt", "ai_weights", "anthropic_api_key", "leetify_api_key", "leetify_cache_ttl_sec",
)


_LOG = logging.getLogger("cs2watch.sources")


def _build_schema(registry: SourceRegistry | None = None) -> list:
    registry = REGISTRY if registry is None else registry
    # Core and built-in keys are fixed: ORDER must match them exactly (a mismatch is a repo bug, fail at import).
    declared: list = list(CORE_FIELDS)
    for source in registry:
        if registry.is_builtin(source.id):
            declared.extend(source.settings)
    by_name: dict = {}
    for f in declared:
        if f.name in by_name:
            raise ValueError(f"config key declared twice: {f.name}")
        by_name[f.name] = f
    if set(by_name) != set(ORDER):
        raise ValueError(f"schema/ORDER mismatch: {sorted(set(by_name) ^ set(ORDER))}")
    schema = [by_name[name] for name in ORDER]
    # Plugin keys follow ORDER in registration order. A plugin cannot break the app: a bad or clashing key is
    # skipped with a warning and its source stays registered.
    taken = set(ORDER)
    for source in registry:
        if registry.is_builtin(source.id):
            continue
        try:
            fields = list(source.settings)
        except Exception as exc:
            _LOG.warning("plugin source %s: settings unreadable, ignored: %s", source.id, exc)
            continue
        for f in fields:
            name = getattr(f, "name", "")
            if not isinstance(f, Field) or not name:
                _LOG.warning("plugin source %s: setting %r is not a Field; skipped", source.id, f)
            elif name in taken:
                _LOG.warning("plugin source %s: config key %s is already in use; skipped", source.id, name)
            else:
                taken.add(name)
                schema.append(f)
    return schema


SCHEMA: list = _build_schema()
FIELDS: dict = {f.name: f for f in SCHEMA}
SECRETS: list = [f for f in SCHEMA if isinstance(f, Secret)]


def default_config() -> dict[str, Any]:
    return {f.name: (dict(f.default) if isinstance(f.default, dict) else f.default) for f in SCHEMA}


def _finite_number(val: Any) -> float | None:
    """`val` as a float when it is a finite number or a numeric string; None otherwise (inf, nan, text, None...)."""
    try:
        num = float(val)
    except (TypeError, ValueError, OverflowError):
        return None
    return num if math.isfinite(num) else None


def clamp_float(val: Any, default: float, min_val: float, max_val: float) -> float:
    num = _finite_number(val)
    if num is None:
        return default
    return round(max(min_val, min(max_val, num)), 2)


def _normalize_field(f: Field, raw: Mapping) -> Any:
    if f.type == "float":
        return clamp_float(raw.get(f.name), f.default, f.min, f.max)
    if f.type == "int":
        # A missing/empty value still means 0 (historical behaviour); only non-numeric or non-finite values
        # fall back to the field default.
        num = _finite_number(raw.get(f.name) or 0)
        if num is None:
            return f.default
        return max(int(f.min), min(int(f.max), int(num)))
    if f.type == "bool":
        return bool(raw.get(f.name, f.default))
    if f.type == "text":
        if f.falsy_empty:
            return str(raw.get(f.name, f.default) or "").strip()
        val = raw.get(f.name, f.default)
        return str(val).strip() if val is not None else ""
    if f.type == "enum":
        value = str(raw.get(f.name, f.default) or f.default).strip().lower()
        return value if value in f.choices else f.default
    if f.type == "str":
        return str(raw.get(f.name, f.default) or f.default).strip() or f.default
    if f.type == "weights":
        raw_weights = raw.get(f.name) if isinstance(raw.get(f.name), dict) else {}
        return {k: clamp_float(raw_weights.get(k), v, f.min, f.max) for k, v in f.default.items()}
    raise ValueError(f"unknown field type {f.type!r} for {f.name}")


def normalize(raw: Mapping) -> dict[str, Any]:
    """Coerce any stored/requested mapping into a complete, clamped config (unknown keys dropped)."""
    return {f.name: _normalize_field(f, raw) for f in SCHEMA}


def apply_updates(current: Mapping, updates: Mapping) -> dict[str, Any]:
    """Merge an API update into the current config. Secrets keep their value on empty input."""
    merged = dict(current)
    for f in SCHEMA:
        if f.name not in updates:
            continue
        if isinstance(f, Secret) and f.keep_on_empty:
            new_key = str(updates.get(f.name) or "").strip()
            if updates.get(f.clear_flag):
                merged[f.name] = ""
            elif new_key:
                merged[f.name] = new_key
        else:
            merged[f.name] = updates[f.name]
    for f in SECRETS:
        if f.clear_flag and updates.get(f.clear_flag):
            merged[f.name] = ""
    return merged


def mask_key(key: str) -> str:
    if len(key) <= 6:
        return "****" if key else ""
    return f"{'*' * (len(key) - 4)}{key[-4:]}"


def public_view(cfg: Mapping, env_present: Mapping[str, bool] | None = None, *, resolved: dict | None = None,
                platform: str = "") -> dict[str, Any]:
    """The config payload returned by GET /api/config (key order is part of the contract)."""
    env_present = dict(env_present or {})
    config = dict(cfg)
    status: list[tuple[Secret, str, str]] = []
    for f in SECRETS:
        if not f.masked:
            if not f.public:
                config.pop(f.name, None)
            continue
        value = config.pop(f.name, "")
        if value:
            status.append((f, "config", mask_key(value)))
            continue
        env_hit = bool(env_present.get(f.name)) or any(os.environ.get(e, "").strip() for e in f.env)
        if env_hit:
            status.append((f, "env", MASKED_FROM_ENV))
        else:
            status.append((f, "none", ""))

    out: dict[str, Any] = {"config": config}
    for f, source, masked in status:
        out[f"has_{f.name}"] = source != "none"
        out[f"{f.name}_source"] = source
        out[f"{f.name}_masked"] = masked
    for f in SCHEMA:
        if f.allowed_key:
            out[f.allowed_key] = list(f.choices)
    out["platform"] = platform
    out["resolved"] = resolved or {}
    return out


def describe_schema() -> list[dict]:
    return [describe(f) for f in SCHEMA]
