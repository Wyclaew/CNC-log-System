"""A fake machine that behaves like a real one.

This exists because the Heidenhain TNC 640 Programming Station runs on Windows
only, so there is nothing on a Mac or on a plain Linux box to connect to. It is
not just a stand-in either: it deliberately produces the awkward cases -- a
5-second pause that must stay below the stop threshold, a part change that must
be logged, an alarm, a dropped connection -- so the state machine can be proven
correct before anyone plugs into a machine that is making parts.

The phase timeline is driven by a seeded RNG, so `--sim-seed 42` replays the
exact same shift every time.

NOTE: the alarm codes below are invented for the simulation. They are shaped
like control messages but they are not real Heidenhain error codes.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import List, Optional

from ..model import Alarm, ExecState, Snapshot
from .base import Driver

PROGRAMS = [
    ("PARCA_12.H", 12),
    ("FLANS_A45.H", 7),
    ("MIL_YATAGI.H", 21),
    ("KAPAK_7.H", 4),
]

SIM_ALARMS = [
    ("1024", "Soğutma sıvısı basıncı düşük", "error"),
    ("2311", "Takım ömrü doldu", "warning"),
    ("0517", "Mil devrine ulaşılamadı", "error"),
    ("1180", "Y ekseni konum sapması", "error"),
    ("3002", "Talaş konveyörü sıkıştı", "warning"),
]


@dataclass
class _Phase:
    kind: str  # setup | cutting | rapid | pause | part_change | alarm | offline
    duration: float
    program: Optional[str]
    tool: Optional[int]
    feed: float = 0.0
    spindle: float = 0.0
    alarm: Optional[Alarm] = None


class SimulatorDriver(Driver):
    """Replays a plausible machining shift on a virtual clock."""

    name = "simulator"

    def __init__(
        self,
        cfg,
        speed: float = 1.0,
        seed: Optional[int] = None,
        clock=None,
    ) -> None:
        """`clock` is injectable so tests can replay hours without waiting.

        In normal use it is time.time and the virtual clock advances
        `speed` times faster than the wall clock.
        """
        self.cfg = cfg
        self.time_scale = max(0.1, float(speed))
        self._rng = random.Random(seed)
        self._clock = clock or time.time
        self._connected = False

        self._t_real0 = 0.0
        self._t_virt0 = 0.0

        self._phase: Optional[_Phase] = None
        self._phase_start = 0.0
        self._phase_end = 0.0

        self._program: Optional[str] = None
        self._tool: Optional[int] = None
        self._block = 0
        self._cuts_left = 0
        self._first_part = True

    # ------------------------------------------------------------------ clock

    def _now(self) -> float:
        """Virtual time. Runs `time_scale` times faster than the wall clock."""
        elapsed = self._clock() - self._t_real0
        return self._t_virt0 + elapsed * self.time_scale

    # ---------------------------------------------------------------- lifecycle

    def connect(self) -> None:
        self._t_real0 = self._clock()
        self._t_virt0 = self._clock()
        self._connected = True
        self._start_part()
        self._advance()

    def disconnect(self) -> None:
        self._connected = False

    def describe(self) -> str:
        hiz = "" if self.time_scale == 1.0 else f", hız x{self.time_scale:g}"
        return f"Simülatör (sahte tezgah{hiz})"

    # ------------------------------------------------------------------ phases

    def _r(self, low: float, high: float) -> float:
        return self._rng.uniform(low, high)

    def _start_part(self) -> None:
        """Begin a new part: pick a program, a tool and how many cuts it takes."""
        program, tool = self._rng.choice(PROGRAMS)
        self._program = program
        self._tool = tool
        self._block = 0
        self._cuts_left = self._rng.randint(3, 6)

    def _advance(self) -> None:
        """Move to the next phase, choosing it from the current one."""
        now = self._now()
        self._phase_start = now if self._phase is None else self._phase_end
        self._phase = self._next_phase()
        self._phase_end = self._phase_start + self._phase.duration

    def _next_phase(self) -> _Phase:
        previous = self._phase.kind if self._phase else None

        # A fresh part starts with a short setup on the very first one only.
        if previous is None:
            if self._first_part:
                self._first_part = False
                return _Phase("setup", self._r(25, 45), self._program, self._tool)
            return self._make_cut()

        if previous in ("setup", "pause", "alarm", "offline"):
            return self._make_cut()

        if previous == "part_change":
            self._start_part()
            return _Phase("setup", self._r(15, 30), self._program, self._tool)

        if previous == "rapid":
            if self._cuts_left <= 0:
                return _Phase("part_change", self._r(30, 90), None, self._tool)
            return self._make_cut()

        # previous == "cutting"
        roll = self._rng.random()
        if roll < 0.06:
            # Deliberately under a 10 s threshold: must NOT become an event.
            return _Phase("pause", self._r(3, 6), self._program, self._tool)
        if roll < 0.10:
            code, text, severity = self._rng.choice(SIM_ALARMS)
            return _Phase(
                "alarm",
                self._r(20, 70),
                self._program,
                self._tool,
                alarm=Alarm(code=code, text=text, severity=severity),
            )
        if roll < 0.12:
            return _Phase("offline", self._r(20, 45), None, None)
        self._cuts_left -= 1
        return _Phase("rapid", self._r(5, 20), self._program, self._tool)

    def _make_cut(self) -> _Phase:
        return _Phase(
            "cutting",
            self._r(45, 180),
            self._program,
            self._tool,
            feed=self._r(400, 1800),
            spindle=self._r(3000, 11000),
        )

    # -------------------------------------------------------------------- read

    def read(self) -> Snapshot:
        if not self._connected:
            return Snapshot(ts=self._now(), connected=False, error="Sürücü kapalı")

        now = self._now()
        # Guard against a pathological loop if a phase somehow has no duration.
        for _ in range(100):
            if now < self._phase_end:
                break
            self._advance()
        phase = self._phase
        assert phase is not None

        if phase.kind == "offline":
            return Snapshot(
                ts=now,
                connected=False,
                error="Tezgah ile bağlantı yok (simüle edilmiş kopma)",
            )

        snap = Snapshot(
            ts=now,
            program_name=phase.program,
            tool_number=phase.tool,
            feed_override=100.0,
            spindle_override=100.0,
            rapid_override=100.0,
        )

        if phase.kind == "setup":
            snap.exec_state = ExecState.MANUAL
            snap.feed_actual = 0.0
            snap.spindle_actual = 0.0
            snap.is_rapid = False

        elif phase.kind == "cutting":
            # Small wobble so the numbers look like a real cut, not a constant.
            wobble = 1.0 + self._rng.uniform(-0.08, 0.08)
            self._block += self._rng.randint(1, 4)
            snap.exec_state = ExecState.RUNNING
            snap.feed_actual = round(phase.feed * wobble, 1)
            snap.feed_programmed = round(phase.feed, 1)
            snap.spindle_actual = round(phase.spindle * (1.0 + self._rng.uniform(-0.02, 0.02)))
            snap.spindle_programmed = round(phase.spindle)
            snap.is_rapid = False
            snap.block_number = self._block

        elif phase.kind == "rapid":
            self._block += self._rng.randint(1, 3)
            snap.exec_state = ExecState.RUNNING
            snap.feed_actual = round(self._r(6000, 12000), 1)
            snap.spindle_actual = round(self._r(3000, 11000))
            snap.is_rapid = True
            snap.block_number = self._block

        elif phase.kind == "pause":
            snap.exec_state = ExecState.INTERRUPTED
            snap.feed_actual = 0.0
            snap.spindle_actual = round(self._r(3000, 11000))
            snap.is_rapid = False
            snap.block_number = self._block

        elif phase.kind == "part_change":
            snap.exec_state = ExecState.STOPPED
            snap.feed_actual = 0.0
            snap.spindle_actual = 0.0
            snap.is_rapid = False

        elif phase.kind == "alarm":
            snap.exec_state = ExecState.ERROR
            snap.feed_actual = 0.0
            snap.spindle_actual = 0.0
            snap.is_rapid = False
            snap.block_number = self._block
            if phase.alarm is not None:
                snap.alarms = [phase.alarm]

        return snap


class ScriptedDriver(Driver):
    """Plays back a fixed list of snapshots. Used by the unit tests."""

    name = "scripted"

    def __init__(self, snapshots: List[Snapshot]) -> None:
        self._snapshots = list(snapshots)
        self._index = 0

    def connect(self) -> None:
        self._index = 0

    def read(self) -> Snapshot:
        if self._index >= len(self._snapshots):
            raise IndexError("Senaryo bitti")
        snap = self._snapshots[self._index]
        self._index += 1
        return snap
