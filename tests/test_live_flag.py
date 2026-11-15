"""The live flag from each entry point reaches enrichment: a fresh capture uses in-game signals, old screenshots do not.

Run: /home/andrii/fun/.venv/bin/python -m unittest tests.test_live_flag -v
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.test_pipeline import IMG, FakeEnrich, Harness  # noqa: E402


class LiveFlagTests(unittest.TestCase):
    def test_new_scan_is_live_by_default(self):
        h = Harness(enrich=FakeEnrich())
        h.pipeline.run_new(IMG, fetch_steam=True)
        self.assertEqual(h.enrich.live_flags, [True])

    def test_upload_is_not_live(self):
        h = Harness(enrich=FakeEnrich())
        h.pipeline.run_new(IMG, fetch_steam=True, live=False)
        self.assertEqual(h.enrich.live_flags, [False])

    def test_rescan_of_an_old_screenshot_is_not_live(self):
        h = Harness(enrich=FakeEnrich())
        rec = h.pipeline.run_new(IMG, fetch_steam=True)
        h.pipeline.rescan(rec, IMG)
        self.assertEqual(h.enrich.live_flags, [True, False])


if __name__ == "__main__":
    unittest.main()
