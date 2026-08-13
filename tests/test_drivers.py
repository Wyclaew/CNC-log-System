"""Driver behaviour.

The LSV2 driver cannot be tested against a real TNC 640 here, so a fake client
stands in for the pyLSV2 connection while the *real* pyLSV2 enums are used for
the state mapping. That way the part most likely to be wrong -- translating
Heidenhain's two separate notions of "mode" and "program state" into one -- is
checked against the actual enum values the library will hand us.
"""

import unittest

from cnclog.config import Config
from cnclog.drivers.base import Driver, DriverError
from cnclog.drivers.heidenhain_lsv2 import (
    HeidenhainLsv2Driver,
    _basename,
    _load_pylsv2,
    _to_alarm,
)
from cnclog.drivers.registry import available_drivers, create_driver
from cnclog.drivers.simulator import SimulatorDriver
from cnclog.model import ExecState, MachineState
from cnclog.state import StateTracker

PYLSV2 = _load_pylsv2()


class FakeOverride:
    def __init__(self, feed=100.0, spindle=100.0, rapid=100.0):
        self.feed, self.spindle, self.rapid = feed, spindle, rapid


class FakeStack:
    def __init__(self, current=None, main=None, line_no=-1):
        self.current, self.main, self.line_no = current, main, line_no


class FakeTool:
    def __init__(self, number=-1):
        self.number = number


class FakeMessage:
    def __init__(self, e_class=2, e_group=15, e_number=1024, e_text="Test hatası"):
        self.e_class, self.e_group = e_class, e_group
        self.e_number, self.e_text = e_number, e_text
        self.dnc = True


class FakeClient:
    """Stands in for pyLSV2.LSV2. Read-only, like the real usage."""

    def __init__(self, **responses):
        self.responses = responses
        self.calls = []

    def _get(self, name, default=None):
        self.calls.append(name)
        value = self.responses.get(name, default)
        if isinstance(value, Exception):
            raise value
        return value

    def program_status(self):
        return self._get("program_status")

    def execution_state(self):
        return self._get("execution_state")

    def override_state(self):
        return self._get("override_state")

    def get_error_messages(self):
        return self._get("get_error_messages", [])

    def program_stack(self):
        return self._get("program_stack")

    def spindle_tool_status(self):
        return self._get("spindle_tool_status")

    def disconnect(self):
        pass


def make_driver(**responses):
    driver = HeidenhainLsv2Driver(Config(tnc_ip="10.0.0.1"))
    driver._client = FakeClient(**responses)
    driver._can = {
        "program_status": True,
        "execution_state": True,
        "override_state": True,
        "error_messages": True,
        "program_stack": True,
        "tool": True,
    }
    return driver


@unittest.skipIf(PYLSV2 is None, "gömülü pyLSV2 bulunamadı")
class TestLsv2StateMapping(unittest.TestCase):
    def test_manual_mode_is_setup_whatever_the_program_says(self):
        driver = make_driver(
            execution_state=PYLSV2.ExecState.MANUAL,
            program_status=PYLSV2.PgmState.STARTED,
        )
        self.assertIs(driver.read().exec_state, ExecState.MANUAL)

    def test_mdi_is_setup(self):
        driver = make_driver(
            execution_state=PYLSV2.ExecState.MDI,
            program_status=PYLSV2.PgmState.STARTED,
        )
        self.assertIs(driver.read().exec_state, ExecState.MANUAL)

    def test_automatic_and_started_is_running(self):
        driver = make_driver(
            execution_state=PYLSV2.ExecState.AUTOMATIC,
            program_status=PYLSV2.PgmState.STARTED,
        )
        self.assertIs(driver.read().exec_state, ExecState.RUNNING)

    def test_automatic_and_error_is_error(self):
        driver = make_driver(
            execution_state=PYLSV2.ExecState.AUTOMATIC,
            program_status=PYLSV2.PgmState.ERROR,
        )
        self.assertIs(driver.read().exec_state, ExecState.ERROR)

    def test_interrupted_is_interrupted(self):
        driver = make_driver(
            execution_state=PYLSV2.ExecState.AUTOMATIC,
            program_status=PYLSV2.PgmState.INTERRUPTED,
        )
        self.assertIs(driver.read().exec_state, ExecState.INTERRUPTED)

    def test_stopped_states_are_stopped(self):
        for pgm in (
            PYLSV2.PgmState.STOPPED,
            PYLSV2.PgmState.FINISHED,
            PYLSV2.PgmState.CANCELLED,
            PYLSV2.PgmState.IDLE,
            PYLSV2.PgmState.ERROR_CLEARED,
        ):
            with self.subTest(pgm=pgm):
                driver = make_driver(
                    execution_state=PYLSV2.ExecState.AUTOMATIC, program_status=pgm
                )
                self.assertIs(driver.read().exec_state, ExecState.STOPPED)

    def test_stopped_program_becomes_durus_downstream(self):
        """End to end: a stopped program has to reach the state machine as DURUŞ."""
        driver = make_driver(
            execution_state=PYLSV2.ExecState.AUTOMATIC,
            program_status=PYLSV2.PgmState.STOPPED,
        )
        tracker = StateTracker("T1", idle_threshold_s=0)
        self.assertIs(tracker.derive_state(driver.read()), MachineState.DURUS)


