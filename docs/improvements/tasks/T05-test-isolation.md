---
id: T05
title: Isolate legacy tests from the real data directory
wave: 1
depends_on: [T01]
review: none
size: S
golden: []
files:
  - tests/test_scoring_f4.py
  - tests/test_nick_match.py
  - tests/test_steam_queue.py
  - tests/test_stale_cache_fallback.py
  - tests/test_data_dir_untouched.py
---

# T05 — Ізолювати старі тести від `data/`

## Мета

Жоден тест не повинен читати чи писати реальні файли в `data/` розробника. Чотири старі тести
(«not audited for real-file access», `ARCHITECTURE.md` → Testing) треба перевірити і, де треба, перевести на
тимчасові файли. Додати guard-тест, який ловить регресію.

## Контекст

- Runtime-шляхи визначені в `storage/paths.py` (`DATA_DIR`, `LOG_DIR`, `SCREENSHOT_DIR`).
- Модульні константи, через які тести можуть зачепити `data/`:
  `lookup.steam_lookup.PROFILE_CACHE_FILE` (`data/profile_cache.json`), `storage.config_store.CONFIG_FILE`
  (`data/config.json`), `app.server.HISTORY_FILE`, `storage.player_db._default_db`, лог `data/logs/pton.logs`
  (створюється **при імпорті** `lookup.steam_lookup` — це прибере T16, тут його не чіпай).
- Вже ізольовані тести роблять так: `mock.patch.object(steam_lookup, "PROFILE_CACHE_FILE", Path(tmp) / "c.json")`,
  `mock.patch.object(config_store, "CONFIG_FILE", ...)` з `tempfile.TemporaryDirectory()`.
- `tests/test_steam_queue.py` уже патчить `PROFILE_CACHE_FILE` в одному місці — перевір, що в **усіх** тестах, де
  викликається код, що зберігає кеш (`_save_profile_cache_locked`, `lookup_steam_profile`, `enrich_players`).

## Що зробити

1. Для кожного з 4 файлів знайди всі виклики коду, що читає/пише файли: `load_config`, `save_config`,
   `_load_profile_cache`, `_save_profile_cache_locked`, `lookup_steam_profile`, `enrich_players`,
   `append_feedback`, `player_db.get_db`, `_save_history_locked`. Простий спосіб — тимчасово запусти тест під
   `strace -f -e trace=openat -o /tmp/t05.trace .venv/bin/python -m unittest tests.test_X` і
   `grep "$PWD/data" /tmp/t05.trace` (якщо `strace` немає — читай код).
2. Де є доступ до `data/` — оберни тест (або `setUp`/`tearDown` класу) у `tempfile.TemporaryDirectory()` і
   `mock.patch.object(...)` відповідної константи. Читання `data/config.json` замінюй на
   `mock.patch.object(<module>, "load_config", return_value={...})` з потрібними ключами.
3. Створи `tests/test_data_dir_untouched.py`: тест, який
   - запамʼятовує `(path, st_mtime_ns, st_size)` усіх файлів у `storage.paths.DATA_DIR` (якщо тека існує),
   - імпортує й запускає 4 модулі з кроку 1 через `unittest.defaultTestLoader.loadTestsFromName(...)` і
     `unittest.TextTestRunner(stream=io.StringIO()).run(suite)`,
   - перевіряє, що набір файлів і їхні `mtime/size` не змінились, **за винятком** `data/logs/pton.logs`
     (його створює імпорт — виключи цей шлях явно і залиш коментар `# removed by T16`).
4. Не змінюй жодних асерцій у 4 файлах — лише налаштування оточення (setUp, патчі, тимчасові теки).

## Не робити

- Не змінювати production-код (`lookup/`, `storage/`, `app/`).
- Не видаляти й не послаблювати тести; не додавати `skip`.

## Критерії приймання

- `tests/test_data_dir_untouched.py` проходить на машині з наповненим `data/` (у тебе він наповнений) і на CI.
- Повний набір тестів зелений, кількість тестів = базова + нові.
- В описі PR: таблиця «файл → що торкалось `data/` → як ізольовано» (або «нічого не торкалось, перевірено так-то»).

## Перевірка

```bash
.venv/bin/python -m unittest tests.test_data_dir_untouched -v
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
git status --short data/    # порожньо — data/ gitignored, але переконайся, що нічого не staged
```

## Docs delta

`ARCHITECTURE.md` → Testing: прибрати речення «The older tests (...) were not audited for real-file access»,
згадати `tests/test_data_dir_untouched.py`.
