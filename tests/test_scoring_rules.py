"""Per-rule unit tests for scoring/rules.py, plus smurf helpers, engine modes and the breakdown contract.

Pure: synthetic Player/SteamProfile objects, base weights passed explicitly, no network, no config.json.
Thresholds come from scoring.thresholds (T.*), so boundary tests follow the table, not copied literals.
Run: /home/andrii/fun/.venv/bin/python -m unittest discover -s tests -v
"""

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vision.scoreboard_scan import Player  # noqa: E402
from scoring import engine  # noqa: E402
from scoring import thresholds as T  # noqa: E402
from scoring.rules import RULES, Hit, ScoreContext, recent_aim_hits, smurf_flags, smurf_signal_hits  # noqa: E402
from lookup.steam_lookup import SteamProfile  # noqa: E402
from unittest import mock  # noqa: E402

BASE_WEIGHTS = {"scoreboard": 0.30, "leetify": 0.40, "steam_account": 0.30}
NAN = float("nan")


# ---------------------------------------------------------------- builders

def player(**over) -> Player:
    base = dict(nick="p", kills=16, deaths=18, kd=0.89, hs=42.0, level=18, score=28.0)
    base.update(over)
    return Player(**base)


def steam(**over) -> SteamProfile:
    base = dict(
        query_nick="p", steamid64="76561190000000001", privacy_state="public", lookup_source="api",
        account_age_years=8.0, cs2_hours=2400.0,
    )
    base.update(over)
    return SteamProfile(**base)


def lf(**over) -> dict:
    base = {"total_matches": 300, "winrate": 52.0, "aim": 70.0, "reaction_time_ms": 440, "preaim": 9.0,
            "accuracy_head": 38.0, "bans": [], "stats": {}}
    base.update(over)
    return base


def ctx(p=None, s=None, unc=50.0) -> ScoreContext:
    return ScoreContext(p or player(), s or steam(), unc)


def rule_fn(component: str, name: str):
    for n, fn in RULES[component]:
        if n == name:
            return fn
    raise KeyError(f"{component}/{name} is not registered")


def run(component: str, name: str, c: ScoreContext) -> list:
    return rule_fn(component, name)(c)


def pairs(hits: list) -> list:
    return [(h.points, h.reason) for h in hits]


class RuleCase(unittest.TestCase):
    """Base for one rule. Subclasses set COMPONENT and NAME; test_registry checks every rule has one."""

    COMPONENT = ""
    NAME = ""

    def hits(self, c: ScoreContext) -> list:
        return run(self.COMPONENT, self.NAME, c)


# ---------------------------------------------------------------- scoreboard

class HighLobbyNumbers(RuleCase):
    COMPONENT, NAME = "scoreboard", "high_lobby_numbers"

    def test_not_applicable_below_threshold(self):
        self.assertEqual(self.hits(ctx(p=player(score=T.SB_HIGH_LOBBY_REASON_AT - 0.1))), [])

    def test_boundary_at_threshold_hits_with_reason_and_zero_points(self):
        h = self.hits(ctx(p=player(score=T.SB_HIGH_LOBBY_REASON_AT)))
        self.assertEqual(pairs(h), [(0.0, "Високі цифри в цьому лобі (70%)")])

    def test_fallback_lobby_formula_when_score_is_nan(self):
        # 15 + (kd-1)*12 + (hs-45)*0.7 : kd 4.0 hs 75 -> 15 + 36 + 21 = 72
        h = self.hits(ctx(p=player(score=NAN, kd=4.0, hs=75.0)))
        self.assertEqual(pairs(h), [(0.0, "Високі цифри в цьому лобі (72%)")])


class LowLevelStrongStats(RuleCase):
    COMPONENT, NAME = "scoreboard", "low_level_strong_stats"

    def test_level_above_max_is_not_applicable(self):
        self.assertEqual(self.hits(ctx(p=player(level=T.SB_LOW_LEVEL_MAX + 1, kd=3.0, hs=90.0))), [])

    def test_level_below_zero_means_unknown_and_is_not_applicable(self):
        self.assertEqual(self.hits(ctx(p=player(level=-1, kd=3.0))), [])

    def test_kd_just_below_and_at_threshold(self):
        self.assertEqual(self.hits(ctx(p=player(level=3, kd=T.SB_LOW_LEVEL_KD - 0.01, hs=10.0))), [])
        h = self.hits(ctx(p=player(level=3, kd=T.SB_LOW_LEVEL_KD, hs=10.0)))
        self.assertEqual(pairs(h), [(18.0, "Низький рівень у грі (lvl 3)")])

    def test_hs_at_threshold_alone_is_enough(self):
        h = self.hits(ctx(p=player(level=0, kd=0.5, hs=T.SB_LOW_LEVEL_HS)))
        self.assertEqual(pairs(h), [(18.0, "Низький рівень у грі (lvl 0)")])
        self.assertEqual(self.hits(ctx(p=player(level=0, kd=0.5, hs=T.SB_LOW_LEVEL_HS - 0.1))), [])


# ---------------------------------------------------------------- steam account

class VacBan(RuleCase):
    COMPONENT, NAME = "steam_account", "vac_ban"

    def test_clean_is_not_applicable(self):
        self.assertEqual(self.hits(ctx(s=steam(vac_banned=False))), [])

    def test_banned_points_and_reason(self):
        self.assertEqual(pairs(self.hits(ctx(s=steam(vac_banned=True)))), [(T.STEAM_VAC_POINTS, "VAC-бан у Steam")])


class TradeBan(RuleCase):
    COMPONENT, NAME = "steam_account", "trade_ban"

    def test_none_is_not_applicable(self):
        self.assertEqual(self.hits(ctx(s=steam(trade_ban=None))), [])

    def test_points_and_reason(self):
        self.assertEqual(pairs(self.hits(ctx(s=steam(trade_ban="banned")))),
                         [(T.STEAM_TRADE_BAN_POINTS, "Трейд-бан у Steam (banned)")])


class LimitedAccount(RuleCase):
    COMPONENT, NAME = "steam_account", "limited_account"

    def test_not_limited_is_not_applicable(self):
        self.assertEqual(self.hits(ctx(s=steam(is_limited=False))), [])

    def test_points_and_reason(self):
        self.assertEqual(pairs(self.hits(ctx(s=steam(is_limited=True)))),
                         [(T.STEAM_LIMITED_POINTS, "Обмежений акаунт Steam")])