@unittest.skipIf(PYLSV2 is None, "gömülü pyLSV2 bulunamadı")
class TestLsv2FieldReading(unittest.TestCase):
    def test_feed_and_spindle_are_always_none(self):
        """LSV2 cannot deliver these. Reporting 0 would be a lie."""
        driver = make_driver(override_state=FakeOverride(feed=80.0))
        snap = driver.read()
        self.assertIsNone(snap.feed_actual)
        self.assertIsNone(snap.spindle_actual)
        self.assertEqual(snap.feed_override, 80.0)

    def test_program_path_reduced_to_filename(self):
        driver = make_driver(
            program_stack=FakeStack(current="TNC:\\nc_prog\\PARCA_12.H", line_no=1240)
        )
        snap = driver.read()
        self.assertEqual(snap.program_name, "PARCA_12.H")
        self.assertEqual(snap.block_number, 1240)

    def test_falls_back_to_main_program(self):
        driver = make_driver(program_stack=FakeStack(current=None, main="ANA.H"))
        self.assertEqual(driver.read().program_name, "ANA.H")

    def test_negative_sentinels_become_none(self):
        """pyLSV2 uses -1 for "unknown"; that must not reach the log as -1."""
        driver = make_driver(
            program_stack=FakeStack(current="A.H", line_no=-1),
            spindle_tool_status=FakeTool(number=-1),
        )
        snap = driver.read()
        self.assertIsNone(snap.block_number)
        self.assertIsNone(snap.tool_number)

    def test_alarms_converted(self):
        driver = make_driver(get_error_messages=[FakeMessage()])
        alarms = driver.read().alarms
        self.assertEqual(len(alarms), 1)
        self.assertEqual(alarms[0].code, "2.15.1024")
        self.assertEqual(alarms[0].text, "Test hatası")

    def test_unsupported_call_is_skipped_entirely(self):
        driver = make_driver(override_state=FakeOverride())
        driver._can["override_state"] = False
        driver.read()
        self.assertNotIn("override_state", driver._client.calls)

    def test_failure_on_a_working_call_raises_driver_error(self):
        """So the collector reconnects instead of recording nonsense."""
        driver = make_driver(program_status=OSError("bağlantı kapandı"))
        with self.assertRaises(DriverError):
            driver.read()

    def test_read_without_connect_raises(self):
        driver = HeidenhainLsv2Driver(Config(tnc_ip="10.0.0.1"))
        with self.assertRaises(DriverError):
            driver.read()


class TestLsv2Helpers(unittest.TestCase):
    def test_basename_handles_tnc_paths(self):
        self.assertEqual(_basename("TNC:\\nc_prog\\A.H"), "A.H")
        self.assertEqual(_basename("/mnt/nc/B.H"), "B.H")
        self.assertEqual(_basename("C.H"), "C.H")
        self.assertIsNone(_basename(None))
        self.assertIsNone(_basename(""))

    def test_alarm_without_text_gets_placeholder(self):
        alarm = _to_alarm(FakeMessage(e_text="   "))
        self.assertEqual(alarm.text, "Açıklama yok")


