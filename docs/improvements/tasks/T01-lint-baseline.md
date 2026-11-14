---
id: T01
title: Lint baseline with ruff (F, E9) in CI
wave: 0
depends_on: []
review: none
size: S
golden: []
files:
  - pyproject.toml
  - requirements-dev.txt
  - .github/workflows/tests.yml
  - app/*.py
  - cs2platform/*.py
  - cs2watch/*.py
  - lookup/*.py
  - scoring/*.py
  - sources/*.py
  - storage/*.py
  - vision/*.py
  - tests/*.py
---

# T01 — Lint-база: ruff (`F`, `E9`) у CI

## Мета

Додати статичну перевірку, яка ловить невикористані імпорти/змінні, невизначені імена та синтаксичні помилки, і
запускати її в CI. Усі наступні задачі мають проходити `ruff check .`.

## Контекст

- Лінтера немає ні в `pyproject.toml`, ні в `.github/workflows/tests.yml` (є тільки `unittest`).
- У коді вже є навмисні `# noqa`, наприклад `lookup/steam_lookup.py:1341-1342`
  (`from scoring.rules import _pick_recent_metric  # noqa: E402,F401` — це **реекспорт**, його не можна видаляти).
- `sources/__init__.py` і `scoring/__init__.py` реекспортують імена — для них F401 буде хибним спрацюванням.
- Job `auto-merge` у `tests.yml` має `needs: unittest`. Це гейт безпеки власника репо — **не змінювати**.

## Що зробити

1. Встанови ruff у venv: `.venv/bin/pip install ruff` і подивись встановлену версію `.venv/bin/ruff --version`.
2. Створи `requirements-dev.txt`:
   ```
   -r requirements.txt
   ruff==<точна версія з кроку 1>
   ```
3. У `pyproject.toml` додай:
   ```toml
   [tool.ruff]
   target-version = "py311"
   extend-exclude = [".venv", "data"]

   [tool.ruff.lint]
   select = ["F", "E9"]

   [tool.ruff.lint.per-file-ignores]
   "*/__init__.py" = ["F401"]
   ```
4. Запусти `.venv/bin/ruff check .` і виправ знахідки **мінімально**:
   - F401 (невикористаний імпорт): видали імпорт, **але** якщо імʼя імпортується з цього модуля деінде
     (`grep -rn "from <module> import <name>"` і `grep -rn "<module>\.<name>"` по `app lookup sources storage scoring
     vision cs2platform cs2watch tests`) або патчиться в тестах (`patch.object(<module>, "<name>"`) — залиш і додай
     `# noqa: F401  # re-export`.
   - F841 (невикористана змінна): видали присвоєння, якщо права частина не має сайд-ефектів; інакше `_ = ...`.
   - F811/F821/E9: виправ по суті; якщо фікс не очевидний — зупинись і познач задачу `Blocked` з описом.
5. У `.github/workflows/tests.yml`, job `unittest`: заміни `pip install -r requirements.txt` на
   `pip install -r requirements-dev.txt` і додай крок **перед** «Run unit tests»:
   ```yaml
         - name: Lint (ruff)
           run: ruff check .
   ```
   Поле `cache-dependency-path` зроби списком з обох файлів (`requirements.txt` і `requirements-dev.txt`).

## Не робити

- Не вмикати інші правила (`E`, `W`, `I`, `UP`, …) і не запускати `ruff format` — жодного масового переформатування.
- Не змінювати job `auto-merge`, `permissions`, тригери workflow.
- Не змінювати поведінку коду; не чіпати golden-файли.

## Критерії приймання

- `.venv/bin/ruff check .` → `All checks passed!`
- Повний набір тестів зелений, кількість тестів та `expected failures=3` не змінились.
- У дифі `.py`-файлів лише видалені/позначені імпорти й змінні.

## Перевірка

```bash
.venv/bin/ruff check .
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
git diff --stat origin/main
```

## Docs delta

`ARCHITECTURE.md` → розділ Testing: додати команду `ruff check .` і `requirements-dev.txt`.
