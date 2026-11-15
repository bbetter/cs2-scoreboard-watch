"""Golden tests that freeze the CURRENT deterministic scoring pipeline (lookup/steam_lookup.py).

Purpose: later refactors (moving scoring into rules, a source registry) must not change
any output. Every fixture is built from synthetic Player/SteamProfile objects or canned
fetcher results; there is no network access.

Run tests:        /home/andrii/fun/.venv/bin/python -m unittest discover -s tests -v
Regenerate:       /home/andrii/fun/.venv/bin/python tests/test_golden_scoring.py --regen
Regenerate ONLY when a scoring change is intentional, and review the JSON diff.

Determinism notes:
- AI weights come from config.json via _load_base_weights(); they are pinned to the
  built-in defaults here so the golden file does not depend on the local config.
- Steam XML memberSince is parsed against datetime.now(), so recheck fixtures omit it.
- Floats are rounded to 6 digits before comparison; NaN is stored as the string "NaN".
"""

import json
import math
import sys
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import lookup.steam_lookup as sl  # noqa: E402
from lookup.steam_lookup import SteamProfile, _build_player_report  # noqa: E402
from vision.scoreboard_scan import Player  # noqa: E402

GOLDEN_PATH = Path(__file__).resolve().parent / "golden" / "scoring_golden.json"
PINNED_WEIGHTS = {"scoreboard": 0.30, "leetify": 0.40, "steam_account": 0.30}
FLOAT_DIGITS = 6
NAN = float("nan")


# ---------------------------------------------------------------- builders

def _player(**over) -> Player:
    base = dict(nick="sample", kills=16, deaths=18, kd=0.89, hs=42.0, level=18, score=28.0)
    base.update(over)
    return Player(**base)


def _steam(**over) -> SteamProfile:
    base = dict(
        query_nick="sample",
        profile_name="sample",
        steamid64="76561190000000001",
        vanity="sample",
        privacy_state="public",
        online_state="offline",
        lookup_source="api",
        account_age_years=8.0,
        cs2_hours=2400.0,
    )
    base.update(over)
    return SteamProfile(**base)


def _leetify(**over) -> dict:
    base = {
        "total_matches": 300,
        "winrate": 52.0,
        "aim": 70.0,
        "reaction_time_ms": 440,
        "preaim": 9.0,
        "accuracy_head": 38.0,
        "faceit": 6,
        "faceit_elo": 1500,
        "premier": None,
        "bans": [],
        "stats": {"counter_strafing_good_shots_ratio": 88.0, "spray_accuracy": 40.0},
    }
    base.update(over)
    return base


def _cstracker(status: str = "ok", trust=None, aim_reactions=None, **over) -> dict:
    if status != "ok":
        base = {"status": status, "http_status": 403 if status == "blocked" else 200}
    else:
        base = {
            "status": "ok",
            "trust": trust,
            "matches": [],
            "sections": {"aim_reactions": dict(aim_reactions or {})},
            "http_status": 200,
        }
    base.update(over)
    return base


def _faceit(**over) -> dict:
    base = {
        "found": True,
        "player_id": "fx-1",
        "nickname": "sample",
        "skill_level": 5,
        "faceit_elo": 1400,
        "matches": 200,
        "winrate": 50.0,
        "kd": 1.1,
        "hs_pct": 45.0,
    }
    base.update(over)
    return base


def _cs2_stats(**over) -> dict:
    base = {
        "total_kills": 9000,
        "total_deaths": 8000,
        "total_kills_headshot": 4000,
        "total_wins": 900,
        "total_matches_played": 1800,
        "kd": 1.12,
        "hs_pct": 44.4,
    }
    base.update(over)
    return base


# ---------------------------------------------------------------- fixtures: (Player, SteamProfile)

def fx_no_steam_profile():
    return _player(), SteamProfile(query_nick="ghost", lookup_source="not_found")


