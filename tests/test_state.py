"""State machine: derivation, the stop threshold, and event bookkeeping.

These are the tests that matter most. Everything downstream -- durations,
availability, the whole report -- is wrong if this module is wrong, and the
threshold behaviour is exactly the part a reader would assume works one way
when it works another.
"""

import unittest

from cnclog.model import Alarm, EventType, ExecState, MachineState, Snapshot
from cnclog.state import StateTracker


def snap(ts, exec_state=ExecState.RUNNING, **kwargs):
    return Snapshot(ts=ts, exec_state=exec_state, **kwargs)


class TestStateDerivation(unittest.TestCase):
    def setUp(self):
        self.tracker = StateTracker("T1", idle_threshold_s=10)

    def test_disconnected_outranks_everything(self):
        s = Snapshot(ts=0, connected=False, exec_state=ExecState.RUNNING)
        s.alarms = [Alarm("1", "x")]
        self.assertIs(self.tracker.derive_state(s), MachineState.BAGLANTI_YOK)

    def test_alarm_outranks_running(self):
        s = snap(0, alarms=[Alarm("1024", "Soğutma basıncı")])
        self.assertIs(self.tracker.derive_state(s), MachineState.ALARM)

    def test_error_exec_state_is_alarm(self):
        self.assertIs(
            self.tracker.derive_state(snap(0, ExecState.ERROR)), MachineState.ALARM
        )

    def test_running_is_calisiyor_even_with_zero_feed(self):
        # A running program with F=0 is a tool change or a dwell, not downtime.
        s = snap(0, ExecState.RUNNING, feed_actual=0.0)
        self.assertIs(self.tracker.derive_state(s), MachineState.CALISIYOR)

    def test_manual_is_kurulum(self):
        self.assertIs(
            self.tracker.derive_state(snap(0, ExecState.MANUAL)), MachineState.KURULUM
        )

    def test_stopped_is_durus(self):
        self.assertIs(
            self.tracker.derive_state(snap(0, ExecState.STOPPED)), MachineState.DURUS
        )

    def test_unknown_is_durus(self):
        self.assertIs(
            self.tracker.derive_state(snap(0, ExecState.UNKNOWN)), MachineState.DURUS
        )


class TestRapidDerivation(unittest.TestCase):
    def setUp(self):
        self.tracker = StateTracker("T1", rapid_feed_threshold=5000)

    def test_control_flag_wins_over_heuristic(self):
        s = snap(0, feed_actual=100.0, is_rapid=True)
        self.assertTrue(self.tracker.derive_rapid(s))

    def test_heuristic_used_when_flag_absent(self):
        self.assertTrue(self.tracker.derive_rapid(snap(0, feed_actual=8000.0)))
        self.assertFalse(self.tracker.derive_rapid(snap(0, feed_actual=1200.0)))

    def test_unknown_when_no_feed_reading(self):
        # LSV2 cannot read actual feed; we must not claim to know.
        self.assertIsNone(self.tracker.derive_rapid(snap(0, feed_actual=None)))


class TestIdleThreshold(unittest.TestCase):
    """The behaviour the user specifically asked for: 10 s default, 0 disables."""

    def _run(self, tracker, script):
        """Feed (ts, exec_state) pairs; collect every event that was opened."""
        opened = []
        for ts, state in script:
            result = tracker.update(snap(ts, state))
            opened.extend(result.opened)
        return opened

    def test_short_pause_creates_no_event(self):
        tracker = StateTracker("T1", idle_threshold_s=10)
        # 5-second pause, well under the threshold.
        opened = self._run(tracker, [
            (0, ExecState.RUNNING),
            (2, ExecState.RUNNING),
            (4, ExecState.STOPPED),
            (6, ExecState.STOPPED),
            (8, ExecState.RUNNING),
            (10, ExecState.RUNNING),
        ])
        stops = [e for e in opened if e.state is MachineState.DURUS]
        self.assertEqual(stops, [], "kısa duraklama olay üretmemeliydi")

    def test_short_pause_still_visible_as_raw_state(self):
        """The pause must not vanish from the data, only from the event list."""
        tracker = StateTracker("T1", idle_threshold_s=10)
        tracker.update(snap(0, ExecState.RUNNING))
        result = tracker.update(snap(4, ExecState.STOPPED))
        self.assertIs(result.raw_state, MachineState.DURUS)
        self.assertIs(result.state, MachineState.CALISIYOR)  # not confirmed yet
        self.assertTrue(result.opened == [] and result.closed == [])

    def test_long_stop_creates_event(self):
        tracker = StateTracker("T1", idle_threshold_s=10)
        opened = self._run(tracker, [
            (0, ExecState.RUNNING),
            (10, ExecState.STOPPED),
            (16, ExecState.STOPPED),
            (22, ExecState.STOPPED),
        ])
        stops = [e for e in opened if e.state is MachineState.DURUS]
        self.assertEqual(len(stops), 1)

    def test_stop_event_is_backdated_to_real_start(self):
        """Confirmed at t=22, but the stop began at t=10 and must say so."""
        tracker = StateTracker("T1", idle_threshold_s=10)
        tracker.update(snap(0, ExecState.RUNNING))
        tracker.update(snap(10, ExecState.STOPPED))
        tracker.update(snap(16, ExecState.STOPPED))
        result = tracker.update(snap(22, ExecState.STOPPED))

        stops = [e for e in result.opened if e.state is MachineState.DURUS]
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0].ts_start, 10, "duruş gerçek başlangıcına tarihlenmeli")

        closed_run = [e for e in result.closed if e.state is MachineState.CALISIYOR]
        self.assertEqual(closed_run[0].ts_end, 10)
        self.assertEqual(closed_run[0].duration_s, 10)

    def test_threshold_zero_logs_immediately(self):
        tracker = StateTracker("T1", idle_threshold_s=0)
        tracker.update(snap(0, ExecState.RUNNING))
        result = tracker.update(snap(4, ExecState.STOPPED))
        stops = [e for e in result.opened if e.state is MachineState.DURUS]
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0].ts_start, 4)

    def test_alarm_is_never_debounced(self):
        """An alarm must be recorded at once, whatever the stop threshold is."""
        tracker = StateTracker("T1", idle_threshold_s=60)
        tracker.update(snap(0, ExecState.RUNNING))
        result = tracker.update(snap(2, alarms=[Alarm("9", "test")]))
        states = [e.state for e in result.opened if e.type is EventType.STATE]
        self.assertIn(MachineState.ALARM, states)

    def test_flapping_does_not_split_the_run(self):
        """Repeated sub-threshold pauses must not chop ÇALIŞIYOR into pieces."""
        tracker = StateTracker("T1", idle_threshold_s=10)
        script = [(0, ExecState.RUNNING)]
        t = 2
        for _ in range(5):
            script += [(t, ExecState.STOPPED), (t + 4, ExecState.RUNNING)]
            t += 10
        opened = self._run(tracker, script)
        runs = [e for e in opened if e.state is MachineState.CALISIYOR]
        self.assertEqual(len(runs), 1, "çalışma tek bir aralık olarak kalmalı")


