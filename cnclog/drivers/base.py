"""Driver interface.

Read-only by construction: there is deliberately no write/send/upload method
anywhere in this interface or in any implementation of it. A logger attached to
a production machine must not be able to change anything on the control, and
the cheapest way to guarantee that is to never give it the vocabulary.
"""

from __future__ import annotations

from typing import Optional

from ..model import Snapshot


class DriverError(Exception):
    """Raised when a driver cannot talk to the control.

    The collector catches this and records BAGLANTI_YOK; it never propagates
    far enough to stop the program.
    """


class Driver:
    """Base class. Subclasses implement connect/read/disconnect."""

    #: Short name used in config.ini under [surucu] tip.
    name = "base"

    #: How much faster than wall clock this driver's timeline runs. Only the
    #: simulator sets anything other than 1.0; the collector divides its sleep
    #: interval by this so a simulated hour can be replayed in a minute.
    time_scale: float = 1.0

    def connect(self) -> None:
        """Establish the link. May raise DriverError."""
        raise NotImplementedError

    def read(self) -> Snapshot:
        """Take one reading. May raise DriverError.

        Implementations must fill in only what the control actually exposes and
        leave every other field as None.
        """
        raise NotImplementedError

    def disconnect(self) -> None:
        """Tear the link down. Must not raise."""
        return None

    def describe(self) -> str:
        """One line shown in the UI and at startup."""
        return self.name

    def __enter__(self) -> "Driver":
        self.connect()
        return self

    def __exit__(self, *exc_info) -> None:
        self.disconnect()


def offline_snapshot(ts: float, reason: str) -> Snapshot:
    """Snapshot used whenever a driver cannot deliver a reading."""
    return Snapshot(ts=ts, connected=False, error=reason)
