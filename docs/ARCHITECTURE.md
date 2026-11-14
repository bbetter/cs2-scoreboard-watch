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

## Module map

### `app/` (web server, pipeline, watcher)

| Module | Role |
|---|---|
| `app/server.py` | HTTP server: router table, route functions, scan history state, SSE, wiring of the pipeline |
| `app/watch_cs2.py` | Entry point for the TAB watcher: registers the Hyprland bind or a pynput listener, then serves `app.server.CsWatchHandler` |
| `app/web_router.py` | Stdlib router (`Router.route` / `fallback`), JSON body helper, static-file resolution |
| `app/pipeline.py` | `ScanPipeline`: scan / rescan / enrichment flow, the only code that writes scan records |
| `app/events.py` | `EventBus`: in-process topics (`scan.created`, `scan.updated`, `player.enriched`, `scan.finished`) |
| `app/console_parser.py` | Reads CS2 `console.log` for match id, map, and exact nicknames |

### `storage/` (config, databases, runtime paths)

| Module | Role |
|---|---|
| `paths.py` | `ROOT`, `DATA_DIR` (`data/`), `LOG_DIR`, `SCREENSHOT_DIR`, `WEB_DIR`: the only place runtime paths are defined |
| `storage/config_fields.py` | `Field` / `Secret` descriptors (no dependencies, importable by `sources/`) |
| `storage/config_schema.py` | The config schema: core keys, source-declared keys, normalize / merge / public-view rules |
| `storage/config_store.py` | `data/config.json` load/save and the public `GET /api/config` payload, thin over `config_schema` |
| `storage/atomic_io.py` | `atomic_write_text`: temp file + `os.replace` for all JSON state writes |
| `storage/player_db.py` | SQLite player history (`data/players.db`, tables `players` and `sightings`) |
| `storage/feedback_store.py` | Labelled feedback records (`data/feedback.json`) and their feature block |

### `vision/` (OCR)

| Module | Role |
|---|---|
| `vision/scoreboard_scan.py` | OCR of the TAB scoreboard screenshot into `Player` rows (`Player` dataclass) |

### `lookup/` (player lookup and AI)

| Module | Role |
|---|---|
| `lookup/steam_lookup.py` | Steam profile lookup, source fetchers (`_fetch_leetify`, `_fetch_faceit_stats`, `_fetch_cstracker`, `_fetch_steam_cs2_stats`), profile cache (`data/profile_cache.json`), `PlayerReport` |
| `lookup/nick_match.py` | Fuzzy nickname matching for OCR output |
| `lookup/ai_analyzer.py` | Optional AI stage (Claude CLI or Anthropic API) and the compact payload sent to it |

### `cs2platform/` (OS integration)

| Module | Role |
|---|---|
| `capture.py` | Screenshot tools: `CAPTURERS` registry (explicit config names) and `select_auto_tool` (auto mode per OS) |
| `focus.py` | Is CS2 the focused window (Windows API, macOS `osascript`, Hyprland `hyprctl`) |
| `notify.py` | Desktop notifications: `notify-send` (Linux), `osascript` (macOS), no-op on Windows |

### `sources/` (external stats providers)

| Module | Role |
|---|---|
| `base.py` | Contract: `DataSource`, `LegacySlotSource`, `SourceResult`, `SourceContext`, `SourceRegistry` |
| `registry.py` | `REGISTRY`: the built-in sources, one `register(...)` line each |
| `leetify.py` | Leetify (recheckable; declares `leetify_api_key`) |
| `faceit.py` | FACEIT (declares `faceit_api_key`) |
| `cstracker.py` | CSTracker.gg (recheckable; status may be `blocked`) |
| `steam_stats.py` | Steam CS2 lifetime stats (declares `steam_api_key`) |
| `_util.py` | Number rounding shared by the source features |

### `scoring/` (risk rules)

| Module | Role |
|---|---|
| `thresholds.py` | Every threshold and point value, one table |
| `rules.py` | One function per rule, registered with `@rule(component, name)` |
| `engine.py` | Runs the rules per component, applies the cheat/smurf post-processing, returns the breakdown (re-exported by the package as `compute_scores`, which `lookup.steam_lookup` imports) |

### `cs2watch/`

| Module | Role |
|---|---|
| `__main__.py` | `python -m cs2watch` and the `cs2watch` console script: calls `app.watch_cs2.main()` |

### Other

| Path | Role |
|---|---|
| `web/` | Static frontend (`index.html`, `app.js`, `styles.css`, `table-view.js`). Served from the repo root |
| `data/` | Runtime state, gitignored: `config.json`, `players.db`, `profile_cache.json`, `scans_history.json`, `feedback.json`, `screenshots/`, `logs/` |
| `scripts/cs_scan.sh` | Starts `app.server` if needed, takes a screenshot, posts it to `/api/scan` |
| `tests/` | `unittest` suite and golden files (see "Testing") |
| `docs/` | This document |
| `.env` | Optional secrets (API keys), read by `lookup/steam_lookup.py`; kept at the root and gitignored |

## Request flow

