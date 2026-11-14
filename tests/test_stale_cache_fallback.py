import sys, tempfile, time, unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import lookup.steam_lookup as sl
import lookup.steam_search as steam_search
from storage import config_store
from storage.profile_cache import ProfileCache

class StaleFallback(unittest.TestCase):
    def setUp(self):
        self._orig = (steam_search._search_steam_community, sl._leetify_cached_or_fetch)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # Isolated cache object: nothing is written to data/profile_cache.json.
        cache = ProfileCache(Path(self._tmp.name) / "profile_cache.json", strip=sl._strip_leetify)
        # Patches are stopped via addCleanup so they cannot leak into later tests.
        for p in (
            mock.patch.object(sl, "PROFILE_CACHE", cache),
            mock.patch.object(cache, "save_locked", lambda: None),
            mock.patch.object(config_store, "load_config", return_value=dict(config_store.DEFAULT_CONFIG)),
            mock.patch.object(sl, "_get_steam_api_key", return_value="key"),
        ):
            p.start()
            self.addCleanup(p.stop)
        sl._leetify_cached_or_fetch = lambda sid, ttl: None
        sl.PROFILE_CACHE.clear()
        base = {"query_nick": "Zed", "steamid64": "76561198000000009", "lookup_source": "steam_web_api",
                "cs2_stats": {}, "extra_stats": {"cstracker": {"status": "not_found"}}, "profile_name": "Zed"}
        sl.PROFILE_CACHE.entries()["zed"] = (time.time(), base)

    def tearDown(self):
        steam_search._search_steam_community, sl._leetify_cached_or_fetch = self._orig
        sl.PROFILE_CACHE.clear()

    def test_stale_entry_served_when_refresh_rate_limited(self):
        sl._get_steam_api_key = lambda: "key"
        steam_search._search_steam_community = lambda *a, **k: ({"_rate_limited": True}, ["429"], [])
        p = sl.lookup_steam_profile("Zed")
        self.assertEqual(p.lookup_source, "steam_web_api")
        self.assertEqual(p.steamid64, "76561198000000009")

    def test_no_cache_entry_still_reports_rate_limited(self):
        sl.PROFILE_CACHE.clear()
        steam_search._search_steam_community = lambda *a, **k: ({"_rate_limited": True}, ["429"], [])
        p = sl.lookup_steam_profile("Nobody")
        self.assertEqual(p.lookup_source, "rate_limited")

if __name__ == "__main__":
    unittest.main(verbosity=1)
