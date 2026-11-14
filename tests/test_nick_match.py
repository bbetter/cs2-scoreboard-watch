import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lookup.nick_match import best_match_index, normalize_nick, pick_best  # noqa: E402


class NormalizeTests(unittest.TestCase):
    def test_rank_number_prefix_removed(self):
        self.assertEqual(normalize_nick("33 cemolog"), "cemolog")

    def test_rank_glyph_prefix_removed(self):
        self.assertEqual(normalize_nick("③cemolog"), "cemolog")

    def test_digit_without_space_is_kept(self):
        # "1uc666" is a misread "luc666", not a rank prefix.
        self.assertEqual(normalize_nick("1uc666"), "1uc666")


class PickBestTests(unittest.TestCase):
    # --- positive examples from the OCR logs ---

    def test_l_vs_1_in_name(self):
        self.assertEqual(pick_best("900dfe1la", ["900dfe11a", "other"]), "900dfe11a")

    def test_digit_one_for_l_at_start(self):
        self.assertEqual(pick_best("1uc666", ["luc666", "zzz999"]), "luc666")

    def test_rank_number_prefix(self):
        self.assertEqual(pick_best("33 cemolog", ["cemolog", "qwerty"]), "cemolog")

    def test_rank_glyph_prefix(self):
        self.assertEqual(pick_best("③cemolog", ["cemolog", "qwerty"]), "cemolog")

    def test_exact_match_case_insensitive(self):
        self.assertEqual(pick_best("CeMoLoG", ["cemolog", "other"]), "cemolog")

    def test_exact_match_wins_even_if_similar_candidate_exists(self):
        self.assertEqual(pick_best("luc666", ["luc666", "luc6661"]), "luc666")

    def test_unreadable_chars_with_enough_literals(self):
        self.assertEqual(pick_best("cem?log", ["cemolog", "qwerty"]), "cemolog")

    def test_zero_for_o(self):
        self.assertEqual(pick_best("s0ldier", ["soldier", "zzzzzz"]), "soldier")

    # --- negative examples: must return None / not substitute ---

    def test_ambiguous_two_close_candidates(self):
        # Both candidates are one edit away from the query: no unique winner.
        self.assertIsNone(pick_best("abcdef", ["abcdeg", "abcdeh"]))

    def test_ambiguous_with_folded_duplicates(self):
        # "luc" and "1uc" fold to the same key: cannot pick one.
        self.assertIsNone(pick_best("1uc666", ["luc666", "1uc666x", "luc666y"]))

    def test_margin_blocks_near_tie(self):
        self.assertIsNone(pick_best("luc666", ["luc666x", "luc66"]))

    def test_all_wildcards_rejected(self):
        self.assertIsNone(pick_best("k?????", ["kxxxxx", "kzzzzz"]))

    def test_too_few_literals_rejected(self):
        self.assertIsNone(pick_best("ab???", ["abcdef"]))

    def test_below_similarity_threshold(self):
        self.assertIsNone(pick_best("cemolog", ["completely", "different"]))

    def test_empty_inputs(self):
        self.assertIsNone(pick_best("", ["a"]))
        self.assertIsNone(pick_best("cemolog", []))
        self.assertIsNone(pick_best("cemolog", [""]))

    def test_index_variant(self):
        self.assertEqual(best_match_index("900dfe1la", ["zzz", "900dfe11a"]), 1)
        self.assertIsNone(best_match_index("abcdef", ["abcdeg", "abcdeh"]))


if __name__ == "__main__":
    unittest.main()
