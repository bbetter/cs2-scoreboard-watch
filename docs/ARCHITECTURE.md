# Architecture

CS2 Scoreboard Watch reads the CS2 TAB scoreboard from a screenshot, OCRs the players, looks up their
Steam/Leetify/FACEIT/CSTracker data, scores them with a rule engine and shows the result in a local web UI.

The code is organised into packages under the repository root: `app/` (web server, pipeline, watcher),
`storage/` (config, SQLite DB, JSON stores, runtime paths), `vision/` (OCR), `lookup/` (player lookup and AI),
plus `cs2platform/` (OS integration), `sources/` (external stats providers), `scoring/` (risk rules) and the thin
entry package `cs2watch/`. Runtime state lives under `data/` (gitignored); the root holds only code, config
files and `.env`.

## Running

| What | Command (from the repository root) |
|---|---|
| TAB watcher + web UI (the usual entry point) | `.venv/bin/python -m cs2watch` (same as `python -m app.watch_cs2`) |
| Web UI + scanner only (default port 8765, `127.0.0.1`) | `.venv/bin/python -m app.server` (also what `scripts/cs_scan.sh` starts, with output in `data/logs/server.log`) |
| Options (watcher) | `--host`, `--port`, `--delay`, `--cooldown`, `--all-windows` |

The watcher takes `--delay` / `--cooldown` from `data/config.json` when the flag is absent. `app.server` uses
fixed defaults (0.5 s, 4.0 s) and does not register a TAB hook.

Runtime paths come from `storage/paths.py`, read once at import: `CS2WATCH_DATA_DIR` replaces `data/` (logs and
screenshots follow it) and `CS2WATCH_WEB_DIR` replaces `web/`. Unset or empty means the default. Use absolute paths:
`scripts/cs_scan.sh` uses `CS2WATCH_DATA_DIR` as given, while the server it starts resolves a relative value against
its own working directory (the repository root).

## Module map

### `app/` (web server, pipeline, watcher)

| Module | Role |
|---|---|
| `app/server.py` | HTTP server: router table, route functions, the scan store `STORE`, the SSE hub `SSE`, wiring of the pipeline |
| `app/tab_watcher.py` | `TabWatcher`: state of the TAB watcher (cooldown, delay, CS2-only flag, notification switch) and the delayed scan + notification. Dependencies are passed to the constructor; `app.server` holds the instance as `TAB` |
| `app/sse.py` | `SseHub`: subscriber queues for `/api/events`, max 8 open streams (`subscribe()` returns `None` when full), overflowing queues dropped on broadcast and their stream ends (close marker) |
| `app/watch_cs2.py` | Entry point for the TAB watcher: registers the Hyprland bind or a pynput listener, then serves `app.server.CsWatchHandler` |
| `app/web_router.py` | Stdlib router (`Router.route` / `fallback`), JSON body helper, static-file resolution |
| `app/pipeline.py` | `ScanPipeline`: scan / rescan / enrichment flow, the only code that writes scan records |
| `app/scan_store.py` | `ScanStore`: scan list (newest first), its lock and the history file `data/scans_history.json`. A file that is unreadable, or whose records fail to adapt on load, is moved to `.corrupt` and logged |
| `app/events.py` | `EventBus`: in-process topics (`scan.created`, `scan.updated`, `player.enriched`, `scan.finished`) |
| `app/player_ops.py` | Player-row operations without HTTP: `find_player`, `replace_player_by_uid`, `mark_edited`, `recheck_source` (one source for one player, returns `(status, body)`), source refresh. Takes the store, registry and callbacks as parameters; `app.server` wraps them with the live `STORE` / `REGISTRY` |
| `app/matches.py` | `build_matches(scans)`: groups scan records into matches by `match_id` (fallback `session_<date>`), pure function |
| `app/console_parser.py` | Reads CS2 `console.log` for match id, map, and exact nicknames |
| `app/scan_migrations.py` | Scan record versions: `CURRENT_SCHEMA_VERSION` (2), `MIGRATIONS` (v1 → v2 adds `steam.sources` and `player_uid`), and `migrate(scan, registry)`, applied on load by `app.server._adapt_legacy_scan`. A record with a newer version is left unchanged, with one warning per process |

