"""The sampling loop: read the machine, derive state, persist, repeat.

Runs on its own daemon thread so the web layer never blocks it and a slow HTTP
request can never cost a sample. Every failure path here ends in "record
BAGLANTI_YOK and keep going" -- a logger that dies mid-shift is worse than one
that reports a gap honestly.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from .config import Config
from .drivers.base import Driver, DriverError, offline_snapshot
from .model import (
    Event,
    EventType,
    MachineState,
    STATE_LABELS,
    Sample,
    Snapshot,
)
from .state import StateTracker
from .storage import TextLog

#: Reconnect backoff bounds, in wall-clock seconds.
_BACKOFF_START = 2.0
_BACKOFF_MAX = 60.0


class Collector:
    """Owns the driver, the state tracker and everything that gets written."""

    def __init__(
        self,
        cfg: Config,
        driver: Driver,
        storage: Any,
        text_log: TextLog,
    ) -> None:
        self.cfg = cfg
        self.driver = driver
        self.storage = storage
        self.text_log = text_log
        self.tracker = StateTracker(
            machine_id=cfg.machine_id,
            idle_threshold_s=cfg.idle_threshold_s,
            rapid_feed_threshold=cfg.rapid_feed_threshold,
        )

        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

        self._driver_ready = False
        self._backoff = _BACKOFF_START
        self._retry_at = 0.0
        self._last_error: Optional[str] = None

        self._last_log_ts = 0.0
        self._last_logged_state: Optional[MachineState] = None
        self._last_prune_day: Optional[str] = None

        self._last_snapshot: Optional[Snapshot] = None
        self._last_state: Optional[MachineState] = None
        self._last_raw_state: Optional[MachineState] = None
        self._started_at = time.time()
        self._tick_count = 0
        self.internal_errors = 0
        self.last_internal_error: Optional[str] = None
        #: False if the collector thread would not stop in time on shutdown.
        self.stopped_cleanly = True

    # ---------------------------------------------------------------- lifecycle

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        # Let a driver that does slow work in connect() (the auto driver sweeps
        # the subnet) bail out as soon as we are shutting down.
        if hasattr(self.driver, "should_continue"):
            self.driver.should_continue = lambda: not self._stop.is_set()
        self._thread = threading.Thread(
            target=self._loop, name="cnclog-collector", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 15.0) -> None:
        """Stop collecting and close every open event.

        The timeout is generous because a tick can be inside a driver call.
        Finalizing while the thread is still writing would corrupt the very
        durations we are trying to close out, so if it will not stop we leave
        the events open -- the next start repairs them -- rather than race it.
        """
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                self.stopped_cleanly = False
                self._thread = None
                return
            self._thread = None
        self.stopped_cleanly = True
        self._finalize()

    def _finalize(self) -> None:
        """Close every open event so no row is left dangling after a shutdown."""
        ts = self._last_snapshot.ts if self._last_snapshot else time.time()
        for event in self.tracker.close_all(ts):
            self.storage.close_event(event)
            self._log_event(event)
        try:
            self.driver.disconnect()
        except Exception:  # noqa: BLE001 - shutdown must not raise
            pass

    def _loop(self) -> None:
        # Sleeping in wall-clock terms; the simulator's virtual clock runs
        # time_scale times faster, so its interval shrinks to match.
        scale = max(0.1, getattr(self.driver, "time_scale", 1.0) or 1.0)
        interval = max(0.05, self.cfg.sample_interval_s / scale)
        while not self._stop.is_set():
            started = time.time()
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - never let the loop die
                self._note_internal_error(exc)
            elapsed = time.time() - started
            self._stop.wait(max(0.0, interval - elapsed))

    # --------------------------------------------------------------------- tick

    def tick(self) -> None:
        """One sampling cycle. Kept public so tests can drive it directly."""
        snap = self._read()
        result = self.tracker.update(snap)

        # Samples record the *raw* state: a 5-second pause that never became an
        # event still has to be visible in the data.
        sample = Sample(
            ts=snap.ts,
            machine_id=self.cfg.machine_id,
            state=result.raw_state,
            feed_actual=snap.feed_actual,
            spindle_actual=snap.spindle_actual,
            feed_override=snap.feed_override,
            spindle_override=snap.spindle_override,
            rapid_override=snap.rapid_override,
            is_rapid=result.is_rapid,
            program_name=snap.program_name,
            block_number=snap.block_number,
            tool_number=snap.tool_number,
        )
        self.storage.write_sample(sample)

        # Closed first, then opened: both carry the same switch timestamp, and
        # this keeps the text log in a sensible reading order.
        for event in result.closed:
            self.storage.close_event(event)
            self._log_event(event)
        for event in result.opened:
            self.storage.insert_event(event)
            self._log_event(event)

        # Routine status lines report the *raw* state. Showing the confirmed
        # state here would print "ÇALIŞIYOR  F=0" while the machine is visibly
        # standing still, waiting for the threshold to elapse.
        self._maybe_log_status(snap, result.raw_state, sample, result.state_changed)

        with self._lock:
            self._last_snapshot = snap
            self._last_state = result.state
            self._last_raw_state = result.raw_state
            self._tick_count += 1

        self._maybe_prune(snap.ts)

    def _read(self) -> Snapshot:
        """Read the driver, reconnecting with backoff when the link is down."""
        wall = time.time()
        if not self._driver_ready:
            if wall < self._retry_at:
                return offline_snapshot(wall, self._last_error or "Bağlantı bekleniyor")
            try:
                self.driver.connect()
                self._driver_ready = True
                self._backoff = _BACKOFF_START
                self._last_error = None
            except (DriverError, OSError) as exc:
                return self._go_offline(wall, str(exc))

        try:
            return self.driver.read()
        except (DriverError, OSError) as exc:
            return self._go_offline(wall, str(exc))

    def _go_offline(self, ts: float, reason: str) -> Snapshot:
        self._driver_ready = False
        self._last_error = reason
        self._retry_at = ts + self._backoff
        self._backoff = min(self._backoff * 2, _BACKOFF_MAX)
        try:
            self.driver.disconnect()
        except Exception:  # noqa: BLE001
            pass
        return offline_snapshot(ts, reason)

    def _note_internal_error(self, exc: Exception) -> None:
        """An unexpected bug in our own code: log it, do not stop collecting.

        Wrapped in its own guard because this runs from the loop's last-resort
        handler. If writing the error also failed, the exception would escape
        the loop and kill the collector thread -- recording would stop silently,
        which is the one outcome worse than the original bug.
        """
        self.internal_errors += 1
        self.last_internal_error = f"{type(exc).__name__}: {exc}"
        try:
            self.text_log.write(
                time.time(), "HATA", f"İç hata: {self.last_internal_error}"
            )
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ logging

    def _log_event(self, event: Event) -> None:
        # A state event of ALARM would duplicate the alarm event's own line,
        # which already carries the code and the message.
        if event.type is EventType.STATE and event.state is MachineState.ALARM:
            return
        self.text_log.write_event(event)

    def _maybe_log_status(
        self,
        snap: Snapshot,
        state: MachineState,
        sample: Sample,
        state_changed: bool,
    ) -> None:
        """Write a routine status line on a state change or on the interval."""
        changed = state is not self._last_logged_state
        self._last_logged_state = state

        if state is MachineState.BAGLANTI_YOK:
            # The state event line already carries the reason, and there are no
            # measurements to report while the link is down, so nothing extra
            # is written here.
            self._last_log_ts = snap.ts
            return

        due = (
            self.cfg.log_interval_s > 0
            and snap.ts - self._last_log_ts >= self.cfg.log_interval_s
        )
        if changed or due:
            self.text_log.write_status(snap.ts, state, sample)
            self._last_log_ts = snap.ts

    def _maybe_prune(self, ts: float) -> None:
        """Apply the retention policy once per day."""
        if self.cfg.retention_days <= 0:
            return
        day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        if day == self._last_prune_day:
            return
        self._last_prune_day = day
        try:
            removed = self.storage.prune(self.cfg.retention_days)
            if removed:
                self.text_log.write(
                    ts,
                    "BAKIM",
                    f"{removed} eski ölçüm silindi "
                    f"({self.cfg.retention_days} günden eski)",
                )
        except Exception:  # noqa: BLE001 - pruning must never break collection
            pass

    # -------------------------------------------------------------------- status

    def status(self) -> Dict[str, Any]:
        """Current picture for the web layer. Safe to call from any thread."""
        with self._lock:
            snap = self._last_snapshot
            state = self._last_state
            raw_state = self._last_raw_state
            ticks = self._tick_count

        state = state or MachineState.BAGLANTI_YOK
        raw_state = raw_state or state
        payload: Dict[str, Any] = {
            "machine_id": self.cfg.machine_id,
            "machine_name": self.cfg.machine_name,
            "driver": self.driver.describe(),
            "driver_name": self.driver.name,
            # `state` is the confirmed one that owns an open event and drives
            # the durations; `raw_state` is what the machine looks like right
            # now. The UI shows raw so the operator is never told the machine
            # is running while it visibly stands still.
            "state": state.value,
            "state_label": STATE_LABELS[state],
            "raw_state": raw_state.value,
            "raw_state_label": STATE_LABELS[raw_state],
            "pending_change": raw_state is not state,
            "state_since": self.tracker.state_since,
            "connected": bool(snap and snap.connected),
            "error": snap.error if snap else "Henüz okuma yapılmadı",
            "ts": snap.ts if snap else None,
            "ticks": ticks,
            "started_at": self._started_at,
            "idle_threshold_s": self.cfg.idle_threshold_s,
            "sample_interval_s": self.cfg.sample_interval_s,
            "yenileme_ms": int(self.cfg.web_refresh_s * 1000),
            "storage_backend": getattr(self.storage, "backend", "?"),
        }

        # While the auto driver is still hunting for the control, show what it
        # is doing instead of a bare "BAĞLANTI YOK".
        arama = getattr(self.driver, "progress", None)
        if arama:
            payload["arama"] = list(arama)
        if snap:
            payload.update(
                {
                    "feed_actual": snap.feed_actual,
                    "feed_programmed": snap.feed_programmed,
                    "spindle_actual": snap.spindle_actual,
                    "spindle_programmed": snap.spindle_programmed,
                    "feed_override": snap.feed_override,
                    "spindle_override": snap.spindle_override,
                    "rapid_override": snap.rapid_override,
                    "is_rapid": self.tracker.derive_rapid(snap),
                    "program_name": snap.program_name,
                    "block_number": snap.block_number,
                    "tool_number": snap.tool_number,
                    "alarms": [a.to_dict() for a in snap.alarms],
                }
            )
        return payload
