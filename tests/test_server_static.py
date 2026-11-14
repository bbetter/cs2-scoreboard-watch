"""Regression test: static file routes must return exactly one response on a keep-alive connection."""

import http.client
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.server as server  # noqa: E402


class StaticRoutesKeepAliveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Port 0 -> OS picks a free ephemeral port.
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.CsWatchHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)

    def test_app_js_two_sequential_requests_on_one_connection(self):
        expected = (server.WEB_DIR / "app.js").read_bytes()
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            for attempt in range(2):
                conn.request("GET", "/app.js")
                resp = conn.getresponse()
                body = resp.read()
                self.assertEqual(resp.status, 200, f"request {attempt + 1}: status {resp.status}")
                self.assertEqual(
                    resp.getheader("Content-Type"), "application/javascript; charset=utf-8"
                )
                self.assertEqual(body, expected, f"request {attempt + 1}: body mismatch")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