### `storage/` (config, databases, runtime paths)

| Module | Role |
|---|---|
| `paths.py` | `ROOT`, `DATA_DIR` (`CS2WATCH_DATA_DIR`, default `data/`), `LOG_DIR` and `SCREENSHOT_DIR` (both under `DATA_DIR`), `WEB_DIR` (`CS2WATCH_WEB_DIR`, default `web/`): the only place runtime paths are defined. Values are computed at import; nothing is created |
| `storage/config_fields.py` | `Field` / `Secret` descriptors (no dependencies, importable by `sources/`) |
| `storage/config_schema.py` | The config schema: core keys, source-declared keys, normalize / merge / public-view rules |
| `storage/config_store.py` | `data/config.json` load/save and the public `GET /api/config` payload, thin over `config_schema` |
| `storage/atomic_io.py` | `atomic_write_text`: temp file + `os.replace` for all JSON state writes |
| `storage/player_db.py` | SQLite player history (`data/players.db`, tables `players` and `sightings`) |
| `storage/feedback_store.py` | Labelled feedback records (`data/feedback.json`) and their feature block |
| `storage/profile_cache.py` | `ProfileCache`: Steam profile cache (`data/profile_cache.json`), read on first use, not at import |
| `storage/api_keys.py` | Steam Web API and FACEIT key lookup: config, then environment, then the root `.env`. Shell rc files are not read. Callers import it from here directly (`lookup/steam_lookup.py` does not re-export it) |

### `vision/` (OCR)

| Module | Role |
|---|---|
| `vision/scoreboard_scan.py` | OCR of the TAB scoreboard screenshot into `Player` rows (`Player` dataclass) |

### `lookup/` (player lookup and AI)

| Module | Role |
|---|---|
| `lookup/steam_lookup.py` | Orchestration only: `lookup_steam_profile`, `enrich_players`, `recheck_player_report`, retry of rate-limited lookups, `SteamProfile`, `PlayerReport`, the scoring call; no re-exports. Fetchers are in `sources/*`, search in `lookup/steam_search.py`. The Leetify lookup memo is `LEETIFY_MEMO = TtlMemo()` |
| `lookup/steam_search.py` | Steam Community search, XML profile parsing, candidate scoring, Web API batch. `SteamRateLimiter` (429 cooldown and minimum interval) is held by the module-level `RATE_LIMITER`. Does not import `steam_lookup` |
| `lookup/ttl_memo.py` | `TtlMemo`: thread-safe in-memory TTL memo for dict values. Returns copies, never writes to disk, and treats `ttl <= 0` as disabled |
| `lookup/http_client.py` | Plain HTTP helpers: `http_get`, `http_get_status`, `USER_AGENT` (standard library only). curl gets the URL, headers and cookie through stdin (`-K -`), so API keys are not visible in the process list |
| `lookup/nick_match.py` | Fuzzy nickname matching for OCR output |
| `lookup/ai_analyzer.py` | Optional AI stage (Claude CLI or Anthropic API) and the compact payload sent to it. Model aliases are in `ANTHROPIC_MODEL_ALIASES` (`resolve_anthropic_model`) |

### `cs2platform/` (OS integration)

| Module | Role |
|---|---|
| `capture.py` | Screenshot tools: `CAPTURERS` registry (explicit config names) and `select_auto_tool` (auto mode per OS) |
| `focus.py` | Is CS2 the focused window (Windows API, macOS `osascript`, Hyprland `hyprctl`) |
| `notify.py` | Desktop notifications: `notify-send` (Linux), `osascript` (macOS), no-op on Windows |

### `sources/` (external stats providers)

