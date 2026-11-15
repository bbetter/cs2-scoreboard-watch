"""Small shared helpers for source features (kept private to the sources package)."""

from __future__ import annotations

from typing import Any


def num_or_none(val: Any) -> float | None:
    """Same rule as feedback_store._num_or_none: numbers only (bools excluded), rounded to 2 places."""
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    return round(float(val), 2)


def round_num(val: Any, nd: int | None = None) -> Any | None:
    """Number rounding for source payloads: bools and non-numbers give None; nd=0 gives int, nd=None keeps float."""
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    if nd == 0:
        return int(round(val))
    return round(float(val), nd) if nd is not None else float(val)


def _safe_float(val: object) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(str(val).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _safe_int(val: object) -> int | None:
    num = _safe_float(val)
    return int(round(num)) if num is not None else None
