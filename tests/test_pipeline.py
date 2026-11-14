"""ScanPipeline with fakes: no OCR, no network, no sleeps (spawn runs enrichment synchronously).

Covers run_new / rescan / enrich_scan: record shape, event order, history trimming, duplicate nicks
(player_uid row matching), the AI stage, removal mid-enrichment and the nick fallback for legacy rows.
Run: /home/andrii/fun/.venv/bin/python -m unittest discover -s tests -v
"""

import copy
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import lookup.steam_lookup as steam_lookup  # noqa: E402
from app.events import EventBus  # noqa: E402
from app.pipeline import ScanPipeline  # noqa: E402
from vision.scoreboard_scan import Player  # noqa: E402
from lookup.steam_lookup import SteamProfile  # noqa: E402


class FakeRepo:
    def __init__(self, max_history=50):
        self.lock = threading.Lock()
        self.scans = []
        self.max_history = max_history
        self.saves = 0

    def save_locked(self):
        self.saves += 1


def build_report(p: Player, with_steam: bool = True):
    """Real PlayerReport built from a real Player: no network. Steam rows carry the row's uid as profile name;
    the initial (fetch_steam=False) rows have no profile yet, like the real pre-Steam rows."""
    profile = SteamProfile(query_nick=p.nick, profile_name=f"prof-{p.player_uid}", lookup_source="api") \
        if with_steam else SteamProfile(query_nick=p.nick)
    return steam_lookup._build_player_report(p, profile)


class FakeEnrich:
    """Stands in for steam_lookup.enrich_players. Calls on_player_ready in `order(players)` order."""

    def __init__(self, order=None, before_return=None):
        self.calls = []
        self.order = order
        self.before_return = before_return

    def __call__(self, players, fetch_steam=True, on_player_ready=None):
        self.calls.append(fetch_steam)
        if not fetch_steam:
            return [build_report(p, with_steam=False) for p in players]
        reports = [build_report(p) for p in players]
        seq = self.order(players) if self.order else range(len(players))
        for i in seq:
            on_player_ready(reports[i])
        if self.before_return:
            self.before_return()
        return reports


class FakeAI:
    def __init__(self, error=None):
        self.calls = 0
        self.error = error

    def __call__(self, dicts):
        self.calls += 1
        return [dict(d, ai_marker=True) for d in dicts], self.error


def sync_spawn(fn):
    fn()


def dup_players():
    return [
        Player(nick="dup", kills=10, deaths=5, kd=2.0, hs=40.0, level=10, score=30.0),
        Player(nick="dup", kills=3, deaths=9, kd=0.33, hs=20.0, level=12, score=15.0),
        Player(nick="solo", kills=16, deaths=18, kd=0.89, hs=42.0, level=18, score=28.0),
    ]


class Harness:
    def __init__(self, players_factory=dup_players, *, ai=False, ai_fn=None, enrich=None, max_history=50,
                 spawn=sync_spawn):
        self.repo = FakeRepo(max_history)
        self.bus = EventBus()
        self.events = []
        self.row_snapshots = []  # the stored player rows as they are at each player.enriched event
        self.spawned = 0
        self.bus.subscribe("*", self._record)
        self.enrich = enrich or FakeEnrich()
        self.ai = ai_fn or FakeAI()

        def counting_spawn(fn):
            self.spawned += 1
            spawn(fn)

        self.pipeline = ScanPipeline(
            self.repo, self.bus,
            ocr=lambda path: (players_factory(), None),
            enrich=self.enrich,
            analyze_ai=self.ai,
            ai_enabled=lambda: ai,
            match_meta=lambda now, status: ("match-1", "de_mirage"),
            screenshot_url=lambda path: f"/screenshots/{path.name}",
            spawn=counting_spawn,
        )

    def _record(self, topic, payload):
        self.events.append((topic, copy.deepcopy(payload)))
        if topic == "player.enriched" and self.repo.scans:
            self.row_snapshots.append(copy.deepcopy(self.repo.scans[0]["players"]))

    def topics(self):
        return [t for t, _ in self.events]


IMG = Path("/tmp/cs2_test_shot.png")


