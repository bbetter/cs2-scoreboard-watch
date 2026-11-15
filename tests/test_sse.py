"""Tests for app.sse.SseHub (subscriber limit, message format, overflow) and the live /api/events limit.

The live test binds an ephemeral port on 127.0.0.1, opens one SSE stream and checks that a second one gets 503.
No scan, config or player data is read or changed.
Run: /home/andrii/fun/.venv/bin/python -m unittest tests.test_sse -v
"""

import http.client
import json
import queue
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.server as server  # noqa: E402
from app.sse import SseHub  # noqa: E402


class SseHubTests(unittest.TestCase):
    def test_limit_is_enforced_and_released_by_unsubscribe(self):
        hub = SseHub(max_subscribers=2)
        a = hub.subscribe()
        b = hub.subscribe()
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        self.assertIsNone(hub.subscribe())
        self.assertEqual(hub.count(), 2)
        hub.unsubscribe(a)
        self.assertEqual(hub.count(), 1)
        self.assertIsNotNone(hub.subscribe())

    def test_unsubscribe_twice_and_none_do_not_raise(self):
        hub = SseHub(max_subscribers=1)
        q = hub.subscribe()
        hub.unsubscribe(q)
        hub.unsubscribe(q)
        hub.unsubscribe(None)
        self.assertEqual(hub.count(), 0)

    def test_broadcast_message_format(self):
        hub = SseHub()
        q = hub.subscribe()
        hub.broadcast("scan", {"name": "тест"})
        self.assertEqual(q.get_nowait(), 'event: scan\ndata: {"name": "тест"}\n\n')

    def test_broadcast_without_subscribers_is_a_no_op(self):
        SseHub().broadcast("scan", {})

    def test_full_queue_is_dropped_and_others_keep_receiving(self):
        hub = SseHub(max_subscribers=2, queue_size=1)
        slow = hub.subscribe()
        fast = hub.subscribe()
        hub.broadcast("a", {})
        fast.get_nowait()
        hub.broadcast("b", {})
        self.assertEqual(hub.count(), 1)
        self.assertIn("event: b", fast.get_nowait())
        # The dropped subscriber keeps only the close marker: the route ends its stream on it.
        self.assertIsNone(slow.get_nowait())
        with self.assertRaises(queue.Empty):
            slow.get_nowait()

    def test_dropped_subscriber_is_told_to_close_even_with_a_full_queue(self):
        hub = SseHub(max_subscribers=1, queue_size=2)
        slow = hub.subscribe()
        hub.broadcast("a", {})
        hub.broadcast("b", {})
        hub.broadcast("c", {})  # overflows: queue is drained and replaced by the close marker
        self.assertEqual(hub.count(), 0)
        self.assertIsNone(slow.get_nowait())
        self.assertTrue(slow.empty())

    def test_broadcast_event_in_server_goes_through_the_hub(self):
        hub = SseHub()
        q = hub.subscribe()
        with mock.patch.object(server, "SSE", hub):
            server._broadcast_event("scan_update", {"ok": True})
        self.assertEqual(json.loads(q.get_nowait().split("data: ", 1)[1]), {"ok": True})


class LiveSseTests(unittest.TestCase):
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

    def setUp(self):
        self.hub = SseHub(max_subscribers=1)
        patcher = mock.patch.object(server, "SSE", self.hub)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.conns = []

    def tearDown(self):
        for conn in self.conns:
            conn.close()

    def _open(self, path="/api/events"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.conns.append(conn)
        conn.request("GET", path)
        return conn, conn.getresponse()

    def test_second_event_stream_gets_503_while_first_is_open(self):
        conn1, resp1 = self._open()
        self.assertEqual(resp1.status, 200)
        self.assertEqual(resp1.getheader("Content-Type"), "text/event-stream")
        self.assertEqual(resp1.readline(), b": connected\n")
        self.assertEqual(self.hub.count(), 1)

        conn2, resp2 = self._open()
        self.assertEqual(resp2.status, 503)
        self.assertEqual(resp2.getheader("Content-Type"), "application/json; charset=utf-8")
        self.assertEqual(json.loads(resp2.read()), {"error": "Too many event streams"})
        self.assertEqual(self.hub.count(), 1)


if __name__ == "__main__":
    unittest.main()
