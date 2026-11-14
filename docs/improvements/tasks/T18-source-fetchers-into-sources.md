---
id: T18
title: Move source fetchers from steam_lookup into sources/*
wave: 3
depends_on: [T15, T17]
review: none
size: M
golden: []
files:
  - lookup/steam_lookup.py
  - sources/leetify.py
  - sources/faceit.py
  - sources/cstracker.py
  - sources/steam_stats.py
  - sources/_util.py
  - tests/test_recheck_source.py
  - tests/test_player_uid.py
  - tests/test_sources.py
  - tests/test_sources_view.py
  - tests/test_golden_scoring.py
  - tests/test_steam_queue.py
---

# T18 — Фетчери джерел зі `steam_lookup` → у `sources/*`

## Мета

Кожне джерело (`sources/*.py`) у `fetch_raw` робить late-import `lookup.steam_lookup` і викликає його приватну
функцію (`steam_lookup._fetch_leetify`). Залежність інвертована: «плагін» залежить від god-module. Перенести код
фетчерів у модулі джерел, щоб «додати джерело = один файл» стало правдою і для мережевого коду.

## Контекст

- Фетчери в `lookup/steam_lookup.py` (номери рядків приблизні):
  - Leetify: `LEETIFY_API_BASE`, `LEETIFY_PROFILE_URL`, `LEETIFY_TIMEOUT_SEC` (~421-423), `_leetify_api_key` (~426),
    `_leetify_get` (~435), `_num`, `_round_stats` (~450-458), `_fetch_leetify` (~465);
  - CSTracker: `CSTRACKER_*`, `_CSTRACKER_LOCK`, `_CSTRACKER_LAST_REQUEST`, `_cstracker_throttle`, `_num_or_raw`,
    `_norm_text`, `_slug`, `_parse_cstracker_html`, `_fetch_cstracker` (~567-688);
  - FACEIT: `_safe_float`, `_safe_int`, `_fetch_faceit_stats` (~701-770) + `_get_faceit_api_key` (~162);
  - Steam stats: `_fetch_steam_cs2_stats` (~772).
- **Лишаються в `steam_lookup`** (їх патчать тести і/або вони — оркестрація): `_fetch_external_stats`,
  `_cstracker_extra`, `_leetify_cached_or_fetch`, `_leetify_mem_get/put`, `_strip_leetify`, `_get_faceit_api_key`,
  `_get_steam_api_key`, `reset_*_api_key_cache` (їх імпортує `app/server.py` — **не змінюй `app/server.py`**).
- Після T16 HTTP — у `lookup/http_client.py` (`http_get`, `http_get_status`, `USER_AGENT`).
- Тести патчать `steam_lookup._fetch_leetify` (7 місць) і `steam_lookup._fetch_cstracker` (4) у
  `tests/test_recheck_source.py`, `tests/test_player_uid.py`, `tests/test_sources.py`, `tests/test_sources_view.py`.
- Скоринговий golden (`tests/golden/scoring_golden.json`) **не має змінитись** — регенерація заборонена.

## Що зробити

Роби **по одному джерелу**, після кожного — повний прогін тестів (порядок: Steam stats → FACEIT → Leetify →
CSTracker, від простого до складного).

1. Перенеси функції і константи джерела у `sources/<name>.py` як **публічні** імена модуля:
   `fetch_steam_cs2_stats`, `fetch_faceit_stats`, `fetch_leetify`, `fetch_cstracker` (+ їхні хелпери з `_`-префіксом
   і константи). Код — **дослівно**; змінюються лише імпорти (`from lookup.http_client import http_get, …`).
   Спільні числові хелпери (`_num`, `_safe_float`, `_safe_int`, …), які потрібні кільком джерелам, поклади в
   наявний `sources/_util.py` (там уже є `num_or_none`); хелпер, потрібний одному джерелу, — у модуль цього джерела.
   Модуль джерела не імпортує `lookup.steam_lookup` (можна `lookup.http_client`). Якщо фетчеру потрібен API-ключ з
   `steam_lookup` (`_get_faceit_api_key`, `_leetify_api_key`) — переносимо `_leetify_api_key` разом з Leetify, а
   для FACEIT передавай ключ параметром або через `SourceContext.extra` — обери варіант, що не змінює поведінку, і
   поясни.
2. `fetch_raw` джерела викликає свою функцію напряму (без late-import).
3. У `steam_lookup` прибери перенесене. Місця, що викликали ці функції (`_fetch_external_stats`,
   `_cstracker_extra`, `_leetify_cached_or_fetch`), викликають їх **через атрибут модуля**:
   `import sources.leetify as leetify_source` … `leetify_source.fetch_leetify(steamid64)` — тоді
   `mock.patch.object(sources.leetify, "fetch_leetify", …)` діє і на пошук, і на recheck-source.
4. Онови тести: `mock.patch.object(steam_lookup, "_fetch_leetify", …)` →
   `mock.patch.object(sources.leetify, "fetch_leetify", …)`; так само для cstracker. Асерції не змінюй.
5. Після всіх чотирьох:
   ```bash
   grep -n "late import\|import lookup.steam_lookup\|from lookup import steam_lookup" sources/*.py   # порожньо
   grep -n "def _fetch_leetify\|def _fetch_cstracker\|def _fetch_faceit_stats\|def _fetch_steam_cs2_stats" lookup/steam_lookup.py  # порожньо
   ```

## Не робити

- Не змінювати `app/server.py`, `scoring/`, `storage/`, golden-файли. Не змінювати формати даних джерел.
- Не змінювати throttle CSTracker (`Crawl-delay: 1`) і User-Agent CSTracker.

## Критерії приймання

- Повний набір тестів зелений; `tests/test_golden_scoring.py`, `tests/test_ai_compact_parity.py`,
  `tests/test_feedback_parity.py` — без регенерації.
- `wc -l lookup/steam_lookup.py` помітно менше (вкажи до/після в PR).
- Обидві команди з кроку 5 — порожні.

## Перевірка

```bash
.venv/bin/python -m unittest tests.test_sources tests.test_sources_view tests.test_recheck_source tests.test_golden_scoring -v 2>&1 | tail -6
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
git diff --stat origin/main -- tests/golden/     # порожньо
```

## Docs delta

`ARCHITECTURE.md` → module map `sources/`: кожне джерело містить свій фетчер; recipe (a): прибрати «write this
fetcher in lookup/steam_lookup.py» і late-import з прикладу; рядок `lookup/steam_lookup.py`: прибрати перелік
фетчерів.
