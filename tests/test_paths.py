"""Tests for storage.paths overrides: CS2WATCH_DATA_DIR and CS2WATCH_WEB_DIR.

The values are computed at import time, so every case runs storage.paths in a fresh interpreter.

Run from the repo root:
    python -m unittest tests.test_paths -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

OVERRIDE_VARS = ("CS2WATCH_DATA_DIR", "CS2WATCH_WEB_DIR")

PROBE = (
    "import json\n"
    "from storage import paths as p\n"
    "print(json.dumps({k: str(getattr(p, k)) for k in ('ROOT', 'DATA_DIR', 'LOG_DIR', 'SCREENSHOT_DIR', 'WEB_DIR')}))\n"
)


def probe_paths(test: unittest.TestCase, extra_env: dict[str, str], home: str | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in OVERRIDE_VARS}
    if home is not None:
        env["HOME"] = home
    env.update(extra_env)
    proc = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    test.assertEqual(proc.returncode, 0, proc.stderr)
    return json.loads(proc.stdout)


class PathsDefaultsTests(unittest.TestCase):
    def test_defaults_are_under_repo_root(self):
        paths = probe_paths(self, {})
        root = REPO_ROOT
        self.assertEqual(paths["ROOT"], str(root))
        self.assertEqual(paths["DATA_DIR"], str(root / "data"))
        self.assertEqual(paths["LOG_DIR"], str(root / "data" / "logs"))
        self.assertEqual(paths["SCREENSHOT_DIR"], str(root / "data" / "screenshots"))
        self.assertEqual(paths["WEB_DIR"], str(root / "web"))

    def test_empty_variables_mean_default(self):
        paths = probe_paths(self, {"CS2WATCH_DATA_DIR": "", "CS2WATCH_WEB_DIR": ""})
        self.assertEqual(paths["DATA_DIR"], str(REPO_ROOT / "data"))
        self.assertEqual(paths["WEB_DIR"], str(REPO_ROOT / "web"))


class PathsOverrideTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()

    def test_data_dir_override_moves_derived_paths(self):
        data = self.tmp / "instance2"
        paths = probe_paths(self, {"CS2WATCH_DATA_DIR": str(data)})
        self.assertEqual(paths["DATA_DIR"], str(data))
        self.assertEqual(paths["LOG_DIR"], str(data / "logs"))
        self.assertEqual(paths["SCREENSHOT_DIR"], str(data / "screenshots"))
        self.assertEqual(paths["WEB_DIR"], str(REPO_ROOT / "web"))

    def test_import_creates_nothing(self):
        data = self.tmp / "instance2"
        web = self.tmp / "ui"
        probe_paths(self, {"CS2WATCH_DATA_DIR": str(data), "CS2WATCH_WEB_DIR": str(web)})
        self.assertEqual(os.listdir(self.tmp), [])

    def test_web_dir_override(self):
        web = self.tmp / "ui"
        paths = probe_paths(self, {"CS2WATCH_WEB_DIR": str(web)})
        self.assertEqual(paths["WEB_DIR"], str(web))
        self.assertEqual(paths["DATA_DIR"], str(REPO_ROOT / "data"))

    def test_tilde_is_expanded(self):
        home = self.tmp / "home"
        home.mkdir()
        paths = probe_paths(self, {"CS2WATCH_DATA_DIR": "~/cs2data"}, home=str(home))
        self.assertEqual(paths["DATA_DIR"], str(home / "cs2data"))


if __name__ == "__main__":
    unittest.main()
