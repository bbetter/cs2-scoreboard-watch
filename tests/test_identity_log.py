"""Identity corrections: which manual changes are recorded, rank bookkeeping, and safe file handling.

Run: /home/andrii/fun/.venv/bin/python -m unittest tests.test_identity_log -v
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import storage.identity_log as identity_log  # noqa: E402

BEFORE = {
    "steamid64": "76561198000000001",
    "candidates": [
        {"steamid64": "76561198000000001", "profile_name": "wrong", "score": 900.0, "avatar_match": 0.1, "in_cs2": True},
        {"steamid64": "76561198000000002", "profile_name": "right", "score": 500.0, "avatar_match": 0.0, "in_cs2": False},
    ],
}
AFTER = {"steamid64": "76561198000000002"}


class QueryMethodTests(unittest.TestCase):
    def test_classifies_how_the_user_identified_the_profile(self):
        self.assertEqual(identity_log.query_method("https://steamcommunity.com/id/abc/"), "url")
        self.assertEqual(identity_log.query_method("https://steamcommunity.com/profiles/76561198000000002"), "url")
        self.assertEqual(identity_log.query_method("76561198000000002"), "steamid64")
        self.assertEqual(identity_log.query_method("  some nick  "), "nick")


class BuildCorrectionTests(unittest.TestCase):
    def test_records_the_change_with_rank_and_candidates(self):
        entry = identity_log.build_correction(
            scan_id="s1", ocr_nick="wrng", before=BEFORE, after=AFTER, query="76561198000000002")
        self.assertEqual(entry["before_steamid64"], "76561198000000001")
        self.assertEqual(entry["after_steamid64"], "76561198000000002")
        self.assertEqual(entry["chosen_rank"], 1)
        self.assertEqual(entry["method"], "steamid64")
        self.assertEqual(len(entry["candidates"]), 2)

    def test_chosen_profile_not_offered_by_the_search_has_no_rank(self):
        entry = identity_log.build_correction(
            scan_id="s1", ocr_nick="x", before=BEFORE, after={"steamid64": "76561198000000009"}, query="https://steamcommunity.com/id/x/")
        self.assertIsNone(entry["chosen_rank"])
        self.assertEqual(entry["method"], "url")

    def test_unchanged_or_missing_identity_is_not_recorded(self):
        same = {"steamid64": BEFORE["steamid64"]}
        self.assertIsNone(identity_log.build_correction(scan_id="s", ocr_nick="x", before=BEFORE, after=same, query="q"))
        self.assertIsNone(identity_log.build_correction(scan_id="s", ocr_nick="x", before=BEFORE, after={}, query="q"))


class RecordFileTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "identity_corrections.json"

    def test_appends_in_order(self):
        identity_log.record({"n": 1}, self.path)
        identity_log.record({"n": 2}, self.path)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), [{"n": 1}, {"n": 2}])

    def test_keeps_only_the_newest_records(self):
        with mock.patch.object(identity_log, "MAX_RECORDS", 2):
            for n in range(4):
                identity_log.record({"n": n}, self.path)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), [{"n": 2}, {"n": 3}])

    def test_corrupt_file_is_moved_aside_not_overwritten_silently(self):
        self.path.write_text("{broken", encoding="utf-8")
        identity_log.record({"n": 1}, self.path)
        self.assertEqual((self.dir / "identity_corrections.json.corrupt").read_text(encoding="utf-8"), "{broken")
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), [{"n": 1}])


if __name__ == "__main__":
    unittest.main()
