---
id: T25
title: Pass macOS notification text as osascript arguments
wave: 5
depends_on: [T24]
review: none
size: S
golden: []
files:
  - cs2platform/notify.py
  - tests/test_platform.py
  - docs/ARCHITECTURE.md
---

# T25 — Текст сповіщення macOS передається як аргументи `osascript`

## Мета

`cs2platform/notify.build_notification_command` на macOS вставляє заголовок і текст (там є нік гравця) прямо в
AppleScript-рядок з ручним екрануванням `\` і `"`. Будь-яка помилка екранування робить нік кодом AppleScript. Треба
передавати текст окремими аргументами `osascript`, щоб він ніколи не був частиною скрипта.

## Контекст

- `cs2platform/notify.py`, гілка `platform_name == "darwin"`: `["osascript", "-e", f'display notification
  "{safe_msg}" with title "{safe_title}"']`.
- `osascript -e 'on run argv' -e '...' -e 'end run' arg1 arg2` передає `arg1`, `arg2` у `argv` скрипта.
- Тест `tests/test_platform.py::...::test_macos_osascript_escapes_quotes_and_backslashes` пінить стару поведінку.
- Повідомлення формує `app/server.py` (`_delayed_tab_scan`): воно починається з фіксованого тексту, не з ніка.

## Що зробити

1. У гілці darwin поверни
   `["osascript", "-e", "on run argv", "-e", "display notification (item 1 of argv) with title (item 2 of argv)",
   "-e", "end run", msg, title]` і `{"check": False}`. Прибери `safe_msg` / `safe_title`.
2. У `tests/test_platform.py` заміни тест екранування на `test_macos_osascript_passes_text_as_arguments`: для
   `title='Ti"tle'`, `msg='a "b" \\c'` перевір повний argv: три `-e` з фіксованими рядками, потім `msg` і `title`
   без змін; жоден `-e`-рядок не містить ні `msg`, ні `title`.

## Не робити

- Не змінювати гілки Linux і Windows і `send_notification`.

## Критерії приймання

- Тести `tests.test_platform` проходять; повний набір зелений; `ruff check .` чистий.

## Перевірка

```bash
.venv/bin/python -m unittest tests.test_platform -v 2>&1 | tail -5
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
.venv/bin/ruff check .
```

## Docs delta

`docs/ARCHITECTURE.md` → рецепт «(c) Add a screenshot tool or a notifier»: речення «Pass user text to notifier
commands as separate argv items, never inside a script string (see the macOS branch)». Додай у цьому ж PR.
