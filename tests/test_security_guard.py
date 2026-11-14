"""Local server guard: Host / Origin / Sec-Fetch-Site / Content-Type checks against CSRF and DNS rebinding."""

import http.client
import json
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.server as server  # noqa: E402
from app.web_router import check_request  # noqa: E402

LOCAL = {"Host": "127.0.0.1:8765"}


def _h(**extra):
    headers = {"Host": "127.0.0.1:8765"}
    headers.update(extra)
    return headers


class CheckRequestHostTests(unittest.TestCase):
    def test_accepted_hosts(self):
        for host in ("127.0.0.1:8765", "localhost:8765", "LOCALHOST", "[::1]:8765", "192.168.1.10:8765", "10.0.0.5"):
            with self.subTest(host=host):
                self.assertIsNone(check_request("GET", "/api/config", {"Host": host}, 0))

    def test_rejected_host_names(self):
        self.assertEqual(check_request("GET", "/", {"Host": "evil.example:8765"}, 0), (403, "Host not allowed"))

    def test_missing_host_rejected(self):
        self.assertEqual(check_request("GET", "/", {}, 0), (403, "Host not allowed"))
        self.assertEqual(check_request("POST", "/", {}, 0), (403, "Host not allowed"))

    def test_host_checked_for_get_with_foreign_origin(self):
        # GET is not subject to the Origin rule, but Host still applies to it.
        self.assertIsNone(check_request("GET", "/", _h(Origin="http://evil.example"), 0))


class CheckRequestOriginTests(unittest.TestCase):
    def test_same_origin_post_ok(self):
        self.assertIsNone(check_request("POST", "/api/tab-pressed", _h(Origin="http://127.0.0.1:8765"), 0))

    def test_foreign_origin_post_rejected(self):
        self.assertEqual(
            check_request("POST", "/api/tab-pressed", _h(Origin="http://evil.example"), 0),
            (403, "Origin not allowed"),
        )

    def test_null_origin_rejected(self):
        self.assertEqual(check_request("POST", "/api/tab-pressed", _h(Origin="null"), 0), (403, "Origin not allowed"))

    def test_https_origin_with_same_host_rejected(self):
        self.assertEqual(
            check_request("POST", "/api/tab-pressed", _h(Origin="https://127.0.0.1:8765"), 0),
            (403, "Origin not allowed"),
        )

    def test_missing_origin_allowed(self):
        self.assertIsNone(check_request("POST", "/api/tab-pressed", _h(), 0))

    def test_foreign_origin_ignored_for_get(self):
        self.assertIsNone(check_request("GET", "/api/config", _h(Origin="http://evil.example"), 0))


class CheckRequestFetchSiteTests(unittest.TestCase):
    def test_cross_site_rejected(self):
        self.assertEqual(
            check_request("POST", "/api/tab-pressed", _h(**{"Sec-Fetch-Site": "cross-site"}), 0),
            (403, "Cross-site request blocked"),
        )

    def test_same_origin_and_none_allowed(self):
        for value in ("same-origin", "none"):
            with self.subTest(value=value):
                self.assertIsNone(check_request("POST", "/api/tab-pressed", _h(**{"Sec-Fetch-Site": value}), 0))


class CheckRequestContentTypeTests(unittest.TestCase):
    def test_text_plain_json_body_rejected(self):
        self.assertEqual(
            check_request("POST", "/api/config", _h(**{"Content-Type": "text/plain"}), 10),
            (415, "Unsupported Content-Type"),
        )

    def test_json_with_charset_allowed(self):
        self.assertIsNone(
            check_request("POST", "/api/config", _h(**{"Content-Type": "application/json; charset=utf-8"}), 10)
        )

    def test_post_without_body_and_without_content_type_allowed(self):
        self.assertIsNone(check_request("POST", "/api/tab-pressed", _h(), 0))
        self.assertIsNone(check_request("POST", "/api/setup-cs2", _h(), 0))

    def test_upload_accepts_images_and_octet_stream(self):
        for ctype in ("image/png", "image/jpeg", "application/octet-stream"):
            with self.subTest(ctype=ctype):
                self.assertIsNone(check_request("POST", "/api/upload", _h(**{"Content-Type": ctype}), 100))

    def test_upload_rejects_text_plain(self):
        self.assertEqual(
            check_request("POST", "/api/upload", _h(**{"Content-Type": "text/plain"}), 100),
            (415, "Unsupported Content-Type"),
        )

    def test_body_without_content_type_rejected(self):
        self.assertEqual(check_request("POST", "/api/scan", _h(), 5), (415, "Unsupported Content-Type"))


class CheckRequestServerIntegrationTests(unittest.TestCase):
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

    def _request(self, method, path, headers=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    def test_get_with_foreign_host_is_forbidden_with_json_error(self):
        status, body = self._request("GET", "/api/config/schema", headers={"Host": "evil.example"})
        self.assertEqual(status, 403)
        self.assertIn("error", json.loads(body))

    def test_get_with_normal_host_is_served(self):
        status, _ = self._request("GET", "/api/config/schema")
        self.assertEqual(status, 200)

    def test_bodyless_tab_pressed_without_headers_is_not_blocked(self):
        with mock.patch.object(server, "schedule_tab_scan", return_value={"status": "ignored"}) as scan:
            status, body = self._request("POST", "/api/tab-pressed")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"status": "ignored"})
        scan.assert_called_once()

    def test_config_post_with_text_plain_is_415_and_never_saves(self):
        with mock.patch.object(server, "save_config") as save:
            status, _ = self._request(
                "POST", "/api/config", headers={"Content-Type": "text/plain"}, body="{}"
            )
        self.assertEqual(status, 415)
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
