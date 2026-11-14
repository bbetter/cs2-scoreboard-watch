"""Config schema: coverage, uniqueness, descriptors (no values), generic merge rules, GET /api/config/schema.

Temp config files and fake keys only. Run: /home/andrii/fun/.venv/bin/python -m unittest discover -s tests -v
"""

import http.client
import json
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import storage.config_schema as config_schema  # noqa: E402
import storage.config_store as config_store  # noqa: E402
import app.server as server  # noqa: E402
from storage.config_fields import Secret  # noqa: E402
from sources import DataSource, SourceRegistry  # noqa: E402

FAKE_KEY = "sk-TESTKEY-SCHEMAPROBE-1234"


class SchemaCoverageTests(unittest.TestCase):
    def test_schema_covers_exactly_the_default_config_keys(self):
        self.assertEqual([f.name for f in config_schema.SCHEMA], list(config_store.DEFAULT_CONFIG))

    def test_every_key_is_declared_once_and_source_keys_come_from_sources(self):
        names = [f.name for f in config_schema.SCHEMA]
        self.assertEqual(len(names), len(set(names)))
        from sources import REGISTRY
        for src in REGISTRY:
            for f in src.settings:
                self.assertIs(config_schema.FIELDS[f.name], f)

    def test_secrets_and_their_flags(self):
        secrets = {f.name: f for f in config_schema.SECRETS}
        self.assertEqual(set(secrets), {"steam_api_key", "faceit_api_key", "anthropic_api_key", "leetify_api_key"})
        self.assertEqual(secrets["steam_api_key"].clear_flag, "clear_steam_api_key")
        self.assertEqual(secrets["faceit_api_key"].clear_flag, "clear_faceit_api_key")
        self.assertEqual(secrets["anthropic_api_key"].clear_flag, "clear_anthropic_api_key")
        # Leetify has never had a clear flag: an empty value clears it, and the raw key stays in config.
        self.assertIsNone(secrets["leetify_api_key"].clear_flag)
        self.assertFalse(secrets["leetify_api_key"].masked)

    def test_env_fallbacks_are_declared(self):
        secrets = {f.name: f for f in config_schema.SECRETS}
        self.assertEqual(secrets["steam_api_key"].env, ("STEAM_API_KEY",))
        self.assertEqual(secrets["faceit_api_key"].env, ("FACEIT_API_KEY",))
        self.assertEqual(secrets["anthropic_api_key"].env, ("ANTHROPIC_API_KEY",))

    def test_duplicate_declaration_is_rejected(self):
        class Clash(DataSource):
            id = "clash"
            label = "Clash"
            settings = (Secret("steam_api_key", "text", "", env=()),)

        from sources import REGISTRY
        reg = SourceRegistry()
        for src in REGISTRY:
            reg.register(src)
        reg.register(Clash())
        with mock.patch.object(config_schema, "REGISTRY", reg):
            with self.assertRaises(ValueError) as ctx:
                config_schema._build_schema()
        self.assertIn("declared twice", str(ctx.exception))


class DescriptorTests(unittest.TestCase):
    def test_descriptors_carry_no_values(self):
        desc = config_schema.describe_schema()
        self.assertEqual([d["name"] for d in desc], list(config_store.DEFAULT_CONFIG))
        for d in desc:
            self.assertNotIn("value", d)
            if d["secret"]:
                self.assertEqual(d["default"], "")
        text = json.dumps(desc)
        self.assertNotIn("TESTKEY", text)

    def test_choices_and_limits_are_described(self):
        by = {d["name"]: d for d in config_schema.describe_schema()}
        self.assertEqual(by["screenshot_tool"]["choices"], list(config_schema.ALLOWED_SCREENSHOT_TOOLS))
        self.assertEqual(by["ai_provider"]["choices"], list(config_schema.ALLOWED_AI_PROVIDERS))
        self.assertEqual((by["tab_delay_sec"]["min"], by["tab_delay_sec"]["max"]), (0.0, 5.0))
        self.assertEqual(by["steam_api_key"]["env"], ["STEAM_API_KEY"])
        self.assertEqual(by["steam_api_key"]["clear_flag"], "clear_steam_api_key")


