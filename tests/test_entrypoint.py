"""Entry points: `python -m cs2watch --help` must work and list the same options as `python watch_cs2.py --help`.

--help exits inside argparse, before any server, screenshot or config write. Importing the modules does open
pton.logs in append mode (steam_lookup), so that file is the only one this test allows to change.
Run: /home/andrii/fun/.venv/bin/python -m unittest discover -s tests -v
"""

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OPTION = re.compile(r"(?<![\w-])(--[a-z][a-z-]*)")


def _help(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args, "--help"],
        cwd=ROOT, capture_output=True, text=True, timeout=20,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )


def _snapshot(path: Path) -> dict:
    return {p.name: (p.stat().st_mtime_ns, p.stat().st_size) for p in path.iterdir()} if path.is_dir() else {}


class EntrypointHelpTests(unittest.TestCase):
    def test_module_entrypoint_help_exits_zero(self):
        res = _help(["-m", "cs2watch"])
        self.assertEqual(res.returncode, 0, res.stderr[-2000:])
        self.assertIn("usage:", res.stdout)

    def test_module_and_script_list_the_same_options(self):
        mod = _help(["-m", "cs2watch"])
        script = _help(["watch_cs2.py"])
        self.assertEqual(script.returncode, 0, script.stderr[-2000:])
        mod_opts = set(OPTION.findall(mod.stdout))
        script_opts = set(OPTION.findall(script.stdout))
        self.assertEqual(mod_opts, script_opts)
        self.assertEqual(script_opts, {"--help", "--host", "--port", "--delay", "--cooldown", "--all-windows"})

    def test_help_does_not_touch_screenshots_or_config(self):
        shots = ROOT / "screenshots"
        config = ROOT / "config.json"
        before_shots = _snapshot(shots)
        before_config = config.stat().st_mtime_ns if config.exists() else None
        res = _help(["-m", "cs2watch"])
        self.assertEqual(res.returncode, 0)
        self.assertEqual(_snapshot(shots), before_shots)
        self.assertEqual(config.stat().st_mtime_ns if config.exists() else None, before_config)


if __name__ == "__main__":
    unittest.main()
