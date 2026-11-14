---
id: T39
title: Stage 2 docs consistency and end-to-end smoke test
wave: 9
depends_on: [T37]
chunk: H-hygiene-docs
review: none
size: S
golden: []
files:
  - docs/ARCHITECTURE.md
  - README.md
  - docs/improvements/PLAN.md
  - docs/improvements/deferred/README.md
---

# T39 — Документація етапу 2 і наскрізний smoke-тест

## Мета

Задачі T22–T29 оновлювали `docs/ARCHITECTURE.md` самі, а задачі чанків A, B, C (T30–T35, T38) і T36–T37 — ні: їхні зміни описано в розділі «Docs delta» тіл PR. Перенеси їх у документ (`gh pr view <n> --json body`). Треба перевірити, що документ і README збігаються з
кодом, закрити застарілі пункти Known limitations і пройти smoke-тест живого сервера з окремим каталогом даних.

## Контекст

- `docs/ARCHITECTURE.md`: Module map, Request flow (таблиця маршрутів), Data shapes, Recipes, Testing, Security
  model, Known limitations.
- Пункт «Tests can write to the real scan history» закрито в T21 (PR bbetter/cs2-scoreboard-watch#44): перевір і
  прибери, якщо так.
- `docs/improvements/PLAN.md`, розділ «Етап 2» — таблиця задач зі статусами.
- Після T38 є `CS2WATCH_DATA_DIR`.

## Що зробити

1. Для кожного пункту Known limitations перевір код (grep / тест). Неактуальні прибери, актуальні уточни.
2. Звір таблицю маршрутів з `server.router.routes()`, Module map з `ls app lookup storage sources scoring`, ключі
   Scan record і Player report з `app/pipeline.py`.
3. Smoke (вивід у PR):
   ```bash
   export CS2WATCH_DATA_DIR=$(mktemp -d)
   .venv/bin/python -m app.server --port 8799 & PID=$!; sleep 2
   curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8799/                      # 200
   curl -s http://127.0.0.1:8799/api/latest | head -c 200; echo                          # JSON
   curl -s http://127.0.0.1:8799/api/sources; echo                                       # 4 джерела
   curl -s http://127.0.0.1:8799/api/config | grep -c TESTKEY                             # 0
   curl -s -o /dev/null -w "%{http_code}\n" -X POST -H "Content-Type: application/json" \
        -d '{"image_path":"/etc/passwd"}' http://127.0.0.1:8799/api/scan                 # 400
   curl -s -o /dev/null -w "%{http_code}\n" -X POST -H "Origin: http://evil.test" \
        -H "Content-Type: application/json" -d '{}' http://127.0.0.1:8799/api/config     # 403
   kill $PID; ls "$CS2WATCH_DATA_DIR"; git status --short data/                          # data/ не змінено
   ```
4. `docs/improvements/PLAN.md`: розділ «Етап 2» — статуси і номери PR усіх задач. `deferred/README.md`: T11–T13
   виконано в етапі 2.

## Не робити

- Не змінювати код. Якщо smoke знайшов баг, запиши його в Follow-ups PR і в Known limitations.

## Критерії приймання

- Усі команди smoke дали очікувані значення (або розбіжність описана в Follow-ups).
- Повний набір тестів зелений.

## Перевірка

```bash
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
grep -n "Tests can write to the real scan history" docs/ARCHITECTURE.md
```

## Docs delta

Уся задача — документація.
