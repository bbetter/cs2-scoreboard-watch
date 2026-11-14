"""Tests for player_db (SQLite player history) and its server integration.

Run from the project root:
    /home/andrii/fun/.venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import player_db  # noqa: E402
from player_db import PlayerDB  # noqa: E402

SID_A = "76561198000000001"
SID_B = "76561198000000002"


def _player(sid: str | None, nick: str, cheat: float = 10.0, smurf: float = 5.0, risk: str = "low") -> dict:
    return {
        "nick": nick,
        "cheat_probability": cheat,
        "smurf_probability": smurf,
        "risk_level": risk,
        "steam": {"steamid64": sid} if sid else {},
    }


class _TempDBCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "players.db"
        self.db = PlayerDB(self.db_path)

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()


class SchemaTests(_TempDBCase):
    def test_schema_created_with_expected_columns_and_wal(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            players_cols = [r[1] for r in conn.execute("PRAGMA table_info(players)")]
            sightings_cols = [r[1] for r in conn.execute("PRAGMA table_info(sightings)")]
            journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(players_cols, ["steamid64", "last_nick", "first_seen", "last_seen", "tag", "notes"])
        self.assertEqual(
            sightings_cols,
            ["id", "steamid64", "scan_id", "nick", "seen_at", "cheat_probability", "smurf_probability", "risk_level"],
        )
        self.assertEqual(journal.lower(), "wal")

    def test_reopening_existing_db_keeps_data(self) -> None:
        self.db.upsert_player(SID_A, "alpha", "2026-01-01T10:00:00")
        self.db.close()
        self.db = PlayerDB(self.db_path)
        profile = self.db.get_player(SID_A)
        self.assertIsNotNone(profile)
        self.assertEqual(profile["last_nick"], "alpha")


class UpsertTests(_TempDBCase):
    def test_upsert_creates_then_updates_without_touching_first_seen(self) -> None:
        self.db.upsert_player(SID_A, "alpha", "2026-01-01T10:00:00")
        self.db.upsert_player(SID_A, "alpha2", "2026-02-01T10:00:00")
        profile = self.db.get_player(SID_A)
        self.assertEqual(profile["first_seen"], "2026-01-01T10:00:00")
        self.assertEqual(profile["last_seen"], "2026-02-01T10:00:00")
        self.assertEqual(profile["last_nick"], "alpha2")

    def test_set_player_meta_updates_only_given_fields(self) -> None:
        self.db.upsert_player(SID_A, "alpha", "2026-01-01T10:00:00")
        self.assertTrue(self.db.set_player_meta(SID_A, tag="suspect"))
        self.assertTrue(self.db.set_player_meta(SID_A, notes="watch him"))
        profile = self.db.get_player(SID_A)
        self.assertEqual(profile["tag"], "suspect")
        self.assertEqual(profile["notes"], "watch him")

    def test_set_player_meta_unknown_player_returns_false(self) -> None:
        self.assertFalse(self.db.set_player_meta(SID_B, tag="x"))
        self.assertIsNone(self.db.get_player(SID_B))

    def test_set_player_meta_rejects_unknown_fields(self) -> None:
        self.db.upsert_player(SID_A, "alpha", "2026-01-01T10:00:00")
        with self.assertRaises(ValueError):
            self.db.set_player_meta(SID_A, nick="hacker")


class SightingTests(_TempDBCase):
    def test_record_sightings_inserts_only_players_with_steamid(self) -> None:
        players = [_player(SID_A, "alpha", 80, 20, "high"), _player(None, "nosteam")]
        inserted = self.db.record_sightings("scan1", players, "2026-03-01T12:00:00")
        self.assertEqual(inserted, 1)
        self.assertIsNone(self.db.get_player("76561198999999999"))

    def test_same_scan_is_not_recorded_twice(self) -> None:
        players = [_player(SID_A, "alpha")]
        self.assertEqual(self.db.record_sightings("scan1", players, "2026-03-01T12:00:00"), 1)
        self.assertEqual(self.db.record_sightings("scan1", players, "2026-03-01T12:00:00"), 0)
        self.assertEqual(self.db.get_player(SID_A)["sighting_count"], 1)

    def test_read_history_newest_first_with_limit(self) -> None:
        for i in range(5):
            self.db.record_sightings(f"scan{i}", [_player(SID_A, f"nick{i}", cheat=float(i))],
                                     f"2026-03-0{i + 1}T12:00:00")
        profile = self.db.get_player(SID_A, sightings_limit=3)
        self.assertEqual(profile["sighting_count"], 5)
        self.assertEqual([s["scan_id"] for s in profile["sightings"]], ["scan4", "scan3", "scan2"])
        self.assertEqual(profile["sightings"][0]["nick"], "nick4")
        self.assertEqual(profile["last_nick"], "nick4")
        self.assertEqual(profile["first_seen"], "2026-03-01T12:00:00")
        self.assertEqual(profile["last_seen"], "2026-03-05T12:00:00")

    def test_sighting_stores_scores_and_risk(self) -> None:
        self.db.record_sightings("scan1", [_player(SID_A, "alpha", 77.5, 12.25, "high")], "2026-03-01T12:00:00")
        s = self.db.get_player(SID_A)["sightings"][0]
        self.assertEqual(s["cheat_probability"], 77.5)
        self.assertEqual(s["smurf_probability"], 12.25)
        self.assertEqual(s["risk_level"], "high")


class ConcurrencyTests(_TempDBCase):
    def test_parallel_writes_from_many_threads_are_all_persisted(self) -> None:
        threads_count, scans_per_thread = 8, 25
        errors: list[BaseException] = []
        start = threading.Barrier(threads_count)

        def worker(t: int) -> None:
            try:
                start.wait()
                for s in range(scans_per_thread):
                    scan_id = f"t{t}-s{s}"
                    players = [_player(SID_A, f"nick{t}"), _player(SID_B, f"other{t}")]
                    self.db.record_sightings(scan_id, players, "2026-04-01T00:00:00")
                    self.db.upsert_player(SID_A, f"nick{t}", "2026-04-01T00:00:00")
                    self.db.get_player(SID_A, sightings_limit=5)
            except BaseException as exc:  # noqa: BLE001 - surface any thread failure
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(threads_count)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        self.assertEqual(errors, [])
        expected = threads_count * scans_per_thread
        self.assertEqual(self.db.get_player(SID_A)["sighting_count"], expected)
        self.assertEqual(self.db.get_player(SID_B)["sighting_count"], expected)


class DbFailureTests(_TempDBCase):
    def test_closed_db_raises_sqlite_error(self) -> None:
        self.db.close()
        with self.assertRaises(sqlite3.Error):
            self.db.record_sightings("scan1", [_player(SID_A, "alpha")], "2026-03-01T12:00:00")

    def test_scan_finalization_survives_db_failure(self) -> None:
        """A broken DB must not stop the scan from reaching status 'done' and being broadcast."""
        import server

        self.db.close()  # every DB call now raises sqlite3.ProgrammingError
        scan_id = "failscan01"
        scan_rec = {
            "id": scan_id,
            "created_at": "2026-03-01T12:00:00",
            "players": [],
            "player_count": 0,
            "scan_status": "loading_steam",
            "progress": {"done": 0, "total": 0},
        }
        with server._STATE_LOCK:
            server._SCANS.insert(0, scan_rec)

        broadcasts: list[tuple[str, dict]] = []
        try:
            with mock.patch.object(player_db, "_default_db", self.db), \
                    mock.patch.object(server, "enrich_players", return_value=[]), \
                    mock.patch.object(server, "load_config", return_value={"ai_enabled": False}), \
                    mock.patch.object(server, "_save_history_locked", return_value=None), \
                    mock.patch.object(server, "_broadcast_event", side_effect=lambda e, p: broadcasts.append((e, p))):
                server._enrich_scan_async(scan_id, [], 0.0)
        finally:
            with server._STATE_LOCK:
                server._SCANS[:] = [s for s in server._SCANS if s["id"] != scan_id]

        self.assertEqual(scan_rec["scan_status"], "done")
        self.assertEqual(broadcasts[-1][0], "scan")
        self.assertEqual(broadcasts[-1][1]["id"], scan_id)


class HttpEndpointTests(_TempDBCase):
    @classmethod
    def setUpClass(cls) -> None:
        import server

        cls.server = server
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.CsWatchHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self) -> None:
        super().setUp()
        self._patch = mock.patch.object(player_db, "_default_db", self.db)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        super().tearDown()

    def _request(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data, method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_get_player_returns_profile_and_sightings(self) -> None:
        self.db.record_sightings("scan1", [_player(SID_A, "alpha")], "2026-03-01T12:00:00")
        status, body = self._request("GET", f"/api/players/{SID_A}?limit=10")
        self.assertEqual(status, 200)
        self.assertEqual(body["steamid64"], SID_A)
        self.assertEqual(len(body["sightings"]), 1)

    def test_get_unknown_player_is_404(self) -> None:
        status, _ = self._request("GET", f"/api/players/{SID_B}")
        self.assertEqual(status, 404)

    def test_get_invalid_steamid_is_400(self) -> None:
        status, _ = self._request("GET", "/api/players/not-a-steamid")
        self.assertEqual(status, 400)

    def test_post_saves_tag_and_notes(self) -> None:
        self.db.upsert_player(SID_A, "alpha", "2026-01-01T10:00:00")
        status, body = self._request("POST", f"/api/players/{SID_A}", {"tag": "smurf", "notes": "check"})
        self.assertEqual(status, 200)
        self.assertEqual(body["tag"], "smurf")
        self.assertEqual(body["notes"], "check")
        self.assertEqual(self.db.get_player(SID_A)["tag"], "smurf")

    def test_post_partial_update_keeps_other_field(self) -> None:
        self.db.upsert_player(SID_A, "alpha", "2026-01-01T10:00:00")
        self._request("POST", f"/api/players/{SID_A}", {"notes": "keep me"})
        status, body = self._request("POST", f"/api/players/{SID_A}", {"tag": "x"})
        self.assertEqual(status, 200)
        self.assertEqual(body["notes"], "keep me")
        self.assertEqual(body["tag"], "x")

    def test_post_unknown_player_is_404(self) -> None:
        status, _ = self._request("POST", f"/api/players/{SID_B}", {"tag": "x"})
        self.assertEqual(status, 404)

    def test_post_rejects_non_string_and_too_long(self) -> None:
        self.db.upsert_player(SID_A, "alpha", "2026-01-01T10:00:00")
        status, _ = self._request("POST", f"/api/players/{SID_A}", {"tag": 5})
        self.assertEqual(status, 400)
        status, _ = self._request("POST", f"/api/players/{SID_A}", {"tag": "t" * (player_db.TAG_MAX_LEN + 1)})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
