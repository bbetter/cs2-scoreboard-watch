"""Active-window detection: is the CS2 client the focused window? (Windows, macOS, Hyprland)."""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys


def is_cs2_client(c: dict) -> bool:
    cls = (c.get("class") or "").strip().lower()
    init_cls = (c.get("initialClass") or "").strip().lower()
    return cls in {"cs2", "steam_app_730"} or init_cls in {"cs2", "steam_app_730"}


def win32_enable_dpi_awareness() -> None:
    try:
        ctypes.windll.user32.SetProcessDPIAware()  # type: ignore[attr-defined]
    except Exception:
        pass


def win32_get_window_title(hwnd: int) -> str:
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def win32_get_process_name(hwnd: int) -> str:
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    pid = ctypes.c_ulong(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        size = ctypes.c_ulong(512)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        kernel32.CloseHandle(handle)


def win32_is_cs2_hwnd(hwnd: int) -> bool:
    if not hwnd:
        return False
    title = win32_get_window_title(hwnd).strip().lower()
    if "counter-strike 2" in title or title == "cs2":
        return True
    return win32_get_process_name(hwnd) == "cs2.exe"


def win32_find_cs2_hwnd() -> int:
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    fg = user32.GetForegroundWindow()
    if win32_is_cs2_hwnd(fg):
        return fg
    return int(user32.FindWindowW(None, "Counter-Strike 2") or 0)


def win32_get_window_center(hwnd: int) -> tuple[int, int] | None:
    if not hwnd:
        return None
    from ctypes import wintypes

    rect = wintypes.RECT()
    if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):  # type: ignore[attr-defined]
        return None
    return (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2


def is_cs2_active() -> bool:
    if sys.platform == "win32":
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()  # type: ignore[attr-defined]
            return win32_is_cs2_hwnd(hwnd)
        except Exception:
            return True

    if sys.platform == "darwin":
        if not shutil.which("osascript"):
            return True
        try:
            res = subprocess.run(
                [
                    "osascript",
                    "-e",
                    'tell application "System Events" to get name of first application process whose frontmost is true',
                ],
                capture_output=True,
                text=True,
                timeout=1.5,
            )
            if res.returncode != 0 or not res.stdout.strip():
                return True
            app_name = res.stdout.strip().lower()
            return "counter-strike" in app_name or "cs2" in app_name
        except Exception:
            return True

    if not shutil.which("hyprctl"):
        return True
    try:
        res = subprocess.run(["hyprctl", "activewindow", "-j"], capture_output=True, text=True, timeout=1.5)
        if res.returncode != 0 or not res.stdout.strip():
            return True
        info = json.loads(res.stdout)
        return is_cs2_client(info)
    except Exception:
        return True