class AccountAge(RuleCase):
    COMPONENT, NAME = "steam_account", "account_age"

    def test_unknown_age_is_not_applicable(self):
        self.assertEqual(self.hits(ctx(s=steam(account_age_years=None))), [])

    def test_new_account_just_below_one_year(self):
        age = T.STEAM_AGE_NEW_YEARS - 0.01
        self.assertEqual(pairs(self.hits(ctx(s=steam(account_age_years=age)))),
                         [(T.STEAM_AGE_NEW_POINTS, f"Новий акаунт ({age} р.)")])

    def test_at_one_year_is_young_tier(self):
        self.assertEqual(pairs(self.hits(ctx(s=steam(account_age_years=T.STEAM_AGE_NEW_YEARS)))),
                         [(T.STEAM_AGE_YOUNG_POINTS, "Молодий акаунт (1.0 р.)")])

    def test_young_tier_ends_at_two_years(self):
        self.assertEqual(len(self.hits(ctx(s=steam(account_age_years=T.STEAM_AGE_YOUNG_YEARS - 0.01)))), 1)
        self.assertEqual(self.hits(ctx(s=steam(account_age_years=T.STEAM_AGE_YOUNG_YEARS))), [])

    def test_veteran_at_six_years_is_negative_without_reason(self):
        self.assertEqual(pairs(self.hits(ctx(s=steam(account_age_years=T.STEAM_AGE_VETERAN_YEARS)))),
                         [(T.STEAM_AGE_VETERAN_POINTS, None)])
        self.assertEqual(self.hits(ctx(s=steam(account_age_years=T.STEAM_AGE_VETERAN_YEARS - 0.01))), [])


class Cs2Hours(RuleCase):
    COMPONENT, NAME = "steam_account", "cs2_hours"

    def test_unknown_hours_is_not_applicable(self):
        self.assertEqual(self.hits(ctx(s=steam(cs2_hours=None))), [])

    def test_very_low_tier(self):
        h = self.hits(ctx(s=steam(cs2_hours=T.STEAM_HOURS_VERY_LOW - 1)))
        self.assertEqual(pairs(h), [(T.STEAM_HOURS_VERY_LOW_POINTS, "Мало годин у CS2 (99 год)")])
        self.assertEqual(pairs(self.hits(ctx(s=steam(cs2_hours=T.STEAM_HOURS_VERY_LOW)))),
                         [(T.STEAM_HOURS_LOW_POINTS, "Небагато годин у CS2 (100 год)")])

    def test_low_tier_ends_at_threshold(self):
        self.assertEqual(len(self.hits(ctx(s=steam(cs2_hours=T.STEAM_HOURS_LOW - 0.1)))), 1)
        self.assertEqual(self.hits(ctx(s=steam(cs2_hours=T.STEAM_HOURS_LOW))), [])

    def test_veteran_tier_at_threshold(self):
        self.assertEqual(pairs(self.hits(ctx(s=steam(cs2_hours=T.STEAM_HOURS_VETERAN)))),
                         [(T.STEAM_HOURS_VETERAN_POINTS, None)])
        self.assertEqual(self.hits(ctx(s=steam(cs2_hours=T.STEAM_HOURS_VETERAN - 0.1))), [])


class ProfileVisibility(RuleCase):
    COMPONENT, NAME = "steam_account", "profile_visibility"

    def test_public_found_is_not_applicable(self):
        self.assertEqual(self.hits(ctx(s=steam(privacy_state="public", lookup_source="api"))), [])

    def test_private_profile(self):
        self.assertEqual(pairs(self.hits(ctx(s=steam(privacy_state="private")))),
                         [(T.STEAM_PRIVATE_POINTS, "Закритий профіль (private)")])

    def test_not_found_without_privacy_state(self):
        self.assertEqual(pairs(self.hits(ctx(s=steam(privacy_state=None, lookup_source="not_found")))),
                         [(T.STEAM_NOT_FOUND_POINTS, "Steam-профіль не знайдено")])

    def test_private_wins_over_not_found(self):
        h = self.hits(ctx(s=steam(privacy_state="private", lookup_source="not_found")))
        self.assertEqual(len(h), 1)
        self.assertEqual(h[0].points, T.STEAM_PRIVATE_POINTS)


# ---------------------------------------------------------------- external

class PlatformBans(RuleCase):
    COMPONENT, NAME = "external", "platform_bans"

    def test_no_leetify_is_not_applicable(self):
        self.assertEqual(self.hits(ctx(s=steam(leetify=None))), [])

    def test_empty_bans_is_not_applicable(self):
        self.assertEqual(self.hits(ctx(s=steam(leetify=lf(bans=[])))), [])

    def test_bans_points_and_count_in_reason(self):
        h = self.hits(ctx(s=steam(leetify=lf(bans=[{"platform": "faceit"}, {"platform": "steam"}]))))
        self.assertEqual(pairs(h), [(T.EXT_BAN_POINTS, "Бани на Leetify/платформах (2)")])


class LeetifyReactionTime(RuleCase):
    COMPONENT, NAME = "external", "leetify_reaction_time"

    def _rt(self, rt, high_elo=False):
        faceit = {"skill_level": 8, "found": True} if high_elo else None
        return self.hits(ctx(s=steam(leetify=lf(reaction_time_ms=rt), faceit=faceit or {})))

    def test_missing_reaction_is_not_applicable(self):
        self.assertEqual(self._rt(None), [])

    def test_superhuman_tier_boundary(self):
        self.assertEqual(pairs(self._rt(T.RT_SUPERHUMAN_MS - 1)),
                         [(T.RT_SUPERHUMAN_POINTS, f"Надлюдська реакція Leetify ({T.RT_SUPERHUMAN_MS - 1} мс)")])
        self.assertEqual(pairs(self._rt(T.RT_SUPERHUMAN_MS)),
                         [(T.RT_VERY_FAST_POINTS, f"Дуже швидка реакція ({T.RT_SUPERHUMAN_MS} мс)")])

    def test_very_fast_tier_boundary(self):
        self.assertEqual(pairs(self._rt(T.RT_VERY_FAST_MS - 1))[0][0], T.RT_VERY_FAST_POINTS)
        self.assertEqual(pairs(self._rt(T.RT_VERY_FAST_MS)),
                         [(T.RT_FAST_POINTS, f"Швидка реакція ({T.RT_VERY_FAST_MS} мс)")])

    def test_fast_tier_boundary_and_end(self):
        self.assertEqual(len(self._rt(T.RT_FAST_MS - 1)), 1)
        self.assertEqual(self._rt(T.RT_FAST_MS), [])

    def test_fast_tier_is_skipped_for_high_elo_but_superhuman_is_not(self):
        self.assertEqual(self._rt(T.RT_VERY_FAST_MS, high_elo=True), [])  # fast tier gated by high_elo
        self.assertEqual(len(self._rt(T.RT_SUPERHUMAN_MS - 1, high_elo=True)), 1)  # superhuman is not gated


