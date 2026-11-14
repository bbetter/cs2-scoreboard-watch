"""Unit tests for recent-window aim signals in steam_lookup._compute_scores (F4).

Synthetic profiles only; no network. Thresholds are heuristics (see lookup/steam_lookup.py).
Run: /home/andrii/fun/.venv/bin/python -m unittest discover -s tests -t . -v
"""

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vision.scoreboard_scan import Player  # noqa: E402
from lookup.steam_lookup import (  # noqa: E402
    AIM_ELITE_RECENT_POINTS,
    SteamProfile,
    _compute_scores,
    _recent_aim_signals,
)


def clean_player(**over) -> Player:
    base = dict(nick="clean", kills=16, deaths=18, kd=0.89, hs=42.0, level=18, score=28.0)
    base.update(over)
    return Player(**base)


def clean_steam(**over) -> SteamProfile:
    base = dict(
        query_nick="clean",
        steamid64="76561190000000001",
        privacy_state="public",
        lookup_source="api",
        account_age_years=8.0,
        cs2_hours=2400.0,
    )
    base.update(over)
    return SteamProfile(**base)


def suspicious_leetify() -> dict:
    return {
        "total_matches": 60,
        "winrate": 68.0,
        "aim": 84.0,
        "reaction_time_ms": 300,
        "preaim": 3.2,
        "accuracy_head": 56.0,
        "faceit": None,
        "faceit_elo": None,
        "premier": None,
        "bans": [],
        "stats": {"counter_strafing_good_shots_ratio": 97.0, "spray_accuracy": 61.0},
    }


def cstracker_ok(**aim) -> dict:
    return {"status": "ok", "trust": None, "matches": [], "sections": {"aim_reactions": dict(aim)}}


class CleanPlayerTests(unittest.TestCase):
    def test_clean_player_low_risk_no_recent_reasons(self):
        cheat, risk, smurf, _, unc, weights, reasons = _compute_scores(clean_player(), clean_steam())
        self.assertLess(cheat, 30.0)
        self.assertEqual(risk, "low")
        self.assertLess(smurf, 30.0)
        self.assertFalse(any("недавні матчі" in r for r in reasons))
        self.assertEqual(weights["leetify"], 0.0)

    def test_missing_leetify_and_cstracker_adds_nothing(self):
        points, elite, reasons = _recent_aim_signals(None, {}, False)
        self.assertEqual((points, elite, reasons), (0.0, False, []))

    def test_leetify_without_stats_adds_nothing(self):
        lf = {"total_matches": 10, "winrate": 50.0, "aim": None, "bans": []}
        points, elite, reasons = _recent_aim_signals(lf, {}, False)
        self.assertEqual((points, elite, reasons), (0.0, False, []))

    def test_cstracker_not_ok_is_ignored(self):
        extra = {"cstracker": {"status": "blocked", "sections": {"aim_reactions": {"Spot to kill": 100}}}}
        points, _, _ = _recent_aim_signals(None, extra, False)
        self.assertEqual(points, 0.0)


class SuspiciousPlayerTests(unittest.TestCase):
    def test_multiple_aim_metrics_raise_cheat_probability_and_name_source(self):
        clean_cheat, *_ = _compute_scores(clean_player(), clean_steam())
        steam = clean_steam(leetify=suspicious_leetify())
        cheat, risk, _, _, _, weights, reasons = _compute_scores(clean_player(), steam)
        self.assertGreater(cheat, clean_cheat)
        self.assertGreater(weights["leetify"], 0.0)
        joined = " | ".join(reasons)
        self.assertIn("Counter-strafing 97% (Leetify, недавні матчі)", joined)
        self.assertIn("Точність у голову 56% (Leetify, недавні матчі)", joined)
        self.assertIn("Точність спрею 61% (Leetify, недавні матчі)", joined)

    def test_cstracker_fallback_when_leetify_missing(self):
        extra = {"cstracker": cstracker_ok(**{"Counter-strafing": 96.0, "Spray accuracy": 58.0, "Spot to kill": 210.0})}
        points, elite, reasons = _recent_aim_signals(None, extra, False)
        self.assertEqual(points, 12.0 + 10.0 + 20.0)
        self.assertTrue(elite)
        self.assertTrue(all("cstracker" in r for r in reasons))

    def test_spot_to_kill_skipped_when_leetify_reaction_present(self):
        lf = {"reaction_time_ms": 400, "stats": {}}
        extra = {"cstracker": cstracker_ok(**{"Spot to kill": 100.0})}
        points, _, reasons = _recent_aim_signals(lf, extra, False)
        self.assertEqual(points, 0.0)
        self.assertEqual(reasons, [])

    def test_elite_recent_flag_adds_smurf_bonus_on_young_account(self):
        lf = suspicious_leetify()
        lf["winrate"] = 70.0
        points, elite, _ = _recent_aim_signals(lf, {}, False)
        self.assertTrue(elite)
        self.assertGreaterEqual(points, AIM_ELITE_RECENT_POINTS)
        # Moderate smurf profile so the score is not clamped at 99.
        young = clean_steam(leetify=lf, account_age_years=1.8, cs2_hours=300.0)
        player = clean_player(kd=1.4, kills=16, hs=40.0, level=6)
        _, _, smurf_with, _, _, _, reasons = _compute_scores(player, young)
        no_recent = clean_steam(
            leetify={**lf, "stats": {}, "aim": 70.0, "accuracy_head": None},
            account_age_years=1.8, cs2_hours=300.0,
        )
        _, _, smurf_without, _, _, _, _ = _compute_scores(player, no_recent)
        self.assertLess(smurf_with, 99.0)
        self.assertGreater(smurf_with, smurf_without)
        self.assertTrue(any("елітний аім" in r for r in reasons))


class HighEloAndLifetimeTests(unittest.TestCase):
    def test_high_elo_skips_aim_metrics(self):
        lf = suspicious_leetify()
        lf["faceit_elo"] = 2100
        points, elite, reasons = _recent_aim_signals(lf, {}, high_elo=True)
        self.assertEqual((points, elite, reasons), (0.0, False, []))

    def test_lifetime_cs2_stats_are_not_labelled_as_recent(self):
        steam = clean_steam(cs2_stats={"kd": 2.6, "hs_pct": 74.0, "total_matches_played": 90})
        _, _, _, _, _, _, reasons = _compute_scores(clean_player(), steam)
        cs2_reasons = [r for r in reasons if "CS2" in r]
        self.assertTrue(cs2_reasons)
        self.assertFalse(any("недавні матчі" in r for r in cs2_reasons))

    def test_scores_stay_finite_and_bounded(self):
        steam = clean_steam(leetify=suspicious_leetify(), extra_stats={"cstracker": cstracker_ok(**{"Spot to kill": 90.0})})
        cheat, _, smurf, _, unc, _, _ = _compute_scores(clean_player(kd=3.2, hs=80.0, level=1), steam)
        for v in (cheat, smurf, unc):
            self.assertTrue(math.isfinite(v))
            self.assertGreaterEqual(v, 0.0)
            self.assertLessEqual(v, 100.0)


if __name__ == "__main__":
    unittest.main()
