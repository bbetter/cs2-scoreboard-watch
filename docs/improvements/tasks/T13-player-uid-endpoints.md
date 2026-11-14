---
id: T13
title: Endpoints address players by player_uid
wave: 6
depends_on: [T12]
review: none
size: M
golden: []
files:
  - app/server.py
  - storage/feedback_store.py
  - tests/test_player_uid.py
  - tests/test_recheck_source.py
  - tests/test_feedback_f4.py
  - tests/test_server_uid_endpoints.py
  - docs/ARCHITECTURE.md
---

# T13 — Ендпоінти адресують гравця за `player_uid`

## Мета

Закрити чотири відомі дефекти адресації рядків у `app/server.py`:

1. `/api/recheck` шукає індекс під одним lock, а пише за ним під іншим → результат може потрапити в чужий рядок.
2. `/api/ai-analyze` з `nick` і дублікатами ніків — результат другого дубля замінює перший.
3. `/api/feedback` вимагає `nick`, навіть коли передано `player_uid`.
4. `/api/recheck-source` викликає `src.fetch_raw` напряму і пропускає `src.is_enabled(ctx)`.

## Контекст

- Тести-специфікації (зараз `@unittest.expectedFailure`) у `tests/test_player_uid.py`:
  `AiAnalyzeUidTests::test_nick_path_keeps_each_duplicate_its_own_row` і
  `RaceExpectedFailureTests::test_recheck_route_writes_back_to_the_right_row_after_a_concurrent_reorder`.
- Пін старої поведінки feedback: `tests/test_player_uid.py:209` очікує `(400, {"error": "nick is required"})`.
- `storage/feedback_store.validate_feedback_payload` перевіряє `nick` (`MAX_NICK_LEN`); `build_feedback_entry` і
  `tests/golden/feedback_golden.json` **не змінюються**.
- Після T09–T12 у сервері є `STORE`, `_mark_edited`, а кожен рядок має `player_uid` (легасі-рядки отримують його в
  `_adapt_legacy_scan`).
- `sources.SourceContext(steam_api_key=...)`; ключ Steam — `_get_steam_api_key()` (уже імпортовано в server).

## Що зробити

1. **`/api/recheck`** (`_post_recheck`): після `recheck_player_report` + AI, під `STORE.lock`, знайди рядок
   **заново** за `player_copy["player_uid"]` у **поточному** `scan_rec["players"]` (не використовуй старий `idx`).
   Якщо не знайдено → `409 {"error": "Player was removed while rechecking"}`. Якщо знайдено — заміни його
   (`_mark_edited` як у T12), далі сортування і збереження як раніше.
2. **`/api/ai-analyze`**: у гілці з `target_nick`/`target_uid` ключуй результати **завжди** за `player_uid`
   (`key = lambda p: p.get("player_uid")`), навіть коли запит прийшов з `nick`. Запис: замість
   `scan_rec["players"] = new_players` онови під lock **лише** проаналізовані рядки за uid у поточному
   `scan_rec["players"]` (інші рядки, змінені паралельно, не чіпаються). Гілка «весь скан» — так само за uid.
3. **`/api/feedback`**: у `validate_feedback_payload` дозволь `player_uid` (непорожній `str`, ≤ 64 символів) як
   альтернативу `nick`: валідно, якщо є хоча б одне з двох. `clean` повертає `{"scan_id", "nick" (може бути ""),
   "player_uid" (або None), "label"}`. Помилку при відсутності обох зроби `"nick or player_uid is required"`.
   У `_post_feedback` шукай гравця через `_player_matches(p, clean["player_uid"], clean["nick"])`.
   Онови пін у `tests/test_player_uid.py:209` на новий текст помилки (для запиту без обох полів) і додай кейс, що
   запит **лише** з `player_uid` дає 200. Перевір `tests/test_feedback_f4.py` на тексти помилок валідації і онови
   так само (лише текст `"nick is required"` → новий; кейси з невалідним `nick` при відсутньому uid лишаються 400).
4. **`/api/recheck-source`** (`recheck_source_for_player`): створи `ctx = SourceContext(steam_api_key=_get_steam_api_key())`;
   якщо `not src.is_enabled(ctx)` → `409 {"error": f"Джерело {source} вимкнене: не задано потрібний ключ"}`;
   інакше `src.fetch_raw(steamid64, ctx)`.
5. Прибери `@unittest.expectedFailure` з двох тестів з контексту; онови докстрінг `RaceExpectedFailureTests`
   (клас лишається, у ньому тепер звичайні регресійні тести — можна перейменувати на `RaceRegressionTests`).
6. Створи `tests/test_server_uid_endpoints.py` (патерн живого сервера — `_LiveServer` з `tests/test_player_uid.py`,
   імпортуй його звідти): recheck з видаленим під час перевірки рядком → 409; ai-analyze за `nick` з дублями не
   змінює рядки з іншими uid і не дублює uid; feedback лише з `player_uid` → 200 і запис містить правильного гравця;
   recheck-source для джерела з `is_enabled → False` (зроби фейкове джерело через `mock.patch.object` методу
   `is_enabled` у `REGISTRY.get("leetify")`) → 409 і `fetch_raw` не викликано.

## Не робити

- Не змінювати `build_feedback_entry` і feedback golden. Не змінювати таблицю маршрутів.
- Не прибирати підтримку `nick` (легасі-клієнти).

## Критерії приймання

- Повний набір тестів зелений; **`expected failures=0`** (рядок `OK` без дужок).
- `grep -n "expectedFailure" tests/*.py` → порожньо.

## Перевірка

```bash
.venv/bin/python -m unittest tests.test_server_uid_endpoints tests.test_player_uid tests.test_feedback_f4 -v 2>&1 | tail -8
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3     # OK
```

## Docs delta

Онови `docs/ARCHITECTURE.md` у цьому ж PR. Request flow: «`/api/feedback` accepts `player_uid` or `nick`»; Known limitations: прибрати
пункти про `/api/recheck` by index, `/api/ai-analyze` duplicates, `/api/feedback requires nick`,
«Recheck-source calls fetch_raw directly».
