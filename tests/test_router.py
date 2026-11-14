"""Tests for web_router (route matching, JSON body helper, static resolution) and the live handler.

The live-server tests bind an ephemeral port on 127.0.0.1 and only send read-only or
invalid-body requests, so no scan, config or player data is changed.
Run: /home/andrii/fun/.venv/bin/python -m unittest discover -s tests -v
"""

import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402
import web_router  # noqa: E402
from web_router import JsonBodyError, Router, compile_pattern, read_json_body, resolve_static_file  # noqa: E402


class FakeHandler:
    def __init__(self, body: bytes = b""):
        self.body = body
        self.sent: list[tuple[object, int]] = []

    def _read_body(self, max_bytes: int = 25 * 1024 * 1024) -> bytes:
        if len(self.body) > max_bytes:
            raise ValueError("Payload too large")
        return self.body

    def _send_json(self, data, status: int = 200) -> None:
        self.sent.append((data, status))


class PatternTests(unittest.TestCase):
    def setUp(self):
        self.r = Router()
        self.calls = []

        @self.r.route("GET", "/exact")
        def exact(h):
            self.calls.append(("exact", {}))

        @self.r.route("GET", "/api/scan/{scan_id:path}")
        def scan(h, scan_id):
            self.calls.append(("scan", {"scan_id": scan_id}))

        @self.r.route("GET", "/api/item/{item_id}")
        def item(h, item_id):
            self.calls.append(("item", {"item_id": item_id}))

        @self.r.route("POST", "/exact")
        def post_exact(h):
            self.calls.append(("post_exact", {}))

        self.r.fallback("GET")(lambda h, path: self.calls.append(("fallback", {"path": path})) or True)

    def test_exact_match_and_method_separation(self):
        self.assertTrue(self.r.dispatch(None, "GET", "/exact"))
        self.assertTrue(self.r.dispatch(None, "POST", "/exact"))
        self.assertEqual([c[0] for c in self.calls], ["exact", "post_exact"])

    def test_path_param_takes_rest_including_slashes_and_empty(self):
        self.r.dispatch(None, "GET", "/api/scan/a/b%20c")
        self.r.dispatch(None, "GET", "/api/scan/")
        self.assertEqual(self.calls[0], ("scan", {"scan_id": "a/b%20c"}))
        self.assertEqual(self.calls[1], ("scan", {"scan_id": ""}))

    def test_single_segment_param_rejects_empty_and_slashes(self):
        self.r.dispatch(None, "GET", "/api/item/")  # empty segment: no item route, so the fallback runs
        self.assertEqual(self.calls[-1][0], "fallback")
        self.r.dispatch(None, "GET", "/api/item/x%2Fy")
        self.assertEqual(self.calls[-1], ("item", {"item_id": "x%2Fy"}))

    def test_literal_text_is_escaped(self):
        rx = compile_pattern("/a.b/{x}")
        self.assertIsNotNone(rx.fullmatch("/a.b/1"))
        self.assertIsNone(rx.fullmatch("/aXb/1"))

    def test_unmatched_request_without_fallback_returns_false(self):
        bare = Router()
        bare.route("GET", "/only")(lambda h: None)
        self.assertFalse(bare.dispatch(None, "GET", "/other"))
        self.assertFalse(bare.dispatch(None, "POST", "/only"))

    def test_pattern_must_start_with_slash(self):
        with self.assertRaises(ValueError):
            compile_pattern("api")

    def test_fallback_only_runs_when_no_route_matches(self):
        self.r.dispatch(None, "GET", "/exact")
        self.assertEqual(self.calls, [("exact", {})])

    def test_first_registered_route_wins(self):
        r = Router()
        seen = []
        r.route("GET", "/x/{a}")(lambda h, a: seen.append(("first", a)))
        r.route("GET", "/x/{b}")(lambda h, b: seen.append(("second", b)))
        r.dispatch(None, "GET", "/x/1")
        self.assertEqual(seen, [("first", "1")])