def fx_private_profile():
    return _player(), _steam(privacy_state="private", account_age_years=4.0, cs2_hours=500.0)


def fx_public_clean_veteran_no_external():
    return _player(), _steam(account_age_years=9.0, cs2_hours=3000.0)


def fx_vac_banned():
    return _player(kd=1.4, hs=50.0), _steam(vac_banned=True, account_age_years=5.0, cs2_hours=900.0)


def fx_trade_ban_community():
    return _player(), _steam(trade_ban="banned", account_age_years=5.0, cs2_hours=900.0)


def fx_limited_account_young():
    return _player(), _steam(is_limited=True, account_age_years=3.0, cs2_hours=90.0)


def fx_leetify_platform_ban():
    return _player(kd=1.2), _steam(
        leetify=_leetify(bans=[{"platform": "faceit", "type": "ban"}]),
        account_age_years=6.0,
        cs2_hours=1100.0,
    )


def fx_young_low_hours_smurf_ish():
    return _player(level=2, kd=2.1, kills=24, deaths=11, hs=70.0), _steam(
        account_age_years=0.6, cs2_hours=80.0
    )


def fx_smurf_faceit_new_account():
    return _player(kd=1.6, kills=22, deaths=14, hs=48.0, level=7, score=40.0), _steam(
        account_age_years=1.2,
        cs2_hours=200.0,
        faceit=_faceit(faceit_elo=2050, skill_level=9, matches=90, kd=1.5, hs_pct=50.0),
        leetify=_leetify(total_matches=40, winrate=60.0, aim=76.0, faceit=9, faceit_elo=2050),
    )


def fx_leetify_full_clean():
    return _player(), _steam(account_age_years=7.0, cs2_hours=1800.0, leetify=_leetify())


def fx_leetify_partial_aim_only():
    return _player(), _steam(account_age_years=7.0, cs2_hours=1800.0, leetify={"total_matches": 40, "aim": 93.0})


def fx_leetify_missing_steam_only():
    return _player(), _steam(account_age_years=7.0, cs2_hours=1800.0, leetify=None)


def fx_leetify_superhuman_reaction():
    return _player(kd=1.9, hs=58.0), _steam(
        account_age_years=2.5,
        cs2_hours=900.0,
        leetify=_leetify(reaction_time_ms=290, aim=96.0, total_matches=90, winrate=74.0),
    )


def fx_cstracker_ok_full():
    return _player(), _steam(
        account_age_years=6.0,
        cs2_hours=1200.0,
        leetify=_leetify(),
        extra_stats={
            "cstracker": _cstracker(
                trust=88,
                aim_reactions={"Counter-strafing": 96.0, "Spray accuracy": 58.0, "Spot to kill": 290.0},
            ),
            "_cs2_checked": True,
        },
    )


def fx_cstracker_blocked():
    return _player(), _steam(
        account_age_years=6.0,
        cs2_hours=1200.0,
        leetify=_leetify(),
        extra_stats={"cstracker": _cstracker("blocked"), "_cs2_checked": True},
    )


def fx_cstracker_not_found():
    return _player(), _steam(
        account_age_years=6.0,
        cs2_hours=1200.0,
        extra_stats={"cstracker": _cstracker("not_found")},
    )


def fx_cstracker_error_no_leetify():
    return _player(), _steam(
        account_age_years=3.0,
        cs2_hours=600.0,
        extra_stats={"cstracker": _cstracker("error")},
    )


def fx_faceit_high_elo_no_leetify():
    return _player(kd=1.5, hs=50.0), _steam(
        account_age_years=5.0,
        cs2_hours=1400.0,
        faceit=_faceit(skill_level=10, faceit_elo=2300, matches=700, kd=1.15, hs_pct=42.0),
    )


def fx_faceit_absent_cs2_stats_only():
    return _player(kd=1.3, hs=55.0), _steam(
        account_age_years=4.0,
        cs2_hours=800.0,
        cs2_stats=_cs2_stats(kd=2.45, total_matches_played=120, hs_pct=74.0),
    )