| Module | Role |
|---|---|
| `base.py` | Contract: `DataSource`, `LegacySlotSource`, `SourceResult`, `SourceContext`, `SourceRegistry`. `payload_key` / `payload_always` control the source's block in the AI payload and feedback features |
| `registry.py` | `REGISTRY`: the built-in sources, one `register(...)` line each, then third-party plugins from the `cs2watch.sources` entry point group (`load_plugins`). `CS2WATCH_DISABLE_PLUGINS=1` skips plugins |
| `leetify.py` | Leetify (recheckable; declares `leetify_api_key`); fetcher `fetch_leetify` |
| `faceit.py` | FACEIT (declares `faceit_api_key`); fetcher `fetch_faceit_stats` |
| `cstracker.py` | CSTracker.gg (recheckable; status may be `blocked`); fetcher `fetch_cstracker` |
| `steam_stats.py` | Steam CS2 lifetime stats (declares `steam_api_key`); fetcher `fetch_steam_cs2_stats` |
| `_util.py` | Numeric helpers shared by the source features and `scoring/rules.py` (`num_or_none`, `round_num`, `_safe_float`, `_safe_int`) |

Each source module holds its own fetcher and does not import `lookup.steam_lookup`.

### `scoring/` (risk rules)

| Module | Role |
|---|---|
| `thresholds.py` | Every threshold and point value, one table |
| `rules.py` | One function per rule, registered with `@rule(component, name)` (including `cstracker_trust`) |
| `engine.py` | Runs the rules per component, applies the cheat/smurf post-processing, returns the breakdown (re-exported by the package as `compute_scores`, which `lookup.steam_lookup` imports) |

### `cs2watch/`

| Module | Role |
|---|---|
| `__main__.py` | `python -m cs2watch` and the `cs2watch` console script: calls `app.watch_cs2.main()` |

### Other

| Path | Role |
|---|---|
| `web/` | Static frontend: `index.html`, `styles.css` and the scripts `app.js`, `api.js`, `state.js`, `sources.js`, `scan-table.js`, `player-details.js`, `lobby.js`, `settings.js`, `attention.js`, `table-view.js`, `util.js`. Served from the repo root |
| `data/` | Runtime state, gitignored: `config.json`, `players.db`, `profile_cache.json`, `scans_history.json`, `feedback.json`, `screenshots/`, `logs/` |
| `scripts/cs_scan.sh` | Starts `app.server` if needed, takes a screenshot, posts it to `/api/scan` |
| `tests/` | `unittest` suite and golden files (see "Testing") |
| `docs/` | This document |
| `.env` | Optional secrets (API keys), read by `storage/api_keys.py`; kept at the root and gitignored. Steam key: config → `STEAM_API_KEY` → root `.env`; shell rc files are not read |

## Request flow

```
browser / scripts/cs_scan.sh
   │  HTTP
   ▼
app.server.CsWatchHandler.do_GET / do_POST
   │  CsWatchHandler._guard → app.web_router.check_request (403 / 415, see "Security model")
   │  urlparse(path).path
   ▼
app.web_router.Router.dispatch(handler, method, path)
   │  exact or template route  ──►  route function(h, **params) in app/server.py
   │  no route, GET only       ──►  fallback: static file under web/ (resolve + relative_to check)
   │  nothing                  ──►  send_error(404)
   ▼
route function
   ├─ reads JSON with app.web_router.read_json_body, checks state under app.server.STORE.lock
   ├─ row logic (find / replace by player_uid, single-source recheck) ──► app.player_ops; match listing ──► app.matches
   │   (routes stay thin; `app.server` keeps the wrappers `_player_matches`, `recheck_source_for_player`, ...)
   ├─ scan work ──► app.pipeline.ScanPipeline
   │                 ├─ OCR (scoreboard_scan + console_parser)  ──► Player rows, player_uid assigned
   │                 ├─ lookup.steam_lookup.enrich_players (background thread unless spawn is synchronous)
   │                 │     └─ per player: lookup.steam_lookup.lookup_steam_profile → lookup.steam_search → sources (REGISTRY) → scoring.engine
   │                 ├─ lookup.ai_analyzer.analyze_players_with_ai  (only when ai_enabled)
   │                 └─ EventBus.publish(topic, payload)
   │                       ├─ app.server._sink_player_db   (scan.finished)  → player_db
   │                       └─ app.server._sink_sse         (all topics)     → subscribers of /api/events
   └─ storage: app.server.STORE (ScanStore.save_locked) → data/scans_history.json (atomic_io)
```

