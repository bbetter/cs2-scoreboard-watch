"""Stable player_uid: assignment, legacy adoption, history round trip, and uid-aware API routes.

Live server on an ephemeral port. STORE is a temp ScanStore, _broadcast_event
is mocked, and every external fetch/AI/feedback call is mocked. Nothing real is read or written.
Run: /home/andrii/fun/.venv/bin/python -m unittest discover -s tests -v
"""

import http.client
import json
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.pipeline as pipeline  # noqa: E402
import app.server as server  # noqa: E402
import lookup.steam_lookup as steam_lookup  # noqa: E402
import sources.leetify as leetify_source  # noqa: E402
from app.scan_store import ScanStore  # noqa: E402
from lookup.steam_lookup import PlayerReport, SteamProfile  # noqa: E402
from vision.scoreboard_scan import Player  # noqa: E402

SID0, SID1, SID2 = "76561198000000001", "76561198000000002", "76561198000000003"


def make_scan(scan_id="scan-u1"):
    def row(idx, nick, sid, kills, cheat):
        return {
            "nick": nick, "player_uid": f"{scan_id}-{idx}", "kills": kills, "deaths": 5, "kd": 1.0,
            "hs": 40.0, "level": 10, "cheat_probability": cheat, "smurf_probability": 5.0,
            "steam": {"steamid64": sid, "privacy_state": "public", "leetify": None, "faceit": {},
                      "extra_stats": {}, "cs2_stats": {}},
        }

    return {"id": scan_id, "created_at": "2026-01-01T10:00:00", "image_name": "a.png", "player_count": 3,
            "players": [row(0, "dup", SID0, 10, 20.0), row(1, "dup", SID1, 3, 40.0), row(2, "solo", SID2, 16, 5.0)],
            "ai_error": None, "scan_status": "done", "progress": {"done": 3, "total": 3}}


class UidAssignmentTests(unittest.TestCase):
    def test_pipeline_assigns_scan_id_and_index(self):
        players = [Player(nick="dup", kills=1, deaths=1, kd=1.0, hs=0.0, level=1),
                   Player(nick="dup", kills=2, deaths=1, kd=2.0, hs=0.0, level=1)]
        pipeline.assign_player_uids("abc123", players)
        self.assertEqual([p.player_uid for p in players], ["abc123-0", "abc123-1"])

    def test_default_uid_is_empty_on_player_and_report(self):
        self.assertEqual(Player(nick="x", kills=0, deaths=0, kd=0.0, hs=0.0, level=0).player_uid, "")
        self.assertEqual(PlayerReport.__dataclass_fields__["player_uid"].default, "")

    def test_report_carries_the_player_uid(self):
        p = Player(nick="x", kills=0, deaths=0, kd=0.0, hs=0.0, level=0, player_uid="scan-7-0")
        report = steam_lookup._build_player_report(p, SteamProfile(query_nick="x"))
        self.assertEqual(report.player_uid, "scan-7-0")


class LegacyAdoptionTests(unittest.TestCase):
    def test_rows_without_uid_get_scan_id_and_index(self):
        scan = {"id": "old9", "players": [{"nick": "a"}, {"nick": "b", "player_uid": ""}]}
        server._adapt_legacy_scan(scan)
        self.assertEqual([p["player_uid"] for p in scan["players"]], ["old9-0", "old9-1"])

    def test_existing_uids_are_left_alone(self):
        scan = {"id": "old9", "players": [{"nick": "a", "player_uid": "keep-me"}, {"nick": "b"}]}
        server._adapt_legacy_scan(scan)
        self.assertEqual([p["player_uid"] for p in scan["players"]], ["keep-me", "old9-1"])

    def test_non_dict_rows_are_skipped(self):
        scan = {"id": "old9", "players": ["junk", {"nick": "a"}]}
        server._adapt_legacy_scan(scan)
        self.assertEqual(scan["players"][0], "junk")
        self.assertEqual(scan["players"][1]["player_uid"], "old9-1")