def fx_extra_medals_veteran_unused():
    return _player(), _steam(
        account_age_years=8.0,
        cs2_hours=2000.0,
        extra_stats={"medals_count": 12, "has_veteran_coin": True, "xp_level": 210},
    )


def fx_recent_aim_elite_mix():
    return _player(kd=1.4, hs=52.0, kills=20, deaths=14), _steam(
        account_age_years=1.8,
        cs2_hours=400.0,
        leetify=_leetify(
            total_matches=80,
            aim=80.0,
            reaction_time_ms=None,
            stats={"counter_strafing_good_shots_ratio": 97.0, "spray_accuracy": 61.0},
            accuracy_head=56.0,
        ),
        extra_stats={
            "cstracker": _cstracker(aim_reactions={"Spot to kill": 200.0}),
            "_cs2_checked": True,
        },
    )


def fx_stale_cached_rate_limited():
    # Served from profile cache after a rate-limited refresh; no fresh cs2 stats.
    return _player(), _steam(
        lookup_source="steam_web_api",
        account_age_years=3.0,
        cs2_hours=300.0,
        leetify=_leetify(),
        extra_stats={"cstracker": _cstracker("not_found"), "_cs2_checked": False},
    )


def fx_combo_cheater_like():
    return _player(kills=30, deaths=9, kd=3.2, hs=81.0, level=12, score=96.0), _steam(
        account_age_years=3.5,
        cs2_hours=1100.0,
        leetify=_leetify(
            total_matches=120,
            winrate=70.0,
            aim=96.0,
            reaction_time_ms=300,
            preaim=3.2,
            accuracy_head=62.0,
            stats={"counter_strafing_good_shots_ratio": 98.0, "spray_accuracy": 66.0},
        ),
        extra_stats={"cstracker": _cstracker(trust=18, aim_reactions={"Counter-strafing": 97.0}), "_cs2_checked": True},
    )


def fx_combo_smurf_like():
    return _player(kills=24, deaths=10, kd=2.4, hs=55.0, level=1, score=40.0), _steam(
        account_age_years=0.8,
        cs2_hours=90.0,
        faceit=_faceit(skill_level=8, faceit_elo=1950, matches=60, kd=1.6, hs_pct=49.0),
        cs2_stats=_cs2_stats(kd=1.6, total_matches_played=80, hs_pct=50.0),
    )


def fx_extreme_rage_no_data():
    return _player(kills=25, deaths=7, kd=3.5, hs=80.0, level=2, score=NAN), _steam(
        query_nick="rage", steamid64=None, lookup_source="not_found"
    )


def fx_nan_score_no_lobby_score():
    return _player(kd=1.0, hs=30.0, level=-1, score=NAN), _steam(
        account_age_years=10.0, cs2_hours=2500.0
    )


def fx_private_limited_new_no_external():
    return _player(), _steam(
        privacy_state="private", is_limited=True, account_age_years=0.9, cs2_hours=30.0
    )


