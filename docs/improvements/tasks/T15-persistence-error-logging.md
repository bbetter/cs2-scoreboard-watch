---
id: T15
title: Log history load/save failures and keep a corrupt history file
wave: 2
depends_on: [T14]
review: none
size: S
golden: []
files:
  - app/scan_store.py
  - tests/test_scan_store.py
---

# T15 — Логувати помилки load/save історії, не губити битий файл

## Мета

`ScanStore.load` / `save_locked` (після T09) мовчки ковтають усі винятки. Гірше: якщо
`data/scans_history.json` битий, load повертає порожній список, і **наступний save перезаписує файл** — історія
втрачена без сліду. Треба логувати і зберігати битий файл поруч.

## Контекст

- `app/scan_store.py` — перенесені дослівно з сервера `except Exception: pass` (T09).
- Для JSON-сховищ у проекті вже є прецедент «відкласти битий файл»: `.gitignore` згадує `feedback.json.corrupt`
  — подивись, як це зроблено в `storage/feedback_store.py`, і повтори той самий підхід/назву.
- Логування: модулі використовують `logging.getLogger(...)` (`lookup/steam_lookup.py` — `"pton"`); без
  налаштованого handler-а записи рівня WARNING+ ідуть у stderr (lastResort) — цього достатньо.

## Що зробити

1. `_LOG = logging.getLogger("cs2watch.scan_store")`.
2. `load`:
   - файл відсутній → як раніше, тихо;
   - `json.loads` / читання кидає → `_LOG.warning("history %s is unreadable (%s); moved to %s", ...)` і
     перейменуй файл у `<name>.corrupt` (якщо такий уже є — `<name>.corrupt.<YYYYmmdd_HHMMSS>`) через `os.replace`;
     сам `os.replace` теж може впасти — тоді `_LOG.error(...)` і нічого не перейменовуй;
   - JSON валідний, але не список → те саме, що битий файл.
3. `save_locked`: `except Exception: _LOG.exception("failed to save history to %s", self.history_file)` (без `pass`),
   стан у памʼяті не змінюється.
4. Тести в `tests/test_scan_store.py` (новий клас `PersistenceErrorTests`, `self.assertLogs("cs2watch.scan_store", ...)`):
   битий JSON → WARNING, файл `.corrupt` існує з оригінальним вмістом, оригінального файлу немає, `scans == []`;
   другий битий файл при наявному `.corrupt` → файл з таймстампом; не-список → `.corrupt`; `save_locked` при
   `mock.patch("app.scan_store.atomic_write_text", side_effect=OSError("disk full"))` → ERROR-лог з `disk full`,
   виняток не виходить назовні.
   Онови з T09 тест «битий JSON → порожній список без винятку» лише якщо він тепер падає через перейменування (він
   має й далі проходити — перевір).

## Не робити

- Не змінювати формат файлу історії і `atomic_io`.
- Не налаштовувати глобальний logging (`basicConfig`) — це не задача модуля.

## Критерії приймання

- Нові тести проходять; повний набір зелений.
- `grep -n "except Exception:\s*$" -A1 app/scan_store.py | grep pass` → порожньо.

## Перевірка

```bash
.venv/bin/python -m unittest tests.test_scan_store -v
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
```

## Docs delta

`ARCHITECTURE.md` → module map `app/scan_store.py`: «a corrupt history file is moved to
`scans_history.json.corrupt` and logged»; `.gitignore` уже ігнорує `data/` повністю — змін не треба.
