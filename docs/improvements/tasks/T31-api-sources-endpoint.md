---
id: T31
title: GET /api/sources and frontend labels from the source registry
wave: 8
depends_on: [T30]
review: none
size: M
golden: []
files:
  - app/server.py
  - web/api.js
  - web/app.js
  - web/state.js
  - web/sources.js
  - tests/test_router.py
  - tests/test_sources_endpoint.py
  - tests/js/sources_test.mjs
  - docs/ARCHITECTURE.md
---

# T31 — `GET /api/sources`: фронтенд бере назви джерел з бекенду

## Мета

Фронтенд дублює реєстр джерел: `web/api.js` вибирає підпис через `source === "leetify" ? "Leetify" : "CSTracker"`,
а для джерела, якого немає у `web/sources.js`, generic-чіп і вкладка показують сирий `id`. Нове джерело на бекенді
має отримати людську назву в UI без правки JS.

## Контекст

- `sources/base.py`: у кожного джерела є `id`, `label`, `recheckable`, `in_coverage`; `REGISTRY` ітерується в
  порядку реєстрації.
- `web/sources.js`: `genericSourceEntries`, `sourceChipsFor` і `sourceTabsFor` (generic-гілки ставлять `label: id`),
  вбудовані записи `SOURCES` з власними підписами.
- `web/api.js::retrySource` — хардкод підпису; `web/state.js` — об'єкт `state`; `web/app.js` наприкінці викликає
  `fetchLatest(); connectSSE();`.
- `tests/test_router.py::RouteTableTests.EXPECTED` заморожує маршрути. `tests/golden/frontend_render_golden.json`
  (рендер у Node без мережі) не повинен змінитися: коли метаданих немає, поведінка як зараз.

## Що зробити

1. Маршрут `@router.route("GET", "/api/sources")` → `h._send_json({"sources": [{"id", "label", "recheckable",
   "in_coverage"} ...]})` у порядку `REGISTRY`. Додай пару в `EXPECTED`.
2. `web/state.js`: поле `sourceMeta: new Map()`. `web/api.js`: `export async function fetchSourceMeta()` — заповнює
   `state.sourceMeta` (id → запис); помилка мережі тихо лишає мапу порожньою. `web/app.js`: виклич перед
   `fetchLatest()` (без `await`, щоб не блокувати старт; після завантаження перерендер не обов'язковий).
3. `retrySource`: `const sourceLabel = state.sourceMeta.get(source)?.label ?? source;`.
4. `web/sources.js`: у generic-гілках `label: state.sourceMeta.get(id)?.label ?? id`. Вбудовані записи не змінюй.
5. `tests/test_sources_endpoint.py`: живий сервер, `GET /api/sources` → 200, ids дорівнюють `REGISTRY.ids()`,
   `recheckable` для `leetify` і `cstracker` True. `tests/js/sources_test.mjs`: generic-джерело `example` без
   метаданих має підпис `example`; після `state.sourceMeta.set("example", {label: "Example"})` — `Example`;
   прибери запис після тесту.

## Не робити

- Не змінювати вбудовані записи `SOURCES`, стани чіпів і golden рендеру.

## Критерії приймання

- `grep -n '"Leetify" : "CSTracker"' web/api.js` нічого не знаходить.
- Нові тести проходять; `tests.test_frontend_modules` проходить; повний набір зелений; `ruff check .` чистий.

## Перевірка

```bash
.venv/bin/python -m unittest tests.test_sources_endpoint tests.test_router tests.test_frontend_modules -v 2>&1 | tail -5
node --import ./tests/js/register.mjs tests/js/sources_test.mjs
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
```

## Docs delta

`docs/ARCHITECTURE.md`: таблиця маршрутів (`GET /api/sources`); рецепт (a) «What updates automatically» — назва в
UI; Known limitations про хардкод імен у фронтенді — уточнити, що лишилось. Онови в цьому ж PR.
