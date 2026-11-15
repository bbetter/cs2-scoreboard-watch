"""Rule-based scoring: thresholds.py (numbers), rules.py (one function per rule), engine.py (assembly)."""

from scoring.engine import (  # noqa: F401
    ScoreResult,
    compute_scores,
    compute_uncertainty_and_weights,
    risk_level,
)
from scoring.rules import Hit, recent_aim_signals  # noqa: F401
