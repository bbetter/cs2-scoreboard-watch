---
id: T34
title: Load third-party sources from the cs2watch.sources entry point group
wave: 8
depends_on: [T32, T33]
chunk: C-sources-scoring
review: none
size: M
golden: []
files:
  - sources/base.py
  - sources/registry.py
  - storage/config_schema.py
  - tests/test_source_plugins.py
---

# T34 — Сторонні джерела через entry points `cs2watch.sources`

## Мета

Додати джерело зараз можна лише правкою `sources/registry.py` і `storage/config_schema.ORDER`. Встановлений пакет
має могти додати джерело, оголосивши entry point у групі `cs2watch.sources`, без змін у цьому репозиторії.
Зламаний плагін не повинен ламати застосунок.

## Контекст

- `sources/registry.py`: `REGISTRY = SourceRegistry()` і чотири `register(...)`.
- `sources/base.py::SourceRegistry.register` кидає `ValueError` на порожній чи повторний `id`.
- `storage/config_schema._build_schema()` збирає поля з `CORE_FIELDS` і `source.settings` кожного джерела та кидає
  `ValueError`, якщо набір імен не дорівнює `ORDER`. Це виконується при імпорті модуля.
- Плагін — це код установленого пакета, з тією самою довірою, що й `pip install`.

## Що зробити

1. `SourceRegistry.register(source, *, builtin: bool = True)` і `is_builtin(source_id) -> bool`.
2. `sources/registry.py`: функція `load_plugins(registry, entry_points=None) -> list[str]` (повертає id
   зареєстрованих). Без `entry_points` бере `importlib.metadata.entry_points(group="cs2watch.sources")`. Для кожного:
   `obj = ep.load()`; якщо `obj` — клас, створи екземпляр; приймай лише `DataSource`; реєструй з `builtin=False`.
   Будь-який виняток чи неправильний тип → `logging.getLogger("cs2watch.sources").warning(...)` з іменем entry point
   і пропуск. Змінна оточення `CS2WATCH_DISABLE_PLUGINS=1` вимикає завантаження. Виклич `load_plugins(REGISTRY)`
   після вбудованих `register`.
3. `_build_schema`: перевірка `== ORDER` лише для полів `CORE_FIELDS` і вбудованих джерел. Поля плагінів додаються
   після `ORDER` у порядку реєстрації; поле з уже зайнятим іменем пропускається з warning, джерело лишається.
4. `tests/test_source_plugins.py` (свіжий `SourceRegistry` у кожному тесті, фейкові entry points — об'єкти з
   `name` і `load()`): клас і екземпляр реєструються як не вбудовані; `load()` кидає → warning, інші плагіни
   завантажено; не `DataSource` → пропуск; дубль id → пропуск; `CS2WATCH_DISABLE_PLUGINS=1` → нічого;
   `_build_schema` з підміненим реєстром ставить поле плагіна після `ORDER`, а поле з іменем `steam_api_key`
   пропускає.

## Не робити

- Не змінювати `ORDER`, вбудовані джерела і golden-файли.

## Критерії приймання

- Нові тести проходять; повний набір зелений (у середовищі CI плагінів немає); `ruff check .` чистий.

## Перевірка

```bash
.venv/bin/python -m unittest tests.test_source_plugins tests.test_config_schema -v 2>&1 | tail -5
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
.venv/bin/ruff check .
```

## Docs delta

Не редагуй `docs/ARCHITECTURE.md` (його паралельно змінювали б інші чанки): опиши зміни для документа в розділі «Docs delta» тіла PR, підрозділ цієї задачі. T39 перенесе їх у документ.

Що описати: `docs/ARCHITECTURE.md`: рецепт (a) — підрозділ «As a plugin package» з прикладом `[project.entry-points."cs2watch.sources"]`,
правило про поля налаштувань і `CS2WATCH_DISABLE_PLUGINS`; «Security model» — довіра до плагінів.
