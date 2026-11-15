"""Freezes ai_analyzer._compact_player_for_ai output for every golden player (and synthetic fallbacks).

Regenerate ONLY after an intentional change:  python tests/test_ai_compact_parity.py --regen
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import json  # noqa: E402

from parity_cases import GOLDEN_DIR, cases, jsonable  # noqa: E402

import lookup.ai_analyzer as ai_analyzer  # noqa: E402

GOLDEN = GOLDEN_DIR / "ai_compact_golden.json"


def _compute() -> dict:
    return {name: jsonable(ai_analyzer._compact_player_for_ai(player)) for name, player in cases().items()}


def _regen() -> None:
    GOLDEN.write_text(json.dumps(_compute(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {GOLDEN} ({len(cases())} cases)")


class AiCompactParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.golden = json.loads(GOLDEN.read_text(encoding="utf-8"))

    def test_case_set_matches_golden(self):
        self.assertEqual(set(self.golden), set(cases()))

    def test_every_case_matches_frozen_output(self):
        actual = _compute()
        for name in self.golden:
            with self.subTest(case=name):
                self.assertEqual(actual[name], self.golden[name], f"ai compact output changed for {name!r}")


if __name__ == "__main__":
    if "--regen" in sys.argv:
        _regen()
        sys.exit(0)
    unittest.main()
