#!/usr/bin/env bash
set -euo pipefail

# Project root (this script lives in scripts/).
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Interpreter: CS2WATCH_PYTHON override, else the project venv, else system python3.
if [[ -n "${CS2WATCH_PYTHON:-}" ]]; then
  VENV_PY="$CS2WATCH_PYTHON"
elif [[ -x "$BASE_DIR/.venv/bin/python" ]]; then
  VENV_PY="$BASE_DIR/.venv/bin/python"
else
  VENV_PY="python3"
fi
SERVER_URL="http://127.0.0.1:8765"
DATA_DIR="$BASE_DIR/data"
SCREENSHOT_DIR="$DATA_DIR/screenshots"
mkdir -p "$SCREENSHOT_DIR" "$DATA_DIR/logs"

# Start web server if it is not running yet
if ! curl -sf "$SERVER_URL/api/latest" >/dev/null 2>&1; then
  (cd "$BASE_DIR" && nohup "$VENV_PY" -m app.server --host 127.0.0.1 --port 8765 >"$DATA_DIR/logs/server.log" 2>&1 &)
  for _ in $(seq 1 30); do
    if curl -sf "$SERVER_URL/api/latest" >/dev/null 2>&1; then
      break
    fi
    sleep 0.2
  done
fi

TS="$(date +%Y%m%d_%H%M%S)"
IMG_PATH="$SCREENSHOT_DIR/cs2_${TS}.png"

if [[ -n "${WAYLAND_DISPLAY:-}" ]] && command -v grim >/dev/null 2>&1; then
  grim "$IMG_PATH"
elif command -v import >/dev/null 2>&1; then
  import -window root "$IMG_PATH"
else
  notify-send "CS2 Watch" "Не знайдено grim або import для скріншоту"
  exit 1
fi

RESP="$(curl -sf -X POST "$SERVER_URL/api/scan" \
  -H "Content-Type: application/json" \
  -d "{\"image_path\": \"$IMG_PATH\"}")"

SUMMARY="$("$VENV_PY" -c '
import json, sys
data = json.loads(sys.stdin.read())
players = data.get("players", [])
if not players:
    print("Гравців не знайдено на скріншоті")
else:
    top = players[0]
    print(f"Знайдено {len(players)} гравців. Топ: {top[\"nick\"]} ({top[\"cheat_probability\"]}%)")
' <<<"$RESP")"

if command -v notify-send >/dev/null 2>&1; then
  notify-send "CS2 Scoreboard Watch" "$SUMMARY\n$SERVER_URL"
fi
