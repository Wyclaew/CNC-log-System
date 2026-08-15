"""Derives machine state from driver readings and turns changes into events.

Two ideas drive this module:

1. *Raw* state is what the control looks like right now; *confirmed* state is
   what we are willing to write into the events table. They differ while a
   short stop is still under the threshold. Samples record the raw state so a
   5-second pause is never lost, while events stay uncluttered.

2. When a stop is finally confirmed, its event is opened at the moment the stop
   actually began -- not at the moment the threshold elapsed. Durations in the
   reports are therefore correct, not short by the threshold.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .model import Alarm, Event, EventType, ExecState, MachineState, Snapshot


@dataclass
class UpdateResult:
    """What one collector tick produced."""

    #: Confirmed state -- the one that currently owns an open event.
    state: MachineState
    #: Instantaneous state, may differ from `state` during a pending stop.
    raw_state: MachineState
    is_rapid: Optional[bool] = None
    #: Events that were newly created and need an INSERT.
    opened: List[Event] = field(default_factory=list)
    #: Events that just got an end timestamp and need an UPDATE.
    closed: List[Event] = field(default_factory=list)
    #: True on the tick where the confirmed state actually changed.
    state_changed: bool = False


class StateTracker:
    """Turns a stream of snapshots into state, alarm and program events."""

    def __init__(
        self,
        machine_id: str,
        idle_threshold_s: float = 10.0,
        rapid_feed_threshold: float = 5000.0,
    ) -> None:
        self.machine_id = machine_id
        self.idle_threshold_s = max(0.0, idle_threshold_s)
        self.rapid_feed_threshold = rapid_feed_threshold

        self.state: Optional[MachineState] = None
        self.state_since: Optional[float] = None
        self._open_state_event: Optional[Event] = None

        self._candidate: Optional[MachineState] = None
        self._candidate_since: float = 0.0
        self._candidate_snap: Optional[Snapshot] = None

        # key -> (alarm, its open event)
        self._active_alarms: Dict[str, Tuple[Alarm, Event]] = {}
        self._program: Optional[str] = None
        self._open_program_event: Optional[Event] = None

    # ------------------------------------------------------------------ public

    def update(self, snap: Snapshot) -> UpdateResult:
        now = snap.ts
        raw = self.derive_state(snap)
        is_rapid = self.derive_rapid(snap)

        opened: List[Event] = []
        closed: List[Event] = []

        # When the link is down we know nothing, so anything we were tracking
        # has to be closed rather than left hanging with a stale end time.
        if raw is MachineState.BAGLANTI_YOK:
            closed.extend(self._close_all_alarms(now))
            closed.extend(self._close_program(now))
        else:
            a_opened, a_closed = self._track_alarms(snap, now)
            opened.extend(a_opened)
            closed.extend(a_closed)

            p_opened, p_closed = self._track_program(snap, now)
            opened.extend(p_opened)
            closed.extend(p_closed)

        s_opened, s_closed, changed = self._track_state(raw, now, snap)
        opened.extend(s_opened)
        closed.extend(s_closed)

        return UpdateResult(
            state=self.state or raw,
            raw_state=raw,
            is_rapid=is_rapid,
            opened=opened,
            closed=closed,
            state_changed=changed,
        )

    def close_all(self, ts: float) -> List[Event]:
        """Close every open event. Called on shutdown so nothing dangles."""
        closed: List[Event] = []
        closed.extend(self._close_all_alarms(ts))
        closed.extend(self._close_program(ts))
        if self._open_state_event is not None:
            self._open_state_event.ts_end = ts
            closed.append(self._open_state_event)
            self._open_state_event = None
        return closed

    # ------------------------------------------------------------- derivation

    def derive_state(self, snap: Snapshot) -> MachineState:
        """Map a snapshot onto the state the operator cares about.

        Order matters: a lost connection outranks everything, then alarms, then
        whatever the control reports about program execution.
        """
        if not snap.connected:
            return MachineState.BAGLANTI_YOK
        if snap.alarms:
            return MachineState.ALARM
        if snap.exec_state is ExecState.ERROR:
            return MachineState.ALARM
        if snap.exec_state is ExecState.RUNNING:
            # A running program with F=0 is still running -- tool changes and
            # dwells are not downtime. Downtime is when the program is not
            # advancing at all.
            return MachineState.CALISIYOR
        if snap.exec_state is ExecState.MANUAL:
            return MachineState.KURULUM
        return MachineState.DURUS

    def derive_rapid(self, snap: Snapshot) -> Optional[bool]:
        """Prefer the control's own rapid flag; fall back to a feed heuristic."""
        if snap.is_rapid is not None:
            return snap.is_rapid
        if snap.feed_actual is None:
            return None
        return snap.feed_actual >= self.rapid_feed_threshold

    def threshold_for(self, state: MachineState) -> float:
        """Only stops are debounced. Alarms and reconnects are logged at once."""
        if state is MachineState.DURUS:
            return self.idle_threshold_s
        return 0.0

    # ----------------------------------------------------------------- private

    def _track_state(
        self, raw: MachineState, now: float, snap: Snapshot
    ) -> Tuple[List[Event], List[Event], bool]:
        opened: List[Event] = []
        closed: List[Event] = []

        if self.state is None:
            self.state = raw
            self.state_since = now
            self._open_state_event = self._new_state_event(raw, now, snap)
            opened.append(self._open_state_event)
            return opened, closed, True

        if raw is self.state:
            self._candidate = None
            self._candidate_snap = None
            return opened, closed, False

        if self._candidate is not raw:
            self._candidate = raw
            self._candidate_since = now
            # Keep the snapshot from the moment the change began: the reason
            # for a stop has to describe when it started, not the tick where
            # the threshold happened to elapse.
            self._candidate_snap = snap

        if now - self._candidate_since < self.threshold_for(raw):
            # Still provisional; keep the current event open.
            return opened, closed, False

        # Confirm the switch, backdated to when the new state really started.
        switch_at = self._candidate_since
        if self._open_state_event is not None:
            self._open_state_event.ts_end = switch_at
            closed.append(self._open_state_event)

        self.state = raw
        self.state_since = switch_at
        self._open_state_event = self._new_state_event(
            raw, switch_at, self._candidate_snap or snap
        )
        opened.append(self._open_state_event)
        self._candidate = None
        self._candidate_snap = None
        return opened, closed, True

    def _reason_for(self, state: MachineState, snap: Snapshot) -> Optional[str]:
        """Best explanation we can give for why the machine entered this state.

        The control does not tell us *why* it stopped, so this is an inference
        from what was loaded at the time -- worded so it never reads as a
        certainty the data does not support.
        """
        if state is MachineState.DURUS:
            if not snap.program_name:
                return "program yüklü değil (parça değişimi veya bekleme)"
            return f"program durduruldu ({snap.program_name})"
        if state is MachineState.BAGLANTI_YOK:
            # Driver errors carry multi-line troubleshooting advice; the event
            # row wants the headline. The full text stays in the UI's error field.
            first_line = (snap.error or "").strip().splitlines()
            return first_line[0] if first_line else "tezgah ile bağlantı kesildi"
        if state is MachineState.KURULUM:
            return "elle kumanda / kurulum modu"
        return None

    def _track_alarms(
        self, snap: Snapshot, now: float
    ) -> Tuple[List[Event], List[Event]]:
        opened: List[Event] = []
        closed: List[Event] = []

        incoming = {a.key(): a for a in snap.alarms}
        for key, alarm in incoming.items():
            if key in self._active_alarms:
                continue
            event = Event(
                machine_id=self.machine_id,
                type=EventType.ALARM,
                ts_start=now,
                code=alarm.code,
                text=alarm.text,
                program_name=snap.program_name,
            )
            self._active_alarms[key] = (alarm, event)
            opened.append(event)

        for key in [k for k in self._active_alarms if k not in incoming]:
            _alarm, event = self._active_alarms.pop(key)
            event.ts_end = now
            closed.append(event)

        return opened, closed

    def _close_all_alarms(self, now: float) -> List[Event]:
        closed = []
        for _alarm, event in self._active_alarms.values():
            event.ts_end = now
            closed.append(event)
        self._active_alarms.clear()
        return closed

    def _track_program(
        self, snap: Snapshot, now: float
    ) -> Tuple[List[Event], List[Event]]:
        opened: List[Event] = []
        closed: List[Event] = []
        name = snap.program_name or None

        if name == self._program:
            return opened, closed

        closed.extend(self._close_program(now))

        if name:
            event = Event(
                machine_id=self.machine_id,
                type=EventType.PROGRAM,
                ts_start=now,
                program_name=name,
                text=name,
            )
            self._open_program_event = event
            opened.append(event)
        self._program = name
        return opened, closed

    def _close_program(self, now: float) -> List[Event]:
        if self._open_program_event is None:
            self._program = None
            return []
        event = self._open_program_event
        event.ts_end = now
        self._open_program_event = None
        self._program = None
        return [event]

    def _new_state_event(
        self, state: MachineState, ts: float, snap: Snapshot
    ) -> Event:
        return Event(
            machine_id=self.machine_id,
            type=EventType.STATE,
            ts_start=ts,
            state=state,
            text=self._reason_for(state, snap),
            program_name=snap.program_name,
        )