class GenericRuleTests(unittest.TestCase):
    def test_normalize_drops_unknown_keys_and_fills_defaults(self):
        out = config_schema.normalize({"unknown": 1})
        self.assertEqual(out, config_store.DEFAULT_CONFIG)

    def test_apply_updates_keeps_secret_on_empty_and_clears_on_flag(self):
        cur = {"steam_api_key": "old", "leetify_api_key": "lf"}
        kept = config_schema.apply_updates(cur, {"steam_api_key": "   "})
        self.assertEqual(kept["steam_api_key"], "old")
        cleared = config_schema.apply_updates(cur, {"clear_steam_api_key": True})
        self.assertEqual(cleared["steam_api_key"], "")
        lf_cleared = config_schema.apply_updates(cur, {"leetify_api_key": ""})
        self.assertEqual(lf_cleared["leetify_api_key"], "")

    def test_public_view_masks_and_never_leaks_raw_secret_except_leetify(self):
        cfg = config_store.DEFAULT_CONFIG | {"steam_api_key": FAKE_KEY, "leetify_api_key": "lf-TESTKEY-LEETIFY1"}
        view = config_schema.public_view(cfg, {}, resolved={}, platform="linux")
        self.assertNotIn("steam_api_key", view["config"])
        self.assertEqual(view["steam_api_key_masked"], "*" * (len(FAKE_KEY) - 4) + FAKE_KEY[-4:])
        self.assertEqual(view["config"]["leetify_api_key"], "lf-TESTKEY-LEETIFY1")  # frozen legacy behaviour
        self.assertNotIn(FAKE_KEY, json.dumps(view))


class SchemaRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.CsWatchHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)

    def test_schema_endpoint_returns_descriptors_without_stored_values(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg_path = Path(tmp.name) / "config.json"
        cfg_path.write_text(json.dumps({"steam_api_key": FAKE_KEY}), encoding="utf-8")
        with mock.patch.object(config_store, "CONFIG_FILE", cfg_path):
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            try:
                conn.request("GET", "/api/config/schema")
                resp = conn.getresponse()
                body = resp.read().decode("utf-8")
            finally:
                conn.close()
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.getheader("Content-Type"), "application/json; charset=utf-8")
        data = json.loads(body)
        self.assertIsInstance(data, list)
        self.assertEqual([d["name"] for d in data], list(config_store.DEFAULT_CONFIG))
        self.assertNotIn(FAKE_KEY, body)
        steam = next(d for d in data if d["name"] == "steam_api_key")
        self.assertTrue(steam["secret"])
        self.assertEqual(steam["env"], ["STEAM_API_KEY"])


class NonFiniteNumbersTests(unittest.TestCase):
    """A bad number falls back to the default of that field only; other keys keep their values."""

    def test_non_finite_float_falls_back_to_field_default(self):
        for bad in (float("inf"), float("-inf"), float("nan"), "inf", "nan"):
            with self.subTest(value=bad):
                out = config_schema.normalize({"tab_delay_sec": bad})
                self.assertEqual(out["tab_delay_sec"], 0.5)

    def test_non_finite_int_falls_back_to_field_default_and_keeps_other_keys(self):
        out = config_schema.normalize({"leetify_cache_ttl_sec": float("inf"), "tab_cooldown_sec": 7.0})
        self.assertEqual(out["leetify_cache_ttl_sec"], config_schema.FIELDS["leetify_cache_ttl_sec"].default)
        self.assertEqual(out["tab_cooldown_sec"], 7.0)
        out = config_schema.normalize({"leetify_cache_ttl_sec": float("-inf")})
        self.assertEqual(out["leetify_cache_ttl_sec"], config_schema.FIELDS["leetify_cache_ttl_sec"].default)

    def test_non_finite_weight_falls_back_to_weight_default(self):
        out = config_schema.normalize({"ai_weights": {"scoreboard": float("nan"), "leetify": 0.5}})
        self.assertEqual(out["ai_weights"]["scoreboard"], config_schema.DEFAULT_AI_WEIGHTS["scoreboard"])
        self.assertEqual(out["ai_weights"]["leetify"], 0.5)

    def test_save_config_with_infinity_does_not_raise(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg_path = Path(tmp.name) / "config.json"
        with mock.patch.object(config_store, "CONFIG_FILE", cfg_path):
            saved = config_store.save_config({"leetify_cache_ttl_sec": float("inf")})
        self.assertEqual(saved["leetify_cache_ttl_sec"], config_schema.FIELDS["leetify_cache_ttl_sec"].default)


if __name__ == "__main__":
    unittest.main()
