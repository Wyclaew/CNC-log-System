"""Wires the pieces together: config -> driver -> storage -> collector.

Both the console mode and the web mode go through this, so they cannot drift
apart in how they set things up.
"""

from __future__ import annotations

import os
import time
from typing import Any, Optional

from .collector import Collector
from .config import Config, load_config
from .drivers.base import Driver, DriverError
from .drivers.registry import create_driver
from .lock import AlreadyRunning, InstanceLock
from .storage import TextLog, ensure_dir, open_storage


class Application:
    """A running logger: storage open, driver built, collector ready.

    `writer=True` means this process intends to record. It takes the instance
    lock and repairs anything a previous crash left half-written. Read-only
    modes (report, connection test) pass writer=False so they can run
    alongside a live logger without disturbing it.
    """

    def __init__(self, cfg: Config, driver: Driver, writer: bool = True) -> None:
        self.cfg = cfg
        self.driver = driver
        self.writer = writer
        ensure_dir(cfg.data_path)

        self.lock: Optional[InstanceLock] = None
        self.storage: Any = None
        self.text_log: Optional[TextLog] = None
        self.recovered_events = 0

        # Anything that fails after the lock is taken has to give it back.
        # A leaked lock file would keep the next start from ever succeeding.
        try:
            if writer:
                self.lock = InstanceLock(os.path.join(cfg.data_path, "cnclog.lock"))
                self.lock.acquire()  # raises AlreadyRunning

            self.storage = open_storage(cfg.db_path, cfg.data_path)
            self.text_log = TextLog(cfg.log_dir, cfg.machine_name)

            if writer:
                # Holding the lock proves no other collector is writing, so any
                # event still open belongs to a run that was killed.
                self.recovered_events = self.storage.close_dangling_events()
                if self.recovered_events:
                    self.text_log.write(
                        time.time(),
                        "BAKIM",
                        f"Önceki oturum düzgün kapanmamış: {self.recovered_events} "
                        "açık kayıt, son ölçüm zamanıyla kapatıldı",
                    )

            self.collector = Collector(cfg, driver, self.storage, self.text_log)
        except BaseException:
            self._release_resources()
            raise

    def _release_resources(self) -> None:
        """Close whatever was opened, in reverse order, swallowing failures."""
        for closer in (
            getattr(self.text_log, "close", None),
            getattr(self.storage, "close", None),
            getattr(self.lock, "release", None),
        ):
            if closer is None:
                continue
            try:
                closer()
            except Exception:  # noqa: BLE001 - teardown must not mask the cause
                pass
        self.text_log = None
        self.storage = None
        self.lock = None

    def start(self) -> None:
        self.collector.start()

    def stop(self) -> None:
        self.collector.stop()
        if not self.collector.stopped_cleanly:
            # The collector thread is still running somewhere. Closing the
            # database under it would only produce errors on the way out; it is
            # a daemon thread, so process exit cleans everything up and the
            # open events get repaired on the next start.
            return
        self._release_resources()

    def close_readonly(self) -> None:
        """Tear down without touching the collector (report / test modes)."""
        self._release_resources()


def build_application(
    config_path: Optional[str] = None,
    base_dir: Optional[str] = None,
    driver_name: Optional[str] = None,
    sim_speed: Optional[float] = None,
    sim_seed: Optional[int] = None,
    port: Optional[int] = None,
    writer: bool = True,
) -> Application:
    """Load config, apply command-line overrides, construct everything."""
    base = base_dir or os.getcwd()
    cfg = load_config(config_path, base_dir=base)

    if driver_name:
        cfg.driver = driver_name.strip().lower()
    if port:
        cfg.web_port = port

    driver = _make_driver(cfg, sim_speed, sim_seed)
    return Application(cfg, driver, writer=writer)


def _make_driver(
    cfg: Config, sim_speed: Optional[float], sim_seed: Optional[int]
) -> Driver:
    if cfg.driver == "simulator":
        # Built directly so the simulator-only options stay off the generic
        # driver constructor signature.
        from .drivers.simulator import SimulatorDriver

        return SimulatorDriver(cfg, speed=sim_speed or 1.0, seed=sim_seed)
    return create_driver(cfg.driver, cfg)


__all__ = ["Application", "AlreadyRunning", "build_application", "DriverError"]
