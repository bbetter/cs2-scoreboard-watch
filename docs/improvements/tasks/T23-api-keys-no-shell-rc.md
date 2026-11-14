---
id: T23
title: Stop reading shell rc files for the Steam API key
wave: 5
depends_on: [T22]
review: none
size: S
golden: []
files:
  - storage/api_keys.py
  - tests/test_api_keys.py
  - README.md
  - docs/ARCHITECTURE.md
---

# T23 — Ключ Steam не шукається у `~/.zshrc` і `~/.bashrc`

## Мета

`storage/api_keys._get_steam_api_key` шукає `STEAM_API_KEY` у кореневому `.env`, а також у `~/.zshrc` і `~/.bashrc`.
Програма не повинна читати shell-конфіги користувача: це неочікуваний доступ до чужих файлів. Джерела ключа:
конфіг (`data/config.json`), змінна оточення, кореневий `.env`.

## Контекст

- `storage/api_keys.py`: `_get_steam_api_key` — порядок: кеш → `load_config()["steam_api_key"]` → `os.environ` →
  цикл по `(ROOT / ".env", Path.home() / ".zshrc", Path.home() / ".bashrc")`. `_get_faceit_api_key` читає лише
  `.env` — його не чіпай.
- README, розділ «Встановлення та запуск», радить `echo 'export STEAM_API_KEY=...' >> ~/.zshrc`. Це працює і далі,
  але лише коли процес запущено з такої оболонки (змінна оточення).
- Ключ можна задати в налаштуваннях веб-інтерфейсу (поле Steam Web API key), він зберігається в `data/config.json`.

## Що зробити

1. У `_get_steam_api_key` залиш у fallback лише `ROOT / ".env"`. Регулярний вираз і кешування не змінюй.
2. Створи `tests/test_api_keys.py`. Ізолюй усе: `mock.patch.object(api_keys, "ROOT", <tmp>)`,
   `mock.patch("pathlib.Path.home", return_value=<tmp_home>)`, `mock.patch.dict(os.environ, {}, clear=True)`,
   `load_config` → конфіг без ключів; скидай кеш `reset_steam_api_key_cache()` у `setUp` і `tearDown`. Кейси:
   - ключ лише у `<tmp_home>/.zshrc` і `.bashrc` → `None`;
   - ключ у `<tmp>/.env` → знайдено;
   - ключ у `STEAM_API_KEY` оточення → знайдено, і `.env` не читається (порожній `.env` з іншим ключем не виграє);
   - ключ у конфігу виграє в оточення.
   Використовуй фейкові ключі з `TESTKEY` у значенні (є тест, що це перевіряє).
3. README: заміни пораду з `~/.zshrc` на два способи: поле в налаштуваннях веб-інтерфейсу або рядок
   `STEAM_API_KEY=...` у кореневому `.env`. Додай речення, що змінна оточення теж працює, а shell-конфіги програма
   сама не читає.

## Не робити

- Не змінювати `_get_faceit_api_key`, публічний вигляд конфігу і маскування.

## Критерії приймання

- `grep -n "zshrc\|bashrc" storage/api_keys.py` нічого не знаходить.
- Нові тести проходять; повний набір зелений; `ruff check .` чистий.

## Перевірка

```bash
.venv/bin/python -m unittest tests.test_api_keys -v 2>&1 | tail -5
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
.venv/bin/ruff check .
```

## Docs delta

`docs/ARCHITECTURE.md`: рядок `.env` у таблиці «Other» і модуль `storage/api_keys.py` (якщо описаний): «Steam key:
config → STEAM_API_KEY → root .env; shell rc files are not read». Онови в цьому ж PR.