```
browser / scripts/cs_scan.sh
   │  HTTP
   ▼
app.server.CsWatchHandler.do_GET / do_POST
   │  urlparse(path).path
   ▼
app.web_router.Router.dispatch(handler, method, path)
   │  exact or template route  ──►  route function(h, **params) in app/server.py
   │  no route, GET only       ──►  fallback: static file under web/ (resolve + relative_to check)
   │  nothing                  ──►  send_error(404)
   ▼
route function
   ├─ reads JSON with app.web_router.read_json_body, checks state under app.server._STATE_LOCK
   ├─ scan work ──► app.pipeline.ScanPipeline
   │                 ├─ OCR (scoreboard_scan + console_parser)  ──► Player rows, player_uid assigned
   │                 ├─ lookup.steam_lookup.enrich_players (background thread unless spawn is synchronous)
   │                 │     └─ per player: lookup.steam_lookup.lookup_steam_profile → sources (REGISTRY) → scoring.engine
   │                 ├─ lookup.ai_analyzer.analyze_players_with_ai  (only when ai_enabled)
   │                 └─ EventBus.publish(topic, payload)
   │                       ├─ app.server._sink_player_db   (scan.finished)  → player_db
   │                       └─ app.server._sink_sse         (all topics)     → subscribers of /api/events
   └─ storage: app.server._save_history_locked → data/scans_history.json (atomic_io)
```

SSE: `GET /api/events` keeps one connection open. The `_SUBSCRIBERS` queues receive pre-formatted messages from
`app.server._broadcast_event`. Topics map to SSE event names: `scan.created`, `scan.updated`, `scan.finished` → `scan`;
`player.enriched` → `scan_update`.

Routes (from `app.server.router.routes()`; `tests/test_router.py` freezes this list):

| Method | Path | Route function |
|---|---|---|
| GET | `/api/steam-status` | `_get_steam_status` |
| GET | `/api/latest` | `_get_latest` |
| GET | `/api/config` | `_get_config` |
| GET | `/api/config/schema` | `_get_config_schema` |
| GET | `/api/matches` | `_get_matches` |
| GET | `/api/scan/{scan_id}` | `_get_scan` |
| GET | `/api/players/{steamid}` | `_get_player` |
| GET | `/screenshots/{name}` | `_get_screenshot` |
| GET | `/api/events` | `_get_events` (SSE) |
| GET | anything else | `_get_static` (fallback) |
| POST | `/api/tab-pressed`, `/api/scan`, `/api/rescan`, `/api/setup-cs2`, `/api/recheck`, `/api/feedback`, `/api/recheck-source`, `/api/ai-analyze`, `/api/config`, `/api/upload` | `_post_*` |
| POST | `/api/players/{steamid}` | `_post_player` |

`POST /api/recheck`, `/api/recheck-source`, `/api/feedback` and `/api/ai-analyze` accept `player_uid` (preferred,
stable) or `nick` (first match, legacy). `/api/feedback` still requires `nick`; see Known limitations.

## Data shapes

### Scan record (`data/scans_history.json`, `GET /api/latest`, SSE `scan`)

```
id, created_at, date, match_id, map_name, image_name, image_url,
duration_ms, player_count, players: [PlayerReport dict, ...],
ai_error, scan_status: "loading_steam" | "loading_ai" | "done",
progress: {done, total}
```

Newest first, at most `app.server._MAX_HISTORY` (100) records. Written only through `app.server._save_history_locked`.

### Player report (an element of `players`)

`PlayerReport` (lookup/steam_lookup.py), serialised with `asdict`:

- `player_uid`: `"<scan_id>-<row index>"`, assigned by the pipeline (`app.pipeline.assign_player_uids`). Stable through
  enrichment, rechecks and AI. Legacy rows get the same form on load (`app.server._adapt_legacy_scan`), which can collide
  with the uids of a later rescan of the same scan (see Known limitations).
- `nick, kills, deaths, kd, hs, level, anomaly_score` (OCR values)
- `cheat_probability, risk_level, smurf_probability, smurf_risk_level, uncertainty, effective_weights, reasons`
- `score_breakdown`: list of `{component, rule, points, reason}` for every rule hit. `points` is `null` for the
  ban floor, the damping and the smurf branch markers.
- `loading`, `loading_stage` (`steam` / `ai` / `done`): UI progress flags.
- `steam`: the per-player external data (below).

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
`anthropic_api_key`) are replaced in the API response by `has_*`, `*_source` (`config` / `env` / `none`) and
`*_masked`. `leetify_api_key` is the exception: it is returned in `config` as stored (see Known limitations).
`GET /api/config/schema` returns the descriptors (names, types, limits, defaults, env var names), never values.

## Recipes

### (a) Add an external data source

