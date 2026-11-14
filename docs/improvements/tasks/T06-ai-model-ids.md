---
id: T06
title: Update Anthropic model aliases in the AI analyzer
wave: 1
depends_on: [T01]
review: none
size: S
golden: []
files:
  - lookup/ai_analyzer.py
  - tests/test_ai_models.py
---

# T06 — Актуальні ID моделей в AI-аналізаторі

## Мета

Провайдер `anthropic_api` резолвить аліаси `sonnet` / `haiku` / `opus` у застарілі ID моделей. Оновити мапу,
винести її в константу з функцією й покрити тестом.

## Контекст

- `lookup/ai_analyzer.py:202-211`, `_run_anthropic_api`:
  ```python
  model_map = {"sonnet": "claude-sonnet-4-20250514", "haiku": "claude-3-5-haiku-latest", "opus": "claude-opus-4-20250514"}
  resolved_model = model_map.get(model.strip().lower(), model.strip() or "claude-sonnet-4-20250514")
  ```
- Актуальні моделі (родина Claude 5): `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5`.
- Конфіг: `ai_model` — довільний рядок, дефолт `"sonnet"` (`storage/config_schema.py:49`). Користувач може вписати
  повний ID — він має передаватись як є.
- Провайдер `claude_cli` передає `--model <alias>` у CLI як є — CLI сам знає аліаси; його **не** чіпай.

## Що зробити

1. На рівні модуля в `lookup/ai_analyzer.py`:
   ```python
   ANTHROPIC_MODEL_ALIASES: dict[str, str] = {
       "opus": "claude-opus-5-5",
       "sonnet": "claude-sonnet-5-5",
       "haiku": "claude-haiku-5-5",
   }
   DEFAULT_ANTHROPIC_MODEL = ANTHROPIC_MODEL_ALIASES["sonnet"]


   def resolve_anthropic_model(model: Optional[str]) -> str:
       """Alias (any case, surrounding spaces) -> full id; a full id passes through; empty -> default."""
   ```
2. У `_run_anthropic_api` заміни локальний `model_map` і вираз `resolved_model` на `resolve_anthropic_model(model)`.
   Решта функції (тіло запиту, заголовки, парсинг) — без змін.
3. Створи `tests/test_ai_models.py`: `"sonnet"`, `" Sonnet "`, `"HAIKU"`, `"opus"` → повні ID; `""`, `None`,
   `"   "` → `DEFAULT_ANTHROPIC_MODEL`; `"claude-sonnet-5-5"` і будь-який інший рядок (`"my-custom-model"`) →
   без змін (лише `strip()`); жоден ID у `ANTHROPIC_MODEL_ALIASES` не містить `"-4-"`, `"3-5"` чи дати `2025`.
4. Додатково — тест, що `_run_anthropic_api` відправляє резолвлений ID: пропатч
   `mock.patch.object(ai_analyzer.urllib.request, "urlopen")` так, щоб він кидав `RuntimeError("stop")`,
   перехопи аргумент `Request`, розпарс `req.data` і перевір `body["model"] == "claude-sonnet-5-5"` для `"sonnet"`.

## Не робити

- Не змінювати промпт, схему відповіді, `anthropic-version`, `max_tokens`, обробку помилок.
- Не додавати SDK `anthropic` у залежності.
- Не чіпати `tests/golden/ai_compact_golden.json` (payload не змінюється).

## Критерії приймання

- `grep -n "claude-sonnet-4\|claude-3-5\|claude-opus-4" lookup/ai_analyzer.py` → порожньо.
- Нові тести проходять; `tests/test_ai_compact_parity.py` зелений без регенерації.

## Перевірка

```bash
.venv/bin/python -m unittest tests.test_ai_models -v
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
```

## Docs delta

`ARCHITECTURE.md` → module map, `lookup/ai_analyzer.py`: «model aliases in `ANTHROPIC_MODEL_ALIASES`».
