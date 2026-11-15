"""GET /api/sources: the registry as JSON, served by the real HTTP handler on a local port."""

from __future__ import annotations

import http.client
import json
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.server as server  # noqa: E402
from sources import REGISTRY  # noqa: E402


class SourcesEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.CsWatchHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        conn = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=5)
        try:
            conn.request("GET", "/api/sources")
            resp = conn.getresponse()
            cls.status = resp.status
            cls.body = json.loads(resp.read())
        finally:
            conn.close()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)

    def test_returns_200(self):
        self.assertEqual(self.status, 200)

    def test_ids_match_registry_order(self):
        self.assertEqual([s["id"] for s in self.body["sources"]], REGISTRY.ids())

    def test_each_entry_has_the_four_metadata_fields(self):
        for entry in self.body["sources"]:
            with self.subTest(source=entry["id"]):
                self.assertEqual(set(entry), {"id", "label", "recheckable", "in_coverage"})

    def test_recheckable_flags_for_builtin_sources(self):
        by_id = {s["id"]: s for s in self.body["sources"]}
        self.assertIs(by_id["leetify"]["recheckable"], True)
        self.assertIs(by_id["cstracker"]["recheckable"], True)


if __name__ == "__main__":
    unittest.main()
