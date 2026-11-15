"""Runtime paths. Everything the app writes (config, DB, caches, history, logs, screenshots) lives under DATA_DIR.

DATA_DIR is gitignored. The only file kept at the project root is .env (secrets, read by steam_lookup).

Overrides (read once, at import; nothing is created here):
- CS2WATCH_DATA_DIR replaces ROOT/"data" (LOG_DIR and SCREENSHOT_DIR follow it).
- CS2WATCH_WEB_DIR replaces ROOT/"web" (the static UI).
An unset or empty variable means the default.
"""

import os
from pathlib import Path


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    return Path(raw).expanduser().resolve()


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = _env_path("CS2WATCH_DATA_DIR", ROOT / "data")
LOG_DIR = DATA_DIR / "logs"
SCREENSHOT_DIR = DATA_DIR / "screenshots"
WEB_DIR = _env_path("CS2WATCH_WEB_DIR", ROOT / "web")
