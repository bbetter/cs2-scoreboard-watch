"""Player-row operations (app.player_ops) with fake store, registry and callbacks. No HTTP, no server import."""
import threading
import unittest

from app import player_ops
from sources.base import SourceContext


class FakeStore:
    def __init__(self, scans):
        self.lock = threading.Lock()
        self.scans = scans

    def find_locked(self, scan_id):
        return next((s for s in self.scans if s["id"] == scan_id), None)


class FakeResult:
    def __init__(self, data):
        self._data = data

    def to_dict(self):
        return dict(self._data)


class FakeSource:
    recheckable = True

    def __init__(self, sid, enabled=True, fetch=None, fetch_error=None):
        self.id = sid
        self._enabled = enabled
        self._fetch = fetch if fetch is not None else {"fresh": True}
        self._fetch_error = fetch_error
        self.fetched_with = []

    def is_enabled(self, ctx):
        return self._enabled

    def fetch_raw(self, steamid64, ctx):
        self.fetched_with.append(steamid64)
        if self._fetch_error is not None:
            raise self._fetch_error
        return self._fetch

    def write_raw(self, steam, raw):
        steam[self.id] = raw

    def result_from(self, steam):
        raw = steam.get(self.id)
        return FakeResult({"raw": raw}) if raw is not None else None


class FakeRegistry:
    def __init__(self, *sources):
        self._sources = {s.id: s for s in sources}

    def get(self, sid):
        return self._sources.get(sid)

    def recheckable_ids(self):
        return [s.id for s in self._sources.values() if s.recheckable]

    def sources_view(self, steam):
        return {sid: r.to_dict() for sid, s in self._sources.items() if (r := s.result_from(steam)) is not None}


def _player(uid, nick, steamid64=None, **extra):
    steam = {"steamid64": steamid64} if steamid64 else {}
    return {"player_uid": uid, "nick": nick, "steam": steam, **extra}


class PlayerMatchesTests(unittest.TestCase):
    def test_uid_wins_when_given(self):
        p = _player("u1", "alpha")
        self.assertTrue(player_ops.player_matches(p, "u1", "other"))
        self.assertFalse(player_ops.player_matches(p, "u2", "alpha"))

    def test_nick_is_used_when_no_uid(self):
        p = _player("u1", "alpha")
        self.assertTrue(player_ops.player_matches(p, None, "alpha"))
        self.assertFalse(player_ops.player_matches(p, "", "beta"))


class FindPlayerTests(unittest.TestCase):
    def setUp(self):
        self.scan = {"id": "s1", "players": [_player("u1", "alpha"), _player("u2", "beta"), _player("u3", "beta")]}

    def test_finds_by_uid(self):
        self.assertIs(player_ops.find_player(self.scan, "u2", None), self.scan["players"][1])

    def test_finds_by_nick_and_takes_the_first_row(self):
        self.assertIs(player_ops.find_player(self.scan, None, "beta"), self.scan["players"][1])

    def test_returns_none_when_missing(self):
        self.assertIsNone(player_ops.find_player(self.scan, "nope", None))
        self.assertIsNone(player_ops.find_player(self.scan, None, "ghost"))

    def test_scan_without_players_key_gives_none(self):
        self.assertIsNone(player_ops.find_player({"id": "s"}, None, "alpha"))


class ReplacePlayerByUidTests(unittest.TestCase):
    def test_replaces_the_row_with_the_same_uid_and_keeps_others(self):
        a, b, c = _player("u1", "alpha"), _player("u2", "beta"), _player("u3", "gamma")
        scan = {"id": "s1", "players": [a, b, c]}
        new_b = _player("u2", "beta-fixed")
        self.assertTrue(player_ops.replace_player_by_uid(scan, new_b))
        self.assertEqual(scan["players"], [a, new_b, c])
        self.assertIs(scan["players"][0], a)
        self.assertIs(scan["players"][2], c)
        self.assertEqual(b["nick"], "beta")  # the old dict itself is not edited

    def test_returns_false_and_changes_nothing_when_uid_is_absent(self):
        a = _player("u1", "alpha")
        scan = {"id": "s1", "players": [a]}
        before = list(scan["players"])
        self.assertFalse(player_ops.replace_player_by_uid(scan, _player("u9", "zeta")))
        self.assertEqual(scan["players"], before)
        self.assertIs(scan["players"][0], a)

    def test_empty_uid_never_matches(self):
        scan = {"id": "s1", "players": [_player(None, "alpha"), _player("", "beta")]}
        self.assertFalse(player_ops.replace_player_by_uid(scan, _player(None, "alpha2")))
        self.assertFalse(player_ops.replace_player_by_uid(scan, _player("", "beta2")))
        self.assertEqual([p["nick"] for p in scan["players"]], ["alpha", "beta"])


class MarkEditedTests(unittest.TestCase):
    def test_stamps_edited_at_and_returns_same_row(self):
        from unittest import mock
        row = {"nick": "alpha"}
        with mock.patch.object(player_ops.time, "time", return_value=42.5):
            out = player_ops.mark_edited(row)
        self.assertIs(out, row)
        self.assertEqual(row["edited_at"], 42.5)


class SourceTextTests(unittest.TestCase):
    def test_choices_text_for_one_two_and_three_ids(self):
        self.assertEqual(player_ops.source_choices_text(["leetify"]), "'leetify'")
        self.assertEqual(player_ops.source_choices_text(["leetify", "cstracker"]), "'leetify' або 'cstracker'")
        self.assertEqual(player_ops.source_choices_text(["a", "b", "c"]), "'a', 'b' або 'c'")


