"""Scoring rules. One small function per rule, registered into a component with @rule.

A rule receives a ScoreContext and returns a list of Hit(points, reason). A rule that does not
apply returns []. Rules of a component run in registration order; their points are summed onto the
component base and their reasons are appended to the report in that same order.

To add a rule: write a function below, decorate it with @rule("<component>", "<name>"), add its
numbers to thresholds.py and a unit test in tests/test_scoring_rules.py.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from scoring import thresholds as T
from sources._util import round_num


@dataclass
class Hit:
    points: float
    reason: str | None = None


class ScoreContext:
    """Everything a rule may look at, derived once. `player`/`steam` are Player / SteamProfile."""

    def __init__(self, player: Any, steam: Any, uncertainty: float = 50.0) -> None:
        self.player = player
        self.steam = steam
        self.uncertainty = uncertainty
        self.lf: dict | None = steam.leetify
        self.faceit: dict = steam.faceit or {}
        self.cs2: dict = steam.cs2_stats or {}
        lf = self.lf
        self.faceit_lvl = self.faceit.get("skill_level") or (lf.get("faceit") if lf else None) or 0
        self.faceit_elo = self.faceit.get("faceit_elo") or (lf.get("faceit_elo") if lf else None) or 0
        self.premier = (lf.get("premier") if lf else None) or 0
        self.high_elo = (
            self.faceit_lvl >= T.HIGH_ELO_FACEIT_LEVEL
            or self.faceit_elo >= T.HIGH_ELO_FACEIT_ELO
            or self.premier >= T.HIGH_ELO_PREMIER
        )
        self.lf_bans: list = (lf.get("bans") or []) if lf else []
        self.faceit_bans: list = self.faceit.get("active_bans") or []
        self.has_ban = bool(steam.vac_banned or self.lf_bans)
        self._recent: list[Hit] | None = None

    @property
    def lobby_raw(self) -> float:
        p = self.player
        if not math.isnan(p.score):
            return float(p.score)
        return min(
            100.0,
            T.LOBBY_FALLBACK_BASE
            + max(0.0, (p.kd - T.LOBBY_FALLBACK_KD_PIVOT) * T.LOBBY_FALLBACK_KD_MULT)
            + max(0.0, (p.hs - T.LOBBY_FALLBACK_HS_PIVOT) * T.LOBBY_FALLBACK_HS_MULT),
        )

    @property
    def recent_hits(self) -> list[Hit]:
        if self._recent is None:
            self._recent = recent_aim_hits(self.lf, self.steam.extra_stats, self.high_elo)
        return self._recent


Rule = Callable[[ScoreContext], list]
COMPONENTS = ("scoreboard", "steam_account", "external")
RULES: dict[str, list[tuple[str, Rule]]] = {c: [] for c in COMPONENTS}


def rule(component: str, name: str) -> Callable[[Rule], Rule]:
    def deco(fn: Rule) -> Rule:
        RULES[component].append((name, fn))
        return fn

    return deco


# ============================================================ recent-window aim (Leetify / cstracker)

def _pick_recent_metric(
    lf: dict | None, cst_sections: dict, leetify_key: str, cst_key: str | None = None
) -> tuple[float | None, str]:
    """Return (value, source) for one metric: Leetify first, cstracker as fallback (no double counting)."""
    if lf:
        raw = lf.get(leetify_key)
        if raw is None:
            raw = (lf.get("stats") or {}).get(leetify_key)
        val = round_num(raw, 1)
        if val is not None:
            return float(val), "Leetify"
    if cst_key:
        val = (cst_sections.get("aim_reactions") or {}).get(cst_key)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return float(val), "cstracker"
    return None, ""


def recent_aim_hits(lf: dict | None, extra_stats: dict, high_elo: bool) -> list[Hit]:
    """Hits for recent-window aim metrics. Only adds points; missing data contributes nothing.

    High-ELO players are skipped, matching the reaction/aim rules. Lifetime Steam cs2_stats are not used.
    """
    cst = (extra_stats or {}).get("cstracker") or {}
    cst_sections = (cst.get("sections") or {}) if cst.get("status") == "ok" else {}
    hits: list[Hit] = []
    if high_elo:
        return hits

    def tiered(val, src, strong, weak, pts, strong_msg, weak_msg):
        if val is None:
            return
        if val >= strong:
            hits.append(Hit(pts[0], f"{strong_msg} {val:.0f}% ({src}, недавні матчі)"))
        elif val >= weak:
            hits.append(Hit(pts[1], f"{weak_msg} {val:.0f}% ({src}, недавні матчі)"))

    v, s = _pick_recent_metric(lf, cst_sections, "counter_strafing_good_shots_ratio", "Counter-strafing")
    tiered(v, s, T.AIM_COUNTER_STRAFE_STRONG, T.AIM_COUNTER_STRAFE_WEAK, T.AIM_COUNTER_STRAFE_POINTS,
           "Counter-strafing", "Високий counter-strafing")
    v, s = _pick_recent_metric(lf, cst_sections, "accuracy_head")
    tiered(v, s, T.AIM_ACCURACY_HEAD_STRONG, T.AIM_ACCURACY_HEAD_WEAK, T.AIM_ACCURACY_HEAD_POINTS,
           "Точність у голову", "Висока точність у голову")
    v, s = _pick_recent_metric(lf, cst_sections, "spray_accuracy", "Spray accuracy")
    tiered(v, s, T.AIM_SPRAY_STRONG, T.AIM_SPRAY_WEAK, T.AIM_SPRAY_POINTS,
           "Точність спрею", "Висока точність спрею")

    # Spot-to-kill is a fallback for reaction time only when Leetify reaction_time_ms is absent.
    if not (lf and lf.get("reaction_time_ms") is not None):
        stk = (cst_sections.get("aim_reactions") or {}).get("Spot to kill")
        if isinstance(stk, (int, float)) and not isinstance(stk, bool) and stk > 0:
            if stk < T.AIM_SPOT_TO_KILL_MS_STRONG:
                hits.append(Hit(T.AIM_SPOT_TO_KILL_POINTS[0], f"Spot-to-kill {stk:.0f} мс (cstracker, недавні матчі)"))
            elif stk < T.AIM_SPOT_TO_KILL_MS_WEAK:
                hits.append(Hit(T.AIM_SPOT_TO_KILL_POINTS[1], f"Швидкий spot-to-kill {stk:.0f} мс (cstracker, недавні матчі)"))
    return hits


def recent_aim_signals(lf: dict | None, extra_stats: dict, high_elo: bool) -> tuple[float, bool, list[str]]:
    """Legacy tuple form: (points, elite_recent flag, reasons)."""
    points = 0.0
    reasons: list[str] = []
    for h in recent_aim_hits(lf, extra_stats, high_elo):
        points += h.points
        if h.reason:
            reasons.append(h.reason)
    return points, points >= T.AIM_ELITE_RECENT_POINTS, reasons


# ============================================================ scoreboard component

@rule("scoreboard", "high_lobby_numbers")
def _high_lobby(c: ScoreContext) -> list:
    if c.lobby_raw >= T.SB_HIGH_LOBBY_REASON_AT:
        return [Hit(0.0, f"Високі цифри в цьому лобі ({c.lobby_raw:.0f}%)")]
    return []


@rule("scoreboard", "low_level_strong_stats")
def _low_level(c: ScoreContext) -> list:
    p = c.player
    if 0 <= p.level <= T.SB_LOW_LEVEL_MAX and (p.kd >= T.SB_LOW_LEVEL_KD or p.hs >= T.SB_LOW_LEVEL_HS):
        return [Hit(T.SB_LOW_LEVEL_POINTS, f"Низький рівень у грі (lvl {p.level})")]
    return []


# ============================================================ steam account component

@rule("steam_account", "vac_ban")
def _vac(c: ScoreContext) -> list:
    return [Hit(T.STEAM_VAC_POINTS, "VAC-бан у Steam")] if c.steam.vac_banned else []


@rule("steam_account", "trade_ban")
def _trade(c: ScoreContext) -> list:
    tb = c.steam.trade_ban
    return [Hit(T.STEAM_TRADE_BAN_POINTS, f"Трейд-бан у Steam ({tb})")] if tb else []


@rule("steam_account", "limited_account")
def _limited(c: ScoreContext) -> list:
    return [Hit(T.STEAM_LIMITED_POINTS, "Обмежений акаунт Steam")] if c.steam.is_limited else []


@rule("steam_account", "account_age")
def _age(c: ScoreContext) -> list:
    age = c.steam.account_age_years
    if age is None:
        return []
    if age < T.STEAM_AGE_NEW_YEARS:
        return [Hit(T.STEAM_AGE_NEW_POINTS, f"Новий акаунт ({age} р.)")]
    if age < T.STEAM_AGE_YOUNG_YEARS:
        return [Hit(T.STEAM_AGE_YOUNG_POINTS, f"Молодий акаунт ({age} р.)")]
    if age >= T.STEAM_AGE_VETERAN_YEARS:
        return [Hit(T.STEAM_AGE_VETERAN_POINTS)]
    return []


@rule("steam_account", "cs2_hours")
def _hours(c: ScoreContext) -> list:
    h = c.steam.cs2_hours
    if h is None:
        return []
    if h < T.STEAM_HOURS_VERY_LOW:
        return [Hit(T.STEAM_HOURS_VERY_LOW_POINTS, f"Мало годин у CS2 ({h:.0f} год)")]
    if h < T.STEAM_HOURS_LOW:
        return [Hit(T.STEAM_HOURS_LOW_POINTS, f"Небагато годин у CS2 ({h:.0f} год)")]
    if h >= T.STEAM_HOURS_VETERAN:
        return [Hit(T.STEAM_HOURS_VETERAN_POINTS)]
    return []


@rule("steam_account", "profile_visibility")
def _visibility(c: ScoreContext) -> list:
    s = c.steam
    if s.privacy_state and s.privacy_state != "public":
        return [Hit(T.STEAM_PRIVATE_POINTS, f"Закритий профіль ({s.privacy_state})")]
    if s.lookup_source == "not_found":
        return [Hit(T.STEAM_NOT_FOUND_POINTS, "Steam-профіль не знайдено")]
    return []


# ============================================================ external component

@rule("external", "platform_bans")
def _ext_bans(c: ScoreContext) -> list:
    if c.lf and c.lf_bans:
        return [Hit(T.EXT_BAN_POINTS, f"Бани на Leetify/платформах ({len(c.lf_bans)})")]
    return []


@rule("external", "faceit_active_ban")
def _ext_faceit_ban(c: ScoreContext) -> list:
    # Leetify's bans list can already carry the FACEIT ban (platform "faceit"); count it once, in platform_bans.
    if c.lf_bans or not c.faceit_bans:
        return []
    reason = str(c.faceit_bans[0].get("reason") or "").strip() or "без причини"
    return [Hit(T.EXT_FACEIT_BAN_POINTS, f"Активний бан на FACEIT: {reason}")]


@rule("external", "leetify_reaction_time")
def _reaction(c: ScoreContext) -> list:
    rt = c.lf.get("reaction_time_ms") if c.lf else None
    if rt is None:
        return []
    if rt < T.RT_SUPERHUMAN_MS:
        return [Hit(T.RT_SUPERHUMAN_POINTS, f"Надлюдська реакція Leetify ({rt} мс)")]
    if rt < T.RT_VERY_FAST_MS:
        return [Hit(T.RT_VERY_FAST_POINTS, f"Дуже швидка реакція ({rt} мс)")]
    if rt < T.RT_FAST_MS and not c.high_elo:
        return [Hit(T.RT_FAST_POINTS, f"Швидка реакція ({rt} мс)")]
    return []


@rule("external", "leetify_aim")
def _aim(c: ScoreContext) -> list:
    if not c.lf:
        return []
    aim = c.lf.get("aim")
    matches = c.lf.get("total_matches") or 0
    if aim is None:
        return []
    if aim >= T.AIM_NEW_ACCOUNT_MIN and matches < T.AIM_NEW_ACCOUNT_MATCHES:
        return [Hit(T.AIM_NEW_ACCOUNT_POINTS, f"Aim {aim} при {matches} матчах")]
    if aim >= T.AIM_VERY_HIGH_MIN and not c.high_elo:
        return [Hit(T.AIM_VERY_HIGH_POINTS, f"Дуже високий Aim ({aim})")]
    return []


@rule("external", "leetify_winrate")
def _winrate(c: ScoreContext) -> list:
    if not c.lf:
        return []
    winrate = c.lf.get("winrate")
    matches = c.lf.get("total_matches") or 0
    if winrate is None or matches < T.WINRATE_MIN_MATCHES:
        return []
    if winrate >= T.WINRATE_EXTREME and matches < T.WINRATE_EXTREME_MATCHES:
        return [Hit(T.WINRATE_EXTREME_POINTS, f"Вінрейт {winrate}% за {matches} матчів")]
    if winrate >= T.WINRATE_HIGH and matches < T.WINRATE_HIGH_MATCHES:
        return [Hit(T.WINRATE_HIGH_POINTS, f"Високий вінрейт {winrate}% ({matches} матчів)")]
    return []


@rule("external", "leetify_preaim")
def _preaim(c: ScoreContext) -> list:
    if not c.lf:
        return []
    preaim = c.lf.get("preaim")
    matches = c.lf.get("total_matches") or 0
    if preaim is not None and preaim < T.PREAIM_MAX_DEG and matches < T.PREAIM_MAX_MATCHES and not c.high_elo:
        return [Hit(T.PREAIM_POINTS, f"Низький preaim ({preaim}°) на новому акаунті")]
    return []


@rule("external", "leetify_veteran_clean")
def _lf_veteran(c: ScoreContext) -> list:
    if not c.lf:
        return []
    matches = c.lf.get("total_matches") or 0
    winrate = c.lf.get("winrate")
    if matches >= T.LEETIFY_VETERAN_MATCHES and (winrate is None or winrate <= T.LEETIFY_VETERAN_MAX_WINRATE) and not c.lf_bans:
        return [Hit(T.LEETIFY_VETERAN_POINTS)]
    return []


@rule("external", "recent_aim_window")
def _recent(c: ScoreContext) -> list:
    return list(c.recent_hits)


@rule("external", "faceit_veteran_clean")
def _faceit_veteran(c: ScoreContext) -> list:
    f = c.faceit
    if not f.get("found"):
        return []
    f_matches = f.get("matches") or 0
    f_kd, f_hs = f.get("kd"), f.get("hs_pct")
    if f_matches >= T.FACEIT_VETERAN_MATCHES and (f_kd is None or f_kd <= T.FACEIT_VETERAN_MAX_KD) and (
        f_hs is None or f_hs <= T.FACEIT_VETERAN_MAX_HS
    ):
        return [Hit(T.FACEIT_VETERAN_POINTS)]
    return []


@rule("external", "steam_cs2_stats")
def _cs2(c: ScoreContext) -> list:
    cs2 = c.cs2
    if not cs2:
        return []
    hits: list[Hit] = []
    c_matches = cs2.get("total_matches_played") or 0
    c_kd, c_hs = cs2.get("kd"), cs2.get("hs_pct")
    if c_kd is not None and c_kd >= T.CS2_KD_EXTREME and c_matches < T.CS2_KD_EXTREME_MAX_MATCHES:
        hits.append(Hit(T.CS2_KD_EXTREME_POINTS, f"Високий загальний K/D у CS2 ({c_kd})"))
    if c_hs is not None and c_hs >= T.CS2_HS_EXTREME:
        hits.append(Hit(T.CS2_HS_EXTREME_POINTS, f"Високий загальний HS% у CS2 ({c_hs}%)"))
    if c_matches >= T.CS2_VETERAN_MATCHES and (c_kd is None or c_kd <= T.CS2_VETERAN_MAX_KD):
        hits.append(Hit(T.CS2_VETERAN_POINTS))
    return hits


@rule("external", "cstracker_trust")
def _cstracker_trust(c: ScoreContext) -> list:
    cst = (c.steam.extra_stats or {}).get("cstracker") or {}
    trust = cst.get("trust")
    if cst.get("status") != "ok" or isinstance(trust, bool) or not isinstance(trust, (int, float)):
        return []
    if trust < T.CST_TRUST_VERY_LOW:
        return [Hit(T.CST_TRUST_VERY_LOW_POINTS, f"Дуже низький CSTracker trust ({trust:.0f})")]
    if trust < T.CST_TRUST_LOW:
        return [Hit(T.CST_TRUST_LOW_POINTS, f"Низький CSTracker trust ({trust:.0f})")]
    return []


# ============================================================ smurf signal rules (used inside the signal branch)

@dataclass
class SmurfFlags:
    young_strong: bool
    young_mod: bool
    low_hrs_strong: bool
    low_hrs_mod: bool
    low_lvl: bool
    high_lobby_perf: bool
    high_leetify_perf: bool
    high_faceit_perf: bool
    high_cs2_perf: bool

    @property
    def account_signal_young(self) -> bool:
        return self.young_strong or self.young_mod

    @property
    def account_signal_hours(self) -> bool:
        return self.low_hrs_strong or self.low_hrs_mod


def smurf_flags(c: ScoreContext) -> SmurfFlags:
    s, p = c.steam, c.player
    age, hrs = s.account_age_years, s.cs2_hours
    return SmurfFlags(
        young_strong=age is not None and age < T.SMURF_YOUNG_STRONG_YEARS,
        young_mod=age is not None and T.SMURF_YOUNG_STRONG_YEARS <= age < T.SMURF_YOUNG_MOD_YEARS,
        low_hrs_strong=hrs is not None and hrs < T.SMURF_LOW_HOURS_STRONG,
        low_hrs_mod=hrs is not None and T.SMURF_LOW_HOURS_STRONG <= hrs < T.SMURF_LOW_HOURS_MOD,
        low_lvl=0 <= p.level <= T.SMURF_LOW_LEVEL_MAX,
        high_lobby_perf=(p.kd >= T.SMURF_LOBBY_KD and p.kills >= T.SMURF_LOBBY_KILLS) or p.kd >= T.SMURF_LOBBY_KD_ALONE,
        high_leetify_perf=bool(c.lf and ((c.lf.get("aim") or 0) >= T.SMURF_LEETIFY_AIM or (c.lf.get("winrate") or 0) > T.SMURF_LEETIFY_WINRATE)),
        high_faceit_perf=bool(c.faceit_lvl >= T.SMURF_FACEIT_LEVEL or c.faceit_elo >= T.SMURF_FACEIT_ELO),
        high_cs2_perf=bool(c.cs2 and (c.cs2.get("kd") or 0) >= T.SMURF_CS2_KD),
    )


def smurf_signal_hits(c: ScoreContext, f: SmurfFlags, recent_elite: bool) -> list[Hit]:
    """Additive hits of the 'account smurf signal AND strong performance' branch, in historical order."""
    hits: list[Hit] = []
    if f.young_strong:
        hits.append(Hit(T.SMURF_YOUNG_STRONG_POINTS))
    elif f.young_mod:
        hits.append(Hit(T.SMURF_YOUNG_MOD_POINTS))
    if f.low_hrs_strong:
        hits.append(Hit(T.SMURF_LOW_HOURS_STRONG_POINTS))
    elif f.low_hrs_mod:
        hits.append(Hit(T.SMURF_LOW_HOURS_MOD_POINTS))
    if f.low_lvl:
        hits.append(Hit(T.SMURF_LOW_LEVEL_POINTS))
    if c.steam.is_limited:
        hits.append(Hit(T.SMURF_LIMITED_POINTS))
    if f.high_lobby_perf:
        hits.append(Hit(T.SMURF_PERF_LOBBY_POINTS))
    if f.high_leetify_perf:
        hits.append(Hit(T.SMURF_PERF_LEETIFY_POINTS))
    if f.high_faceit_perf:
        hits.append(Hit(T.SMURF_PERF_FACEIT_POINTS))
    if f.high_cs2_perf:
        hits.append(Hit(T.SMURF_PERF_CS2_POINTS))
    hits.append(Hit(T.SMURF_SIGNAL_BONUS))
    if recent_elite:
        hits.append(Hit(T.AIM_ELITE_SMURF_BONUS, "Смурф-сигнал: елітний аім у недавніх матчах (Leetify/cstracker)"))
    if f.account_signal_young:
        hits.append(Hit(0.0, "Смурф-сигнал: молодий акаунт + високий K/D"))
    if f.account_signal_hours:
        hits.append(Hit(0.0, "Смурф-сигнал: мало годин у CS2 + сильна гра"))
    if f.high_faceit_perf and (f.account_signal_young or f.account_signal_hours):
        hits.append(Hit(0.0, f"Смурф-сигнал: високий FACEIT (lvl {c.faceit_lvl}) на новому акаунті"))
    return hits
