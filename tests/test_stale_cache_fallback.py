import sys, time, unittest
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import lookup.steam_lookup as sl

class StaleFallback(unittest.TestCase):
    def setUp(self):
        self._orig = (sl._search_steam_community, sl._leetify_cached_or_fetch, sl._save_profile_cache_locked)
        sl._leetify_cached_or_fetch = lambda sid, ttl: None
        sl._save_profile_cache_locked = lambda: None
        sl._PROFILE_CACHE.clear()
        base = {"query_nick": "Zed", "steamid64": "76561198000000009", "lookup_source": "steam_web_api",
                "cs2_stats": {}, "extra_stats": {"cstracker": {"status": "not_found"}}, "profile_name": "Zed"}
        sl._PROFILE_CACHE["zed"] = (time.time(), base)

    def tearDown(self):
        sl._search_steam_community, sl._leetify_cached_or_fetch, sl._save_profile_cache_locked = self._orig
        sl._PROFILE_CACHE.clear()

    def test_stale_entry_served_when_refresh_rate_limited(self):
        sl._get_steam_api_key = lambda: "key"
        sl._search_steam_community = lambda *a, **k: ({"_rate_limited": True}, ["429"], [])
        p = sl.lookup_steam_profile("Zed")
        self.assertEqual(p.lookup_source, "steam_web_api")
        self.assertEqual(p.steamid64, "76561198000000009")

    def test_no_cache_entry_still_reports_rate_limited(self):
        sl._PROFILE_CACHE.clear()
        sl._search_steam_community = lambda *a, **k: ({"_rate_limited": True}, ["429"], [])
        p = sl.lookup_steam_profile("Nobody")
        self.assertEqual(p.lookup_source, "rate_limited")

if __name__ == "__main__":
    unittest.main(verbosity=1)