class LeetifyAim(RuleCase):
    COMPONENT, NAME = "external", "leetify_aim"

    def _aim(self, aim, matches, high_elo=False):
        faceit = {"skill_level": 8, "found": True} if high_elo else {}
        return self.hits(ctx(s=steam(leetify=lf(aim=aim, total_matches=matches), faceit=faceit)))

    def test_none_aim_is_not_applicable(self):
        self.assertEqual(self._aim(None, 10), [])

    def test_new_account_tier_needs_both_high_aim_and_few_matches(self):
        self.assertEqual(pairs(self._aim(T.AIM_NEW_ACCOUNT_MIN, T.AIM_NEW_ACCOUNT_MATCHES - 1)),
                         [(T.AIM_NEW_ACCOUNT_POINTS, f"Aim {T.AIM_NEW_ACCOUNT_MIN} при {T.AIM_NEW_ACCOUNT_MATCHES - 1} матчах")])

    def test_new_account_tier_boundary_on_matches_falls_to_very_high_tier(self):
        self.assertEqual(pairs(self._aim(T.AIM_NEW_ACCOUNT_MIN, T.AIM_NEW_ACCOUNT_MATCHES)),
                         [(T.AIM_VERY_HIGH_POINTS, f"Дуже високий Aim ({T.AIM_NEW_ACCOUNT_MIN})")])

    def test_very_high_tier_boundary(self):
        self.assertEqual(pairs(self._aim(T.AIM_VERY_HIGH_MIN, 500)),
                         [(T.AIM_VERY_HIGH_POINTS, f"Дуже високий Aim ({T.AIM_VERY_HIGH_MIN})")])
        self.assertEqual(self._aim(T.AIM_VERY_HIGH_MIN - 0.1, 500), [])

    def test_very_high_tier_is_gated_by_high_elo_but_new_account_tier_is_not(self):
        self.assertEqual(self._aim(T.AIM_VERY_HIGH_MIN, 500, high_elo=True), [])
        self.assertEqual(len(self._aim(T.AIM_NEW_ACCOUNT_MIN, 10, high_elo=True)), 1)


class LeetifyWinrate(RuleCase):
    COMPONENT, NAME = "external", "leetify_winrate"

    def _wr(self, winrate, matches):
        return self.hits(ctx(s=steam(leetify=lf(winrate=winrate, total_matches=matches))))

    def test_too_few_matches_is_not_applicable(self):
        self.assertEqual(self._wr(90, T.WINRATE_MIN_MATCHES - 1), [])

    def test_min_matches_is_inclusive(self):
        self.assertEqual(pairs(self._wr(T.WINRATE_EXTREME, T.WINRATE_MIN_MATCHES)),
                         [(T.WINRATE_EXTREME_POINTS, f"Вінрейт {T.WINRATE_EXTREME}% за {T.WINRATE_MIN_MATCHES} матчів")])

    def test_extreme_tier_needs_matches_below_limit(self):
        self.assertEqual(pairs(self._wr(T.WINRATE_EXTREME, T.WINRATE_EXTREME_MATCHES - 1))[0][0], T.WINRATE_EXTREME_POINTS)
        # at the matches limit the extreme tier stops, and the high tier also needs fewer matches -> nothing
        self.assertEqual(self._wr(T.WINRATE_EXTREME, T.WINRATE_EXTREME_MATCHES), [])

    def test_high_tier_boundary(self):
        self.assertEqual(pairs(self._wr(T.WINRATE_HIGH, T.WINRATE_HIGH_MATCHES - 1)),
                         [(T.WINRATE_HIGH_POINTS, f"Високий вінрейт {T.WINRATE_HIGH}% ({T.WINRATE_HIGH_MATCHES - 1} матчів)")])
        self.assertEqual(self._wr(T.WINRATE_HIGH - 1, T.WINRATE_HIGH_MATCHES - 1), [])
        self.assertEqual(self._wr(T.WINRATE_HIGH, T.WINRATE_HIGH_MATCHES), [])

    def test_none_winrate_is_not_applicable(self):
        self.assertEqual(self._wr(None, 50), [])


class LeetifyPreaim(RuleCase):
    COMPONENT, NAME = "external", "leetify_preaim"

    def _pa(self, preaim, matches, high_elo=False):
        faceit = {"skill_level": 8, "found": True} if high_elo else {}
        return self.hits(ctx(s=steam(leetify=lf(preaim=preaim, total_matches=matches), faceit=faceit)))

    def test_below_max_degrees_and_few_matches(self):
        self.assertEqual(pairs(self._pa(T.PREAIM_MAX_DEG - 0.1, T.PREAIM_MAX_MATCHES - 1)),
                         [(T.PREAIM_POINTS, f"Низький preaim ({T.PREAIM_MAX_DEG - 0.1}°) на новому акаунті")])

    def test_boundaries_are_exclusive_on_degrees_and_matches(self):
        self.assertEqual(self._pa(T.PREAIM_MAX_DEG, 10), [])
        self.assertEqual(self._pa(1.0, T.PREAIM_MAX_MATCHES), [])

    def test_gated_by_high_elo(self):
        self.assertEqual(self._pa(1.0, 10, high_elo=True), [])

    def test_none_preaim_is_not_applicable(self):
        self.assertEqual(self._pa(None, 10), [])


class LeetifyVeteranClean(RuleCase):
    COMPONENT, NAME = "external", "leetify_veteran_clean"

    def _vet(self, matches, winrate, bans=()):
        return self.hits(ctx(s=steam(leetify=lf(total_matches=matches, winrate=winrate, bans=list(bans)))))

    def test_matches_boundary(self):
        self.assertEqual(pairs(self._vet(T.LEETIFY_VETERAN_MATCHES, 50)), [(T.LEETIFY_VETERAN_POINTS, None)])
        self.assertEqual(self._vet(T.LEETIFY_VETERAN_MATCHES - 1, 50), [])

    def test_winrate_boundary_is_inclusive(self):
        self.assertEqual(len(self._vet(T.LEETIFY_VETERAN_MATCHES, T.LEETIFY_VETERAN_MAX_WINRATE)), 1)
        self.assertEqual(self._vet(T.LEETIFY_VETERAN_MATCHES, T.LEETIFY_VETERAN_MAX_WINRATE + 0.1), [])

    def test_none_winrate_counts_as_clean(self):
        self.assertEqual(len(self._vet(T.LEETIFY_VETERAN_MATCHES, None)), 1)

    def test_any_ban_blocks_it(self):
        self.assertEqual(self._vet(T.LEETIFY_VETERAN_MATCHES, 50, bans=[{"x": 1}]), [])


