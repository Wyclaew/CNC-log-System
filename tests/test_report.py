"""Report arithmetic: window clipping, availability, per-program totals.

Uses a real SQLite database in a temp dir rather than a fake, because the
clipping logic and the SQL overlap query have to agree with each other.
"""

import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta

from cnclog import report
from cnclog.config import Config
from cnclog.model import Event, EventType, MachineState, Sample
from cnclog.storage import SqliteStorage


def ts(hour, minute=0, second=0, day_offset=0):
    """A timestamp on a fixed day, so tests do not depend on when they run."""
    base = datetime(2026, 3, 10) + timedelta(days=day_offset)
    return base.replace(hour=hour, minute=minute, second=second).timestamp()


DAY_START = datetime(2026, 3, 10).timestamp()
DAY_END = (datetime(2026, 3, 10) + timedelta(days=1)).timestamp()


class ReportTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cnclog-test-")
        self.storage = SqliteStorage(os.path.join(self.tmp, "test.db"))
        self.cfg = Config(machine_id="T1", sample_interval_s=2.0)

    def tearDown(self):
        self.storage.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add_state(self, state, start, end, text=None, program=None):
        event = Event(
            machine_id="T1",
            type=EventType.STATE,
            ts_start=start,
            ts_end=end,
            state=state,
            text=text,
            program_name=program,
        )
        self.storage.insert_event(event)
        return event

    def add_program(self, name, start, end):
        self.storage.insert_event(
            Event(
                machine_id="T1",
                type=EventType.PROGRAM,
                ts_start=start,
                ts_end=end,
                program_name=name,
            )
        )

    def summarize(self, now=None):
        return report.summarize(
            self.storage, self.cfg, DAY_START, DAY_END, now=now or ts(23, 59)
        )


class TestDurations(ReportTestCase):
    def test_basic_totals(self):
        self.add_state(MachineState.CALISIYOR, ts(8), ts(10))      # 2 h
        self.add_state(MachineState.DURUS, ts(10), ts(10, 30))     # 30 min
        self.add_state(MachineState.KURULUM, ts(10, 30), ts(11))   # 30 min

        s = self.summarize()
        self.assertEqual(s["run_s"], 7200)
        self.assertEqual(s["down_s"], 1800)
        self.assertEqual(s["setup_s"], 1800)

    def test_alarm_counts_as_downtime(self):
        self.add_state(MachineState.CALISIYOR, ts(8), ts(9))
        self.add_state(MachineState.ALARM, ts(9), ts(9, 15))
        s = self.summarize()
        self.assertEqual(s["down_s"], 900)

    def test_offline_is_not_downtime(self):
        """The whole point: unobserved time must not look like machine downtime."""
        self.add_state(MachineState.CALISIYOR, ts(8), ts(9))
        self.add_state(MachineState.BAGLANTI_YOK, ts(9), ts(17))  # 8 h offline

        s = self.summarize()
        self.assertEqual(s["down_s"], 0)
        self.assertEqual(s["offline_s"], 8 * 3600)
        # Availability must ignore the offline stretch entirely.
        self.assertEqual(s["availability"], 100.0)

    def test_availability_formula(self):
        self.add_state(MachineState.CALISIYOR, ts(8), ts(11))  # 3 h
        self.add_state(MachineState.DURUS, ts(11), ts(12))     # 1 h
        s = self.summarize()
        self.assertAlmostEqual(s["availability"], 75.0)

    def test_availability_is_none_without_observations(self):
        self.assertIsNone(self.summarize()["availability"])


class TestWindowClipping(ReportTestCase):
    def test_event_crossing_midnight_is_split(self):
        """A stop from 23:00 to 01:00 belongs half to each day, not twice."""
        start = ts(23)
        end = ts(1, day_offset=1)
        self.add_state(MachineState.DURUS, start, end)

        today = report.summarize(
            self.storage, self.cfg, DAY_START, DAY_END, now=ts(12, day_offset=1)
        )
        self.assertEqual(today["down_s"], 3600, "sadece gece yarısına kadarki kısım")

        next_start = DAY_END
        next_end = DAY_END + 86400
        tomorrow = report.summarize(
            self.storage, self.cfg, next_start, next_end, now=ts(12, day_offset=1)
        )
        self.assertEqual(tomorrow["down_s"], 3600, "kalan kısım ertesi güne")

    def test_open_event_clipped_at_now(self):
        self.add_state(MachineState.CALISIYOR, ts(8), None)
        s = self.summarize(now=ts(10))
        self.assertEqual(s["run_s"], 7200)

    def test_open_event_clipped_at_window_end_when_now_is_later(self):
        self.add_state(MachineState.CALISIYOR, ts(22), None)
        s = report.summarize(
            self.storage, self.cfg, DAY_START, DAY_END, now=ts(12, day_offset=1)
        )
        self.assertEqual(s["run_s"], 2 * 3600, "gün sonunda kesilmeli")

    def test_driver_clock_ahead_of_wall_clock(self):
        """--sim-hiz makes data timestamps run ahead; the report must cope."""
        self.add_state(MachineState.CALISIYOR, ts(8), ts(10))
        # `now` deliberately earlier than the data.
        s = report.summarize(self.storage, self.cfg, DAY_START, DAY_END, now=ts(1))
        self.assertEqual(s["run_s"], 7200)