SSE: `GET /api/events` keeps one connection open per subscriber. `app.server.SSE` is an `app.sse.SseHub` that holds the
subscriber queues and their lock (separate from the scan lock). `app.server._broadcast_event` formats each message and
calls `SSE.broadcast`. The hub allows at most 8 open streams; a ninth `GET /api/events` gets 503
`{"error": "Too many event streams"}` before any SSE headers are sent. Topics map to SSE event names: `scan.created`,
`scan.updated`, `scan.finished` → `scan`; `player.enriched` → `scan_update`.

Routes (from `app.server.router.routes()`; `tests/test_router.py::RouteTableTests.EXPECTED` freezes this list):

| Method | Path | Route function |
|---|---|---|
| GET | `/api/steam-status` | `_get_steam_status` |
| GET | `/api/sources` | `_get_sources` (registry metadata: `id`, `label`, `recheckable`, `in_coverage`) |
| GET | `/api/latest` | `_get_latest` |
| GET | `/api/config` | `_get_config` |
| GET | `/api/config/schema` | `_get_config_schema` |
| GET | `/api/matches` | `_get_matches` |
| GET | `/api/scan/{scan_id:path}` | `_get_scan` |
| GET | `/api/players/{steamid:path}` | `_get_player` |
| GET | `/screenshots/{name:path}` | `_get_screenshot` |
| GET | `/api/events` | `_get_events` (SSE) |
| GET | anything else | `_get_static` (fallback) |
| POST | `/api/tab-pressed`, `/api/scan`, `/api/rescan`, `/api/setup-cs2`, `/api/recheck`, `/api/feedback`, `/api/recheck-source`, `/api/ai-analyze`, `/api/config`, `/api/upload` | `_post_*` |
| POST | `/api/players/{steamid:path}` | `_post_player` |

`POST /api/recheck`, `/api/recheck-source`, `/api/feedback` and `/api/ai-analyze` accept `player_uid` (preferred,
stable) or `nick` (first match, legacy). `/api/feedback` accepts `player_uid` or `nick`, and at least one is required.
`/api/recheck` and `/api/ai-analyze` write results back by `player_uid` only, under the lock, into the current rows.

## Data shapes

### Scan record (`data/scans_history.json`, `GET /api/latest`, SSE `scan`)

```
schema_version (2; a record without it is version 1 and is migrated on load), id, run_id, created_at, date, match_id,
map_name, image_name, image_url, duration_ms, player_count, players: [PlayerReport dict, ...],
ai_error, enrich_error (`null` or `"<ExceptionType>: <message>"`), scan_status: "loading_steam" | "loading_ai" | "done",
progress: {done, total}
```

`run_id` is `uuid4().hex[:8]`, set by `ScanPipeline.run_new` and replaced by every `rescan` (`app.pipeline`). Background
enrichment writes to the record only while its `run_id` still matches (`ScanPipeline._find_run`), so an older run cannot
write into a newer one even though the scan `id` and the `player_uid`s are the same.

Newest first, at most `app.server._MAX_HISTORY` (100) records. Written only through `ScanStore.save_locked` (`app.server.STORE`).

### Player report (an element of `players`)

`PlayerReport` (lookup/steam_lookup.py), serialised with `asdict`:

- `player_uid`: `"<scan_id>-<row index>"`, assigned by the pipeline (`app.pipeline.assign_player_uids`). Stable through
  enrichment, rechecks and AI. Legacy rows get the same form on load (`app.scan_migrations`, v1 → v2), which can collide
  with the uids of a later rescan of the same scan (see Known limitations).
