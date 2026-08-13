"""Shift and day summaries built from the events table.

Durations always come from events, never from counting samples: the sampling
interval can change, the collector can be restarted, and samples get pruned
after the retention window. Events survive all of that.

Every span is clipped to the requested window, so a stop that straddles
midnight is split correctly between the two days instead of being counted twice.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, time as dtime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .config import Config
from .model import (
    DOWNTIME_STATES,
    MachineState,
    PRODUCTIVE_STATES,
    STATE_LABELS,
    format_duration,
)


def overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    """Seconds shared by two intervals."""
    return max(0.0, min(a1, b1) - max(a0, b0))


def day_bounds(day: date) -> Tuple[float, float]:
    start = datetime.combine(day, dtime.min)
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


def parse_day(text: Optional[str]) -> date:
    """Accept 'bugun', 'dun', or an ISO date. Defaults to today."""
    value = (text or "").strip().lower()
    if not value or value in ("bugun", "bugün", "today"):
        return date.today()
    if value in ("dun", "dün", "yesterday"):
        return date.today() - timedelta(days=1)
    return datetime.strptime(value, "%Y-%m-%d").date()


def shift_bounds(cfg: Config, day: date) -> List[Tuple[str, float, float]]:
    """Split a day into shifts using the configured start times.

    A shift that starts at 00:00 and one that starts at 16:00 mean the last
    shift wraps past midnight; the wrap is closed at the day boundary so the
    numbers still add up to one day.
    """
    starts: List[float] = []
    for raw in cfg.shift_starts:
        try:
            hh, mm = raw.split(":")
            moment = datetime.combine(day, dtime(int(hh), int(mm)))
        except (ValueError, TypeError):
            continue
        starts.append(moment.timestamp())
    if not starts:
        t0, t1 = day_bounds(day)
        return [("Tüm gün", t0, t1)]

    starts.sort()
    day_start, day_end = day_bounds(day)
    if starts[0] > day_start:
        starts.insert(0, day_start)

    result = []
    for index, begin in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else day_end
        label = datetime.fromtimestamp(begin).strftime("%H:%M")
        label_end = datetime.fromtimestamp(end).strftime("%H:%M")
        result.append((f"{label}–{label_end}", begin, end))
    return result


def _clip(row: Dict[str, Any], ts_from: float, ts_to: float, now: float) -> float:
    """Seconds of this event that fall inside the window.

    An event with no end is still running: clip it at now (or the window end),
    which is what makes 'today so far' come out right mid-shift.
    """
    start = row["ts_start"]
    end = row.get("ts_end")
    if end is None:
        end = min(now, ts_to)
    return overlap(start, end, ts_from, ts_to)


def summarize(
    storage: Any,
    cfg: Config,
    ts_from: float,
    ts_to: float,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Everything the report screen and the CLI report need for one window."""
    now = now if now is not None else datetime.now().timestamp()
    events = storage.events_between(ts_from, ts_to)

    # Timestamps come from the driver's clock. If it ran ahead of the wall
    # clock -- which `--sim-hiz` does deliberately, and a machine with a
    # drifting clock can do accidentally -- an open event would otherwise be
    # clipped to zero length. Trust the latest moment the data itself proves.
    for row in events:
        now = max(now, row["ts_start"], row.get("ts_end") or 0.0)
    now = min(now, ts_to)

    durations: Dict[str, float] = {s.value: 0.0 for s in MachineState}
    stops: List[Dict[str, Any]] = []
    alarms: List[Dict[str, Any]] = []
    state_spans: List[Tuple[float, float, str]] = []
    program_spans: List[Tuple[float, float, str]] = []

    for row in events:
        seconds = _clip(row, ts_from, ts_to, now)
        row_type = row["type"]

        if row_type == "state":
            state = row.get("state") or MachineState.DURUS.value
            durations[state] = durations.get(state, 0.0) + seconds
            end = row.get("ts_end") if row.get("ts_end") is not None else min(now, ts_to)
            state_spans.append((row["ts_start"], end, state))
            if state in (s.value for s in DOWNTIME_STATES) and seconds > 0:
                stops.append(
                    {
                        "ts_start": row["ts_start"],
                        "ts_end": row.get("ts_end"),
                        "duration_s": seconds,
                        "state": state,
                        "state_label": STATE_LABELS[MachineState(state)],
                        "reason": row.get("text"),
                        "program_name": row.get("program_name"),
                        "open": row.get("ts_end") is None,
                    }
                )

        elif row_type == "alarm":
            alarms.append(
                {
                    "ts_start": row["ts_start"],
                    "ts_end": row.get("ts_end"),
                    "duration_s": seconds,
                    "code": row.get("code"),
                    "text": row.get("text"),
                    "open": row.get("ts_end") is None,
                }
            )

        elif row_type == "program":
            end = row.get("ts_end") if row.get("ts_end") is not None else min(now, ts_to)
            program_spans.append((row["ts_start"], end, row.get("program_name") or "?"))

    run_s = sum(durations.get(s.value, 0.0) for s in PRODUCTIVE_STATES)
    down_s = sum(durations.get(s.value, 0.0) for s in DOWNTIME_STATES)
    setup_s = durations.get(MachineState.KURULUM.value, 0.0)
    offline_s = durations.get(MachineState.BAGLANTI_YOK.value, 0.0)

    # Availability deliberately excludes time we could not observe. A PC that
    # was switched off overnight must not read as machine downtime.
    observed_s = run_s + down_s + setup_s
    availability = (run_s / observed_s * 100.0) if observed_s > 0 else None

    programs = _program_totals(program_spans, state_spans, ts_from, ts_to)
    rapid_s = _rapid_seconds(storage, cfg, ts_from, ts_to)

    stops.sort(key=lambda s: s["duration_s"], reverse=True)
    alarms.sort(key=lambda a: a["ts_start"])

    return {
        "ts_from": ts_from,
        "ts_to": ts_to,
        "machine_id": cfg.machine_id,
        "machine_name": cfg.machine_name,
        "run_s": run_s,
        "down_s": down_s,
        "setup_s": setup_s,
        "offline_s": offline_s,
        "observed_s": observed_s,
        "rapid_s": rapid_s,
        "availability": availability,
        "durations": durations,
        "stop_count": len(stops),
        "stops": stops,
        "alarm_count": len(alarms),
        "alarms": alarms,
        "programs": programs,
        "idle_threshold_s": cfg.idle_threshold_s,
    }


