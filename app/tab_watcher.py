"""TAB watcher: a TAB press schedules one delayed screenshot scan, debounced by a cooldown.

The state (cooldown timestamp, delay, CS2-only flag, notification switch) lives on a `TabWatcher` instance.
Its dependencies are passed to the constructor, so tests run it with fakes and two servers can share a process.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

Task = Callable[[], None]


def _spawn_daemon(task: Task) -> None:
    threading.Thread(target=task, daemon=True).start()


class TabWatcher:
    def __init__(
        self,
        *,
        is_cs2_active: Callable[[], bool],
        run_scan: Callable[[], dict],
        notify: Callable[[str, str], None],
        spawn: Callable[[Task], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._is_cs2_active = is_cs2_active
        self._run_scan = run_scan
        self._notify_sink = notify
        self._spawn = spawn or _spawn_daemon
        self._sleep = sleep
        self._clock = clock
        self._lock = threading.Lock()
        self._last_ts = 0.0
        self.delay_sec = 0.5
        self.cooldown_sec = 4.0
        self.cs2_only = True
        self.notify_enabled = True

    def configure(self, delay_sec: float, cooldown_sec: float, cs2_only: bool) -> None:
        self.delay_sec = delay_sec
        self.cooldown_sec = cooldown_sec
        self.cs2_only = cs2_only

    def apply_config(self, cfg: dict) -> None:
        self.delay_sec = float(cfg.get("tab_delay_sec", 0.5))
        self.cooldown_sec = float(cfg.get("tab_cooldown_sec", 4.0))
        self.cs2_only = bool(cfg.get("cs2_only", True))
        self.notify_enabled = bool(cfg.get("notify_enabled", True))

    def notify(self, title: str, msg: str) -> None:
        if not self.notify_enabled:
            return
        self._notify_sink(title, msg)

    def schedule(self) -> dict:
        if self.cs2_only and not self._is_cs2_active():
            return {"status": "ignored", "reason": "cs2_not_focused"}
        now = self._clock()
        with self._lock:
            if now - self._last_ts < self.cooldown_sec:
                return {"status": "debounced"}
            self._last_ts = now
        self._spawn(self.run_delayed)
        return {"status": "scheduled", "delay_sec": self.delay_sec}

    def run_delayed(self) -> None:
        self._sleep(self.delay_sec)
        try:
            if self.cs2_only and not self._is_cs2_active():
                return
            result = self._run_scan()
            players = result.get("players", [])
            if players:
                top = players[0]
                msg = f"Знайдено {len(players)} гравців. Топ: {top['nick']} ({top['cheat_probability']}%)"
            else:
                msg = "Гравців не знайдено на скріншоті"
            self.notify("CS2 Scoreboard Watch", msg)
        except Exception as exc:
            print(f"[tab-scan error] {exc}")
