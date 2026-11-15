"""The source registry: built-in sources first, then third-party plugins from the `cs2watch.sources` entry points.

Adding a built-in source = one module under sources/ + one line below. A third-party package adds a source by
declaring `[project.entry-points."cs2watch.sources"]`; see docs/ARCHITECTURE.md ("As a plugin package").
Plugins run with the same trust as any installed package. A broken plugin is logged and skipped, never fatal.
"""

from __future__ import annotations

import logging
import os
from importlib.metadata import entry_points
from typing import Any, Iterable, Optional

from sources.base import DataSource, SourceRegistry
from sources.cstracker import CsTrackerSource
from sources.faceit import FaceitSource
from sources.leetify import LeetifySource
from sources.steam_stats import SteamStatsSource

PLUGIN_GROUP = "cs2watch.sources"
DISABLE_ENV = "CS2WATCH_DISABLE_PLUGINS"

_LOG = logging.getLogger("cs2watch.sources")

REGISTRY = SourceRegistry()
REGISTRY.register(LeetifySource())
REGISTRY.register(FaceitSource())
REGISTRY.register(CsTrackerSource())
REGISTRY.register(SteamStatsSource())


def _plugin_entry_points() -> Iterable[Any]:
    return entry_points(group=PLUGIN_GROUP)


def load_plugins(registry: SourceRegistry, entry_points: Optional[Iterable[Any]] = None) -> list[str]:
    """Register every `cs2watch.sources` entry point that yields a DataSource; return the registered ids.

    `entry_points` is injectable for tests (objects with `.name` and `.load()`). Any failure is logged as a
    warning with the entry point name and that plugin is skipped.
    """
    if os.environ.get(DISABLE_ENV, "").strip() == "1":
        return []
    eps = _plugin_entry_points() if entry_points is None else entry_points
    loaded: list[str] = []
    for ep in eps:
        name = getattr(ep, "name", "?")
        try:
            obj = ep.load()
            if isinstance(obj, type):
                if not issubclass(obj, DataSource):
                    _LOG.warning("plugin %s: %r is not a DataSource subclass; skipped", name, obj)
                    continue
                obj = obj()
            if not isinstance(obj, DataSource):
                _LOG.warning("plugin %s: %r is not a DataSource; skipped", name, type(obj).__name__)
                continue
            registry.register(obj, builtin=False)
        except Exception as exc:  # a broken plugin must not break the app
            _LOG.warning("plugin %s skipped: %s: %s", name, type(exc).__name__, exc)
            continue
        loaded.append(obj.id)
    return loaded


load_plugins(REGISTRY)
