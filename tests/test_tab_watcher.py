"""Tests for app.tab_watcher.TabWatcher with fake dependencies: no threads, no sleeps, no OS calls.

Run: /home/andrii/fun/.venv/bin/python -m unittest tests.test_tab_watcher -v
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.tab_watcher import TabWatcher  # noqa: E402


class FakeClock:
    def __init__(self, now: float = 100.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


class TabWatcherTests(unittest.TestCase):
    def make(self, *, cs2_active=True, scan_result=None, scan_error=None):
        self.clock = FakeClock()
        self.sleeps: list[float] = []
        self.notified: list[tuple[str, str]] = []
        self.scans = 0
        self.cs2 = cs2_active

        def run_scan() -> dict:
            self.scans += 1
            if scan_error is not None:
                raise scan_error
            return scan_result if scan_result is not None else {"players": []}

        watcher = TabWatcher(
            is_cs2_active=lambda: self.cs2,
            run_scan=run_scan,
            notify=lambda t, m: self.notified.append((t, m)),
            spawn=lambda task: task(),  # run synchronously
            sleep=self.sleeps.append,
            clock=self.clock,
        )
        return watcher

    def test_defaults(self):
        w = self.make()
        self.assertEqual((w.delay_sec, w.cooldown_sec, w.cs2_only, w.notify_enabled), (0.5, 4.0, True, True))

    def test_ignored_when_cs2_not_focused(self):
        w = self.make(cs2_active=False)
        self.assertEqual(w.schedule(), {"status": "ignored", "reason": "cs2_not_focused"})
        self.assertEqual(self.scans, 0)

    def test_cs2_only_false_ignores_focus(self):
        w = self.make(cs2_active=False)
        w.configure(delay_sec=0.5, cooldown_sec=4.0, cs2_only=False)
        self.assertEqual(w.schedule(), {"status": "scheduled", "delay_sec": 0.5})
        self.assertEqual(self.scans, 1)

    def test_debounce_within_cooldown(self):
        w = self.make()
        self.assertEqual(w.schedule(), {"status": "scheduled", "delay_sec": 0.5})
        self.clock.now += 3.9
        self.assertEqual(w.schedule(), {"status": "debounced"})
        self.clock.now += 0.1  # exactly cooldown_sec after the last accepted press
        self.assertEqual(w.schedule(), {"status": "scheduled", "delay_sec": 0.5})
        self.assertEqual(self.scans, 2)

    def test_schedule_reports_configured_delay(self):
        w = self.make()
        w.configure(delay_sec=1.25, cooldown_sec=4.0, cs2_only=True)
        self.assertEqual(w.schedule(), {"status": "scheduled", "delay_sec": 1.25})
        self.assertEqual(self.sleeps, [1.25])

    def test_spawn_receives_run_delayed(self):
        tasks = []
        w = self.make()
        w._spawn = tasks.append
        w.schedule()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].__name__, "run_delayed")
        self.assertEqual(self.scans, 0)  # nothing ran until the task is called

    def test_run_delayed_notifies_with_top_player(self):
        result = {"players": [{"nick": "alpha", "cheat_probability": 12.5}, {"nick": "beta", "cheat_probability": 1}]}
        w = self.make(scan_result=result)
        w.run_delayed()
        self.assertEqual(self.sleeps, [0.5])
        self.assertEqual(
            self.notified,
            [("CS2 Scoreboard Watch", "Знайдено 2 гравців. Топ: alpha (12.5%)")],
        )

    def test_run_delayed_without_players(self):
        w = self.make(scan_result={"players": []})
        w.run_delayed()
        self.assertEqual(self.notified, [("CS2 Scoreboard Watch", "Гравців не знайдено на скріншоті")])

    def test_run_delayed_skips_scan_when_focus_lost(self):
        w = self.make()
        self.cs2 = False
        w.run_delayed()
        self.assertEqual(self.scans, 0)
        self.assertEqual(self.notified, [])

    def test_exception_in_scan_does_not_escape(self):
        w = self.make(scan_error=RuntimeError("boom"))
        w.run_delayed()  # must not raise
        self.assertEqual(self.scans, 1)
        self.assertEqual(self.notified, [])

    def test_notify_disabled_is_silent(self):
        w = self.make(scan_result={"players": []})
        w.notify_enabled = False
        w.notify("T", "M")
        w.run_delayed()
        self.assertEqual(self.notified, [])

    def test_apply_config_empty_sets_defaults(self):
        w = self.make()
        w.configure(delay_sec=9.0, cooldown_sec=9.0, cs2_only=False)
        w.notify_enabled = False
        w.apply_config({})
        self.assertEqual((w.delay_sec, w.cooldown_sec, w.cs2_only, w.notify_enabled), (0.5, 4.0, True, True))

    def test_apply_config_reads_keys_and_casts(self):
        w = self.make()
        w.apply_config({"tab_delay_sec": "1.5", "tab_cooldown_sec": 2, "cs2_only": 0, "notify_enabled": False})
        self.assertEqual((w.delay_sec, w.cooldown_sec, w.cs2_only, w.notify_enabled), (1.5, 2.0, False, False))

    def test_instances_do_not_share_state(self):
        a = self.make()
        b = self.make()
        a.schedule()
        self.assertEqual(b.schedule(), {"status": "scheduled", "delay_sec": 0.5})


if __name__ == "__main__":
    unittest.main()
