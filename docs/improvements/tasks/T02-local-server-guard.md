---
id: T02
title: Guard the local server against CSRF and DNS rebinding
wave: 1
depends_on: [T01]
review: none
size: M
golden: []
files:
  - app/web_router.py
  - app/server.py
  - tests/test_security_guard.py
---

# T02 — Захист локального сервера від CSRF і DNS-rebinding

## Мета

Зробити так, щоб сторонній сайт, відкритий у браузері користувача, не міг викликати API на
`http://127.0.0.1:8765` (змінити конфіг, запустити скан, переписати `autoexec.cfg`) і не міг читати відповіді через
DNS-rebinding. Легітимні клієнти — власний фронтенд, `curl` з бінда Hyprland, `scripts/cs_scan.sh` — мають
працювати як раніше.

## Контекст

- `app/server.py` `CsWatchHandler.do_GET` / `do_POST` (кінець файлу) одразу викликають `router.dispatch`, без
  жодних перевірок заголовків.
- `app/web_router.read_json_body` парсить тіло як JSON незалежно від `Content-Type`, тому крос-доменний
  «simple request» (`mode: "no-cors"`, `Content-Type: text/plain`) проходить без CORS-preflight.
- Сервер не віддає `Access-Control-Allow-*` заголовків (перевір: `grep -rn "Access-Control" app` → порожньо).
  Тобто читати відповіді крос-доменно браузер не дасть — **крім** DNS-rebinding, де атакуючий домен резолвиться в
  `127.0.0.1`. Від rebinding захищає перевірка `Host`.
- Легітимні клієнти:
  - фронтенд (`web/*.js`) — same-origin, усі `fetch` з тілом уже шлють `Content-Type: application/json`, а
    `/api/upload` — `file.type || "application/octet-stream"` (`web/api.js:51`);
  - `app/watch_cs2.py:27` — `curl -sf -X POST {url}/api/tab-pressed` **без тіла, без Content-Type, без Origin**;
  - `web/app.js:351` — `fetch("/api/setup-cs2", { method: "POST" })` без тіла;
  - `scripts/cs_scan.sh:42` — `curl … -H "Content-Type: application/json" -d '{"image_path": …}'`.
- Сервер може слухати `--host 0.0.0.0` (доступ з LAN), тож allowlist за конкретним IP не підходить.

## Що зробити

1. У `app/web_router.py` додай чисту функцію (без I/O, легко тестувати):

   ```python
   def check_request(method: str, path: str, headers: Mapping[str, str], body_length: int) -> Optional[tuple[int, str]]:
       """None if the request may proceed, else (http_status, error_message)."""
   ```

   Правила (саме в цьому порядку):
   1. **Host** (усі методи): заголовок `Host` обовʼязковий. Відкинь порт (`host:port`, `[::1]:port`). Дозволено, якщо
      імʼя — `localhost` (без урахування регістру) **або** IP-літерал (`ipaddress.ip_address(...)` не кидає; для
      `[::1]` прибери квадратні дужки). Інакше → `(403, "Host not allowed")`.
      *Чому так:* DNS-rebinding завжди приходить з доменним імʼям у `Host`; IP-літерали та `localhost` — це прямий
      доступ, LAN-доступ через `0.0.0.0` теж працює.
   2. **Origin** (лише `POST`): якщо заголовок `Origin` присутній:
      - `Origin: null` → `(403, "Origin not allowed")`;
      - інакше розбери `urlparse(origin)`; дозволено лише якщо `scheme == "http"` і `netloc.lower() == Host.lower()`.
        Інакше → `(403, "Origin not allowed")`.
      Відсутній `Origin` дозволений (curl, скрипти; браузери на POST його завжди шлють).
   3. **Sec-Fetch-Site** (лише `POST`): якщо присутній і значення не в `("same-origin", "none")` →
      `(403, "Cross-site request blocked")`.
   4. **Content-Type** (лише `POST` з `body_length > 0`): медіа-тип = частина до `;`, у нижньому регістрі.
      - `path == "/api/upload"`: дозволено `image/png`, `image/jpeg`, `application/octet-stream`;
      - будь-який інший шлях: дозволено лише `application/json`.
      Інакше → `(415, "Unsupported Content-Type")`.
      POST без тіла (`body_length == 0`) не перевіряється — так працюють `/api/tab-pressed` і `/api/setup-cs2`.

