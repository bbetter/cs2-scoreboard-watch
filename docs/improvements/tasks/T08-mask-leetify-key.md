---
id: T08
title: Mask the Leetify API key in GET /api/config
wave: 1
depends_on: [T07]
review: none
size: S
golden: [tests/golden/config_golden.json]
files:
  - sources/leetify.py
  - tests/test_config_schema.py
  - tests/golden/config_golden.json
---

# T08 — Маскувати Leetify-ключ у `GET /api/config`

## Мета

`GET /api/config` віддає `leetify_api_key` відкритим текстом у `config`. Віддавати його так само, як інші ключі:
`has_leetify_api_key`, `leetify_api_key_source`, `leetify_api_key_masked`, без сирого значення.

## Контекст

- `sources/leetify.py`, `settings = (Secret(name="leetify_api_key", ..., keep_on_empty=False, masked=False),)`.
- `storage/config_fields.Secret`: `masked=True` → значення прибирається з `config` і замінюється полями статусу
  (`storage/config_schema.public_view`); `keep_on_empty=True` → порожнє значення в `POST /api/config` **зберігає**
  ключ, а `clear_leetify_api_key: true` видаляє (`clear_flag`).
- **UI не має поля для Leetify-ключа**: `web/settings.js` не шле `leetify_api_key` у payload (перевір:
  `grep -n leetify_api_key web/*.js` → порожньо). Тобто ключ задається лише в `data/config.json` вручну, і
  `apply_updates` його не чіпає (ключа немає в `updates`). Зміна безпечна для UI.
- Код, що **використовує** ключ, читає його з `load_config()` (не з публічного view) — перевір:
  `grep -n "leetify_api_key" lookup/*.py sources/*.py`.
- Поведінка зафіксована в `tests/golden/config_golden.json` (Known limitations: «Leetify key is returned unmasked»).

## Що зробити

1. У `sources/leetify.py` прибери `keep_on_empty=False, masked=False` (залиш дефолти `True`) і онови коментар
   над `settings`: ключ маскується як інші; порожнє значення зберігає ключ; `clear_leetify_api_key` видаляє.
   Онови `help`, якщо там написано щось про відкрите збереження.
2. Додай у `tests/test_config_schema.py` клас `LeetifyKeyMaskingTests`:
   - `public_view({... "leetify_api_key": "abcdefTESTKEY1234"})`: у `out["config"]` **немає** `leetify_api_key`;
     `out["has_leetify_api_key"] is True`; `out["leetify_api_key_source"] == "config"`;
     `out["leetify_api_key_masked"]` закінчується на `"1234"` і не містить `"abcdef"`;
   - `apply_updates({"leetify_api_key": "OLDTESTKEY"}, {"leetify_api_key": ""})["leetify_api_key"] == "OLDTESTKEY"`;
   - `apply_updates({"leetify_api_key": "OLDTESTKEY"}, {"clear_leetify_api_key": True})["leetify_api_key"] == ""`;
   - `describe_schema()` для `leetify_api_key` має `"clear_flag": "clear_leetify_api_key"`.
   Усі фікстури — з `TESTKEY` у значенні (є тест, який це перевіряє).
3. Регенеруй golden командою і прочитай диф:
   ```bash
   .venv/bin/python tests/test_config_parity.py --regen
   git diff tests/golden/config_golden.json | head -200
   ```
   Очікувано: у публічних view зникає `config.leetify_api_key` і зʼявляються три поля `*leetify_api_key*`;
   у кейсах «save з порожнім ключем» ключ тепер зберігається. Будь-яка інша зміна → відкоти golden і шукай помилку.
4. Перевір, що фронтенд не читав це поле: `grep -rn "leetify_api_key" web/` → порожньо (якщо ні — `Blocked`).

## Не робити

- Не додавати поле Leetify-ключа в UI (окрема продуктова задача).
- Не змінювати `storage/config_schema.py` / `config_fields.py` — достатньо прапорців у дескрипторі.

## Критерії приймання

- Нові тести проходять; повний набір зелений з регенерованим golden.
- Ручну перевірку через запущений сервер **не** роби: вона читала б реальний `data/config.json` з ключем.
  Достатньо юніт-тестів; у Verification напиши «manual check skipped: would expose real data/config.json».
- В описі PR — розділ «Golden diff», як у T07.

## Перевірка

```bash
.venv/bin/python -m unittest tests.test_config_schema tests.test_config_parity -v 2>&1 | tail -5
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
```

## Docs delta

`ARCHITECTURE.md` → Data shapes / Config: прибрати «`leetify_api_key` is the exception»; Known limitations:
прибрати «Leetify key is returned unmasked».
