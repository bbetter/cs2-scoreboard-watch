"""Declarative config field descriptors: `Field` for plain settings, `Secret` for API keys.

Kept dependency-free so that sources/ can declare their own settings without importing config_schema
(which imports the source registry).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Field:
    name: str
    type: str  # "float" | "int" | "bool" | "text" | "enum" | "str" | "weights"
    default: Any
    label: str = ""
    group: str = "general"
    help: str = ""
    min: float | None = None
    max: float | None = None
    choices: tuple = ()
    # Public-view key that lists the choices (e.g. "allowed_ai_providers"); empty = not listed.
    allowed_key: str = ""
    # text only: how None/falsy values become "" (see config_schema._normalize_field).
    falsy_empty: bool = False
    # The `public` flag: whether the value is part of the config returned by the API.
    public: bool = True


@dataclass(frozen=True)
class Secret(Field):
    """An API key. Shown to the UI only as source/has/masked, unless `masked` is False (raw in config)."""

    env: tuple = ()  # environment variable names that act as a fallback
    # Empty input keeps the stored key, and `clear_<name>` removes it. False = empty input clears it.
    keep_on_empty: bool = True
    masked: bool = True

    @property
    def clear_flag(self) -> str | None:
        return f"clear_{self.name}" if self.keep_on_empty else None


def describe(f: Field) -> dict:
    """JSON-able descriptor for the UI. Never contains a value other than the static default."""
    out = {
        "name": f.name,
        "type": f.type,
        "label": f.label or f.name,
        "group": f.group,
        "help": f.help,
        "default": dict(f.default) if isinstance(f.default, dict) else f.default,
        "secret": isinstance(f, Secret),
    }
    if f.min is not None:
        out["min"] = f.min
    if f.max is not None:
        out["max"] = f.max
    if f.choices:
        out["choices"] = list(f.choices)
    if isinstance(f, Secret):
        out["env"] = list(f.env)
        out["clear_flag"] = f.clear_flag
    return out
