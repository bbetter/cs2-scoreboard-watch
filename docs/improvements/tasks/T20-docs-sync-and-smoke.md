---
id: T20
title: Sync ARCHITECTURE.md and README with the plan, final smoke test
wave: 4
depends_on: [T19, T03, T04, T06]
review: none
size: S
golden: []
files:
  - docs/ARCHITECTURE.md
  - README.md
  - docs/improvements/PLAN.md
---

# T20 — Синхронізувати документацію, фінальний smoke

## Мета

Паралельні задачі свідомо не редагували `docs/ARCHITECTURE.md`, а записували зміни в розділ «Docs delta» свого
PR. Зібрати всі дельти в документацію, виправити застарілі твердження і перевірити, що застосунок запускається.

## Контекст

- Злиті PR задач плану: `gh pr list --state merged --search "[T in:title" --limit 50 --json number,title,body`.
  Розділ `## Docs delta` у кожному тілі — джерело правок. Дельти також є в кінці кожного
  `docs/improvements/tasks/Txx-*.md`.
- Відоме застаріле твердження, не повʼязане з задачами: Known limitations → «**Frontend is not modular.**
  `web/app.js` is a single file…» — фронтенд уже розбитий на модулі (`web/api.js`, `state.js`, `sources.js`, …;
  перевір `ls web/` і `tests/test_frontend_modules.py`). Перевір і решту тверджень про `web/app.js`.
- Глобальне правило користувача: **шляхи, ендпоінти, порти, константи в документації — тільки після `grep` по
  коду**, не з памʼяті.

## Що зробити

1. Збери дельти (команда з контексту) у чекліст. Для кожного пункту знайди підтвердження в коді (`grep -n`) і внеси
   в `docs/ARCHITECTURE.md`. Якщо дельта суперечить коду — вір коду і напиши про це в PR.
2. Онови в `ARCHITECTURE.md`:
   - module map (нові модулі: `app/scan_store.py`, `lookup/http_client.py`, `lookup/steam_search.py`,
     `storage/profile_cache.py`; фетчери в `sources/*`);
   - Request flow (guard перед dispatch; `STORE`);
   - Data shapes (`enrich_error`; Leetify-ключ маскується);
   - Recipes (a) і (d);
   - Testing (`ruff check .`, `requirements-dev.txt`, CI 3.11–3.13, `tests/test_data_dir_untouched.py`);
   - Known limitations: прибрати закриті пункти, виправити твердження про фронтенд.
3. `README.md`: лише якщо змінилось те, що там описано (команди встановлення — `requirements-dev.txt` для
   розробки; поведінка безпеки локального сервера — 1-2 речення). Не переписувати README.
4. Перевір кожен шлях і константу, що згадані в змінених рядках:
   ```bash
   git diff origin/main -- docs/ARCHITECTURE.md README.md | grep "^+" | grep -oE '`[^`]+`' | sort -u
   ```
   Для кожного шляху — `ls`, для кожного імені — `grep -rn`. Результат (таблиця «згадка → де знайдено») — у PR.
5. Smoke (вивід — у PR):
   ```bash
   .venv/bin/python -m cs2watch --help >/dev/null && echo "help ok"
   .venv/bin/python -m app.server --port 8799 & sleep 2
   curl -s -o /dev/null -w "index %{http_code}\n"   http://127.0.0.1:8799/
   curl -s -o /dev/null -w "latest %{http_code}\n"  http://127.0.0.1:8799/api/latest
   curl -s -o /dev/null -w "schema %{http_code}\n"  http://127.0.0.1:8799/api/config/schema
   curl -s -o /dev/null -w "rebind %{http_code}\n"  -H "Host: evil.example" http://127.0.0.1:8799/api/latest
   kill %1
   ```
   Очікування: `help ok`, `index 200`, `latest 200`, `schema 200`, `rebind 403`.
6. У `docs/improvements/PLAN.md` зміни рядок «Статус» на «виконано», додай дату і список PR.

## Не робити

- Не змінювати код. Не додавати нових розділів, крім «Security model of the local server» (якщо T02 його не
  описав у дельті — додай за кодом `app/web_router.check_request`).

## Критерії приймання

- Кожна дельта з усіх злитих PR відображена або явно відхилена в PR з причиною.
- Smoke — усі 5 очікувань виконані. Повний набір тестів зелений.
- Таблиця маршрутів у `ARCHITECTURE.md` збігається з `tests/test_router.py::RouteTableTests.EXPECTED`
  (тест документацію не перевіряє — порівняй самостійно і напиши в PR «routes table: matches»).

## Перевірка

```bash
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
.venv/bin/ruff check .
```

## Docs delta

—
