---
id: T09
title: Extract scan state into app/scan_store.py (no behaviour change)
wave: 2
depends_on: [T02, T08]
review: none
size: M
golden: []
files:
  - app/scan_store.py
  - app/server.py
  - tests/test_scan_store.py
  - tests/test_player_db.py
  - tests/test_player_uid.py
  - tests/test_recheck_source.py
  - tests/test_sources_view.py
---

# T09 — Винести стан сканів у `app/scan_store.py` (без зміни поведінки)

## Мета

Зараз стан сканів — це модульні глобали `app/server.py` (`_SCANS`, `_STATE_LOCK`, `HISTORY_FILE`,
`_load_history`, `_save_history_locked`, `_ServerRepo`). Зібрати їх в один клас `ScanStore`, щоб наступні задачі
(T10, T14, T15, а пізніше відкладені задачі про гонки) були маленькими методами з юніт-тестами. **Це чистий рефакторинг: жодна HTTP-відповідь,
жоден запис у файл історії не змінюється.**

## Контекст

- `app/pipeline.py` вже описує інтерфейс `ScanRepo` (`lock`, `scans`, `max_history`, `save_locked()`), а
  `app/server.py` реалізує його класом `_ServerRepo`, який **на момент виклику** читає глобали модуля — саме для
  того, щоб тести могли патчити `server._SCANS` / `server.HISTORY_FILE`.
- `_STATE_LOCK` зараз охороняє і список сканів, і список SSE-підписників `_SUBSCRIBERS` (`_broadcast_event`,
  `_get_events`).
- Тести, що патчать ці глобали (перевір `grep -n "_SCANS\|HISTORY_FILE\|_STATE_LOCK\|_load_history\|_save_history_locked" tests/*.py`):
  `tests/test_player_db.py`, `tests/test_player_uid.py`, `tests/test_recheck_source.py`, `tests/test_sources_view.py`.
- `_adapt_legacy_scan` залежить від `sources.REGISTRY` — це логіка сервера, не сховища.

## Що зробити

1. Створи `app/scan_store.py`:
   ```python
   class ScanStore:
       """Scan records (newest first), their lock and their JSON file. Implements app.pipeline.ScanRepo."""

       def __init__(self, history_file: Path, max_history: int = 100) -> None:
           self.lock = threading.Lock()
           self.scans: list[dict] = []
           self.history_file = history_file
           self.max_history = max_history

       def load(self, adapt: Optional[Callable[[dict], None]] = None) -> None: ...   # тіло = нинішній _load_history
       def save_locked(self) -> None: ...                                              # тіло = нинішній _save_history_locked
       def find_locked(self, scan_id: Optional[str]) -> Optional[dict]: ...           # next((s for s in scans if s["id"] == scan_id), None)
       def latest_locked(self) -> Optional[dict]: ...                                 # scans[0] if scans else None
   ```
   Поведінку (включно з `except Exception: pass` у load/save, обрізанням до `max_history` при завантаженні і
   викликом `adapt` для кожного dict-скану) перенеси **дослівно**. Логування помилок — це T15, не зараз.
   Модуль не імпортує нічого з `app.server`, `lookup`, `sources`.
2. У `app/server.py`:
   - `STORE = ScanStore(HISTORY_FILE, _MAX_HISTORY)`; глобал `_SCANS` видали.
   - Заведи окремий `_SUBSCRIBERS_LOCK = threading.Lock()` для `_SUBSCRIBERS` (у `_broadcast_event` і
     `_get_events`), а всі місця, де `_STATE_LOCK` охороняв скани, заміни на `STORE.lock`. Глобал `_STATE_LOCK`
     видали (аліас **не** залишай — так `grep` гарантовано знайде всі пропущені місця).
   - `_load_history()` → `STORE.load(adapt=_adapt_legacy_scan)`; `_save_history_locked()` → `STORE.save_locked()`.
     Залиш ці дві функції як тонкі обгортки (їх викликають тести і `main()`).
   - `next((s for s in _SCANS if s["id"] == scan_id), None)` → `STORE.find_locked(scan_id)`;
     `_SCANS[0] if _SCANS else None` → `STORE.latest_locked()`; ітерації `for s in _SCANS` → `STORE.scans`.
   - `_ServerRepo` перероби так, щоб він делегував у **поточний** `STORE` модуля на момент виклику
     (`return STORE.lock`, `return STORE.scans`, `STORE.max_history`, `STORE.save_locked()`) — тоді
     `mock.patch.object(server, "STORE", ScanStore(tmp_path))` працює і для `PIPELINE`.
   - `HISTORY_FILE` залиш (його читає `STORE` при створенні; тести тепер патчать `STORE`, а не `HISTORY_FILE`).
3. Онови тести з контексту: заміни
   `mock.patch.object(server, "_SCANS", [...])` + `mock.patch.object(server, "HISTORY_FILE", tmp)` на
   ```python
   store = ScanStore(Path(tmpdir) / "scans_history.json")
   store.scans[:] = [...]
   mock.patch.object(server, "STORE", store)
   ```
   а прямі звертання `server._SCANS` → `server.STORE.scans`, `server._STATE_LOCK` → `server.STORE.lock`.
   **Асерції не змінюй.** Якщо тест неможливо перевести без зміни асерції — зупинись, `Blocked`, опиши.
4. Створи `tests/test_scan_store.py`: save → load повертає ті самі скани; load обрізає до `max_history`;
   load з битим JSON → порожній список і без винятку; load з не-списком → порожньо; `adapt` викликається для кожного
   dict-скану і не викликається для не-dict; `find_locked` / `latest_locked` на порожньому і непорожньому сторі;
   `save_locked` пише атомарно (файл існує, валідний JSON, `ensure_ascii=False` — кирилиця не екранована).

## Не робити

- Не виправляти жодних гонок (вони відкладені в `docs/improvements/deferred/`) — навіть якщо бачиш їх. Не змінювати `app/pipeline.py`.
- Не змінювати відповіді маршрутів, коди помилок, формат `data/scans_history.json`.
- Не перейменовувати маршрути / функції-маршрути (`tests/test_router.py` заморожує таблицю).

## Критерії приймання

- `grep -n "_SCANS\|_STATE_LOCK" app/server.py` → порожньо.
- Повний набір тестів зелений; `expected failures=3` (ті самі 3 тести).
- `git diff origin/main -- tests/ | grep "^[-+].*assert"` — порожньо, крім нового `tests/test_scan_store.py`.

## Перевірка

```bash
.venv/bin/python -m unittest tests.test_scan_store -v
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
git diff origin/main -- tests/ ':!tests/test_scan_store.py' | grep -E "^[-+]\s*self\.assert" ; echo "assert-lines-changed=$?"   # очікуємо 1
.venv/bin/ruff check .
```

## Docs delta

`ARCHITECTURE.md` → module map `app/`: новий рядок `app/scan_store.py` (`ScanStore`: scan list, lock, history
file); request flow: `_save_history_locked` → `STORE.save_locked`; Data shapes / Scan record: «Written only through
`ScanStore.save_locked`».
