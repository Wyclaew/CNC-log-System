"""Text log format and the web parser must agree.

TextLog writes fixed columns and web/api.py reads them back by position. That
is a contract between two files, and the widest label ("BAĞLANTI YOK", 12
characters, containing a space) is exactly the case that breaks a naive
whitespace split. Every label is round-tripped here so a future formatting
tweak cannot silently blank out the log view.
"""

import os
import shutil
import tempfile
import unittest
from datetime import datetime

from cnclog.model import (
    STATE_LABELS,
    Alarm,
    Event,
    EventType,
    MachineState,
    Sample,
    format_duration,
)
from cnclog.storage import TextLog
from cnclog.web.api import _parse_line, _row_kind

TS = datetime(2026, 3, 10, 14, 32, 7).timestamp()


class TestRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cnclog-log-")
        self.log = TextLog(self.tmp, "Test Tezgah")

    def tearDown(self):
        self.log.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def lines(self):
        path = os.path.join(self.tmp, "2026-03-10.log")
        with open(path, encoding="utf-8") as handle:
            return [ln.rstrip("\n") for ln in handle if not ln.startswith("#")]

    def test_every_state_label_round_trips(self):
        for state, label in STATE_LABELS.items():
            with self.subTest(state=state):
                self.log.write(TS, label, "test mesajı")
        for line, (state, label) in zip(self.lines(), STATE_LABELS.items()):
            parsed = _parse_line(line)
            self.assertIsNotNone(parsed, f"'{label}' satırı ayrıştırılamadı")
            clock, tag, message = parsed
            self.assertEqual(clock, "14:32:07")
            self.assertEqual(tag, label, f"'{label}' etiketi bozuldu")
            self.assertEqual(message, "test mesajı")

    def test_widest_label_survives(self):
        """BAĞLANTI YOK contains a space and fills the column exactly."""
        label = STATE_LABELS[MachineState.BAGLANTI_YOK]
        self.assertEqual(len(label), 12, "sütun genişliği bu uzunluğa göre seçildi")
        self.log.write(TS, label, "kopma sebebi")
        clock, tag, message = _parse_line(self.lines()[0])
        self.assertEqual(tag, "BAĞLANTI YOK")
        self.assertEqual(message, "kopma sebebi")

    def test_event_tags_round_trip(self):
        for tag in ("ALARM", "PROGRAM", "BAKIM", "HATA"):
            self.log.write(TS, tag, "içerik")
        for line, tag in zip(self.lines(), ("ALARM", "PROGRAM", "BAKIM", "HATA")):
            self.assertEqual(_parse_line(line)[1], tag)

    def test_status_line_round_trips(self):
        sample = Sample(
            ts=TS,
            machine_id="T1",
            state=MachineState.CALISIYOR,
            feed_actual=1250.0,
            spindle_actual=8000.0,
            program_name="PARCA_12.H",
            block_number=1240,
            tool_number=12,
        )
        self.log.write_status(TS, MachineState.CALISIYOR, sample)
        clock, tag, message = _parse_line(self.lines()[0])
        self.assertEqual(tag, "ÇALIŞIYOR")
        self.assertIn("F=", message)
        self.assertIn("1250", message)
        self.assertIn("PARCA_12.H", message)
        self.assertIn("N=1240", message)
        self.assertIn("T=12", message)

    def test_unreadable_values_render_as_dash(self):
        """LSV2 cannot read F and S; they must not print as a misleading 0."""
        sample = Sample(
            ts=TS,
            machine_id="T1",
            state=MachineState.CALISIYOR,
            feed_actual=None,
            spindle_actual=None,
        )
        self.log.write_status(TS, MachineState.CALISIYOR, sample)
        message = _parse_line(self.lines()[0])[2]
        self.assertIn("F=", message)
        self.assertIn("—", message)
        self.assertNotIn("F=     0", message)

    def test_header_lines_are_skipped_by_parser(self):
        self.log.write(TS, "ALARM", "x")
        path = os.path.join(self.tmp, "2026-03-10.log")
        with open(path, encoding="utf-8") as handle:
            header = [ln for ln in handle if ln.startswith("#")]
        self.assertTrue(header)
        for line in header:
            self.assertIsNone(_parse_line(line.rstrip("\n")))


