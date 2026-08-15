"""End-to-end: driver -> collector -> storage -> text log -> report.

Runs a whole simulated shift deterministically (fake clock, fixed seed, ticks
driven by hand rather than by a thread) and then checks that what ended up on
disk is internally consistent. This is the test that would catch a break
between two components that each pass their own unit tests.
"""

import os
import shutil
import tempfile
import unittest

from cnclog.app import Application
from cnclog.config import Config, load_config
from cnclog.drivers.simulator import SimulatorDriver
from cnclog.lock import AlreadyRunning, InstanceLock
from cnclog.model import MachineState
from cnclog import report


class FakeClock:
    def __init__(self, start=1_700_000_000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class TestFullShift(unittest.TestCase):
    """One simulated eight-hour shift, then verify the recorded result."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="cnclog-int-")
        cls.clock = FakeClock()
        cfg = Config(
            machine_id="TEST-01",
            machine_name="Test Tezgah",
            data_dir=os.path.join(cls.tmp, "veri"),
            sample_interval_s=2.0,
            idle_threshold_s=10.0,
            log_interval_s=30.0,
            base_dir=cls.tmp,
        )
        driver = SimulatorDriver(cfg, speed=1.0, seed=7, clock=cls.clock)
        cls.app = Application(cfg, driver, writer=True)
        cls.cfg = cfg

        driver.connect()
        for _ in range(14400):  # 8 hours at 2 s
            cls.clock.advance(2)
            cls.app.collector.tick()
        cls.app.collector._finalize()
        cls.app.text_log.close()

    @classmethod
    def tearDownClass(cls):
        cls.app.storage.close()
        if cls.app.lock:
            cls.app.lock.release()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def summary(self):
        events = self.app.storage.recent_events(limit=10000)
        starts = [e["ts_start"] for e in events]
        return report.summarize(
            self.app.storage,
            self.cfg,
            min(starts) - 1,
            max(starts) + 100000,
            now=self.clock.now,
        )

    def test_samples_were_written(self):
        rows = self.app.storage.samples_between(0, self.clock.now + 1, limit=100000)
        self.assertGreater(len(rows), 14000)

    def test_no_event_left_open_after_finalize(self):
        open_rows = [
            e for e in self.app.storage.recent_events(limit=10000)
            if e["ts_end"] is None
        ]
        self.assertEqual(open_rows, [], "kapatılmamış kayıt kalmamalı")

    def test_no_two_state_events_overlap(self):
        """Overlapping state spans would double-count every duration."""
        spans = sorted(
            (e["ts_start"], e["ts_end"])
            for e in self.app.storage.recent_events(limit=10000)
            if e["type"] == "state"
        )
        for (_s1, e1), (s2, _e2) in zip(spans, spans[1:]):
            self.assertLessEqual(
                e1, s2 + 1e-6, "iki durum aralığı çakışıyor"
            )

    def test_state_events_are_contiguous(self):
        """No gaps either: every second of the shift belongs to some state."""
        spans = sorted(
            (e["ts_start"], e["ts_end"])
            for e in self.app.storage.recent_events(limit=10000)
            if e["type"] == "state"
        )
        for (_s1, e1), (s2, _e2) in zip(spans, spans[1:]):
            self.assertAlmostEqual(e1, s2, delta=1e-6, msg="durum aralıkları arasında boşluk var")

    def test_every_stop_event_meets_the_threshold(self):
        stops = [
            e for e in self.app.storage.recent_events(limit=10000)
            if e["type"] == "state" and e["state"] == "DURUS"
        ]
        self.assertGreater(len(stops), 0, "hiç duruş kaydedilmemiş")
        for stop in stops:
            self.assertGreaterEqual(
                stop["duration_s"], 10.0 - 2.0,  # allow one sampling interval
                "eşiğin altında bir duruş olay olarak kaydedilmiş",
            )

    def test_short_pauses_present_in_samples_but_not_events(self):
        rows = self.app.storage.samples_between(0, self.clock.now + 1, limit=100000)
        idle_samples = [r for r in rows if r["state"] == "DURUS"]
        stop_events = [
            e for e in self.app.storage.recent_events(limit=10000)
            if e["type"] == "state" and e["state"] == "DURUS"
        ]
        self.assertGreater(len(idle_samples), 0)
        # Far more idle samples than stop events: the short ones were kept in
        # the data but stayed out of the event list, which is the whole point.
        self.assertGreater(len(idle_samples), len(stop_events) * 2)

    def test_durations_add_up_to_the_shift(self):
        s = self.summary()
        total = s["run_s"] + s["down_s"] + s["setup_s"] + s["offline_s"]
        self.assertAlmostEqual(total, 8 * 3600, delta=60)

    def test_offline_excluded_from_availability(self):
        s = self.summary()
        self.assertGreater(s["offline_s"], 0, "simülatör kopma üretmedi")
        expected = s["run_s"] / (s["run_s"] + s["down_s"] + s["setup_s"]) * 100
        self.assertAlmostEqual(s["availability"], expected, places=6)

    def test_alarms_recorded(self):
        s = self.summary()
        self.assertGreater(s["alarm_count"], 0)
        for alarm in s["alarms"]:
            self.assertTrue(alarm["code"])
            self.assertTrue(alarm["text"])

    def test_program_run_time_never_exceeds_loaded_time(self):
        for row in self.summary()["programs"]:
            self.assertLessEqual(
                row["run_s"], row["loaded_s"] + 1e-6,
                f"{row['program_name']}: çalışma süresi yüklü kalma süresini aşamaz",
            )

    def test_text_log_written_and_chronological(self):
        log_files = sorted(os.listdir(self.cfg.log_dir))
        self.assertTrue(log_files)
        path = os.path.join(self.cfg.log_dir, log_files[0])
        with open(path, encoding="utf-8") as handle:
            clocks = [
                line[:8] for line in handle
                if not line.startswith("#") and len(line) > 8
            ]
        self.assertGreater(len(clocks), 50)
        self.assertEqual(clocks, sorted(clocks), "metin log kronolojik olmalı")

    def test_stop_lines_carry_a_reason(self):
        path = os.path.join(
            self.cfg.log_dir, sorted(os.listdir(self.cfg.log_dir))[0]
        )
        with open(path, encoding="utf-8") as handle:
            starts = [ln for ln in handle if "DURUŞ" in ln and "başladı" in ln]
        self.assertTrue(starts)
        for line in starts:
            self.assertIn("—", line, "duruş satırında sebep yok")


class TestInstanceLock(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cnclog-lock-")
        self.path = os.path.join(self.tmp, "veri", "cnclog.lock")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_second_acquire_is_refused(self):
        first = InstanceLock(self.path)
        first.acquire()
        try:
            with self.assertRaises(AlreadyRunning) as ctx:
                InstanceLock(self.path).acquire()
            self.assertIn("zaten çalışıyor", str(ctx.exception))
        finally:
            first.release()

    def test_lock_can_be_taken_again_after_release(self):
        first = InstanceLock(self.path)
        first.acquire()
        first.release()
        second = InstanceLock(self.path)
        second.acquire()  # must not raise
        second.release()


class TestCrashRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cnclog-crash-")
        self.clock = FakeClock()
        self.cfg = Config(
            machine_id="T1",
            data_dir=os.path.join(self.tmp, "veri"),
            idle_threshold_s=0,
            base_dir=self.tmp,
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_without_shutdown(self, ticks=100):
        driver = SimulatorDriver(self.cfg, speed=1.0, seed=3, clock=self.clock)
        app = Application(self.cfg, driver, writer=True)
        driver.connect()
        for _ in range(ticks):
            self.clock.advance(2)
            app.collector.tick()
        # Simulate a kill: close the files but never finalize the events.
        app.text_log.close()
        app.storage.close()
        app.lock.release()

    def test_open_events_are_closed_on_next_start(self):
        self._run_without_shutdown()

        # A crashed run leaves events with no end time.
        from cnclog.storage import SqliteStorage

        probe = SqliteStorage(self.cfg.db_path)
        dangling = [
            e for e in probe.recent_events(limit=100) if e["ts_end"] is None
        ]
        probe.close()
        self.assertGreater(len(dangling), 0, "çökme açık kayıt bırakmalıydı")

        # Restarting must repair them.
        driver = SimulatorDriver(self.cfg, speed=1.0, seed=3, clock=self.clock)
        app = Application(self.cfg, driver, writer=True)
        try:
            self.assertEqual(app.recovered_events, len(dangling))
            still_open = [
                e for e in app.storage.recent_events(limit=100) if e["ts_end"] is None
            ]
            self.assertEqual(still_open, [])
        finally:
            app.text_log.close()
            app.storage.close()
            app.lock.release()

    def test_recovery_does_not_invent_time(self):
        """Recovered events end at the last observed sample, not at 'now'."""
        self._run_without_shutdown(ticks=50)
        last_sample_ts = self.clock.now

        self.clock.advance(10 * 3600)  # ten hours pass before the restart

        driver = SimulatorDriver(self.cfg, speed=1.0, seed=3, clock=self.clock)
        app = Application(self.cfg, driver, writer=True)
        try:
            closed = [
                e for e in app.storage.recent_events(limit=100)
                if e["ts_end"] is not None
            ]
            for event in closed:
                self.assertLessEqual(
                    event["ts_end"], last_sample_ts + 1,
                    "kurtarma gözlenmemiş süre uydurmamalı",
                )
        finally:
            app.text_log.close()
            app.storage.close()
            app.lock.release()


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cnclog-cfg-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, text):
        path = os.path.join(self.tmp, "config.ini")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def test_defaults_without_a_file(self):
        cfg = load_config(os.path.join(self.tmp, "yok.ini"), base_dir=self.tmp)
        self.assertEqual(cfg.idle_threshold_s, 10.0)
        # Default is discovery, not the simulator: an unconfigured install
        # must go looking for the real machine, never invent data.
        self.assertEqual(cfg.driver, "auto")
        self.assertEqual(cfg.tnc_ip, "")
        self.assertTrue(cfg.auto_scan)
        self.assertEqual(cfg.web_bind, "127.0.0.1")
        self.assertIsNone(cfg.source_path)

    def test_values_are_read(self):
        path = self.write(
            "[toplama]\ndurus_esigi_sn = 30\n"
            "[surucu]\ntip = heidenhain_lsv2\ntnc_ip = 10.0.0.5\n"
        )
        cfg = load_config(path, base_dir=self.tmp)
        self.assertEqual(cfg.idle_threshold_s, 30.0)
        self.assertEqual(cfg.driver, "heidenhain_lsv2")
        self.assertEqual(cfg.tnc_ip, "10.0.0.5")

    def test_threshold_zero_is_kept_not_defaulted(self):
        """0 means "log every pause" and must not fall back to 10."""
        path = self.write("[toplama]\ndurus_esigi_sn = 0\n")
        self.assertEqual(load_config(path, base_dir=self.tmp).idle_threshold_s, 0.0)

    def test_garbage_value_falls_back_to_default(self):
        """A typo on the shop floor must not stop the logger."""
        path = self.write("[toplama]\ndurus_esigi_sn = abc\nornekleme_araligi_sn = ??\n")
        cfg = load_config(path, base_dir=self.tmp)
        self.assertEqual(cfg.idle_threshold_s, 10.0)
        self.assertEqual(cfg.sample_interval_s, 2.0)

    def test_comma_decimal_accepted(self):
        path = self.write("[toplama]\nornekleme_araligi_sn = 1,5\n")
        self.assertEqual(load_config(path, base_dir=self.tmp).sample_interval_s, 1.5)

    def test_turkish_boolean(self):
        for text, expected in (("evet", True), ("hayir", False), ("HAYIR", False)):
            path = self.write(f"[web]\ntarayici_ac = {text}\n")
            self.assertIs(load_config(path, base_dir=self.tmp).open_browser, expected)

    def test_sample_interval_has_a_floor(self):
        path = self.write("[toplama]\nornekleme_araligi_sn = 0\n")
        self.assertGreaterEqual(
            load_config(path, base_dir=self.tmp).sample_interval_s, 0.2
        )

    def test_malformed_file_does_not_crash(self):
        path = self.write("bu geçerli bir ini dosyası değil {{{")
        cfg = load_config(path, base_dir=self.tmp)
        self.assertEqual(cfg.idle_threshold_s, 10.0)

    def test_data_paths_resolve_under_base_dir(self):
        cfg = load_config(None, base_dir=self.tmp)
        self.assertTrue(cfg.db_path.startswith(self.tmp))
        self.assertTrue(cfg.log_dir.startswith(self.tmp))


if __name__ == "__main__":
    unittest.main()
