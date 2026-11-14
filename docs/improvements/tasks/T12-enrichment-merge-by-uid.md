---
id: T12
title: Enrichment writes keep rows edited by the user during the run
wave: 6
depends_on: [T11]
review: none
size: M
golden: []
files:
  - app/pipeline.py
  - app/server.py
  - tests/test_pipeline.py
  - tests/test_enrichment_merge.py
  - docs/ARCHITECTURE.md
---

# T12 — Enrichment не перетирає рядки, змінені користувачем під час прогону

## Мета

Фінальний запис `enrich_scan` замінює весь список `players`. Результат `/api/recheck`, `/api/recheck-source` або
`/api/ai-analyze`, записаний поки enrichment ще йде, губиться. Треба: рядок, який користувач змінив **після старту
прогону**, enrichment не перезаписує.

## Контекст

- `app/pipeline.py` `enrich_scan`: `_on_player` замінює рядок за `player_uid`; перед AI і в кінці —
  `scan_rec["players"] = player_dicts` (весь список).
- Маршрути, що пишуть рядки гравців, у `app/server.py`: `_post_recheck` (замінює рядок), `_post_ai_analyze`
  (замінює весь список), `recheck_source_for_player` (замінює `player["steam"]`).
- `started = time.time()` фіксується в `run_new` / `rescan` і передається в `enrich_scan`.
- Known limitations: «The final enrichment write can overwrite concurrent rechecks … not yet covered by a test».

## Що зробити

1. **Маркер ручної зміни.** Кожен рядок, який пише маршрут, отримує `edited_at = time.time()` (float, секунди).
   Додай у `app/server.py` хелпер `_mark_edited(player: dict) -> dict` (ставить `player["edited_at"]` і повертає
   його) і виклич його:
   - `_post_recheck` — для `updated_player` перед записом у `scan_rec["players"][idx]`;
   - `_post_ai_analyze` — для кожного рядка, який реально проаналізовано (у uid/nick-гілці — лише рядки з `subset`;
     у гілці «весь скан» — усі);
   - `recheck_source_for_player` — для `player` перед `_save_history_locked()`.
2. **Правило злиття в пайплайні.** Додай у `ScanPipeline` статичний метод
   ```python
   @staticmethod
   def _keep_user_row(existing: dict, started: float) -> bool:
       return float(existing.get("edited_at") or 0) >= started
   ```
   - У `_on_player`: якщо знайдений за uid рядок `_keep_user_row(existing, started)` → **не** замінюй його
     (прогрес і подію все одно оновлюй/публікуй, але в подію клади `existing`, а не `pdict`).
   - Перед AI і у фінальному записі: замість `scan_rec["players"] = player_dicts` збери новий список:
     для кожного `d` з `player_dicts` (у їхньому порядку) — якщо в поточному `scan_rec["players"]` є рядок з тим самим
     `player_uid` і `_keep_user_row(...)` → бери поточний рядок, інакше `d`. Рядки без `player_uid` — як раніше
     (замінюються).
   - AI-стадія аналізує `player_dicts` (знімок): рядок, змінений користувачем під час AI, у фінальному записі
     лишається користувацьким (без AI-полів). Це свідомий компроміс — опиши його в коментарі над методом.
3. Створи `tests/test_enrichment_merge.py` (патерн — `tests/test_pipeline.py`: `FakeRepo`, відкладений `spawn`):
   - `run_new` → до запуску відкладеної enrichment зміни рядок uid `-0` (`marker="user"`,
     `edited_at=time.time()+1`) → запусти enrichment → у фінальному записі рядок `-0` має `marker="user"`,
     а решта рядків збагачені;
   - те саме, але `edited_at` **менший** за `started` (стара ручна зміна до прогону) → рядок перезаписано;
   - зміна під час AI-стадії (зроби `analyze_ai`, який перед поверненням змінює рядок у `repo.scans`) → рядок
     лишається користувацьким;
   - порядок рядків = порядок `player_dicts`;
   - маршрутний тест: `_mark_edited` ставить `edited_at` (через `mock.patch.object(server.time, "time",
     return_value=123.0)`).
4. `tests/test_pipeline.py` змінюй лише якщо старий тест падає через нове правило злиття — тоді зупинись і
   опиши в PR, чому (очікується, що старі тести проходять без змін).

## Не робити

- Не змінювати пошук рядка за індексом у `_post_recheck` і ключування за ніком у `_post_ai_analyze` (це T13).
- Не додавати блокувань на час мережевих викликів.

## Критерії приймання

- Нові тести проходять; повний набір зелений; `expected failures=2`.
- `edited_at` не потрапляє в рядки, які ніхто не редагував (перевір тестом: після чистої enrichment ключа немає).

## Перевірка

```bash
.venv/bin/python -m unittest tests.test_enrichment_merge -v
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
```

## Docs delta

Онови `docs/ARCHITECTURE.md` у цьому ж PR. Data shapes / Player report: додати `edited_at` (float, ставлять маршрути recheck /
recheck-source / ai-analyze); Known limitations: прибрати «The final enrichment write can overwrite concurrent
rechecks», додати компроміс про AI-стадію.
