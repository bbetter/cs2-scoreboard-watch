"""The built-in source registry. Adding a source = one module under sources/ + one line below."""

from sources.base import SourceRegistry
from sources.cstracker import CsTrackerSource
from sources.faceit import FaceitSource
from sources.leetify import LeetifySource
from sources.steam_stats import SteamStatsSource

REGISTRY = SourceRegistry()
REGISTRY.register(LeetifySource())
REGISTRY.register(FaceitSource())
REGISTRY.register(CsTrackerSource())
REGISTRY.register(SteamStatsSource())
