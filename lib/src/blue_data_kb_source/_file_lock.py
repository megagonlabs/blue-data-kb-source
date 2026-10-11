"""Cross-process exclusive locks for the file-backed stores (#127)."""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Iterator

if os.name == "nt":  # pragma: no cover - exercised on Windows only
    import msvcrt

    def _lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)  # retries for ~10s, then raises OSError

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


class FileLocks:
    """One exclusive lock per path, held across processes and threads.

    Reentrant within a thread: a locked call that makes another locked call on
    the same path doesn't deadlock on itself. The OS lock is taken only by the
    outermost holder; inner ones just count.
    """

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._threads: dict[str, threading.RLock] = {}
        self._depth: dict[str, int] = {}
        self._fds: dict[str, int] = {}

    @contextmanager
    def hold(self, path: str) -> Iterator[None]:
        with self._guard:
            rlock = self._threads.setdefault(path, threading.RLock())
        with rlock:
            depth = self._depth.get(path, 0)
            if depth == 0:
                fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o666)
                try:
                    _lock(fd)
                except BaseException:
                    os.close(fd)
                    raise
                self._fds[path] = fd
            self._depth[path] = depth + 1
            try:
                yield
            finally:
                self._depth[path] -= 1
                if self._depth[path] == 0:
                    fd = self._fds.pop(path)
                    try:
                        _unlock(fd)
                    finally:
                        os.close(fd)