def _program_totals(
    program_spans: List[Tuple[float, float, str]],
    state_spans: List[Tuple[float, float, str]],
    ts_from: float,
    ts_to: float,
) -> List[Dict[str, Any]]:
    """Per-program loaded time and actual running time.

    Running time is the intersection of "this program was loaded" with "the
    machine was in ÇALIŞIYOR" -- that is the honest answer to "programı toplam
    kaç dakika çalıştırdık", as opposed to how long it sat loaded.
    """
    running = [
        (start, end)
        for start, end, state in state_spans
        if state == MachineState.CALISIYOR.value
    ]

    totals: Dict[str, Dict[str, float]] = {}
    for start, end, name in program_spans:
        entry = totals.setdefault(name, {"loaded_s": 0.0, "run_s": 0.0, "count": 0.0})
        entry["loaded_s"] += overlap(start, end, ts_from, ts_to)
        entry["count"] += 1
        for run_start, run_end in running:
            entry["run_s"] += overlap(
                max(start, ts_from), min(end, ts_to), run_start, run_end
            )

    rows = [
        {
            "program_name": name,
            "loaded_s": values["loaded_s"],
            "run_s": values["run_s"],
            "count": int(values["count"]),
        }
        for name, values in totals.items()
    ]
    rows.sort(key=lambda r: r["run_s"], reverse=True)
    return rows


def _rapid_seconds(
    storage: Any, cfg: Config, ts_from: float, ts_to: float
) -> Optional[float]:
    """Time spent in rapid (G0) moves, measured from samples.

    Rapid moves are too short and too frequent to deserve their own events, so
    this is the one figure derived from samples. Gaps longer than three
    sampling intervals are skipped rather than assumed to be rapid.
    """
    try:
        samples = storage.samples_between(ts_from, ts_to, limit=200000)
    except Exception:  # noqa: BLE001 - a missing figure beats a crashed report
        return None
    if not samples:
        return None

    max_gap = cfg.sample_interval_s * 3
    total = 0.0
    previous = None
    for row in samples:
        if previous is not None:
            gap = row["ts"] - previous["ts"]
            if 0 < gap <= max_gap and previous.get("is_rapid"):
                total += gap
        previous = row
    return total


# ------------------------------------------------------------------ rendering


