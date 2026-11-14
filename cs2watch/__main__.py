"""`python -m cs2watch [options]` and the `cs2watch` console script: the same options as `python watch_cs2.py`."""

from __future__ import annotations


def main() -> None:
    # Imported here, not at module level, so `import cs2watch` stays cheap.
    from watch_cs2 import main as run_watch

    run_watch()


if __name__ == "__main__":
    main()