class ReadJsonBodyTests(unittest.TestCase):
    def test_empty_body_uses_factory(self):
        self.assertEqual(read_json_body(FakeHandler(b"")), {})
        self.assertIsNone(read_json_body(FakeHandler(b""), empty_json=lambda: None))

    def test_valid_json_parsed(self):
        self.assertEqual(read_json_body(FakeHandler(b'{"a": 1}')), {"a": 1})

    def test_invalid_json_raises_wrapped_error_with_parser_message(self):
        with self.assertRaises(JsonBodyError) as ctx:
            read_json_body(FakeHandler(b"{bad"))
        self.assertIn("Expecting", str(ctx.exception))

    def test_bad_utf8_is_also_a_json_body_error(self):
        with self.assertRaises(JsonBodyError):
            read_json_body(FakeHandler(b"\xff\xfe"))

    def test_payload_too_large_is_not_wrapped(self):
        with self.assertRaises(ValueError) as ctx:
            read_json_body(FakeHandler(b"x" * 10), max_bytes=5)
        self.assertNotIsInstance(ctx.exception, JsonBodyError)
        self.assertEqual(str(ctx.exception), "Payload too large")


class StaticResolutionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "web"
        (self.root / "sub").mkdir(parents=True)
        (self.root / "index.html").write_text("<html>", encoding="utf-8")
        (self.root / "app.js").write_text("1", encoding="utf-8")
        (self.root / "sub" / "x.css").write_text("a{}", encoding="utf-8")
        (self.root / ".hidden.js").write_text("secret", encoding="utf-8")
        (self.root / "data.txt").write_text("no", encoding="utf-8")
        self.outside = Path(self._tmp.name) / "outside.js"
        self.outside.write_text("outside", encoding="utf-8")

    def test_root_maps_to_index(self):
        self.assertEqual(resolve_static_file(self.root, "/"), self.root / "index.html")

    def test_nested_and_percent_encoded_files_resolve(self):
        self.assertEqual(resolve_static_file(self.root, "/sub/x.css"), self.root / "sub" / "x.css")
        self.assertEqual(resolve_static_file(self.root, "/app%2Ejs"), self.root / "app.js")

    def test_dotfiles_disallowed_extensions_and_missing_files_are_refused(self):
        for p in ["/.hidden.js", "/data.txt", "/missing.js", "/sub", "/sub/", "/app.js/", "/app.js?x"]:
            with self.subTest(path=p):
                self.assertIsNone(resolve_static_file(self.root, p))

    def test_traversal_forms_are_refused(self):
        for p in ["/../outside.js", "/%2e%2e/outside.js", "/..%2foutside.js", "//etc/passwd",
                  "/sub/../app.js", "/./app.js", "/%2e%2e%2foutside.js", "/app.js%00.txt", "/sub\\..\\app.js"]:
            with self.subTest(path=p):
                self.assertIsNone(resolve_static_file(self.root, p))

    def test_symlink_escaping_root_is_refused(self):
        link = self.root / "link.js"
        try:
            os.symlink(self.outside, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported here")
        self.assertIsNone(resolve_static_file(self.root, "/link.js"))

    def test_symlink_inside_root_is_allowed(self):
        link = self.root / "alias.js"
        try:
            os.symlink(self.root / "app.js", link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported here")
        self.assertEqual(resolve_static_file(self.root, "/alias.js"), (self.root / "app.js").resolve())

    def test_content_types(self):
        self.assertEqual(web_router.static_content_type(Path("a.js")), "application/javascript; charset=utf-8")
        self.assertEqual(web_router.static_content_type(Path("a.mjs")), "application/javascript; charset=utf-8")
        self.assertEqual(web_router.static_content_type(Path("a.css")), "text/css; charset=utf-8")
        self.assertEqual(web_router.static_content_type(Path("a.html")), "text/html; charset=utf-8")
        self.assertEqual(web_router.static_content_type(Path("a.png")), "image/png")
        self.assertEqual(web_router.static_content_type(Path("a.woff2")), "font/woff2")


class RouteTableTests(unittest.TestCase):
    EXPECTED = {
        ("GET", "/api/steam-status"), ("GET", "/api/latest"), ("GET", "/api/config"),
        ("GET", "/api/matches"), ("GET", "/api/config/schema"), ("GET", "/api/scan/{scan_id:path}"),
        ("GET", "/api/players/{steamid:path}"), ("GET", "/screenshots/{name:path}"), ("GET", "/api/events"),
        ("POST", "/api/tab-pressed"), ("POST", "/api/scan"), ("POST", "/api/rescan"),
        ("POST", "/api/setup-cs2"), ("POST", "/api/recheck"), ("POST", "/api/feedback"),
        ("POST", "/api/recheck-source"), ("POST", "/api/ai-analyze"), ("POST", "/api/config"),
        ("POST", "/api/upload"), ("POST", "/api/players/{steamid:path}"),
    }

    def test_route_table_is_frozen(self):
        self.assertEqual(set(server.router.routes()), self.EXPECTED)

    def test_no_route_is_registered_twice(self):
        routes = server.router.routes()
        self.assertEqual(len(routes), len(set(routes)))


class LiveServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.CsWatchHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)

    def _request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, body=body, headers={"Content-Type": "application/json"} if body else {})
            resp = conn.getresponse()
            return resp.status, resp.getheader("Content-Type"), resp.read()
        finally:
            conn.close()

    def test_get_unknown_is_404(self):
        self.assertEqual(self._request("GET", "/no/such/thing")[0], 404)

    def test_post_unknown_is_404(self):
        self.assertEqual(self._request("POST", "/no/such/thing", body=b"{}")[0], 404)

    def test_post_to_get_only_route_is_404(self):
        self.assertEqual(self._request("POST", "/api/latest", body=b"{}")[0], 404)

    def test_traversal_attempts_are_404_and_never_leak_server_source(self):
        for p in ["/../server.py", "/%2e%2e/server.py", "/..%2fserver.py", "//etc/passwd"]:
            with self.subTest(path=p):
                status, _ctype, body = self._request("GET", p)
                self.assertEqual(status, 404)
                self.assertNotIn(b"def do_GET", body)
                self.assertNotIn(b"import", body)

    def test_static_content_types(self):
        status, ctype, body = self._request("GET", "/")
        self.assertEqual((status, ctype), (200, "text/html; charset=utf-8"))
        self.assertTrue(body.lstrip().lower().startswith(b"<!doctype") or b"<html" in body[:500].lower())
        status, ctype, _ = self._request("GET", "/app.js")
        self.assertEqual((status, ctype), (200, "application/javascript; charset=utf-8"))
        status, ctype, _ = self._request("GET", "/styles.css")
        self.assertEqual((status, ctype), (200, "text/css; charset=utf-8"))

    def test_invalid_json_on_recheck_source_keeps_its_400_message(self):
        status, _ctype, body = self._request("POST", "/api/recheck-source", body=b"{bad")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body), {"error": "Тіло запиту має бути JSON"})

    def test_non_object_json_on_recheck_source_keeps_its_400_message(self):
        status, _ctype, body = self._request("POST", "/api/recheck-source", body=b"[1, 2]")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body), {"error": "Тіло запиту має бути JSON-об'єктом"})

    def test_invalid_json_on_config_stays_a_500_with_parser_message(self):
        # Preserved quirk: the generic JSON endpoints report parse errors as 500 with the parser text.
        status, _ctype, body = self._request("POST", "/api/config", body=b"{bad")
        self.assertEqual(status, 500)
        self.assertIn("Expecting", json.loads(body)["error"])

    def test_sequential_mixed_requests_on_one_keep_alive_connection(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("GET", "/styles.css")
            r1 = conn.getresponse()
            body1 = r1.read()
            self.assertEqual(r1.status, 200)
            self.assertEqual(r1.getheader("Content-Type"), "text/css; charset=utf-8")
            self.assertEqual(body1, (server.WEB_DIR / "styles.css").read_bytes())

            conn.request("GET", "/api/steam-status")
            r2 = conn.getresponse()
            body2 = json.loads(r2.read())
            self.assertEqual(r2.status, 200)
            self.assertIsInstance(body2, dict)

            conn.request("GET", "/definitely/unknown")
            r3 = conn.getresponse()
            r3.read()
            self.assertEqual(r3.status, 404)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
