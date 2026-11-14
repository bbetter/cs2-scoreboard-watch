---
id: T16
title: Lazy pton logging and lookup/http_client.py
wave: 2
depends_on: [T05]
review: none
size: M
golden: []
files:
  - lookup/steam_lookup.py
  - lookup/http_client.py
  - tests/test_http_client.py
  - tests/test_entrypoint.py
  - tests/test_steam_queue.py
  - tests/test_golden_scoring.py
  - tests/test_data_dir_untouched.py
---

# T16 — Ліниве логування `pton` і `lookup/http_client.py`

## Мета

Перший крок розбиття god-module `lookup/steam_lookup.py` (1557 рядків):
(а) прибрати сайд-ефект імпорту — створення `data/logs/pton.logs`;
(б) винести HTTP-хелпери в окремий модуль, від якого потім залежатимуть `sources/*` (T18) і пошук Steam (T19).

## Контекст

- `lookup/steam_lookup.py:34-49`: `_setup_pton_log()` викликається на рівні модуля (рядок 49).
  `tests/test_entrypoint.py` документує, що `--help` створює лише цей файл (рядок 4) — після задачі не створює
  нічого.
- `tests/test_data_dir_untouched.py` (з T05) явно виключає `data/logs/pton.logs` з коментарем `# removed by T16`.
- HTTP-хелпери: `USER_AGENT` (рядок 51), `_http_get_status` (~291), `_http_get` (~336). Тести патчать
  `sl._http_get` (`tests/test_golden_scoring.py`, `tests/test_steam_queue.py`) і `sl._http_get_status`
  (`tests/test_steam_queue.py`).
- **Важливо для патчів:** `mock.patch.object(sl, "_http_get", …)` підміняє імʼя в просторі `steam_lookup`. Код у
  `steam_lookup`, що викликає `_http_get(...)` як глобал модуля, побачить підміну. Але якщо `http_client.http_get`
  всередині викликає `http_client.http_get_status`, патч `sl._http_get_status` на нього **не** подіє.
- Не чіпай `_load_profile_cache()` на рівні модуля (рядок 149) — це T17.

## Що зробити

1. **Логування.** Прибери виклик `_setup_pton_log()` з рівня модуля. Викликай його (функція ідемпотентна) першим
   рядком публічних точок входу, що логують: `enrich_players`, `lookup_steam_profile`, `recheck_player_report`.
   Перевір `grep -n "_LOG\." lookup/steam_lookup.py`, що немає логування з інших публічних функцій, які
   викликаються ззовні до цих трьох (якщо є — додай виклик і туди).
2. **`lookup/http_client.py`**: перенеси `USER_AGENT`, `_http_get_status` → `http_get_status`, `_http_get` →
   `http_get` **дослівно** (ті ж параметри, дефолти, обробка помилок, повернення). Модуль імпортує лише stdlib.
3. У `steam_lookup` заміни визначення на
   ```python
   from lookup.http_client import USER_AGENT, http_get as _http_get, http_get_status as _http_get_status  # noqa: F401
   ```
   щоб і внутрішні виклики, і нинішні патчі тестів працювали.
4. Знайди тест, що патчить `sl._http_get_status` (`grep -n "_http_get_status" tests/*.py`). Якщо він очікує, що
   патч подіє на виклик **через** `_http_get` — переведи патч на `lookup.http_client.http_get_status`
   (`mock.patch.object(http_client, "http_get_status", …)`). Асерції не змінюй.
5. `tests/test_entrypoint.py`: тест, що дозволяв зміну `pton.logs`, тепер має вимагати, що `--help` не створює і не
   змінює **жодного** файлу в `data/`. Це навмисна зміна очікування — поясни в PR.
6. `tests/test_data_dir_untouched.py`: прибери виключення `pton.logs` і коментар `# removed by T16`.
7. Створи `tests/test_http_client.py`: `http_get_status` з `mock.patch.object(http_client.urllib.request, "urlopen")`
   — успіх (статус і тіло), `HTTPError` 429 (повертає статус, не кидає — перевір фактичну поведінку перенесеної
   функції і зафіксуй її), таймаут/`URLError`; `http_get` повертає те саме, що й до переносу (порівняй з
   поведінкою на `origin/main` — прочитай стару функцію).
8. Перевір, що імпорт не пише в `data/`:
   ```bash
   ls -la --time-style=full-iso data/logs/ > /tmp/before
   .venv/bin/python -c "import lookup.steam_lookup"; ls -la --time-style=full-iso data/logs/ > /tmp/after
   diff /tmp/before /tmp/after && echo "import is side-effect free"
   ```

## Не робити

- Не переносити кеш профілів (T17), фетчери джерел (T18), пошук Steam (T19).
- Не змінювати поведінку HTTP (заголовки, таймаути, ретраї).

## Критерії приймання

- Повний набір тестів зелений; `tests/test_golden_scoring.py` зелений **без** регенерації.
- Крок 8 друкує `import is side-effect free`.

## Перевірка

```bash
.venv/bin/python -m unittest tests.test_http_client tests.test_entrypoint tests.test_steam_queue tests.test_data_dir_untouched -v 2>&1 | tail -6
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
```

## Docs delta

`ARCHITECTURE.md` → module map `lookup/`: новий `lookup/http_client.py`; Known limitations: прибрати «Import has
side effects … opens `data/logs/pton.logs`» (лишається читання кешу профілів при імпорті — до T17).
