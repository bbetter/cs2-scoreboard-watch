"""Freezes feedback_store.build_feedback_entry output (ts removed) for every golden player and synthetic case.

Regenerate ONLY after an intentional change:  python tests/test_feedback_parity.py --regen
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from parity_cases import GOLDEN_DIR, cases, jsonable  # noqa: E402

import storage.feedback_store as feedback_store  # noqa: E402

GOLDEN = GOLDEN_DIR / "feedback_golden.json"
SCAN_ID = "scan-golden"
LABEL = "unsure"


def _compute() -> dict:
    out = {}
    for name, player in cases().items():
        entry = feedback_store.build_feedback_entry(SCAN_ID, LABEL, player)
        entry.pop("ts", None)  # wall-clock field, not part of the frozen contract
        out[name] = jsonable(entry)
    return out


def _regen() -> None:
    GOLDEN.write_text(json.dumps(_compute(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {GOLDEN} ({len(cases())} cases)")


class FeedbackParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.golden = json.loads(GOLDEN.read_text(encoding="utf-8"))

    def test_case_set_matches_golden(self):
        self.assertEqual(set(self.golden), set(cases()))

    def test_every_case_matches_frozen_output(self):
        actual = _compute()
        for name in self.golden:
            with self.subTest(case=name):
                self.assertEqual(actual[name], self.golden[name], f"feedback entry changed for {name!r}")

    def test_ts_is_a_timestamp(self):
        entry = feedback_store.build_feedback_entry(SCAN_ID, LABEL, next(iter(cases().values())))
        self.assertRegex(entry["ts"], r"^\d{4}-\d{2}-\d{2}T")


if __name__ == "__main__":
    if "--regen" in sys.argv:
        _regen()
        sys.exit(0)
    unittest.main()
