"""Local persistence: SQLite for queries, a daily text file for humans.

Nothing here talks to the network and nothing leaves the machine.

SQLite is the primary backend. If the Python build on the shop PC was compiled
without the sqlite3 module -- rare but it happens on stripped-down systems --
JsonlStorage takes over so data is still captured rather than lost.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .model import (
    Event,
    EventType,
    MachineState,
    STATE_LABELS,
    Sample,
    format_duration,
    format_number,
)

try:  # pragma: no cover - exercised only on builds without sqlite3
    import sqlite3

    HAVE_SQLITE = True
except ImportError:  # pragma: no cover
    sqlite3 = None  # type: ignore[assignment]
    HAVE_SQLITE = False


SCHEMA_VERSION = "1"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS samples (
    ts              REAL NOT NULL,
    machine_id      TEXT NOT NULL,
    state           TEXT NOT NULL,
    feed_actual     REAL,
    spindle_actual  REAL,
    feed_override   REAL,
    spindle_override REAL,
    rapid_override  REAL,
    is_rapid        INTEGER,
    program_name    TEXT,
    block_number    INTEGER,
    tool_number     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts);
CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id   TEXT NOT NULL,
    type         TEXT NOT NULL,
    ts_start     REAL NOT NULL,
    ts_end       REAL,
    duration_s   REAL,
    state        TEXT,
    code         TEXT,
    text         TEXT,
    program_name TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(ts_start);
CREATE INDEX IF NOT EXISTS idx_events_type  ON events(type);
"""


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


# --------------------------------------------------------------------- SQLite


