---
id: T27
title: Replace the TAB watcher globals with a TabWatcher object
wave: 7
depends_on: [T26]
review: none
size: M
golden: []
files:
  - app/tab_watcher.py
  - app/server.py
  - tests/test_tab_watcher.py
  - tests/test_platform.py
  - tests/test_security_guard.py
  - docs/ARCHITECTURE.md
---

# T27 — `app/tab_watcher.py`: стан TAB-вотчера в об'єкті, а не в модульних глобалах

## Мета

Стан вотчера TAB — це глобали `app/server.py` (`_TAB_LOCK`, `_LAST_TAB_TS`, `_TAB_COOLDOWN_SEC`, `_CS2_ONLY`,
`_TAB_DELAY_SEC`, `_NOTIFY_ENABLED`), які змінюють через `global` три функції. Це заважає тестувати вотчер без
`mock.patch` модуля і запускати два сервери в одному процесі. Треба перенести стан і логіку в клас із
залежностями, переданими в конструктор.

## Контекст

- `app/server.py`: `_apply_runtime_config`, `init`, `configure_tab_watcher`, `_send_notification`,
  `schedule_tab_scan`, `_delayed_tab_scan`.
- `app/watch_cs2.py` імпортує `configure_tab_watcher, init, schedule_tab_scan` з `app.server`. Ці імена мають
  лишитися з тими самими підписами.
- `tests/test_platform.py` (близько рядків 212–230) патчить і перевіряє `server._TAB_DELAY_SEC`,
  `server._TAB_COOLDOWN_SEC`, `server._CS2_ONLY`, `server._NOTIFY_ENABLED`; там же тест обгортки сповіщень.
  Перевір `tests/test_security_guard.py`: `grep -n "tab\|TAB" tests/test_security_guard.py`.

## Що зробити

1. Створи `app/tab_watcher.py` з класом `TabWatcher`:
   ```python
   TabWatcher(*, is_cs2_active, run_scan, notify, spawn=None, sleep=time.sleep, clock=time.time)
   ```
   Поля: `delay_sec=0.5`, `cooldown_sec=4.0`, `cs2_only=True`, `notify_enabled=True`. Методи:
   `configure(delay_sec, cooldown_sec, cs2_only)`, `apply_config(cfg: dict)` (ті самі ключі й дефолти, що
   `_apply_runtime_config`), `notify(title, msg)` (нічого, якщо `notify_enabled` False), `schedule() -> dict`
   (точно та сама логіка і ті самі словники-відповіді, що `schedule_tab_scan`), `run_delayed()` (логіка
   `_delayed_tab_scan`, текст сповіщення без змін). `spawn` за замовчуванням запускає daemon-потік.
2. У `app/server.py` створи `TAB = TabWatcher(is_cs2_active=lambda: _is_cs2_active(), run_scan=lambda:
   run_scan(), notify=lambda t, m: send_notification(t, m))`. Лямбди потрібні, щоб тести могли патчити ці імена в
   `server`. Залиш тонкі обгортки: `_apply_runtime_config()` (повертає cfg і викликає `TAB.apply_config`),
   `init()`, `configure_tab_watcher(...)`, `_send_notification(...)`, `schedule_tab_scan()`. Видали шість глобалів
   і `_delayed_tab_scan`.
3. Онови `tests/test_platform.py`: перевіряй `server.TAB.delay_sec` тощо замість глобалів; тест обгортки
   сповіщень — через `server.TAB.notify_enabled`.
4. Створи `tests/test_tab_watcher.py` з фейковими залежностями (`spawn` виконує одразу, `sleep` нічого не робить,
   `clock` керований): ігнор, коли CS2 не у фокусі; debounce у межах `cooldown_sec`; `scheduled` з `delay_sec`;
   `run_delayed` викликає `run_scan` і `notify` з очікуваним текстом; виняток у `run_scan` не виходить назовні;
   `apply_config` з порожнім конфігом ставить дефолти.

## Не робити

- Не змінювати `app/watch_cs2.py`, маршрут `/api/tab-pressed` і тексти відповідей.

## Критерії приймання

- `grep -n "global \|_TAB_\|_CS2_ONLY\|_NOTIFY_ENABLED" app/server.py` нічого не знаходить.
- Нові тести проходять; повний набір зелений; `ruff check .` чистий.

## Перевірка

```bash
.venv/bin/python -m unittest tests.test_tab_watcher tests.test_platform -v 2>&1 | tail -5
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
.venv/bin/ruff check .
```

## Docs delta

`docs/ARCHITECTURE.md`: рядок `app/tab_watcher.py` у таблиці `app/`; опис `app/server.py` без «TAB state». Онови в
цьому ж PR.