class RunNewTests(unittest.TestCase):
    def test_record_shape_at_creation_and_final_state(self):
        h = Harness()
        rec = h.pipeline.run_new(IMG, fetch_steam=True)
        created = next(p for t, p in h.events if t == "scan.created")
        self.assertEqual(len(created["id"]), 10)
        self.assertEqual(created["scan_status"], "loading_steam")
        self.assertEqual(created["progress"], {"done": 0, "total": 3})
        self.assertEqual(created["image_url"], "/screenshots/cs2_test_shot.png")
        self.assertIsNone(created["ai_error"])
        self.assertEqual(created["player_count"], 3)
        for p in created["players"]:
            self.assertTrue(p["loading"])
            self.assertEqual(p["loading_stage"], "steam")
        # final state (spawn is synchronous, so enrichment already ran)
        self.assertIs(h.repo.scans[0], rec)
        self.assertEqual(rec["scan_status"], "done")
        self.assertEqual(rec["progress"], {"done": 3, "total": 3})
        self.assertIsNone(rec["ai_error"])
        for p in rec["players"]:
            self.assertFalse(p["loading"])
            self.assertEqual(p["loading_stage"], "done")

    def test_event_order_created_then_one_enriched_per_player_then_finished(self):
        h = Harness()
        h.pipeline.run_new(IMG, fetch_steam=True)
        self.assertEqual(h.topics(), ["scan.created", "player.enriched", "player.enriched", "player.enriched",
                                      "scan.finished"])
        progress = [p["progress"]["done"] for t, p in h.events if t == "player.enriched"]
        self.assertEqual(progress, [1, 2, 3])

    def test_fetch_steam_false_is_done_without_enrichment(self):
        h = Harness()
        rec = h.pipeline.run_new(IMG, fetch_steam=False)
        self.assertEqual(rec["scan_status"], "done")
        self.assertEqual(rec["progress"], {"done": 3, "total": 3})
        self.assertEqual(h.spawned, 0)
        self.assertEqual(h.enrich.calls, [False])
        self.assertEqual(h.topics(), ["scan.created"])
        self.assertTrue(all(p["loading_stage"] == "done" and not p["loading"] for p in rec["players"]))

    def test_zero_players_is_done_and_starts_no_enrichment(self):
        h = Harness(players_factory=lambda: [])
        rec = h.pipeline.run_new(IMG, fetch_steam=True)
        self.assertEqual(rec["scan_status"], "done")
        self.assertEqual(rec["progress"], {"done": 0, "total": 0})
        self.assertEqual(h.spawned, 0)
        self.assertEqual(h.topics(), ["scan.created"])

    def test_history_is_trimmed_to_max_newest_first(self):
        h = Harness(players_factory=lambda: [], max_history=2)
        ids = [h.pipeline.run_new(IMG, fetch_steam=False)["id"] for _ in range(3)]
        self.assertEqual(len(h.repo.scans), 2)
        self.assertEqual([s["id"] for s in h.repo.scans], [ids[2], ids[1]])

    def test_one_save_per_run_new_plus_one_at_final(self):
        h = Harness()
        h.pipeline.run_new(IMG, fetch_steam=True)
        self.assertEqual(h.repo.saves, 2)


class DuplicateNickTests(unittest.TestCase):
    def test_uids_are_scan_id_and_row_index_and_distinct(self):
        h = Harness()
        rec = h.pipeline.run_new(IMG, fetch_steam=True)
        uids = [p["player_uid"] for p in rec["players"]]
        self.assertEqual(uids, [f"{rec['id']}-0", f"{rec['id']}-1", f"{rec['id']}-2"])
        self.assertEqual(len(set(uids)), 3)

    def test_each_enriched_event_replaces_its_own_row_even_out_of_order(self):
        # The second duplicate finishes first; rows must still keep their own stats.
        h = Harness(enrich=FakeEnrich(order=lambda players: [1, 0, 2]))
        rec = h.pipeline.run_new(IMG, fetch_steam=True)
        enriched_uids = [p["player"]["player_uid"] for t, p in h.events if t == "player.enriched"]
        self.assertEqual(enriched_uids, [f"{rec['id']}-1", f"{rec['id']}-0", f"{rec['id']}-2"])
        by_uid = {p["player_uid"]: p for p in rec["players"]}
        self.assertEqual((by_uid[f"{rec['id']}-0"]["kills"], by_uid[f"{rec['id']}-0"]["deaths"]), (10, 5))
        self.assertEqual((by_uid[f"{rec['id']}-1"]["kills"], by_uid[f"{rec['id']}-1"]["deaths"]), (3, 9))
        self.assertEqual(by_uid[f"{rec['id']}-0"]["steam"]["profile_name"], f"prof-{rec['id']}-0")
        self.assertEqual(by_uid[f"{rec['id']}-1"]["steam"]["profile_name"], f"prof-{rec['id']}-1")

    def test_stored_rows_are_replaced_by_uid_while_loading_not_only_at_the_end(self):
        # The final overwrite hides wrong intermediate writes, so check the rows at each enriched event.
        h = Harness(enrich=FakeEnrich(order=lambda players: [1, 0, 2]))
        rec = h.pipeline.run_new(IMG, fetch_steam=True)
        rid = rec["id"]
        first, second = h.row_snapshots[0], h.row_snapshots[1]
        self.assertEqual(first[0]["kills"], 10)                       # row 0 untouched by the uid-1 event
        self.assertEqual(first[0]["steam"]["profile_name"], None)     # still the initial (steam-less) row
        self.assertEqual(first[1]["steam"]["profile_name"], f"prof-{rid}-1")
        self.assertEqual(second[0]["steam"]["profile_name"], f"prof-{rid}-0")

    def test_uid_is_stable_through_enrichment(self):
        h = Harness()
        rec = h.pipeline.run_new(IMG, fetch_steam=True)
        created = next(p for t, p in h.events if t == "scan.created")
        self.assertEqual([p["player_uid"] for p in created["players"]], [p["player_uid"] for p in rec["players"]])
        finished = next(p for t, p in h.events if t == "scan.finished")
        self.assertEqual([p["player_uid"] for p in finished["players"]], [p["player_uid"] for p in rec["players"]])