class HistoryRoundTripTests(unittest.TestCase):
    def test_legacy_file_loads_with_uids_and_survives_a_save_and_reload(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        history = Path(tmp.name) / "scans_history.json"
        legacy = make_scan("legacy-1")
        for row in legacy["players"]:
            row.pop("player_uid")
        history.write_text(json.dumps([legacy]), encoding="utf-8")

        store = ScanStore(history)
        with mock.patch.object(server, "STORE", store):
            scans = store.scans
            server._load_history()
            self.assertEqual([p["player_uid"] for p in scans[0]["players"]],
                             ["legacy-1-0", "legacy-1-1", "legacy-1-2"])
            server._save_history_locked()

        saved = json.loads(history.read_text(encoding="utf-8"))
        self.assertEqual([p["player_uid"] for p in saved[0]["players"]], ["legacy-1-0", "legacy-1-1", "legacy-1-2"])

        store2 = ScanStore(history)
        with mock.patch.object(server, "STORE", store2):
            scans2 = store2.scans
            server._load_history()
            self.assertEqual([p["player_uid"] for p in scans2[0]["players"]],
                             ["legacy-1-0", "legacy-1-1", "legacy-1-2"])


class _LiveServer(unittest.TestCase):
    """Base: temp history, temp ScanStore, mocked broadcast, live handler on an ephemeral port."""

    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.CsWatchHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = ScanStore(Path(tmp.name) / "history.json")
        self.store.scans[:] = [make_scan()]
        self.scans = self.store.scans
        patches = [
            mock.patch.object(server, "STORE", self.store),
            mock.patch.object(server, "_broadcast_event"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def post(self, path, body):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("POST", path, body=json.dumps(body).encode("utf-8"),
                         headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read() or b"{}")
        finally:
            conn.close()

    def player(self, uid):
        return next(p for p in self.scans[0]["players"] if p["player_uid"] == uid)


class RecheckSourceUidTests(_LiveServer):
    def test_uid_selects_the_second_duplicate(self):
        with mock.patch.object(leetify_source, "fetch_leetify", return_value={"aim": 55.0}) as fetch:
            status, body = self.post("/api/recheck-source",
                                     {"scan_id": "scan-u1", "nick": "dup", "source": "leetify",
                                      "player_uid": "scan-u1-1"})
        self.assertEqual(status, 200)
        fetch.assert_called_once_with(SID1)
        self.assertEqual(self.player("scan-u1-1")["steam"]["leetify"], {"aim": 55.0})
        self.assertIsNone(self.player("scan-u1-0")["steam"]["leetify"])

    def test_nick_only_still_picks_the_first_match(self):
        with mock.patch.object(leetify_source, "fetch_leetify", return_value={"aim": 1.0}) as fetch:
            status, _ = self.post("/api/recheck-source", {"scan_id": "scan-u1", "nick": "dup", "source": "leetify"})
        self.assertEqual(status, 200)
        fetch.assert_called_once_with(SID0)

    def test_unknown_uid_is_404(self):
        status, body = self.post("/api/recheck-source", {"scan_id": "scan-u1", "nick": "dup", "source": "leetify",
                                                         "player_uid": "nope"})
        self.assertEqual((status, body), (404, {"error": "Player not found in scan"}))

    def test_missing_nick_and_uid_is_the_same_400_as_before(self):
        status, body = self.post("/api/recheck-source", {"scan_id": "scan-u1", "source": "leetify"})
        self.assertEqual((status, body), (400, {"error": "scan_id і nick є обов'язковими"}))


class FeedbackUidTests(_LiveServer):
    def test_uid_with_nick_selects_the_right_duplicate(self):
        with mock.patch.object(server, "append_feedback") as append:
            status, body = self.post("/api/feedback", {"scan_id": "scan-u1", "nick": "dup", "label": "cheater",
                                                       "player_uid": "scan-u1-1"})
        self.assertEqual(status, 200)
        entry = body["entry"]
        self.assertEqual(entry["steamid64"], SID1)
        self.assertEqual(entry["nick"], "dup")
        append.assert_called_once()

    def test_nick_only_feedback_still_uses_the_first_match(self):
        with mock.patch.object(server, "append_feedback"):
            status, body = self.post("/api/feedback", {"scan_id": "scan-u1", "nick": "dup", "label": "legit"})
        self.assertEqual(body["entry"]["steamid64"], SID0)

    def test_unknown_uid_is_404(self):
        with mock.patch.object(server, "append_feedback") as append:
            status, body = self.post("/api/feedback", {"scan_id": "scan-u1", "nick": "dup", "label": "legit",
                                                       "player_uid": "nope"})
        self.assertEqual((status, body), (404, {"error": "Player not found in scan"}))
        append.assert_not_called()

    def test_uid_only_feedback_is_accepted_and_names_the_right_player(self):
        with mock.patch.object(server, "append_feedback") as append:
            status, body = self.post("/api/feedback", {"scan_id": "scan-u1", "label": "legit",
                                                       "player_uid": "scan-u1-1"})
        self.assertEqual(status, 200)
        self.assertEqual(body["entry"]["steamid64"], SID1)
        append.assert_called_once()

    def test_neither_nick_nor_uid_is_rejected_by_validation(self):
        with mock.patch.object(server, "append_feedback") as append:
            status, body = self.post("/api/feedback", {"scan_id": "scan-u1", "label": "legit"})
        self.assertEqual((status, body), (400, {"error": "nick or player_uid is required"}))
        append.assert_not_called()


class RecheckUidTests(_LiveServer):
    def test_uid_selects_the_row_and_only_that_row_is_replaced(self):
        seen = {}

        def fake_recheck(player_dict, query):
            seen["player"] = dict(player_dict)
            return dict(player_dict, cheat_probability=99.0, marker="rechecked")

        with mock.patch.object(server, "recheck_player_report", side_effect=fake_recheck), \
                mock.patch.object(server, "analyze_players_with_ai", side_effect=lambda ps, **k: (list(ps), None)):
            status, body = self.post("/api/recheck", {"scan_id": "scan-u1", "old_nick": "dup",
                                                      "player_uid": "scan-u1-1", "query": "76561198000000009"})
        self.assertEqual(status, 200)
        self.assertEqual(seen["player"]["player_uid"], "scan-u1-1")
        marked = [p for p in self.scans[0]["players"] if p.get("marker") == "rechecked"]
        self.assertEqual([p["player_uid"] for p in marked], ["scan-u1-1"])
        self.assertEqual(self.player("scan-u1-0")["kills"], 10)
        self.assertEqual(len(self.scans[0]["players"]), 3)

    def test_unknown_uid_is_404_and_missing_fields_are_400(self):
        status, body = self.post("/api/recheck", {"scan_id": "scan-u1", "player_uid": "nope", "query": "x"})
        self.assertEqual((status, body), (404, {"error": "Player not found in scan"}))
        status, body = self.post("/api/recheck", {"scan_id": "scan-u1", "query": "x"})
        self.assertEqual((status, body), (400, {"error": "scan_id, old_nick, and query are required"}))


class AiAnalyzeUidTests(_LiveServer):
    def test_uid_analyzes_only_that_player(self):
        def fake_ai(dicts, force=False):
            return [dict(d, ai_marker=True) for d in dicts], None

        with mock.patch.object(server, "analyze_players_with_ai", side_effect=fake_ai) as analyze:
            status, _ = self.post("/api/ai-analyze", {"scan_id": "scan-u1", "player_uid": "scan-u1-1"})
        self.assertEqual(status, 200)
        self.assertEqual([d["player_uid"] for d in analyze.call_args.args[0]], ["scan-u1-1"])
        self.assertTrue(self.player("scan-u1-1").get("ai_marker"))
        self.assertFalse(self.player("scan-u1-0").get("ai_marker"))
        self.assertFalse(self.player("scan-u1-2").get("ai_marker"))

    def test_nick_path_keeps_each_duplicate_its_own_row(self):
        # The nick path maps results by player_uid, so both "dup" rows keep their identity.
        def fake_ai(dicts, force=False):
            return [dict(d, analysed_kills=d["kills"]) for d in dicts], None

        with mock.patch.object(server, "analyze_players_with_ai", side_effect=fake_ai):
            self.post("/api/ai-analyze", {"scan_id": "scan-u1", "nick": "dup"})
        self.assertEqual(sorted(p["player_uid"] for p in self.scans[0]["players"]),
                         ["scan-u1-0", "scan-u1-1", "scan-u1-2"])
        self.assertEqual(self.player("scan-u1-0").get("analysed_kills"), 10)
        self.assertEqual(self.player("scan-u1-1").get("analysed_kills"), 3)


class RaceRegressionTests(_LiveServer):
    """Races reproduced deterministically. Each one used to lose or misplace a row; they now run as regular tests."""

    def test_stale_enrichment_of_an_old_run_does_not_overwrite_a_rescan(self):
        from app.events import EventBus
        from tests.test_pipeline import FakeEnrich, FakeRepo

        deferred = []

        repo_obj = FakeRepo()
        runs = iter([
            [Player(nick="dup", kills=10, deaths=5, kd=2.0, hs=40.0, level=10, score=30.0)],
            [Player(nick="dup", kills=99, deaths=1, kd=9.0, hs=40.0, level=10, score=30.0)],
        ])
        p = pipeline.ScanPipeline(
            repo_obj, EventBus(), ocr=lambda path: (next(runs), None), enrich=FakeEnrich(),
            analyze_ai=lambda d: (d, None), ai_enabled=lambda: False,
            match_meta=lambda now, st: ("m", None), screenshot_url=lambda path: None,
            spawn=deferred.append,
        )
        IMG = Path("/tmp/race.png")
        rec = p.run_new(IMG, fetch_steam=True)           # run 1: enrichment deferred
        p.rescan(rec, IMG)                                # run 2 replaces the rows (same scan id, same uids)
        deferred[0]()                                     # run 1's enrichment finishes late
        self.assertEqual(rec["scan_status"], "loading_steam")   # run 2 is still in progress
        self.assertEqual(rec["players"][0]["kills"], 99)         # run 2's rows must not be overwritten

    def test_recheck_route_writes_back_to_the_right_row_after_a_concurrent_reorder(self):
        scan = make_scan("scan-race")
        def fake_recheck(player_dict, query):
            # Simulate a concurrent change that rotates the rows while the recheck runs: the stale index now
            # points at a different player than the one that was rechecked.
            scan["players"] = scan["players"][1:] + scan["players"][:1]
            return dict(player_dict, marker="rechecked")
        self.scans[:] = [scan]
        with mock.patch.object(server, "recheck_player_report", side_effect=fake_recheck), \
                mock.patch.object(server, "analyze_players_with_ai", side_effect=lambda ps, **k: (list(ps), None)):
            self.post("/api/recheck", {"scan_id": "scan-race", "player_uid": "scan-race-1", "query": "x"})
        uids = sorted(p["player_uid"] for p in scan["players"])
        self.assertEqual(uids, ["scan-race-0", "scan-race-1", "scan-race-2"])  # no row may be lost or duplicated


if __name__ == "__main__":
    unittest.main()
