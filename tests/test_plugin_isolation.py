"""A third-party source whose methods raise must not break the AI payload, feedback or sources_view.

Run: /home/andrii/fun/.venv/bin/python -m unittest tests.test_plugin_isolation -v
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import lookup.ai_analyzer as ai_analyzer  # noqa: E402
import storage.feedback_store as feedback_store  # noqa: E402
from sources import SourceRegistry  # noqa: E402
from sources.base import DataSource  # noqa: E402


class ExplodingSource(DataSource):
    id = "boom"
    label = "Boom"
    payload_key = "boom"

    def fetch_raw(self, steamid64, ctx):
        return {"x": 1}

    def normalize(self, raw):
        raise RuntimeError("normalize exploded")

    def ai_compact(self, result):
        raise RuntimeError("ai_compact exploded")

    def features(self, result):
        raise RuntimeError("features exploded")

    def result_from(self, steam):
        raise RuntimeError("result_from exploded")


def _registry():
    reg = SourceRegistry()
    reg.register(ExplodingSource(), builtin=False)
    return reg


PLAYER = {
    "nick": "p",
    "kills": 10,
    "deaths": 5,
    "kd": 2.0,
    "steam": {"steamid64": "76561198000000001", "sources": {"boom": {"status": "ok", "data": {"x": 1}}}},
}


class PluginIsolationTests(unittest.TestCase):
    def test_ai_payload_leaves_out_the_broken_block_and_keeps_going(self):
        with mock.patch.object(ai_analyzer, "REGISTRY", _registry()), self.assertLogs("cs2watch.ai", level="ERROR"):
            data = ai_analyzer._compact_player_for_ai(PLAYER)
        self.assertNotIn("boom", data)
        self.assertEqual(data["nick"], "p")

    def test_feedback_entry_is_built_without_the_broken_features(self):
        with mock.patch.object(feedback_store, "REGISTRY", _registry()), \
                self.assertLogs("cs2watch.feedback", level="ERROR"):
            entry = feedback_store.build_feedback_entry("scan1", "legit", PLAYER)
        self.assertNotIn("boom", entry["features"])
        self.assertEqual(entry["nick"], "p")

    def test_sources_view_skips_a_source_that_cannot_read_its_result(self):
        reg = _registry()
        with self.assertLogs("cs2watch.sources", level="ERROR"):
            view = reg.sources_view(PLAYER["steam"])
            cov = reg.coverage(PLAYER["steam"])
        self.assertEqual(view, {})
        self.assertEqual(cov, {"boom": False})


if __name__ == "__main__":
    unittest.main()