def render_text(summary: Dict[str, Any], title: str) -> str:
    """The report as the operator sees it in the terminal."""
    out = io.StringIO()
    start = datetime.fromtimestamp(summary["ts_from"]).strftime("%d.%m.%Y %H:%M")
    end = datetime.fromtimestamp(summary["ts_to"]).strftime("%d.%m.%Y %H:%M")

    out.write("=" * 68 + "\n")
    out.write(f" {title}\n")
    out.write(f" {summary['machine_name']} ({summary['machine_id']})\n")
    out.write(f" {start} — {end}\n")
    out.write("=" * 68 + "\n\n")

    avail = summary["availability"]
    avail_text = f"%{avail:.1f}" if avail is not None else "—"

    out.write(f"  Çalışma süresi      : {format_duration(summary['run_s'])}\n")
    out.write(f"  Duruş süresi        : {format_duration(summary['down_s'])}\n")
    out.write(f"  Kurulum/elle kumanda: {format_duration(summary['setup_s'])}\n")
    out.write(f"  Ölçülemeyen süre    : {format_duration(summary['offline_s'])}"
              "   (bağlantı yok — duruşa sayılmaz)\n")
    if summary["rapid_s"] is not None:
        out.write(f"  Hızlı hareket (G0)  : {format_duration(summary['rapid_s'])}\n")
    out.write(f"  Kullanılabilirlik   : {avail_text}"
              "   (çalışma / gözlenen süre)\n")
    out.write(f"  Duruş sayısı        : {summary['stop_count']}"
              f"   (eşik {summary['idle_threshold_s']:g} sn)\n")
    out.write(f"  Alarm sayısı        : {summary['alarm_count']}\n\n")

    if summary["programs"]:
        out.write("-" * 68 + "\n")
        out.write("  PROGRAM BAZLI SÜRELER\n")
        out.write("-" * 68 + "\n")
        out.write(f"  {'Program':<24} {'Çalışma':>12} {'Yüklü kalma':>14} {'Kez':>5}\n")
        for row in summary["programs"]:
            out.write(
                f"  {row['program_name'][:24]:<24} "
                f"{format_duration(row['run_s']):>12} "
                f"{format_duration(row['loaded_s']):>14} "
                f"{row['count']:>5}\n"
            )
        out.write("\n")

    if summary["stops"]:
        out.write("-" * 68 + "\n")
        out.write("  EN UZUN DURUŞLAR\n")
        out.write("-" * 68 + "\n")
        for row in summary["stops"][:10]:
            clock = datetime.fromtimestamp(row["ts_start"]).strftime("%H:%M:%S")
            reason = row.get("reason") or "—"
            flag = " (devam ediyor)" if row["open"] else ""
            out.write(
                f"  {clock}  {format_duration(row['duration_s']):>10}  "
                f"{row['state_label']:<12} {reason}{flag}\n"
            )
        out.write("\n")

    if summary["alarms"]:
        out.write("-" * 68 + "\n")
        out.write("  ALARMLAR\n")
        out.write("-" * 68 + "\n")
        for row in summary["alarms"]:
            clock = datetime.fromtimestamp(row["ts_start"]).strftime("%H:%M:%S")
            flag = " (aktif)" if row["open"] else ""
            out.write(
                f"  {clock}  {format_duration(row['duration_s']):>10}  "
                f"[{row['code']}] {row['text']}{flag}\n"
            )
        out.write("\n")

    return out.getvalue()


def render_csv(summary: Dict[str, Any]) -> str:
    """One CSV holding the summary, the stops, the alarms and the programs.

    Semicolon-separated and UTF-8 BOM prefixed so Excel in a Turkish locale
    opens it with the columns already split.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")

    def clock(ts: Optional[float]) -> str:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else ""

    writer.writerow(["ÖZET"])
    writer.writerow(["Makine", summary["machine_name"]])
    writer.writerow(["Makine kimliği", summary["machine_id"]])
    writer.writerow(["Başlangıç", clock(summary["ts_from"])])
    writer.writerow(["Bitiş", clock(summary["ts_to"])])
    writer.writerow(["Çalışma süresi (sn)", round(summary["run_s"])])
    writer.writerow(["Duruş süresi (sn)", round(summary["down_s"])])
    writer.writerow(["Kurulum süresi (sn)", round(summary["setup_s"])])
    writer.writerow(["Bağlantı yok (sn)", round(summary["offline_s"])])
    if summary["rapid_s"] is not None:
        writer.writerow(["Hızlı hareket (sn)", round(summary["rapid_s"])])
    writer.writerow(
        [
            "Kullanılabilirlik (%)",
            f"{summary['availability']:.1f}" if summary["availability"] else "",
        ]
    )
    writer.writerow(["Duruş sayısı", summary["stop_count"]])
    writer.writerow(["Alarm sayısı", summary["alarm_count"]])
    writer.writerow([])

    writer.writerow(["PROGRAMLAR"])
    writer.writerow(["Program", "Çalışma (sn)", "Yüklü kalma (sn)", "Kez"])
    for row in summary["programs"]:
        writer.writerow(
            [
                row["program_name"],
                round(row["run_s"]),
                round(row["loaded_s"]),
                row["count"],
            ]
        )
    writer.writerow([])

    writer.writerow(["DURUŞLAR"])
    writer.writerow(["Başlangıç", "Bitiş", "Süre (sn)", "Durum", "Sebep", "Program"])
    for row in sorted(summary["stops"], key=lambda s: s["ts_start"]):
        writer.writerow(
            [
                clock(row["ts_start"]),
                clock(row["ts_end"]),
                round(row["duration_s"]),
                row["state_label"],
                row.get("reason") or "",
                row.get("program_name") or "",
            ]
        )
    writer.writerow([])

    writer.writerow(["ALARMLAR"])
    writer.writerow(["Başlangıç", "Bitiş", "Süre (sn)", "Kod", "Mesaj"])
    for row in summary["alarms"]:
        writer.writerow(
            [
                clock(row["ts_start"]),
                clock(row["ts_end"]),
                round(row["duration_s"]),
                row.get("code") or "",
                row.get("text") or "",
            ]
        )

    return "﻿" + buffer.getvalue()
