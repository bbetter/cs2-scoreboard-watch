"""Guard: the legacy tests must not create, modify or delete anything under storage.paths.DATA_DIR.

Snapshot (path, mtime_ns, size) of every file in DATA_DIR, run the four legacy test modules in-process,
snapshot again and compare. Reads are not detected here; the modules were audited by hand (see T05 PR).
"""

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lookup.steam_lookup as sl  # noqa: E402
from storage.paths import DATA_DIR  # noqa: E402

LEGACY_MODULES = [
    "tests.test_scoring_f4",
    "tests.test_nick_match",
    "tests.test_steam_queue",
    "tests.test_stale_cache_fallback",
]


def _snapshot() -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    if not DATA_DIR.is_dir():
        return out
    for root, _dirs, files in os.walk(DATA_DIR):
        for name in files:
            path = Path(root) / name
            st = path.stat()
            out[str(path)] = (st.st_mtime_ns, st.st_size)
    return out


class DataDirUntouchedTests(unittest.TestCase):
    def test_legacy_tests_do_not_touch_data_dir(self):
        before = _snapshot()

        suite = unittest.TestSuite()
        for name in LEGACY_MODULES:
            suite.addTests(unittest.defaultTestLoader.loadTestsFromName(name))
        # Production logging stays on: lookup calls write the pton.logs handler, but into a temp dir here,
        # so the snapshot only sees real writes under DATA_DIR. (tests.test_stale_cache_fallback calls
        # lookup_steam_profile without patching the logger.)
        with tempfile.TemporaryDirectory() as log_tmp, mock.patch.object(sl, "LOG_DIR", Path(log_tmp)):
            result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
        self.assertGreater(result.testsRun, 0, "legacy suite did not run any test")

        after = _snapshot()
        added = sorted(set(after) - set(before))
        removed = sorted(set(before) - set(after))
        changed = sorted(p for p in set(before) & set(after) if before[p] != after[p])
        self.assertEqual(added, [], f"files created in data/: {added}")
        self.assertEqual(removed, [], f"files removed from data/: {removed}")
        self.assertEqual(changed, [], f"files modified in data/: {changed}")


if __name__ == "__main__":
    unittest.main()
