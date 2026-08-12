"""Process lifecycle primitives for safe local and cloud execution."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import TracebackType
from typing import TextIO

if sys.platform == "win32":
    import msvcrt

    def _try_lock(handle: TextIO) -> None:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise BlockingIOError from exc

    def _unlock(handle: TextIO) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(handle: TextIO) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(handle: TextIO) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class SingleInstanceLock:
    """Cross-platform advisory process lock with a visible PID file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self._handle: TextIO | None = None

    def acquire(self) -> None:
        if self._handle is not None:
            raise RuntimeError("single-instance lock is already acquired")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        handle = self.path.open("r+", encoding="utf-8")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(" ")
            handle.flush()
        try:
            _try_lock(handle)
        except BlockingIOError as exc:
            handle.seek(0)
            owner = handle.read().strip() or "unknown"
            handle.close()
            raise RuntimeError(f"another simulation process holds the lock (PID {owner})") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        os.fsync(handle.fileno())
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        handle = self._handle
        self._handle = None
        _unlock(handle)
        handle.close()

    def __enter__(self) -> SingleInstanceLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()