FIXTURES = {
    "01_no_steam_profile": fx_no_steam_profile,
    "02_private_profile": fx_private_profile,
    "03_public_clean_veteran_no_external": fx_public_clean_veteran_no_external,
    "04_vac_banned": fx_vac_banned,
    "05_trade_ban_community": fx_trade_ban_community,
    "06_limited_account_young": fx_limited_account_young,
    "07_leetify_platform_ban": fx_leetify_platform_ban,
    "08_young_low_hours_smurf_ish": fx_young_low_hours_smurf_ish,
    "09_smurf_faceit_new_account": fx_smurf_faceit_new_account,
    "10_leetify_full_clean": fx_leetify_full_clean,
    "11_leetify_partial_aim_only": fx_leetify_partial_aim_only,
    "12_leetify_missing_steam_only": fx_leetify_missing_steam_only,
    "13_leetify_superhuman_reaction": fx_leetify_superhuman_reaction,
    "14_cstracker_ok_full": fx_cstracker_ok_full,
    "15_cstracker_blocked": fx_cstracker_blocked,
    "16_cstracker_not_found": fx_cstracker_not_found,
    "17_cstracker_error_no_leetify": fx_cstracker_error_no_leetify,
    "18_faceit_high_elo_no_leetify": fx_faceit_high_elo_no_leetify,
    "19_faceit_absent_cs2_stats_only": fx_faceit_absent_cs2_stats_only,
    "20_extra_medals_veteran_unused": fx_extra_medals_veteran_unused,
    "21_recent_aim_elite_mix": fx_recent_aim_elite_mix,
    "22_stale_cached_rate_limited": fx_stale_cached_rate_limited,
    "23_combo_cheater_like": fx_combo_cheater_like,
    "24_combo_smurf_like": fx_combo_smurf_like,
    "25_extreme_rage_no_data": fx_extreme_rage_no_data,
    "26_nan_score_no_lobby_score": fx_nan_score_no_lobby_score,
    "27_private_limited_new_no_external": fx_private_limited_new_no_external,
}


# ---------------------------------------------------------------- recheck fixtures (fetchers mocked)

_XML_PUBLIC = (
    b"<profile><steamID64>76561198000000042</steamID64><steamID>Zed Vanity</steamID>"
    b"<privacyState>public</privacyState><onlineState>offline</onlineState>"
    b"<vacBanned>0</vacBanned><tradeBanState>None</tradeBanState><isLimitedAccount>0</isLimitedAccount>"
    b"<mostPlayedGames><mostPlayedGame><gameName>Counter-Strike 2</gameName>"
    b"<hoursOnRecord>1,234.5</hoursOnRecord></mostPlayedGame></mostPlayedGames></profile>"
)
_XML_VAC = (
    b"<profile><steamID64>76561198000000099</steamID64><steamID>Banned Guy</steamID>"
    b"<privacyState>public</privacyState><onlineState>offline</onlineState>"
    b"<vacBanned>1</vacBanned><tradeBanState>banned</tradeBanState><isLimitedAccount>0</isLimitedAccount>"
    b"</profile>"
)


def _recheck_cases():
    """name -> dict(player, query, http, external, cstracker, lookup).

    lookup is a zero-arg callable returning the SteamProfile for the nickname fallback path.
    """
    return {
        "R1_steam_url_vanity": dict(
            player={"nick": "Zed", "kills": 18, "deaths": 15, "kd": 1.2, "hs": 40.0, "level": 9, "anomaly_score": 35.0},
            query="https://steamcommunity.com/id/zed_vanity/",
            http=_XML_PUBLIC,
            external=(_leetify(), _faceit(), _cs2_stats()),
            cstracker={"cstracker": _cstracker(trust=75, aim_reactions={"Counter-strafing": 91.0}), "_cs2_checked": True},
            lookup=None,
        ),
        "R2_steamid64_banned": dict(
            player={"nick": "Banned Guy", "kills": 22, "deaths": 12, "kd": 1.8, "hs": 61.0, "level": 4, "anomaly_score": None},
            query="76561198000000099",
            http=_XML_VAC,
            external=(None, {}, {}),
            cstracker={"cstracker": _cstracker("blocked"), "_cs2_checked": False},
            lookup=None,
        ),
        "R3_nick_fallback_lookup": dict(
            player={"nick": "Old", "kills": 14, "deaths": 16, "kd": 0.88, "hs": 35.0, "level": 20, "anomaly_score": 12.5},
            query="Nickname2",
            http=None,
            external=None,
            cstracker=None,
            lookup=lambda: _steam(
                query_nick="Nickname2", account_age_years=5.0, cs2_hours=700.0,
                faceit=_faceit(skill_level=7, faceit_elo=1650), leetify=_leetify(aim=88.0),
            ),
        ),
        "R4_url_unresolvable_falls_back": dict(
            player={"nick": "Ghost", "kills": 9, "deaths": 20, "kd": 0.45, "hs": 22.0, "level": -1, "anomaly_score": None},
            query="https://steamcommunity.com/id/nobody_here/",
            http=None,
            external=None,
            cstracker=None,
            lookup=lambda: SteamProfile(query_nick="https://steamcommunity.com/id/nobody_here/", lookup_source="not_found"),
        ),
    }


