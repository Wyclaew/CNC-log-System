"""Core data types shared by drivers, collector, storage and the web layer.

Enum values are deliberately ASCII so they survive round-trips through SQLite,
JSON and CSV on any locale. Turkish text lives in the LABELS tables and is
applied only at display time.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class ExecState(enum.Enum):
    """Raw execution state as reported by the control.

    Drivers map whatever their protocol gives them onto this small set; the
    state machine never sees vendor-specific values.
    """

    RUNNING = "running"
    STOPPED = "stopped"
    INTERRUPTED = "interrupted"
    MANUAL = "manual"
    ERROR = "error"
    UNKNOWN = "unknown"


class MachineState(enum.Enum):
    """Derived state that the text log, the events table and reports use."""

    CALISIYOR = "CALISIYOR"
    DURUS = "DURUS"
    ALARM = "ALARM"
    KURULUM = "KURULUM"
    BAGLANTI_YOK = "BAGLANTI_YOK"


#: Display labels. Kept apart from the enum values so the stored data stays
#: ASCII while the operator sees proper Turkish.
STATE_LABELS: Dict[MachineState, str] = {
    MachineState.CALISIYOR: "ÇALIŞIYOR",
    MachineState.DURUS: "DURUŞ",
    MachineState.ALARM: "ALARM",
    MachineState.KURULUM: "KURULUM",
    MachineState.BAGLANTI_YOK: "BAĞLANTI YOK",
}

#: States that count as productive time in availability calculations.
PRODUCTIVE_STATES = frozenset({MachineState.CALISIYOR})

#: States that count as downtime. BAGLANTI_YOK is deliberately absent: a PC
#: that was switched off overnight must not be reported as machine downtime.
DOWNTIME_STATES = frozenset({MachineState.DURUS, MachineState.ALARM})


class EventType(enum.Enum):
    """Kind of row stored in the events table."""

    STATE = "state"
    ALARM = "alarm"
    PROGRAM = "program"
    CONNECTION = "connection"


@dataclass
class Alarm:
    """A single active message reported by the control."""

    code: str
    text: str
    severity: str = "error"  # info | warning | error

    def key(self) -> str:
        """Identity used to tell a still-active alarm from a new one."""
        return f"{self.code}|{self.text}"

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "text": self.text, "severity": self.severity}


@dataclass
class Snapshot:
    """One reading from a driver.

    Every measurement is Optional. A driver fills in whatever the control
    actually exposes and leaves the rest as None; nothing downstream may assume
    a field is present. This is what lets the same code run against a TNC 640
    with the OPC UA option and against one where only LSV2 is reachable.
    """

    ts: float = field(default_factory=time.time)
    exec_state: ExecState = ExecState.UNKNOWN

    feed_actual: Optional[float] = None  # mm/min
    feed_programmed: Optional[float] = None  # mm/min
    spindle_actual: Optional[float] = None  # rpm
    spindle_programmed: Optional[float] = None  # rpm

    feed_override: Optional[float] = None  # %
    spindle_override: Optional[float] = None  # %
    rapid_override: Optional[float] = None  # %

    is_rapid: Optional[bool] = None  # True while the control is in a G0 move

    program_name: Optional[str] = None
    block_number: Optional[int] = None
    tool_number: Optional[int] = None

    alarms: List[Alarm] = field(default_factory=list)

    #: False whenever the driver could not talk to the control at all.
    connected: bool = True
    #: Human-readable reason, shown in the UI when connected is False.
    error: Optional[str] = None

    def alarm_keys(self) -> frozenset:
        return frozenset(a.key() for a in self.alarms)


@dataclass
class Sample:
    """A periodic measurement row, one per collector tick."""

    ts: float
    machine_id: str
    state: MachineState
    feed_actual: Optional[float] = None
    spindle_actual: Optional[float] = None
    feed_override: Optional[float] = None
    spindle_override: Optional[float] = None
    rapid_override: Optional[float] = None
    is_rapid: Optional[bool] = None
    program_name: Optional[str] = None
    block_number: Optional[int] = None
    tool_number: Optional[int] = None

    def to_row(self) -> tuple:
        return (
            self.ts,
            self.machine_id,
            self.state.value,
            self.feed_actual,
            self.spindle_actual,
            self.feed_override,
            self.spindle_override,
            self.rapid_override,
            None if self.is_rapid is None else int(self.is_rapid),
            self.program_name,
            self.block_number,
            self.tool_number,
        )


@dataclass
class Event:
    """A span of time or a discrete occurrence worth keeping forever.

    Samples get pruned by the retention policy; events do not. Every duration
    the reports show is computed from these rows, never from sample counts, so
    the numbers stay correct even if the collector was restarted or the
    sampling interval changed.
    """

    machine_id: str
    type: EventType
    ts_start: float
    ts_end: Optional[float] = None
    state: Optional[MachineState] = None
    code: Optional[str] = None
    text: Optional[str] = None
    program_name: Optional[str] = None
    #: Set by storage once the row is written, so the collector can close it.
    id: Optional[int] = None

    @property
    def duration_s(self) -> Optional[float]:
        if self.ts_end is None:
            return None
        return max(0.0, self.ts_end - self.ts_start)

    @property
    def is_open(self) -> bool:
        return self.ts_end is None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "machine_id": self.machine_id,
            "type": self.type.value,
            "ts_start": self.ts_start,
            "ts_end": self.ts_end,
            "duration_s": self.duration_s,
            "state": self.state.value if self.state else None,
            "code": self.code,
            "text": self.text,
            "program_name": self.program_name,
        }


def format_duration(seconds: Optional[float]) -> str:
    """Render a duration the way the operator reads it: '3dk 29sn', '2sa 05dk'."""
    if seconds is None:
        return "—"
    # Clamp: a clock adjustment can produce a negative span, and "-3dk" in a
    # downtime report would be nonsense.
    total = max(0, int(round(seconds)))
    if total < 60:
        return f"{total}sn"
    if total < 3600:
        return f"{total // 60}dk {total % 60:02d}sn"
    hours, rest = divmod(total, 3600)
    return f"{hours}sa {rest // 60:02d}dk"


def format_number(value: Optional[float], decimals: int = 0) -> str:
    """Render a measurement, or an em dash when the control did not expose it."""
    if value is None:
        return "—"
    if decimals == 0:
        return str(int(round(value)))
    return f"{value:.{decimals}f}"