class RecentAimWindow(RuleCase):
    COMPONENT, NAME = "external", "recent_aim_window"

    def _ext(self, aim_reactions=None):
        return {"cstracker": {"status": "ok", "sections": {"aim_reactions": aim_reactions or {}}}}

    def test_strong_counter_strafe_boundary(self):
        h = self.hits(ctx(s=steam(leetify=lf(stats={"counter_strafing_good_shots_ratio": T.AIM_COUNTER_STRAFE_STRONG}))))
        self.assertEqual(pairs(h), [(T.AIM_COUNTER_STRAFE_POINTS[0], "Counter-strafing 95% (Leetify, недавні матчі)")])

    def test_weak_counter_strafe_tier(self):
        h = self.hits(ctx(s=steam(leetify=lf(stats={"counter_strafing_good_shots_ratio": 91.0}))))
        self.assertEqual(pairs(h), [(T.AIM_COUNTER_STRAFE_POINTS[1], "Високий counter-strafing 91% (Leetify, недавні матчі)")])

    def test_below_weak_counter_strafe_is_nothing(self):
        self.assertEqual(self.hits(ctx(s=steam(leetify=lf(stats={"counter_strafing_good_shots_ratio": T.AIM_COUNTER_STRAFE_WEAK - 0.1})))), [])

    def test_head_accuracy_uses_flat_leetify_key(self):
        h = self.hits(ctx(s=steam(leetify=lf(accuracy_head=T.AIM_ACCURACY_HEAD_STRONG))))
        self.assertEqual(pairs(h), [(T.AIM_ACCURACY_HEAD_POINTS[0], "Точність у голову 50% (Leetify, недавні матчі)")])

    def test_spray_falls_back_to_cstracker_when_leetify_missing(self):
        h = self.hits(ctx(s=steam(leetify=None, extra_stats=self._ext({"Spray accuracy": T.AIM_SPRAY_STRONG}))))
        self.assertEqual(pairs(h), [(T.AIM_SPRAY_POINTS[0], "Точність спрею 55% (cstracker, недавні матчі)")])

    def test_spot_to_kill_strong_and_weak(self):
        h = self.hits(ctx(s=steam(leetify=lf(reaction_time_ms=None), extra_stats=self._ext({"Spot to kill": 249}))))
        self.assertEqual(pairs(h), [(T.AIM_SPOT_TO_KILL_POINTS[0], "Spot-to-kill 249 мс (cstracker, недавні матчі)")])
        h = self.hits(ctx(s=steam(leetify=lf(reaction_time_ms=None), extra_stats=self._ext({"Spot to kill": 250}))))
        self.assertEqual(pairs(h), [(T.AIM_SPOT_TO_KILL_POINTS[1], "Швидкий spot-to-kill 250 мс (cstracker, недавні матчі)")])

    def test_spot_to_kill_ignored_when_leetify_has_reaction_time(self):
        h = self.hits(ctx(s=steam(leetify=lf(reaction_time_ms=400), extra_stats=self._ext({"Spot to kill": 100}))))
        self.assertEqual(h, [])

    def test_gated_by_high_elo(self):
        c = ctx(s=steam(leetify=lf(accuracy_head=99.0), faceit={"skill_level": 8, "found": True}))
        self.assertEqual(self.hits(c), [])

    def test_cstracker_not_ok_contributes_nothing(self):
        ext = {"cstracker": {"status": "blocked", "sections": {"aim_reactions": {"Spot to kill": 100}}}}
        self.assertEqual(self.hits(ctx(s=steam(leetify=None, extra_stats=ext))), [])


class FaceitVeteranClean(RuleCase):
    COMPONENT, NAME = "external", "faceit_veteran_clean"

    def _fc(self, **over):
        base = {"found": True, "matches": T.FACEIT_VETERAN_MATCHES, "kd": T.FACEIT_VETERAN_MAX_KD,
                "hs_pct": T.FACEIT_VETERAN_MAX_HS}
        base.update(over)
        return self.hits(ctx(s=steam(faceit=base)))

    def test_not_found_is_not_applicable_even_with_matching_numbers(self):
        self.assertEqual(self._fc(found=False), [])

    def test_all_boundaries_inclusive(self):
        self.assertEqual(pairs(self._fc()), [(T.FACEIT_VETERAN_POINTS, None)])

    def test_each_limit_is_exclusive_above(self):
        self.assertEqual(self._fc(matches=T.FACEIT_VETERAN_MATCHES - 1), [])
        self.assertEqual(self._fc(kd=T.FACEIT_VETERAN_MAX_KD + 0.01), [])
        self.assertEqual(self._fc(hs_pct=T.FACEIT_VETERAN_MAX_HS + 0.1), [])

    def test_none_kd_and_hs_count_as_clean(self):
        self.assertEqual(len(self._fc(kd=None, hs_pct=None)), 1)


class SteamCs2Stats(RuleCase):
    COMPONENT, NAME = "external", "steam_cs2_stats"

    def _cs(self, **over):
        base = {"total_matches_played": 100, "kd": 1.0, "hs_pct": 40.0}
        base.update(over)
        return self.hits(ctx(s=steam(cs2_stats=base)))

    def test_empty_cs2_is_not_applicable(self):
        self.assertEqual(self.hits(ctx(s=steam(cs2_stats={}))), [])

    def test_extreme_kd_boundary_and_matches_limit(self):
        self.assertEqual(pairs(self._cs(kd=T.CS2_KD_EXTREME, total_matches_played=T.CS2_KD_EXTREME_MAX_MATCHES - 1)),
                         [(T.CS2_KD_EXTREME_POINTS, f"Високий загальний K/D у CS2 ({T.CS2_KD_EXTREME})")])
        self.assertEqual(self._cs(kd=T.CS2_KD_EXTREME, total_matches_played=T.CS2_KD_EXTREME_MAX_MATCHES), [])
        self.assertEqual(self._cs(kd=T.CS2_KD_EXTREME - 0.01, total_matches_played=10), [])

    def test_extreme_hs_boundary(self):
        self.assertEqual(pairs(self._cs(hs_pct=T.CS2_HS_EXTREME)),
                         [(T.CS2_HS_EXTREME_POINTS, f"Високий загальний HS% у CS2 ({T.CS2_HS_EXTREME}%)")])
        self.assertEqual(self._cs(hs_pct=T.CS2_HS_EXTREME - 0.1), [])

    def test_both_extreme_tiers_report_in_kd_then_hs_order(self):
        h = self._cs(kd=2.5, hs_pct=80.0, total_matches_played=50)
        self.assertEqual([p for p, _ in pairs(h)], [T.CS2_KD_EXTREME_POINTS, T.CS2_HS_EXTREME_POINTS])

    def test_veteran_tier_boundary(self):
        self.assertEqual(pairs(self._cs(total_matches_played=T.CS2_VETERAN_MATCHES, kd=T.CS2_VETERAN_MAX_KD)),
                         [(T.CS2_VETERAN_POINTS, None)])
        self.assertEqual(self._cs(total_matches_played=T.CS2_VETERAN_MATCHES - 1, kd=0.5), [])
        self.assertEqual(self._cs(total_matches_played=T.CS2_VETERAN_MATCHES, kd=T.CS2_VETERAN_MAX_KD + 0.01), [])


