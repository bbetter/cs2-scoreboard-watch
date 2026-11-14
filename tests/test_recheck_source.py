"""Unit tests for POST /api/recheck-source (per-source refresh).

Fetchers are mocked; history is redirected to a temp file so the real
scans_history.json is never touched.
Run: /home/andrii/fun/.venv/bin/python -m unittest discover -s tests -v
"""

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.server as server  # noqa: E402
from app.scan_store import ScanStore  # noqa: E402
import lookup.steam_lookup as steam_lookup  # noqa: E402


def _make_scan(scan_id: str = "scan-1") -> dict:
    return {
        "id": scan_id,
        "created_at": "2026-11-14T10:00:00",
        "players": [
            {
                "nick": "alpha",
                "cheat_probability": 80,
                "steam": {
                    "steamid64": "76561198000000001",
                    "lookup_source": "steam_web_api",
                    "leetify": {"aim": 10},
                    "cs2_stats": {"kd": 1.1},
                    "extra_stats": {"cstracker": {"status": "blocked"}, "other": {"keep": True}},
                },
            },
            {
                "nick": "beta",
                "cheat_probability": 10,
                "steam": {"steamid64": "76561198000000002", "leetify": None, "extra_stats": {}},
            },
        ],
    }


def _store(path: Path, scans: list) -> ScanStore:
    store = ScanStore(path)
    store.scans[:] = scans
    return store


class RecheckSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._patches = [
            mock.patch.object(server, "STORE", _store(Path(self._tmp.name) / "history.json", [_make_scan()])),
            mock.patch.object(server, "_broadcast_event"),
        ]
        for p in self._patches:
            p.start()
        self.leetify = mock.patch.object(steam_lookup, "_fetch_leetify", return_value={"aim": 99}).start()
        self.cstracker = mock.patch.object(steam_lookup, "_fetch_cstracker", return_value={"status": "ok"}).start()

    def tearDown(self) -> None:
        mock.patch.stopall()
        self._tmp.cleanup()

    def _player(self, nick: str) -> dict:
        return next(p for p in server.STORE.scans[0]["players"] if p["nick"] == nick)

    def test_leetify_refresh_updates_only_leetify(self) -> None:
        status, body = server.recheck_source_for_player("scan-1", "alpha", "leetify")
        self.assertEqual(status, 200)
        self.leetify.assert_called_once_with("76561198000000001")
        self.cstracker.assert_not_called()
        steam = self._player("alpha")["steam"]
        self.assertEqual(steam["leetify"], {"aim": 99})
        self.assertEqual(steam["cs2_stats"], {"kd": 1.1})
        self.assertEqual(steam["extra_stats"]["cstracker"], {"status": "blocked"})
        self.assertEqual(self._player("alpha")["cheat_probability"], 80)
        self.assertEqual(body["id"], "scan-1")

    def test_cstracker_refresh_keeps_other_extra_stats(self) -> None:
        status, _ = server.recheck_source_for_player("scan-1", "alpha", "cstracker")
        self.assertEqual(status, 200)
        self.cstracker.assert_called_once_with("76561198000000001")
        self.leetify.assert_not_called()
        extra = self._player("alpha")["steam"]["extra_stats"]
        self.assertEqual(extra["cstracker"], {"status": "ok"})
        self.assertEqual(extra["other"], {"keep": True})
        self.assertEqual(self._player("alpha")["steam"]["leetify"], {"aim": 10})

    def test_leetify_none_is_stored_as_none(self) -> None:
        self.leetify.return_value = None
        status, _ = server.recheck_source_for_player("scan-1", "alpha", "leetify")
        self.assertEqual(status, 200)
        self.assertIsNone(self._player("alpha")["steam"]["leetify"])

    def test_bad_source_is_400(self) -> None:
        status, body = server.recheck_source_for_player("scan-1", "alpha", "faceit")
        self.assertEqual(status, 400)
        self.assertIn("error", body)
        self.leetify.assert_not_called()
        self.cstracker.assert_not_called()

    def test_missing_fields_is_400(self) -> None:
        self.assertEqual(server.recheck_source_for_player(None, "alpha", "leetify")[0], 400)
        self.assertEqual(server.recheck_source_for_player("scan-1", "", "leetify")[0], 400)

    def test_unknown_scan_and_player_is_404(self) -> None:
        self.assertEqual(server.recheck_source_for_player("nope", "alpha", "leetify")[0], 404)
        self.assertEqual(server.recheck_source_for_player("scan-1", "ghost", "leetify")[0], 404)

    def test_no_steamid_is_409_without_fetch(self) -> None:
        self._player("beta")["steam"]["steamid64"] = None
        status, _ = server.recheck_source_for_player("scan-1", "beta", "leetify")
        self.assertEqual(status, 409)
        self.leetify.assert_not_called()

    def test_fetch_exception_is_502_and_state_unchanged(self) -> None:
        self.cstracker.side_effect = RuntimeError("boom")
        status, body = server.recheck_source_for_player("scan-1", "alpha", "cstracker")
        self.assertEqual(status, 502)
        self.assertIn("boom", body["error"])
        self.assertEqual(self._player("alpha")["steam"]["extra_stats"]["cstracker"], {"status": "blocked"})

    def test_history_is_saved(self) -> None:
        server.recheck_source_for_player("scan-1", "alpha", "leetify")
        saved = json.loads(server.STORE.history_file.read_text(encoding="utf-8"))
        self.assertEqual(saved[0]["players"][0]["steam"]["leetify"], {"aim": 99})


class RecheckSourceHttpTests(unittest.TestCase):
    """Checks the route itself: status codes and JSON bodies over a real socket."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.CsWatchHandler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._patches = [
            mock.patch.object(server, "STORE", _store(Path(self._tmp.name) / "history.json", [_make_scan()])),
            mock.patch.object(server, "_broadcast_event"),
            mock.patch.object(steam_lookup, "_fetch_leetify", return_value={"aim": 42}),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        mock.patch.stopall()
        self._tmp.cleanup()

    def _post(self, raw: bytes) -> tuple[int, dict]:
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/recheck-source",
            data=raw,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())

    def test_ok_returns_scan(self) -> None:
        body = json.dumps({"scan_id": "scan-1", "nick": "alpha", "source": "leetify"}).encode()
        status, data = self._post(body)
        self.assertEqual(status, 200)
        self.assertEqual(data["players"][0]["steam"]["leetify"], {"aim": 42})

    def test_invalid_json_is_400(self) -> None:
        status, data = self._post(b"{not json")
        self.assertEqual(status, 400)
        self.assertIn("error", data)

    def test_non_object_is_400(self) -> None:
        status, _ = self._post(b"[1, 2]")
        self.assertEqual(status, 400)

    def test_bad_source_is_400_over_http(self) -> None:
        body = json.dumps({"scan_id": "scan-1", "nick": "alpha", "source": "steam"}).encode()
        status, _ = self._post(body)
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
