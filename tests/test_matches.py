"""Grouping of scans into matches (app.matches.build_matches). Synthetic records, no server."""
import unittest

from app.matches import build_matches


def _scan(sid, created_at, players=(), match_id=None, map_name=None, date=None):
    rec = {
        "id": sid,
        "created_at": created_at,
        "player_count": len(players),
        "image_name": f"{sid}.png",
        "players": [{"cheat_probability": p} for p in players],
    }
    if match_id is not None:
        rec["match_id"] = match_id
    if map_name is not None:
        rec["map_name"] = map_name
    if date is not None:
        rec["date"] = date
    return rec


class BuildMatchesTests(unittest.TestCase):
    def test_empty_input_gives_no_matches(self):
        self.assertEqual(build_matches([]), [])

    def test_scans_are_grouped_by_match_id_in_first_seen_order(self):
        # newest first, as ScanStore keeps them
        scans = [
            _scan("s3", "2026-01-02T10:30:00", match_id="m2"),
            _scan("s2", "2026-01-01T12:10:00", match_id="m1"),
            _scan("s1", "2026-01-01T12:00:00", match_id="m1"),
        ]
        matches = build_matches(scans)
        self.assertEqual([m["match_id"] for m in matches], ["m2", "m1"])
        m1 = matches[1]
        self.assertEqual([s["id"] for s in m1["scans"]], ["s2", "s1"])
        self.assertEqual(m1["scan_count"], 2)

    def test_missing_match_id_falls_back_to_session_date(self):
        scans = [
            _scan("a", "2026-03-05T20:00:00"),
            _scan("b", "2026-03-05T19:00:00"),
            _scan("c", "2026-03-04T23:00:00"),
        ]
        matches = build_matches(scans)
        self.assertEqual([m["match_id"] for m in matches], ["session_2026-03-05", "session_2026-03-04"])
        self.assertEqual(matches[0]["scan_count"], 2)

    def test_duration_is_newest_minus_oldest_in_whole_seconds(self):
        scans = [
            _scan("n", "2026-01-01T12:10:30.400", match_id="m"),
            _scan("o", "2026-01-01T12:00:00", match_id="m"),
        ]
        m = build_matches(scans)[0]
        self.assertEqual(m["duration_sec"], 630)
        self.assertEqual(m["started_at"], "2026-01-01T12:00:00")
        self.assertEqual(m["ended_at"], "2026-01-01T12:10:30.400")

    def test_unparseable_timestamps_give_zero_duration(self):
        scans = [_scan("n", "not-a-date", match_id="m"), _scan("o", "also-bad", match_id="m")]
        self.assertEqual(build_matches(scans)[0]["duration_sec"], 0)

    def test_max_risk_is_the_highest_probability_rounded_to_one_digit(self):
        scans = [
            _scan("n", "2026-01-01T12:10:00", players=[0.12, 0.31], match_id="m"),
            _scan("o", "2026-01-01T12:00:00", players=[0.87654], match_id="m"),
        ]
        self.assertEqual(build_matches(scans)[0]["max_risk"], 0.9)

    def test_match_without_players_has_zero_max_risk(self):
        m = build_matches([_scan("n", "2026-01-01T12:00:00", match_id="m")])[0]
        self.assertEqual(m["max_risk"], 0.0)

    def test_map_name_is_the_first_non_empty_one(self):
        scans = [
            _scan("n", "2026-01-01T12:10:00", match_id="m"),
            _scan("mid", "2026-01-01T12:05:00", match_id="m", map_name="de_mirage"),
            _scan("o", "2026-01-01T12:00:00", match_id="m", map_name="de_inferno"),
        ]
        self.assertEqual(build_matches(scans)[0]["map_name"], "de_mirage")

    def test_map_name_is_none_when_no_scan_has_one(self):
        self.assertIsNone(build_matches([_scan("n", "2026-01-01T12:00:00", match_id="m")])[0]["map_name"])

    def test_date_comes_from_newest_scan_or_created_at(self):
        scans = [
            _scan("n", "2026-01-02T01:00:00", match_id="m", date="2026-01-01"),
            _scan("o", "2026-01-01T23:00:00", match_id="m"),
        ]
        self.assertEqual(build_matches(scans)[0]["date"], "2026-01-01")
        self.assertEqual(build_matches([_scan("x", "2026-05-06T07:00:00", match_id="k")])[0]["date"], "2026-05-06")

    def test_scan_summary_fields_are_exactly_the_four_listed(self):
        m = build_matches([_scan("n", "2026-01-01T12:00:00", players=[0.5], match_id="m")])[0]
        self.assertEqual(m["scans"], [{
            "id": "n",
            "created_at": "2026-01-01T12:00:00",
            "player_count": 1,
            "image_name": "n.png",
        }])

    def test_input_list_is_not_modified(self):
        scans = [_scan("n", "2026-01-01T12:00:00", match_id="m")]
        before = [dict(s) for s in scans]
        build_matches(scans)
        self.assertEqual(scans, before)


if __name__ == "__main__":
    unittest.main()
