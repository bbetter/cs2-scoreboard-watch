"""Runtime paths. Everything the app writes (config, DB, caches, history, logs, screenshots) lives under DATA_DIR.

DATA_DIR is gitignored. The only file kept at the project root is .env (secrets, read by steam_lookup).
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
LOG_DIR = DATA_DIR / "logs"
SCREENSHOT_DIR = DATA_DIR / "screenshots"
WEB_DIR = ROOT / "web"
