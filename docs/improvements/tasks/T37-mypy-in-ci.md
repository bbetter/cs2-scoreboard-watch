---
id: T37
title: Type-check storage, sources and scoring with mypy in CI
wave: 9
depends_on: [T36]
chunk: H-hygiene-docs
review: none
size: M
golden: []
files:
  - pyproject.toml
  - requirements-dev.txt
  - .github/workflows/tests.yml
  - storage/*.py
  - sources/*.py
  - scoring/*.py
---

# T37 — mypy для `storage/`, `sources/`, `scoring/` у CI

## Мета

Анотації типів є, але їх ніхто не перевіряє. Три пакети без важких залежностей (`storage`, `sources`, `scoring`)
можна перевіряти в CI вже зараз. Треба додати mypy і зробити ці пакети чистими.

## Контекст

- `requirements-dev.txt`: `-r requirements.txt` і закріплений `ruff`. CI (`.github/workflows/tests.yml`) ставить
  `requirements-dev.txt` і запускає `ruff check .` лише для Python 3.11.
- mypy у `.venv` не встановлено.

## Що зробити

1. Додай `mypy==<остання версія з pip index versions mypy>` у `requirements-dev.txt` і встанови в `.venv`.
2. `pyproject.toml`: `[tool.mypy] python_version = "3.11"`, `files = ["storage", "sources", "scoring"]`,
   `ignore_missing_imports = true`, `warn_unused_ignores = true`. Режим за замовчуванням (без `strict`).
3. Виправ помилки в цих пакетах мінімальними змінами анотацій. Якщо виправлення змінило б поведінку, став
   `# type: ignore[<code>]` з коментарем-причиною в тому ж рядку. Кількість помилок до і після — у PR.
4. CI: крок `Type-check (mypy)` (`run: mypy`) поруч із ruff, лише для 3.11.

## Не робити

- Не розширювати перевірку на `app/`, `lookup/`, `vision/`, `cs2platform/` (окремий наступний крок).
- Не змінювати поведінку коду і golden-файли.

## Критерії приймання

- `mypy` без аргументів завершується з кодом 0; повний набір тестів зелений; `ruff check .` чистий.

## Перевірка

```bash
.venv/bin/mypy
.venv/bin/ruff check .
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
```

## Docs delta

Не редагуй `docs/ARCHITECTURE.md` (його паралельно змінювали б інші чанки): опиши зміни для документа в розділі «Docs delta» тіла PR, підрозділ цієї задачі. T39 перенесе їх у документ.

Що описати: `docs/ARCHITECTURE.md` → Testing: «Types: `mypy` checks storage, sources, scoring (config in pyproject.toml)».
