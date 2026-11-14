---
id: T10
title: A failing enrichment still brings the scan to a terminal state
wave: 2
depends_on: [T09]
review: none
size: S
golden: []
files:
  - app/pipeline.py
  - tests/test_pipeline.py
---

# T10 — Enrichment, що впав, доводить скан до кінцевого стану

## Мета

Якщо `enrich_players` або `analyze_players_with_ai` кидає виняток у фоновому потоці, скан зараз назавжди лишається
`loading_steam` / `loading_ai`, а помилка видна лише в traceback потоку. Треба: скан завжди доходить до
`scan_status: "done"`, причина зберігається в записі, подія `scan.finished` публікується.

## Контекст

- `app/pipeline.py`, `ScanPipeline.enrich_scan`: `reports = self._enrich(players, fetch_steam=True,
  on_player_ready=_on_player)` і `player_dicts, ai_error = self._analyze_ai(player_dicts)` — без `try`.
- `ai_error` уже є в записі скану (`None` або рядок) і показується UI.
- `_initial_dicts` будує рядки з `loading=True`, `loading_stage="steam"`.
- Тести пайплайна: `tests/test_pipeline.py` (`FakeRepo`, `FakeEnrich`, синхронний `spawn`).

## Що зробити

1. Новий ключ запису скану **`enrich_error`**: `None` за замовчуванням. Додай його в запис у `run_new` (поруч з
   `"ai_error": None`) і скидай у `None` у `rescan`.
2. У `enrich_scan`:
   - Оберни виклик `self._enrich(...)` у `try/except Exception as exc`. При винятку:
     залогуй `logging.getLogger(__name__).exception("enrichment failed for scan %s", scan_id)`;
     під `self.repo.lock` знайди запис; для кожного рядка `players` постав `loading=False`, `loading_stage="done"`
     (рядки, які `_on_player` уже встиг замінити, лишаються як є); `enrich_error = f"{type(exc).__name__}: {exc}"`;
     `scan_status = "done"`; `progress = {"done": <скільки встиг _on_player>, "total": total}`;
     `duration_ms`; `save_locked()`; потім `publish("scan.finished", snapshot)`; `return`.
   - Оберни `self._analyze_ai(player_dicts)` у `try/except Exception as exc`: при винятку `ai_error =
     f"{type(exc).__name__}: {exc}"`, `player_dicts` лишаються збагаченими (без AI), далі звичайне завершення.
3. Тести в `tests/test_pipeline.py` (новий клас `EnrichmentFailureTests`):
   - `enrich` кидає `RuntimeError("boom")` одразу → `scan_status == "done"`, `enrich_error == "RuntimeError: boom"`,
     усі рядки `loading is False`, опубліковано рівно одне `scan.finished`, `save_locked` викликано;
   - `enrich` встигає викликати `on_player_ready` для першого гравця, потім кидає → перший рядок збагачений,
     решта `loading=False`, `progress == {"done": 1, "total": N}`;
   - `analyze_ai` кидає → `scan_status == "done"`, `ai_error` містить текст винятку, `enrich_error is None`,
     рядки збагачені;
   - успішний шлях: `enrich_error is None` у фінальному записі.

## Не робити

- Не змінювати гілку, де запис скану зник (`if not scan_rec: return`) — її змінять відкладені задачі про гонки (`docs/improvements/deferred/`).
- Не змінювати `app/server.py` і фронтенд (UI покаже `enrich_error` окремою задачею, якщо треба).

## Критерії приймання

- Нові тести проходять; старі тести `tests/test_pipeline.py` зелені без змін асерцій. Якщо якийсь старий тест
  порівнює **повний** словник запису і падає через новий ключ `enrich_error` — допиши ключ в очікуване значення і
  явно вкажи це в PR (це єдина дозволена зміна старих асерцій).

## Перевірка

```bash
.venv/bin/python -m unittest tests.test_pipeline -v 2>&1 | tail -5
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
```

## Docs delta

`ARCHITECTURE.md` → Data shapes / Scan record: додати `enrich_error`; Known limitations: прибрати «A failing
enrichment leaves the scan in `loading_steam` or `loading_ai`».
