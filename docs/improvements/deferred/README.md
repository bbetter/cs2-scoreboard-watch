# Відкладені задачі: гонки і адресація рядків

T11–T13 свідомо винесено з основного плану (рішення власника, 2026-11-14) і буде зроблено окремим етапом.
`taskctl.py` їх не бачить: він читає лише `docs/improvements/tasks/`.

| Задача | Що закриває | Тест-специфікація |
|---|---|---|
| T11 | rescan + застаріла enrichment | `tests/test_player_uid.py::RaceExpectedFailureTests::test_stale_enrichment_of_an_old_run_does_not_overwrite_a_rescan` |
| T12 | фінальний запис enrichment перетирає паралельні recheck/AI | — (тест пише сама задача) |
| T13 | `/api/recheck` за індексом, `/api/ai-analyze` з дублями ніків, `/api/feedback` без `nick`, `/api/recheck-source` без `is_enabled` | `AiAnalyzeUidTests::test_nick_path_keeps_each_duplicate_its_own_row`, `RaceExpectedFailureTests::test_recheck_route_writes_back_to_the_right_row_after_a_concurrent_reorder` |

Перед запуском етапу:

1. Звірити контекст задач з кодом після T09 (`ScanStore`), T10 (`enrich_error`), T14, T15 — номери рядків і назви
   змінились.
2. Перенести файли в `../tasks/`, поставити `depends_on` на останню змерджену задачу, що чіпає `app/server.py` /
   `app/pipeline.py`, і прогнати `python3 scripts/agents/taskctl.py validate`.
3. `taskctl.py board-init` додасть їх на борду, нічого не дублюючи.