- `nick, kills, deaths, kd, hs, level, anomaly_score` (OCR values)
- `cheat_probability, risk_level, smurf_probability, smurf_risk_level, uncertainty, effective_weights, reasons`
- `score_breakdown`: list of `{component, rule, points, reason}` for every rule hit. `points` is `null` for the
  ban floor, the damping and the smurf branch markers.
- `loading`, `loading_stage` (`steam` / `ai` / `done`): UI progress flags.
- `steam`: the per-player external data (below).
- `edited_at` (float, Unix seconds): set only on rows that a hand-edit route wrote (`POST /api/recheck`,
  `/api/recheck-source`, `/api/ai-analyze`, via `app.server._mark_edited`). Absent on every other row. An enrichment run
  keeps a row whose `edited_at` is at or after the run's start (`ScanPipeline._keep_user_row`).

### `steam` dict

Legacy slots, which stay canonical because scoring and the current frontend read them:
`steam.leetify`, `steam.faceit`, `steam.cs2_stats`, `steam.extra_stats.cstracker`, plus profile fields
(`steamid64`, `privacy_state`, `vac_banned`, `cs2_hours`, `account_age_years`, ...).

Derived, written at lookup and recheck time:
- `coverage`: `{steam, leetify, faceit, cstracker}` booleans (from `REGISTRY.coverage`).
- `sources`: `{<source id>: {status, data, fetched_at}}` from `REGISTRY.sources_view`. `fetched_at` is `""` for
  legacy-slot sources because no timestamp is stored there. An entry exists whenever the slot is present, even if it
  is empty: an empty default (`faceit: {}`, `cs2_stats: {}`) reads as `not_found`, not `unavailable`. A missing slot
  (`None`) has no entry. See Known limitations.

A source with no legacy slot stores its raw data in `steam.sources[<id>].data` (the default `DataSource` slot).

### Config (`data/config.json`, `GET /api/config`)

Keys and order come from `storage.config_schema.ORDER`. Secrets (`steam_api_key`, `faceit_api_key`,
`anthropic_api_key`, `leetify_api_key`) are replaced in the API response by `has_*` and `*_masked`; keys with an
environment variable also get `*_source` (`config` / `env` / `none`).
`GET /api/config/schema` returns the descriptors (names, types, limits, defaults, env var names), never values.

## Recipes

### (a) Add an external data source

1. Create `sources/<name>.py`:

   ```python
   from storage.config_fields import Secret
   from sources.base import DataSource, SourceContext, SourceResult

   def fetch_example(steamid64):
       ...   # HTTP through lookup.http_client; do not import lookup.steam_lookup here

   class ExampleSource(DataSource):
       id = "example"                 # key in steam["sources"] and in coverage
       label = "Example"
       in_coverage = True             # appears in steam.coverage
       recheckable = True             # allowed in POST /api/recheck-source
       settings = (
           Secret("example_api_key", "text", "", label="Example API key", group="api_keys",
                  env=("EXAMPLE_API_KEY",)),
       )

       def fetch_raw(self, steamid64, ctx):
           return fetch_example(steamid64)        # the fetcher lives in this module

       def is_enabled(self, ctx):
           return bool(ctx.steam_api_key)  # or read the key via storage.api_keys, as sources/faceit.py does

       def normalize(self, raw):
           return SourceResult("ok" if raw else "not_found", raw if isinstance(raw, dict) else {})
   ```

   For a source that already has a legacy slot, subclass `LegacySlotSource` and set `slot = ("...",)`, as
   `sources/leetify.py` does.
2. Register it: one line in `sources/registry.py`:
   `REGISTRY.register(ExampleSource())`.
3. Add the setting key to `storage.config_schema.ORDER` (a fixed tuple). `_build_schema` raises if a declared key is
   missing from it, so this step is not optional.
4. Run `tests/test_sources.py` and `tests/test_config_schema.py`. Then `tests/test_config_parity.py` will fail
   with new keys in `config`. Regenerate its golden only if the change is intentional (see Testing).