# ---------------------------------------------------------------- registry coverage

class RegistryCoverageTests(unittest.TestCase):
    def test_every_registered_rule_has_a_test_case_and_nothing_extra(self):
        registered = {(component, name) for component in RULES for name, _fn in RULES[component]}
        tested = {(c.COMPONENT, c.NAME) for c in RuleCase.__subclasses__()}
        self.assertEqual(tested, registered, "a rule was added or removed without updating its test case")

    def test_each_rule_case_has_at_least_one_test(self):
        for case in RuleCase.__subclasses__():
            tests = [m for m in dir(case) if m.startswith("test_")]
            self.assertTrue(tests, f"{case.__name__} has no test methods")

    def test_rule_names_are_unique_per_component(self):
        for component, entries in RULES.items():
            names = [n for n, _ in entries]
            self.assertEqual(len(names), len(set(names)), component)


# ---------------------------------------------------------------- smurf helpers

class SmurfFlagTests(unittest.TestCase):
    def _flags(self, p=None, s=None):
        return smurf_flags(ctx(p=p, s=s))

    def test_account_age_boundaries(self):
        self.assertTrue(self._flags(s=steam(account_age_years=T.SMURF_YOUNG_STRONG_YEARS - 0.01)).young_strong)
        f = self._flags(s=steam(account_age_years=T.SMURF_YOUNG_STRONG_YEARS))
        self.assertFalse(f.young_strong)
        self.assertTrue(f.young_mod)
        f = self._flags(s=steam(account_age_years=T.SMURF_YOUNG_MOD_YEARS - 0.01))
        self.assertTrue(f.young_mod)
        f = self._flags(s=steam(account_age_years=T.SMURF_YOUNG_MOD_YEARS))
        self.assertFalse(f.young_mod)
        self.assertFalse(f.young_strong)

    def test_unknown_age_sets_no_young_flags(self):
        f = self._flags(s=steam(account_age_years=None))
        self.assertFalse(f.young_strong or f.young_mod)

    def test_hours_boundaries(self):
        self.assertTrue(self._flags(s=steam(cs2_hours=T.SMURF_LOW_HOURS_STRONG - 0.1)).low_hrs_strong)
        f = self._flags(s=steam(cs2_hours=T.SMURF_LOW_HOURS_STRONG))
        self.assertFalse(f.low_hrs_strong)
        self.assertTrue(f.low_hrs_mod)
        self.assertTrue(self._flags(s=steam(cs2_hours=T.SMURF_LOW_HOURS_MOD - 0.1)).low_hrs_mod)
        self.assertFalse(self._flags(s=steam(cs2_hours=T.SMURF_LOW_HOURS_MOD)).low_hrs_mod)

    def test_low_level_boundary(self):
        self.assertTrue(self._flags(p=player(level=T.SMURF_LOW_LEVEL_MAX)).low_lvl)
        self.assertFalse(self._flags(p=player(level=T.SMURF_LOW_LEVEL_MAX + 1)).low_lvl)
        self.assertFalse(self._flags(p=player(level=-1)).low_lvl)

    def test_lobby_perf_needs_kd_and_kills_or_kd_alone(self):
        self.assertTrue(self._flags(p=player(kd=T.SMURF_LOBBY_KD, kills=T.SMURF_LOBBY_KILLS)).high_lobby_perf)
        self.assertFalse(self._flags(p=player(kd=T.SMURF_LOBBY_KD, kills=T.SMURF_LOBBY_KILLS - 1)).high_lobby_perf)
        self.assertTrue(self._flags(p=player(kd=T.SMURF_LOBBY_KD_ALONE, kills=1)).high_lobby_perf)
        self.assertFalse(self._flags(p=player(kd=T.SMURF_LOBBY_KD_ALONE - 0.01, kills=1)).high_lobby_perf)

    def test_faceit_level_or_elo(self):
        self.assertTrue(self._flags(s=steam(faceit={"skill_level": T.SMURF_FACEIT_LEVEL})).high_faceit_perf)
        self.assertTrue(self._flags(s=steam(faceit={"faceit_elo": T.SMURF_FACEIT_ELO})).high_faceit_perf)
        self.assertFalse(self._flags(s=steam(faceit={"skill_level": T.SMURF_FACEIT_LEVEL - 1,
                                                      "faceit_elo": T.SMURF_FACEIT_ELO - 1})).high_faceit_perf)

    def test_leetify_aim_or_winrate_strictly_above(self):
        self.assertTrue(self._flags(s=steam(leetify=lf(aim=T.SMURF_LEETIFY_AIM, winrate=0))).high_leetify_perf)
        self.assertFalse(self._flags(s=steam(leetify=lf(aim=T.SMURF_LEETIFY_AIM - 1, winrate=0))).high_leetify_perf)
        self.assertFalse(self._flags(s=steam(leetify=lf(aim=0, winrate=T.SMURF_LEETIFY_WINRATE))).high_leetify_perf)
        self.assertTrue(self._flags(s=steam(leetify=lf(aim=0, winrate=T.SMURF_LEETIFY_WINRATE + 0.1))).high_leetify_perf)

    def test_cs2_kd_boundary_requires_cs2_stats(self):
        self.assertTrue(self._flags(s=steam(cs2_stats={"kd": T.SMURF_CS2_KD})).high_cs2_perf)
        self.assertFalse(self._flags(s=steam(cs2_stats={"kd": T.SMURF_CS2_KD - 0.01})).high_cs2_perf)
        self.assertFalse(self._flags(s=steam(cs2_stats={})).high_cs2_perf)

    def test_account_signal_properties(self):
        f = self._flags(s=steam(account_age_years=2.0, cs2_hours=200.0))
        self.assertTrue(f.account_signal_young)
        self.assertTrue(f.account_signal_hours)


