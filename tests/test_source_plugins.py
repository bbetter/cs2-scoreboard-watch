"""Third-party sources from the `cs2watch.sources` entry point group: loading, isolation, config keys.

Each test builds a fresh SourceRegistry and fake entry points (objects with `name` and `load()`).
Run: /home/andrii/fun/.venv/bin/python -m unittest tests.test_source_plugins -v
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import storage.config_schema as config_schema  # noqa: E402
import sources.registry as registry_mod  # noqa: E402
from sources import DataSource, SourceRegistry  # noqa: E402
from sources.registry import DISABLE_ENV, PLUGIN_GROUP, REGISTRY, load_plugins  # noqa: E402
from storage.config_fields import Field, Secret  # noqa: E402


class AcmeSource(DataSource):
    id = "acme"
    label = "Acme"
    settings = (Field("acme_api_key", "text", "", label="Acme key", group="api_keys"),)


class OtherSource(DataSource):
    id = "other"
    label = "Other"


class FakeEP:
    def __init__(self, name, target):
        self.name = name
        self._target = target

    def load(self):
        if isinstance(self._target, Exception):
            raise self._target
        return self._target


def _fresh_registry() -> SourceRegistry:
    reg = SourceRegistry()
    for src in REGISTRY:
        reg.register(src)
    return reg


class LoadPluginsTests(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        os.environ.pop(DISABLE_ENV, None)
        self.addCleanup(self._env.stop)

    def test_class_and_instance_are_registered_as_not_builtin(self):
        reg = _fresh_registry()
        loaded = load_plugins(reg, [FakeEP("acme", AcmeSource), FakeEP("other", OtherSource())])
        self.assertEqual(loaded, ["acme", "other"])
        self.assertIsInstance(reg.get("acme"), AcmeSource)
        self.assertIsInstance(reg.get("other"), OtherSource)
        self.assertFalse(reg.is_builtin("acme"))
        self.assertFalse(reg.is_builtin("other"))
        self.assertTrue(reg.is_builtin("leetify"))

    def test_failing_entry_point_warns_and_other_plugins_still_load(self):
        reg = _fresh_registry()
        eps = [FakeEP("broken", ImportError("no module named acme_missing")), FakeEP("acme", AcmeSource)]
        with self.assertLogs("cs2watch.sources", level="WARNING") as logs:
            loaded = load_plugins(reg, eps)
        self.assertEqual(loaded, ["acme"])
        self.assertIsNotNone(reg.get("acme"))
        self.assertTrue(any("broken" in line for line in logs.output))

    def test_non_datasource_is_skipped(self):
        class NotASource:
            id = "fake"

        reg = _fresh_registry()
        eps = [FakeEP("class_bad", NotASource), FakeEP("inst_bad", "just a string"), FakeEP("acme", AcmeSource)]
        with self.assertLogs("cs2watch.sources", level="WARNING") as logs:
            loaded = load_plugins(reg, eps)
        self.assertEqual(loaded, ["acme"])
        self.assertIsNone(reg.get("fake"))
        self.assertTrue(any("class_bad" in line for line in logs.output))
        self.assertTrue(any("inst_bad" in line for line in logs.output))

    def test_duplicate_id_is_skipped_and_builtin_is_kept(self):
        class Impostor(DataSource):
            id = "leetify"

        class SecondAcme(DataSource):
            id = "acme"

        reg = _fresh_registry()
        original = reg.get("leetify")
        eps = [FakeEP("impostor", Impostor), FakeEP("acme1", AcmeSource), FakeEP("acme2", SecondAcme)]
        with self.assertLogs("cs2watch.sources", level="WARNING") as logs:
            loaded = load_plugins(reg, eps)
        self.assertEqual(loaded, ["acme"])
        self.assertIs(reg.get("leetify"), original)
        self.assertTrue(reg.is_builtin("leetify"))
        self.assertIsInstance(reg.get("acme"), AcmeSource)
        self.assertTrue(any("impostor" in line for line in logs.output))
        self.assertTrue(any("acme2" in line for line in logs.output))

    def test_empty_id_is_skipped(self):
        class NoId(DataSource):
            id = ""

        reg = _fresh_registry()
        with self.assertLogs("cs2watch.sources", level="WARNING"):
            self.assertEqual(load_plugins(reg, [FakeEP("noid", NoId)]), [])

    def test_disable_env_loads_nothing(self):
        reg = _fresh_registry()
        before = reg.ids()
        os.environ[DISABLE_ENV] = "1"
        self.assertEqual(load_plugins(reg, [FakeEP("acme", AcmeSource)]), [])
        self.assertEqual(reg.ids(), before)

    def test_default_reads_the_cs2watch_sources_group(self):
        with mock.patch.object(registry_mod, "entry_points", return_value=[]) as eps_mock:
            self.assertEqual(load_plugins(_fresh_registry()), [])
        eps_mock.assert_called_once_with(group=PLUGIN_GROUP)


class PluginSchemaTests(unittest.TestCase):
    def test_plugin_field_is_appended_after_order_in_registration_order(self):
        reg = _fresh_registry()
        load_plugins(reg, [FakeEP("acme", AcmeSource)])
        schema = config_schema._build_schema(reg)
        names = [f.name for f in schema]
        self.assertEqual(names[:len(config_schema.ORDER)], list(config_schema.ORDER))
        self.assertEqual(names[len(config_schema.ORDER):], ["acme_api_key"])

    def test_plugin_field_clashing_with_existing_key_is_skipped(self):
        class Clashing(DataSource):
            id = "clashing"
            settings = (
                Secret("steam_api_key", "text", "", env=()),
                Field("clash_extra", "text", "", label="Extra", group="api_keys"),
            )

        reg = _fresh_registry()
        load_plugins(reg, [FakeEP("clashing", Clashing)])
        with self.assertLogs("cs2watch.sources", level="WARNING") as logs:
            schema = config_schema._build_schema(reg)
        names = [f.name for f in schema]
        self.assertEqual(names.count("steam_api_key"), 1)
        self.assertIs(schema[names.index("steam_api_key")], config_schema.FIELDS["steam_api_key"])
        self.assertEqual(names[len(config_schema.ORDER):], ["clash_extra"])
        self.assertTrue(any("steam_api_key" in line for line in logs.output))
        self.assertIsNotNone(reg.get("clashing"))

    def test_module_schema_is_unchanged_without_plugins(self):
        self.assertEqual([f.name for f in config_schema.SCHEMA], list(config_schema.ORDER))


if __name__ == "__main__":
    unittest.main()
