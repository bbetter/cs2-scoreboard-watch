---
id: T19
title: Move Steam search and candidate matching into lookup/steam_search.py
wave: 3
depends_on: [T18]
review: none
size: M
golden: []
files:
  - lookup/steam_lookup.py
  - lookup/steam_search.py
  - tests/test_steam_search.py
  - tests/test_steam_queue.py
  - tests/test_stale_cache_fallback.py
  - tests/test_golden_scoring.py
  - tests/test_nick_match.py
---

# T19 — Пошук і матчинг профілів Steam → `lookup/steam_search.py`

## Мета

Останній крок розбиття `lookup/steam_lookup.py`: винести пошук кандидатів у Steam Community, rate-limit Steam і
вибір найкращого кандидата в окремий модуль. `steam_lookup` лишається оркестрацією: `SteamProfile`,
`PlayerReport`, `lookup_steam_profile`, `enrich_players`, `recheck_player_report`, `_build_player_report`, скоринг.

## Контекст

- Кандидати на перенос (`grep -n "^def \|^[A-Z_]* =" lookup/steam_lookup.py`): `_SEARCH_LOCK`, `_LAST_SEARCH_TS`,
  `STEAM_SEARCH_MIN_INTERVAL_SEC`, `STEAM_COOLDOWN_BASE_SEC`, `STEAM_COOLDOWN_MAX_SEC`, `STEAM_RETRY_ROUNDS`,
  `STEAM_RETRY_BUDGET_SEC`, `_STEAM_RL_LOCK` і стан cooldown, `_parse_member_age_years`, `_parse_steam_xml`,
  `_avatar_similarity`, `_score_candidate`, `_search_candidates`, `_steam_cooldown_remaining`,
  `_steam_record_search_status`, `get_steam_search_status`, `_fetch_batch_via_web_api`, `_fetch_cs2_hours_web_api`,
  `_prefer_unambiguous_name_match`, `_search_steam_community`, `_retry_rate_limited`.
- `app/server.py` імпортує з `steam_lookup` `get_steam_search_status` — **`app/server.py` не змінювати**, тому в
  `steam_lookup` лишається реекспорт.
- Тести патчать у `steam_lookup` константи `STEAM_*` і функції `_search_steam_community`, `lookup_steam_profile`,
  `_http_get` (`tests/test_steam_queue.py`, `tests/test_stale_cache_fallback.py`, `tests/test_golden_scoring.py`).
  **Патч працює лише там, де імʼя читається.** Константу, яку читає функція з `steam_search`, треба патчити в
  `steam_search`.
- Golden скорингу не має змінитись.

## Що зробити

1. Перед переносом побудуй граф викликів: для кожної функції зі списку —
   `grep -n "<name>(" lookup/steam_lookup.py`. Переноси лише функції, які **не** викликають
   `lookup_steam_profile`, `_build_player_report`, `enrich_players` (інакше буде циклічний імпорт). Якщо функція
   потрібна обом модулям — вона йде в `steam_search`, а `steam_lookup` імпортує її.
   Список «перенесено / лишилось і чому» — у PR.
2. Створи `lookup/steam_search.py`, переносячи функції **дослівно**, групами (rate-limit → парсинг XML → скоринг
   кандидатів → пошук/батч Web API), з повним прогоном тестів після кожної групи.
   `steam_search` імпортує `lookup.http_client`, `lookup.nick_match`, `storage.*`, але **не** `lookup.steam_lookup`.
3. У `steam_lookup`: `from lookup import steam_search` і виклики через атрибут модуля
   (`steam_search._search_steam_community(...)`) — так патчі `mock.patch.object(steam_search, ...)` діють.
   Реекспорт для сервера: `from lookup.steam_search import get_steam_search_status  # noqa: F401  # re-export`.
4. Онови тести: патчі перенесених констант і функцій → `steam_search`. Якщо тест патчить
   `sl._search_steam_community`, а викликає `sl.lookup_steam_profile` — переведи на
   `mock.patch.object(steam_search, "_search_steam_community", …)`. Асерції не змінюй.
5. Створи `tests/test_steam_search.py`: `import lookup.steam_search` не імпортує `lookup.steam_lookup`
   (`"lookup.steam_lookup" not in sys.modules` у підпроцесі: `subprocess.run([sys.executable, "-c", "..."])`);
   `_parse_steam_xml` на мінімальному XML-фікстурі; `_score_candidate` — ідентичний нік дає більший бал, ніж
   схожий; `get_steam_search_status()` повертає dict з тими ж ключами, що й до переносу.

## Не робити

- Не змінювати алгоритм матчингу, пороги, rate-limit, User-Agent. Не змінювати `app/server.py`.
- Не регенерувати golden.

## Критерії приймання

- Повний набір тестів зелений; golden-файли без змін.
- `lookup/steam_lookup.py` < 700 рядків (вкажи до/після); `lookup/steam_search.py` не імпортує `steam_lookup`.

## Перевірка

```bash
.venv/bin/python -m unittest tests.test_steam_search tests.test_steam_queue tests.test_stale_cache_fallback tests.test_golden_scoring -v 2>&1 | tail -6
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
wc -l lookup/steam_lookup.py lookup/steam_search.py
```

## Docs delta

`ARCHITECTURE.md` → module map `lookup/`: новий `lookup/steam_search.py` (Steam Community search, Web API batch,
rate limit, candidate scoring); рядок `lookup/steam_lookup.py`: «orchestration: profile lookup, enrichment,
recheck, report building».
