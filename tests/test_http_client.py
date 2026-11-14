"""Behaviour of lookup/http_client.py (moved from steam_lookup in T16).

No network: urllib.request.urlopen is mocked, and shutil.which is forced to None so the curl branch
is skipped and the urllib fallback is the one under test.
Run: python -m unittest tests.test_http_client -v
"""

from __future__ import annotations

import sys
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


if __name__ == "__main__":
    unittest.main()
