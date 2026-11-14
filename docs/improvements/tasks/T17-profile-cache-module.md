---
id: T17
title: Move the profile cache into storage/profile_cache.py with lazy load
wave: 2
depends_on: [T16]
review: none
size: S
golden: []
files:
  - lookup/steam_lookup.py
  - storage/profile_cache.py
  - tests/test_profile_cache.py
  - tests/test_steam_queue.py
  - tests/test_stale_cache_fallback.py
  - tests/test_golden_scoring.py
---

# T17 — Кеш профілів → `storage/profile_cache.py` з лінивим завантаженням

## Мета

Кеш профілів Steam (`data/profile_cache.json`) — це сховище, але живе в `lookup/steam_lookup.py` і **читається при
імпорті** модуля. Перенести в `storage/` поруч з іншими JSON-сховищами і завантажувати при першому зверненні.

## Контекст

- `lookup/steam_lookup.py`: `CACHE_TTL_SEC`, `PROFILE_CACHE_FILE` (рядки ~53-54), `_CACHE_LOCK`, `_PROFILE_CACHE`
  (dict `key -> (ts, data)`, ~56), `_load_profile_cache` (~122), `_save_profile_cache_locked` (~137), виклик
  `_load_profile_cache()` на рівні модуля (~149). Використання: `lookup_steam_profile` (~1222 get, ~1308 put + save).
- `_strip_leetify` (~75) застосовується при load і put — Leetify-блок **ніколи** не пишеться в кеш (README,
  розділ Leetify). Це інваріант — збережи.
- Тести напряму звертаються до `sl._PROFILE_CACHE` (`.clear()`, `in`, присвоєння) у `tests/test_steam_queue.py` і
  `tests/test_stale_cache_fallback.py`, патчать `sl.PROFILE_CACHE_FILE` і підміняють `sl._save_profile_cache_locked`.
- Атомарний запис: `storage.atomic_io.atomic_write_text`.

## Що зробити

1. Створи `storage/profile_cache.py`:
   ```python
   class ProfileCache:
       def __init__(self, path: Path, strip: Callable[[dict], dict] = lambda d: d) -> None: ...
       lock: threading.Lock
       def entries(self) -> dict[str, tuple[float, dict]]: ...   # lazy: перший виклик робить load
       def get(self, key: str) -> Optional[tuple[float, dict]]: ...
       def put_locked(self, key: str, ts: float, data: dict) -> None: ...   # застосовує strip
       def save_locked(self) -> None: ...
       def clear(self) -> None: ...                                         # для тестів; позначає як завантажений
   ```
   Формат файлу і логіку load/save перенеси **дослівно** з `_load_profile_cache` / `_save_profile_cache_locked`.
   Модуль не імпортує `lookup.*` (функцію `_strip_leetify` передає `steam_lookup` у конструктор).
2. У `steam_lookup`: `PROFILE_CACHE = ProfileCache(PROFILE_CACHE_FILE, strip=_strip_leetify)`; прибери
   `_PROFILE_CACHE`, `_CACHE_LOCK`, `_load_profile_cache`, `_save_profile_cache_locked` і виклик на рівні модуля;
   `lookup_steam_profile` використовує `PROFILE_CACHE.get/put_locked/save_locked` під `PROFILE_CACHE.lock` (той
   самий порядок і межі блокування, що й зараз з `_CACHE_LOCK`).
3. Онови тести: `sl._PROFILE_CACHE.clear()` → `sl.PROFILE_CACHE.clear()`; присвоєння/`in` → через
   `entries()`; `patch.object(sl, "PROFILE_CACHE_FILE", p)` → `patch.object(sl, "PROFILE_CACHE", ProfileCache(p,
   strip=sl._strip_leetify))`; підміна `sl._save_profile_cache_locked` → `patch.object(sl.PROFILE_CACHE,
   "save_locked")`. Асерції не змінюй.
4. Створи `tests/test_profile_cache.py`: конструктор не читає файл (створи кеш на неіснуючому шляху і на шляху до
   битого файлу — без винятку до першого `get`); `get` ініціює load; save → новий екземпляр → load повертає ті самі
   записи; `strip` застосовується при load і put (передай strip, що видаляє ключ `leetify`, і перевір);
   битий JSON → порожній кеш.
5. Перевір, що імпорт більше не читає кеш:
   `strace -f -e trace=openat .venv/bin/python -c "import lookup.steam_lookup" 2>&1 | grep profile_cache` → порожньо
   (якщо `strace` немає — тест у кроці 4 покриває це через конструктор).

## Не робити

- Не змінювати TTL, ключі кешу, формат файлу, правило «Leetify не кешується на диск».
- Не чіпати in-memory кеш Leetify (`_leetify_mem_get/put`) — він лишається в `steam_lookup` (T18 вирішить).

## Критерії приймання

- Повний набір тестів зелений; `tests/test_golden_scoring.py` без регенерації.
- `grep -n "_PROFILE_CACHE\b\|_load_profile_cache\|_save_profile_cache_locked" lookup/ tests/ -r` → порожньо.

## Перевірка

```bash
.venv/bin/python -m unittest tests.test_profile_cache tests.test_steam_queue tests.test_stale_cache_fallback -v 2>&1 | tail -6
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
```

## Docs delta

`ARCHITECTURE.md` → module map `storage/`: новий `storage/profile_cache.py`; рядок `lookup/steam_lookup.py`:
прибрати «profile cache»; Known limitations: прибрати залишок про сайд-ефекти імпорту.
