"""Discovery and the auto driver.

Runs against a real listening socket rather than a mock, because the thing most
likely to be wrong is the socket handling itself -- timeouts, cleanup, and the
distinction between "a port is open" and "a Heidenhain control is there".
"""

import socket
import threading
import unittest

from cnclog import discovery
from cnclog.config import Config
from cnclog.drivers.auto import AutoDriver
from cnclog.drivers.base import DriverError


class FakeServer:
    """A socket that accepts connections but speaks no LSV2."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        self.sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
                conn.close()
            except (socket.timeout, OSError):
                continue

    def close(self):
        self._stop.set()
        try:
            self.sock.close()
        except OSError:
            pass


class TestPortProbe(unittest.TestCase):
    def setUp(self):
        self.server = FakeServer()

    def tearDown(self):
        self.server.close()

    def test_open_port_detected(self):
        self.assertTrue(discovery.port_open("127.0.0.1", self.server.port))

    def test_closed_port_not_detected(self):
        self.server.close()
        self.assertFalse(
            discovery.port_open("127.0.0.1", self.server.port, timeout=0.3)
        )

    def test_unroutable_address_times_out_quickly(self):
        import time

        started = time.time()
        # TEST-NET-1, reserved and never routed.
        discovery.port_open("192.0.2.1", 19000, timeout=0.3)
        self.assertLess(time.time() - started, 3.0, "zaman aşımı çok uzun sürdü")

    def test_probe_many_returns_only_open_ones(self):
        addresses = ["127.0.0.1", "192.0.2.1", "192.0.2.2"]
        open_ones = discovery._probe_many(addresses, self.server.port)
        self.assertEqual(open_ones, ["127.0.0.1"])

    def test_no_socket_leak_over_many_probes(self):
        """A leaking probe would exhaust file descriptors during a subnet sweep."""
        import resource

        soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        for _ in range(min(400, soft // 2)):
            discovery.port_open("127.0.0.1", self.server.port, timeout=0.05)
        # Reaching here without OSError: too many open files is the assertion.
        self.assertTrue(discovery.port_open("127.0.0.1", self.server.port))


class TestOpenPortIsNotAControl(unittest.TestCase):
    """The distinction that keeps the logger off the wrong machine."""

    def setUp(self):
        self.server = FakeServer()

    def tearDown(self):
        self.server.close()

    def test_verify_rejects_non_lsv2_service(self):
        self.assertIsNone(
            discovery.verify_lsv2("127.0.0.1", self.server.port, timeout=1.0)
        )

    def test_find_control_returns_none_for_plain_socket(self):
        found = discovery.find_control(
            configured_ip="127.0.0.1",
            port=self.server.port,
            timeout=1.0,
            scan_subnet=False,
        )
        self.assertIsNone(found, "LSV2 konuşmayan bir servise bağlanmamalı")

    def test_failed_verification_closes_its_socket(self):
        """pyLSV2's disconnect() logs out first and can fail, stranding the
        socket. During a /24 sweep that would exhaust file descriptors."""
        import gc
        import warnings

        gc.collect()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            for _ in range(12):
                discovery.verify_lsv2("127.0.0.1", self.server.port, timeout=0.5)
            gc.collect()
        leaked = [w for w in caught if issubclass(w.category, ResourceWarning)
                  and "socket" in str(w.message)]
        self.assertEqual(leaked, [], "doğrulama sonrası soket sızdı")


class TestRefusalDetection(unittest.TestCase):
    """"Denied" and "not there" must not look the same to the operator.

    A control that answers LSV2 and refuses the login has been *found*; the
    fix is one setting on the machine. Reporting "not found" would send
    someone hunting for an IP address that is already known.
    """

    def setUp(self):
        self.server = FakeServer()

    def tearDown(self):
        self.server.close()

    def test_plain_socket_produces_no_refusal(self):
        reasons = []
        discovery.verify_lsv2(
            "127.0.0.1", self.server.port, timeout=0.5, reason=reasons
        )
        self.assertEqual(reasons, [], "LSV2 konuşmayan servis red sayılmamalı")

    def test_log_catcher_recognises_privilege_error(self):
        catcher = discovery._LogCatcher()
        catcher.messages = [
            "Connected to host 192.168.1.10 at port 19000",
            "an error was received after the last transmission, Error Type: 1,"
            " Error Code: 23 'LSV2_ERROR_T_ER_NO_PRIV'",
        ]
        self.assertTrue(catcher.saw_privilege_error())

    def test_log_catcher_recognises_failed_login(self):
        catcher = discovery._LogCatcher()
        catcher.messages = ["error logging in as INSPECT"]
        self.assertTrue(catcher.saw_privilege_error())

    def test_log_catcher_ignores_ordinary_messages(self):
        catcher = discovery._LogCatcher()
        catcher.messages = ["Socket successfully created", "Connection closed"]
        self.assertFalse(catcher.saw_privilege_error())

    def test_catcher_restores_logger_levels(self):
        """It raises log levels temporarily; leaving them raised would flood
        the operator's console with pyLSV2 debug output."""
        from cnclog.drivers.heidenhain_lsv2 import _PYLSV2_LOGGERS

        import logging

        before = {n: logging.getLogger(n).level for n in _PYLSV2_LOGGERS}
        with discovery._LogCatcher():
            pass
        after = {n: logging.getLogger(n).level for n in _PYLSV2_LOGGERS}
        self.assertEqual(before, after)

    def test_catcher_removes_its_handler(self):
        from cnclog.drivers.heidenhain_lsv2 import _PYLSV2_LOGGERS

        import logging

        catcher = discovery._LogCatcher()
        with catcher:
            pass
        for name in _PYLSV2_LOGGERS:
            self.assertNotIn(catcher, logging.getLogger(name).handlers)


