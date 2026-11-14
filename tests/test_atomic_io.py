"""Tests for atomic_io.atomic_write_text (temp file + os.replace)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from atomic_io import atomic_write_text  # noqa: E402


class AtomicWriteTextTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _leftover_tmp_files(self):
        return [p.name for p in self.dir.iterdir() if p.name.endswith(".tmp")]

    def test_writes_content_and_leaves_no_tmp_file(self):
        target = self.dir / "data.json"
        atomic_write_text(target, '{"a": "привіт"}')
        self.assertEqual(target.read_text(encoding="utf-8"), '{"a": "привіт"}')
        self.assertEqual(self._leftover_tmp_files(), [])

    def test_overwrites_existing_file(self):
        target = self.dir / "data.json"
        target.write_text("old", encoding="utf-8")
        atomic_write_text(target, "new")
        self.assertEqual(target.read_text(encoding="utf-8"), "new")
        self.assertEqual(self._leftover_tmp_files(), [])

    def test_original_preserved_when_write_fails(self):
        target = self.dir / "data.json"
        target.write_text("original", encoding="utf-8")
        # Non-str payload makes the underlying write raise TypeError after the temp file is opened.
        with self.assertRaises(TypeError):
            atomic_write_text(target, 12345)  # type: ignore[arg-type]
        self.assertEqual(target.read_text(encoding="utf-8"), "original")
        self.assertEqual(self._leftover_tmp_files(), [])

    def test_original_preserved_when_replace_fails(self):
        target = self.dir / "data.json"
        target.write_text("original", encoding="utf-8")
        with mock.patch("atomic_io.os.replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                atomic_write_text(target, "new content")
        self.assertEqual(target.read_text(encoding="utf-8"), "original")
        self.assertEqual(self._leftover_tmp_files(), [])

    def test_failure_on_new_file_creates_nothing(self):
        target = self.dir / "fresh.json"
        with self.assertRaises(TypeError):
            atomic_write_text(target, None)  # type: ignore[arg-type]
        self.assertFalse(target.exists())
        self.assertEqual(os.listdir(self.dir), [])


if __name__ == "__main__":
    unittest.main()