class SqliteStorage:
    """Thread-safe SQLite writer/reader.

    One connection guarded by a lock: the collector writes on its own thread
    while the web layer reads on request threads. WAL mode keeps readers from
    blocking the writer.
    """

    backend = "sqlite"

    def __init__(self, db_path: str) -> None:
        ensure_dir(os.path.dirname(db_path) or ".")
        self.db_path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=NORMAL")
            except sqlite3.DatabaseError:
                # Some filesystems (a few network mounts) refuse WAL. The
                # default rollback journal is slower but perfectly correct.
                pass
            self._conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                ("schema_version", SCHEMA_VERSION),
            )
            self._conn.commit()

    def write_sample(self, sample: Sample) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO samples (ts, machine_id, state, feed_actual, "
                "spindle_actual, feed_override, spindle_override, rapid_override, "
                "is_rapid, program_name, block_number, tool_number) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                sample.to_row(),
            )
            self._conn.commit()

    def insert_event(self, event: Event) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO events (machine_id, type, ts_start, ts_end, duration_s, "
                "state, code, text, program_name) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    event.machine_id,
                    event.type.value,
                    event.ts_start,
                    event.ts_end,
                    event.duration_s,
                    event.state.value if event.state else None,
                    event.code,
                    event.text,
                    event.program_name,
                ),
            )
            self._conn.commit()
            event.id = int(cur.lastrowid)
            return event.id

    def close_event(self, event: Event) -> None:
        if event.id is None:
            self.insert_event(event)
            return
        with self._lock:
            self._conn.execute(
                "UPDATE events SET ts_end = ?, duration_s = ? WHERE id = ?",
                (event.ts_end, event.duration_s, event.id),
            )
            self._conn.commit()

    def events_between(
        self,
        ts_from: float,
        ts_to: float,
        types: Optional[List[str]] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Every event overlapping the window, oldest first."""
        sql = (
            "SELECT * FROM events WHERE ts_start < ? "
            "AND (ts_end IS NULL OR ts_end > ?)"
        )
        params: List[Any] = [ts_to, ts_from]
        if types:
            sql += " AND type IN (%s)" % ",".join("?" for _ in types)
            params.extend(types)
        sql += " ORDER BY ts_start ASC"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def recent_events(
        self, limit: int = 200, types: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM events"
        params: List[Any] = []
        if types:
            sql += " WHERE type IN (%s)" % ",".join("?" for _ in types)
            params.extend(types)
        sql += " ORDER BY ts_start DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def latest_sample(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM samples ORDER BY ts DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def samples_between(
        self, ts_from: float, ts_to: float, limit: int = 5000
    ) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM samples WHERE ts >= ? AND ts < ? "
                "ORDER BY ts ASC LIMIT ?",
                (ts_from, ts_to, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def close_dangling_events(self) -> int:
        """Close events a crashed run left open. Returns how many were closed.

        Safe only while holding the instance lock -- otherwise this would close
        the events a *running* collector legitimately has open. The end time is
        the last sample ever written, which is the last moment we can prove the
        collector was alive; guessing "now" instead would invent hours of
        machine state that was never observed.
        """
        with self._lock:
            row = self._conn.execute("SELECT MAX(ts) FROM samples").fetchone()
            last_seen = row[0] if row and row[0] is not None else None
            open_rows = self._conn.execute(
                "SELECT id, ts_start FROM events WHERE ts_end IS NULL"
            ).fetchall()
            if not open_rows:
                return 0
            for open_row in open_rows:
                start = open_row["ts_start"]
                end = last_seen if last_seen is not None and last_seen > start else start
                self._conn.execute(
                    "UPDATE events SET ts_end = ?, duration_s = ? WHERE id = ?",
                    (end, max(0.0, end - start), open_row["id"]),
                )
            self._conn.commit()
            return len(open_rows)

    def prune(self, retention_days: int) -> int:
        """Drop samples older than the retention window. Events are kept forever."""
        if retention_days <= 0:
            return 0
        cutoff = (datetime.now() - timedelta(days=retention_days)).timestamp()
        with self._lock:
            cur = self._conn.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
            self._conn.commit()
            return cur.rowcount or 0

    def close(self) -> None:
        with self._lock:
            self._conn.commit()
            self._conn.close()


# ---------------------------------------------------------------------- JSONL


class JsonlStorage:
    """Fallback used only when the Python build has no sqlite3 module.

    Events are mirrored in memory so the UI can still query them; samples are
    append-only on disk. Good enough to keep a shift's data safe until a proper
    Python is available.
    """

    backend = "jsonl"

    def __init__(self, data_dir: str) -> None:
        ensure_dir(data_dir)
        self.data_dir = data_dir
        self.samples_path = os.path.join(data_dir, "samples.jsonl")
        self.events_path = os.path.join(data_dir, "events.jsonl")
        self._lock = threading.RLock()
        self._events: List[Dict[str, Any]] = []
        self._next_id = 1
        self._latest: Optional[Dict[str, Any]] = None
        self._load_events()

    def _load_events(self) -> None:
        if not os.path.isfile(self.events_path):
            return
        try:
            with open(self.events_path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    # Later lines for the same id supersede earlier ones.
                    self._events = [e for e in self._events if e["id"] != row.get("id")]
                    self._events.append(row)
                    self._next_id = max(self._next_id, int(row.get("id", 0)) + 1)
        except OSError:
            pass

    def _append(self, path: str, payload: Dict[str, Any]) -> None:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def write_sample(self, sample: Sample) -> None:
        row = {
            "ts": sample.ts,
            "machine_id": sample.machine_id,
            "state": sample.state.value,
            "feed_actual": sample.feed_actual,
            "spindle_actual": sample.spindle_actual,
            "feed_override": sample.feed_override,
            "spindle_override": sample.spindle_override,
            "rapid_override": sample.rapid_override,
            "is_rapid": sample.is_rapid,
            "program_name": sample.program_name,
            "block_number": sample.block_number,
            "tool_number": sample.tool_number,
        }
        with self._lock:
            self._latest = row
            self._append(self.samples_path, row)

    def insert_event(self, event: Event) -> int:
        with self._lock:
            event.id = self._next_id
            self._next_id += 1
            row = event.to_dict()
            self._events.append(row)
            self._append(self.events_path, row)
            return event.id

    def close_event(self, event: Event) -> None:
        if event.id is None:
            self.insert_event(event)
            return
        with self._lock:
            row = event.to_dict()
            self._events = [e for e in self._events if e["id"] != event.id]
            self._events.append(row)
            self._append(self.events_path, row)

    def events_between(
        self,
        ts_from: float,
        ts_to: float,
        types: Optional[List[str]] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            rows = [
                e
                for e in self._events
                if e["ts_start"] < ts_to
                and (e.get("ts_end") is None or e["ts_end"] > ts_from)
                and (not types or e["type"] in types)
            ]
        rows.sort(key=lambda e: e["ts_start"])
        return rows[:limit] if limit else rows

    def recent_events(
        self, limit: int = 200, types: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        with self._lock:
            rows = [e for e in self._events if not types or e["type"] in types]
        rows.sort(key=lambda e: e["ts_start"], reverse=True)
        return rows[:limit]

    def latest_sample(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self._latest) if self._latest else None

    def samples_between(
        self, ts_from: float, ts_to: float, limit: int = 5000
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        if not os.path.isfile(self.samples_path):
            return rows
        try:
            with open(self.samples_path, "r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if ts_from <= row.get("ts", 0) < ts_to:
                        rows.append(row)
                        if len(rows) >= limit:
                            break
        except OSError:
            pass
        return rows

    def close_dangling_events(self) -> int:
        """See SqliteStorage.close_dangling_events."""
        with self._lock:
            last_seen = self._latest.get("ts") if self._latest else None
            open_rows = [e for e in self._events if e.get("ts_end") is None]
            for row in open_rows:
                start = row["ts_start"]
                end = last_seen if last_seen is not None and last_seen > start else start
                row["ts_end"] = end
                row["duration_s"] = max(0.0, end - start)
                self._append(self.events_path, row)
            return len(open_rows)

    def prune(self, retention_days: int) -> int:
        return 0  # Not supported on the fallback backend.

    def close(self) -> None:
        return None


def open_storage(db_path: str, data_dir: str):
    """Pick the best backend this Python build can support."""
    if HAVE_SQLITE:
        return SqliteStorage(db_path)
    return JsonlStorage(data_dir)


# ------------------------------------------------------------------- text log


class TextLog:
    """The daily human-readable log: timestamps on the left, event on the right.

    This is the file the operator opens, so it is written for reading, not for
    parsing. Values the control did not expose print as an em dash rather than
    a misleading zero.
    """

    HEADER = (
        "# CNC Log System — {machine}\n"
        "# Tarih: {date}\n"
        "# Saat      Durum         Veriler\n"
        "# " + "-" * 72 + "\n"
    )

    def __init__(self, log_dir: str, machine_name: str) -> None:
        ensure_dir(log_dir)
        self.log_dir = log_dir
        self.machine_name = machine_name
        self._lock = threading.Lock()
        self._current_date: Optional[str] = None
        self._handle = None
        self._last_ts = 0.0

    def _handle_for(self, ts: float):
        date_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        if date_str != self._current_date:
            if self._handle is not None:
                self._handle.close()
            path = os.path.join(self.log_dir, f"{date_str}.log")
            is_new = not os.path.isfile(path) or os.path.getsize(path) == 0
            self._handle = open(path, "a", encoding="utf-8")
            if is_new:
                self._handle.write(
                    self.HEADER.format(machine=self.machine_name, date=date_str)
                )
            self._current_date = date_str
        return self._handle

    def write(self, ts: float, tag: str, message: str) -> None:
        """Append one line, keeping the file strictly chronological.

        A confirmed stop is backdated to when it really began, which can be
        earlier than lines already written. Rewinding the clock inside the file
        would make it unreadable, so such a line is placed at the current
        position and the real timestamp is stated in the text instead.
        """
        with self._lock:
            if ts < self._last_ts:
                real = datetime.fromtimestamp(ts).strftime("%H:%M:%S")
                message = f"{message}  [gerçek zaman {real}]"
                ts = self._last_ts
            handle = self._handle_for(ts)
            self._last_ts = ts
            clock = datetime.fromtimestamp(ts).strftime("%H:%M:%S")
            # Fixed columns: clock [0:8], tag [10:22], message from [22].
            # The web UI parses by position, so the widest label
            # ("BAĞLANTI YOK", 12 chars) has to fit without pushing the
            # message column around.
            handle.write(f"{clock}  {tag:<12}  {message}\n")
            handle.flush()

    # -- convenience writers -------------------------------------------------

    def write_status(self, ts: float, state: MachineState, sample: Sample) -> None:
        parts = [
            f"F={format_number(sample.feed_actual):>6} mm/dk",
            f"S={format_number(sample.spindle_actual):>6} dev/dk",
        ]
        if sample.program_name:
            parts.append(f"Prog={sample.program_name}")
        if sample.block_number is not None:
            parts.append(f"N={sample.block_number}")
        if sample.tool_number is not None:
            parts.append(f"T={sample.tool_number}")
        if sample.is_rapid:
            parts.append("(hızlı hareket)")
        self.write(ts, STATE_LABELS[state], "  ".join(parts))

    def write_event(self, event: Event) -> None:
        """Render an event as one line, both when it opens and when it closes."""
        if event.type is EventType.ALARM:
            if event.is_open:
                self.write(event.ts_start, "ALARM", f"[{event.code}] {event.text}")
            else:
                self.write(
                    event.ts_end,
                    "ALARM",
                    f"giderildi — [{event.code}] (süre {format_duration(event.duration_s)})",
                )
            return

        if event.type is EventType.PROGRAM:
            if event.is_open:
                self.write(event.ts_start, "PROGRAM", f"{event.program_name} yüklendi")
            else:
                self.write(
                    event.ts_end,
                    "PROGRAM",
                    f"{event.program_name} bırakıldı — yüklü kalma süresi "
                    f"{format_duration(event.duration_s)}",
                )
            return

        if event.type is EventType.STATE and event.state is not None:
            label = STATE_LABELS[event.state]
            if event.is_open:
                reason = f" — {event.text}" if event.text else ""
                self.write(event.ts_start, label, f"başladı{reason}")
            else:
                self.write(
                    event.ts_end,
                    label,
                    f"bitti — süre {format_duration(event.duration_s)}",
                )

    def close(self) -> None:
        with self._lock:
            if self._handle is not None:
                self._handle.close()
                self._handle = None
                self._current_date = None