class AiPathTests(unittest.TestCase):
    def test_ai_stage_events_and_loading_stage_values(self):
        h = Harness(ai=True)
        rec = h.pipeline.run_new(IMG, fetch_steam=True)
        self.assertEqual(h.topics(), ["scan.created", "player.enriched", "player.enriched", "player.enriched",
                                      "scan.updated", "scan.finished"])
        enriched = [p["player"] for t, p in h.events if t == "player.enriched"]
        self.assertTrue(all(p["loading_stage"] == "ai" for p in enriched))
        updated = next(p for t, p in h.events if t == "scan.updated")
        self.assertEqual(updated["scan_status"], "loading_ai")
        self.assertEqual(h.ai.calls, 1)
        self.assertEqual(rec["scan_status"], "done")
        self.assertTrue(all(p["loading_stage"] == "done" and p["ai_marker"] for p in rec["players"]))

    def test_ai_error_is_propagated_to_the_final_record(self):
        h = Harness(ai=True, ai_fn=FakeAI(error="rate limited"))
        rec = h.pipeline.run_new(IMG, fetch_steam=True)
        self.assertEqual(rec["ai_error"], "rate limited")
        self.assertEqual(rec["scan_status"], "done")
        finished = next(p for t, p in h.events if t == "scan.finished")
        self.assertEqual(finished["ai_error"], "rate limited")

    def test_ai_disabled_never_calls_the_analyzer(self):
        h = Harness(ai=False)
        h.pipeline.run_new(IMG, fetch_steam=True)
        self.assertEqual(h.ai.calls, 0)
        self.assertNotIn("scan.updated", h.topics())


class RescanTests(unittest.TestCase):
    def _first_scan(self, h):
        return h.pipeline.run_new(IMG, fetch_steam=False)

    def test_same_record_object_is_updated_in_place(self):
        h = Harness()
        rec = self._first_scan(h)
        scan_id = rec["id"]
        h.events.clear()
        snapshot = h.pipeline.rescan(rec, IMG)
        self.assertIs(h.repo.scans[0], rec)
        self.assertEqual(rec["id"], scan_id)
        self.assertEqual(snapshot["id"], scan_id)
        self.assertEqual(len(h.repo.scans), 1)

    def test_players_replaced_and_scan_created_event_carries_snapshot(self):
        h = Harness()
        rec = self._first_scan(h)
        self.assertEqual(rec["player_count"], 3)
        h.events.clear()
        snapshot = h.pipeline.rescan(rec, IMG)
        self.assertEqual(h.topics()[0], "scan.created")
        created = h.events[0][1]
        self.assertEqual(created["id"], rec["id"])
        self.assertEqual(len(created["players"]), 3)
        self.assertEqual(snapshot["scan_status"], "loading_steam")  # snapshot is taken before enrichment

    def test_enrichment_runs_only_when_players_exist(self):
        h = Harness()
        rec = self._first_scan(h)
        h.pipeline.rescan(rec, IMG)
        self.assertEqual(h.spawned, 1)
        h2 = Harness(players_factory=lambda: [])
        rec2 = h2.pipeline.run_new(IMG, fetch_steam=False)
        h2.pipeline.rescan(rec2, IMG)
        self.assertEqual(h2.spawned, 0)
        self.assertEqual(rec2["scan_status"], "done")

    def test_old_ai_error_is_cleared(self):
        h = Harness()
        rec = self._first_scan(h)
        rec["ai_error"] = "stale error"
        h.pipeline.rescan(rec, IMG)
        self.assertIsNone(rec["ai_error"])


