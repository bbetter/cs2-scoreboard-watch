#!/home/andrii/fun/.venv/bin/python
"""Auto-watch CS2 TAB presses: wait 1s, screenshot, OCR, Steam lookup, and update Web UI."""

from __future__ import annotations

import argparse
import atexit
import os
import shutil
import signal
import subprocess
import sys
from http.server import ThreadingHTTPServer

from server import CsWatchHandler, _load_history, configure_tab_watcher, schedule_tab_scan


def is_hyprland() -> bool:
    return (
        sys.platform.startswith("linux")
        and bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"))
        and bool(shutil.which("hyprctl"))
    )


def register_tab_bind(url: str) -> None:
    cmd = f"curl -sf -X POST {url}/api/tab-pressed >/dev/null 2>&1 || true"
    lua = f'hl.bind("Tab", hl.dsp.exec_cmd("{cmd}"), {{ non_consuming = true }})'
    res = subprocess.run(["hyprctl", "eval", lua], capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Не вдалося зареєструвати Tab у Hyprland: {res.stderr.strip()}")


def unregister_tab_bind() -> None:
    subprocess.run(["hyprctl", "eval", 'if hl.unbind then hl.unbind("Tab") end'], capture_output=True)


def start_pynput_listener():
    try:
        from pynput import keyboard  # type: ignore[import-not-found]
    except ImportError:
        print(
            "Помилка: для відстеження натискання TAB на цій платформі потрібен пакет 'pynput'.\n"
            "Встановіть його командою: pip install pynput",
            file=sys.stderr,
        )
        return None

    def on_press(key) -> None:
        if key == keyboard.Key.tab:
            schedule_tab_scan()

    listener = keyboard.Listener(on_press=on_press)
    listener.start()
    return listener


def main() -> None:
    parser = argparse.ArgumentParser(description="CS2 TAB auto-scanner + Web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--delay", type=float, default=0.5, help="Затримка після TAB перед скріншотом (сек)")
    parser.add_argument("--cooldown", type=float, default=4.0, help="Мін. пауза між сканами (сек)")
    parser.add_argument("--all-windows", action="store_true", help="Реагувати на TAB у будь-якому вікні (для тесту)")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"
    configure_tab_watcher(delay_sec=args.delay, cooldown_sec=args.cooldown, cs2_only=not args.all_windows)
    _load_history()

    httpd = ThreadingHTTPServer((args.host, args.port), CsWatchHandler)
    use_hyprland = is_hyprland()
    listener = None

    def _cleanup() -> None:
        if use_hyprland:
            unregister_tab_bind()
        elif listener is not None:
            listener.stop()

    if use_hyprland:
        register_tab_bind(url)
    else:
        listener = start_pynput_listener()
        if listener is None:
            httpd.server_close()
            sys.exit(1)

    atexit.register(_cleanup)

    def _handle_sig(_signum, _frame):
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_sig)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, _handle_sig)

    print(f"Веб-інтерфейс: {url}")
    print(f"Слухаю натискання TAB (затримка {args.delay:.1f}с, тільки CS2: {not args.all_windows}). Ctrl+C для виходу.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _cleanup()
        httpd.server_close()
        print("\nЗупинено, Tab-бінд знято.")


if __name__ == "__main__":
    main()
