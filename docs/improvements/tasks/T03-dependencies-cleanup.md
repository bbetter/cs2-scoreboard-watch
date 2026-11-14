---
id: T03
title: Drop unused playwright and align requirements with pyproject
wave: 1
depends_on: [T01]
review: none
size: S
golden: []
files:
  - requirements.txt
  - pyproject.toml
---

# T03 — Прибрати `playwright`, вирівняти залежності

## Мета

Прибрати важку залежність, яку ніхто не імпортує, і зробити так, щоб `requirements.txt` і
`[project].dependencies` у `pyproject.toml` містили одне й те саме.

## Контекст

- `playwright>=1.49.0` є в `requirements.txt` і в `pyproject.toml`, але жоден модуль його не імпортує
  (`ARCHITECTURE.md`, Known limitations; перевір сам — крок 1).
- `beautifulsoup4` **використовується**: `lookup/steam_lookup.py:600` (`from bs4 import BeautifulSoup`). Не видаляй.
- Після T01 є `requirements-dev.txt` з `-r requirements.txt` — його не чіпай.

## Що зробити

1. Доведи, що `playwright` не використовується:
   ```bash
   grep -rn "playwright" --include="*.py" --include="*.sh" --include="*.js" --include="*.yml" . | grep -v "^./.venv/"
   ```
   Очікується: жодного збігу поза `requirements.txt` / `pyproject.toml`. Якщо збіг є — зупинись, `Blocked`.
2. Видали рядок `playwright…` з обох файлів.
3. Порівняй решту списків: імена пакетів, версії і маркери (`; sys_platform != "linux"`) мають збігатися 1:1, у
   тому ж порядку. Якщо є розбіжність — приведи `requirements.txt` до `pyproject.toml`.
4. Додай першим рядком у `requirements.txt` коментар:
   `# Keep in sync with [project].dependencies in pyproject.toml.`
5. Перевір у чистому venv, що все ставиться й імпортується:
   ```bash
   python3 -m venv /tmp/t03venv && /tmp/t03venv/bin/pip install -q -r requirements.txt
   /tmp/t03venv/bin/python -c "import app.server, app.watch_cs2, lookup.steam_lookup, vision.scoreboard_scan; print('ok')"
   rm -rf /tmp/t03venv
   ```

## Не робити

- Не змінювати версії інших пакетів, не додавати нових.
- Не видаляти `playwright` з локального `.venv` (не потрібно і не входить у задачу).

## Критерії приймання

- `grep -n playwright requirements.txt pyproject.toml` → порожньо.
- Списки залежностей у двох файлах ідентичні (в PR покажи вивід `diff` двох списків — крок 3).
- Імпорт у чистому venv — `ok`; повний набір тестів зелений.

## Перевірка

```bash
grep -n playwright requirements.txt pyproject.toml; echo "exit=$?"   # очікуємо exit=1
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
```

## Docs delta

`ARCHITECTURE.md` → Known limitations: прибрати пункт «`playwright` is listed in `requirements.txt` but nothing
in the repo imports it.»
