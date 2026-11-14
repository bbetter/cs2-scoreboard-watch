"""Tests for storage.profile_cache.ProfileCache: lazy load, persistence, strip hook, bad files.

Run from the repo root:
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from storage.profile_cache import ProfileCache  # noqa: E402


def _strip_leetify_key(d: dict) -> dict:
    out = dict(d)
    out["leetify"] = None
    return out


class ProfileCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "profile_cache.json"

    def _profile(self, **extra) -> dict:
        base = {"steamid64": "76561198000000001", "lookup_source": "steam_web_api", "profile_name": "Nick"}
        base.update(extra)
        return base

    def test_constructor_does_not_read_file(self) -> None:
        # Broken JSON on disk must not raise at construction time.
        self.path.write_text("{not json", encoding="utf-8")
        cache = ProfileCache(self.path)  # must not raise
        self.assertFalse(cache._loaded)

    def test_missing_file_is_not_an_error(self) -> None:
        cache = ProfileCache(self.path)
        self.assertIsNone(cache.get("nick"))
        self.assertEqual(cache.entries(), {})

    def test_first_access_loads_the_file(self) -> None:
        cache = ProfileCache(self.path)
        # The file appears after construction; the first get must see it, proving the load is deferred.
        self.path.write_text(
            json.dumps({"nick": [time.time(), self._profile()]}), encoding="utf-8"
        )
        hit = cache.get("nick")
        self.assertIsNotNone(hit)
        self.assertEqual(hit[1]["profile_name"], "Nick")

    def test_save_then_new_instance_loads_same_records(self) -> None:
        ts = time.time()
        cache = ProfileCache(self.path)
        with cache.lock:
            cache.put_locked("nick", ts, self._profile())
            cache.save_locked()

        reloaded = ProfileCache(self.path)
        with reloaded.lock:
            loaded = reloaded.get("nick")
        self.assertEqual(loaded, (ts, self._profile()))

    def test_strip_applied_on_put(self) -> None:
        cache = ProfileCache(self.path, strip=_strip_leetify_key)
        with cache.lock:
            cache.put_locked("nick", time.time(), self._profile(leetify={"rank": 9}))
            stored = cache.get("nick")[1]
        self.assertIsNone(stored["leetify"])

    def test_strip_applied_on_load(self) -> None:
        self.path.write_text(
            json.dumps({"nick": [time.time(), self._profile(leetify={"rank": 9})]}), encoding="utf-8"
        )
        cache = ProfileCache(self.path, strip=_strip_leetify_key)
        with cache.lock:
            stored = cache.get("nick")[1]
        self.assertIsNone(stored["leetify"])

    def test_broken_json_gives_empty_cache(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")
        cache = ProfileCache(self.path)
        with cache.lock:
            self.assertEqual(cache.entries(), {})
            self.assertIsNone(cache.get("nick"))

    def test_expired_entries_are_dropped_on_load(self) -> None:
        self.path.write_text(
            json.dumps({"old": [time.time() - 10_000, self._profile()]}), encoding="utf-8"
        )
        cache = ProfileCache(self.path, ttl_sec=100)
        with cache.lock:
            self.assertIsNone(cache.get("old"))

    def test_not_found_entries_are_not_persisted(self) -> None:
        cache = ProfileCache(self.path)
        with cache.lock:
            cache.put_locked("ghost", time.time(), self._profile(lookup_source="not_found"))
            cache.save_locked()
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {})

    def test_clear_marks_loaded_so_file_is_not_read(self) -> None:
        self.path.write_text(
            json.dumps({"nick": [time.time(), self._profile()]}), encoding="utf-8"
        )
        cache = ProfileCache(self.path)
        with cache.lock:
            cache.clear()
            self.assertIsNone(cache.get("nick"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
