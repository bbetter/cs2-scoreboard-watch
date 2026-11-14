---
id: T36
title: Extend ruff to import sorting, pyupgrade and bugbear
wave: 9
depends_on: [T35]
review: none
size: M
golden: []
files:
  - pyproject.toml
  - *.py
  - docs/ARCHITECTURE.md
---

# T36 — Ruff: `I`, `UP`, `B`

## Мета

CI перевіряє лише `F` і `E9`. Додаткові родини ловлять реальні помилки (`B`: zip без `strict`, змінювані дефолти) і
тримають імпорти й синтаксис однаковими. Треба увімкнути `I`, `UP`, `B` без зміни поведінки.

## Контекст

- `pyproject.toml`: `[tool.ruff] target-version = "py311"`, `[tool.ruff.lint] select = ["F", "E9"]`,
  `per-file-ignores` для `__init__.py`.
- Перед задачею (на 2026-11-15): `I` — близько 300 автофіксів, `UP` — близько 1500 (майже всі безпечні), `B` —
  30 × `B905` (zip без `strict`).
- Тести вставляють `sys.path` перед імпортами (`# noqa: E402`); сортування не повинне переносити імпорти вище
  цього рядка.
- Порядок імпортів у пакетах може бути важливим для циклів (`storage.config_schema` імпортує `sources`). Тести це
  покажуть.

## Що зробити

1. `select = ["F", "E9", "I", "UP", "B"]`; `[tool.ruff.lint.isort] known-first-party = ["app", "cs2watch",
   "cs2platform", "lookup", "scoring", "sources", "storage", "vision"]`.
2. `ruff check . --fix` (лише безпечні фікси). Потім `ruff check . --select B905 --fix --unsafe-fixes` — він додає
   `strict=False`, що зберігає поведінку. Не вмикай `--unsafe-fixes` для інших правил.
3. Решту порушень виправ руками, якщо фікс очевидно не змінює поведінку. Правило, яке не можна виправити без зміни
   поведінки, додай в `ignore` з коментарем-причиною в `pyproject.toml`. Перелік таких правил — у PR.
4. Не запускай `ruff format`.

## Не робити

- Не змінювати логіку, тексти, golden-файли, не перейменовувати імена.

## Критерії приймання

- `ruff check .` чистий; повний набір тестів зелений з тією самою кількістю тестів.
- `git diff --stat origin/main -- tests/golden/` порожній.

## Перевірка

```bash
.venv/bin/ruff check .
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
git diff --stat origin/main -- tests/golden/
```

## Docs delta

`docs/ARCHITECTURE.md` → Testing: «Lint: `ruff check .` with F, E9, I, UP, B». Онови в цьому ж PR.
