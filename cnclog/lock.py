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
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]
    HAVE_FCNTL = False

try:
    import msvcrt

    HAVE_MSVCRT = True
except ImportError:  # everything except Windows
    msvcrt = None  # type: ignore[assignment]
    HAVE_MSVCRT = False

#: True when this platform can actually enforce single-instance.
CAN_LOCK = HAVE_FCNTL or HAVE_MSVCRT


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
        if not CAN_LOCK:
            # No locking primitive at all. Running is still better than
            # refusing to start; the risk is two copies on one data folder.
            self.unavailable_reason = "bu platformda kilit desteği yok"
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
            self._take(handle)
        except OSError:
            other = "bilinmiyor"
            try:
                handle.seek(0)
                other = handle.read().strip() or "bilinmiyor"
            except OSError:
                # On Windows the holder's byte range is locked against reads.
                pass
            handle.close()
            raise AlreadyRunning(
                f"Program zaten çalışıyor (PID {other}).\n"
                f"Kilit dosyası: {self.path}\n"
                "Aynı veri klasörüne iki kopya birden yazamaz — "
                "çalışan kopyayı kapatın veya --dizin ile başka bir klasör verin."
            )
        self._handle = handle

    def _take(self, handle) -> None:
        """Grab the lock, or raise OSError if someone else holds it."""
        if HAVE_FCNTL:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            handle.seek(0)
            handle.truncate()
            handle.write(str(os.getpid()))
            handle.flush()
            return

        # Windows: lock one byte. The file must have that byte to lock, so the
        # pid is written first -- if another process holds it, the write itself
        # is what fails, which is the same answer.
        handle.seek(0)
        handle.write(str(os.getpid()).ljust(16))
        handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            if HAVE_FCNTL:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            elif HAVE_MSVCRT:
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        try:
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
