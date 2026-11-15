"""FACEIT active bans: parsing of the bans endpoint, the unknown-vs-clean split, and the profile cache refresh.

Run: /home/andrii/fun/.venv/bin/python -m unittest tests.test_faceit_bans -v
"""

import json
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import lookup.steam_lookup as steam_lookup  # noqa: E402
import sources.faceit as faceit  # noqa: E402

HEADERS = {"Authorization": "Bearer k"}
FUTURE = (datetime.now(UTC) + timedelta(days=30)).isoformat()
PAST = (datetime.now(UTC) - timedelta(days=30)).isoformat()


def _body(obj) -> bytes:
    return json.dumps(obj).encode("utf-8")


class ActiveBansParsingTests(unittest.TestCase):
    def _call(self, body):
        with mock.patch.object(faceit, "_http_get", return_value=body) as get:
            out = faceit.fetch_faceit_active_bans("pid-1", HEADERS)
        return out, get

    def test_no_items_means_checked_and_clean(self):
        out, _ = self._call(_body({"items": []}))
        self.assertEqual(out, [])

    def test_only_expired_bans_is_clean(self):
        out, _ = self._call(_body({"items": [{"reason": "old", "starts_at": PAST, "ends_at": PAST}]}))
        self.assertEqual(out, [])

    def test_active_ban_is_kept_with_reason_and_end(self):
        out, _ = self._call(_body({"items": [
            {"reason": "Cheating", "starts_at": PAST, "ends_at": FUTURE},
            {"reason": "old", "starts_at": PAST, "ends_at": PAST},
        ]}))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["reason"], "Cheating")
        self.assertTrue(out[0]["ends_at"].startswith(FUTURE[:10]))

    def test_unreadable_response_is_unknown_not_clean(self):
        self.assertIsNone(self._call(None)[0])
        self.assertIsNone(self._call(b"not json")[0])
        self.assertIsNone(self._call(_body({"error": "nope"}))[0])
        self.assertIsNone(self._call(_body(["list"]))[0])

    def test_bad_timestamps_and_items_are_skipped(self):
        out, _ = self._call(_body({"items": ["x", {"reason": "a", "ends_at": "garbage"}, {"reason": "b"}]}))
        self.assertEqual(out, [])

    def test_naive_timestamp_is_read_as_utc(self):
        naive = (datetime.now(UTC) + timedelta(days=1)).replace(tzinfo=None).isoformat()
        out, _ = self._call(_body({"items": [{"reason": "r", "ends_at": naive}]}))
        self.assertEqual(len(out), 1)

    def test_reason_is_capped(self):
        out, _ = self._call(_body({"items": [{"reason": "x" * 500, "ends_at": FUTURE}]}))
        self.assertEqual(len(out[0]["reason"]), 200)

    def test_requests_the_bans_endpoint_for_the_player(self):
        _, get = self._call(_body({"items": []}))
        self.assertEqual(get.call_args.args[0], "https://open.faceit.com/data/v4/players/pid-1/bans")


class FetchStatsIncludesBansTests(unittest.TestCase):
    def test_found_player_result_carries_active_bans(self):
        player = _body({"player_id": "pid-1", "nickname": "n", "games": {"cs2": {"skill_level": 9, "faceit_elo": 2000}}})
        bans = _body({"items": [{"reason": "Abandon", "ends_at": FUTURE}]})
        responses = [player, bans, None]  # player, bans, stats (unavailable)
        with mock.patch.object(faceit.api_keys, "_get_faceit_api_key", return_value="k"), \
                mock.patch.object(faceit, "_http_get", side_effect=responses):
            res = faceit.fetch_faceit_stats("76561198000000001")
        self.assertTrue(res["found"])
        self.assertEqual(res["active_bans"][0]["reason"], "Abandon")

    def test_unreadable_bans_leave_active_bans_none(self):
        player = _body({"player_id": "pid-1", "games": {"cs2": {}}})
        with mock.patch.object(faceit.api_keys, "_get_faceit_api_key", return_value="k"), \
                mock.patch.object(faceit, "_http_get", side_effect=[player, None, None]):
            res = faceit.fetch_faceit_stats("76561198000000001")
        self.assertTrue(res["found"])
        self.assertIsNone(res["active_bans"])


class CacheRefreshTests(unittest.TestCase):
    def test_found_faceit_entry_without_ban_field_is_stale_once(self):
        entry = {"steamid64": "7656", "faceit": {"found": True, "skill_level": 9}}
        with mock.patch.object(steam_lookup, "_get_steam_api_key", return_value=None), \
                mock.patch.object(steam_lookup, "_extra_sources", return_value=[]):
            self.assertFalse(steam_lookup._cache_entry_current(
                {**entry, "extra_stats": {"cstracker": {"status": "ok"}}, "cs2_stats": {"kd": 1}}))
            entry["faceit"]["active_bans"] = []
            self.assertTrue(steam_lookup._cache_entry_current(
                {**entry, "extra_stats": {"cstracker": {"status": "ok"}}, "cs2_stats": {"kd": 1}}))


if __name__ == "__main__":
    unittest.main()
