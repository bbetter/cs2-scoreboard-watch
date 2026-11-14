---
id: T32
title: Build the AI payload and feedback features from the source registry
wave: 8
depends_on: [T29]
chunk: C-sources-scoring
review: none
size: M
golden: []
files:
  - sources/base.py
  - sources/leetify.py
  - sources/faceit.py
  - sources/cstracker.py
  - sources/steam_stats.py
  - lookup/ai_analyzer.py
  - storage/feedback_store.py
  - tests/test_registry_payloads.py
---

# T32 — AI-пейлоад і feedback будуються з реєстру джерел

## Мета

`lookup/ai_analyzer._compact_player_for_ai` явно бере `REGISTRY.get("leetify")` і `REGISTRY.get("faceit")`, а
`storage/feedback_store.build_feedback_entry` — лише Leetify. Нове джерело не потрапляє ні в AI, ні у feedback, хоча
`DataSource` уже має `ai_compact()` і `features()`. Треба пройтися по реєстру, не змінивши вихід для вбудованих
джерел.

## Контекст

- Вихід заморожено: `tests/golden/ai_compact_golden.json` (`test_ai_compact_parity.py`) і
  `tests/golden/feedback_golden.json` (`test_feedback_parity.py`). Обидва мають пройти **без** `--regen`.
- Поточна поведінка AI: блок `"leetify"` є завжди; `"faceit"` — лише якщо `ai_compact` повернув непорожнє; CSTracker
  іде всередині `"extra_stats"` (весь словник), Steam CS2 stats — у `"steam"]["cs2_stats"]`.
- Поточна поведінка feedback: `features["leetify"] = {"present": ok, **features}`; `cstracker_status` окремо.
- `sources/base.py`: `DataSource` (слот за замовчуванням `steam.sources[<id>]`), `LegacySlotSource`.

## Що зробити

1. У `DataSource` додай атрибути класу `payload_key: Optional[str] = ""` і `payload_always: bool = False`.
   `""` означає «ключ = `id`», `None` — «джерело не має власного блоку». У `LegacySlotSource`
   `payload_key = None`. Вбудовані: `LeetifySource.payload_key = "leetify"`, `payload_always = True`;
   `FaceitSource.payload_key = "faceit"`; CSTracker і Steam stats лишаються `None`. Хелпер
   `DataSource.payload_name() -> Optional[str]`.
2. `_compact_player_for_ai`: замість двох явних блоків цикл по `REGISTRY`: `name = src.payload_name()`; якщо
   `None` — пропусти; `block = src.ai_compact(src.normalize(src.read_raw(steam)))`; додай, якщо `payload_always`
   або `block`. Порядок ключів: як зараз для вбудованих (`leetify` після `steam`, `faceit` після нього), нові
   джерела — після `faceit`, перед `extra_stats`.
3. `build_feedback_entry`: цикл по `REGISTRY` з тим самим `payload_name()`; блок
   `{"present": res.ok, **src.features(res)}` додається, якщо `payload_always` або `src.features(res)` непорожній.
   `cstracker_status` лишається як є.
4. `tests/test_registry_payloads.py`: тимчасовий реєстр з вбудованими джерелами плюс фейкове `ExampleSource(DataSource)`
   (`id="example"`, `features` повертає `{"score": 1.0}`), підмінений через `mock.patch.object` у `ai_analyzer` і
   `feedback_store`. Перевір: блок `example` є в AI і feedback, коли дані є в `steam.sources.example`; його немає,
   коли даних немає; для гравця без `example` вихід дорівнює golden-кейсу.

## Не робити

- Не змінювати golden-файли, `_build_prompt`, тексти промпта і схему feedback для вбудованих джерел.

## Критерії приймання

- `grep -n 'REGISTRY.get("leetify")\|REGISTRY.get("faceit")' lookup/ai_analyzer.py storage/feedback_store.py`
  нічого не знаходить.
- `tests.test_ai_compact_parity` і `tests.test_feedback_parity` проходять без regen; повний набір зелений;
  `ruff check .` чистий.

## Перевірка

```bash
.venv/bin/python -m unittest tests.test_registry_payloads tests.test_ai_compact_parity tests.test_feedback_parity -v 2>&1 | tail -5
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
git diff --stat origin/main -- tests/golden/   # порожньо
```

## Docs delta

Не редагуй `docs/ARCHITECTURE.md` (його паралельно змінювали б інші чанки): опиши зміни для документа в розділі «Docs delta» тіла PR, підрозділ цієї задачі. T39 перенесе їх у документ.

Що описати: `docs/ARCHITECTURE.md`: рецепт (a) — `payload_key` / `payload_always`; прибрати AI payload і feedback зі списку
«does not update automatically».
