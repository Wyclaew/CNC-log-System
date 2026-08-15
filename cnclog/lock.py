"""Single-instance lock.

Two collectors writing the same database produce overlapping state events --
two stops claiming the same minute -- which quietly corrupts every duration in
every report. Cheaper to refuse the second start than to detect it later.

Uses fcntl.flock, which the kernel releases automatically if the process is
killed, so a crash never leaves a stale lock behind.
"""

from __future__ import annotations

import os
from typing import Optional

try:
    import fcntl

    HAVE_FCNTL = True
except ImportError:  # pragma: no cover - Windows, not a target platform
    fcntl = None  # type: ignore[assignment]
    HAVE_FCNTL = False


class AlreadyRunning(Exception):
    """Raised when another instance already holds the lock."""


class InstanceLock:
    """Holds an exclusive lock on a file inside the data directory."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._handle = None
        #: Set when the lock file itself could not be created (read-only media).
        self.unavailable_reason: Optional[str] = None

    def acquire(self) -> None:
        if not HAVE_FCNTL:
            # Without flock we cannot guarantee exclusivity; running is still
            # better than refusing to start on a platform we do not target.
            return
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            handle = open(self.path, "a+", encoding="utf-8")
        except OSError as exc:
            # Read-only media or a permissions problem. Without a lock we
            # cannot guarantee exclusivity, but refusing to run would be worse
            # than a small risk on a machine that only ever runs one copy.
            self._handle = None
            self.unavailable_reason = str(exc)
            return
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.seek(0)
            other = handle.read().strip() or "bilinmiyor"
            handle.close()
            raise AlreadyRunning(
                f"Program zaten çalışıyor (PID {other}).\n"
                f"Kilit dosyası: {self.path}\n"
                "Aynı veri klasörüne iki kopya birden yazamaz — "
                "çalışan kopyayı kapatın veya --dizin ile başka bir klasör verin."
            )
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            if HAVE_FCNTL:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
        except OSError:
            pass
        self._handle = None
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def __enter__(self) -> "InstanceLock":
        self.acquire()
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()
