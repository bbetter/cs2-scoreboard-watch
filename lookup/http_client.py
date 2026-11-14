"""Plain HTTP helpers shared by lookup and sources: curl when available, urllib otherwise.

Moved verbatim from lookup/steam_lookup.py (T16). Stdlib only.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import Optional

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def _curl_config(url: str, cookies: Optional[str], headers: Optional[dict[str, str]]) -> str:
    """Render URL, cookie and headers as a curl config file (read via `curl -K -`).

    Keeping secrets out of argv: other local processes can read /proc/<pid>/cmdline, but not stdin.
    Raises ValueError if any value contains a line break, which would break the config syntax.
    """

    def quote(value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError("line break in curl config value")
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    lines = [f"url = {quote(url)}"]
    if cookies:
        lines.append(f"cookie = {quote(cookies)}")
    for k, v in (headers or {}).items():
        lines.append(f"header = {quote(f'{k}: {v}')}")
    return "\n".join(lines) + "\n"


def http_get_status(
    url: str,
    timeout: float = 4.0,
    cookies: Optional[str] = None,
    headers: Optional[dict[str, str]] = None,
) -> tuple[int, Optional[bytes]]:
    import shutil
    import subprocess

    if shutil.which("curl"):
        cmd = ["curl", "-sL", "--max-time", str(timeout), "-w", "\n%{http_code}", "-K", "-"]
        try:
            config = _curl_config(url, cookies, headers)
            res = subprocess.run(
                cmd, input=config.encode("utf-8"), capture_output=True, timeout=timeout + 2
            )
            out = res.stdout
            if b"\n" in out:
                body, code_bytes = out.rsplit(b"\n", 1)
                try:
                    code = int(code_bytes.strip())
                    return code, (body if code == 200 else None)
                except ValueError:
                    pass
        except Exception:
            pass

    req_headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    if cookies:
        req_headers["Cookie"] = cookies
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, TimeoutError, ValueError):
        return 0, None


def http_get(
    url: str,
    timeout: float = 4.0,
    cookies: Optional[str] = None,
    headers: Optional[dict[str, str]] = None,
) -> Optional[bytes]:
    status, data = http_get_status(url, timeout=timeout, cookies=cookies, headers=headers)
    return data if status == 200 else None
