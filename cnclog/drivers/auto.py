"""Finds the control by itself, then hands over to the LSV2 driver.

This is the default. The operator starts the program and it goes looking for
the machine; there is no IP address to type and no "which driver?" question.

If no control answers, this driver fails cleanly. The collector then records
BAĞLANTI YOK and keeps retrying with backoff -- it never falls back to
simulated data, because a log full of invented numbers is worse than an honest
gap. The simulator is only ever used when it is asked for by name.
"""

from __future__ import annotations

import dataclasses
import time
from typing import Any, List, Optional

from ..discovery import LSV2_PORT, Found, find_control
from ..model import Snapshot
from .base import Driver, DriverError
from .heidenhain_lsv2 import HeidenhainLsv2Driver

#: Skip the subnet sweep for this long after a successful find. Reconnecting
#: to a known machine should be instant, not a fresh network scan.
_REMEMBER_S = 3600.0


class AutoDriver(Driver):
    """Discovery wrapper around HeidenhainLsv2Driver."""

    name = "auto"

    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg
        self._inner: Optional[HeidenhainLsv2Driver] = None
        self._found: Optional[Found] = None
        self._known_host: Optional[str] = None
        self._known_at: float = 0.0
        self._progress: List[str] = []
        #: Set by --test-baglanti so the search is visible while it runs.
        self.progress_callback = None
        #: Set by the collector so a shutdown can cut a subnet sweep short.
        self.should_continue = None

    # ---------------------------------------------------------------- connect

    def describe(self) -> str:
        if self._found:
            return f"Otomatik bulundu — {self._found}"
        return "Otomatik arama (tezgah henüz bulunamadı)"

    @property
    def progress(self) -> List[str]:
        """Last few discovery messages, shown in the UI while searching."""
        return list(self._progress)

    @property
    def capabilities(self):
        return self._inner.capabilities if self._inner else {}

    def _note(self, message: str) -> None:
        self._progress.append(message)
        del self._progress[:-6]
        if self.progress_callback:
            self.progress_callback(message)

    def connect(self) -> None:
        self._progress = []
        port = self.cfg.tnc_port or LSV2_PORT

        # A machine we already found stays the first thing we try, for an hour.
        preferred = self.cfg.tnc_ip or None
        if self._known_host and (time.time() - self._known_at) < _REMEMBER_S:
            preferred = self._known_host

        found = find_control(
            configured_ip=preferred,
            port=port,
            timeout=self.cfg.timeout_s,
            scan_subnet=getattr(self.cfg, "auto_scan", True),
            on_progress=self._note,
            should_continue=self.should_continue,
        )

        if found is None:
            self._found = None
            if self.should_continue and not self.should_continue():
                raise DriverError("Arama iptal edildi (program kapanıyor)")
            raise DriverError(self._not_found_message(port))

        self._found = found
        self._known_host = found.host
        self._known_at = time.time()
        self._note(f"Bulundu: {found}")

        # Hand the real work to the LSV2 driver, pointed at what we found.
        inner_cfg = dataclasses.replace(self.cfg, tnc_ip=found.host, tnc_port=found.port)
        inner = HeidenhainLsv2Driver(inner_cfg)
        inner.connect()
        self._inner = inner

    def _not_found_message(self, port: int) -> str:
        scanned = "ağ taraması dahil" if getattr(self.cfg, "auto_scan", True) else (
            "ağ taraması kapalı"
        )
        return (
            "Ağda Heidenhain kontrol bulunamadı ({0}).\n"
            "Kontrol edilecekler:\n"
            "  - Tezgah açık mı ve ağ kablosu takılı mı?\n"
            "  - Tezgahta LSV2/DNC erişimi açık mı? (port {1})\n"
            "  - Adresi biliyorsanız config.ini içinde [surucu] tnc_ip "
            "satırına yazın;\n"
            "    o adres her aramada ilk sırada denenir.\n"
            "Program aramaya devam edecek, bu süre 'BAĞLANTI YOK' olarak "
            "kaydedilir."
        ).format(scanned, port)

    def disconnect(self) -> None:
        if self._inner is not None:
            self._inner.disconnect()
            self._inner = None

    # ------------------------------------------------------------------- read

    def read(self) -> Snapshot:
        if self._inner is None:
            raise DriverError("Tezgah bağlantısı kurulmadı")
        return self._inner.read()
