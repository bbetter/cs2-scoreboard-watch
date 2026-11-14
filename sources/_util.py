"""Small shared helpers for source features (kept private to the sources package)."""

from __future__ import annotations

from typing import Any, Optional


def num_or_none(val: Any) -> Optional[float]:
    """Same rule as feedback_store._num_or_none: numbers only (bools excluded), rounded to 2 places."""
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    return round(float(val), 2)


def _safe_float(val: object) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        return float(str(val).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _safe_int(val: object) -> Optional[int]:
    num = _safe_float(val)
    return int(round(num)) if num is not None else None
