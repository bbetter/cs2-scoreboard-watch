"""Scoring engine: runs the registered rules, assembles cheat/smurf probabilities and a breakdown.

Behaviour is frozen by tests/golden/scoring_golden.json. The breakdown lists every rule hit with its
component, rule name, points and reason, so a report can explain exactly where a score came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from scoring import thresholds as T
from scoring.rules import RULES, ScoreContext, smurf_flags, smurf_signal_hits


def risk_level(prob: float) -> str:
    for min_prob, label in T.RISK_LEVELS:
        if prob >= min_prob:
            return label
    return T.RISK_DEFAULT


def compute_uncertainty_and_weights(steam: Any, base: dict[str, float]) -> tuple[float, dict[str, float]]:
    """Uncertainty (0..100) and effective component weights given which data the profile actually has."""
    if steam.steamid64:
        has_steam = T.HAS_STEAM_PUBLIC if steam.privacy_state == "public" else T.HAS_STEAM_NOT_PUBLIC
    else:
        has_steam = 0.0

    has_faceit = bool(steam.faceit and steam.faceit.get("found"))
    has_leetify = bool(steam.leetify)
    has_cs2_stats = bool(steam.cs2_stats)
    has_extra = bool(((steam.extra_stats or {}).get("cstracker") or {}).get("status") == "ok")

    if has_leetify or has_faceit or has_extra:
        ext_presence = 1.0
    elif has_cs2_stats:
        ext_presence = T.EXT_PRESENCE_PARTIAL
    else:
        ext_presence = 0.0

    raw_sb = base["scoreboard"] * 1.0
    raw_steam = base["steam_account"] * has_steam
    raw_ext = base["leetify"] * ext_presence
    total = raw_sb + raw_steam + raw_ext

    if (has_steam == 0.0 and ext_presence == 0.0) or total <= 0.0:
        effective = {"scoreboard": 1.0, "leetify": 0.0, "steam_account": 0.0}
    else:
        effective = {
            "scoreboard": round(raw_sb / total, 3),
            "leetify": round(raw_ext / total, 3),
            "steam_account": round(raw_steam / total, 3),
        }

    unc = 100.0
    unc -= T.UNC_STEAM * has_steam
    if has_leetify:
        unc -= T.UNC_LEETIFY
    if has_faceit:
        unc -= T.UNC_FACEIT
    if has_cs2_stats:
        unc -= T.UNC_CS2_STATS
    if has_extra:
        unc -= T.UNC_CSTRACKER
    return round(max(0.0, min(100.0, unc)), 1), effective


@dataclass
class ScoreResult:
    cheat_probability: float
    cheat_risk: str
    smurf_probability: float
    smurf_risk: str
    uncertainty: float
    effective_weights: dict[str, float]
    reasons: list[str]
    breakdown: list[dict] = field(default_factory=list)
    component_scores: dict[str, float] = field(default_factory=dict)

    def as_tuple(self) -> tuple:
        return (
            self.cheat_probability, self.cheat_risk, self.smurf_probability, self.smurf_risk,
            self.uncertainty, self.effective_weights, self.reasons,
        )


def _run_component(component: str, base: float, ctx: ScoreContext, reasons: list[str], breakdown: list[dict]) -> float:
    score = base
    for name, fn in RULES[component]:
        for hit in fn(ctx):
            score += hit.points
            if hit.reason:
                reasons.append(hit.reason)
            breakdown.append({"component": component, "rule": name, "points": hit.points, "reason": hit.reason})
    return max(0.0, min(100.0, score))


def compute_scores(player: Any, steam: Any, base_weights: dict[str, float]) -> ScoreResult:
    reasons: list[str] = []
    breakdown: list[dict] = []
    uncertainty, weights = compute_uncertainty_and_weights(steam, base_weights)
    ctx = ScoreContext(player, steam, uncertainty)

    sb_score = _run_component("scoreboard", ctx.lobby_raw * T.SB_LOBBY_FACTOR, ctx, reasons, breakdown)
    steam_score = _run_component("steam_account", T.STEAM_BASE, ctx, reasons, breakdown)
    ext_score = _run_component("external", T.EXT_BASE, ctx, reasons, breakdown)

    cheat = (
        weights["scoreboard"] * sb_score
        + weights["steam_account"] * steam_score
        + weights["leetify"] * ext_score
    )
    has_extreme_rage = player.kd >= T.EXTREME_RAGE_KD and player.hs >= T.EXTREME_RAGE_HS
    if ctx.has_ban:
        cheat = max(cheat, T.CHEAT_BAN_FLOOR)
        breakdown.append({"component": "cheat", "rule": "ban_floor", "points": None, "reason": None})
    elif not has_extreme_rage and uncertainty >= T.DAMP_START_UNCERTAINTY:
        damp = 1.0 - T.DAMP_MAX * ((uncertainty - T.DAMP_START_UNCERTAINTY) / T.DAMP_RANGE)
        cheat *= damp
        if uncertainty >= T.DAMP_CAP_UNCERTAINTY:
            cheat = min(cheat, T.DAMP_CAP)
        breakdown.append({"component": "cheat", "rule": "uncertainty_damping", "points": None, "reason": None})
    cheat = max(T.CHEAT_MIN, min(T.CHEAT_MAX, round(cheat, 1)))

    smurf = _smurf(ctx, uncertainty, reasons, breakdown)

    if not reasons:
        reasons.append("Зовнішні та лобі-показники в нормі")

    return ScoreResult(
        cheat_probability=cheat, cheat_risk=risk_level(cheat),
        smurf_probability=smurf, smurf_risk=risk_level(smurf),
        uncertainty=uncertainty, effective_weights=weights, reasons=reasons, breakdown=breakdown,
        component_scores={"scoreboard": sb_score, "steam_account": steam_score, "external": ext_score},
    )


def _smurf(ctx: ScoreContext, uncertainty: float, reasons: list[str], breakdown: list[dict]) -> float:
    steam, player = ctx.steam, ctx.player
    f = smurf_flags(ctx)
    account_signal = (
        f.young_strong or f.young_mod or f.low_hrs_strong or f.low_hrs_mod or f.low_lvl or bool(steam.is_limited)
    )
    strong_perf = f.high_lobby_perf or f.high_leetify_perf or f.high_faceit_perf or f.high_cs2_perf
    rage_hs = player.hs > T.SMURF_RAGE_HS and 0 <= player.level <= T.SMURF_RAGE_MAX_LEVEL

    prob = T.SMURF_BASE
    if ctx.has_ban:
        prob = T.SMURF_BANNED_VALUE
        breakdown.append({"component": "smurf", "rule": "banned", "points": None, "reason": None})
    elif rage_hs:
        prob = T.SMURF_RAGE_VALUE
        breakdown.append({"component": "smurf", "rule": "rage_hs_low_level", "points": None, "reason": None})
    elif account_signal and strong_perf:
        recent_elite = sum(h.points for h in ctx.recent_hits) >= T.AIM_ELITE_RECENT_POINTS
        for hit in smurf_signal_hits(ctx, f, recent_elite):
            prob += hit.points
            if hit.reason:
                reasons.append(hit.reason)
            breakdown.append({"component": "smurf", "rule": "signal", "points": hit.points, "reason": hit.reason})
    elif strong_perf:
        prob = T.SMURF_STRONG_PERF_ONLY[0] if uncertainty >= T.SMURF_STRONG_PERF_UNC else T.SMURF_STRONG_PERF_ONLY[1]
        breakdown.append({"component": "smurf", "rule": "strong_performance_only", "points": None, "reason": None})
    return max(T.SMURF_MIN, min(T.SMURF_MAX, round(prob, 1)))