class SmurfSignalHitsTests(unittest.TestCase):
    def test_all_branches_in_historical_order(self):
        c = ctx(
            p=player(level=2, kd=2.0, kills=20),
            s=steam(account_age_years=0.5, cs2_hours=100.0, is_limited=True, leetify=lf(aim=80.0),
                    faceit={"skill_level": 9, "faceit_elo": 1900}, cs2_stats={"kd": 1.5}),
        )
        f = smurf_flags(c)
        got = smurf_signal_hits(c, f, recent_elite=True)
        elite = "Смурф-сигнал: елітний аім у недавніх матчах (Leetify/cstracker)"
        expected = [
            (T.SMURF_YOUNG_STRONG_POINTS, None),
            (T.SMURF_LOW_HOURS_STRONG_POINTS, None),
            (T.SMURF_LOW_LEVEL_POINTS, None),
            (T.SMURF_LIMITED_POINTS, None),
            (T.SMURF_PERF_LOBBY_POINTS, None),
            (T.SMURF_PERF_LEETIFY_POINTS, None),
            (T.SMURF_PERF_FACEIT_POINTS, None),
            (T.SMURF_PERF_CS2_POINTS, None),
            (T.SMURF_SIGNAL_BONUS, None),
            (T.AIM_ELITE_SMURF_BONUS, elite),
            (0.0, "Смурф-сигнал: молодий акаунт + високий K/D"),
            (0.0, "Смурф-сигнал: мало годин у CS2 + сильна гра"),
            (0.0, "Смурф-сигнал: високий FACEIT (lvl 9) на новому акаунті"),
        ]
        self.assertEqual(pairs(got), expected)

    def test_mod_tiers_and_no_elite_flag(self):
        c = ctx(p=player(level=20, kd=2.0, kills=20), s=steam(account_age_years=2.5, cs2_hours=200.0))
        got = smurf_signal_hits(c, smurf_flags(c), recent_elite=False)
        self.assertEqual(pairs(got), [
            (T.SMURF_YOUNG_MOD_POINTS, None),
            (T.SMURF_LOW_HOURS_MOD_POINTS, None),
            (T.SMURF_PERF_LOBBY_POINTS, None),
            (T.SMURF_SIGNAL_BONUS, None),
            (0.0, "Смурф-сигнал: молодий акаунт + високий K/D"),
            (0.0, "Смурф-сигнал: мало годин у CS2 + сильна гра"),
        ])

    def test_faceit_reason_needs_account_signal(self):
        c = ctx(p=player(level=20, kd=1.0, kills=5), s=steam(account_age_years=8.0, cs2_hours=2000.0,
                                                             faceit={"skill_level": 9}))
        got = smurf_signal_hits(c, smurf_flags(c), recent_elite=False)
        self.assertFalse(any(h.reason and "FACEIT" in h.reason for h in got))


# ---------------------------------------------------------------- engine: smurf modes

class SmurfModeTests(unittest.TestCase):
    def _smurf(self, c, unc=50.0):
        return engine._smurf(c, unc, [], [])

    def test_banned_gives_fixed_value(self):
        self.assertEqual(self._smurf(ctx(s=steam(vac_banned=True))), T.SMURF_BANNED_VALUE)

    def test_leetify_ban_also_counts_as_ban(self):
        self.assertEqual(self._smurf(ctx(s=steam(leetify=lf(bans=[{"x": 1}])))), T.SMURF_BANNED_VALUE)

    def test_rage_hs_low_level(self):
        c = ctx(p=player(hs=T.SMURF_RAGE_HS + 1, level=T.SMURF_RAGE_MAX_LEVEL, kd=1.0))
        self.assertEqual(self._smurf(c), T.SMURF_RAGE_VALUE)

    def test_rage_hs_needs_hs_strictly_above_and_low_level(self):
        self.assertEqual(self._smurf(ctx(p=player(hs=T.SMURF_RAGE_HS, level=1, kd=1.0, kills=5))), T.SMURF_BASE)
        self.assertEqual(self._smurf(ctx(p=player(hs=90.0, level=T.SMURF_RAGE_MAX_LEVEL + 1, kd=1.0, kills=5))), T.SMURF_BASE)

    def test_signal_branch_sums_points_and_bonus(self):
        # young_strong (26) + low_lvl (14) + lobby perf (20) + bonus (8) + base 5 = 73
        c = ctx(p=player(level=3, kd=2.0, kills=20), s=steam(account_age_years=0.5, cs2_hours=2000.0))
        self.assertEqual(self._smurf(c), 5.0 + T.SMURF_YOUNG_STRONG_POINTS + T.SMURF_LOW_LEVEL_POINTS
                         + T.SMURF_PERF_LOBBY_POINTS + T.SMURF_SIGNAL_BONUS)
        self.assertEqual(self._smurf(c), 73.0)

    def test_signal_without_strong_performance_is_not_the_signal_branch(self):
        c = ctx(p=player(level=20, kd=0.8, kills=5), s=steam(account_age_years=0.5, cs2_hours=2000.0))
        self.assertEqual(self._smurf(c), T.SMURF_BASE)

    def test_strong_performance_only_depends_on_uncertainty(self):
        c = ctx(p=player(level=20, kd=2.0, kills=20), s=steam(account_age_years=5.0, cs2_hours=2000.0))
        self.assertEqual(self._smurf(c, unc=T.SMURF_STRONG_PERF_UNC), T.SMURF_STRONG_PERF_ONLY[0])
        self.assertEqual(self._smurf(c, unc=T.SMURF_STRONG_PERF_UNC - 0.1), T.SMURF_STRONG_PERF_ONLY[1])

    def test_no_signal_no_performance_is_base(self):
        c = ctx(p=player(level=20, kd=1.0, kills=10, hs=30.0), s=steam(account_age_years=5.0, cs2_hours=2000.0))
        self.assertEqual(self._smurf(c), 5.0)

    def test_smurf_reasons_and_breakdown_come_from_the_signal_branch(self):
        c = ctx(p=player(level=3, kd=2.0, kills=20), s=steam(account_age_years=0.5, cs2_hours=2000.0))
        reasons, breakdown = [], []
        engine._smurf(c, 50.0, reasons, breakdown)
        self.assertEqual(reasons, ["Смурф-сигнал: молодий акаунт + високий K/D"])
        # young_strong, low_lvl, lobby perf, +8 bonus (points) and the young-account reason line (0 points)
        self.assertEqual([b["rule"] for b in breakdown], ["signal"] * 5)
        self.assertEqual([b["points"] for b in breakdown][-1], 0.0)

    def test_result_is_clamped_to_table_range(self):
        c = ctx(p=player(level=1, kd=3.0, kills=50), s=steam(account_age_years=0.1, cs2_hours=1.0, is_limited=True,
                                                             leetify=lf(aim=99, winrate=90),
                                                             faceit={"skill_level": 10, "faceit_elo": 3000},
                                                             cs2_stats={"kd": 3.0}))
        reasons = []
        val = engine._smurf(c, 50.0, reasons, [])
        self.assertLessEqual(val, T.SMURF_MAX)
        self.assertGreater(val, 90.0)


# ---------------------------------------------------------------- engine: cheat post-processing

