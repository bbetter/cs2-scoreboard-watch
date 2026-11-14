---
id: T33
title: Score a low CSTracker trust rating and drop unknown fields from the AI prompt
wave: 8
depends_on: [T32]
review: none
size: M
golden:
  - tests/golden/scoring_golden.json
  - tests/golden/config_golden.json
files:
  - scoring/thresholds.py
  - scoring/rules.py
  - storage/config_schema.py
  - tests/test_scoring_rules.py
  - tests/golden/scoring_golden.json
  - tests/golden/config_golden.json
  - docs/ARCHITECTURE.md
---

# T33 — Правило для низького CSTracker trust; промпт без неіснуючих полів

## Мета

CSTracker віддає `trust` (0–100), але скоринг його не читає. Водночас дефолтний AI-промпт посилається на поля, яких
жоден парсер не створює (`cstracker.vaclive`, `ttd_ms`, `medals_count`, `has_veteran_coin`, `xp_level`). Треба
додати одне правило скорингу для `trust` і прибрати з промпта поля, яких немає в даних.

## Контекст

- `sources/cstracker.py::_parse_cstracker_html` повертає `{"status", "trust", "sections", "matches"}`; у матчах є
  `ttd`. Перевір сам: `grep -n "vaclive\|ttd\|medal\|veteran\|xp_level\|hltv" sources/*.py lookup/*.py`.
- `scoring/rules.py`: `@rule("external", ...)`, `ScoreContext` (`c.steam.extra_stats`), `Hit(points, reason)`.
  `scoring/thresholds.py` — усі числа.
- Рецепт (b) у `docs/ARCHITECTURE.md`: новий клас у `tests/test_scoring_rules.py` (підклас `RuleCase`), registry-тест
  падає без нього. Golden скорингу — лише через `python tests/test_golden_scoring.py --regen` і з поясненим дифом.
- `storage/config_schema.DEFAULT_AI_PROMPT` входить у дефолтний конфіг, тому зміна промпта змінює
  `tests/golden/config_golden.json` (regen: `python tests/test_config_parity.py --regen`).

## Що зробити

1. `scoring/thresholds.py`: `CST_TRUST_VERY_LOW = 20`, `CST_TRUST_VERY_LOW_POINTS = 10.0`, `CST_TRUST_LOW = 35`,
   `CST_TRUST_LOW_POINTS = 5.0`.
2. `scoring/rules.py`, останнім правилом компонента `external`:
   ```python
   @rule("external", "cstracker_trust")
   def _cstracker_trust(c: ScoreContext) -> list:
   ```
   Застосовується, лише коли `extra_stats.cstracker.status == "ok"` і `trust` — число (`int`/`float`, не `bool`).
   `trust < CST_TRUST_VERY_LOW` → `Hit(CST_TRUST_VERY_LOW_POINTS, f"Дуже низький CSTracker trust ({trust:.0f})")`;
   інакше `trust < CST_TRUST_LOW` → `Hit(CST_TRUST_LOW_POINTS, f"Низький CSTracker trust ({trust:.0f})")`; інакше `[]`.
3. `tests/test_scoring_rules.py`: клас `CstrackerTrust(RuleCase)`: немає CSTracker; статус `blocked`; `trust=None`;
   `trust=True`; межі `CST_TRUST_VERY_LOW - 1`, `CST_TRUST_VERY_LOW`, `CST_TRUST_LOW - 1`, `CST_TRUST_LOW` з точними
   очками й текстом.
4. `DEFAULT_AI_PROMPT`: прибери згадки полів, яких не створює жоден парсер (крок «Контекст»); згадку `ttd`
   переформулюй як «cstracker.matches[].ttd». Решту тексту не змінюй.
5. Regen обох golden. У PR, розділ «Golden diff»: для кожного змінного фікстура скорингу — trust, було → стало
   (`cheat_probability`, нова причина). Очікується зміна лише для фікстур з `trust < 35`. Для config golden —
   лише значення `ai_prompt`.

## Не робити

- Не змінювати інші правила, ваги, пороги і фронтенд (теги VACLive / Veteran у `web/scan-table.js` лишаються,
  запиши їх у Follow-ups).

## Критерії приймання

- Повний набір тестів зелений; `ruff check .` чистий.
- Диф `tests/golden/scoring_golden.json` зачіпає лише фікстури з `trust < 35`.

## Перевірка

```bash
.venv/bin/python -m unittest tests.test_scoring_rules -v 2>&1 | tail -5
.venv/bin/python tests/test_golden_scoring.py --regen && .venv/bin/python tests/test_config_parity.py --regen
git diff --stat -- tests/golden/
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
```

## Docs delta

`docs/ARCHITECTURE.md` → Known limitations: пункт «Scoring pieces with no effect» оновити (trust тепер у скорингу;
промпт не згадує неіснуючих полів). Онови в цьому ж PR.
