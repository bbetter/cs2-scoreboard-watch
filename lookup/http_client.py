"""Plain HTTP helpers shared by lookup and sources: curl when available, urllib otherwise.

Moved verbatim from lookup/steam_lookup.py (T16). Stdlib only.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import Optional

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def http_get_status(
    url: str,
    timeout: float = 4.0,
    cookies: Optional[str] = None,
    headers: Optional[dict[str, str]] = None,
) -> tuple[int, Optional[bytes]]:
    import shutil
    import subprocess

    if shutil.which("curl"):
        cmd = ["curl", "-sL", "--max-time", str(timeout), "-w", "\n%{http_code}"]
        if cookies:
            cmd.extend(["-b", cookies])
        if headers:
            for k, v in headers.items():
                cmd.extend(["-H", f"{k}: {v}"])
        cmd.append(url)
        try:
            res = subprocess.run(cmd, capture_output=True, timeout=timeout + 2)
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
