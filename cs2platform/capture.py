"""Screenshot capture of the CS2 scoreboard.

Selection: config `screenshot_tool` names a registry entry in CAPTURERS. Anything else (including
"auto" or an unknown value) goes through select_auto_tool(), which picks by OS and tools present.
Nothing is created at import time; the screenshot directory is made lazily by get_screenshot_dir().
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path

from cs2platform.focus import (
    is_cs2_client,
    win32_enable_dpi_awareness,
    win32_find_cs2_hwnd,
    win32_get_window_center,
)
from storage.config_store import load_config
from storage.paths import SCREENSHOT_DIR

Capturer = Callable[[Path], Path]


def get_screenshot_dir() -> Path:
    custom = str(load_config().get("screenshots_dir") or "").strip()
    if custom:
        p = Path(custom).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        return p
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    return SCREENSHOT_DIR


def _get_cs2_monitor_name() -> str | None:
    if not shutil.which("hyprctl"):
        return None
    try:
        mon_res = subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True, text=True, timeout=1.5)
        if mon_res.returncode != 0 or not mon_res.stdout.strip():
            return None
        monitors = json.loads(mon_res.stdout)
        id_to_name = {m["id"]: m["name"] for m in monitors if "id" in m and "name" in m}

        cli_res = subprocess.run(["hyprctl", "clients", "-j"], capture_output=True, text=True, timeout=1.5)
        if cli_res.returncode == 0 and cli_res.stdout.strip():
            clients = json.loads(cli_res.stdout)
            for c in clients:
                if is_cs2_client(c):
                    mon_id = c.get("monitor")
                    if mon_id in id_to_name:
                        return id_to_name[mon_id]

        # Fallback: currently focused monitor
        for m in monitors:
            if m.get("focused") and "name" in m:
                return m["name"]
    except Exception:
        return None
    return None


def _capture_with_mss_or_pil(out_path: Path, center: tuple[int, int] | None = None) -> None:
    try:
        import mss
        import mss.tools

        with mss.mss() as sct:
            monitors = sct.monitors
            target = monitors[1] if len(monitors) > 1 else monitors[0]
            if center is not None:
                cx, cy = center
                for mon in monitors[1:]:
                    if mon["left"] <= cx < mon["left"] + mon["width"] and mon["top"] <= cy < mon["top"] + mon["height"]:
                        target = mon
                        break
            img = sct.grab(target)
            mss.tools.to_png(img.rgb, img.size, output=str(out_path))
            return
    except ImportError:
        pass

    from PIL import ImageGrab

    ImageGrab.grab().save(out_path)


def _run_checked(cmd: list[str], out_path: Path) -> None:
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if res.returncode != 0 or not out_path.exists():
        raise RuntimeError(f"Screenshot failed ({' '.join(cmd)}): {res.stderr.strip()}")


# ---- explicit tools (config screenshot_tool) -------------------------------------------

def _capture_mss(out_path: Path) -> Path:
    _capture_with_mss_or_pil(out_path)
    if not out_path.exists():
        raise RuntimeError("Screenshot failed using mss.")
    return out_path


def _capture_pil(out_path: Path) -> Path:
    from PIL import ImageGrab

    ImageGrab.grab().save(out_path)
    if not out_path.exists():
        raise RuntimeError("Screenshot failed using PIL ImageGrab.")
    return out_path


def _capture_screencapture(out_path: Path) -> Path:
    _run_checked(["screencapture", "-x", str(out_path)], out_path)
    return out_path


def _capture_grim(out_path: Path) -> Path:
    monitor_name = _get_cs2_monitor_name()
    cmd = ["grim", "-o", monitor_name, str(out_path)] if monitor_name else ["grim", str(out_path)]
    _run_checked(cmd, out_path)
    return out_path


def _capture_imagemagick(out_path: Path) -> Path:
    _run_checked(["import", "-window", "root", str(out_path)], out_path)
    return out_path


CAPTURERS: dict[str, Capturer] = {
    "mss": _capture_mss,
    "pil": _capture_pil,
    "screencapture": _capture_screencapture,
    "grim": _capture_grim,
    "imagemagick": _capture_imagemagick,
}


# ---- auto selection (no explicit tool) --------------------------------------------------

def _capture_win32(out_path: Path) -> Path:
    win32_enable_dpi_awareness()
    hwnd = win32_find_cs2_hwnd()
    center = win32_get_window_center(hwnd) if hwnd else None
    _capture_with_mss_or_pil(out_path, center=center)
    if not out_path.exists():
        raise RuntimeError("Screenshot failed on Windows.")
    return out_path


def _capture_macos_mss(out_path: Path) -> Path:
    """macOS fallback when the `screencapture` binary is missing."""
    _capture_with_mss_or_pil(out_path)
    if not out_path.exists():
        raise RuntimeError("Screenshot failed on macOS.")
    return out_path


AUTO_CAPTURERS: dict[str, Capturer] = {
    "win32": _capture_win32,
    "macos_mss": _capture_macos_mss,
}


def select_auto_tool(
    platform_name: str,
    which: Callable[[str], str | None],
    environ: Mapping[str, str],
) -> str:
    """Name of the tool auto mode uses: a CAPTURERS key or an AUTO_CAPTURERS key.

    Order: Windows -> win32; macOS -> screencapture if present else macos_mss;
    otherwise (Linux and others) -> grim on Wayland if present, else ImageMagick `import`.
    """
    if platform_name == "win32":
        return "win32"
    if platform_name == "darwin":
        return "screencapture" if which("screencapture") else "macos_mss"
    if environ.get("WAYLAND_DISPLAY") and which("grim"):
        return "grim"
    if which("import"):
        return "imagemagick"
    raise RuntimeError("No screenshot tool found (need 'grim' on Wayland or ImageMagick 'import').")


def _auto_capturer() -> Capturer:
    name = select_auto_tool(sys.platform, shutil.which, os.environ)
    return CAPTURERS.get(name) or AUTO_CAPTURERS[name]


def capture_screenshot() -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    out_path = get_screenshot_dir() / f"cs2_{ts}.png"
    tool = str(load_config().get("screenshot_tool") or "auto").strip().lower()
    capturer = CAPTURERS.get(tool) or _auto_capturer()
    return capturer(out_path)