class RemovalAndLegacyTests(unittest.TestCase):
    def test_scan_removed_mid_enrichment_returns_quietly_without_finished(self):
        repo_holder = {}
        enrich = FakeEnrich(before_return=lambda: repo_holder["repo"].scans.clear())
        h = Harness(enrich=enrich)
        repo_holder["repo"] = h.repo
        rec = h.pipeline.run_new(IMG, fetch_steam=True)  # must not raise
        self.assertNotIn("scan.finished", h.topics())
        self.assertEqual(h.repo.scans, [])
        self.assertEqual(rec["scan_status"], "loading_steam")  # the detached record is never finalised

    def test_enrich_scan_falls_back_to_nick_for_rows_without_uid(self):
        h = Harness()
        legacy_players = [Player(nick="alpha", kills=5, deaths=5, kd=1.0, hs=30.0, level=3, score=10.0),
                          Player(nick="beta", kills=7, deaths=2, kd=3.5, hs=60.0, level=9, score=50.0)]
        record = {"id": "legacy1", "players": [{"nick": "alpha", "kills": 0}, {"nick": "beta", "kills": 0}],
                  "player_count": 2, "ai_error": None, "scan_status": "loading_steam",
                  "progress": {"done": 0, "total": 2}}
        h.repo.scans.append(record)
        h.pipeline.enrich_scan("legacy1", legacy_players, 0.0)
        self.assertEqual([p["nick"] for p in record["players"]], ["alpha", "beta"])
        self.assertEqual([p["kills"] for p in record["players"]], [5, 7])
        self.assertEqual(record["scan_status"], "done")


class FailingEnrich(FakeEnrich):
    """Steam pass raises `error` after `ready_before_fail` on_player_ready calls. The initial pass (fetch_steam=False) works."""

    def __init__(self, error, ready_before_fail=0):
        super().__init__()
        self.error = error
        self.ready_before_fail = ready_before_fail

    def __call__(self, players, fetch_steam=True, on_player_ready=None):
        if not fetch_steam:
            return super().__call__(players, fetch_steam=False)
        reports = [build_report(p) for p in players]
        for i in range(self.ready_before_fail):
            on_player_ready(reports[i])
        raise self.error


class RaisingAI:
    def __init__(self, error):
        self.error = error

    def __call__(self, dicts):
        raise self.error


class EnrichmentFailureTests(unittest.TestCase):
    def test_enrich_raising_at_once_still_finishes_the_scan(self):
        h = Harness(enrich=FailingEnrich(RuntimeError("boom")))
        with self.assertLogs("app.pipeline", level="ERROR"):
            rec = h.pipeline.run_new(IMG, fetch_steam=True)
        self.assertEqual(rec["scan_status"], "done")
        self.assertEqual(rec["enrich_error"], "RuntimeError: boom")
        self.assertIsNone(rec["ai_error"])
        for row in rec["players"]:
            self.assertIs(row["loading"], False)
            self.assertEqual(row["loading_stage"], "done")
        self.assertEqual(h.topics().count("scan.finished"), 1)
        self.assertEqual(h.repo.saves, 2)  # once at creation, once when the failure is recorded

    def test_enrich_failing_after_first_player_keeps_enriched_row(self):
        h = Harness(enrich=FailingEnrich(RuntimeError("boom"), ready_before_fail=1))
        with self.assertLogs("app.pipeline", level="ERROR"):
            rec = h.pipeline.run_new(IMG, fetch_steam=True)
        first, rest = rec["players"][0], rec["players"][1:]
        self.assertIs(first["loading"], False)
        self.assertEqual(first["steam"]["profile_name"], f"prof-{first['player_uid']}")  # enriched by on_player_ready
        for row in rest:
            self.assertIs(row["loading"], False)
            self.assertEqual(row["loading_stage"], "done")
        self.assertEqual(rec["progress"], {"done": 1, "total": 3})
        self.assertEqual(rec["enrich_error"], "RuntimeError: boom")
        self.assertEqual(rec["scan_status"], "done")

    def test_ai_raising_keeps_enrichment_and_sets_ai_error(self):
        h = Harness(ai=True, ai_fn=RaisingAI(RuntimeError("ai exploded")))
        rec = h.pipeline.run_new(IMG, fetch_steam=True)
        self.assertEqual(rec["scan_status"], "done")
        self.assertEqual(rec["ai_error"], "RuntimeError: ai exploded")
        self.assertIsNone(rec["enrich_error"])
        self.assertEqual(rec["progress"], {"done": 3, "total": 3})
        for row in rec["players"]:
            self.assertIs(row["loading"], False)
            self.assertEqual(row["loading_stage"], "done")
            self.assertNotIn("ai_marker", row)  # AI did not run, so the rows are the enriched ones
            self.assertIn("steam", row)
        self.assertEqual(h.topics().count("scan.finished"), 1)

    def test_success_path_has_no_enrich_error(self):
        h = Harness()
        rec = h.pipeline.run_new(IMG, fetch_steam=True)
        self.assertEqual(rec["scan_status"], "done")
        self.assertIsNone(rec["enrich_error"])