class TestReadOnlyGuarantee(unittest.TestCase):
    """The core safety promise, enforced as a test.

    If someone later adds a write method to a driver, this fails.
    """

    FORBIDDEN = (
        "write", "send", "set_", "delete", "move_file", "make_directory",
        "send_key", "upload", "put", "execute", "login_plc",
    )

    def _check(self, cls):
        for name in dir(cls):
            if name.startswith("__"):
                continue
            for bad in self.FORBIDDEN:
                self.assertFalse(
                    name.startswith(bad),
                    f"{cls.__name__}.{name} yazma benzeri bir metod — "
                    "sürücüler salt okunur olmalı",
                )

    def test_base_driver_has_no_write_methods(self):
        self._check(Driver)

    def test_lsv2_driver_has_no_write_methods(self):
        self._check(HeidenhainLsv2Driver)

    def test_simulator_has_no_write_methods(self):
        self._check(SimulatorDriver)


class FakeClock:
    """Controllable clock so a simulated shift can be replayed instantly."""

    def __init__(self, start=1_700_000_000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class TestSimulator(unittest.TestCase):
    def _driver(self, seed=7, clock=None):
        driver = SimulatorDriver(Config(), speed=1.0, seed=seed, clock=clock)
        driver.connect()
        return driver

    def test_same_seed_gives_same_shift(self):
        def run(seed):
            clock = FakeClock()
            driver = self._driver(seed=seed, clock=clock)
            states = []
            for _ in range(200):
                clock.advance(2)
                states.append(driver.read().exec_state)
            return states

        self.assertEqual(run(42), run(42))

    def test_different_seeds_diverge(self):
        def run(seed):
            clock = FakeClock()
            driver = self._driver(seed=seed, clock=clock)
            out = []
            for _ in range(200):
                clock.advance(2)
                out.append(driver.read().exec_state)
            return out

        self.assertNotEqual(run(1), run(2))

    def test_produces_every_situation_the_logger_must_handle(self):
        """Eight simulated hours must contain each case the logger has to get right."""
        clock = FakeClock()
        driver = self._driver(seed=7, clock=clock)
        tracker = StateTracker("T1", idle_threshold_s=10)

        seen = set()
        for _ in range(14400):  # 8 h at 2 s per sample
            clock.advance(2)
            seen.add(tracker.derive_state(driver.read()))

        for expected in (
            MachineState.CALISIYOR,
            MachineState.DURUS,
            MachineState.ALARM,
            MachineState.KURULUM,
            MachineState.BAGLANTI_YOK,
        ):
            self.assertIn(expected, seen, f"simülatör {expected.value} üretmedi")

    def test_produces_sub_threshold_pauses(self):
        """The awkward case the threshold exists for has to actually occur."""
        clock = FakeClock()
        driver = self._driver(seed=7, clock=clock)
        tracker = StateTracker("T1", idle_threshold_s=10)

        pauses, events = 0, 0
        previous = None
        for _ in range(14400):
            clock.advance(2)
            result = tracker.update(driver.read())
            if result.raw_state is MachineState.DURUS and previous is not MachineState.DURUS:
                pauses += 1
            events += sum(1 for e in result.opened if e.state is MachineState.DURUS)
            previous = result.raw_state

        self.assertGreater(pauses, 0, "simülatör hiç duraklama üretmedi")
        self.assertGreater(
            pauses, events, "eşiğin altında kalan kısa duraklama hiç olmamış"
        )

    def test_time_scale_multiplies_virtual_time(self):
        clock = FakeClock()
        driver = SimulatorDriver(Config(), speed=60, seed=1, clock=clock)
        driver.connect()
        start = driver.read().ts
        clock.advance(10)  # ten real seconds
        self.assertAlmostEqual(driver.read().ts - start, 600, delta=1)

    def test_disconnected_read_reports_offline(self):
        driver = self._driver()
        driver.disconnect()
        snap = driver.read()
        self.assertFalse(snap.connected)


class TestRegistry(unittest.TestCase):
    def test_known_drivers_listed(self):
        names = available_drivers()
        self.assertIn("simulator", names)
        self.assertIn("heidenhain_lsv2", names)
        self.assertIn("heidenhain_opcua", names)

    def test_unknown_driver_names_the_alternatives(self):
        with self.assertRaises(DriverError) as ctx:
            create_driver("fanuc_focas", Config())
        message = str(ctx.exception)
        self.assertIn("fanuc_focas", message)
        self.assertIn("simulator", message)


if __name__ == "__main__":
    unittest.main()
