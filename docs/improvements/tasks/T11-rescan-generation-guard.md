---
id: T11
title: Rescan generation token so a stale enrichment cannot write into a new run
wave: 6
depends_on: [T25]
review: none
size: M
golden: []
files:
  - app/pipeline.py
  - tests/test_pipeline.py
  - tests/test_player_uid.py
  - docs/ARCHITECTURE.md
---

# T11 — Токен покоління rescan

## Мета

Rescan зберігає `scan_id`, тому `player_uid` нового прогону збігаються зі старими. Повільна enrichment старого
прогону може записати свої рядки в новий прогін і позначити скан `done`. Треба, щоб enrichment писала **лише** у
свій прогін.

## Контекст

- Баг відтворює `tests/test_player_uid.py::RaceExpectedFailureTests::test_stale_enrichment_of_an_old_run_does_not_overwrite_a_rescan`
  (зараз `@unittest.expectedFailure`). Прочитай його повністю — він і є специфікацією.
- `app/pipeline.py`: `run_new` / `rescan` → `_start_enrichment(scan_id, players, started)` →
  `enrich_scan(scan_id, players, started)`. Усі записи в `enrich_scan` (у `_on_player`, перед AI, фінальний,
  а після T10 — і гілка помилки) шукають запис через `self._find(scan_id)`.
- `app/server.py` має `_enrich_scan_async(scan_id, players, started)` → `PIPELINE.enrich_scan(...)` (тести можуть
  його викликати) — сигнатура має лишитись сумісною.

## Що зробити

1. Новий ключ запису скану **`run_id`**: `uuid.uuid4().hex[:8]`. `run_new` ставить його в новий запис; `rescan`
   генерує **новий** `run_id` і записує в `scan_rec` під lock (разом з іншими полями).
2. `enrich_scan(self, scan_id, players, started, run_id: Optional[str] = None)`. Додай приватний метод
   ```python
   def _find_run(self, scan_id: str, run_id: Optional[str]) -> Optional[dict]:
       rec = self._find(scan_id)
       if rec is None:
           return None
       if run_id is not None and rec.get("run_id") != run_id:
           return None          # a newer run owns this record now
       return rec
   ```
   і заміни **кожен** `self._find(scan_id)` в `enrich_scan` (включно з `_on_player`, гілкою AI, фінальним записом і
   гілкою помилки з T10) на `self._find_run(scan_id, run_id)`. Якщо запис «чужий» — нічого не пиши і **не
   публікуй** події (`player.enriched`, `scan.updated`, `scan.finished`) для цього прогону.
   Порада: у `_on_player` перенеси `self.bus.publish(...)` так, щоб він виконувався лише коли запис знайдено.
3. `_start_enrichment(scan_id, players, started, run_id)` передає `run_id` далі; `run_new` і `rescan` передають
   `run_id` свого запису. `run_id=None` (старі виклики, `_enrich_scan_async`) = без перевірки, як раніше.
4. У `tests/test_player_uid.py` прибери `@unittest.expectedFailure` **лише** з
   `test_stale_enrichment_of_an_old_run_does_not_overwrite_a_rescan` і онови докстрінг класу/коментар, що цей
   тест більше не відомий баг. Решту двох `expectedFailure` не чіпай (це T13).
5. Додай у `tests/test_pipeline.py` клас `RunGenerationTests`:
   - після `run_new` у записі є `run_id` (рядок довжини 8); після `rescan` — інший `run_id`;
   - відкладена enrichment старого прогону не публікує жодної події (зібрати події через
     `EventBus.subscribe` на всі топіки з `app/events.py`) і не викликає `save_locked`;
   - відкладена enrichment **нового** прогону після цього нормально доводить запис до `done` з даними нового прогону;
   - `enrich_scan(..., run_id=None)` поводиться як раніше (пише в запис).

## Не робити

- Не змінювати логіку злиття рядків (T12) і маршрути сервера.
- Не видаляти тест-кейс з `RaceExpectedFailureTests` і не змінювати його асерції.

## Критерії приймання

- Повний набір тестів зелений; **`expected failures=2`** (було 3).
- Тест з кроку 4 проходить без `expectedFailure`.

## Перевірка

```bash
.venv/bin/python -m unittest tests.test_player_uid tests.test_pipeline -v 2>&1 | tail -8
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3     # OK (expected failures=2)
```

## Docs delta

Онови `docs/ARCHITECTURE.md` у цьому ж PR. Data shapes / Scan record: додати `run_id`; Known limitations: прибрати «Rescan can race with a
stale enrichment».