2. У `app/server.py`, `CsWatchHandler`: додай метод `_guard(self, method) -> bool`, який рахує
   `body_length = int(self.headers.get("Content-Length", "0") or 0)` (некоректне значення → 0), викликає
   `check_request(method, urlparse(self.path).path, self.headers, body_length)` і, якщо є помилка, відповідає
   `self._send_json({"error": msg}, status=code)` та повертає `False`. Виклич його першим рядком у `do_GET`
   (`method="GET"`) і `do_POST` (`method="POST"`); при `False` — `return`.
   `self.headers` — це `email.message.Message`: `.get()` нечутливий до регістру, передавай його як є.
3. Створи `tests/test_security_guard.py`:
   - **Юніт-тести `check_request`** (словник заголовків → результат): `Host: 127.0.0.1:8765` ок; `localhost:8765` ок;
     `LOCALHOST` ок; `[::1]:8765` ок; `192.168.1.10:8765` ок; `evil.example:8765` → 403; відсутній Host → 403;
     POST з `Origin: http://127.0.0.1:8765` при тому ж Host ок; `Origin: http://evil.example` → 403;
     `Origin: null` → 403; `Origin: https://127.0.0.1:8765` → 403 (інша схема); `Sec-Fetch-Site: cross-site` → 403;
     `Sec-Fetch-Site: same-origin` ок; POST `/api/config` з `text/plain` і `body_length=10` → 415;
     `application/json; charset=utf-8` ок; POST без тіла без Content-Type ок; `/api/upload` з `image/png` ок, з
     `text/plain` → 415; GET з чужим Origin — Origin не перевіряється (ок).
   - **Інтеграційні тести** через справжній сервер — скопіюй патерн з `tests/test_server_static.py`
     (`ThreadingHTTPServer(("127.0.0.1", 0), server.CsWatchHandler)` у `setUpClass`, `http.client.HTTPConnection`):
     - `GET /api/config/schema` з `Host: evil.example` → 403, тіло JSON з `error`;
     - `GET /api/config/schema` зі звичайним Host → 200;
     - `POST /api/tab-pressed` без тіла і без заголовків → **не** 403/415 (пропатч
       `mock.patch.object(server, "schedule_tab_scan", return_value={"status": "ignored"})`), очікуй 200;
     - `POST /api/config` з `Content-Type: text/plain` і тілом `{}` → 415, і `save_config` **не викликався**
       (`mock.patch.object(server, "save_config")` → `assert_not_called()`).
     `http.client` сам ставить `Host: 127.0.0.1:<port>`; щоб підмінити — `conn.putrequest(..., skip_host=True)` +
     `conn.putheader("Host", ...)`, або передай `headers={"Host": ...}` у `conn.request`.

## Не робити

- Не додавати CORS-заголовки, токени, cookies, сесії — лише перевірки вище.
- Не чіпати `web/*.js`, `scripts/cs_scan.sh`, `app/watch_cs2.py` — вони вже сумісні (перевір, але не змінюй).
- Не змінювати `Router.dispatch` і таблицю маршрутів (`tests/test_router.py` заморожує її).

## Критерії приймання

- Усі кейси з кроку 3 є і проходять.
- Повний набір тестів зелений; `tests/test_server_static.py` і `tests/test_router.py` не змінені.
- Ручна перевірка в PR (виконай і встав вивід у розділ Verification):
  ```bash
  .venv/bin/python -m app.server --port 8799 &
  curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8799/api/config/schema                       # 200
  curl -s -o /dev/null -w "%{http_code}\n" -H "Host: evil.example" http://127.0.0.1:8799/api/config/schema  # 403
  curl -s -o /dev/null -w "%{http_code}\n" -X POST -H "Origin: http://evil.example" http://127.0.0.1:8799/api/tab-pressed  # 403
  curl -s -o /dev/null -w "%{http_code}\n" -X POST -H "Content-Type: text/plain" -d '{}' http://127.0.0.1:8799/api/config  # 415
  kill %1
  ```
  (Запуск `app.server` створює файли в `data/` — це очікувано, вони gitignored; **не** додавай їх у коміт.)

## Перевірка

```bash
.venv/bin/python -m unittest tests.test_security_guard -v
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
.venv/bin/ruff check .
```

## Docs delta

`ARCHITECTURE.md` → Request flow: перед `Router.dispatch` — `web_router.check_request` (Host / Origin /
Sec-Fetch-Site / Content-Type, коди 403 / 415). Додати підрозділ «Security model of the local server» з правилами з
кроку 1.