class TestChronology(unittest.TestCase):
    """A backdated stop must not rewind the clock inside the file."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cnclog-log-")
        self.log = TextLog(self.tmp, "Test")

    def tearDown(self):
        self.log.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def read(self):
        path = os.path.join(self.tmp, "2026-03-10.log")
        with open(path, encoding="utf-8") as handle:
            return [ln.rstrip("\n") for ln in handle if not ln.startswith("#")]

    def test_timestamps_never_go_backwards(self):
        self.log.write(TS + 30, "ÇALIŞIYOR", "sonraki")
        self.log.write(TS, "DURUŞ", "geriye tarihli")  # earlier than the last line

        clocks = [_parse_line(line)[0] for line in self.read()]
        self.assertEqual(clocks, sorted(clocks), "log kronolojik kalmalı")

    def test_backdated_line_states_its_real_time(self):
        self.log.write(TS + 30, "ÇALIŞIYOR", "sonraki")
        self.log.write(TS, "DURUŞ", "başladı")
        message = _parse_line(self.read()[1])[2]
        self.assertIn("gerçek zaman 14:32:07", message)


class TestEventRendering(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cnclog-log-")
        self.log = TextLog(self.tmp, "Test")

    def tearDown(self):
        self.log.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def read(self):
        path = os.path.join(self.tmp, "2026-03-10.log")
        with open(path, encoding="utf-8") as handle:
            return [ln.rstrip("\n") for ln in handle if not ln.startswith("#")]

    def test_stop_event_shows_reason_then_duration(self):
        event = Event(
            machine_id="T1",
            type=EventType.STATE,
            ts_start=TS,
            state=MachineState.DURUS,
            text="program yüklü değil (parça değişimi veya bekleme)",
        )
        self.log.write_event(event)
        event.ts_end = TS + 209  # 3dk 29sn
        self.log.write_event(event)

        first, second = (_parse_line(line)[2] for line in self.read())
        self.assertIn("başladı", first)
        self.assertIn("parça değişimi", first)
        self.assertIn("bitti", second)
        self.assertIn(format_duration(209), second)

    def test_alarm_event_shows_code_and_text(self):
        event = Event(
            machine_id="T1",
            type=EventType.ALARM,
            ts_start=TS,
            code="2.15.1024",
            text="Soğutma sıvısı basıncı düşük",
        )
        self.log.write_event(event)
        clock, tag, message = _parse_line(self.read()[0])
        self.assertEqual(tag, "ALARM")
        self.assertIn("[2.15.1024]", message)
        self.assertIn("Soğutma", message)


class TestRowKind(unittest.TestCase):
    """The UI filter depends on this classification."""

    def test_alarm_and_program(self):
        self.assertEqual(_row_kind("ALARM", "[1] x"), "alarm")
        self.assertEqual(_row_kind("PROGRAM", "A.H yüklendi"), "program")

    def test_stop_labels_classified_as_durus(self):
        self.assertEqual(_row_kind("DURUŞ", "başladı"), "durus")
        self.assertEqual(_row_kind("BAĞLANTI YOK", "başladı"), "durus")

    def test_measurement_vs_event(self):
        self.assertEqual(_row_kind("ÇALIŞIYOR", "F=1250 mm/dk  S=8000"), "olcum")
        self.assertEqual(_row_kind("ÇALIŞIYOR", "başladı"), "olay")
        self.assertEqual(_row_kind("ÇALIŞIYOR", "bitti — süre 5dk"), "olay")

    def test_system_lines(self):
        self.assertEqual(_row_kind("BAKIM", "eski ölçüm silindi"), "sistem")
        self.assertEqual(_row_kind("HATA", "İç hata"), "sistem")


class TestDailyRotation(unittest.TestCase):
    def test_new_file_per_day(self):
        tmp = tempfile.mkdtemp(prefix="cnclog-log-")
        try:
            log = TextLog(tmp, "Test")
            log.write(TS, "ALARM", "gün 1")
            log.write(TS + 86400, "ALARM", "gün 2")
            log.close()
            files = sorted(f for f in os.listdir(tmp) if f.endswith(".log"))
            self.assertEqual(files, ["2026-03-10.log", "2026-03-11.log"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
