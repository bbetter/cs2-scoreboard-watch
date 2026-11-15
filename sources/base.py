"""DataSource contract: how an external stats provider plugs into the scan pipeline.

A source knows how to fetch raw data for a SteamID64 and where that raw data lives inside the
stored player `steam` dict (its *slot*). Everything else (coverage, recheck, AI compaction,
feedback features, UI chips) iterates the registry instead of hard-coding source names.

Storage compatibility: the four built-in sources keep their historical slots
(`steam.leetify`, `steam.faceit`, `steam.cs2_stats`, `steam.extra_stats.cstracker`), so
scans_history.json and the current frontend keep working. A new source simply inherits the
default slot `steam.sources[<id>]` and needs no other storage code.
`sources_view()` produces the uniform `{id: SourceResult-dict}` view for any stored record.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Optional

Status = Literal["ok", "not_found", "private", "blocked", "rate_limited", "error", "unavailable"]
STATUSES: tuple[str, ...] = ("ok", "not_found", "private", "blocked", "rate_limited", "error", "unavailable")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class SourceResult:
    status: Status
    data: dict = field(default_factory=dict)
    fetched_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass
class SourceContext:
    """Per-fetch environment. Kept tiny on purpose; extend with fields, not positional args."""

    steam_api_key: Optional[str] = None
    extra: dict = field(default_factory=dict)


class DataSource:
    """Base class for sources. Subclass, set the class attributes, implement fetch_raw/normalize."""

    id: str = ""
    label: str = ""
    # Appears in steam.coverage[<id>] (the legacy per-source flags map)?
    in_coverage: bool = True
    # Can the user re-fetch only this source from the UI (POST /api/recheck-source)?
    recheckable: bool = False
    # Declarative settings this source needs (consumed by the config schema in step 6).
    settings: tuple = ()
    # Own block name in the AI payload and feedback features: "" = use `id`, None = no own block.
    payload_key: Optional[str] = ""
    # Always emit the block (even when empty) instead of only when it has content.
    payload_always: bool = False

    def payload_name(self) -> Optional[str]:
        """Key of this source's block in the AI payload / feedback features, or None if it has none."""
        if self.payload_key is None:
            return None
        return self.payload_key or self.id

    # -- fetching -------------------------------------------------------------------------
    def fetch_raw(self, steamid64: str, ctx: SourceContext) -> Any:
        """Return the raw payload exactly as it is stored in the slot (None/{} if nothing)."""
        raise NotImplementedError

    def is_enabled(self, ctx: SourceContext) -> bool:
        """False when the source cannot run (e.g. missing API key). Disabled sources are skipped."""
        return True

    def normalize(self, raw: Any) -> SourceResult:
        """Map a stored raw payload to a SourceResult (never raises)."""
        return SourceResult("ok" if raw else "not_found", raw if isinstance(raw, dict) else {})

    def fetch(self, steamid64: str, ctx: SourceContext) -> SourceResult:
        if not self.is_enabled(ctx):
            return SourceResult("unavailable", {}, _now_iso())
        try:
            raw = self.fetch_raw(steamid64, ctx)
        except Exception:
            return SourceResult("error", {}, _now_iso())
        res = self.normalize(raw)
        res.fetched_at = res.fetched_at or _now_iso()
        return res

    # -- storage slot (where raw lives inside the stored `steam` dict) --------------------
    def read_raw(self, steam: Mapping) -> Any:
        return ((steam.get("sources") or {}).get(self.id) or {}).get("data")

    def write_raw(self, steam: dict, raw: Any) -> None:
        res = self.normalize(raw) if not isinstance(raw, SourceResult) else raw
        res.fetched_at = res.fetched_at or _now_iso()
        sources = dict(steam.get("sources") or {})
        sources[self.id] = res.to_dict()
        steam["sources"] = sources

    def result_from(self, steam: Mapping) -> Optional[SourceResult]:
        """Uniform view of this source for a stored record; None if never fetched."""
        stored = (steam.get("sources") or {}).get(self.id)
        if isinstance(stored, dict) and "status" in stored:
            return SourceResult(stored["status"], stored.get("data") or {}, stored.get("fetched_at") or "")
        raw = self.read_raw(steam)
        if raw is None:
            return None
        return self.normalize(raw)

    # -- features for scoring / AI / feedback ---------------------------------------------
    def features(self, result: SourceResult) -> dict[str, Optional[float]]:
        """Flat numeric features (used by feedback entries and, later, scoring rules)."""
        return {}

    def ai_compact(self, result: SourceResult) -> Any:
        """Compact JSON-able summary for the AI prompt (default: the raw data when ok)."""
        return result.data if result.ok else None


class LegacySlotSource(DataSource):
    """Source whose raw payload lives at a historical path in the `steam` dict."""

    slot: tuple[str, ...] = ()
    # Legacy slots are not AI/feedback blocks unless a subclass opts in with its own payload_key.
    payload_key = None

    def read_raw(self, steam: Mapping) -> Any:
        cur: Any = steam
        for key in self.slot:
            if not isinstance(cur, Mapping):
                return None
            cur = cur.get(key)
        return cur

    def write_raw(self, steam: dict, raw: Any) -> None:
        if isinstance(raw, SourceResult):
            raw = raw.data
        cur = steam
        for key in self.slot[:-1]:
            nxt = dict(cur.get(key) or {})
            cur[key] = nxt
            cur = nxt
        cur[self.slot[-1]] = raw

    def result_from(self, steam: Mapping) -> Optional[SourceResult]:
        raw = self.read_raw(steam)
        if raw is None:
            return None
        return self.normalize(raw)


class SourceRegistry:
    def __init__(self) -> None:
        self._sources: dict[str, DataSource] = {}
        self._builtin: set[str] = set()

    def register(self, source: DataSource, *, builtin: bool = True) -> DataSource:
        if not source.id:
            raise ValueError("DataSource.id is required")
        if source.id in self._sources:
            raise ValueError(f"duplicate source id: {source.id}")
        self._sources[source.id] = source
        if builtin:
            self._builtin.add(source.id)
        return source

    def is_builtin(self, source_id: Optional[str]) -> bool:
        """True for sources shipped in this repository; third-party plugins register with builtin=False."""
        return (source_id or "") in self._builtin

    def get(self, source_id: Optional[str]) -> Optional[DataSource]:
        return self._sources.get(source_id or "")

    def __iter__(self):
        return iter(self._sources.values())

    def ids(self) -> list[str]:
        return list(self._sources)

    def recheckable_ids(self) -> list[str]:
        return [s.id for s in self if s.recheckable]

    def sources_view(self, steam: Mapping) -> dict[str, dict]:
        """Uniform {id: SourceResult-dict} for a stored `steam` dict (works on legacy records)."""
        out: dict[str, dict] = {}
        for s in self:
            r = s.result_from(steam)
            if r is not None:
                out[s.id] = r.to_dict()
        return out

    def coverage(self, steam: Mapping) -> dict[str, bool]:
        out: dict[str, bool] = {}
        for s in self:
            if not s.in_coverage:
                continue
            r = s.result_from(steam)
            out[s.id] = bool(r and r.ok)
        return out