class CheatPostProcessingTests(unittest.TestCase):
    def _raw(self, r, weights=None):
        w = weights or r.effective_weights
        cs = r.component_scores
        return w["scoreboard"] * cs["scoreboard"] + w["steam_account"] * cs["steam_account"] + w["leetify"] * cs["external"]

    def test_ban_floor_lifts_low_score_to_78(self):
        p = player(kd=0.5, hs=10.0, score=5.0)
        r = engine.compute_scores(p, steam(vac_banned=True), BASE_WEIGHTS)
        self.assertLess(self._raw(r), T.CHEAT_BAN_FLOOR)
        self.assertEqual(r.cheat_probability, T.CHEAT_BAN_FLOOR)
        self.assertIn({"component": "cheat", "rule": "ban_floor", "points": None, "reason": None}, r.breakdown)

    def test_ban_floor_does_not_lower_a_higher_score(self):
        p = player(kd=3.0, hs=60.0, score=100.0)
        r = engine.compute_scores(p, steam(vac_banned=True, account_age_years=0.5, cs2_hours=10.0,
                                           leetify=lf(reaction_time_ms=200, aim=99.0, total_matches=20)), BASE_WEIGHTS)
        self.assertGreater(r.cheat_probability, T.CHEAT_BAN_FLOOR)

    def test_damping_starts_at_uncertainty_70_and_scales_linearly(self):
        # no steam at all -> uncertainty 100, weights (1, 0, 0); cheat = scoreboard component
        p = player(kd=2.0, kills=20, hs=40.0, level=20, score=50.0)
        s = steam(steamid64=None, lookup_source="none", account_age_years=None, cs2_hours=None, privacy_state=None)
        r = engine.compute_scores(p, s, BASE_WEIGHTS)
        self.assertEqual(r.uncertainty, 100.0)
        raw = r.component_scores["scoreboard"]  # weight 1.0
        self.assertEqual(r.cheat_probability, round(raw * (1.0 - T.DAMP_MAX), 1))
        self.assertIn("uncertainty_damping", [b["rule"] for b in r.breakdown])

    def test_no_damping_below_threshold_uncertainty(self):
        # public steam only -> uncertainty 65, below 70: no damping entry
        r = engine.compute_scores(player(), steam(), BASE_WEIGHTS)
        self.assertLess(r.uncertainty, T.DAMP_START_UNCERTAINTY)
        self.assertNotIn("uncertainty_damping", [b["rule"] for b in r.breakdown])

    def test_real_inputs_never_reach_the_68_cap(self):
        # Documents the finding: scoreboard tops out at 85 and damping at uncertainty 100 is x0.75,
        # so with real weights the cap never binds (85 * 0.75 = 63.75 < 68).
        p = player(kd=2.0, kills=20, hs=40.0, level=20, score=100.0)
        s = steam(steamid64=None, lookup_source="none", account_age_years=None, cs2_hours=None, privacy_state=None)
        r = engine.compute_scores(p, s, BASE_WEIGHTS)
        self.assertEqual(r.uncertainty, 100.0)
        self.assertEqual(r.cheat_probability, 63.8)
        self.assertLess(r.cheat_probability, T.DAMP_CAP)

    def test_damping_cap_branch_with_mocked_weights(self):
        # Inflate the scoreboard weight so the raw score exceeds the cap; the cap branch must then bind.
        p = player(kd=2.0, kills=20, hs=40.0, level=20, score=100.0)  # sb 85
        s = steam(steamid64=None, lookup_source="none", account_age_years=None, cs2_hours=None, privacy_state=None)
        weights = {"scoreboard": 1.5, "leetify": 0.0, "steam_account": 0.0}
        with mock.patch.object(engine, "compute_uncertainty_and_weights", return_value=(100.0, weights)):
            r = engine.compute_scores(p, s, BASE_WEIGHTS)
        self.assertEqual(r.cheat_probability, T.DAMP_CAP)

    def test_clamp_maximum_is_99_when_raw_is_huge(self):
        p = player(kd=2.0, kills=20, hs=40.0, level=20, score=100.0)
        weights = {"scoreboard": 2.0, "leetify": 0.0, "steam_account": 0.0}
        with mock.patch.object(engine, "compute_uncertainty_and_weights", return_value=(0.0, weights)):
            r = engine.compute_scores(p, steam(), BASE_WEIGHTS)
        self.assertEqual(r.cheat_probability, T.CHEAT_MAX)

    def test_extreme_rage_skips_damping(self):
        p = player(kd=T.EXTREME_RAGE_KD, hs=T.EXTREME_RAGE_HS, kills=25, deaths=8, level=20, score=50.0)
        s = steam(steamid64=None, lookup_source="none", account_age_years=None, cs2_hours=None, privacy_state=None)
        r = engine.compute_scores(p, s, BASE_WEIGHTS)
        self.assertEqual(r.uncertainty, 100.0)
        self.assertNotIn("uncertainty_damping", [b["rule"] for b in r.breakdown])
        self.assertEqual(r.cheat_probability, round(r.component_scores["scoreboard"], 1))

    def test_extreme_rage_needs_both_kd_and_hs(self):
        p = player(kd=T.EXTREME_RAGE_KD, hs=T.EXTREME_RAGE_HS - 1, level=20, score=50.0)
        s = steam(steamid64=None, lookup_source="none", account_age_years=None, cs2_hours=None, privacy_state=None)
        r = engine.compute_scores(p, s, BASE_WEIGHTS)
        self.assertIn("uncertainty_damping", [b["rule"] for b in r.breakdown])

    def test_clamp_minimum_is_1(self):
        p = player(kd=0.5, hs=10.0, score=0.0, level=20)
        s = steam(
            account_age_years=8.0, cs2_hours=2000.0,
            leetify=lf(total_matches=400, winrate=50.0, bans=[]),
            faceit={"found": True, "matches": 300, "kd": 1.0, "hs_pct": 40.0},
            cs2_stats={"total_matches_played": 400, "kd": 1.0},
        )
        r = engine.compute_scores(p, s, BASE_WEIGHTS)
        self.assertEqual(r.uncertainty, 0.0)
        self.assertEqual(r.cheat_probability, T.CHEAT_MIN)

    def test_risk_levels_at_boundaries(self):
        self.assertEqual(engine.risk_level(T.RISK_LEVELS[0][0]), "critical")
        self.assertEqual(engine.risk_level(T.RISK_LEVELS[0][0] - 0.1), "high")
        self.assertEqual(engine.risk_level(T.RISK_LEVELS[1][0]), "high")
        self.assertEqual(engine.risk_level(T.RISK_LEVELS[1][0] - 0.1), "medium")
        self.assertEqual(engine.risk_level(T.RISK_LEVELS[2][0]), "medium")
        self.assertEqual(engine.risk_level(T.RISK_LEVELS[2][0] - 0.1), T.RISK_DEFAULT)


# ---------------------------------------------------------------- uncertainty and effective weights

