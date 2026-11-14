"""Tests for storage.api_keys._get_steam_api_key lookup order.

Order: cache, config.json (load_config), environment, root .env. Shell rc files (~/.zshrc, ~/.bashrc) are never read.

Run from the repo root:
    python -m unittest tests.test_api_keys -v

Everything is isolated: ROOT and the home directory point at temp dirs, the environment is cleared, and load_config
is replaced, so the developer's real data/config.json, .env and shell files are never touched.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from storage import api_keys  # noqa: E402

CONFIG_KEY = "TESTKEYconfig0123456789"
ENV_KEY = "TESTKEYenvvar0123456789"
DOTENV_KEY = "TESTKEYdotenv0123456789"
RC_KEY = "TESTKEYshellrc0123456789"


class SteamApiKeyLookupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.root = base / "project"
        self.home = base / "home"
        self.root.mkdir()
        self.home.mkdir()

        self._patches = [
            mock.patch.object(api_keys, "ROOT", self.root),
            mock.patch("pathlib.Path.home", return_value=self.home),
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("storage.config_store.load_config", return_value={}),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

        api_keys.reset_steam_api_key_cache()
        self.addCleanup(api_keys.reset_steam_api_key_cache)

    def _write(self, path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8")

    def test_key_only_in_shell_rc_files_is_not_read(self) -> None:
        self._write(self.home / ".zshrc", f'export STEAM_API_KEY="{RC_KEY}"\n')
        self._write(self.home / ".bashrc", f'export STEAM_API_KEY="{RC_KEY}"\n')
        self.assertIsNone(api_keys._get_steam_api_key())

    def test_key_in_root_dotenv_is_found(self) -> None:
        self._write(self.root / ".env", f"STEAM_API_KEY={DOTENV_KEY}\n")
        key = api_keys._get_steam_api_key()
        self.assertEqual(key, DOTENV_KEY)
        self.assertIn("TESTKEY", key)

    def test_environment_variable_wins_over_dotenv(self) -> None:
        self._write(self.root / ".env", f"STEAM_API_KEY={DOTENV_KEY}\n")
        with mock.patch.dict(os.environ, {"STEAM_API_KEY": ENV_KEY}, clear=True):
            key = api_keys._get_steam_api_key()
        self.assertEqual(key, ENV_KEY)
        self.assertIn("TESTKEY", key)

    def test_config_key_wins_over_environment(self) -> None:
        with mock.patch("storage.config_store.load_config", return_value={"steam_api_key": CONFIG_KEY}):
            with mock.patch.dict(os.environ, {"STEAM_API_KEY": ENV_KEY}, clear=True):
                key = api_keys._get_steam_api_key()
        self.assertEqual(key, CONFIG_KEY)
        self.assertIn("TESTKEY", key)


if __name__ == "__main__":
    unittest.main()
