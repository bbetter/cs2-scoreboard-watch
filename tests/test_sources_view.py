"""steam.sources view: wiring into reports/profiles, legacy adapter on load, per-source refresh, native storage.

Fetchers are mocked; history is redirected to a temp file. Golden files are checked elsewhere.
Run: /home/andrii/fun/.venv/bin/python -m unittest discover -s tests -v
"""

import copy
import dataclasses
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import lookup.ai_analyzer as ai_analyzer  # noqa: E402
import app.server as server  # noqa: E402
import lookup.steam_lookup as steam_lookup  # noqa: E402
from vision.scoreboard_scan import Player  # noqa: E402
from sources import REGISTRY, DataSource, SourceRegistry, SourceResult  # noqa: E402
from lookup.steam_lookup import SteamProfile, _build_player_report, _finalize_source_views, _strip_leetify  # noqa: E402

SID = "76561198000000042"


def _legacy_steam() -> dict:
    return {
        "steamid64": SID,
        "profile_name": "legacy",
        "account_age_years": 3.0,
        "cs2_hours": 400.0,
        "leetify": {"total_matches": 120, "aim": 77.0, "bans": []},
        "faceit": {"found": True, "skill_level": 6, "faceit_elo": 1500},
        "cs2_stats": {"kd": 1.1, "total_matches_played": 300},
        "extra_stats": {"cstracker": {"status": "blocked", "http_status": 403}, "_cs2_checked": True},
        "coverage": {"steam": True, "leetify": True, "faceit": True, "cstracker": False},
    }


def _player_for(steam: dict) -> Player:
    return Player(nick="legacy", kills=20, deaths=10, kd=2.0, hs=50.0, level=9, score=30.0)


class ProfileWiringTests(unittest.TestCase):
    def test_sources_is_the_last_field_of_steam_profile(self):
        names = [f.name for f in dataclasses.fields(SteamProfile)]
        self.assertEqual(names[-2:], ["coverage", "sources"])

    def test_finalize_sets_coverage_and_sources_together(self):
        profile = SteamProfile(query_nick="x", steamid64=SID, leetify={"aim": 1.0}, lookup_source="api")
        _finalize_source_views(profile)
        self.assertEqual(profile.coverage["leetify"], True)
        self.assertEqual(profile.sources["leetify"]["status"], "ok")
        # Default {} for an unfetched FACEIT reads as "fetched, not found" (registry semantics; see report).
        self.assertEqual(profile.sources["faceit"]["status"], "not_found")
        self.assertNotIn("cstracker", profile.sources)  # no extra_stats -> slot is None -> no entry

    def test_build_player_report_steam_carries_the_sources_view(self):
        profile = SteamProfile(
            query_nick="legacy", steamid64=SID, leetify={"aim": 77.0}, faceit={"found": False},
            extra_stats={"cstracker": {"status": "ok", "trust": 80}}, lookup_source="api",
        )
        report = dataclasses.asdict(_build_player_report(_player_for({}), profile))
        self.assertEqual(report["steam"]["sources"], REGISTRY.sources_view(report["steam"]))
        self.assertEqual(report["steam"]["sources"]["faceit"]["status"], "not_found")
        self.assertEqual(report["steam"]["sources"]["cstracker"]["status"], "ok")

    def test_strip_leetify_drops_leetify_payload_from_cache_copy(self):
        data = {"leetify": {"aim": 99.0}, "sources": {"leetify": {"status": "ok", "data": {"aim": 99.0}}},
                "faceit": {"found": True}}
        out = _strip_leetify(data)
        self.assertIsNone(out["leetify"])
        self.assertEqual(out["sources"], {})
        self.assertEqual(out["faceit"], {"found": True})
        self.assertIn("aim", data["leetify"])  # input untouched

    def test_compaction_for_ai_never_includes_sources(self):
        profile = SteamProfile(query_nick="legacy", steamid64=SID, leetify={"aim": 77.0}, lookup_source="api")
        report = dataclasses.asdict(_build_player_report(_player_for({}), profile))
        compact = ai_analyzer._compact_player_for_ai(report)
        self.assertNotIn("sources", compact)
        self.assertNotIn("sources", compact["steam"])
        self.assertNotIn("sources", json.dumps(compact))


class LegacyAdapterTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.history = Path(self._tmp.name) / "scans_history.json"
        record = {
            "id": "old-scan", "created_at": "2026-01-01T10:00:00", "image_name": "a.png", "player_count": 1,
            "players": [{"nick": "legacy", "cheat_probability": 12.0, "steam": _legacy_steam()}],
        }
        self.history.write_text(json.dumps([record]), encoding="utf-8")
        self.file_before = self.history.read_bytes()
        patches = [
            mock.patch.object(server, "HISTORY_FILE", self.history),
            mock.patch.object(server, "_SCANS", []),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_load_history_fills_sources_for_legacy_records(self):
        server._load_history()
        steam = server._SCANS[0]["players"][0]["steam"]
        self.assertEqual({k: v["status"] for k, v in steam["sources"].items()},
                         {"leetify": "ok", "faceit": "ok", "cstracker": "blocked", "steam_stats": "ok"})
        self.assertEqual(steam["sources"]["leetify"]["data"], {"total_matches": 120, "aim": 77.0, "bans": []})

    def test_load_history_does_not_rewrite_the_file(self):
        server._load_history()
        self.assertEqual(self.history.read_bytes(), self.file_before)

    def test_load_history_keeps_existing_sources_untouched(self):
        record = json.loads(self.file_before)
        record[0]["players"][0]["steam"]["sources"] = {"custom": {"status": "ok", "data": {}, "fetched_at": "x"}}
        self.history.write_text(json.dumps(record), encoding="utf-8")
        server._load_history()
        self.assertEqual(list(server._SCANS[0]["players"][0]["steam"]["sources"]), ["custom"])


class RecheckRefreshTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        steam = _legacy_steam()
        steam["sources"] = REGISTRY.sources_view(steam)
        self.scan = {"id": "scan-1", "created_at": "2026-01-01T10:00:00", "image_name": "a.png",
                     "player_count": 1, "players": [{"nick": "legacy", "steam": steam}]}
        patches = [
            mock.patch.object(server, "HISTORY_FILE", Path(self._tmp.name) / "h.json"),
            mock.patch.object(server, "_SCANS", [self.scan]),
            mock.patch.object(server, "_broadcast_event"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_recheck_leetify_refreshes_only_leetify_in_sources(self):
        before = copy.deepcopy(self.scan["players"][0]["steam"])
        with mock.patch.object(steam_lookup, "_fetch_leetify", return_value={"aim": 99.0, "bans": []}):
            status, _body = server.recheck_source_for_player("scan-1", "legacy", "leetify")
        self.assertEqual(status, 200)
        steam = self.scan["players"][0]["steam"]
        self.assertEqual(steam["sources"]["leetify"]["data"], {"aim": 99.0, "bans": []})
        self.assertEqual(steam["sources"]["leetify"]["status"], "ok")
        for other in ("faceit", "cstracker", "steam_stats"):
            self.assertEqual(steam["sources"].get(other), before["sources"].get(other), other)
        # Coverage is deliberately NOT refreshed by recheck-source (current behaviour, see report).
        self.assertEqual(steam["coverage"], before["coverage"])
        self.assertEqual(steam["faceit"], before["faceit"])

    def test_recheck_cstracker_refreshes_only_cstracker_entry(self):
        before_leetify = copy.deepcopy(self.scan["players"][0]["steam"]["sources"]["leetify"])
        with mock.patch.object(steam_lookup, "_fetch_cstracker", return_value={"status": "ok", "trust": 90}):
            status, _ = server.recheck_source_for_player("scan-1", "legacy", "cstracker")
        self.assertEqual(status, 200)
        sources = self.scan["players"][0]["steam"]["sources"]
        self.assertEqual(sources["cstracker"]["status"], "ok")
        self.assertEqual(sources["cstracker"]["data"]["trust"], 90)
        self.assertEqual(sources["leetify"], before_leetify)

    def test_refresh_entry_removes_source_when_it_has_no_data_anymore(self):
        steam = _legacy_steam()
        steam["sources"] = REGISTRY.sources_view(steam)
        with mock.patch.object(steam_lookup, "_fetch_leetify", return_value=None):
            src = REGISTRY.get("leetify")
            src.write_raw(steam, None)
            server._refresh_source_entry(steam, src)
        self.assertNotIn("leetify", steam["sources"])


class NativeSourceTests(unittest.TestCase):
    class Lobby(DataSource):
        id = "lobby_native"
        label = "Lobby"
        in_coverage = True

        def fetch_raw(self, steamid64, ctx):
            return {"score": 4}

        def normalize(self, raw):
            return SourceResult("ok" if raw else "not_found", raw if isinstance(raw, dict) else {})

    def test_native_source_lives_in_steam_sources_and_shows_in_view(self):
        reg = SourceRegistry()
        reg.register(self.Lobby())
        steam = {"steamid64": SID, "leetify": {"aim": 50.0}}
        reg.get("lobby_native").write_raw(steam, {"score": 4})
        self.assertEqual(steam["sources"]["lobby_native"]["status"], "ok")
        view = reg.sources_view(steam)
        self.assertEqual(view["lobby_native"]["data"], {"score": 4})
        self.assertEqual(reg.get("lobby_native").read_raw(steam), {"score": 4})

    def test_native_entry_survives_legacy_write_and_refresh(self):
        reg = SourceRegistry()
        reg.register(self.Lobby())
        steam = {"steamid64": SID}
        reg.get("lobby_native").write_raw(steam, {"score": 4})
        legacy = REGISTRY.get("leetify")
        legacy.write_raw(steam, {"aim": 61.0})
        server._refresh_source_entry(steam, legacy)
        self.assertEqual(steam["sources"]["lobby_native"]["data"], {"score": 4})
        self.assertEqual(steam["sources"]["leetify"]["status"], "ok")
        self.assertEqual(reg.get("lobby_native").read_raw(steam), {"score": 4})


if __name__ == "__main__":
    unittest.main()