class TestStopsAndAlarms(ReportTestCase):
    def test_stops_sorted_longest_first(self):
        self.add_state(MachineState.DURUS, ts(8), ts(8, 1), text="kısa")
        self.add_state(MachineState.DURUS, ts(9), ts(9, 30), text="uzun")
        self.add_state(MachineState.DURUS, ts(10), ts(10, 5), text="orta")

        s = self.summarize()
        self.assertEqual(s["stop_count"], 3)
        self.assertEqual([r["reason"] for r in s["stops"]], ["uzun", "orta", "kısa"])

    def test_alarm_rows_collected(self):
        self.storage.insert_event(
            Event(
                machine_id="T1",
                type=EventType.ALARM,
                ts_start=ts(9),
                ts_end=ts(9, 2),
                code="1024",
                text="Soğutma basıncı",
            )
        )
        s = self.summarize()
        self.assertEqual(s["alarm_count"], 1)
        self.assertEqual(s["alarms"][0]["code"], "1024")
        self.assertEqual(s["alarms"][0]["duration_s"], 120)


class TestProgramTotals(ReportTestCase):
    def test_run_time_is_intersection_not_loaded_time(self):
        """'Programı kaç dakika çalıştırdık' != 'program kaç dakika yüklüydü'."""
        self.add_program("PARCA_12.H", ts(8), ts(10))          # loaded 2 h
        self.add_state(MachineState.CALISIYOR, ts(8), ts(9))   # ran 1 h
        self.add_state(MachineState.DURUS, ts(9), ts(10))      # idle 1 h

        s = self.summarize()
        row = next(r for r in s["programs"] if r["program_name"] == "PARCA_12.H")
        self.assertEqual(row["loaded_s"], 7200)
        self.assertEqual(row["run_s"], 3600)

    def test_repeated_program_accumulates(self):
        self.add_program("A.H", ts(8), ts(9))
        self.add_program("A.H", ts(11), ts(12))
        self.add_state(MachineState.CALISIYOR, ts(8), ts(9))
        self.add_state(MachineState.CALISIYOR, ts(11), ts(12))

        s = self.summarize()
        row = next(r for r in s["programs"] if r["program_name"] == "A.H")
        self.assertEqual(row["count"], 2)
        self.assertEqual(row["run_s"], 7200)

    def test_programs_sorted_by_run_time(self):
        self.add_program("KISA.H", ts(8), ts(8, 30))
        self.add_program("UZUN.H", ts(9), ts(12))
        self.add_state(MachineState.CALISIYOR, ts(8), ts(12))
        s = self.summarize()
        self.assertEqual(s["programs"][0]["program_name"], "UZUN.H")


class TestRapidSeconds(ReportTestCase):
    def _sample(self, at, rapid):
        self.storage.write_sample(
            Sample(
                ts=at,
                machine_id="T1",
                state=MachineState.CALISIYOR,
                is_rapid=rapid,
            )
        )

    def test_rapid_time_summed_from_samples(self):
        for i in range(10):
            self._sample(ts(8) + i * 2, rapid=(i < 5))
        s = self.summarize()
        # Five rapid samples, each followed 2 s later by the next reading.
        self.assertEqual(s["rapid_s"], 10.0)

    def test_gaps_are_not_counted(self):
        """A restart leaves a gap; it must not be attributed to rapid motion."""
        self._sample(ts(8), rapid=True)
        self._sample(ts(9), rapid=True)  # one-hour gap
        s = self.summarize()
        self.assertEqual(s["rapid_s"], 0.0)


class TestShiftBounds(unittest.TestCase):
    def test_three_shifts_cover_the_day(self):
        cfg = Config(shift_starts=["08:00", "16:00", "00:00"])
        shifts = report.shift_bounds(cfg, datetime(2026, 3, 10).date())
        total = sum(end - begin for _label, begin, end in shifts)
        self.assertEqual(total, 86400)

    def test_single_shift_still_covers_the_day(self):
        cfg = Config(shift_starts=["08:00"])
        shifts = report.shift_bounds(cfg, datetime(2026, 3, 10).date())
        total = sum(end - begin for _label, begin, end in shifts)
        self.assertEqual(total, 86400)

    def test_garbage_shift_times_are_ignored(self):
        cfg = Config(shift_starts=["saçma", "08:00"])
        shifts = report.shift_bounds(cfg, datetime(2026, 3, 10).date())
        self.assertEqual(sum(e - b for _l, b, e in shifts), 86400)


class TestParseDay(unittest.TestCase):
    def test_keywords(self):
        from datetime import date

        self.assertEqual(report.parse_day("bugun"), date.today())
        self.assertEqual(report.parse_day(""), date.today())
        self.assertEqual(report.parse_day("dün"), date.today() - timedelta(days=1))

    def test_iso_date(self):
        self.assertEqual(report.parse_day("2026-03-10"), datetime(2026, 3, 10).date())

    def test_bad_date_raises(self):
        with self.assertRaises(ValueError):
            report.parse_day("10.03.2026")


class TestCsvRendering(ReportTestCase):
    def test_csv_has_bom_and_sections(self):
        self.add_state(MachineState.CALISIYOR, ts(8), ts(9))
        self.add_state(MachineState.DURUS, ts(9), ts(9, 30), text="parça değişimi")
        csv_text = report.render_csv(self.summarize())

        self.assertTrue(csv_text.startswith("﻿"), "Excel için BOM gerekli")
        for section in ("ÖZET", "PROGRAMLAR", "DURUŞLAR", "ALARMLAR"):
            self.assertIn(section, csv_text)
        self.assertIn("parça değişimi", csv_text)


if __name__ == "__main__":
    unittest.main()
