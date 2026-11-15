"""A source registered in sources.REGISTRY reaches the AI payload and feedback features without code changes.

The built-in registry is copied and an `ExampleSource` is added; both consumers are pointed at the copy with
mock.patch.object. Output must stay identical to the frozen goldens when the example source has no data.
"""

import copy
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import lookup.ai_analyzer as ai_analyzer  # noqa: E402
import storage.feedback_store as feedback_store  # noqa: E402
from parity_cases import GOLDEN_DIR, cases, jsonable  # noqa: E402
from sources import REGISTRY  # noqa: E402
from sources.base import DataSource, SourceRegistry, SourceResult  # noqa: E402

SCAN_ID = "scan-golden"
LABEL = "unsure"


class ExampleSource(DataSource):
    id = "example"
    label = "Example"

    def fetch_raw(self, steamid64, ctx):
        return {"x": 1}

    def features(self, result: SourceResult):
        return {"score": 1.0} if result.ok else {}


def _registry_with_example() -> SourceRegistry:
    reg = SourceRegistry()
    for src in REGISTRY:
        reg.register(src)
    reg.register(ExampleSource())
    return reg


def _steam_with_example(data=None) -> dict:
    steam = {"steamid64": "76561198000000042", "cs2_hours": 300.0}
    if data is not None:
        steam["sources"] = {"example": {"status": "ok", "data": data, "fetched_at": ""}}
    return steam


def _player(steam: dict) -> dict:
    return {"nick": "ex", "kd": 1.2, "hs": 40.0, "level": 5, "steam": steam}


class RegistryPayloadTests(unittest.TestCase):
    def setUp(self):
        self.reg = _registry_with_example()

    def test_builtin_payload_names(self):
        names = {s.id: s.payload_name() for s in REGISTRY}
        self.assertEqual(names, {"leetify": "leetify", "faceit": "faceit", "cstracker": None, "steam_stats": None})

    def test_example_block_in_ai_payload_when_data_present(self):
        steam = _steam_with_example({"x": 1})
        steam["faceit"] = {"found": True, "skill_level": 7}
        with mock.patch.object(ai_analyzer, "REGISTRY", self.reg):
            data = ai_analyzer._compact_player_for_ai(_player(steam))
        self.assertEqual(data["example"], {"x": 1})
        keys = list(data)
        self.assertLess(keys.index("faceit"), keys.index("example"))

    def test_no_example_block_in_ai_payload_without_data(self):
        player = _player(_steam_with_example())
        with mock.patch.object(ai_analyzer, "REGISTRY", self.reg):
            data = ai_analyzer._compact_player_for_ai(player)
        self.assertNotIn("example", data)

    def test_example_block_in_feedback_when_data_present(self):
        player = _player(_steam_with_example({"x": 1}))
        with mock.patch.object(feedback_store, "REGISTRY", self.reg):
            entry = feedback_store.build_feedback_entry(SCAN_ID, LABEL, player)
        self.assertEqual(entry["features"]["example"], {"present": True, "score": 1.0})

    def test_no_example_block_in_feedback_without_data(self):
        player = _player(_steam_with_example())
        with mock.patch.object(feedback_store, "REGISTRY", self.reg):
            entry = feedback_store.build_feedback_entry(SCAN_ID, LABEL, player)
        self.assertNotIn("example", entry["features"])

    def test_players_without_example_match_golden(self):
        ai_golden = json.loads((GOLDEN_DIR / "ai_compact_golden.json").read_text(encoding="utf-8"))
        fb_golden = json.loads((GOLDEN_DIR / "feedback_golden.json").read_text(encoding="utf-8"))
        with mock.patch.object(ai_analyzer, "REGISTRY", self.reg), \
                mock.patch.object(feedback_store, "REGISTRY", self.reg):
            for name, player in cases().items():
                with self.subTest(case=name):
                    ai_out = jsonable(ai_analyzer._compact_player_for_ai(copy.deepcopy(player)))
                    self.assertEqual(ai_out, ai_golden[name])
                    fb = feedback_store.build_feedback_entry(SCAN_ID, LABEL, copy.deepcopy(player))
                    fb.pop("ts", None)
                    self.assertEqual(jsonable(fb), fb_golden[name])


if __name__ == "__main__":
    unittest.main()
