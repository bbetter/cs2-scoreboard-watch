"""Rule-based scoring: thresholds.py (numbers), rules.py (one function per rule), engine.py (assembly)."""

from scoring.engine import ScoreResult, compute_scores, compute_uncertainty_and_weights, risk_level  # noqa: F401
from scoring.rules import Hit, recent_aim_signals  # noqa: F401
