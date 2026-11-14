"""External stats sources: contract in base.py, built-in sources in leetify/faceit/cstracker/steam_stats.

Use `REGISTRY` (built in registry.py) to iterate sources, read/write their slots, and build coverage.
"""

from sources.base import (
    STATUSES,
    DataSource,
    LegacySlotSource,
    SourceContext,
    SourceRegistry,
    SourceResult,
)
from sources.registry import REGISTRY

__all__ = [
    "REGISTRY",
    "STATUSES",
    "DataSource",
    "LegacySlotSource",
    "SourceContext",
    "SourceRegistry",
    "SourceResult",
]
