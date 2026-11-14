"""Optional template hook for an official external CS2 telemetry API client."""

from __future__ import annotations

from typing import Any, Optional


def fetch_player_telemetry(steamid64: str) -> Optional[dict[str, Any]]:
    """Return a multi-source telemetry dict for the given SteamID64, or None when unconfigured."""
    return None