A scan fetches every registered `DataSource` that is not a `LegacySlotSource` (`lookup.steam_lookup._fetch_registry_sources`,
run once per player on the lookup path and on the manual-report path), and stores the result in `steam.sources`. The four
built-in legacy sources keep their dedicated fetchers (`_fetch_external_stats`). A `LegacySlotSource` is fetched only by its
own code path, so a new source that needs a historical slot must be wired there too.

What updates automatically: `coverage`, `steam.sources`, `POST /api/recheck-source` (when `recheckable`),
`GET /api/config/schema`, the config key list, the AI payload (`lookup.ai_analyzer`) and the feedback features
(`storage.feedback_store.build_feedback_entry`), both through `REGISTRY`. A source's block there is named by `payload_key`
(default: its `id`; `None` = no block) and is emitted when it has content or when `payload_always` is set. The UI label
of a source without a built-in entry (generic chips, tabs and retry messages) comes from `GET /api/sources`.
What does **not** update automatically: scoring rules (`scoring/rules.py`, which read the legacy slots), and the names of
built-in sources in the frontend (`lobby.js`, `scan-table.js` and `player-details.js` still write Leetify, CSTracker, CS2
stats and FACEIT out by hand). Each of those needs an explicit edit.

#### As a plugin package

A third-party package adds a source without touching this repository. Declare an entry point in the group
`cs2watch.sources`, pointing at a `DataSource` subclass (or an instance):

```toml
[project.entry-points."cs2watch.sources"]
example = "my_package.sources:ExampleSource"
```

Plugin settings become config keys after the built-in ones in `ORDER`. A key that clashes with an existing one is
skipped with a warning. Plugins are loaded by `sources.registry.load_plugins`. A plugin that fails to load, or that is
not a `DataSource`, is logged and skipped, and a duplicate id is skipped too. `CS2WATCH_DISABLE_PLUGINS=1` turns loading off.

### (b) Add a scoring rule

1. Add the numbers to `scoring/thresholds.py`, for example `EXAMPLE_MIN = 80` and `EXAMPLE_POINTS = 10.0`.
2. Add the rule to `scoring/rules.py`:

   ```python
   @rule("external", "example_signal")   # component: scoreboard | steam_account | external
   def _example(c: ScoreContext) -> list:
       if not c.lf:
           return []
       if (c.lf.get("aim") or 0) >= T.EXAMPLE_MIN:
           return [Hit(T.EXAMPLE_POINTS, f"Example signal ({c.lf['aim']})")]
       return []
   ```

   Rules run in registration order, and their reasons are reported in that order.
3. Add a test class to `tests/test_scoring_rules.py`: subclass `RuleCase`, set `COMPONENT` and `NAME`, and test the
   not-applicable case, each tier boundary (using `T.*`), and the exact points and reason text. The registry test
   fails until the class exists.
4. Regenerate the scoring golden **only** for an intentional scoring change, after reading the diff:
   `.venv/bin/python tests/test_golden_scoring.py --regen`, then `git diff tests/golden/scoring_golden.json`.
   Never edit golden values by hand. If the diff shows changes you did not intend, the rule is wrong, not the golden.

### (c) Add a screenshot tool or a notifier

Screenshot tool (`cs2platform/capture.py`):

```python
def _capture_example(out_path: Path) -> Path:
    _run_checked(["example-tool", str(out_path)], out_path)   # raises RuntimeError on failure
    return out_path

CAPTURERS["example"] = _capture_example   # the dict literal near the end of capture.py
```

Also add `"example"` to `ALLOWED_SCREENSHOT_TOOLS` in `storage/config_schema.py`. Without that, the config layer resets the
value to `auto`. Auto mode uses `select_auto_tool`, so add a branch there only if the tool should be picked
automatically.

Notifier (`cs2platform/notify.py`): add a platform branch in `build_notification_command`, returning
`(argv, subprocess kwargs)` or `None`. `send_notification` runs it and swallows errors. Pass user text to notifier
commands as separate argv items, never inside a script string (see the macOS branch).

