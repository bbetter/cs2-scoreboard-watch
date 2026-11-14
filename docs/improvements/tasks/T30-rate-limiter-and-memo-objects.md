---
id: T30
title: Steam rate limiter and Leetify memo as objects instead of module state
wave: 8
depends_on: [T29]
chunk: B-lookup-paths
review: none
size: M
golden: []
files:
  - lookup/steam_search.py
  - lookup/steam_lookup.py
  - lookup/ttl_memo.py
  - tests/test_rate_limiter.py
  - tests/test_ttl_memo.py
  - tests/test_steam_queue.py
  - tests/test_stale_cache_fallback.py
---

# T30 — Лімітер Steam і пам'ять Leetify як об'єкти

## Мета

Стан черги пошуку Steam живе в модульних глобалах `lookup/steam_search.py` (`_SEARCH_LOCK`, `_LAST_SEARCH_TS` з
`global`, `_STEAM_RL_LOCK`, `_STEAM_RL_STATE`), а пам'ять Leetify — у `_LEETIFY_MEM` і `_LEETIFY_LOCK` у
`lookup/steam_lookup.py`. Тести скидають цей стан руками. Треба зробити два маленькі класи з керованим годинником.

## Контекст

- `lookup/steam_search.py`: `_search_candidates` (інтервал `STEAM_SEARCH_MIN_INTERVAL_SEC` між стартами запитів,
  повертає `(429, [])` під час cooldown), `_steam_cooldown_remaining`, `_steam_record_search_status` (cooldown
  `STEAM_COOLDOWN_BASE_SEC * 2**(streak-1)`, не більше `STEAM_COOLDOWN_MAX_SEC`, скидання streak на 200),
  `get_steam_search_status`. Інтервал рахується через `time.time()`, cooldown через `time.monotonic()`.
- `lookup/steam_lookup.py`: `_leetify_mem_get`, `_leetify_mem_put`, `_leetify_cached_or_fetch`.
- `tests/test_steam_queue.py` патчить `STEAM_SEARCH_MIN_INTERVAL_SEC`, `STEAM_COOLDOWN_BASE_SEC`,
  `STEAM_COOLDOWN_MAX_SEC` і скидає стан у `setUp` (рядки близько 69–89, 124–133).

## Що зробити

1. У `lookup/steam_search.py` клас `SteamRateLimiter(min_interval_sec, cooldown_base_sec, cooldown_max_sec, *,
   clock=time.monotonic, sleep=time.sleep)` з методами `wait_turn()`, `cooldown_remaining()`, `record(status)`,
   `status() -> {"limited", "retry_in_sec"}`, `reset()`, `streak` (property). Один годинник `clock` для інтервалу
   і cooldown. Модульний екземпляр `RATE_LIMITER` з поточних констант. Функції `_steam_cooldown_remaining`,
   `_steam_record_search_status`, `get_steam_search_status` лишаються обгортками над `RATE_LIMITER`;
   `_search_candidates` викликає `RATE_LIMITER.wait_turn()`. Прибери `global` і чотири глобали. Повідомлення в лог
   про 429 збережи.
2. `lookup/ttl_memo.py`: клас `TtlMemo(clock=time.time)` з `get(key, ttl) -> Optional[dict]` (копія значення),
   `put(key, value, ttl)` (нічого при `ttl <= 0`, порожньому ключі чи порожньому значенні), `clear()`. У
   `steam_lookup` — `LEETIFY_MEMO = TtlMemo()`; `_leetify_mem_get` / `_leetify_mem_put` делегують у нього з тією
   самою поведінкою.
3. `tests/test_steam_queue.py`: у `setUp` підмінюй `steam_search.RATE_LIMITER` свіжим `SteamRateLimiter` з
   тестовими значеннями (`mock.patch.object`), замість ручного скидання глобалів; перевірки cooldown — через
   `cooldown_remaining()` і `streak`. Асерції за змістом не змінюй. `tests/test_stale_cache_fallback.py` онови лише
   якщо він звертається до прибраних імен.
4. `tests/test_rate_limiter.py` (фейковий годинник і `sleep`, що просуває годинник): інтервал між стартами;
   cooldown подвоюється і впирається в максимум; 200 скидає streak; `status()` округлює вгору. `tests/test_ttl_memo.py`:
   влучання в межах ttl, промах після, ttl 0, повернення копії.

## Не робити

- Не змінювати значення констант, логіку повторів `_retry_rate_limited` і формат `/api/steam-status`.

## Критерії приймання

- `grep -n "global \|_STEAM_RL_STATE\|_LAST_SEARCH_TS" lookup/steam_search.py` і
  `grep -n "_LEETIFY_MEM\b" lookup/steam_lookup.py` нічого не знаходять.
- Повний набір тестів зелений; тестів не менше, ніж до задачі; `ruff check .` чистий.

## Перевірка

```bash
.venv/bin/python -m unittest tests.test_rate_limiter tests.test_ttl_memo tests.test_steam_queue -v 2>&1 | tail -5
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
.venv/bin/ruff check .
```

## Docs delta

Не редагуй `docs/ARCHITECTURE.md` (його паралельно змінювали б інші чанки): опиши зміни для документа в розділі «Docs delta» тіла PR, підрозділ цієї задачі. T39 перенесе їх у документ.

Що описати: `docs/ARCHITECTURE.md`: `lookup/steam_search.py` — `SteamRateLimiter` / `RATE_LIMITER`; новий рядок
`lookup/ttl_memo.py`.