class TestStopReason(unittest.TestCase):
    def test_reason_says_part_change_when_no_program_loaded(self):
        tracker = StateTracker("T1", idle_threshold_s=0)
        tracker.update(snap(0, ExecState.RUNNING, program_name="A.H"))
        result = tracker.update(snap(5, ExecState.STOPPED, program_name=None))
        stop = [e for e in result.opened if e.state is MachineState.DURUS][0]
        self.assertIn("parça değişimi", stop.text)

    def test_reason_names_the_program_when_one_is_loaded(self):
        tracker = StateTracker("T1", idle_threshold_s=0)
        tracker.update(snap(0, ExecState.RUNNING, program_name="PARCA_12.H"))
        result = tracker.update(snap(5, ExecState.STOPPED, program_name="PARCA_12.H"))
        stop = [e for e in result.opened if e.state is MachineState.DURUS][0]
        self.assertIn("PARCA_12.H", stop.text)

    def test_reason_taken_from_when_the_stop_began(self):
        """With a threshold, the reason must reflect the moment it started."""
        tracker = StateTracker("T1", idle_threshold_s=10)
        tracker.update(snap(0, ExecState.RUNNING, program_name="A.H"))
        tracker.update(snap(10, ExecState.STOPPED, program_name=None))
        # By the time it is confirmed a different program is loaded.
        result = tracker.update(snap(22, ExecState.STOPPED, program_name="B.H"))
        stop = [e for e in result.opened if e.state is MachineState.DURUS][0]
        self.assertIn("parça değişimi", stop.text)


class TestAlarmTracking(unittest.TestCase):
    def test_alarm_opens_and_closes_once(self):
        tracker = StateTracker("T1", idle_threshold_s=0)
        alarm = Alarm("1024", "Soğutma basıncı")

        r1 = tracker.update(snap(0, alarms=[alarm]))
        opened = [e for e in r1.opened if e.type is EventType.ALARM]
        self.assertEqual(len(opened), 1)
        self.assertEqual(opened[0].code, "1024")

        # Same alarm still active: must not open a second event.
        r2 = tracker.update(snap(5, alarms=[alarm]))
        self.assertEqual([e for e in r2.opened if e.type is EventType.ALARM], [])

        r3 = tracker.update(snap(9, ExecState.RUNNING))
        closed = [e for e in r3.closed if e.type is EventType.ALARM]
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0].duration_s, 9)

    def test_two_alarms_tracked_separately(self):
        tracker = StateTracker("T1")
        a, b = Alarm("1", "bir"), Alarm("2", "iki")
        tracker.update(snap(0, alarms=[a]))
        r = tracker.update(snap(3, alarms=[a, b]))
        self.assertEqual(len([e for e in r.opened if e.type is EventType.ALARM]), 1)

    def test_lost_connection_closes_open_alarms(self):
        """We stop being able to observe, so we must stop claiming."""
        tracker = StateTracker("T1")
        tracker.update(snap(0, alarms=[Alarm("1", "bir")]))
        result = tracker.update(Snapshot(ts=10, connected=False))
        closed = [e for e in result.closed if e.type is EventType.ALARM]
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0].ts_end, 10)


class TestProgramTracking(unittest.TestCase):
    def test_program_change_closes_and_opens(self):
        tracker = StateTracker("T1")
        tracker.update(snap(0, program_name="A.H"))
        result = tracker.update(snap(10, program_name="B.H"))

        closed = [e for e in result.closed if e.type is EventType.PROGRAM]
        opened = [e for e in result.opened if e.type is EventType.PROGRAM]
        self.assertEqual(closed[0].program_name, "A.H")
        self.assertEqual(closed[0].duration_s, 10)
        self.assertEqual(opened[0].program_name, "B.H")

    def test_close_all_leaves_nothing_open(self):
        tracker = StateTracker("T1")
        tracker.update(snap(0, program_name="A.H", alarms=[Alarm("1", "x")]))
        closed = tracker.close_all(50)
        self.assertTrue(all(not e.is_open for e in closed))
        self.assertGreaterEqual(len(closed), 3)  # state + program + alarm


if __name__ == "__main__":
    unittest.main()
