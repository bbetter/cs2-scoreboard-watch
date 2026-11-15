"""Contract tests for sources/: normalize mapping, storage slots, coverage parity, registry rules.

Fetchers are mocked on their sources/* modules and keys on storage.api_keys; nothing touches the network.
Run: /home/andrii/fun/.venv/bin/python -m unittest discover -s tests -v
"""

import copy
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import lookup.ai_analyzer as ai_analyzer  # noqa: E402
import lookup.steam_lookup as steam_lookup  # noqa: E402
import sources.cstracker as cstracker_source  # noqa: E402
import sources.leetify as leetify_source  # noqa: E402
import sources.steam_stats as steam_stats_source  # noqa: E402
import storage.api_keys as api_keys  # noqa: E402
import storage.feedback_store as feedback_store  # noqa: E402
from sources import REGISTRY, DataSource, SourceContext, SourceRegistry, SourceResult  # noqa: E402
from sources.cstracker import CsTrackerSource  # noqa: E402
from sources.faceit import FaceitSource  # noqa: E402
from sources.leetify import LeetifySource  # noqa: E402
from sources.steam_stats import SteamStatsSource  # noqa: E402

GOLDEN = ROOT / "tests" / "golden" / "scoring_golden.json"
SID = "76561198000000042"


def _golden_fixtures():
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    return {**data["fixtures"], **data["recheck"]}


class NormalizeTests(unittest.TestCase):
    def test_leetify(self):
        s = LeetifySource()
        self.assertEqual(s.normalize({"aim": 80.0}).status, "ok")
        self.assertEqual(s.normalize({}).status, "not_found")
        self.assertEqual(s.normalize(None).status, "not_found")
        bad = s.normalize("not a dict")
        self.assertEqual((bad.status, bad.data), ("not_found", {}))

    def test_faceit(self):
        s = FaceitSource()
        self.assertEqual(s.normalize({"found": True, "skill_level": 9}).status, "ok")
        self.assertEqual(s.normalize({"found": False, "skill_level": 9}).status, "not_found")
        self.assertEqual(s.normalize({}).status, "not_found")
        self.assertEqual(s.normalize(None).status, "not_found")

    def test_cstracker_every_status_branch(self):
        s = CsTrackerSource()
        self.assertEqual(s.normalize({"status": "ok", "sections": {}}).status, "ok")
        self.assertEqual(s.normalize({"status": "not_found"}).status, "not_found")
        self.assertEqual(s.normalize({"status": "blocked", "http_status": 403}).status, "blocked")
        self.assertEqual(s.normalize({"status": "error"}).status, "error")
        self.assertEqual(s.normalize({"status": "something_new"}).status, "error")
        self.assertEqual(s.normalize({"trust": 5}).status, "error")  # status missing
        self.assertEqual(s.normalize({"status": ["unhashable"]}).status, "error")  # never raises
        for empty in ({}, None):
            with self.subTest(empty=empty):
                res = s.normalize(empty)
                self.assertEqual((res.status, res.data), ("unavailable", {}))

    def test_cstracker_keeps_raw_dict_as_data(self):
        raw = {"status": "ok", "trust": 88, "sections": {"aim_reactions": {"Spot to kill": 290.0}}}
        res = CsTrackerSource().normalize(raw)
        self.assertEqual(res.data, raw)

    def test_steam_stats(self):
        s = SteamStatsSource()
        self.assertEqual(s.normalize({"kd": 1.1}).status, "ok")
        self.assertEqual(s.normalize({}).status, "not_found")
        self.assertEqual(s.normalize(None).status, "not_found")

    def test_base_normalize_is_never_raising_for_unknown_payloads(self):
        for src in REGISTRY:
            for raw in (None, {}, [], "x", 0, {"status": None}):
                with self.subTest(source=src.id, raw=raw):
                    self.assertIn(src.normalize(raw).status, ("ok", "not_found", "error", "unavailable", "blocked"))


