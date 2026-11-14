# Repository Agent Rules

- **Dedicated commit per subagent feature:** Whenever a subagent implements a feature or task in this repository, its changes MUST be committed as a separate, dedicated commit (do not bundle multiple subagent features into a single commit).
- **Never commit secrets or local runtime state:** Ensure `config.json`, `.env`, `profile_cache.json`, `scans_history.json`, and `screenshots/` remain untracked and excluded via `.gitignore`.
