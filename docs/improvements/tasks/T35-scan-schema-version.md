---
id: T35
title: Version scan records and migrate them through an adapter chain
wave: 8
depends_on: [T34]
review: none
size: M
golden: []
files:
  - app/scan_migrations.py
  - app/server.py
  - app/pipeline.py
  - tests/test_scan_migrations.py
  - tests/test_pipeline.py
  - docs/ARCHITECTURE.md
---

# T35 — Версія запису скану і ланцюжок міграцій

## Мета

Записи в `data/scans_history.json` не мають версії. Старі записи підганяє `app/server._adapt_legacy_scan` (додає
`steam.sources` і `player_uid`), і щоразу перевіряє це заново. Наступна зміна формату (наприклад, перенесення
legacy-слотів у `steam.sources`) не матиме куди вбудуватися. Треба додати `schema_version` і явний ланцюжок
міграцій. Саме перенесення слотів у цій задачі не робиться.

## Контекст

- `app/server.py`: `_adapt_legacy_scan(scan)`, `_load_history()` → `STORE.load(adapt=_adapt_legacy_scan)`.
- `app/pipeline.py::run_new` будує новий запис словником.
- `app/scan_store.py::ScanStore.load` викликає `adapt` для кожного dict-запису.

## Що зробити

1. Створи `app/scan_migrations.py`: `CURRENT_SCHEMA_VERSION = 2`; `_v1_to_v2(scan)` — дослівно логіка
   `_adapt_legacy_scan` плюс `scan["schema_version"] = 2`; `MIGRATIONS = {1: _v1_to_v2}`;
   `migrate(scan, registry) -> dict`: версія = `scan.get("schema_version", 1)`; поки версія менша за поточну,
   застосовуй міграцію; версія більша за поточну → запис без змін і один `warning` у лог на весь процес.
   Реєстр джерел передається параметром.
2. `app/server.py`: `_adapt_legacy_scan(scan)` викликає `migrate(scan, REGISTRY)` (ім'я лишається для тестів).
3. `app/pipeline.py::run_new`: новий запис має `"schema_version": CURRENT_SCHEMA_VERSION`. `rescan` версію не
   змінює.
4. `tests/test_scan_migrations.py`: v1 без `steam.sources` і `player_uid` → v2 з обома і `schema_version == 2`;
   повторна міграція нічого не змінює; запис з `schema_version: 99` не змінено і warning лише один на два записи;
   запис без гравців. У `tests/test_pipeline.py` додай перевірку `schema_version` у записі `run_new`; наявні
   асерції, що порівнюють набір ключів запису, онови на новий ключ.

## Не робити

- Не переносити legacy-слоти і не змінювати формат гравця.

## Критерії приймання

- Нові тести проходять; повний набір зелений; `ruff check .` чистий.

## Перевірка

```bash
.venv/bin/python -m unittest tests.test_scan_migrations tests.test_pipeline -v 2>&1 | tail -5
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
.venv/bin/ruff check .
```

## Docs delta

`docs/ARCHITECTURE.md`: Scan record — `schema_version`; новий рядок `app/scan_migrations.py`; Known limitations
про legacy-слоти — «a future v3 migration would move them». Онови в цьому ж PR.
