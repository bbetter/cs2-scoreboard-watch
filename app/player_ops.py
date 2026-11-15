"""Player-row operations on scan records: lookup, replacement, source refresh. No HTTP here.

Routes in `app.server` call these with the live STORE / REGISTRY and pass the callbacks
(`make_ctx`, `save_locked`, `broadcast`) so the tests can replace them.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from sources.base import SourceContext


def player_matches(p: dict, uid: str | None, nick: str | None) -> bool:
    """Match a stored player by its stable player_uid when the caller sent one, else by nick (legacy API)."""
    if uid:
        return p.get("player_uid") == uid
    return p.get("nick") == nick


def find_player(scan_rec: dict, uid: str | None, nick: str | None) -> dict | None:
    """First row of the scan that `player_matches`, or None. Caller holds the store lock."""
    return next((p for p in scan_rec.get("players", []) if player_matches(p, uid, nick)), None)


def replace_player_by_uid(scan_rec: dict, player: dict) -> bool:
    """Put `player` in place of the row with the same player_uid. False when there is no such row
    (or the uid is empty). Other rows are not touched. Caller holds the store lock."""
    uid = player.get("player_uid")
    if not uid:
        return False
    players = scan_rec["players"]
    for i, p in enumerate(players):
        if p.get("player_uid") == uid:
            players[i] = player
            return True
    return False


def mark_edited(player: dict) -> dict:
    """Stamp a row that a route just wrote by hand. A run whose enrichment started before this moment keeps the row
    (see ScanPipeline._merge_rows)."""
    player["edited_at"] = time.time()
    return player


def source_choices_text(ids: list[str]) -> str:
    """'a' або 'b' for two ids; 'a', 'b' або 'c' for more (matches the historical 400 text)."""
    quoted = [f"'{i}'" for i in ids]
    if len(quoted) <= 2:
        return " або ".join(quoted)
    return ", ".join(quoted[:-1]) + " або " + quoted[-1]


def refresh_source_entry(steam: dict, src: Any, registry: Any) -> None:
    """Update steam["sources"] for ONE source after its raw data was rewritten. Other entries and coverage stay as they are."""
    if "sources" not in steam:
        steam["sources"] = registry.sources_view(steam)
        return
    view = dict(steam.get("sources") or {})
    res = src.result_from(steam)
    if res is None:
        view.pop(src.id, None)
    else:
        view[src.id] = res.to_dict()
    steam["sources"] = view


def recheck_source(
    store: Any,
    registry: Any,
    scan_id: str | None,
    nick: str,
    source: Any,
    player_uid: str | None,
    *,
    make_ctx: Callable[[], SourceContext],
    save_locked: Callable[[], None],
    broadcast: Callable[[str, dict], None],
) -> tuple[int, dict]:
    """Re-fetch ONE recheckable external source for one player of a scan.

    Other sources and the scoring are left untouched. Returns (http_status, body):
    the updated scan on success, {"error": ...} otherwise. `save_locked` runs under `store.lock`;
    `broadcast("scan", snapshot)` runs after the lock is released.
    """
    # `source` comes from JSON, so only strings can be registry keys.
    src = registry.get(source) if isinstance(source, str) else None
    if src is None or not src.recheckable:
        return 400, {"error": f"source має бути {source_choices_text(registry.recheckable_ids())}"}
    if not scan_id or not (nick or player_uid):
        return 400, {"error": "scan_id і nick є обов'язковими"}

    with store.lock:
        scan_rec = store.find_locked(scan_id)
        if not scan_rec:
            return 404, {"error": "Scan not found"}
        player = find_player(scan_rec, player_uid, nick)
        if player is None:
            return 404, {"error": "Player not found in scan"}
        steamid64 = (player.get("steam") or {}).get("steamid64")
    if not steamid64:
        return 409, {"error": "У гравця немає SteamID64: спочатку виправте нік або вставте Steam URL"}

    ctx = make_ctx()
    if not src.is_enabled(ctx):
        return 409, {"error": f"Джерело {source} вимкнене: не задано потрібний ключ"}
    try:
        fresh = src.fetch_raw(steamid64, ctx)
    except Exception as exc:
        return 502, {"error": f"Запит до {source} не вдався: {exc}"}

    with store.lock:
        scan_rec = store.find_locked(scan_id)
        player = find_player(scan_rec, player_uid, nick) if scan_rec else None
        if scan_rec is None or player is None:
            return 404, {"error": "Scan or player was removed while refreshing"}
        steam = dict(player.get("steam") or {})
        src.write_raw(steam, fresh)  # copies the containers along the slot path; siblings are kept
        refresh_source_entry(steam, src, registry)
        player["steam"] = steam
        mark_edited(player)
        save_locked()
        snapshot = dict(scan_rec)

    broadcast("scan", snapshot)
    return 200, snapshot