class RefreshSourceEntryTests(unittest.TestCase):
    def test_updates_only_the_given_source_entry(self):
        leet = FakeSource("leetify")
        reg = FakeRegistry(leet)
        steam = {"leetify": {"x": 1}, "sources": {"leetify": {"old": True}, "other": {"keep": 1}}}
        player_ops.refresh_source_entry(steam, leet, reg)
        self.assertEqual(steam["sources"], {"leetify": {"raw": {"x": 1}}, "other": {"keep": 1}})

    def test_removes_the_entry_when_the_source_has_no_result(self):
        leet = FakeSource("leetify")
        steam = {"sources": {"leetify": {"old": True}, "other": {"keep": 1}}}
        player_ops.refresh_source_entry(steam, leet, FakeRegistry(leet))
        self.assertEqual(steam["sources"], {"other": {"keep": 1}})

    def test_builds_the_view_from_registry_when_sources_is_missing(self):
        leet = FakeSource("leetify")
        steam = {"leetify": {"x": 1}}
        player_ops.refresh_source_entry(steam, leet, FakeRegistry(leet))
        self.assertEqual(steam["sources"], {"leetify": {"raw": {"x": 1}}})


class RecheckSourceTests(unittest.TestCase):
    def setUp(self):
        self.leet = FakeSource("leetify", fetch={"fresh": 7})
        self.registry = FakeRegistry(self.leet, FakeSource("cstracker"))
        self.scan = {
            "id": "scan-1",
            "players": [
                _player("u1", "alpha", steamid64="76561190000000001"),
                _player("u2", "beta"),
            ],
        }
        self.store = FakeStore([self.scan])
        self.saves = []
        self.broadcasts = []

    def _call(self, scan_id="scan-1", nick="alpha", source="leetify", player_uid="u1", store=None, registry=None):
        return player_ops.recheck_source(
            store if store is not None else self.store,
            registry if registry is not None else self.registry,
            scan_id, nick, source, player_uid,
            make_ctx=lambda: SourceContext(steam_api_key="k"),
            save_locked=lambda: self.saves.append(True),
            broadcast=lambda event, payload: self.broadcasts.append((event, payload)),
        )

    def test_unknown_or_non_string_source_is_400_with_choices(self):
        for bad in (None, "nope", 5, ["leetify"]):
            status, body = self._call(source=bad)
            self.assertEqual(status, 400)
            self.assertEqual(body, {"error": "source має бути 'leetify' або 'cstracker'"})
        self.assertEqual(self.leet.fetched_with, [])

    def test_missing_scan_id_or_identity_is_400(self):
        self.assertEqual(self._call(scan_id=None)[0], 400)
        self.assertEqual(self._call(scan_id="scan-1", nick="", player_uid=None)[0], 400)

    def test_nick_only_is_enough_for_the_identity_check(self):
        status, _ = self._call(nick="alpha", player_uid=None)
        self.assertEqual(status, 200)

    def test_missing_scan_is_404(self):
        status, body = self._call(scan_id="nope")
        self.assertEqual((status, body), (404, {"error": "Scan not found"}))

    def test_missing_player_is_404(self):
        status, body = self._call(nick="ghost", player_uid="u9")
        self.assertEqual((status, body), (404, {"error": "Player not found in scan"}))

    def test_player_without_steamid64_is_409(self):
        status, body = self._call(nick="beta", player_uid="u2")
        self.assertEqual(status, 409)
        self.assertIn("SteamID64", body["error"])
        self.assertEqual(self.leet.fetched_with, [])

    def test_disabled_source_is_409(self):
        disabled = FakeSource("leetify", enabled=False)
        status, body = self._call(registry=FakeRegistry(disabled))
        self.assertEqual(status, 409)
        self.assertEqual(body, {"error": "Джерело leetify вимкнене: не задано потрібний ключ"})
        self.assertEqual(self.saves, [])
        self.assertEqual(self.broadcasts, [])

    def test_fetch_exception_is_502_and_nothing_is_saved(self):
        failing = FakeSource("leetify", fetch_error=RuntimeError("timeout"))
        status, body = self._call(registry=FakeRegistry(failing))
        self.assertEqual(status, 502)
        self.assertEqual(body, {"error": "Запит до leetify не вдався: timeout"})
        self.assertEqual(self.saves, [])
        self.assertEqual(self.broadcasts, [])

    def test_success_writes_row_saves_and_broadcasts(self):
        status, body = self._call()
        self.assertEqual(status, 200)
        player = self.scan["players"][0]
        self.assertEqual(player["steam"]["leetify"], {"fresh": 7})
        self.assertEqual(player["steam"]["sources"], {"leetify": {"raw": {"fresh": 7}}})
        self.assertIn("edited_at", player)
        self.assertNotIn("edited_at", self.scan["players"][1])
        self.assertEqual(self.leet.fetched_with, ["76561190000000001"])
        self.assertEqual(self.saves, [True])
        self.assertEqual(len(self.broadcasts), 1)
        event, snapshot = self.broadcasts[0]
        self.assertEqual(event, "scan")
        self.assertIs(body, snapshot)
        self.assertEqual(snapshot["id"], "scan-1")


if __name__ == "__main__":
    unittest.main()
