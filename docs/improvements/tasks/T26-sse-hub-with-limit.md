---
id: T26
title: Move SSE subscribers into app/sse.py with a connection limit
wave: 7
depends_on: [T13]
review: none
size: M
golden: []
files:
  - app/sse.py
  - app/server.py
  - tests/test_sse.py
  - docs/ARCHITECTURE.md
---

# T26 — `app/sse.py`: хаб SSE-підписників з лімітом з'єднань

## Мета

Кожен `GET /api/events` тримає окремий потік `ThreadingHTTPServer` назавжди і додає чергу в `_SUBSCRIBERS` без
обмежень. Будь-яка локальна сторінка може відкрити сотні потоків. Треба винести підписників у клас з лімітом і
повертати 503, коли ліміт вичерпано.

## Контекст

- `app/server.py`: `_SUBSCRIBERS_LOCK`, `_SUBSCRIBERS: list[queue.Queue[str]]`, `_broadcast_event(event, payload)`
  (форматує `event: ...\ndata: ...\n\n`, викидає переповнені черги), маршрут `_get_events` (черга `maxsize=20`,
  `: connected`, `: ping` кожні 20 с, прибирання в `finally`).
- Тести патчать `server._broadcast_event` (наприклад, `tests/test_recheck_source.py`, `tests/test_player_db.py`), тому
  ця функція має лишитись у `app/server.py` з тим самим підписом.
- `ThreadingHTTPServer` уже має `daemon_threads = True`.

## Що зробити

1. Створи `app/sse.py` з класом `SseHub(max_subscribers: int = 8, queue_size: int = 20)`:
   - `subscribe() -> Optional[queue.Queue[str]]`: нова черга або `None`, якщо підписників уже `max_subscribers`;
   - `unsubscribe(q)`: безпечно, якщо черги вже немає;
   - `broadcast(event: str, payload) -> None`: те саме форматування і викидання переповнених черг, що зараз;
   - `count() -> int`. Усе під одним `threading.Lock`.
2. У `app/server.py`: `SSE = SseHub()`; `_broadcast_event` викликає `SSE.broadcast(event, payload)`; прибери
   `_SUBSCRIBERS` і `_SUBSCRIBERS_LOCK`. У `_get_events` спершу `q = SSE.subscribe()`; якщо `None`, відповідай
   `h._send_json({"error": "Too many event streams"}, status=503)` **до** будь-яких SSE-заголовків. У `finally` —
   `SSE.unsubscribe(q)`.
3. Створи `tests/test_sse.py`:
   - юніт: ліміт (третя підписка при `max_subscribers=2` → `None`, після `unsubscribe` знову можна); формат
     повідомлення; переповнена черга викидається; `unsubscribe` двічі не падає;
   - живий сервер (патерн `tests/test_router.py::LiveServerTests`) з `mock.patch.object(server, "SSE",
     SseHub(max_subscribers=1))`: перше з'єднання отримує `200 text/event-stream` і `: connected`; друге — `503` з
     JSON-помилкою. Закривай з'єднання в `tearDown`.

## Не робити

- Не змінювати формат подій, назви подій, інтервал ping і `_WIRE_EVENT`.

## Критерії приймання

- `grep -n "_SUBSCRIBERS" app/server.py` нічого не знаходить.
- Нові тести проходять; повний набір зелений; `ruff check .` чистий.

## Перевірка

```bash
.venv/bin/python -m unittest tests.test_sse -v 2>&1 | tail -5
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
.venv/bin/ruff check .
```

## Docs delta

`docs/ARCHITECTURE.md`: рядок `app/sse.py` у таблиці `app/`; абзац «SSE» у Request flow (хаб, ліміт 8, 503);
пункт у «Security model». Онови в цьому ж PR.
