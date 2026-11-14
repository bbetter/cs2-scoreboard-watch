# Repository Agent Rules

- **Dedicated commit per subagent feature:** Whenever a subagent implements a feature or task in this repository, its changes MUST be committed as a separate, dedicated commit (do not bundle multiple subagent features into a single commit).
- **Never commit secrets or local runtime state:** Ensure `config.json`, `.env`, `profile_cache.json`, `scans_history.json`, and `screenshots/` remain untracked and excluded via `.gitignore`.

## Architecture

- Start with `docs/ARCHITECTURE.md`: module map, request flow, data shapes and the recipes for adding a source, a rule, a capture tool, a config setting or a route.
- **Add a source = one file in `sources/` + one line in `sources/registry.py`** (and its settings keys in `config_schema.ORDER`, if it declares any).
- **Scoring changes:** edit `scoring/thresholds.py` and `scoring/rules.py`, then regenerate `tests/golden/scoring_golden.json` with `python tests/test_golden_scoring.py --regen` only for an intentional change, and review the diff before committing it.
- **Never edit golden values by hand.** Regenerate them with the `--regen` command of the test that owns them, and read the diff.
