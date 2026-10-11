"""Crash-safe JSON file writes for the file-backed stores."""
from __future__ import annotations

import json
import os
import tempfile
from typing import Any


def write_json_atomic(path: str | os.PathLike, data: Any) -> None:
    """Serialize *data* to *path* without ever leaving a partial file behind.

    The JSON goes to a temp file in the same directory first, then `os.replace`
    swaps it in (atomic on POSIX and Windows). If serialization fails partway —
    a value json can't encode, a full disk — the existing file is untouched.
    Opening the target with "w" and dumping straight into it truncated the
    committed database first, so one bad value made every earlier write
    unreadable (#118).
    """
    path = os.fspath(path)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        # mkstemp creates the file 0600; without this, the first save narrowed
        # a group-shared data directory's files to their owner (#135).
        os.chmod(tmp, _target_mode(path))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _target_mode(path: str) -> int:
    """The mode *path* should keep: its current one if it exists, else what a plain
    `open(path, "w")` would give under the process umask."""
    try:
        return os.stat(path).st_mode & 0o7777
    except FileNotFoundError:
        umask = os.umask(0)
        os.umask(umask)
        return 0o666 & ~umask
