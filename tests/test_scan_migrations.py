"""Scan record schema versions: v1 -> v2 migration, idempotence, and records from a newer schema."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.scan_migrations as migrations  # noqa: E402
from app.scan_migrations import CURRENT_SCHEMA_VERSION, migrate  # noqa: E402
from sources import REGISTRY  # noqa: E402


def _v1_scan(scan_id: str = "s1", players: int = 2) -> dict:
    return {
        "id": scan_id,
        "players": [
            {"nick": f"p{i}", "steam": {"steamid64": f"7656119800000000{i}", "leetify": {"aim": 80 + i}}}
            for i in range(players)
        ],
    }


class MigrateV1ToV2Tests(unittest.TestCase):
    def test_v1_record_gets_sources_view_uid_and_version(self):
        scan = migrate(_v1_scan(), REGISTRY)
        self.assertEqual(CURRENT_SCHEMA_VERSION, 2)
        self.assertEqual(scan["schema_version"], 2)
        self.assertEqual([p["player_uid"] for p in scan["players"]], ["s1-0", "s1-1"])
        self.assertIn("leetify", scan["players"][0]["steam"]["sources"])
        self.assertEqual(scan["players"][0]["steam"]["sources"]["leetify"]["status"], "ok")

    def test_existing_player_uid_is_kept(self):
        raw = _v1_scan()
        raw["players"][0]["player_uid"] = "keep-me"
        scan = migrate(raw, REGISTRY)
        self.assertEqual(scan["players"][0]["player_uid"], "keep-me")

    def test_legacy_slots_are_not_moved(self):
        scan = migrate(_v1_scan(), REGISTRY)
        steam = scan["players"][0]["steam"]
        self.assertEqual(steam["leetify"], {"aim": 80})

    def test_record_without_players_is_upgraded(self):
        scan = migrate({"id": "empty"}, REGISTRY)
        self.assertEqual(scan["schema_version"], 2)

    def test_player_without_steam_dict_is_left_alone_apart_from_uid(self):
        scan = migrate({"id": "x", "players": [{"nick": "anon"}]}, REGISTRY)
        self.assertNotIn("steam", scan["players"][0])
        self.assertEqual(scan["players"][0]["player_uid"], "x-0")


class IdempotenceTests(unittest.TestCase):
    def test_migrating_a_current_record_changes_nothing(self):
        once = migrate(_v1_scan(), REGISTRY)
        snapshot = repr(once)
        twice = migrate(once, REGISTRY)
        self.assertIs(twice, once)
        self.assertEqual(repr(twice), snapshot)

    def test_migrating_twice_equals_migrating_once(self):
        a = migrate(_v1_scan(), REGISTRY)
        b = migrate(migrate(_v1_scan(), REGISTRY), REGISTRY)
        self.assertEqual(a, b)


class NewerSchemaTests(unittest.TestCase):
    def setUp(self):
        migrations._warned_newer = False

    def test_newer_record_is_untouched(self):
        raw = {"id": "future", "schema_version": 99, "players": [{"nick": "p", "steam": {}}]}
        before = repr(raw)
        out = migrate(raw, REGISTRY)
        self.assertEqual(repr(out), before)
        self.assertNotIn("sources", raw["players"][0]["steam"])

    def test_warns_once_for_several_newer_records(self):
        with self.assertLogs("app.scan_migrations", level="WARNING") as logs:
            migrate({"id": "a", "schema_version": 99, "players": []}, REGISTRY)
            migrate({"id": "b", "schema_version": 99, "players": []}, REGISTRY)
        self.assertEqual(len(logs.records), 1)
        self.assertIn("99", logs.output[0])


if __name__ == "__main__":
    unittest.main()
