"""Nickname search: live-only in-game signals, default avatars, nick variants, merged candidates and confidence.

Steam is never called: _search_candidates, _http_get and the Web API key are mocked.
Run: /home/andrii/fun/.venv/bin/python -m unittest tests.test_nick_search -v
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import lookup.steam_search as steam_search  # noqa: E402
from lookup.steam_search import (  # noqa: E402
    DEFAULT_AVATAR_HASH,
    MAX_CANDIDATES,
    _avatar_similarity,
    _nick_variants,
    _score_candidate,
    _search_steam_community,
)

DEFAULT_URL = f"https://avatars.akamai.steamstatic.com/{DEFAULT_AVATAR_HASH}_full.jpg"


def _cand(n: int, name: str) -> tuple:
    return (f"https://steamcommunity.com/id/u{n}", f"u{n}", f"7656119800000{n:04d}", name)


class ScoreCandidateTests(unittest.TestCase):
    def test_in_game_signals_count_only_for_a_live_capture(self):
        playing = {"profile_name": "x", "in_cs2": True}
        self.assertEqual(_score_candidate(playing, "other", live=True), steam_search.SCORE_IN_CS2_LIVE)
        self.assertEqual(_score_candidate(playing, "other", live=False), 0.0)

    def test_exact_name_beats_a_same_nick_stranger_who_is_in_game_now(self):
        exact = {"profile_name": "ivan"}
        stranger_in_game = {"profile_name": "ivan_pro", "in_cs2": True}
        for live in (True, False):
            with self.subTest(live=live):
                self.assertGreater(
                    _score_candidate(exact, "ivan", live=live), _score_candidate(stranger_in_game, "ivan", live=live)
                )

    def test_avatar_below_threshold_adds_nothing(self):
        self.assertEqual(_score_candidate({"profile_name": "z", "avatar_match": 0.5}, "q"), 0.0)


class DefaultAvatarTests(unittest.TestCase):
    def test_default_avatar_is_never_downloaded_or_matched(self):
        with mock.patch.object(steam_search, "_http_get") as get:
            self.assertEqual(_avatar_similarity(object(), DEFAULT_URL), 0.0)
        get.assert_not_called()

    def test_other_avatars_are_not_treated_as_default(self):
        self.assertFalse(steam_search._is_default_avatar("https://avatars.example/abc_full.jpg"))


class NickVariantTests(unittest.TestCase):
    def test_variants_are_unique_and_keep_the_original_first(self):
        self.assertEqual(_nick_variants("L0ng"), ["L0ng", "Long"])  # the l-to-1 reading equals the original here, so it is dropped
        self.assertEqual(_nick_variants("abc"), ["abc"])

    def test_one_and_l_readings_are_both_tried(self):
        self.assertEqual(_nick_variants("kl1ng"), ["kl1ng", "kllng", "k11ng"])


class SearchFlowTests(unittest.TestCase):
    def _run(self, nick, results_by_query, *, live=True):
        def fake_search(query):
            return results_by_query.get(query, (200, []))

        with mock.patch.object(steam_search, "_search_candidates", side_effect=fake_search) as search, \
                mock.patch.object(steam_search, "_get_steam_api_key", return_value=None), \
                mock.patch.object(steam_search, "_http_get", return_value=None):
            data, _log, cands = _search_steam_community(nick, live=live)
        return data, cands, search

    def test_alternative_spelling_is_queried_when_the_first_has_no_exact_name(self):
        first = (200, [_cand(1, "Lonely Wolf")])
        second = (200, [_cand(2, "Long")])
        data, _cands, search = self._run("L0ng", {"L0ng": first, "Long": second})
        self.assertEqual(search.call_count, 2)
        self.assertEqual(data["profile_name"], "Long")

    def test_alternative_spelling_is_skipped_once_an_exact_name_is_found(self):
        data, _cands, search = self._run("Long", {"Long": (200, [_cand(3, "Long")])})
        self.assertEqual(search.call_count, 1)
        self.assertEqual(data["search_confidence"]["exact_name"], True)
        self.assertEqual(data["search_confidence"]["level"], "high")

    def test_candidates_are_merged_by_profile_url_and_capped(self):
        many = [_cand(n, f"other{n}") for n in range(6)]
        _data, cands, _search = self._run("nick", {"nick": (200, many), "n1ck": (200, many)})
        self.assertLessEqual(len(cands), MAX_CANDIDATES)
        urls = [c["profile_url"] for c in cands]
        self.assertEqual(len(urls), len(set(urls)))

    def test_unconfident_pick_without_exact_name_or_avatar_is_low(self):
        data, _cands, _search = self._run("zzz", {"zzz": (200, [_cand(4, "zzzz1"), _cand(5, "zzzz2")])})
        self.assertEqual(data["search_confidence"]["level"], "low")
        self.assertFalse(data["search_confidence"]["exact_name"])

    def test_single_candidate_without_exact_name_is_low_with_no_margin(self):
        data, _cands, _search = self._run("zzz", {"zzz": (200, [_cand(6, "other")])})
        self.assertEqual(data["search_confidence"]["level"], "low")
        self.assertIsNone(data["search_confidence"]["margin"])

    def test_live_flag_reaches_the_scoring(self):
        results = {"nick": (200, [_cand(7, "nick"), _cand(8, "nick_x")])}
        with mock.patch.object(steam_search, "_score_candidate", wraps=steam_search._score_candidate) as score:
            self._run("nick", results, live=False)
        self.assertTrue(all(call.kwargs.get("live") is False for call in score.call_args_list))


if __name__ == "__main__":
    unittest.main()
