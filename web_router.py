"""Tiny stdlib router for the CS2 scanner HTTP handler, plus JSON-body and static-file helpers.

Routes are registered with a decorator and matched on the raw (undecoded) URL path:

    router = Router()

    @router.route("GET", "/api/scan/{scan_id:path}")
    def get_scan(h, scan_id): ...

Pattern syntax: literal text is matched exactly; `{name}` matches one non-empty path segment (no "/");
`{name:path}` matches any text, including "/" and the empty string. Routes are tried in registration
order; the first match wins. Fallbacks (used for static files) run only when no route matched.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Optional

_TOKEN = re.compile(r"\{(\w+)(?::(path))?\}")

Handler = Callable[..., None]
Fallback = Callable[[Any, str], bool]


def compile_pattern(pattern: str) -> re.Pattern:
    if not pattern.startswith("/"):
        raise ValueError(f"route pattern must start with '/': {pattern!r}")
    parts: list[str] = []
    pos = 0
    for m in _TOKEN.finditer(pattern):
        parts.append(re.escape(pattern[pos:m.start()]))
        name, kind = m.group(1), m.group(2)
        parts.append(f"(?P<{name}>.*)" if kind == "path" else f"(?P<{name}>[^/]+)")
        pos = m.end()
    parts.append(re.escape(pattern[pos:]))
    return re.compile("".join(parts))


class Router:
    def __init__(self) -> None:
        self._routes: list[tuple[str, re.Pattern, str, Handler]] = []
        self._fallbacks: dict[str, Fallback] = {}

    def route(self, method: str, pattern: str) -> Callable[[Handler], Handler]:
        method = method.upper()
        rx = compile_pattern(pattern)

        def deco(fn: Handler) -> Handler:
            self._routes.append((method, rx, pattern, fn))
            return fn

        return deco

    def fallback(self, method: str) -> Callable[[Fallback], Fallback]:
        """fn(handler, path) -> bool. Consulted only when no route matched; True means handled."""
        method = method.upper()

        def deco(fn: Fallback) -> Fallback:
            self._fallbacks[method] = fn
            return fn

        return deco

    def routes(self) -> list[tuple[str, str]]:
        """(method, pattern) for every registered route, in match order."""
        return [(m, pattern) for m, _rx, pattern, _fn in self._routes]

    def dispatch(self, handler: Any, method: str, path: str) -> bool:
        """Run the first matching route (or fallback). Returns False if nothing handled the request."""
        method = method.upper()
        for route_method, rx, _pattern, fn in self._routes:
            if route_method != method:
                continue
            match = rx.fullmatch(path)
            if match is not None:
                fn(handler, **match.groupdict())
                return True
        fb = self._fallbacks.get(method)
        return bool(fb is not None and fb(handler, path))


# ---------------------------------------------------------------- JSON bodies

class JsonBodyError(ValueError):
    """Request body is not valid JSON (size errors are NOT wrapped in this)."""


def read_json_body(h: Any, *, empty_json: Callable[[], Any] = dict, max_bytes: int = 25 * 1024 * 1024) -> Any:
    """Read the request body and parse it as JSON.

    Empty body -> empty_json(). Too large -> ValueError("Payload too large") from the handler, unchanged.
    Invalid JSON / bad UTF-8 -> JsonBodyError whose message is the original parser message, so callers
    that turn exceptions into {"error": str(exc)} keep the exact same text.
    """
    raw = h._read_body(max_bytes)
    if not raw:
        return empty_json()
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise JsonBodyError(str(exc)) from exc


# ---------------------------------------------------------------- static files

STATIC_CONTENT_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}


def resolve_static_file(root: Path, url_path: str) -> Optional[Path]:
    """Map a raw request path to a file under `root`, or None if it must not be served.

    "/" maps to index.html. Anything else is percent-decoded, must be made of plain segments (no empty,
    "." or ".." segments, no dot-files, no backslashes or NUL), must have an allowed extension, must be a
    regular file, and must still lie inside `root` after symlinks are resolved.
    """
    decoded = urllib.parse.unquote(url_path)
    if "\x00" in decoded or "\\" in decoded or not decoded.startswith("/"):
        return None
    if decoded == "/":
        segments = ["index.html"]
    else:
        segments = decoded[1:].split("/")
        for seg in segments:
            if seg in ("", ".", "..") or seg.startswith("."):
                return None
    if Path(segments[-1]).suffix.lower() not in STATIC_CONTENT_TYPES:
        return None
    base = root.resolve()
    target = (base.joinpath(*segments)).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return None
    if not target.is_file():
        return None
    return target


def static_content_type(path: Path) -> str:
    return STATIC_CONTENT_TYPES[path.suffix.lower()]
