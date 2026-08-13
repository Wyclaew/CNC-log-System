"""JSON endpoints backing the web UI.

The live log view reads the daily text file rather than the events table on
purpose: that file is what the operator would open by hand, so what the browser
shows and what is on disk can never disagree.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from .. import report
from ..model import STATE_LABELS, MachineState

#: TextLog writes fixed columns: clock [0:8], tag [10:22], message from [22].
#: Parsing by position rather than by whitespace keeps labels that contain a
#: space ("BAĞLANTI YOK") from being split in the middle.
_CLOCK_RE = re.compile(r"^\d{2}:\d{2}:\d{2}$")
_TAG_START, _TAG_END = 10, 22

#: How much of a large log file to read when tailing.
_TAIL_BYTES = 256 * 1024

_KNOWN_TAGS = {label: state for state, label in STATE_LABELS.items()}
_EVENT_TAGS = {"ALARM", "PROGRAM", "BAKIM", "HATA"}


class Api:
    """Everything the browser can ask for. Read-only."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.cfg = app.cfg
        self.storage = app.storage
        self.collector = app.collector

    # ------------------------------------------------------------------ durum

    def durum(self) -> Dict[str, Any]:
        """Live state plus a running summary of the current day."""
        status = self.collector.status()
        now = self._now(status)
        day = report.parse_day(None)
        ts_from, ts_to = report.day_bounds(day)
        summary = report.summarize(self.storage, self.cfg, ts_from, ts_to, now=now)

        status["bugun"] = {
            "run_s": summary["run_s"],
            "down_s": summary["down_s"],
            "setup_s": summary["setup_s"],
            "offline_s": summary["offline_s"],
            "rapid_s": summary["rapid_s"],
            "availability": summary["availability"],
            "stop_count": summary["stop_count"],
            "alarm_count": summary["alarm_count"],
        }
        status["now"] = now
        status["version"] = _version()
        return status

    # ----------------------------------------------------------------- loglar

    def loglar(self, params: Dict[str, str]) -> Dict[str, Any]:
        source = params.get("kaynak", "metin")
        limit = _int(params.get("limit"), 300, low=1, high=5000)
        day_text = params.get("tarih")

        if source == "olay":
            return {"kaynak": "olay", "satirlar": self._event_rows(day_text, limit,
                                                                  params.get("tip"))}
        return {
            "kaynak": "metin",
            "satirlar": self._text_rows(day_text, limit, params.get("tip")),
        }

    def _text_rows(
        self, day_text: Optional[str], limit: int, kind: Optional[str]
    ) -> List[Dict[str, Any]]:
        try:
            day = report.parse_day(day_text)
        except ValueError:
            return []
        path = os.path.join(self.cfg.log_dir, f"{day.isoformat()}.log")
        if not os.path.isfile(path):
            return []

        try:
            size = os.path.getsize(path)
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                if size > _TAIL_BYTES:
                    handle.seek(size - _TAIL_BYTES)
                    handle.readline()  # drop the partial first line
                lines = handle.readlines()
        except OSError:
            return []

        rows: List[Dict[str, Any]] = []
        for line in lines:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            parsed = _parse_line(line)
            if parsed is None:
                continue
            clock, tag, message = parsed
            row = {
                "saat": clock,
                "etiket": tag,
                "mesaj": message.strip(),
                "tur": _row_kind(tag, message),
                "durum": _state_value_for(tag),
            }
            if kind and kind != "hepsi" and row["tur"] != kind:
                continue
            rows.append(row)

        rows.reverse()  # newest first
        return rows[:limit]

    def _event_rows(
        self, day_text: Optional[str], limit: int, kind: Optional[str]
    ) -> List[Dict[str, Any]]:
        try:
            day = report.parse_day(day_text)
        except ValueError:
            return []
        ts_from, ts_to = report.day_bounds(day)
        types = None
        if kind == "alarm":
            types = ["alarm"]
        elif kind == "durus":
            types = ["state"]
        elif kind == "program":
            types = ["program"]

        rows = self.storage.events_between(ts_from, ts_to, types=types, limit=None)
        out: List[Dict[str, Any]] = []
        for row in rows:
            state = row.get("state")
            if kind == "durus" and state not in ("DURUS", "ALARM"):
                continue
            out.append(
                {
                    "saat": datetime.fromtimestamp(row["ts_start"]).strftime("%H:%M:%S"),
                    "bitis": (
                        datetime.fromtimestamp(row["ts_end"]).strftime("%H:%M:%S")
                        if row.get("ts_end")
                        else None
                    ),
                    "tip": row["type"],
                    "durum": state,
                    "etiket": (
                        STATE_LABELS[MachineState(state)]
                        if state
                        else row["type"].upper()
                    ),
                    "sure_s": row.get("duration_s"),
                    "kod": row.get("code"),
                    "mesaj": row.get("text"),
                    "program": row.get("program_name"),
                    "acik": row.get("ts_end") is None,
                }
            )
        out.reverse()
        return out[:limit]

    # ------------------------------------------------------------------ rapor

    def rapor(self, params: Dict[str, str]) -> Dict[str, Any]:
        day = report.parse_day(params.get("tarih"))
        ts_from, ts_to = report.day_bounds(day)
        now = self._now()
        summary = report.summarize(self.storage, self.cfg, ts_from, ts_to, now=now)
        summary["tarih"] = day.isoformat()
        summary["vardiyalar"] = [
            dict(
                report.summarize(self.storage, self.cfg, begin, end, now=now),
                etiket=label,
            )
            for label, begin, end in report.shift_bounds(self.cfg, day)
        ]
        return summary

    def rapor_csv(self, params: Dict[str, str]) -> str:
        day = report.parse_day(params.get("tarih"))
        ts_from, ts_to = report.day_bounds(day)
        summary = report.summarize(
            self.storage, self.cfg, ts_from, ts_to, now=self._now()
        )
        return report.render_csv(summary)

    def _now(self, status: Optional[Dict[str, Any]] = None) -> float:
        """The 'now' that still-open events should be measured against.

        Timestamps in the data come from the driver's clock, which is the wall
        clock in production but runs ahead of it under `--sim-hiz`. Taking the
        later of the two keeps an open event from being clipped to zero length
        whenever the two clocks disagree.
        """
        wall = datetime.now().timestamp()
        latest = (status or self.collector.status()).get("ts") or 0.0
        return max(wall, float(latest))

    # ----------------------------------------------------------------- gunler

    def gunler(self) -> Dict[str, Any]:
        """Dates that actually have a log file, newest first."""
        days: List[str] = []
        try:
            for name in os.listdir(self.cfg.log_dir):
                if name.endswith(".log"):
                    days.append(name[:-4])
        except OSError:
            pass
        days.sort(reverse=True)
        return {"gunler": days}


# ---------------------------------------------------------------- helpers


def _version() -> str:
    from .. import __version__

    return __version__


def _parse_line(line: str):
    """Split a text-log line into (clock, tag, message), or None if it is not one."""
    if len(line) <= _TAG_END:
        return None
    clock = line[:8]
    if not _CLOCK_RE.match(clock):
        return None
    tag = line[_TAG_START:_TAG_END].strip()
    message = line[_TAG_END:].strip()
    if not tag:
        return None
    return clock, tag, message


def _int(raw: Optional[str], fallback: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(raw)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback


def _row_kind(tag: str, message: str) -> str:
    """Classify a text-log line for the UI filter."""
    if tag == "ALARM":
        return "alarm"
    if tag == "PROGRAM":
        return "program"
    if tag in ("BAKIM", "HATA"):
        return "sistem"
    state = _KNOWN_TAGS.get(tag)
    if state in (MachineState.DURUS, MachineState.BAGLANTI_YOK):
        return "durus"
    lowered = message.lower()
    if lowered.startswith(("başladı", "bitti")):
        return "olay"
    return "olcum"


def _state_value_for(tag: str) -> Optional[str]:
    state = _KNOWN_TAGS.get(tag)
    return state.value if state else None
