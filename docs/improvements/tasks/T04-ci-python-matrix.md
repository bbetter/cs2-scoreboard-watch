---
id: T04
title: CI matrix for Python 3.11, 3.12 and 3.13
wave: 1
depends_on: [T01]
review: none
size: S
golden: []
files:
  - .github/workflows/tests.yml
---

# T04 — CI-матриця Python 3.11 / 3.12 / 3.13

## Мета

`pyproject.toml` обіцяє `requires-python = ">=3.11"`, а CI перевіряє тільки 3.11. Запускати тести на 3.11, 3.12 і
3.13, не послабивши гейт авто-мерджу.

## Контекст

- `.github/workflows/tests.yml`: job `unittest` (`python-version: "3.11"`), job `auto-merge` з `needs: unittest`.
- `needs:` на job з матрицею чекає **всі** комбінації матриці — тобто авто-мердж і далі вимагатиме зелені тести,
  тепер на всіх трьох версіях. Це бажана поведінка.
- Імʼя job використовується в бейджі README лише через назву workflow (`Tests`), тому перейменування job безпечне,
  але **не** перейменовуй workflow (`name: Tests`).

## Що зробити

1. У job `unittest`:
   ```yaml
       name: unittest (Python ${{ matrix.python-version }})
       strategy:
         fail-fast: false
         matrix:
           python-version: ["3.11", "3.12", "3.13"]
   ```
   і `python-version: ${{ matrix.python-version }}` у `actions/setup-python`.
2. Крок lint (`ruff check .`, з T01) запускай лише один раз:
   `if: matrix.python-version == '3.11'`.
3. Нічого не міняй у job `auto-merge`, `permissions`, `concurrency`, `on:`.

## Не робити

- Не додавати macOS/Windows раннери (окрема задача, не зараз).
- Не змінювати `needs`, `if` і кроки `auto-merge`.

## Критерії приймання

- На PR цієї задачі в Checks видно 3 job-и `unittest (Python 3.11|3.12|3.13)`, усі зелені.
- Якщо якась версія падає — **не** виключай її з матриці: опиши падіння в PR і познач задачу `Blocked`
  (це сигнал про реальну несумісність, рішення за людиною).
- `git diff origin/main -- .github/workflows/tests.yml` торкається лише job `unittest`.

## Перевірка

```bash
python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/tests.yml'))" 2>/dev/null || .venv/bin/python -c "print('install pyyaml or skip; CI will validate')"
gh pr checks <PR>          # після push: 3 unittest job-и success
```

## Docs delta

`ARCHITECTURE.md` → Known limitations, пункт «Python version»: CI тестує 3.11–3.13.
