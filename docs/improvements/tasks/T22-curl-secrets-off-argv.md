---
id: T22
title: Keep API keys out of the curl command line
wave: 5
depends_on: [T20]
review: none
size: S
golden: []
files:
  - lookup/http_client.py
  - tests/test_http_client.py
  - docs/ARCHITECTURE.md
---

# T22 — API-ключі не потрапляють в argv `curl`

## Мета

`lookup/http_client.http_get_status` запускає `curl` і передає URL, заголовки й cookie як аргументи командного
рядка. Ключ Steam Web API іде в URL (`?key=...`), ключ FACEIT у заголовку `Authorization: Bearer ...`, ключ Leetify
у заголовку. Аргументи будь-якого процесу видно іншим локальним процесам (`ps`, `/proc/<pid>/cmdline`). Треба
передавати URL, заголовки й cookie в `curl` через stdin (`-K -`), а не через argv.

## Контекст

- `lookup/http_client.py`: гілка `if shutil.which("curl")` будує `cmd = ["curl", "-sL", "--max-time", ..., "-w",
  "\n%{http_code}"]`, додає `-b <cookies>`, `-H "<k>: <v>"`, потім URL. Відповідь розбирається з `res.stdout`:
  тіло і код через останній `\n`. Цю логіку розбору не змінюй.
- Хто передає секрети: `lookup/steam_search.py` (`key=` у URL GetPlayerSummaries / GetPlayerBans / GetOwnedGames),
  `sources/steam_stats.py` (`key=` у URL), `sources/faceit.py` (`Authorization`), `sources/leetify.py` (заголовок з
  ключем). Ці файли **не** змінюй: виправлення має бути в одному місці.
- Формат конфіг-файлу curl (`man curl`, розділ `-K, --config`): рядки `url = "..."`, `header = "..."`,
  `cookie = "..."`. У значенні в лапках екрануються `\\` і `\"`.
- Тести: `tests/test_http_client.py` уже вимикає гілку curl (`shutil.which -> None`) і тестує urllib.

## Що зробити

1. Додай у `lookup/http_client.py` функцію `_curl_config(url, cookies, headers) -> str`, яка повертає текст
   конфігу: рядок `url = "<v>"`, потім `cookie = "<v>"` (якщо є cookies), потім `header = "<k>: <v>"` для кожного
   заголовка. Екрануй `\` → `\\`, потім `"` → `\"`. Якщо будь-яке значення (URL, ключ чи значення заголовка,
   cookie) містить `\r` або `\n`, кидай `ValueError`.
2. У гілці curl: `cmd = ["curl", "-sL", "--max-time", str(timeout), "-w", "\n%{http_code}", "-K", "-"]` і
   `subprocess.run(cmd, input=_curl_config(...).encode("utf-8"), capture_output=True, timeout=timeout + 2)`.
   `ValueError` з кроку 1 не передається далі: пропусти curl і перейди до гілки urllib, як зараз при будь-якій
   помилці curl.
3. Розбір виводу, fallback на urllib і всі коди повернення лишаються як є.
4. Тести в `tests/test_http_client.py`, новий клас `CurlArgvTests` (`shutil.which -> "/usr/bin/curl"`,
   `subprocess.run` замокано і повертає `stdout=b"body\n200"`):
   - секрет з URL (`https://api.example.test/x?key=SECRET123`) і з заголовка (`Authorization: Bearer SECRET456`) не
     трапляється в жодному елементі argv; argv містить `-K` і `-`;
   - `input` містить `url = "https://api.example.test/x?key=SECRET123"` і `header = "Authorization: Bearer SECRET456"`;
   - значення з `"` і `\` екрануються (перевір точний рядок конфігу);
   - заголовок з `\n` у значенні: curl не викликано, використано urllib (замокай `urlopen`);
   - результат розбору: `(200, b"body")`.
5. Інтеграційний тест `CurlLiveTests` (`@unittest.skipUnless(shutil.which("curl"), ...)`): локальний
   `http.server` на `127.0.0.1:0` віддає 200 і тіло зі значенням отриманого заголовка `X-Test` і query-рядка.
   Перевір, що справжній curl з `-K -` доставляє URL, query і заголовок.

## Не робити

- Не прибирати curl і не міняти таймаути, User-Agent чи порядок fallback.
- Не міняти виклики `http_get` / `http_get_status` в інших модулях.

## Критерії приймання

- Нові тести проходять; повний набір зелений; `ruff check .` чистий.
- `grep -n '"-H"\|"-b"\|cmd.append(url)' lookup/http_client.py` нічого не знаходить.

## Перевірка

```bash
.venv/bin/python -m unittest tests.test_http_client -v 2>&1 | tail -5
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
.venv/bin/ruff check .
```

## Docs delta

`docs/ARCHITECTURE.md` → таблиця модулів `lookup/`, рядок `http_client.py`: «curl отримує URL, заголовки й cookie
через stdin (`-K -`), тому ключі API не видно в списку процесів». Онови цей рядок у цьому ж PR.