### (d) Add a config setting

1. In `storage/config_schema.py`, add a `Field` to `CORE_FIELDS`, for example
   `Field("example_limit", "int", 5, label="Example limit", group="scan", min=1, max=50)`.
   Types: `float`, `int`, `bool`, `text`, `enum` (with `choices`), `str`, `weights`.
2. Add `"example_limit"` to `ORDER` in the position it should appear in the API and in `data/config.json`.
3. Read it with `storage.config_store.load_config().get("example_limit")` where it is used. A non-numeric or
   non-finite value (`inf`, `nan`) for a `float` or `int` field falls back to the field default (`_normalize_field`).
4. Regenerate `tests/golden/config_golden.json` if the change is intentional (see Testing).

### (e) Add an HTTP route

In `app/server.py`, after the existing route functions:

```python
@router.route("GET", "/api/example/{item_id}")
def _get_example(h: CsWatchHandler, item_id: str) -> None:
    h._send_json({"item": item_id})
```

Then add the `("GET", "/api/example/{item_id}")` pair to `RouteTableTests.EXPECTED` in `tests/test_router.py`.
That test freezes the route table on purpose: a new route must be added there too.
Path parameters are the raw, undecoded URL segments. Use `read_json_body(h)` for JSON bodies.

## Testing

Run the whole suite from the repository root:

```
.venv/bin/python -m unittest discover -s tests
```

Lint: `.venv/bin/ruff check .` (rules `F`, `E9`, `I`, `UP` and `B`, configured in `pyproject.toml`; import order uses the
first-party package list there).
Types: `.venv/bin/mypy` checks `storage`, `sources` and `scoring` (configuration in `pyproject.toml`).
Development dependencies, including ruff and mypy, are in `requirements-dev.txt`. CI (`.github/workflows/tests.yml`) runs
the lint, the type check (3.11 only) and the suite on Python 3.11 and 3.12 (both required; auto-merge waits for them).

Golden files (regenerate only for an intentional change, and review the diff):

| File | Freezes | Test | Regenerate |
|---|---|---|---|
| `tests/golden/scoring_golden.json` | Scores, breakdown, uncertainty and weights for 27 synthetic players plus 4 recheck cases; each player's `steam` dict and `steam.sources` | `test_golden_scoring.py` | `python tests/test_golden_scoring.py --regen` |
| `tests/golden/ai_compact_golden.json` | `lookup.ai_analyzer._compact_player_for_ai` output | `test_ai_compact_parity.py` | `python tests/test_ai_compact_parity.py --regen` |
| `tests/golden/feedback_golden.json` | `storage.feedback_store.build_feedback_entry` output (without `ts`) | `test_feedback_parity.py` | `python tests/test_feedback_parity.py --regen` |
| `tests/golden/config_golden.json` | `config_store` load / save / public view over a matrix of inputs, in key order | `test_config_parity.py` | `python tests/test_config_parity.py --regen` |

Shared player cases are in `tests/parity_cases.py`. All fixtures use fake keys (`TESTKEY` in the value); a test
checks that. The tests added in the recent steps write their state to temporary files (history, feedback, config,
player database, profile cache) and mock the network. The scoring golden pins the AI weight defaults, so it does not
depend on `data/config.json`. The older tests (`test_scoring_f4`, `test_nick_match`, `test_steam_queue`,
`test_stale_cache_fallback`) are isolated the same way: config reads are patched with fresh-install defaults and the
profile cache is pointed at a temporary file. `tests/test_data_dir_untouched.py` snapshots `data/` around those four
modules and fails on any created, removed or modified file.

## Security model of the local server

The server binds to `127.0.0.1` by default. It has no authentication: the checks below only stop DNS rebinding and
cross-site requests. `app.web_router.check_request` runs for every request, before routing, from
`CsWatchHandler._guard`:

- **Host:** must be `localhost` or an IP literal (IPv4 or IPv6). Any other name gets 403 `Host not allowed`.
- **Origin**, when sent: must be `http://` with the same host and port as the Host header. `null` or anything else gets
  403 `Origin not allowed`.
