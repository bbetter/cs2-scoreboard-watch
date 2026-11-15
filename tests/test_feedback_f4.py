"""Tests for feedback validation and the append-only feedback.json store (F4)."""

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from storage.feedback_store import (  # noqa: E402
    append_feedback,
    build_feedback_entry,
    validate_feedback_payload,
)


class ValidationTests(unittest.TestCase):
    def test_valid_labels_accepted(self):
        for label in ("cheater", "legit", "unsure"):
            clean, err = validate_feedback_payload({"scan_id": "abc", "nick": " x ", "label": label})
            self.assertIsNone(err)
            self.assertEqual(clean, {"scan_id": "abc", "nick": "x", "player_uid": None, "label": label})

    def test_player_uid_alone_is_accepted(self):
        clean, err = validate_feedback_payload({"scan_id": "abc", "player_uid": " abc-1 ", "label": "legit"})
        self.assertIsNone(err)
        self.assertEqual(clean, {"scan_id": "abc", "nick": "", "player_uid": "abc-1", "label": "legit"})

    def test_missing_nick_and_uid_gives_the_new_message(self):
        clean, err = validate_feedback_payload({"scan_id": "abc", "label": "legit"})
        self.assertIsNone(clean)
        self.assertEqual(err, "nick or player_uid is required")

    def test_overlong_player_uid_does_not_replace_a_missing_nick(self):
        clean, err = validate_feedback_payload({"scan_id": "abc", "player_uid": "u" * 65, "label": "legit"})
        self.assertIsNone(clean)
        self.assertEqual(err, "nick or player_uid is required")

    def test_invalid_payloads_rejected(self):
        bad = [
            None,
            [],
            {"scan_id": "", "nick": "x", "label": "legit"},
            {"scan_id": "abc", "nick": "", "label": "legit"},
            {"scan_id": "abc", "nick": "x" * 101, "label": "legit"},
            {"scan_id": "abc", "nick": "x", "label": "CHEATER"},
            {"scan_id": "abc", "nick": "x"},
            {"scan_id": 5, "nick": "x", "label": "legit"},
        ]
        for payload in bad:
            clean, err = validate_feedback_payload(payload)
            self.assertIsNone(clean, payload)
            self.assertIsNotNone(err, payload)


class EntryAndStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "feedback.json"

    def tearDown(self):
        self.tmp.cleanup()

    def sample_player(self):
        return {
            "nick": "suspect",
            "kd": 1.9,
            "hs": 61.0,
            "level": 4,
            "anomaly_score": 70.0,
            "cheat_probability": 62.5,
            "smurf_probability": 31.0,
            "uncertainty": 45.0,
            "steam": {
                "steamid64": "76561190000000002",
                "account_age_years": 0.9,
                "cs2_hours": 120.0,
                "vac_banned": False,
                "privacy_state": "public",
                "leetify": {"aim": 88.0, "reaction_time_ms": 310, "accuracy_head": 52.0, "winrate": 66.0, "total_matches": 40},
                "extra_stats": {"cstracker": {"status": "ok"}},
            },
        }

    def test_entry_snapshot_contains_scores_and_features(self):
        entry = build_feedback_entry("scan-1", "cheater", self.sample_player())
        self.assertEqual(entry["scan_id"], "scan-1")
        self.assertEqual(entry["nick"], "suspect")
        self.assertEqual(entry["steamid64"], "76561190000000002")
        self.assertEqual(entry["label"], "cheater")
        self.assertEqual(entry["cheat_probability"], 62.5)
        self.assertEqual(entry["smurf_probability"], 31.0)
        self.assertTrue(entry["features"]["leetify"]["present"])
        self.assertEqual(entry["features"]["cstracker_status"], "ok")
        self.assertIn("T", entry["ts"])

    def test_append_creates_and_extends_list(self):
        e1 = build_feedback_entry("scan-1", "legit", self.sample_player())
        e2 = build_feedback_entry("scan-1", "unsure", self.sample_player())
        append_feedback(e1, self.path)
        append_feedback(e2, self.path)
        records = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual([r["label"] for r in records], ["legit", "unsure"])

    def test_concurrent_appends_are_not_lost(self):
        entries = [build_feedback_entry(f"scan-{i}", "legit", self.sample_player()) for i in range(40)]
        threads = [threading.Thread(target=append_feedback, args=(e, self.path)) for e in entries]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        records = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(len(records), 40)
        self.assertEqual({r["scan_id"] for r in records}, {e["scan_id"] for e in entries})

    def test_corrupt_file_is_kept_aside_not_overwritten_silently(self):
        self.path.write_text("{not json", encoding="utf-8")
        append_feedback(build_feedback_entry("scan-1", "legit", self.sample_player()), self.path)
        self.assertTrue(self.path.with_name(self.path.name + ".corrupt").exists())
        records = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(len(records), 1)


if __name__ == "__main__":
    unittest.main()