class UncertaintyWeightTests(unittest.TestCase):
    def _uw(self, s):
        return engine.compute_uncertainty_and_weights(s, BASE_WEIGHTS)

    def test_no_steam_and_no_external_is_fully_scoreboard(self):
        u, w = self._uw(steam(steamid64=None))
        self.assertEqual(u, 100.0)
        self.assertEqual(w, {"scoreboard": 1.0, "leetify": 0.0, "steam_account": 0.0})

    def test_private_steam_uses_partial_presence(self):
        u, w = self._uw(steam(privacy_state="private"))
        self.assertEqual(u, round(100.0 - T.UNC_STEAM * T.HAS_STEAM_NOT_PUBLIC, 1))
        self.assertEqual(u, 84.2)
        self.assertEqual(w, {"scoreboard": 0.69, "leetify": 0.0, "steam_account": 0.31})

    def test_public_steam_alone(self):
        u, w = self._uw(steam())
        self.assertEqual(u, 65.0)
        self.assertEqual(w, {"scoreboard": 0.5, "leetify": 0.0, "steam_account": 0.5})

    def test_leetify_alone_adds_its_uncertainty_and_weight(self):
        u, w = self._uw(steam(leetify=lf()))
        self.assertEqual(u, 100.0 - T.UNC_STEAM - T.UNC_LEETIFY)
        self.assertEqual(w, {"scoreboard": 0.3, "leetify": 0.4, "steam_account": 0.3})

    def test_faceit_alone(self):
        u, w = self._uw(steam(faceit={"found": True}))
        self.assertEqual(u, 100.0 - T.UNC_STEAM - T.UNC_FACEIT)
        self.assertEqual(w, {"scoreboard": 0.3, "leetify": 0.4, "steam_account": 0.3})

    def test_faceit_not_found_is_no_source(self):
        u, _ = self._uw(steam(faceit={"found": False}))
        self.assertEqual(u, 100.0 - T.UNC_STEAM)

    def test_cstracker_ok_alone(self):
        u, w = self._uw(steam(extra_stats={"cstracker": {"status": "ok"}}))
        self.assertEqual(u, 100.0 - T.UNC_STEAM - T.UNC_CSTRACKER)
        self.assertEqual(w, {"scoreboard": 0.3, "leetify": 0.4, "steam_account": 0.3})

    def test_cstracker_blocked_counts_as_nothing(self):
        u, w = self._uw(steam(extra_stats={"cstracker": {"status": "blocked"}}))
        self.assertEqual(u, 100.0 - T.UNC_STEAM)
        self.assertEqual(w, {"scoreboard": 0.5, "leetify": 0.0, "steam_account": 0.5})

    def test_only_cs2_stats_is_partial_external_presence(self):
        u, w = self._uw(steam(cs2_stats={"kd": 1.0}))
        self.assertEqual(u, 100.0 - T.UNC_STEAM - T.UNC_CS2_STATS)
        # external raw = 0.4 * 0.5 = 0.2 ; total = 0.3 + 0.3 + 0.2
        self.assertEqual(w, {"scoreboard": 0.375, "leetify": 0.25, "steam_account": 0.375})

    def test_cs2_stats_without_steam_id(self):
        u, w = self._uw(steam(steamid64=None, cs2_stats={"kd": 1.0}))
        self.assertEqual(u, 100.0 - T.UNC_CS2_STATS)
        self.assertEqual(w, {"scoreboard": 0.6, "leetify": 0.4, "steam_account": 0.0})

    def test_leetify_without_steam_id(self):
        u, w = self._uw(steam(steamid64=None, leetify=lf()))
        self.assertEqual(u, 100.0 - T.UNC_LEETIFY)
        self.assertEqual(w, {"scoreboard": 0.429, "leetify": 0.571, "steam_account": 0.0})  # rounded to 3 places

    def test_all_base_weights_zero_falls_back_to_scoreboard(self):
        u, w = engine.compute_uncertainty_and_weights(steam(), {"scoreboard": 0, "leetify": 0, "steam_account": 0})
        self.assertEqual(w, {"scoreboard": 1.0, "leetify": 0.0, "steam_account": 0.0})

    def test_full_stack_uncertainty_is_clamped_at_zero(self):
        s = steam(leetify=lf(), faceit={"found": True}, cs2_stats={"kd": 1.0},
                  extra_stats={"cstracker": {"status": "ok"}})
        u, _ = self._uw(s)
        self.assertEqual(u, max(0.0, 100.0 - T.UNC_STEAM - T.UNC_LEETIFY - T.UNC_FACEIT - T.UNC_CS2_STATS - T.UNC_CSTRACKER))


# ---------------------------------------------------------------- breakdown contract

class BreakdownTests(unittest.TestCase):
    def _fixture(self):
        p = player(kd=2.0, kills=20, hs=40.0, level=20, score=75.0)
        s = steam(account_age_years=1.5, cs2_hours=500.0,
                  leetify=lf(reaction_time_ms=300, aim=96.0, total_matches=100))
        return p, s

    def test_additive_component_sums_to_component_score(self):
        p, s = self._fixture()
        r = engine.compute_scores(p, s, BASE_WEIGHTS)
        bases = {
            "scoreboard": ctx(p=p, s=s).lobby_raw * T.SB_LOBBY_FACTOR,
            "steam_account": T.STEAM_BASE,
            "external": T.EXT_BASE,
        }
        for component, base in bases.items():
            with self.subTest(component=component):
                total = base + sum(e["points"] for e in r.breakdown
                                   if e["component"] == component and e["points"] is not None)
                self.assertTrue(0.0 <= total <= 100.0, "fixture must stay inside the clamp range")
                self.assertAlmostEqual(r.component_scores[component], total, places=9)

    def test_every_entry_has_the_four_keys(self):
        p, s = self._fixture()
        r = engine.compute_scores(p, s, BASE_WEIGHTS)
        self.assertTrue(r.breakdown)
        for entry in r.breakdown:
            self.assertEqual(set(entry), {"component", "rule", "points", "reason"})
            self.assertIn(entry["component"], {"scoreboard", "steam_account", "external", "smurf", "cheat"})

    def test_reasons_equal_non_none_breakdown_reasons_in_order(self):
        p, s = self._fixture()
        r = engine.compute_scores(p, s, BASE_WEIGHTS)
        fallback = "Зовнішні та лобі-показники в нормі"
        from_breakdown = [e["reason"] for e in r.breakdown if e["reason"] is not None]
        self.assertEqual([x for x in r.reasons if x != fallback], from_breakdown)
        self.assertIn("Високі цифри в цьому лобі (75%)", r.reasons)

    def test_fallback_reason_only_when_nothing_else_applies(self):
        r = engine.compute_scores(player(score=10.0, kd=0.8, hs=20.0, level=20), steam(), BASE_WEIGHTS)
        self.assertEqual(r.reasons, ["Зовнішні та лобі-показники в нормі"])
        self.assertEqual([e["reason"] for e in r.breakdown if e["reason"] is not None], [])

    def test_every_registered_rule_name_is_used_only_as_declared(self):
        p, s = self._fixture()
        r = engine.compute_scores(p, s, BASE_WEIGHTS)
        declared = {(c, n) for c in RULES for n, _ in RULES[c]}
        for e in r.breakdown:
            if e["component"] in RULES:
                self.assertIn((e["component"], e["rule"]), declared)


if __name__ == "__main__":
    unittest.main()
