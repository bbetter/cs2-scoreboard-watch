"""Unit tests for lookup.steam_search.SteamRateLimiter with a fake clock; sleep advances the clock.

Run from the repo root:
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lookup.steam_search import SteamRateLimiter  # noqa: E402


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def advance(self, sec: float) -> None:
        self.now += sec

    def sleep(self, sec: float) -> None:
        self.sleeps.append(sec)
        self.now += sec


class RateLimiterTests(unittest.TestCase):
    def make(self, min_interval: float = 0.5, base: float = 5.0, max_sec: float = 20.0) -> tuple[SteamRateLimiter, FakeClock]:
        clock = FakeClock()
        return SteamRateLimiter(min_interval, base, max_sec, clock=clock, sleep=clock.sleep), clock

    def test_first_turn_does_not_wait(self) -> None:
        limiter, clock = self.make()
        self.assertTrue(limiter.wait_turn())
        self.assertEqual(clock.sleeps, [])

    def test_interval_between_request_starts(self) -> None:
        limiter, clock = self.make(min_interval=0.5)
        limiter.wait_turn()
        self.assertTrue(limiter.wait_turn())
        self.assertEqual(len(clock.sleeps), 1)
        self.assertAlmostEqual(clock.sleeps[0], 0.5)

        clock.advance(1.0)  # enough time has passed since the last start
        limiter.wait_turn()
        self.assertEqual(len(clock.sleeps), 1)

    def test_cooldown_doubles_and_caps_at_max(self) -> None:
        limiter, _ = self.make(base=5.0, max_sec=20.0)
        limiter.record(429)
        self.assertAlmostEqual(limiter.cooldown_remaining(), 5.0)
        limiter.record(429)
        self.assertAlmostEqual(limiter.cooldown_remaining(), 10.0)
        limiter.record(429)
        self.assertAlmostEqual(limiter.cooldown_remaining(), 20.0)
        limiter.record(429)
        self.assertAlmostEqual(limiter.cooldown_remaining(), 20.0)
        self.assertEqual(limiter.streak, 4)

    def test_wait_turn_refuses_during_cooldown_without_sleeping(self) -> None:
        limiter, clock = self.make(base=5.0)
        limiter.record(429)
        self.assertFalse(limiter.wait_turn())
        self.assertEqual(clock.sleeps, [])

        clock.advance(5.0)
        self.assertEqual(limiter.cooldown_remaining(), 0.0)
        self.assertTrue(limiter.wait_turn())

    def test_200_resets_streak_and_next_cooldown_starts_from_base(self) -> None:
        limiter, clock = self.make(base=5.0)
        limiter.record(429)
        limiter.record(429)
        clock.advance(100.0)
        limiter.record(200)
        self.assertEqual(limiter.streak, 0)
        limiter.record(429)
        self.assertAlmostEqual(limiter.cooldown_remaining(), 5.0)

    def test_status_rounds_retry_up(self) -> None:
        limiter, clock = self.make(base=5.0)
        limiter.record(429)
        clock.advance(0.8)  # 4.2 s left
        self.assertEqual(limiter.status(), {"limited": True, "retry_in_sec": 5})

    def test_status_not_limited_by_default(self) -> None:
        limiter, _ = self.make()
        self.assertEqual(limiter.status(), {"limited": False, "retry_in_sec": 0})

    def test_reset_clears_cooldown_and_streak(self) -> None:
        limiter, _ = self.make()
        limiter.record(429)
        limiter.reset()
        self.assertEqual(limiter.streak, 0)
        self.assertEqual(limiter.status(), {"limited": False, "retry_in_sec": 0})


if __name__ == "__main__":
    unittest.main()
