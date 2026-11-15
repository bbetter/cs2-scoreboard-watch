"""Unit tests for app/scan_store.py: load/save of the scan history file and the lookups on the store."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


class PersistenceErrorTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "scans_history.json"

    def test_corrupt_json_is_logged_and_moved_aside(self) -> None:
        self.path.write_text("{broken", encoding="utf-8")
        store = ScanStore(self.path)
        with self.assertLogs("cs2watch.scan_store", level="WARNING") as cm:
            store.load()
        self.assertEqual(store.scans, [])
        self.assertFalse(self.path.exists())
        corrupt = self.dir / "scans_history.json.corrupt"
        self.assertEqual(corrupt.read_text(encoding="utf-8"), "{broken")
        self.assertIn("moved to", cm.output[0])

    def test_second_corrupt_file_gets_a_timestamped_name(self) -> None:
        (self.dir / "scans_history.json.corrupt").write_text("first", encoding="utf-8")
        self.path.write_text("{second", encoding="utf-8")
        store = ScanStore(self.path)
        with self.assertLogs("cs2watch.scan_store", level="WARNING"):
            store.load()
        self.assertEqual((self.dir / "scans_history.json.corrupt").read_text(encoding="utf-8"), "first")
        stamped = list(self.dir.glob("scans_history.json.corrupt.*"))
        self.assertEqual(len(stamped), 1)
        self.assertEqual(stamped[0].read_text(encoding="utf-8"), "{second")
        self.assertFalse(self.path.exists())

    def test_non_list_json_is_treated_as_corrupt(self) -> None:
        self.path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
        store = ScanStore(self.path)
        with self.assertLogs("cs2watch.scan_store", level="WARNING"):
            store.load()
        self.assertEqual(store.scans, [])
        self.assertTrue((self.dir / "scans_history.json.corrupt").exists())
        self.assertFalse(self.path.exists())

    def test_failed_move_aside_is_logged_and_file_is_kept(self) -> None:
        self.path.write_text("{broken", encoding="utf-8")
        store = ScanStore(self.path)
        with mock.patch("app.scan_store.os.replace", side_effect=OSError("read-only fs")), \
                self.assertLogs("cs2watch.scan_store", level="ERROR") as cm:
            store.load()
        self.assertIn("could not be moved aside", cm.output[0])
        self.assertTrue(self.path.exists())
        self.assertEqual(store.scans, [])

    def test_save_failure_is_logged_and_does_not_raise(self) -> None:
        store = ScanStore(self.path)
        store.scans[:] = [_scan("a")]
        with mock.patch("app.scan_store.atomic_write_text", side_effect=OSError("disk full")), \
                self.assertLogs("cs2watch.scan_store", level="ERROR") as cm:
            store.save_locked()  # must not raise
        self.assertEqual(store.scans, [_scan("a")])  # in-memory state unchanged
        exc = cm.records[0].exc_info[1]
        self.assertIn("disk full", str(exc))


class AdapterFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "scans_history.json"

    def test_failing_adapter_moves_the_file_aside_and_keeps_the_data(self) -> None:
        records = [_scan("good1"), {"id": "bad", "schema_version": "2"}, _scan("good2")]
        self.path.write_text(json.dumps(records), encoding="utf-8")

        def adapt(scan: dict) -> None:
            if not isinstance(scan.get("schema_version", 1), int):
                raise TypeError("'>' not supported")

        store = ScanStore(self.path)
        with self.assertLogs("cs2watch.scan_store", level="WARNING") as cm:
            store.load(adapt=adapt)
        self.assertEqual(store.scans, [])
        self.assertFalse(self.path.exists())  # a later save must not overwrite the original
        corrupt = self.dir / "scans_history.json.corrupt"
        self.assertEqual(json.loads(corrupt.read_text(encoding="utf-8")), records)
        self.assertIn("could not be adapted", cm.output[0])


if __name__ == "__main__":
    unittest.main()