1. Create `sources/<name>.py`:

   ```python
   from storage.config_fields import Secret
   from sources.base import DataSource, SourceContext, SourceResult

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
           from lookup import steam_lookup        # late import, as in sources/leetify.py
           return steam_lookup._fetch_example(steamid64)   # write this fetcher in lookup/steam_lookup.py

       def is_enabled(self, ctx):
           return bool(ctx.steam_api_key)  # or read the key the same way steam_lookup does

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

What updates automatically: `coverage`, `steam.sources`, `POST /api/recheck-source` (when `recheckable`),
`GET /api/config/schema` and the config key list.
What does **not** update automatically: the AI payload (`lookup.ai_analyzer._compact_player_for_ai` names Leetify and
FACEIT explicitly), feedback features (`storage.feedback_store.build_feedback_entry`), scoring rules, and the frontend
(`web/app.js` hard-codes source names). Each of those needs an explicit edit.

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
`(argv, subprocess kwargs)` or `None`. `send_notification` runs it and swallows errors.

### (d) Add a config setting

1. In `storage/config_schema.py`, add a `Field` to `CORE_FIELDS`, for example
   `Field("example_limit", "int", 5, label="Example limit", group="scan", min=1, max=50)`.
   Types: `float`, `int`, `bool`, `text`, `enum` (with `choices`), `str`, `weights`.
2. Add `"example_limit"` to `ORDER` in the position it should appear in the API and in `data/config.json`.
3. Read it with `storage.config_store.load_config().get("example_limit")` where it is used.
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
`test_stale_cache_fallback`) were not audited for real-file access.

## Known limitations and follow-ups

- **Frontend is not modular.** `web/app.js` is a single file, and source names (Leetify, CSTracker, CS2 stats) are
  hard-coded in it, for example in the source chips around lines 137–180. A new source needs a frontend change.
- **Legacy storage slots are canonical** for Leetify, FACEIT, CSTracker and Steam CS2 stats. `steam.sources` is a
  derived view. Moving to native storage would need a data migration and changes to scoring and the frontend.
- **Rescan can race with a stale enrichment.** A rescan keeps the scan id, so its `player_uid`s repeat. A slow
  enrichment from the previous run can write its rows into the new run and mark the scan `done`. Reproduced by
  `tests/test_player_uid.py::RaceExpectedFailureTests` (`expectedFailure`).
- **The final enrichment write can overwrite concurrent rechecks.** `app.pipeline.enrich_scan` replaces the whole
  `players` list at the end. A `/api/recheck` or `/api/ai-analyze` result written while enrichment is still running
  is lost. Read from the code; not yet covered by a test.
- **A failing enrichment leaves the scan in `loading_steam` or `loading_ai`.** Nothing catches exceptions from
  `enrich_players` or `analyze_players_with_ai` in `enrich_scan`, so the record never reaches `done` and no error is
  stored (it only goes to the thread's traceback). Read from the code; not yet covered by a test.
- **`/api/recheck` writes back by index.** The row index is found under one lock and used under another, so a
  concurrent change can put the result in the wrong row. Reproduced by `tests/test_player_uid.py` (`expectedFailure`).
- **`/api/ai-analyze` with only a nick and duplicate nicks** replaces the first duplicate row with the second
  one's result. Reproduced by `tests/test_player_uid.py::AiAnalyzeUidTests` (`expectedFailure`). The `player_uid`
  path is not affected.
- **`/api/feedback` requires `nick`** even when `player_uid` is sent, unlike `/api/recheck-source`. Pinned by a test.
- **Leetify key is returned unmasked.** `GET /api/config` puts `leetify_api_key` into `config` exactly as stored
  (`config_schema` has `masked=False` for it). Anthropic, Steam and FACEIT keys are masked. Pinned by
  `tests/golden/config_golden.json`.
- **Infinity in `data/config.json` resets all settings.** `load_config` catches the `OverflowError` raised by
  `int(inf)` and returns the defaults for every key. Saving `leetify_cache_ttl_sec = inf` raises `OverflowError`
  (a 500). Pinned by the config golden.
- **The 68 damping cap can never bind with real inputs**, and the 99 cheat ceiling cannot be reached with the
  default weights (see `tests/test_scoring_rules.py`). Both are exercised only with mocked weights.
- **Import has side effects.** `steam_lookup` opens `data/logs/pton.logs` in append mode when it is imported, which
  creates the directory and file if missing. `--help` on either entry point does this, and nothing else.
- **argparse shows `__main__.py` as the program name** under `python -m cs2watch`. Cosmetic.
- **`pip install` of this project is not self-contained.** `pyproject.toml` installs the packages listed in
  `[tool.setuptools]`, but `web/` and `data/` are resolved relative to the repository root (`storage/paths.py`), so an
  installed copy will not find the UI. `pip install -e .` is intended for development from the checkout. It has not been run.
- **Python version.** The code parses with the 3.8 feature set (checked with `ast.parse(feature_version=...)`), but
  only Python 3.11.9 is available here, so `requires-python = ">=3.11"` is the tested floor.
- **`playwright` is listed in `requirements.txt` but nothing in the repo imports it.**
- **Recheck-source calls `fetch_raw` directly**, so it skips `is_enabled`. `DataSource.fetch` does check it.
- **Scoring pieces with no effect:** scoring never reads cstracker `trust`, `vaclive`, `ttd`, or the medals / veteran
  coin / xp fields, even though the AI prompt mentions them (see `tests/golden/scoring_golden.json`, fixtures 14 and 20).
