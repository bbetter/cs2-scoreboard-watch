---
id: T07
title: Invalid or non-finite numbers in config fall back per field
wave: 1
depends_on: [T01]
review: none
size: S
golden: [tests/golden/config_golden.json]
files:
  - storage/config_schema.py
  - tests/test_config_schema.py
  - tests/golden/config_golden.json
---

# T07 — Конфіг: невалідні / нескінченні числа → дефолт поля

## Мета

Одне погане число в `data/config.json` (або в `POST /api/config`) не повинно скидати **всі** налаштування чи
давати 500. Погане значення → дефолт **лише цього поля**.

## Контекст

- `storage/config_schema.py`:
  - `clamp_float` (рядок ~92) ловить лише `TypeError, ValueError`. `float("inf")` проходить і клампиться до `max`;
    `float("nan")` теж: `min(max_val, nan)` повертає `max_val`, тобто NaN мовчки стає максимумом.
  - `_normalize_field`, гілка `int`: `int(raw.get(f.name) or 0)` для `inf` кидає **`OverflowError`**, який не
    ловиться. `storage/config_store.load_config` ловить `Exception` і повертає дефолти **всього** конфігу;
    `save_config` пропускає виняток → `POST /api/config` відповідає 500.
- Поведінка зафіксована в `tests/golden/config_golden.json` (Known limitations: «Infinity in data/config.json resets
  all settings»). Після фіксу golden **має** змінитись — це навмисна зміна.

## Що зробити

1. Додай у `storage/config_schema.py` хелпер `_finite_number(val) -> Optional[float]`: повертає `float(val)`, якщо
   значення — `int`/`float`/числовий рядок і `math.isfinite`; інакше `None`. `bool` вважай **не** числом
   (`isinstance(val, bool)` → `None`) — перевір, чи не ламає це golden; якщо ламає для існуючих кейсів з `True`,
   залиш поточну поведінку для `bool` і напиши про це в PR.
2. `clamp_float`: якщо `_finite_number(val) is None` → `default`; інакше як раніше (`round(max(min, min(max, num)), 2)`).
3. Гілка `int` у `_normalize_field`: `raw.get(f.name)` → `_finite_number`; `None` → `f.default`;
   інакше `max(int(f.min), min(int(f.max), int(num)))`. Особливий випадок, який треба зберегти: відсутній ключ або
   `None`/`0`/`""` зараз дає `int(0)` (а не дефолт) — перевір golden; якщо golden очікує 0 для відсутнього ключа,
   збережи це (`raw.get(...) or 0`) і дефолт повертай лише для нечислових / нескінченних значень.
4. Додай у `tests/test_config_schema.py` клас `NonFiniteNumbersTests`:
   - `normalize({"tab_delay_sec": float("inf")})["tab_delay_sec"] == 0.5` (дефолт), те саме для `-inf`, `nan`;
   - `normalize({"leetify_cache_ttl_sec": float("inf")})["leetify_cache_ttl_sec"] == <дефолт поля>` і
     **інші ключі** при цьому мають свої значення, а не дефолти (передай ще `{"tab_cooldown_sec": 7.0}` і перевір 7.0);
   - вагу `ai_weights={"scoreboard": float("nan")}` → дефолт ваги `scoreboard`;
   - `config_store.save_config({"leetify_cache_ttl_sec": float("inf")})` не кидає (пропатч
     `config_store.CONFIG_FILE` у тимчасовий файл, як роблять інші тести цього файлу).
5. Регенеруй golden **лише** командою і прочитай диф:
   ```bash
   .venv/bin/python tests/test_config_parity.py --regen
   git diff --stat tests/golden/config_golden.json
   git diff tests/golden/config_golden.json | head -200
   ```
   Очікувано змінюються **тільки** кейси з `inf` / `nan` (і, можливо, нечислові рядки). Якщо змінилось щось інше —
   фікс неправильний, а не golden: відкоти golden (`git checkout tests/golden/config_golden.json`) і виправ код.

## Не робити

- Не редагувати golden руками. Не змінювати `storage/config_store.py` (його `except Exception` — окрема тема).
- Не змінювати межі `min`/`max` і дефолти полів.

## Критерії приймання

- Нові тести проходять; повний набір зелений з регенерованим golden.
- В описі PR — розділ «Golden diff»: список змінених кейсів (назва кейсу → було → стало), 1 рядок на кейс.

## Перевірка

```bash
.venv/bin/python -m unittest tests.test_config_schema tests.test_config_parity -v 2>&1 | tail -5
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
```

## Docs delta

`ARCHITECTURE.md` → Known limitations: прибрати пункт «Infinity in `data/config.json` resets all settings»;
у recipe (d) додати: «non-finite or non-numeric values fall back to the field default».
