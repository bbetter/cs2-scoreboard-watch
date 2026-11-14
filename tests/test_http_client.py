"""Behaviour of lookup/http_client.py (moved from steam_lookup in T16).

No network: urllib.request.urlopen is mocked, and shutil.which is forced to None so the curl branch
is skipped and the urllib fallback is the one under test.
Run: python -m unittest tests.test_http_client -v
"""

from __future__ import annotations

import http.server
import shutil
import sys
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lookup.http_client as http_client  # noqa: E402


def _response(status: int, body: bytes) -> mock.MagicMock:
    resp = mock.MagicMock()
    resp.status = status
    resp.read.return_value = body
    resp.__enter__.return_value = resp
    return resp


class HttpClientTests(unittest.TestCase):
    def setUp(self) -> None:
        no_curl = mock.patch("shutil.which", return_value=None)
        no_curl.start()
        self.addCleanup(no_curl.stop)
        open_patch = mock.patch.object(http_client.urllib.request, "urlopen")
        self.urlopen = open_patch.start()
        self.addCleanup(open_patch.stop)

    def test_success_returns_status_and_body(self) -> None:
        self.urlopen.return_value = _response(200, b"<xml/>")
        self.assertEqual(http_client.http_get_status("https://example.test/a"), (200, b"<xml/>"))
        self.assertEqual(http_client.http_get("https://example.test/a"), b"<xml/>")

    def test_sends_user_agent_cookie_and_extra_headers(self) -> None:
        self.urlopen.return_value = _response(200, b"ok")
        http_client.http_get_status(
            "https://example.test/b", timeout=2.5, cookies="sessionid=abc", headers={"X-Test": "1"}
        )
        req, kwargs = self.urlopen.call_args.args[0], self.urlopen.call_args.kwargs
        self.assertEqual(kwargs, {"timeout": 2.5})
        self.assertEqual(req.get_header("User-agent"), http_client.USER_AGENT)
        self.assertEqual(req.get_header("Accept-language"), "en-US,en;q=0.9")
        self.assertEqual(req.get_header("Cookie"), "sessionid=abc")
        self.assertEqual(req.get_header("X-test"), "1")

    def test_http_error_429_returns_status_without_raising(self) -> None:
        self.urlopen.side_effect = urllib.error.HTTPError("https://example.test/c", 429, "Too Many", {}, None)
        self.assertEqual(http_client.http_get_status("https://example.test/c"), (429, None))
        self.assertIsNone(http_client.http_get("https://example.test/c"))

    def test_http_error_500_returns_status_without_raising(self) -> None:
        self.urlopen.side_effect = urllib.error.HTTPError("https://example.test/d", 500, "Oops", {}, None)
        self.assertEqual(http_client.http_get_status("https://example.test/d"), (500, None))

    def test_non_200_success_status_gives_no_body_to_http_get(self) -> None:
        self.urlopen.return_value = _response(203, b"partial")
        self.assertEqual(http_client.http_get_status("https://example.test/e"), (203, b"partial"))
        self.assertIsNone(http_client.http_get("https://example.test/e"))

    def test_url_error_and_timeout_return_zero(self) -> None:
        self.urlopen.side_effect = urllib.error.URLError("refused")
        self.assertEqual(http_client.http_get_status("https://example.test/f"), (0, None))
        self.urlopen.side_effect = TimeoutError("timed out")
        self.assertEqual(http_client.http_get_status("https://example.test/f"), (0, None))
        self.assertIsNone(http_client.http_get("https://example.test/f"))

    def test_value_error_from_urlopen_returns_zero(self) -> None:
        self.urlopen.side_effect = ValueError("unknown url type")
        self.assertEqual(http_client.http_get_status("https://example.test/g"), (0, None))

    def test_malformed_url_raises_from_request_constructor(self) -> None:
        # Kept as in the original steam_lookup helper: Request() is built before the try block,
        # so a malformed URL raises ValueError to the caller instead of returning (0, None).
        with self.assertRaises(ValueError):
            http_client.http_get_status("not a url")


