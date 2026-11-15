"""Tests for the Steam Community search queue, 429 cooldown and rate-limit retry.

Run from the repo root:
    python -m unittest discover -s tests -v

All HTTP is mocked (steam_search._http_get_status and lookup.http_client.http_get_status); no network access is needed.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lookup.http_client as http_client  # noqa: E402
import lookup.steam_lookup as sl  # noqa: E402
import lookup.steam_search as steam_search  # noqa: E402
from storage import config_store  # noqa: E402
from storage.profile_cache import ProfileCache  # noqa: E402
from vision.scoreboard_scan import Player  # noqa: E402

SEARCH_MARK = "SearchCommunityAjax"
SEARCH_OK = json.dumps(
    {
        "html": '<div class="search_row"><a class="searchPersonaName" '
        'href="https://steamcommunity.com/profiles/76561198000000001">Nick</a></div>'
    }
).encode("utf-8")


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, sec: float) -> None:
        self.now += sec


class FakeSteamHTTP:
    """Scripted responses for search requests; everything else returns (0, None)."""

    def __init__(self, search_statuses):
        self.search_statuses = list(search_statuses)
        self.search_calls: list[float] = []

    def __call__(self, url, timeout=4.0, cookies=None, headers=None):
        if SEARCH_MARK not in url:
            return 0, None
        self.search_calls.append(time.monotonic())
        status = self.search_statuses.pop(0) if len(self.search_statuses) > 1 else self.search_statuses[0]
        return status, (SEARCH_OK if status == 200 else None)


class SteamQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.patches = [
            # Isolated cache object: its saves go to the temp dir, never to data/profile_cache.json.
            mock.patch.object(
                sl, "PROFILE_CACHE", ProfileCache(Path(self._tmp.name) / "profile_cache.json", strip=sl._strip_leetify)
            ),
            # Keep reads of data/config.json out of the tests: fresh-install defaults instead.
            mock.patch.object(config_store, "load_config", return_value=dict(config_store.DEFAULT_CONFIG)),
            mock.patch.object(sl, "_get_steam_api_key", return_value=None),
            mock.patch.object(steam_search, "_get_steam_api_key", return_value=None),
            mock.patch.object(sl, "_fetch_external_stats", return_value=(None, None, None)),
            mock.patch.object(sl, "_cstracker_extra", return_value={}),
            # Lazy pton.logs handler would append to data/logs; these tests do not assert on logs.
            mock.patch.object(sl, "_setup_pton_log", lambda: None),
            # Fresh limiter per test with fast timings (no spacing, 0.2 s base cooldown, 1 s max).
            mock.patch.object(steam_search, "RATE_LIMITER", steam_search.SteamRateLimiter(0.0, 0.2, 1.0)),
            mock.patch.object(sl, "STEAM_RETRY_ROUNDS", 3),
            mock.patch.object(sl, "STEAM_RETRY_BUDGET_SEC", 10.0),
        ]
        for p in self.patches:
            p.start()
        self._reset_state()

    def tearDown(self) -> None:
        for p in self.patches:
            p.stop()
        self._reset_state()
        self._tmp.cleanup()

    def _reset_state(self) -> None:
        with sl.PROFILE_CACHE.lock:
            sl.PROFILE_CACHE.clear()

    def _http(self, statuses) -> FakeSteamHTTP:
        # _search_candidates calls steam_search._http_get_status directly; the other lookups go through
        # sl._http_get -> lookup.http_client.http_get -> http_client.http_get_status, so patch both.
        fake = FakeSteamHTTP(statuses)
        for patcher in (
            mock.patch.object(steam_search, "_http_get_status", side_effect=fake),
            mock.patch.object(http_client, "http_get_status", side_effect=fake),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        return fake

    def test_429_starts_cooldown_and_blocks_next_search_without_network(self) -> None:
        fake = self._http([429])
        status, found = steam_search._search_candidates("Nick")
        self.assertEqual(status, 429)
        self.assertEqual(found, [])
        self.assertEqual(len(fake.search_calls), 1)

        st = steam_search.get_steam_search_status()
        self.assertTrue(st["limited"])
        self.assertGreater(st["retry_in_sec"], 0)

        # During cooldown a new search must not reach Steam at all.
        status2, _ = steam_search._search_candidates("Other")
        self.assertEqual(status2, 429)
        self.assertEqual(len(fake.search_calls), 1)

    def test_cooldown_doubles_per_consecutive_429_and_resets_on_200(self) -> None:
        clock = FakeClock()
        limiter = steam_search.SteamRateLimiter(0.0, 0.2, 1.0, clock=clock)
        with mock.patch.object(steam_search, "RATE_LIMITER", limiter):
            self._http([429])
            steam_search._steam_record_search_status(429)
            first = limiter.cooldown_remaining()
            steam_search._steam_record_search_status(429)
            second = limiter.cooldown_remaining()
            self.assertGreater(second, first)
            self.assertLessEqual(second, limiter.cooldown_max_sec + 0.05)
            self.assertEqual(limiter.streak, 2)

            clock.advance(limiter.cooldown_max_sec)  # cooldown expired
            steam_search._steam_record_search_status(200)
            self.assertEqual(limiter.streak, 0)

    def test_status_reports_not_limited_without_429(self) -> None:
        self.assertEqual(steam_search.get_steam_search_status(), {"limited": False, "retry_in_sec": 0})

    def test_normal_search_without_429_unchanged(self) -> None:
        fake = self._http([200])
        result, log, cands = steam_search._search_steam_community("Nick")
        self.assertIsNotNone(result)
        self.assertEqual(result["lookup_source"], "search")
        self.assertEqual(len(fake.search_calls), 1)
        self.assertFalse(steam_search.get_steam_search_status()["limited"])
        self.assertEqual(len(cands), 1)

    def test_enrich_retries_rate_limited_lookup_after_cooldown(self) -> None:
        fake = self._http([429, 200])  # first search limited, retry succeeds
        reported = []
        players = [Player(nick="Nick", kills=1, deaths=1, kd=1.0, hs=0.0, level=1)]
        reports = sl.enrich_players(players, fetch_steam=True, on_player_ready=reported.append)

        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].steam["lookup_source"], "search")
        self.assertEqual(len(reported), 1, "on_player_ready must fire exactly once per player")
        self.assertEqual(len(fake.search_calls), 2)
        gap = fake.search_calls[1] - fake.search_calls[0]
        self.assertGreaterEqual(gap, 0.15, "retry must wait out the cooldown, not hammer Steam")

    def test_enrich_gives_up_after_bounded_rounds(self) -> None:
        fake = self._http([429])  # Steam keeps limiting
        reported = []
        players = [Player(nick="Nick", kills=1, deaths=1, kd=1.0, hs=0.0, level=1)]
        reports = sl.enrich_players(players, fetch_steam=True, on_player_ready=reported.append)

        self.assertEqual(reports[0].steam["lookup_source"], "rate_limited")
        self.assertEqual(len(reported), 1)
        self.assertLessEqual(len(fake.search_calls), 1 + sl.STEAM_RETRY_ROUNDS)
        self.assertNotIn("Nick".lower(), sl.PROFILE_CACHE.entries(), "rate_limited results must not be cached")

    def test_enrich_without_rate_limit_makes_single_request_per_player(self) -> None:
        fake = self._http([200])
        players = [Player(nick="Nick", kills=1, deaths=1, kd=1.0, hs=0.0, level=1)]
        reports = sl.enrich_players(players, fetch_steam=True)
        self.assertEqual(reports[0].steam["lookup_source"], "search")
        self.assertEqual(len(fake.search_calls), 1)


if __name__ == "__main__":
    unittest.main()