class FetchTests(unittest.TestCase):
    def test_leetify_fetch_calls_steam_lookup_at_call_time(self):
        payload = {"total_matches": 120, "aim": 77.0, "bans": []}
        with mock.patch.object(leetify_source, "fetch_leetify", return_value=payload) as fetch:
            res = LeetifySource().fetch(SID, SourceContext())
        fetch.assert_called_once_with(SID)
        self.assertEqual((res.status, res.data), ("ok", payload))
        self.assertTrue(res.fetched_at)

    def test_fetch_turns_exceptions_into_error(self):
        with mock.patch.object(cstracker_source, "fetch_cstracker", side_effect=RuntimeError("boom")):
            res = CsTrackerSource().fetch(SID, SourceContext())
        self.assertEqual(res.status, "error")

    def test_cstracker_fetch_passes_through_blocked(self):
        with mock.patch.object(cstracker_source, "fetch_cstracker", return_value={"status": "blocked"}):
            self.assertEqual(CsTrackerSource().fetch(SID, SourceContext()).status, "blocked")

    def test_faceit_is_enabled_follows_api_key(self):
        with mock.patch.object(api_keys, "_get_faceit_api_key", return_value=None):
            self.assertFalse(FaceitSource().is_enabled(SourceContext()))
        with mock.patch.object(api_keys, "_get_faceit_api_key", return_value="k"):
            self.assertTrue(FaceitSource().is_enabled(SourceContext()))

    def test_steam_stats_fetch_uses_context_key_or_config_key(self):
        with mock.patch.object(steam_stats_source, "fetch_steam_cs2_stats", return_value={"kd": 1.0}) as fetch, \
                mock.patch.object(api_keys, "_get_steam_api_key", return_value="cfgkey"):
            SteamStatsSource().fetch(SID, SourceContext(steam_api_key="ctxkey"))
            fetch.assert_called_with(SID, "ctxkey")
            SteamStatsSource().fetch(SID, SourceContext())
            fetch.assert_called_with(SID, "cfgkey")

    def test_steam_stats_is_enabled_needs_a_key(self):
        with mock.patch.object(api_keys, "_get_steam_api_key", return_value=None):
            self.assertFalse(SteamStatsSource().is_enabled(SourceContext()))
            self.assertTrue(SteamStatsSource().is_enabled(SourceContext(steam_api_key="k")))


class SlotRoundTripTests(unittest.TestCase):
    def test_cstracker_write_keeps_sibling_flag_and_does_not_mutate_inputs_in_place(self):
        extra = {"cstracker": {"status": "ok", "trust": 5}, "_cs2_checked": True}
        steam = {"steamid64": SID, "extra_stats": extra}
        snapshot = copy.deepcopy(steam)
        src = CsTrackerSource()

        src.write_raw(steam, {"status": "blocked", "http_status": 403})

        self.assertEqual(steam["extra_stats"]["_cs2_checked"], True)
        self.assertEqual(src.read_raw(steam), {"status": "blocked", "http_status": 403})
        # The nested dict the caller passed in is untouched; only the record's pointer was replaced.
        self.assertEqual(extra, snapshot["extra_stats"])
        self.assertIsNot(steam["extra_stats"], extra)

    def test_legacy_round_trip_for_each_slot(self):
        cases = [
            (LeetifySource(), {"aim": 70.0, "bans": []}),
            (FaceitSource(), {"found": True, "skill_level": 5}),
            (SteamStatsSource(), {"kd": 1.2}),
            (CsTrackerSource(), {"status": "ok", "sections": {}}),
        ]
        for src, raw in cases:
            with self.subTest(source=src.id):
                steam: dict = {}
                src.write_raw(steam, raw)
                self.assertEqual(src.read_raw(steam), raw)
                self.assertEqual(src.result_from(steam).status, "ok")

    def test_write_into_existing_steam_keeps_unrelated_keys(self):
        steam = {"steamid64": SID, "leetify": {"aim": 1.0}, "faceit": {"found": True}, "vac_banned": False}
        LeetifySource().write_raw(steam, {"aim": 2.0})
        self.assertEqual(steam["leetify"], {"aim": 2.0})
        self.assertEqual(steam["faceit"], {"found": True})
        self.assertEqual(steam["vac_banned"], False)

    def test_default_native_slot_round_trip(self):
        class Dummy(DataSource):
            id = "dummy"
            label = "Dummy"

            def fetch_raw(self, steamid64, ctx):
                return {"score": 3}

            def normalize(self, raw):
                return SourceResult("ok" if raw else "not_found", raw if isinstance(raw, dict) else {})

        d = Dummy()
        steam = {"steamid64": SID}
        d.write_raw(steam, {"score": 3})
        self.assertEqual(steam["sources"]["dummy"]["status"], "ok")
        self.assertEqual(d.read_raw(steam), {"score": 3})
        self.assertEqual(d.result_from(steam).status, "ok")


