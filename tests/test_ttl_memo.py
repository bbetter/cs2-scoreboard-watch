"""Unit tests for lookup.ttl_memo.TtlMemo with a fake clock.

Run from the repo root:
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lookup.ttl_memo import TtlMemo  # noqa: E402


class FakeClock:
    def __init__(self) -> None:
        self.now = 500.0

    def __call__(self) -> float:
        return self.now

    def advance(self, sec: float) -> None:
        self.now += sec


class TtlMemoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.memo = TtlMemo(clock=self.clock)

    def test_hit_within_ttl(self) -> None:
        self.memo.put("76561198000000001", {"rating": 1}, 10)
        self.clock.advance(9.9)
        self.assertEqual(self.memo.get("76561198000000001", 10), {"rating": 1})

    def test_miss_after_ttl(self) -> None:
        self.memo.put("k", {"rating": 1}, 10)
        self.clock.advance(10.0)
        self.assertIsNone(self.memo.get("k", 10))

    def test_ttl_zero_never_hits(self) -> None:
        self.memo.put("k", {"rating": 1}, 10)
        self.assertIsNone(self.memo.get("k", 0))

    def test_put_with_ttl_zero_stores_nothing(self) -> None:
        self.memo.put("k", {"rating": 1}, 0)
        self.assertIsNone(self.memo.get("k", 10))

    def test_empty_key_or_value_is_ignored(self) -> None:
        self.memo.put("", {"rating": 1}, 10)
        self.memo.put("k", {}, 10)
        self.memo.put("k", None, 10)
        self.assertIsNone(self.memo.get("", 10))
        self.assertIsNone(self.memo.get("k", 10))

    def test_get_returns_copy(self) -> None:
        self.memo.put("k", {"rating": 1}, 10)
        got = self.memo.get("k", 10)
        got["rating"] = 99
        self.assertEqual(self.memo.get("k", 10), {"rating": 1})

    def test_put_stores_copy(self) -> None:
        value = {"rating": 1}
        self.memo.put("k", value, 10)
        value["rating"] = 99
        self.assertEqual(self.memo.get("k", 10), {"rating": 1})

    def test_clear_drops_everything(self) -> None:
        self.memo.put("k", {"rating": 1}, 10)
        self.memo.clear()
        self.assertIsNone(self.memo.get("k", 10))


if __name__ == "__main__":
    unittest.main()
