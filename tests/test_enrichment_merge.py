"""Enrichment must not overwrite rows the user changed after the run started (T12).

Pipeline cases use the fakes from tests/test_pipeline.py (no OCR, no network, enrichment run on demand).
Route cases check that the hand-edit paths stamp `edited_at` (fetchers are mocked, history is a temp file).
Run: /home/andrii/fun/.venv/bin/python -m unittest discover -s tests -v
"""

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.server as server  # noqa: E402
import sources.leetify as leetify_source  # noqa: E402
from tests.test_pipeline import IMG, FakeEnrich, Harness  # noqa: E402
from tests.test_recheck_source import _make_scan, _store  # noqa: E402


def _deferred():
    """A spawn that keeps the enrichment callable, so the test decides when it runs."""
    queue = []
    return queue, queue.append


def _row(h, uid):
    return next(p for p in h.repo.scans[0]["players"] if p["player_uid"] == uid)


class UserEditDuringRunTests(unittest.TestCase):
    def test_user_edit_after_start_survives_final_write(self):
        queue, spawn = _deferred()
        h = Harness(spawn=spawn)
        rec = h.pipeline.run_new(IMG, fetch_steam=True)
        uid0, uid1, uid2 = (f"{rec['id']}-{i}" for i in range(3))
        _row(h, uid0).update(marker="user", edited_at=time.time() + 1)
        self.assertEqual(len(queue), 1)
        queue[0]()
        final = {p["player_uid"]: p for p in h.repo.scans[0]["players"]}
        self.assertEqual(final[uid0]["marker"], "user")
        self.assertIsNone(final[uid0]["steam"]["profile_name"])   # the user's row, not the enriched one
        self.assertEqual(final[uid1]["steam"]["profile_name"], f"prof-{uid1}")
        self.assertEqual(final[uid2]["steam"]["profile_name"], f"prof-{uid2}")
        self.assertNotIn("marker", final[uid1])
        self.assertEqual(h.repo.scans[0]["scan_status"], "done")
        self.assertEqual(h.repo.scans[0]["progress"], {"done": 3, "total": 3})

    def test_edit_from_before_the_run_is_overwritten(self):
        queue, spawn = _deferred()
        h = Harness(spawn=spawn)
        rec = h.pipeline.run_new(IMG, fetch_steam=True)
        uid0 = f"{rec['id']}-0"
        _row(h, uid0).update(marker="user", edited_at=time.time() - 3600)
        queue[0]()
        row = _row(h, uid0)
        self.assertNotIn("marker", row)
        self.assertEqual(row["steam"]["profile_name"], f"prof-{uid0}")

    def test_edit_during_ai_stage_keeps_user_row_without_ai_fields(self):
        holder = {}

        def ai(dicts):
            # The user changes player 0 while the AI stage is running.
            _row(holder["h"], holder["uid0"]).update(marker="user", edited_at=time.time() + 1)
            return [dict(d, ai_marker=True) for d in dicts], None

        queue, spawn = _deferred()
        h = Harness(ai=True, ai_fn=ai, spawn=spawn)
        holder["h"] = h
        rec = h.pipeline.run_new(IMG, fetch_steam=True)
        holder["uid0"] = f"{rec['id']}-0"
        queue[0]()
        final = {p["player_uid"]: p for p in h.repo.scans[0]["players"]}
        self.assertEqual(final[holder["uid0"]]["marker"], "user")
        self.assertNotIn("ai_marker", final[holder["uid0"]])
        self.assertTrue(final[f"{rec['id']}-1"]["ai_marker"])
        self.assertTrue(final[f"{rec['id']}-2"]["ai_marker"])

    def test_row_order_follows_enrichment_output_even_with_user_row(self):
        queue, spawn = _deferred()
        h = Harness(spawn=spawn, enrich=FakeEnrich(order=lambda players: [2, 1, 0]))
        rec = h.pipeline.run_new(IMG, fetch_steam=True)
        uids = [f"{rec['id']}-{i}" for i in range(3)]
        _row(h, uids[1]).update(marker="user", edited_at=time.time() + 1)
        queue[0]()
        self.assertEqual([p["player_uid"] for p in h.repo.scans[0]["players"]], uids)

    def test_clean_enrichment_never_adds_edited_at(self):
        h = Harness()
        rec = h.pipeline.run_new(IMG, fetch_steam=True)
        for p in rec["players"]:
            self.assertNotIn("edited_at", p)


class RouteStampTests(unittest.TestCase):
    def test_mark_edited_sets_timestamp_and_returns_the_row(self):
        row = {"nick": "alpha"}
        with mock.patch.object(server.time, "time", return_value=123.0):
            out = server._mark_edited(row)
        self.assertIs(out, row)
        self.assertEqual(row["edited_at"], 123.0)

    def test_recheck_source_stamps_the_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(Path(tmp) / "history.json", [_make_scan()])
            with mock.patch.object(server, "STORE", store), \
                    mock.patch.object(server, "_broadcast_event"), \
                    mock.patch.object(leetify_source, "fetch_leetify", return_value={"aim": 99}), \
                    mock.patch.object(server.time, "time", return_value=500.0):
                status, _ = server.recheck_source_for_player("scan-1", "alpha", "leetify")
            self.assertEqual(status, 200)
            alpha = next(p for p in store.scans[0]["players"] if p["nick"] == "alpha")
            beta = next(p for p in store.scans[0]["players"] if p["nick"] == "beta")
            self.assertEqual(alpha["edited_at"], 500.0)
            self.assertNotIn("edited_at", beta)


if __name__ == "__main__":
    unittest.main()
