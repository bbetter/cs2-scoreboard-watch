---
id: T29
title: Remove re-exports from lookup/steam_lookup.py
wave: 7
depends_on: [T28]
review: none
size: M
golden: []
files:
  - lookup/steam_lookup.py
  - app/server.py
  - scoring/rules.py
  - sources/_util.py
  - sources/leetify.py
  - tests/test_*.py
  - docs/ARCHITECTURE.md
---

# T29 — `lookup/steam_lookup.py` без реекспортів

## Мета

Після T16–T19 `steam_lookup` реекспортує імена з `storage.api_keys`, `lookup.steam_search`, `sources.leetify` і
`scoring.rules` з позначками `# noqa: F401`, бо їх звідти імпортують сервер, скоринг і тести. Через це
`steam_lookup` лишається вузлом, від якого залежать усі, а `scoring/rules.py` імпортує `lookup`. Треба імпортувати
кожне ім'я з модуля, де воно визначене, і прибрати реекспорти.

## Контекст

- `lookup/steam_lookup.py`: блоки `from lookup.steam_search import (...)  # noqa: F401`,
  `from storage.api_keys import (...)  # noqa: F401`, `from sources.leetify import _num  # noqa: F401`,
  `from scoring.rules import _pick_recent_metric  # noqa: E402,F401`.
- `app/server.py` імпортує з `lookup.steam_lookup`: `_get_faceit_api_key`, `_get_steam_api_key`,
  `get_steam_search_status`, `reset_faceit_api_key_cache`, `reset_steam_api_key_cache` (разом з `enrich_players`,
  `recheck_player_report`, які визначені в `steam_lookup`).
- `scoring/rules.py` у `_pick_recent_metric` робить `from lookup.steam_lookup import _num`.
- Тести, які звертаються до реекспортованих імен через `steam_lookup` (`sl.`):
  `grep -rn "steam_lookup\.\|sl\.\|from lookup.steam_lookup import" tests/`. Зокрема `tests/test_steam_queue.py`
  використовує `sl._STEAM_RL_LOCK`, `sl._STEAM_RL_STATE`, `sl.STEAM_COOLDOWN_MAX_SEC`, `sl._get_steam_api_key`.
- `mock.patch.object(sl, "X")` має сенс лише якщо `steam_lookup` сам викликає `X`. Якщо після прибирання
  реекспорту ім'я зникає з `steam_lookup`, такий патч у тесті треба перевести на модуль, де `X` визначене і
  викликається.

## Що зробити

1. Додай у `sources/_util.py` функцію `round_num(val, nd=None)` — точна копія поведінки `sources.leetify._num`. У
   `sources/leetify.py` зроби `_num = round_num` (ім'я лишається для внутрішніх викликів). `scoring/rules.py`
   імпортує `round_num` з `sources._util` на рівні модуля; `lookup` у `scoring/` більше не імпортується.
2. У `app/server.py` імпортуй ключові функції з `storage.api_keys`, а `get_steam_search_status` з
   `lookup.steam_search`.
3. У `lookup/steam_lookup.py` прибери всі `# noqa: F401` і `# noqa: E402,F401`. Імпорти, які сам модуль
   використовує, лишаються (без noqa); невикористані видали (`ruff check` покаже `F401`).
4. Переведи тести на справжні модулі: імпорти й `mock.patch.object` цілі. Не змінюй асерції і не послаблюй тести.
5. Скоринг-golden не змінюється: `tests.test_golden_scoring` має пройти без `--regen`.

## Не робити

- Не перейменовувати публічні функції і не змінювати поведінку.
- Не чіпати `tests/golden/*`.

## Критерії приймання

- `grep -n "noqa: F401\|noqa: E402,F401" lookup/steam_lookup.py` нічого не знаходить.
- `grep -rn "steam_lookup" scoring/` нічого не знаходить.
- Повний набір тестів зелений з тією самою кількістю тестів; `ruff check .` чистий.

## Перевірка

```bash
grep -n "noqa: F401\|noqa: E402,F401" lookup/steam_lookup.py; grep -rn "steam_lookup" scoring/
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
.venv/bin/ruff check .
```

## Docs delta

`docs/ARCHITECTURE.md`: опис `lookup/steam_lookup.py` — «orchestration only: profile lookup, report building,
enrichment; no re-exports»; згадки, що scoring імпортує зі `steam_lookup`, прибрати. Онови в цьому ж PR.
