"""Versioned scan records: `schema_version` plus an ordered chain of adapters applied on load.

A record without `schema_version` is version 1 (written before versioning). `migrate()` applies
`MIGRATIONS[v]` for each step from the record's version up to CURRENT_SCHEMA_VERSION. A record newer than this
code is returned untouched, with one warning per process.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

CURRENT_SCHEMA_VERSION = 2

_LOG = logging.getLogger(__name__)
_warned_newer = False


def _v1_to_v2(scan: dict, registry: Any) -> None:
    """Pre-versioning records: give players `steam.sources` and a `player_uid`."""
    for player in scan.get("players") or []:
        steam = player.get("steam") if isinstance(player, dict) else None
        if isinstance(steam, dict) and "sources" not in steam:
            steam["sources"] = registry.sources_view(steam)
    for idx, player in enumerate(scan.get("players") or []):
        if isinstance(player, dict) and not player.get("player_uid"):
            player["player_uid"] = f"{scan.get('id')}-{idx}"  # legacy record: assign once, persisted on next save
    scan["schema_version"] = 2


# from version -> migration that produces version (from + 1)
MIGRATIONS: dict[int, Callable[[dict, Any], None]] = {1: _v1_to_v2}


def migrate(scan: dict, registry: Any) -> dict:
    """Upgrade `scan` in place to CURRENT_SCHEMA_VERSION and return it.

    Raises ValueError for a schema_version that is not a positive integer (e.g. the string "2"), so the caller can
    treat the file as unreadable instead of crashing on a comparison.
    """
    global _warned_newer
    version = scan.get("schema_version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError(f"unknown scan schema_version {version!r}")
    if version > CURRENT_SCHEMA_VERSION:
        if not _warned_newer:
            _warned_newer = True
            _LOG.warning(
                "scan record schema_version %s is newer than supported %s; left unchanged",
                version, CURRENT_SCHEMA_VERSION,
            )
        return scan
    while version < CURRENT_SCHEMA_VERSION:
        MIGRATIONS[version](scan, registry)
        version = scan["schema_version"]
    return scan
