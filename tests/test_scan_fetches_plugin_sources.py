"""A registry source that is not a legacy slot source is fetched during a scan (not only on recheck).

Run: /home/andrii/fun/.venv/bin/python -m unittest tests.test_scan_fetches_plugin_sources -v
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import lookup.steam_lookup as steam_lookup  # noqa: E402
from sources import SourceRegistry  # noqa: E402
from sources.base import DataSource, LegacySlotSource, SourceContext  # noqa: E402

SID = "76561198000000001"


class PluginSource(DataSource):
    id = "acme"
    label = "Acme"

    def fetch_raw(self, steamid64, ctx: SourceContext):
        return {"rating": 7} if steamid64 == SID else None


class BrokenSource(DataSource):
    id = "broken"
    label = "Broken"

    def fetch_raw(self, steamid64, ctx: SourceContext):
        raise RuntimeError("boom")


class LegacyLike(LegacySlotSource):
    id = "legacy"
    label = "Legacy"
    slot = ("legacy",)

    def fetch_raw(self, steamid64, ctx: SourceContext):
        return {"x": 1}


class FetchRegistrySourcesTests(unittest.TestCase):
    def _registry(self, *sources):
        reg = SourceRegistry()
        for src in sources:
            reg.register(src, builtin=False)
        return reg

    def test_plugin_result_is_stored_in_the_sources_block(self):
        reg = self._registry(PluginSource(), LegacyLike())
        with mock.patch.object(steam_lookup, "REGISTRY", reg):
            stored = steam_lookup._fetch_registry_sources(SID)
        self.assertEqual(set(stored), {"acme"})  # the legacy-slot source is not fetched here
        self.assertEqual(stored["acme"]["status"], "ok")
        self.assertEqual(stored["acme"]["data"], {"rating": 7})

    def test_a_raising_source_is_recorded_as_error_and_others_still_run(self):
        reg = self._registry(BrokenSource(), PluginSource())
        with mock.patch.object(steam_lookup, "REGISTRY", reg):
            stored = steam_lookup._fetch_registry_sources(SID)
        self.assertEqual(stored["broken"]["status"], "error")
        self.assertEqual(stored["acme"]["status"], "ok")

    def test_no_steamid_means_no_fetch(self):
        reg = self._registry(PluginSource())
        with mock.patch.object(steam_lookup, "REGISTRY", reg), \
                mock.patch.object(PluginSource, "fetch_raw") as raw:
            self.assertEqual(steam_lookup._fetch_registry_sources(None), {})
        raw.assert_not_called()

    def test_profile_cache_entry_without_plugin_data_is_not_current(self):
        reg = self._registry(PluginSource())
        entry = {"steamid64": SID, "sources": {}, "extra_stats": {"cstracker": {"status": "ok"}}, "cs2_stats": {"kd": 1}}
        with mock.patch.object(steam_lookup, "REGISTRY", reg), \
                mock.patch.object(steam_lookup, "_get_steam_api_key", return_value=None):
            self.assertFalse(steam_lookup._cache_entry_current(entry))
            entry["sources"] = {"acme": {"status": "ok", "data": {}, "fetched_at": ""}}
            self.assertTrue(steam_lookup._cache_entry_current(entry))


if __name__ == "__main__":
    unittest.main()
