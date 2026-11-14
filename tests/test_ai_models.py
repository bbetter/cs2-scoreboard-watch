"""Anthropic model alias resolution in lookup.ai_analyzer."""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import lookup.ai_analyzer as ai_analyzer  # noqa: E402


class ResolveAnthropicModelTests(unittest.TestCase):
    def test_aliases_resolve_case_and_spaces_insensitive(self):
        self.assertEqual(ai_analyzer.resolve_anthropic_model("sonnet"), "claude-sonnet-5-5")
        self.assertEqual(ai_analyzer.resolve_anthropic_model(" Sonnet "), "claude-sonnet-5-5")
        self.assertEqual(ai_analyzer.resolve_anthropic_model("HAIKU"), "claude-haiku-5-5")
        self.assertEqual(ai_analyzer.resolve_anthropic_model("opus"), "claude-opus-5-5")

    def test_empty_values_fall_back_to_default(self):
        for value in ("", None, "   "):
            with self.subTest(value=value):
                self.assertEqual(ai_analyzer.resolve_anthropic_model(value), ai_analyzer.DEFAULT_ANTHROPIC_MODEL)
        self.assertEqual(ai_analyzer.DEFAULT_ANTHROPIC_MODEL, "claude-sonnet-5-5")

    def test_full_ids_pass_through_unchanged(self):
        self.assertEqual(ai_analyzer.resolve_anthropic_model("claude-sonnet-5-5"), "claude-sonnet-5-5")
        self.assertEqual(ai_analyzer.resolve_anthropic_model("my-custom-model"), "my-custom-model")
        self.assertEqual(ai_analyzer.resolve_anthropic_model("  my-custom-model  "), "my-custom-model")

    def test_alias_targets_are_current_family(self):
        for alias, full_id in ai_analyzer.ANTHROPIC_MODEL_ALIASES.items():
            with self.subTest(alias=alias):
                self.assertNotIn("-4-", full_id)
                self.assertNotIn("3-5", full_id)
                self.assertNotIn("2025", full_id)


class RunAnthropicApiModelTests(unittest.TestCase):
    def test_request_body_carries_resolved_model(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["req"] = req
            raise RuntimeError("stop")

        with mock.patch.object(ai_analyzer.urllib.request, "urlopen", side_effect=fake_urlopen):
            with self.assertRaises(RuntimeError):
                ai_analyzer._run_anthropic_api("prompt", "sonnet", "test-key")

        body = json.loads(captured["req"].data.decode("utf-8"))
        self.assertEqual(body["model"], "claude-sonnet-5-5")


if __name__ == "__main__":
    unittest.main()