class CoverageParityTests(unittest.TestCase):
    def test_golden_fixtures_with_stored_coverage_match_legacy_map(self):
        checked = 0
        for name, fx in _golden_fixtures().items():
            steam = fx["steam"]
            legacy = steam.get("coverage") or {}
            if not legacy:
                continue
            checked += 1
            expected = {k: v for k, v in legacy.items() if k != "steam"}
            with self.subTest(fixture=name):
                self.assertEqual(REGISTRY.coverage(steam), expected)
        self.assertGreaterEqual(checked, 1)

    def test_coverage_matches_legacy_source_coverage_for_every_golden_steam_dict(self):
        for name, fx in _golden_fixtures().items():
            steam = fx["steam"]
            profile = steam_lookup.SteamProfile(**steam)
            expected = {k: v for k, v in steam_lookup._source_coverage(profile).items() if k != "steam"}
            with self.subTest(fixture=name):
                self.assertEqual(REGISTRY.coverage(steam), expected)

    def test_steam_stats_is_not_in_coverage(self):
        steam = {"steamid64": SID, "cs2_stats": {"kd": 1.0}}
        self.assertNotIn("steam_stats", REGISTRY.coverage(steam))


class SourcesViewTests(unittest.TestCase):
    def test_sources_view_on_legacy_dict(self):
        steam = {
            "steamid64": SID,
            "leetify": {"aim": 70.0},
            "faceit": {},
            "extra_stats": {"cstracker": {"status": "blocked", "http_status": 403}},
        }
        view = REGISTRY.sources_view(steam)
        self.assertEqual(sorted(view), ["cstracker", "faceit", "leetify"])  # steam_stats: no cs2_stats -> absent
        self.assertEqual(view["leetify"]["status"], "ok")
        self.assertEqual(view["faceit"]["status"], "not_found")
        self.assertEqual(view["cstracker"]["status"], "blocked")
        self.assertEqual(view["cstracker"]["data"], {"status": "blocked", "http_status": 403})

    def test_result_from_is_none_when_never_fetched(self):
        self.assertIsNone(CsTrackerSource().result_from({"steamid64": SID}))
        self.assertIsNone(LeetifySource().result_from({}))


class ParityWithConsumersTests(unittest.TestCase):
    """Sources must reproduce what ai_analyzer and feedback_store produce today."""

    def _player(self, steam):
        return {
            "nick": "p", "kills": 10, "deaths": 5, "kd": 2.0, "hs": 50.0, "level": 5,
            "cheat_probability": 40.0, "smurf_probability": 10.0, "uncertainty": 30.0,
            "anomaly_score": 12.0, "steam": steam,
        }

    def test_leetify_ai_compact_matches_compaction(self):
        leetify = {"total_matches": 200, "aim": 80.0, "reaction_time_ms": 330, "premier": 15000,
                   "faceit": None, "faceit_level": 7, "bans": [{"x": 1}], "winrate": 55.0}
        steam = {"steamid64": SID, "leetify": leetify}
        expected = ai_analyzer._compact_player_for_ai(self._player(steam))["leetify"]
        res = LeetifySource().result_from(steam)
        self.assertEqual(LeetifySource().ai_compact(res), expected)

    def test_leetify_ai_compact_when_absent(self):
        steam = {"steamid64": SID}
        expected = ai_analyzer._compact_player_for_ai(self._player(steam))["leetify"]
        self.assertEqual(LeetifySource().ai_compact(SourceResult("not_found", {})), expected)

    def test_faceit_ai_compact_matches_compaction(self):
        faceit = {"found": True, "skill_level": 9, "faceit_elo": 2100}
        steam = {"steamid64": SID, "faceit": faceit}
        expected = ai_analyzer._compact_player_for_ai(self._player(steam)).get("faceit")
        res = FaceitSource().result_from(steam)
        self.assertEqual(FaceitSource().ai_compact(res), expected)
        self.assertIsNone(FaceitSource().ai_compact(SourceResult("not_found", {})))

    def test_leetify_features_match_feedback_entry(self):
        leetify = {"aim": 81.234, "reaction_time_ms": 300, "preaim": 3.0, "accuracy_head": 55.5,
                   "winrate": 70.0, "total_matches": 90}
        steam = {"steamid64": SID, "leetify": leetify}
        player = self._player(steam)
        entry = feedback_store.build_feedback_entry("scan-1", "cheater", player)["features"]["leetify"]
        got = LeetifySource().features(LeetifySource().result_from(steam))
        expected = {k: v for k, v in entry.items() if k != "present"}
        self.assertEqual(got, expected)
        self.assertEqual(entry["present"], LeetifySource().result_from(steam).ok)

    def test_cstracker_status_matches_feedback_for_known_statuses(self):
        for status in ("ok", "not_found", "blocked"):
            steam = {"steamid64": SID, "extra_stats": {"cstracker": {"status": status}}}
            entry = feedback_store.build_feedback_entry("scan-1", "legit", self._player(steam))
            with self.subTest(status=status):
                self.assertEqual(entry["features"]["cstracker_status"], status)
                self.assertEqual(CsTrackerSource().result_from(steam).status, status)