class CurlArgvTests(unittest.TestCase):
    """curl branch: URL, cookies and headers go through stdin (`-K -`), never through argv."""

    URL = "https://api.example.test/x?key=SECRET123"

    def setUp(self) -> None:
        which = mock.patch("shutil.which", return_value="/usr/bin/curl")
        which.start()
        self.addCleanup(which.stop)
        run_patch = mock.patch("subprocess.run", return_value=mock.Mock(stdout=b"body\n200"))
        self.run = run_patch.start()
        self.addCleanup(run_patch.stop)
        open_patch = mock.patch.object(http_client.urllib.request, "urlopen")
        self.urlopen = open_patch.start()
        self.addCleanup(open_patch.stop)

    def test_secrets_are_not_in_argv(self) -> None:
        http_client.http_get_status(self.URL, headers={"Authorization": "Bearer SECRET456"})
        argv = self.run.call_args.args[0]
        self.assertIn("-K", argv)
        self.assertIn("-", argv)
        for arg in argv:
            self.assertNotIn("SECRET123", arg)
            self.assertNotIn("SECRET456", arg)
            self.assertNotIn("api.example.test", arg)
        self.assertNotIn("-H", argv)
        self.assertNotIn("-b", argv)

    def test_config_on_stdin_contains_url_and_header(self) -> None:
        http_client.http_get_status(self.URL, headers={"Authorization": "Bearer SECRET456"})
        config = self.run.call_args.kwargs["input"].decode("utf-8")
        self.assertIn('url = "https://api.example.test/x?key=SECRET123"', config)
        self.assertIn('header = "Authorization: Bearer SECRET456"', config)

    def test_cookie_goes_to_config(self) -> None:
        http_client.http_get_status(self.URL, cookies="sessionid=abc")
        config = self.run.call_args.kwargs["input"].decode("utf-8")
        self.assertIn('cookie = "sessionid=abc"', config)

    def test_quotes_and_backslashes_are_escaped(self) -> None:
        config = http_client._curl_config('https://x/"a\\b', None, {"X-Q": 'say "hi" \\'})
        self.assertEqual(
            config,
            'url = "https://x/\\"a\\\\b"\n'
            'header = "X-Q: say \\"hi\\" \\\\"\n',
        )

    def test_line_break_in_value_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            http_client._curl_config("https://x/\nevil", None, None)
        with self.assertRaises(ValueError):
            http_client._curl_config("https://x/", "a=b\rc", None)
        with self.assertRaises(ValueError):
            http_client._curl_config("https://x/", None, {"X-Evil": "a\nb"})

    def test_line_break_falls_back_to_urllib_without_calling_curl(self) -> None:
        self.urlopen.return_value = mock.MagicMock(status=200)
        self.urlopen.return_value.read.return_value = b"ok"
        self.urlopen.return_value.__enter__.return_value = self.urlopen.return_value
        result = http_client.http_get_status(self.URL, headers={"X-Evil": "a\nb"})
        self.run.assert_not_called()
        self.urlopen.assert_called_once()
        self.assertEqual(result, (200, b"ok"))

    def test_parses_status_and_body(self) -> None:
        self.assertEqual(http_client.http_get_status(self.URL), (200, b"body"))


@unittest.skipUnless(shutil.which("curl"), "curl is not installed")
class CurlLiveTests(unittest.TestCase):
    """Real curl against a local server: URL, query and headers must arrive via `-K -`."""

    def setUp(self) -> None:
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                body = f"{self.headers.get('X-Test')}|{self.headers.get('Cookie')}|{self.path}".encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def test_real_curl_delivers_url_query_header_and_cookie(self) -> None:
        port = self.server.server_address[1]
        url = f"http://127.0.0.1:{port}/p?key=Q1"
        # urllib must not answer this request: only the curl branch may succeed.
        with mock.patch.object(http_client.urllib.request, "urlopen", side_effect=AssertionError("urllib used")):
            status, body = http_client.http_get_status(url, cookies="sid=C1", headers={"X-Test": "H1"})
        self.assertEqual(status, 200)
        self.assertEqual(body, b"H1|sid=C1|/p?key=Q1")


if __name__ == "__main__":
    unittest.main()