class RunGenerationTests(unittest.TestCase):
    """A rescan keeps the scan id (and so the player uids), so a deferred enrichment of an older run must not
    write into the newer run that now owns the same record."""

    TOPICS = ("scan.created", "scan.updated", "player.enriched", "scan.finished")

    def _harness(self, players_factory=dup_players, ai=False, ai_fn=None):
        deferred = []
        h = Harness(players_factory=players_factory, ai=ai, ai_fn=ai_fn, spawn=deferred.append)
        published = []
        for topic in self.TOPICS:
            h.bus.subscribe(topic, lambda t, payload: published.append((t, payload)))
        return h, deferred, published

    def test_run_new_and_rescan_assign_distinct_run_ids(self):
        h, _, _ = self._harness()
        rec = h.pipeline.run_new(IMG, fetch_steam=True)
        first = rec["run_id"]
        self.assertEqual(len(first), 8)
        h.pipeline.rescan(rec, IMG)
        self.assertEqual(len(rec["run_id"]), 8)
        self.assertNotEqual(rec["run_id"], first)

    def test_stale_enrichment_publishes_nothing_and_does_not_save(self):
        h, deferred, published = self._harness()
        rec = h.pipeline.run_new(IMG, fetch_steam=True)
        h.pipeline.rescan(rec, IMG)
        published.clear()
        saves_before = h.repo.saves
        deferred[0]()                                  # run 1 finishes late
        self.assertEqual(published, [])
        self.assertEqual(h.repo.saves, saves_before)
        self.assertEqual(rec["scan_status"], "loading_steam")
        self.assertTrue(all(p["loading"] for p in rec["players"]))

    def test_new_run_still_completes_after_the_stale_one_was_skipped(self):
        runs = iter([
            [Player(nick="dup", kills=10, deaths=5, kd=2.0, hs=40.0, level=10, score=30.0)],
            [Player(nick="dup", kills=99, deaths=1, kd=9.0, hs=40.0, level=10, score=30.0)],
        ])
        h, deferred, published = self._harness(players_factory=lambda: next(runs), ai=True)
        rec = h.pipeline.run_new(IMG, fetch_steam=True)
        h.pipeline.rescan(rec, IMG)
        deferred[0]()                                  # stale run: no AI spend, no writes
        self.assertEqual(h.ai.calls, 0)
        deferred[1]()                                  # current run
        self.assertEqual(rec["scan_status"], "done")
        self.assertEqual(rec["progress"], {"done": 1, "total": 1})
        self.assertEqual([p["kills"] for p in rec["players"]], [99])
        self.assertEqual(h.ai.calls, 1)
        finished = [p for t, p in published if t == "scan.finished"]
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0]["players"][0]["kills"], 99)

    def test_enrich_scan_with_run_id_none_writes_to_the_record(self):
        h, _, _ = self._harness()
        record = {"id": "legacy2", "run_id": "abcdef12",
                  "players": [{"nick": "alpha", "kills": 0}], "player_count": 1, "ai_error": None,
                  "scan_status": "loading_steam", "progress": {"done": 0, "total": 1}}
        h.repo.scans.append(record)
        alpha = [Player(nick="alpha", kills=5, deaths=5, kd=1.0, hs=30.0, level=3, score=10.0)]
        h.pipeline.enrich_scan("legacy2", alpha, 0.0, None)
        self.assertEqual(record["scan_status"], "done")
        self.assertEqual(record["players"][0]["kills"], 5)


if __name__ == "__main__":
    unittest.main()
