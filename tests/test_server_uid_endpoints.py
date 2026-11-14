"""Uid-addressed endpoints: recheck removal race, ai-analyze by nick with duplicates, feedback by uid only,
and recheck-source honouring is_enabled.

Reuses the live-server base from tests/test_player_uid.py. Nothing real is read or written.
Run: /home/andrii/fun/.venv/bin/python -m unittest tests.test_server_uid_endpoints -v
"""

import copy
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.server as server  # noqa: E402
from sources import REGISTRY  # noqa: E402
import sources.leetify as leetify_source  # noqa: E402
from tests.test_player_uid import SID0, SID1, _LiveServer  # noqa: E402


class RecheckRemovedRowTests(_LiveServer):
    def test_row_removed_during_recheck_gives_409_and_others_stay(self):
        def fake_recheck(player_dict, query):
            # Another route removes this player while the recheck runs.
            self.scans[0]["players"] = [p for p in self.scans[0]["players"] if p["player_uid"] != "scan-u1-1"]
            return dict(player_dict, marker="rechecked")

        with mock.patch.object(server, "recheck_player_report", side_effect=fake_recheck), \
                mock.patch.object(server, "analyze_players_with_ai", side_effect=lambda ps, **k: (list(ps), None)):
            status, body = self.post("/api/recheck", {"scan_id": "scan-u1", "player_uid": "scan-u1-1", "query": "x"})
        self.assertEqual((status, body), (409, {"error": "Player was removed while rechecking"}))
        self.assertEqual(sorted(p["player_uid"] for p in self.scans[0]["players"]), ["scan-u1-0", "scan-u1-2"])
        self.assertFalse(any(p.get("marker") for p in self.scans[0]["players"]))


class AiAnalyzeNickDuplicatesTests(_LiveServer):
    def test_nick_with_duplicates_leaves_other_uids_untouched(self):
        before_solo = copy.deepcopy(self.player("scan-u1-2"))

        def fake_ai(dicts, force=False):
            return [dict(d, analysed=True) for d in dicts], None

        with mock.patch.object(server, "analyze_players_with_ai", side_effect=fake_ai):
            status, _ = self.post("/api/ai-analyze", {"scan_id": "scan-u1", "nick": "dup"})
        self.assertEqual(status, 200)
        uids = [p["player_uid"] for p in self.scans[0]["players"]]
        self.assertEqual(sorted(uids), ["scan-u1-0", "scan-u1-1", "scan-u1-2"])
        self.assertEqual(len(set(uids)), len(uids))
        self.assertTrue(self.player("scan-u1-0").get("analysed"))
        self.assertTrue(self.player("scan-u1-1").get("analysed"))
        self.assertEqual(self.player("scan-u1-2"), before_solo)


class FeedbackUidOnlyTests(_LiveServer):
    def test_uid_only_feedback_writes_the_right_player(self):
        with mock.patch.object(server, "append_feedback") as append:
            status, body = self.post("/api/feedback", {"scan_id": "scan-u1", "label": "cheater",
                                                       "player_uid": "scan-u1-1"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["entry"]["steamid64"], SID1)
        self.assertEqual(body["entry"]["nick"], "dup")
        append.assert_called_once_with(body["entry"])
        self.assertNotEqual(body["entry"]["steamid64"], SID0)


class RecheckSourceEnabledTests(_LiveServer):
    def test_disabled_source_gives_409_and_no_fetch(self):
        leetify = REGISTRY.get("leetify")
        with mock.patch.object(leetify, "is_enabled", return_value=False) as enabled, \
                mock.patch.object(leetify, "fetch_raw") as raw, \
                mock.patch.object(leetify_source, "fetch_leetify") as fetch:
            status, body = self.post("/api/recheck-source", {"scan_id": "scan-u1", "nick": "dup",
                                                             "source": "leetify", "player_uid": "scan-u1-1"})
        self.assertEqual(status, 409)
        self.assertEqual(body, {"error": "Джерело leetify вимкнене: не задано потрібний ключ"})
        enabled.assert_called_once()
        raw.assert_not_called()
        fetch.assert_not_called()
        self.assertIsNone(self.player("scan-u1-1")["steam"]["leetify"])


if __name__ == "__main__":
    import unittest

    unittest.main()
