"""Shared player-dict cases for the ai_compact and feedback golden freezes.

Cases = every golden scoring fixture (player-report dicts, already JSON-normalised) plus a few
synthetic players that hit the fallback branches the fixtures don't cover.
"""

import copy
import json
from pathlib import Path

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
SCORING_GOLDEN = GOLDEN_DIR / "scoring_golden.json"

_SID = "76561198000000042"


def _synthetic():
    base_steam = {"steamid64": _SID, "profile_name": "syn", "account_age_years": 2.0, "cs2_hours": 300.0,
                  "steam_level": 12, "vac_banned": False, "is_limited": False, "in_cs2": True,
                  "cs2_stats": {"kd": 1.3}, "extra_stats": {"cstracker": {"status": "ok"}, "_cs2_checked": True}}
    player = {"nick": "syn", "kills": 20, "deaths": 12, "kd": 1.67, "hs": 48.0, "level": 9,
              "cheat_probability": 33.3, "smurf_probability": 12.0, "risk_level": "medium",
              "smurf_risk_level": "low", "uncertainty": 42.0, "effective_weights": {"scoreboard": 0.5},
              "reasons": ["r1"], "anomaly_score": 10.0}
    out = {}
    # premier None -> premier_rank fallback; faceit None -> faceit_level fallback
    p1 = copy.deepcopy(player)
    p1["steam"] = copy.deepcopy(base_steam)
    p1["steam"]["leetify"] = {"total_matches": 50, "premier": None, "premier_rank": 17000,
                              "faceit": None, "faceit_level": 6, "bans": None, "aim": 70.0}
    out["syn_premier_rank_fallback"] = p1
    # truthy faceit dict with found False is still sent to the AI (raw dict)
    p2 = copy.deepcopy(player)
    p2["steam"] = copy.deepcopy(base_steam)
    p2["steam"]["faceit"] = {"found": False, "skill_level": None}
    p2["steam"]["leetify"] = {}
    out["syn_faceit_not_found_truthy"] = p2
    # empty extra_stats / empty cs2_stats / no steamid
    p3 = copy.deepcopy(player)
    p3["steam"] = {"steamid64": None, "extra_stats": {}, "cs2_stats": {}, "faceit": {}}
    out["syn_no_steam_empty_blocks"] = p3
    # premier present, faceit present in leetify
    p4 = copy.deepcopy(player)
    p4["steam"] = copy.deepcopy(base_steam)
    p4["steam"]["leetify"] = {"total_matches": 300, "premier": 21000, "premier_rank": 1, "faceit": 8,
                              "faceit_level": 2, "bans": [{"type": "x"}], "winrate": 61.0, "kd": 1.2,
                              "hs_pct": 40.0, "leetify_rating": 0.1, "reaction_time_ms": 330, "preaim": 4.1}
    p4["steam"]["faceit"] = {"found": True, "skill_level": 7, "faceit_elo": 1700, "matches": 80}
    out["syn_full_leetify_and_faceit"] = p4
    return out


def cases() -> dict:
    data = json.loads(SCORING_GOLDEN.read_text(encoding="utf-8"))
    out = {}
    for name, value in {**data["fixtures"], **data["recheck"]}.items():
        out[name] = value
    out.update(_synthetic())
    return out


def jsonable(obj):
    """Round-trip through JSON so tuples/ints compare the same way the golden file stores them."""
    return json.loads(json.dumps(obj, ensure_ascii=False, sort_keys=True))
