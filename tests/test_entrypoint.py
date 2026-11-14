"""Entry points: `python -m cs2watch --help` must work and list the same options as `python -m app.watch_cs2 --help`.

--help exits inside argparse, before any server, screenshot or config write. Since T16 importing the modules
writes nothing under data/ (the pton.logs handler is attached lazily), so no file under data/ may change.
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


def _tree_snapshot(path: Path) -> dict:
    """(mtime_ns, size) of every file under path, recursively. Empty if path does not exist."""
    out: dict[str, tuple[int, int]] = {}
    if not path.is_dir():
        return out
    for root, _dirs, files in os.walk(path):
        for name in files:
            full = Path(root) / name
            st = full.stat()
            out[str(full.relative_to(path))] = (st.st_mtime_ns, st.st_size)
    return out


class EntrypointHelpTests(unittest.TestCase):
    def test_module_entrypoint_help_exits_zero(self):
        res = _help(["-m", "cs2watch"])
        self.assertEqual(res.returncode, 0, res.stderr[-2000:])
        self.assertIn("usage:", res.stdout)

    def test_module_and_script_list_the_same_options(self):
        mod = _help(["-m", "cs2watch"])
        script = _help(["-m", "app.watch_cs2"])
        self.assertEqual(script.returncode, 0, script.stderr[-2000:])
        mod_opts = set(OPTION.findall(mod.stdout))
        script_opts = set(OPTION.findall(script.stdout))
        self.assertEqual(mod_opts, script_opts)
        self.assertEqual(script_opts, {"--help", "--host", "--port", "--delay", "--cooldown", "--all-windows"})

    def test_help_does_not_touch_screenshots_or_config(self):
        shots = ROOT / "data" / "screenshots"
        config = ROOT / "data" / "config.json"
        before_shots = _snapshot(shots)
        before_config = config.stat().st_mtime_ns if config.exists() else None
        res = _help(["-m", "cs2watch"])
        self.assertEqual(res.returncode, 0)
        self.assertEqual(_snapshot(shots), before_shots)
        self.assertEqual(config.stat().st_mtime_ns if config.exists() else None, before_config)

    def test_help_creates_or_changes_no_file_in_data_dir(self):
        data = ROOT / "data"
        before = _tree_snapshot(data)
        for args in (["-m", "cs2watch"], ["-m", "app.watch_cs2"]):
            res = _help(args)
            self.assertEqual(res.returncode, 0, res.stderr[-2000:])
        after = _tree_snapshot(data)
        self.assertEqual(sorted(set(after) - set(before)), [], "--help created files in data/")
        self.assertEqual(sorted(set(before) - set(after)), [], "--help removed files from data/")
        self.assertEqual(
            sorted(p for p in set(before) & set(after) if before[p] != after[p]), [], "--help changed files in data/"
        )


if __name__ == "__main__":
    unittest.main()
