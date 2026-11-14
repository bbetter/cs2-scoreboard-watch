"""EventBus: subscription order, wildcard, unsubscribe, failing handlers, thread-safety smoke test."""

import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.events as events  # noqa: E402
from app.events import WILDCARD, EventBus  # noqa: E402


class SubscribePublishTests(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()

    def test_handler_receives_topic_and_payload(self):
        seen = []
        self.bus.subscribe("scan.created", lambda t, p: seen.append((t, p)))
        self.bus.publish("scan.created", {"id": 1})
        self.bus.publish("scan.finished", {"id": 2})
        self.assertEqual(seen, [("scan.created", {"id": 1})])

    def test_publish_without_subscribers_is_a_noop(self):
        self.bus.publish("nobody.listens", None)

    def test_handlers_run_in_registration_order(self):
        order = []
        for name in ("a", "b", "c"):
            self.bus.subscribe("t", lambda topic, payload, n=name: order.append(n))
        self.bus.publish("t")
        self.assertEqual(order, ["a", "b", "c"])

    def test_wildcard_receives_every_topic_after_specific_handlers(self):
        order = []
        self.bus.subscribe(WILDCARD, lambda t, p: order.append(("*", t)))
        self.bus.subscribe("x", lambda t, p: order.append(("x", t)))
        self.bus.publish("x", 1)
        self.bus.publish("y", 2)
        self.assertEqual(order, [("x", "x"), ("*", "x"), ("*", "y")])

    def test_unsubscribe_stops_delivery_and_is_idempotent(self):
        seen = []

        def handler(t, p):
            seen.append(p)

        unsub = self.bus.subscribe("t", handler)
        self.bus.publish("t", 1)
        unsub()
        unsub()  # second call must not raise
        self.bus.publish("t", 2)
        self.assertEqual(seen, [1])

    def test_unsubscribe_removes_only_that_registration(self):
        seen = []

        def handler(t, p):
            seen.append(p)

        self.bus.subscribe("t", handler)
        unsub_second = self.bus.subscribe("t", handler)
        unsub_second()
        self.bus.publish("t", "x")
        self.assertEqual(seen, ["x"])  # the first registration is still there

    def test_unsubscribe_during_publish_does_not_break_the_publish(self):
        calls = []
        unsub_holder = {}

        def first(t, p):
            calls.append("first")
            unsub_holder["fn"]()

        def second(t, p):
            calls.append("second")

        unsub_holder["fn"] = self.bus.subscribe("t", first)
        self.bus.subscribe("t", second)
        self.bus.publish("t")
        self.assertEqual(calls, ["first", "second"])  # publish works from a snapshot of handlers


class FailureIsolationTests(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()

    def test_raising_handler_does_not_stop_later_handlers_or_the_publisher(self):
        later = []

        def boom(t, p):
            raise RuntimeError("handler failed")

        self.bus.subscribe("t", lambda t, p: later.append("before"))
        self.bus.subscribe("t", boom)
        self.bus.subscribe("t", lambda t, p: later.append("after"))
        self.bus.subscribe(WILDCARD, lambda t, p: later.append("wild"))
        with mock.patch.object(events.traceback, "print_exc") as printed:
            self.bus.publish("t", None)  # must not raise
        self.assertEqual(later, ["before", "after", "wild"])
        printed.assert_called_once()

    def test_failing_wildcard_handler_is_contained(self):
        with mock.patch.object(events.traceback, "print_exc"):
            self.bus.subscribe(WILDCARD, mock.Mock(side_effect=ValueError("x")))
            ok = mock.Mock()
            self.bus.subscribe("t", ok)
            self.bus.publish("t", 1)
        ok.assert_called_once_with("t", 1)

    def test_base_exceptions_are_not_swallowed(self):
        # Only Exception is caught; KeyboardInterrupt-style errors propagate to the caller.
        def interrupt(t, p):
            raise KeyboardInterrupt

        self.bus.subscribe("t", interrupt)
        with self.assertRaises(KeyboardInterrupt):
            self.bus.publish("t")


class ThreadSafetyTests(unittest.TestCase):
    def test_publish_from_eight_threads_delivers_every_event(self):
        bus = EventBus()
        received = []
        bus.subscribe("t", lambda topic, payload: received.append(payload))
        per_thread, threads_n = 200, 8

        def worker(k):
            for i in range(per_thread):
                bus.publish("t", (k, i))

        threads = [threading.Thread(target=worker, args=(k,)) for k in range(threads_n)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=30)
        self.assertEqual(len(received), per_thread * threads_n)
        self.assertEqual(len(set(received)), per_thread * threads_n)

    def test_subscribe_and_unsubscribe_while_publishing(self):
        bus = EventBus()
        bus.subscribe("t", lambda t, p: None)
        stop = threading.Event()
        errors = []

        def publisher():
            try:
                while not stop.is_set():
                    bus.publish("t", 1)
            except Exception as exc:  # pragma: no cover - would be a failure
                errors.append(exc)

        th = threading.Thread(target=publisher)
        th.start()
        try:
            for _ in range(300):
                unsub = bus.subscribe("t", lambda t, p: None)
                unsub()
        finally:
            stop.set()
            th.join(timeout=10)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