class TestInterfaceEnumeration(unittest.TestCase):
    """A virtualised control often sits on a host-only adapter with no
    default route -- the address must still be discovered."""

    def test_command_parsing_finds_addresses(self):
        addresses = discovery._addresses_from_commands()
        self.assertIsInstance(addresses, list)
        for address in addresses:
            self.assertRegex(address, r"^\d{1,3}(\.\d{1,3}){3}$")

    def test_loopback_and_linklocal_filtered(self):
        self.assertFalse(discovery._usable("127.0.0.1"))
        self.assertFalse(discovery._usable("169.254.10.4"))
        self.assertFalse(discovery._usable("0.0.0.0"))
        self.assertTrue(discovery._usable("192.168.56.1"))

    def test_addr_line_matches_all_three_formats(self):
        samples = [
            ("   IPv4 Address. . . . . . . . . . . : 192.168.56.1", "192.168.56.1"),
            ("    inet 192.168.1.10/24 brd 192.168.1.255 scope global", "192.168.1.10"),
            ("\tinet 10.0.2.15 netmask 0xffffff00 broadcast 10.0.2.255", "10.0.2.15"),
        ]
        for line, expected in samples:
            with self.subTest(line=line):
                match = discovery._ADDR_LINE.search(line)
                self.assertIsNotNone(match, "satır tanınmadı")
                self.assertEqual(match.group(1), expected)


class TestCandidateOrder(unittest.TestCase):
    def test_configured_address_comes_first(self):
        candidates = discovery.candidate_addresses("10.1.2.3")
        self.assertEqual(candidates[0], "10.1.2.3")

    def test_localhost_always_included(self):
        # When running on the control's own HEROS desktop, the machine is here.
        self.assertIn("127.0.0.1", discovery.candidate_addresses(None))

    def test_no_duplicates(self):
        candidates = discovery.candidate_addresses("127.0.0.1")
        self.assertEqual(len(candidates), len(set(candidates)))


class TestSubnetExpansion(unittest.TestCase):
    def test_excludes_own_address(self):
        hosts = discovery.subnet_addresses("192.168.1.4")
        self.assertNotIn("192.168.1.4", hosts)
        self.assertIn("192.168.1.1", hosts)
        self.assertIn("192.168.1.254", hosts)
        self.assertEqual(len(hosts), 253)

    def test_garbage_input_is_safe(self):
        self.assertEqual(discovery.subnet_addresses("not-an-ip"), [])
        self.assertEqual(discovery.subnet_addresses(""), [])


class TestCancellation(unittest.TestCase):
    def test_search_stops_when_asked(self):
        """Shutdown must not have to wait out a 253-address sweep."""
        calls = []

        def should_continue():
            calls.append(1)
            return False  # cancelled immediately

        found = discovery.find_control(
            configured_ip="192.0.2.1",
            port=19000,
            timeout=0.2,
            scan_subnet=True,
            should_continue=should_continue,
        )
        self.assertIsNone(found)
        self.assertTrue(calls, "iptal kontrolü hiç çağrılmadı")


class TestAutoDriver(unittest.TestCase):
    def test_reports_clear_error_when_nothing_found(self):
        cfg = Config(tnc_ip="192.0.2.1", tnc_port=19000, timeout_s=0.3,
                     auto_scan=False)
        driver = AutoDriver(cfg)
        with self.assertRaises(DriverError) as ctx:
            driver.connect()
        message = str(ctx.exception)
        self.assertIn("bulunamadı", message)
        self.assertIn("LSV2", message)

    def test_never_falls_back_to_simulated_data(self):
        """A logger that invents numbers is worse than one that reports a gap."""
        cfg = Config(tnc_ip="192.0.2.1", timeout_s=0.3, auto_scan=False)
        driver = AutoDriver(cfg)
        try:
            driver.connect()
        except DriverError:
            pass
        with self.assertRaises(DriverError):
            driver.read()

    def test_read_before_connect_raises(self):
        driver = AutoDriver(Config())
        with self.assertRaises(DriverError):
            driver.read()

    def test_cancelled_search_says_so(self):
        cfg = Config(tnc_ip="192.0.2.1", timeout_s=0.3, auto_scan=False)
        driver = AutoDriver(cfg)
        driver.should_continue = lambda: False
        with self.assertRaises(DriverError) as ctx:
            driver.connect()
        self.assertIn("iptal", str(ctx.exception).lower())

    def test_has_no_write_methods(self):
        for name in dir(AutoDriver):
            self.assertFalse(
                name.startswith(("write", "send", "set_", "delete")),
                f"AutoDriver.{name} yazma benzeri bir metod",
            )


if __name__ == "__main__":
    unittest.main()
