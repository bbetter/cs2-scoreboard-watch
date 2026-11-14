---
id: T14
title: Restrict scan image paths and validate uploads
wave: 2
depends_on: [T10]
review: none
size: S
golden: []
files:
  - app/server.py
  - tests/test_scan_inputs.py
---

# T14 — Обмежити `image_path` у `/api/scan` і перевіряти `/api/upload`

## Мета

`POST /api/scan` приймає будь-який шлях на диску (`{"image_path": "/home/user/secret.png"}`) і запускає на ньому
OCR. `POST /api/upload` записує будь-які байти як `upload_<ts>.png`. Дозволити лише зображення з тек скріншотів і
лише PNG/JPEG у завантаженні.

## Контекст

- `app/server.py`: `_post_scan` → `run_scan(image_path=Path(img_path) if img_path else None, ...)`;
  `run_scan` робить `Path(image_path).resolve()` і перевіряє лише існування.
- Теки скріншотів: `cs2platform.capture.get_screenshot_dir()` (може бути кастомна з конфігу `screenshots_dir`) і
  `cs2platform.capture.SCREENSHOT_DIR` (`data/screenshots`). Так само їх уже використовує `_post_rescan`.
- Легітимний клієнт: `scripts/cs_scan.sh` шле `image_path="$DATA_DIR/screenshots/cs2_<ts>.png"` — це
  `SCREENSHOT_DIR`, має працювати.
- `_post_upload` пише `out_path = get_screenshot_dir() / f"upload_{ts}.png"`.

## Що зробити

1. Додай у `app/server.py` хелпер
   `_resolve_scan_image(raw: str) -> Path` — `Path(raw).expanduser().resolve()`; дозволено, якщо результат
   `.is_relative_to(get_screenshot_dir().resolve())` **або** `.is_relative_to(SCREENSHOT_DIR.resolve())`, і суфікс
   у `{".png", ".jpg", ".jpeg"}` (без урахування регістру). Інакше `ValueError("image_path must be a .png/.jpg file
   inside the screenshots directory")`.
2. У `_post_scan`: якщо `img_path` переданий — `str`-перевірка (не рядок → 400), потім `_resolve_scan_image`;
   `ValueError` → `400 {"error": str(exc)}`; неіснуючий файл → `404 {"error": "Image not found"}` (замість нинішньої
   500). `run_scan` викликай з уже перевіреним шляхом.
3. У `_post_upload`: перевір сигнатуру: PNG `raw.startswith(b"\x89PNG\r\n\x1a\n")` → суфікс `.png`; JPEG
   `raw.startswith(b"\xff\xd8\xff")` → суфікс `.jpg`; інакше `400 {"error": "Upload must be a PNG or JPEG image"}`
   і **нічого не записувати** на диск.
4. Створи `tests/test_scan_inputs.py` (живий сервер — патерн `tests/test_server_static.py`; або виклик функцій-
   маршрутів з фейковим handler — патерн `tests/test_router.py`). Пропатч `server.get_screenshot_dir` на тимчасову
   теку, `server.SCREENSHOT_DIR` на іншу тимчасову, і `server.run_scan` → фейк, що повертає `{"players": []}` і
   запамʼятовує шлях. Кейси: шлях у теці скріншотів → 200 і `run_scan` отримав resolved-шлях; шлях у
   `SCREENSHOT_DIR` → 200; `/etc/passwd` → 400; `<screens>/../x.png` → 400; `.txt` у теці → 400; неіснуючий
   `.png` у теці → 404; `image_path: 123` → 400; без `image_path` → `run_scan(image_path=None)` (як раніше);
   upload PNG-байтів → 200, файл `upload_*.png` створено; upload `b"hello"` → 400 і тека порожня; upload JPEG →
   файл `.jpg`.

## Не робити

- Не змінювати `_post_rescan`, `run_scan` для `image_path=None` (захоплення екрана) і `scripts/cs_scan.sh`.
- Не перевіряти вміст зображення глибше сигнатури (без Pillow у маршруті).

## Критерії приймання

- Нові тести проходять; повний набір зелений.
- `scripts/cs_scan.sh` сумісний (обґрунтуй у PR, що його шлях проходить `_resolve_scan_image`).

## Перевірка

```bash
.venv/bin/python -m unittest tests.test_scan_inputs -v
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
```

## Docs delta

`ARCHITECTURE.md` → Security model: `/api/scan image_path` обмежено теками скріншотів; `/api/upload` — PNG/JPEG за
сигнатурою.
