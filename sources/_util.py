"""Small shared helpers for source features (kept private to the sources package)."""

from __future__ import annotations

from typing import Any, Optional


def num_or_none(val: Any) -> Optional[float]:
    """Same rule as feedback_store._num_or_none: numbers only (bools excluded), rounded to 2 places."""
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    return round(float(val), 2)
