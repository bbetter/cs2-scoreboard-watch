"""Unit tests for app/scan_store.py: load/save of the scan history file and the lookups on the store."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.scan_store import ScanStore  # noqa: E402


def _scan(scan_id: str, **extra) -> dict:
    return {"id": scan_id, "created_at": "2026-01-01T10:00:00", "players": [], **extra}


class ScanStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "scans_history.json"

    def test_save_then_load_round_trips(self) -> None:
        store = ScanStore(self.path)
        store.scans[:] = [_scan("b"), _scan("a")]
        store.save_locked()

        loaded = ScanStore(self.path)
        loaded.load()
        self.assertEqual(loaded.scans, [_scan("b"), _scan("a")])

    def test_save_writes_valid_json_without_escaping_cyrillic(self) -> None:
        store = ScanStore(self.path)
        store.scans[:] = [_scan("x", nick="привіт")]
        store.save_locked()

        text = self.path.read_text(encoding="utf-8")
        self.assertIn("привіт", text)
        self.assertEqual(json.loads(text)[0]["nick"], "привіт")
        self.assertFalse(list(self.dir.glob("*.tmp")))  # atomic write leaves no temp file behind

    def test_load_truncates_to_max_history(self) -> None:
        self.path.write_text(json.dumps([_scan(str(i)) for i in range(5)]), encoding="utf-8")
        store = ScanStore(self.path, max_history=3)
        store.load()
        self.assertEqual([s["id"] for s in store.scans], ["0", "1", "2"])

    def test_load_missing_file_leaves_scans_empty(self) -> None:
        store = ScanStore(self.path)
        store.load()
        self.assertEqual(store.scans, [])

    def test_load_corrupt_json_gives_empty_list_without_raising(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")
        store = ScanStore(self.path)
        store.load()
        self.assertEqual(store.scans, [])

    def test_load_non_list_gives_empty_list(self) -> None:
        self.path.write_text(json.dumps({"id": "x"}), encoding="utf-8")
        store = ScanStore(self.path)
        store.load()
        self.assertEqual(store.scans, [])

    def test_adapt_called_for_dict_scans_only(self) -> None:
        self.path.write_text(json.dumps([_scan("a"), "not-a-dict", _scan("b")]), encoding="utf-8")
        seen: list[str] = []
        store = ScanStore(self.path)
        store.load(adapt=lambda scan: seen.append(scan["id"]))
        self.assertEqual(seen, ["a", "b"])
        self.assertEqual(len(store.scans), 3)

    def test_find_and_latest_on_empty_store(self) -> None:
        store = ScanStore(self.path)
        self.assertIsNone(store.find_locked("a"))
        self.assertIsNone(store.latest_locked())

    def test_find_and_latest_on_populated_store(self) -> None:
        store = ScanStore(self.path)
        store.scans[:] = [_scan("newest"), _scan("older")]
        self.assertEqual(store.find_locked("older")["id"], "older")
        self.assertIsNone(store.find_locked("missing"))
        self.assertEqual(store.latest_locked()["id"], "newest")

    def test_defaults_and_lock(self) -> None:
        store = ScanStore(self.path)
        self.assertEqual(store.max_history, 100)
        self.assertEqual(store.history_file, self.path)
        self.assertTrue(hasattr(store.lock, "acquire") and hasattr(store.lock, "release"))


if __name__ == "__main__":
    unittest.main()