- **Sec-Fetch-Site**, when sent: `same-origin` or `none`. Anything else gets 403 `Cross-site request blocked`.
- **Content-Type** on a POST with a body: `application/json` for all routes except `/api/upload`, which accepts
  `image/png`, `image/jpeg` and `application/octet-stream`. Anything else gets 415 `Unsupported Content-Type`. Bodyless
  POSTs skip this rule.
- A rejected request gets a JSON error and its connection is closed.
- **Plugins:** `cs2watch.sources` entry points run with the same trust as any installed package (`pip install`). A broken
  plugin is logged and skipped, never fatal (see the recipe for plugin packages).
- **SSE limit:** at most 8 open `GET /api/events` streams (`app.sse.SseHub`). Each stream holds a server thread, so a
  ninth one gets 503 `Too many event streams` and no stream is opened.

Scan files: `POST /api/scan` accepts an `image_path` only if it resolves to a `.png`, `.jpg` or `.jpeg` file inside the
configured screenshots directory or `SCREENSHOT_DIR` (`data/screenshots`). Otherwise it returns 400; a missing file
returns 404. `POST /api/upload` checks the PNG or JPEG signature before it writes anything and returns 400 otherwise.

AI prompt: player data is sent inside `<players_data>` with `<`/`>` escaped and an instruction to treat it as data.
Nicks and profile text come from third parties, so `lookup.ai_analyzer._data_block` escapes `<` and `>` as `<` and
`>` (still valid JSON), and `_build_prompt` tells the model that the block is untrusted and must not be followed
as instructions. Covered by `tests/test_ai_prompt.py`.

## Known limitations and follow-ups

- **Built-in source names are hard-coded in the frontend.** Leetify, CSTracker, CS2 stats and FACEIT names are still
  written into `lobby.js`, `scan-table.js` and `player-details.js`. A new source (built-in or plugin) gets a generic
  chip and tab with its registry label from `GET /api/sources`, without a frontend change.
- **Legacy storage slots are canonical** for Leetify, FACEIT, CSTracker and Steam CS2 stats. `steam.sources` is a
  derived view. Moving to native storage would need a data migration (a future v3 step in `app/scan_migrations.py`) and
  changes to scoring and the frontend.
- **Enrichment keeps user edits only for rows matched by `player_uid`, and not during the AI stage.** A row edited
  by a hand-edit route after the run started is kept by the enrichment writes (`ScanPipeline._merge_rows`). The AI
  stage analyzes a snapshot, so a row edited while the AI stage runs keeps the user's version and gets no AI fields
  for that run. Covered by `tests/test_enrichment_merge.py`.
- **The 68 damping cap can never bind with real inputs**, and the 99 cheat ceiling cannot be reached with the
  default weights (see `tests/test_scoring_rules.py`). Both are exercised only with mocked weights.
- **argparse shows `__main__.py` as the program name** under `python -m cs2watch`. Cosmetic.
- **`pip install` of this project is not self-contained.** `pyproject.toml` installs the packages listed in
  `[tool.setuptools]`, but `web/` and `data/` default to the repository root (`storage/paths.py`). An installed copy needs
  `CS2WATCH_WEB_DIR` and `CS2WATCH_DATA_DIR` set. `pip install -e .` is intended for development from the checkout. It has not been run.
- **Python version.** The code parses with the 3.8 feature set (checked with `ast.parse(feature_version=...)`), but
  only Python 3.11.9 is available here, so `requires-python = ">=3.11"` is the tested floor. CI runs 3.11 and 3.12
  (both required). A 3.13 job was removed because `rapidocr-onnxruntime` has no 3.13 wheels.
- **Scoring pieces with no effect:** scoring reads cstracker `trust` (rule `cstracker_trust`), but never reads
  `vaclive`, `ttd`, or the medals / veteran coin / xp fields. The AI prompt does not mention fields that do not exist.
