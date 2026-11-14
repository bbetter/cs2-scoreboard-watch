"""Crash-safe file writes: write to a temp file in the same directory, then os.replace()."""

from __future__ import annotations

import os
import threading
from pathlib import Path


def atomic_write_text(path: Path | str, text: str, encoding: str = "utf-8") -> None:
    """Replace `path` with `text` atomically.

    The data is written to a unique temp file next to `path` and moved into place with
    os.replace(), so readers see either the old or the new content, never a partial file.
    If anything fails, the temp file is removed and the original file is left untouched;
    the exception is re-raised for the caller to handle.
    """
    path = Path(path)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        # os.open with 0o666 keeps the same permissions write_text() would have produced (umask applies).
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o666)
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
