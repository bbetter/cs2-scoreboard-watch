"""lookup/steam_search.py: no dependency on lookup.steam_lookup, XML parsing, candidate scoring and the status shape."""

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import lookup.steam_search as steam_search  # noqa: E402

XML_PROFILE = b"""<?xml version="1.0" encoding="UTF-8"?>
<profile>
  <steamID64>76561198000000001</steamID64>
  <steamID><![CDATA[Nick]]></steamID>
  <privacyState>public</privacyState>
  <onlineState>in-game</onlineState>
  <stateMessage>Playing Counter-Strike 2</stateMessage>
  <vacBanned>0</vacBanned>
  <tradeBanState>None</tradeBanState>
  <isLimitedAccount>0</isLimitedAccount>
  <avatarMedium>https://example.invalid/avatar.jpg</avatarMedium>
  <mostPlayedGames>
    <mostPlayedGame>
      <gameName>Counter-Strike 2</gameName>
      <hoursOnRecord>1,234.5</hoursOnRecord>
    </mostPlayedGame>
  </mostPlayedGames>
</profile>
"""


class SteamSearchModuleTests(unittest.TestCase):
    def test_importing_search_module_does_not_load_steam_lookup(self):
        code = "import sys; import lookup.steam_search; print('lookup.steam_lookup' in sys.modules)"
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "False")

    def test_parse_steam_xml_reads_profile_fields(self):
        parsed = steam_search._parse_steam_xml(XML_PROFILE, source="xml")
        self.assertEqual(parsed["steamid64"], "76561198000000001")
        self.assertEqual(parsed["profile_name"], "Nick")
        self.assertEqual(parsed["privacy_state"], "public")
        self.assertTrue(parsed["in_cs2"])
        self.assertFalse(parsed["vac_banned"])
        self.assertIsNone(parsed["trade_ban"])
        self.assertEqual(parsed["cs2_hours"], 1234.5)
        self.assertEqual(parsed["lookup_source"], "xml")
        self.assertEqual(parsed["profile_url"], "https://steamcommunity.com/profiles/76561198000000001")

    def test_parse_steam_xml_error_document_is_none(self):
        self.assertIsNone(steam_search._parse_steam_xml(b"<profile><error>private</error></profile>"))
        self.assertIsNone(steam_search._parse_steam_xml(b"not xml"))

    def test_score_candidate_identical_nick_beats_similar_nick(self):
        exact = steam_search._score_candidate({"profile_name": "Nick"}, "Nick")
        similar = steam_search._score_candidate({"profile_name": "Nick2"}, "Nick")
        self.assertGreater(exact, similar)
        self.assertGreater(similar, 0.0)

    def test_status_keeps_its_shape(self):
        status = steam_search.get_steam_search_status()
        self.assertEqual(set(status), {"limited", "retry_in_sec"})
        self.assertIsInstance(status["limited"], bool)


if __name__ == "__main__":
    unittest.main()
