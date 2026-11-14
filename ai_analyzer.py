"""Optional AI analysis stage (Claude CLI subscription / Anthropic API) for CS2 players."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Optional

from config_store import DEFAULT_AI_PROMPT, DEFAULT_AI_WEIGHTS, load_config

ALLOWED_DECISIONS = ("LEGIT", "UNCERTAIN", "SUSPICIOUS", "BLATANT")

_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "nick": {"type": "string"},
                    "decision": {"type": "string", "enum": list(ALLOWED_DECISIONS)},
                    "confidence": {"type": "number"},
                    "analysis_steps": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": ["nick", "decision", "confidence", "analysis_steps", "summary"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


def _compact_player_for_ai(p: dict[str, Any]) -> dict[str, Any]:
    steam = p.get("steam") or {}
    leetify = steam.get("leetify") or {}
    return {
        "nick": p.get("nick"),
        "scoreboard": {
            "kills": p.get("kills"),
            "deaths": p.get("deaths"),
            "kd": p.get("kd"),
            "hs_percent": p.get("hs"),
            "level": p.get("level"),
            "heuristic_probability": p.get("cheat_probability"),
            "heuristic_risk": p.get("risk_level"),
            "heuristic_reasons": p.get("reasons") or [],
        },
        "steam": {
            "found": bool(steam.get("steamid64")),
            "steamid64": steam.get("steamid64"),
            "profile_name": steam.get("profile_name"),
            "account_age_years": steam.get("account_age_years"),
            "cs2_hours": steam.get("cs2_hours"),
            "steam_level": steam.get("steam_level"),
            "vac_banned": steam.get("vac_banned"),
            "number_of_vac_bans": steam.get("number_of_vac_bans"),
            "game_bans": steam.get("game_bans"),
            "days_since_last_ban": steam.get("days_since_last_ban"),
            "is_private": steam.get("is_private"),
            "is_limited": steam.get("is_limited"),
            "in_cs2": steam.get("in_cs2"),
        },
        "leetify": {
            "found": bool(leetify.get("found")),
            "aim": leetify.get("aim"),
            "reaction_time_ms": leetify.get("reaction_time_ms"),
            "winrate": leetify.get("winrate"),
            "premier_rank": leetify.get("premier_rank"),
            "faceit_level": leetify.get("faceit_level"),
            "bans": leetify.get("bans") or [],
        },
    }


def _build_prompt(players: list[dict[str, Any]], cfg: dict[str, Any]) -> str:
    user_prompt = str(cfg.get("ai_prompt") or DEFAULT_AI_PROMPT).strip()
    weights = cfg.get("ai_weights") or DEFAULT_AI_WEIGHTS
    payload = {
        "weights": {
            "scoreboard": weights.get("scoreboard", 0.30),
            "leetify": weights.get("leetify", 0.40),
            "steam_account": weights.get("steam_account", 0.30),
        },
        "players": [_compact_player_for_ai(p) for p in players],
    }
    return (
        f"{user_prompt}\n\n"
        "Поверни ВИКЛЮЧНО валідний JSON за схемою:\n"
        '{"results": [{"nick": "<точний нік з входу>", "decision": "LEGIT|UNCERTAIN|SUSPICIOUS|BLATANT", '
        '"confidence": <0..100>, "analysis_steps": "<покроковий аналіз>", "summary": "<короткий висновок>"}]}\n\n'
        f"Вхідні дані:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def _extract_json_object(text: str) -> Optional[dict[str, Any]]:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.S)
    if m:
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(raw[start : end + 1])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    return None


def _run_claude_cli(prompt: str, model: str, timeout: int = 90) -> dict[str, Any]:
    claude_bin = shutil.which("claude")
    if not claude_bin:
        raise RuntimeError("Claude CLI ('claude') не знайдено в PATH.")

    cmd = [
        claude_bin,
        "-p",
        "--output-format",
        "json",
        "--no-session-persistence",
        "--setting-sources",
        "",
        "--tools",
        "",
        "--model",
        model or "sonnet",
        "--json-schema",
        json.dumps(_OUTPUT_SCHEMA, ensure_ascii=False),
    ]
    proc = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"Claude CLI помилка (код {proc.returncode}): {err[:300]}")

    outer = _extract_json_object(proc.stdout)
    if not outer:
        raise RuntimeError("Claude CLI повернув невалідний JSON.")
    if outer.get("is_error"):
        raise RuntimeError(str(outer.get("result") or "Claude CLI повернув помилку."))

    structured = outer.get("structured_output")
    if isinstance(structured, dict) and "results" in structured:
        return structured

    inner = _extract_json_object(str(outer.get("result") or ""))
    if inner and "results" in inner:
        return inner
    raise RuntimeError("Не вдалося розпарсити structured results від Claude CLI.")


def _run_anthropic_api(prompt: str, model: str, api_key: str, timeout: int = 60) -> dict[str, Any]:
    if not api_key:
        raise RuntimeError("Не задано Anthropic API Key (в налаштуваннях або ANTHROPIC_API_KEY).")

    model_map = {
        "sonnet": "claude-sonnet-4-20250514",
        "haiku": "claude-3-5-haiku-latest",
        "opus": "claude-opus-4-20250514",
    }
    resolved_model = model_map.get(model.strip().lower(), model.strip() or "claude-sonnet-4-20250514")

    body = json.dumps({
        "model": resolved_model,
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="ignore")[:300]
        raise RuntimeError(f"Anthropic API HTTP {exc.code}: {err_body}") from exc
    except Exception as exc:
        raise RuntimeError(f"Anthropic API помилка: {exc}") from exc

    text_chunks = [c.get("text", "") for c in data.get("content", []) if c.get("type") == "text"]
    parsed = _extract_json_object("\n".join(text_chunks))
    if not parsed or "results" not in parsed:
        raise RuntimeError("Anthropic API повернув відповідь без JSON results.")
    return parsed


def analyze_players_with_ai(
    players: list[dict[str, Any]],
    force: bool = False,
) -> tuple[list[dict[str, Any]], Optional[str]]:
    cfg = load_config()
    if not force and not cfg.get("ai_enabled"):
        return players, None
    if not players:
        return players, None

    provider = str(cfg.get("ai_provider") or "claude_cli").strip().lower()
    model = str(cfg.get("ai_model") or "sonnet").strip() or "sonnet"
    prompt = _build_prompt(players, cfg)

    try:
        if provider == "anthropic_api":
            api_key = str(cfg.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY") or "").strip()
            parsed = _run_anthropic_api(prompt, model, api_key)
        else:
            parsed = _run_claude_cli(prompt, model)
    except Exception as exc:
        return players, str(exc)

    now_iso = datetime.now().isoformat(timespec="seconds")
    by_nick: dict[str, dict[str, Any]] = {}
    for item in parsed.get("results", []):
        if not isinstance(item, dict):
            continue
        nick = str(item.get("nick") or "").strip()
        if not nick:
            continue
        decision = str(item.get("decision") or "UNCERTAIN").strip().upper()
        if decision not in ALLOWED_DECISIONS:
            decision = "UNCERTAIN"
        try:
            conf = round(max(0.0, min(100.0, float(item.get("confidence", 50)))))
        except (TypeError, ValueError):
            conf = 50
        by_nick[nick] = {
            "provider": provider,
            "model": model,
            "decision": decision,
            "confidence": conf,
            "chain_of_thought": str(item.get("analysis_steps") or item.get("chain_of_thought") or "").strip(),
            "summary": str(item.get("summary") or "").strip(),
            "analyzed_at": now_iso,
        }

    for p in players:
        nick = str(p.get("nick") or "").strip()
        if nick in by_nick:
            p["ai_analysis"] = by_nick[nick]

    return players, None
