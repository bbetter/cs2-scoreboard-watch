"""Desktop notifications: notify-send on Linux, osascript on macOS, no-op on Windows."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Callable, Mapping, Optional


def build_notification_command(
    title: str,
    msg: str,
    platform_name: str = sys.platform,
    which: Callable[[str], Optional[str]] = shutil.which,
    environ: Mapping[str, str] = os.environ,
) -> Optional[tuple[list[str], dict]]:
    """Return (argv, subprocess kwargs) for the notification, or None when nothing should run."""
    if platform_name.startswith("linux"):
        if which("notify-send"):
            clean_env = {k: v for k, v in environ.items() if k != "LD_LIBRARY_PATH"}
            return ["notify-send", title, msg], {"env": clean_env, "check": False}
        return None
    if platform_name == "darwin":
        if which("osascript"):
            safe_msg = msg.replace("\\", "\\\\").replace('"', '\\"')
            safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
            return ["osascript", "-e", f'display notification "{safe_msg}" with title "{safe_title}"'], {"check": False}
        return None
    # Windows: safe no-op
    return None


def send_notification(title: str, msg: str) -> None:
    """Best-effort notification; never raises."""
    try:
        # Resolve platform/which/environ at call time: the defaults above are bound at import.
        spec = build_notification_command(title, msg, sys.platform, shutil.which, os.environ)
        if spec is not None:
            argv, kwargs = spec
            subprocess.run(argv, **kwargs)
    except Exception:
        pass
