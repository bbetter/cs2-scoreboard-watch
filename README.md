# CS2 Scoreboard Watch

[![Tests](https://github.com/bbetter/cs2-scoreboard-watch/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/bbetter/cs2-scoreboard-watch/actions/workflows/tests.yml?query=branch%3Amain)

Утиліта для автоматичного аналізу таблиці рахунку (`TAB`) у **Counter-Strike 2** (Linux Wayland/Hyprland, Windows та macOS) із розпізнаванням гравців через OCR, пошуком реальних профілів у Steam та Leetify, оцінкою ризику і окремим веб-інтерфейсом.

## Що вміє проект

- **Автоматичний скан по `TAB` у грі (`app/watch_cs2.py`):**
  - Реєструє non-consuming бінд `Tab` у Hyprland (таблиця в CS2 відкривається як завжди), перевіряє, що активне вікно — CS2, чекає `0.5` с, робить скріншот **тільки з монітора, на якому запущена CS2** (`grim -o <monitor>`), та оновлює веб-інтерфейс у реальному часі через SSE.
- **Точне розпізнавання таблиці (`vision/scoreboard_scan.py`):**
  - Адаптується до роздільної здатності екрана (якір за колонкою `K/D` та висотою екрана), зчитує нікнейми, `Kills`, `Deaths`, `K/D`, `HS%`, `Level`, а також вирізає аватарку кожного гравця зі скріншоту.
- **Пошук реального профілю та звірка аватарок (`lookup/steam_search.py`, оркестрація в `lookup/steam_lookup.py`):**
  - Шукає кандидатів у Steam Community, пакетно підтягує дані через **Steam Web API** (`GetPlayerSummaries`, `GetPlayerBans`, `GetOwnedGames`), перевіряє статус `In CS2`, візуальну схожість аватарки зі скріншоту та статистику з **Leetify Public API v3** (`Aim`, `Reaction Time`, `Winrate`, `Premier`, `FACEIT`, бани).
  - Зберігає постійний кеш знайдених профілів у `data/profile_cache.json`.
- **Оцінка ймовірності ризику:**
  - Основна вага (~80%) — зовнішні фактори (VAC/ігрові/Leetify бани, час реакції, winrate на малій кількості матчів, вік акаунта, години в CS2, приватність), а поточне лобі дає лише ~20% ваги.
- **Інтеграція з `console.log` (`app/console_parser.py`):**
  - Опціонально читає вивід команди `status` із `console.log` (`-condebug`) для точного виправлення OCR-ніків, визначення карти та групування скріншотів у матчі.
  - У веб-інтерфейсі є кнопка **«Налаштувати CS2 (autoexec)»**, яка сама знаходить папку CS2 через `libraryfolders.vdf`, додає `bind "TAB" "+showscores; status"` в `autoexec.cfg` та дає копіювання параметрів запуску `-condebug +exec autoexec.cfg`.
- **Веб-інтерфейс (`http://127.0.0.1:8765`):**
  - Живе оновлення таблиці, сортування по будь-якій колонці, пошук та фільтри за датою, матчем, тривалістю і рівнем ризику.
  - Збереження історії матчів у `data/scans_history.json`, кнопка **«Повторний аналіз»**, ручне редагування ніку / `SteamID64` / URL (`✎`), вкладка **«Потребує уваги»** з можливістю вибрати потрібного кандидата в 1 клік, а також прямі посилання на **Leetify**, **Steam** та **steamcommunity.rip**.

---

Стан програми (конфіг, база гравців, кеші, історія скану, feedback, логи, скріншоти) зберігається в `data/` (у `.gitignore`). Кореневий `.env` (ключі API) читається з кореня.

## Вимоги

- **Linux:** Wayland / Hyprland + `grim`, `curl`, `notify-send` (опціонально)
- **Windows:** Windows 10/11 (використовує `pynput` та `mss`)
- **macOS:** macOS (використовує `pynput`, вбудований `screencapture`; потрібні дозволи **Accessibility** та **Screen Recording** для терміналу/Python у *System Settings → Privacy & Security*)
- Python 3.11+

## Встановлення та запуск

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Для розробки (тести і `ruff`): `.venv/bin/pip install -r requirements-dev.txt`.

*(Опціонально, але рекомендовано для стабільної роботи без `HTTP 429` від Steam)* — отримайте безкоштовний ключ на [steamcommunity.com/dev/apikey](https://steamcommunity.com/dev/apikey) та додайте в оточення:

```zsh
echo 'export STEAM_API_KEY="ВАШ_КЛЮЧ"' >> ~/.zshrc && source ~/.zshrc
```

Запуск утиліти:

```bash
# Linux / macOS
.venv/bin/python -m cs2watch
```

```powershell
# Windows (PowerShell)
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m cs2watch
```

Після запуску відкрийте `http://127.0.0.1:8765` у браузері (можна на другому моніторі) та натискайте `TAB` у грі.

Локальний сервер приймає запити лише з хостом `localhost` або IP-адресою і відхиляє міжсайтові запити (захист від DNS rebinding, перевірка `Origin` і `Sec-Fetch-Site`). Автентифікації немає, тому не відкривайте порт назовні. Деталі — у `docs/ARCHITECTURE.md`, розділ «Security model of the local server».

---

## Leetify: атрибуція та кеш

- Дані Leetify позначені в інтерфейсі написом **«Data Provided by Leetify»** з посиланням на `https://leetify.com/` (у футері та в секції Leetify розгорнутої картки).
- Опція `leetify_cache_ttl_sec` у `data/config.json` (за замовчуванням `0`, максимум `86400`) керує тим, як довго блок Leetify можна повторно використовувати з оперативної пам'яті процесу. Блок Leetify **ніколи не записується** у `data/profile_cache.json`; при `0` він не повторно використовується між запитами і завжди запитується заново. Дані скану (`data/scans_history.json`) зберігають блок Leetify як і раніше.
- Відсутній `faceit_api_key` не є помилкою: FACEIT просто пропускається (поле ключа є в налаштуваннях веб-інтерфейсу).

> **Примітка:** Оцінка ймовірності є статистичним та профільним індикатором ризику і не є остаточним доказом порушення правил.