class RegistryRulesTests(unittest.TestCase):
    def test_builtin_order_and_recheckable_ids(self):
        self.assertEqual(REGISTRY.ids(), ["leetify", "faceit", "cstracker", "steam_stats"])
        self.assertEqual(REGISTRY.recheckable_ids(), ["leetify", "cstracker"])

    def test_duplicate_id_raises_and_leaves_registry_unchanged(self):
        before = REGISTRY.ids()
        with self.assertRaises(ValueError):
            REGISTRY.register(LeetifySource())
        self.assertEqual(REGISTRY.ids(), before)

    def test_empty_id_is_rejected(self):
        class NoId(DataSource):
            pass

        with self.assertRaises(ValueError):
            SourceRegistry().register(NoId())

    def test_new_source_needs_only_a_class_and_a_registration(self):
        class Lobby(DataSource):
            id = "lobby_bonus"
            label = "Lobby bonus"
            in_coverage = True
            recheckable = True

            def fetch_raw(self, steamid64, ctx):
                return {"score": 9}

            def normalize(self, raw):
                return SourceResult("ok" if raw else "not_found", raw if isinstance(raw, dict) else {})

        reg = SourceRegistry()
        reg.register(Lobby())
        steam = {"steamid64": SID}
        reg.get("lobby_bonus").write_raw(steam, {"score": 9})
        self.assertEqual(reg.coverage(steam), {"lobby_bonus": True})
        self.assertEqual(reg.recheckable_ids(), ["lobby_bonus"])
        self.assertEqual(sorted(reg.sources_view(steam)), ["lobby_bonus"])


if __name__ == "__main__":
    unittest.main()


class ConsumerRegistryTests(unittest.TestCase):
    """Behaviour of the registry-driven consumers that the golden files do not cover."""

    def test_recheck_invalid_source_message_is_unchanged(self):
        import app.server as server

        status, body = server.recheck_source_for_player("scan-x", "nick", "faceit")
        self.assertEqual(status, 400)
        self.assertEqual(body, {"error": "source має бути 'leetify' або 'cstracker'"})

    def test_recheck_non_string_source_is_400_not_crash(self):
        import app.server as server

        for bad in (["leetify"], {"a": 1}, 5, None, ""):
            with self.subTest(source=bad):
                status, body = server.recheck_source_for_player("scan-x", "nick", bad)
                self.assertEqual(status, 400)
                self.assertIn("source має бути", body["error"])

    def test_choices_text_two_and_many(self):
        import app.server as server

        self.assertEqual(server._source_choices_text(["leetify", "cstracker"]), "'leetify' або 'cstracker'")
        self.assertEqual(server._source_choices_text(["a", "b", "c"]), "'a', 'b' або 'c'")
