---
id: T28
title: Move player write operations and match grouping out of app/server.py
wave: 7
depends_on: [T27]
review: none
size: M
golden: []
files:
  - app/player_ops.py
  - app/matches.py
  - app/server.py
  - tests/test_player_ops.py
  - tests/test_matches.py
  - docs/ARCHITECTURE.md
---

# T28 — `app/player_ops.py` і `app/matches.py`: логіка поза маршрутами

## Мета

`app/server.py` змішує HTTP-маршрути з доменною логікою: пошук і заміна рядків гравців, повтор одного джерела,
групування сканів у матчі. Цю логіку не можна протестувати без HTTP. Треба винести її в два модулі без
залежності від HTTP, а в `server.py` лишити маршрути й тонкі обгортки.

## Контекст

- Кандидати на перенесення в `app/server.py`: `_player_matches`, `_source_choices_text`, `_refresh_source_entry`,
  `recheck_source_for_player`, `_build_matches_locked`, а також хелпери запису рядків за `player_uid`, які додали
  T12–T13 (`_mark_edited` і місця, де маршрути замінюють рядки в `scan_rec["players"]`).
- Тести патчать імена в модулі `server` (`server.STORE`, `server._broadcast_event`, `server._save_history_locked`,
  `server.REGISTRY` тощо). Тому винесені функції отримують залежності параметрами, а обгортки в `server.py`
  передають поточні значення модульних імен **у момент виклику**.
- `tests/test_router.py` заморожує таблицю маршрутів: вона не змінюється.

## Що зробити

1. `app/matches.py`: `build_matches(scans: list[dict]) -> list[dict]` — дослівно тіло `_build_matches_locked`, але
   над переданим списком. У `server.py`: `_build_matches_locked()` повертає `build_matches(STORE.scans)`.
2. `app/player_ops.py`:
   - `player_matches(p, uid, nick)`, `source_choices_text(ids)`, `refresh_source_entry(steam, src, registry)`;
   - `find_player(scan_rec, uid, nick) -> Optional[dict]` і `replace_player_by_uid(scan_rec, player) -> bool`
     (заміна рядка з тим самим `player_uid` у поточному списку; False, якщо його немає);
   - `recheck_source(store, registry, scan_id, nick, source, player_uid, *, make_ctx, save_locked, broadcast)
     -> tuple[int, dict]` — тіло `recheck_source_for_player` з тими самими статусами й текстами помилок.
3. У `app/server.py` залиш обгортки з тими самими іменами й підписами (`_player_matches`, `_source_choices_text`,
   `_refresh_source_entry`, `recheck_source_for_player`), які делегують у `player_ops` і передають
   `STORE`, `REGISTRY`, `_save_history_locked`, `_broadcast_event` у момент виклику. Маршрути, що замінюють рядки
   за uid, викликають `replace_player_by_uid`.
4. `tests/test_matches.py`: групування за `match_id`, fallback `session_<date>`, тривалість, `max_risk`, порядок
   (на синтетичних записах, без сервера). `tests/test_player_ops.py`: `find_player` за uid і за ніком,
   `replace_player_by_uid` (є / немає рядка, інші рядки не змінено), `recheck_source` з фейковими store, registry і
   колбеками: 400 для невідомого джерела, 404 без скану, 409 без SteamID64, 502 при винятку, 200 і виклик
   `save_locked` та `broadcast`.

## Не робити

- Не змінювати маршрути, статуси, тексти помилок і формат відповідей.
- Не переносити самі маршрути в окремі модулі.

## Критерії приймання

- Повний набір тестів зелений без змін у наявних тестах; нові тести проходять; `ruff check .` чистий.
- `wc -l app/server.py` менше, ніж до задачі (вкажи обидва числа в PR).

## Перевірка

```bash
.venv/bin/python -m unittest tests.test_player_ops tests.test_matches -v 2>&1 | tail -5
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
.venv/bin/ruff check .
```

## Docs delta

`docs/ARCHITECTURE.md`: рядки `app/player_ops.py` і `app/matches.py` у таблиці `app/`; Request flow: маршрути
делегують у `player_ops`. Онови в цьому ж PR.