# ---------------------------------------------------------------- normalisation + runners

def _normalise(obj):
    if isinstance(obj, float):
        if math.isnan(obj):
            return "NaN"
        return round(obj, FLOAT_DIGITS)
    if isinstance(obj, dict):
        return {str(k): _normalise(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_normalise(v) for v in obj]
    return obj


def _pinned():
    return mock.patch.object(sl, "_load_base_weights", return_value=dict(PINNED_WEIGHTS))


def _run_fixture(name: str) -> dict:
    with _pinned():
        player, steam = FIXTURES[name]()
        return _normalise(asdict(_build_player_report(player, steam)))


def _run_recheck(name: str) -> dict:
    case = _recheck_cases()[name]
    ext = case["external"]
    lookup_fn = case["lookup"]
    with _pinned(), \
            mock.patch.object(sl, "_http_get", return_value=case["http"]), \
            mock.patch.object(sl, "_fetch_external_stats", return_value=ext if ext is not None else (None, {}, {})), \
            mock.patch.object(sl, "_cstracker_extra", return_value=case["cstracker"] or {}), \
            mock.patch.object(sl, "lookup_steam_profile", side_effect=lambda q, *a, **k: lookup_fn() if lookup_fn else None):
        return _normalise(sl.recheck_player_report(dict(case["player"]), case["query"]))


def _compute_all() -> dict:
    return {
        "fixtures": {name: _run_fixture(name) for name in FIXTURES},
        "recheck": {name: _run_recheck(name) for name in _recheck_cases()},
    }


def _regen() -> None:
    data = {
        "_meta": {
            "generated_by": "python tests/test_golden_scoring.py --regen",
            "float_digits": FLOAT_DIGITS,
            "pinned_weights": PINNED_WEIGHTS,
            "note": "Frozen CURRENT behaviour of steam_lookup scoring. Regenerate only for intentional changes.",
        },
        **_compute_all(),
    }
    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {GOLDEN_PATH} ({len(data['fixtures'])} fixtures, {len(data['recheck'])} recheck cases)")


# ---------------------------------------------------------------- tests

class GoldenScoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not GOLDEN_PATH.is_file():
            raise unittest.SkipTest(f"golden file missing: {GOLDEN_PATH} (run with --regen)")
        cls.golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))

    def test_golden_covers_exactly_the_defined_fixtures(self):
        self.assertEqual(set(self.golden["fixtures"]), set(FIXTURES), "fixture set drifted from golden file")
        self.assertEqual(set(self.golden["recheck"]), set(_recheck_cases()), "recheck set drifted from golden file")

    def test_fixture_outputs_match_golden(self):
        for name in FIXTURES:
            with self.subTest(fixture=name):
                actual = _run_fixture(name)
                self.assertEqual(actual, self.golden["fixtures"][name], f"scoring output changed for fixture {name!r}")

    def test_recheck_outputs_match_golden(self):
        for name in _recheck_cases():
            with self.subTest(recheck=name):
                actual = _run_recheck(name)
                self.assertEqual(actual, self.golden["recheck"][name], f"recheck output changed for case {name!r}")

    def test_outputs_are_deterministic_across_two_runs(self):
        self.assertEqual(_compute_all(), _compute_all())


if __name__ == "__main__":
    if "--regen" in sys.argv:
        _regen()
        sys.exit(0)
    unittest.main()
