---
id: T38
title: CS2WATCH_DATA_DIR and CS2WATCH_WEB_DIR overrides for runtime paths
wave: 8
depends_on: [T29]
chunk: B-lookup-paths
review: none
size: S
golden: []
files:
  - storage/paths.py
  - scripts/cs_scan.sh
  - tests/test_paths.py
  - README.md
---

# T38 — Перевизначення каталогів даних і UI змінними оточення

## Мета

`storage/paths.py` завжди бере `data/` і `web/` відносно кореня репозиторію. Встановлена копія (`pip install`) не
знайде UI, а дані не можна винести за межі робочої копії (наприклад, для другого екземпляра чи smoke-тесту).
Треба дозволити перевизначення змінними оточення, без зміни поведінки за замовчуванням.

## Контекст

- `storage/paths.py`: `ROOT`, `DATA_DIR = ROOT / "data"`, `LOG_DIR`, `SCREENSHOT_DIR`, `WEB_DIR = ROOT / "web"`.
  Значення обчислюються при імпорті.
- `scripts/cs_scan.sh`: `DATA_DIR="$BASE_DIR/data"`.
- Known limitations у `docs/ARCHITECTURE.md`: «`pip install` of this project is not self-contained».

## Що зробити

1. `storage/paths.py`: `DATA_DIR` = `Path(os.environ["CS2WATCH_DATA_DIR"]).expanduser().resolve()`, якщо змінна є
   і непорожня, інакше `ROOT / "data"`. Так само `WEB_DIR` з `CS2WATCH_WEB_DIR`. `LOG_DIR` і `SCREENSHOT_DIR`
   похідні від `DATA_DIR`. Каталоги не створюються при імпорті.
2. `scripts/cs_scan.sh`: `DATA_DIR="${CS2WATCH_DATA_DIR:-$BASE_DIR/data}"`; передай змінну серверу, який скрипт
   запускає (вона успадковується).
3. `tests/test_paths.py`: запускай окремий інтерпретатор (`subprocess.run([sys.executable, "-c", ...], env=...)`),
   бо значення обчислюються при імпорті: без змінних — `ROOT/data` і `ROOT/web`; з `CS2WATCH_DATA_DIR=<tmp>` —
   `DATA_DIR`, `LOG_DIR`, `SCREENSHOT_DIR` під `<tmp>`; порожня змінна = за замовчуванням; імпорт нічого не створює
   в `<tmp>`.
4. README: розділ про стан програми — змінні `CS2WATCH_DATA_DIR`, `CS2WATCH_WEB_DIR`.

## Не робити

- Не змінювати пакування в `pyproject.toml` і розташування `web/`.

## Критерії приймання

- Нові тести проходять; повний набір зелений; `ruff check .` чистий.

## Перевірка

```bash
.venv/bin/python -m unittest tests.test_paths -v 2>&1 | tail -5
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
bash -n scripts/cs_scan.sh
```

## Docs delta

Не редагуй `docs/ARCHITECTURE.md` (його паралельно змінювали б інші чанки): опиши зміни для документа в розділі «Docs delta» тіла PR, підрозділ цієї задачі. T39 перенесе їх у документ.

Що описати: `docs/ARCHITECTURE.md`: рядок `paths.py` у таблиці `storage/`; Known limitations про `pip install` — «set
CS2WATCH_WEB_DIR / CS2WATCH_DATA_DIR for an installed copy».
