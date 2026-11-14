"""Pure JSON parser/normalizer for multi-source CS2 player telemetry payloads."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


def _v(obj: Any, key: str) -> Optional[float]:
    """Extract numeric 'value' from {'value': ..., 'unit': ..., 'raw': ...} or plain numbers."""
    node = obj.get(key) if isinstance(obj, dict) else None
    if isinstance(node, dict):
        val = node.get("value")
        return float(val) if isinstance(val, (int, float)) else None
    return float(node) if isinstance(node, (int, float)) else None


def _parse_age_years(iso_str: Optional[str]) -> Optional[float]:
    if not iso_str or not isinstance(iso_str, str):
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        years = (datetime.now(timezone.utc) - dt).days / 365.25
        return round(max(0.0, years), 1)
    except ValueError:
        return None


def parse_multi_source_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize a multi-source CS2 profile JSON payload into internal structures."""
    if not isinstance(payload, dict):
        return {}

    prof = payload.get("profile") or {}
    st = payload.get("steam") or {}
    lf = payload.get("leetify") or {}
    fc = payload.get("faceit") or {}
    ct = payload.get("cstracker") or {}
    sg = payload.get("scope_gg") or {}
    medals_obj = payload.get("medals") or {}
    matches_sources = (payload.get("matches") or {}).get("sources") or {}

    steamid64 = str(prof.get("steamid64") or st.get("steamid64") or "").strip() or None
    age_years = _parse_age_years(st.get("registered"))
    medals_items = medals_obj.get("items") if medals_obj.get("status") == "loaded" else []
    if not isinstance(medals_items, list):
        medals_items = []
    has_veteran_coin = any(
        any(tok in str(m) for tok in ("Year Service Coin", "Coin CSGO"))
        for m in medals_items
    )

    leetify_norm: Optional[dict[str, Any]] = None
    if lf.get("status") == "loaded":
        matches_val = _v(lf, "matches")
        leetify_norm = {
            "url": f"https://leetify.com/app/profile/{steamid64}" if steamid64 else None,
            "total_matches": int(matches_val) if matches_val is not None else None,
            "winrate": _v(lf, "winrate"),
            "kd": _v(lf, "kd"),
            "hs_pct": _v(lf, "hs_kills"),
            "aim": _v(lf, "aim"),
            "reaction_time_ms": _v(lf, "time_to_dmg"),
            "preaim": _v(lf, "preaim"),
            "leetify_rating": _v(lf, "rating"),
            "accuracy": _v(lf, "accuracy"),
            "counter_strafe": _v(lf, "counter_strafe"),
            "kast": _v(lf, "kast"),
            "bans": [],
        }

    faceit_norm: dict[str, Any] = {}
    if fc.get("status") == "loaded":
        faceit_norm = {
            "found": True,
            "nickname": fc.get("nickname"),
            "faceit_url": fc.get("profile_url"),
            "country": fc.get("country"),
            "registered": fc.get("registered"),
        }

    cstracker_norm: dict[str, Any] = {}
    if ct.get("status") == "loaded":
        ct_matches = (matches_sources.get("cstracker") or {}).get("matches") or []
        latest_skillgroup = None
        if ct_matches and isinstance(ct_matches[0], dict):
            latest_skillgroup = (ct_matches[0].get("rank") or {}).get("skillgroup")
        cstracker_norm = {
            "url": ct.get("profile_url"),
            "trust": _v(ct, "trust"),
            "trust_based_on_matches": ct.get("trust_based_on_matches"),
            "vaclive": _v(ct, "vaclive"),
            "ttd_ms": _v(ct, "ttd"),
            "preaim": _v(ct, "preaim"),
            "kd": _v(ct, "kd"),
            "accuracy": _v(ct, "accuracy"),
            "adr": _v(ct, "adr"),
            "hltv": _v(ct, "hltv"),
            "latest_skillgroup": latest_skillgroup,
            "recent_match_count": len(ct_matches),
        }

    scope_norm: dict[str, Any] = {}
    if sg.get("status") == "loaded":
        sg_head = sg.get("headline") or {}
        sg_perf = sg.get("performance") or {}
        scope_norm = {
            "url": sg.get("profile_url"),
            "role": sg.get("role"),
            "period": sg.get("period"),
            "aim": _v(sg_head, "aim"),
            "hltv_2_1": _v(sg_head, "hltv_2_1"),
            "win_rate": _v(sg_head, "win_rate"),
            "kd": _v(sg_perf, "k_d"),
            "adr": _v(sg_perf, "adr"),
            "kast": _v(sg_perf, "kast"),
            "opening_duels": _v(sg_perf, "opening_duels"),
        }

    return {
        "steamid64": steamid64,
        "profile_name": prof.get("name"),
        "profile_url": st.get("profile_url"),
        "vanity": st.get("vanity"),
        "country": st.get("country"),
        "account_age_years": age_years,
        "xp_level": _v(prof, "xp_level"),
        "medals_count": len(medals_items),
        "has_veteran_coin": has_veteran_coin,
        "medals": medals_items,
        "leetify": leetify_norm,
        "faceit": faceit_norm,
        "cstracker": cstracker_norm,
        "scope_gg": scope_norm,
    }
