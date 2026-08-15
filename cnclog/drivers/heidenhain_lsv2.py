"""Heidenhain TNC 640 over LSV2 (free, no licensed OPC UA option needed).

WHAT THIS CAN AND CANNOT READ
-----------------------------
Available through LSV2:
    program state, execution mode, override percentages, active program and
    block number, spindle tool, and the control's error/alarm messages.

NOT available through LSV2:
    the *actual* feed rate and spindle speed in mm/min and rpm. LSV2 exposes
    the override percentages, not the live values. Reading the real figures
    needs either the OPC UA NC Server (Option 56) or PLC memory access.

PLC memory is deliberately NOT used here. pyLSV2 can read it, but doing so
requires the PLCDEBUG access level, which is password protected and grants
*write* access to the PLC. This program's core guarantee is that it cannot
change anything on the machine, and that guarantee is worth more than two
extra numbers. Use the OPC UA driver if you need real F and S.

ABOUT safe_mode
---------------
pyLSV2 defaults to safe_mode=True, which restricts logins to INSPECT,
FILETRANSFER and MONITOR. Every monitoring call we need -- program_status,
execution_state, override_state, get_error_messages, program_stack,
spindle_tool_status -- requires the DNC login, which safe_mode blocks. So this
driver constructs the client with safe_mode=False.

That flag only lifts a restriction inside the library; it does not make this
driver write anything. This class calls six read-only methods and nothing else.
There is no code path here that sends a key code, transfers a file, changes a
machine parameter or logs in as PLCDEBUG.

REQUIREMENTS ON THE MACHINE
---------------------------
The DNC login generally requires the DNC software option (Option 18) to be
enabled on the control, and the LSV2 interface has to be switched on in the
network settings. Without it, connect() succeeds but every read returns
nothing; the driver reports that clearly instead of pretending.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..model import Alarm, ExecState, Snapshot
from .base import Driver, DriverError

#: LSV2 listens here on Heidenhain controls.
DEFAULT_PORT = 19000

#: pyLSV2 writes its own diagnostics to these loggers. A failed connection is
#: an expected, handled situation here -- we report it in Turkish through our
#: own log -- so its raw English output is silenced rather than duplicated.
_PYLSV2_LOGGERS = (
    "LSV2 Client",
    "LSV2 Client Scope",
    "LSV2 TCP",
    "LSV2 RS232",
    "NCTable",
    "NCTable parser",
    "NCTable format parser",
)


def _quiet_pylsv2() -> None:
    for name in _PYLSV2_LOGGERS:
        logger = logging.getLogger(name)
        logger.setLevel(logging.CRITICAL)
        logger.propagate = False


def close_client(client) -> None:
    """Close an LSV2 client and make sure its socket really goes away.

    pyLSV2's disconnect() logs out over the network first. On a half-open
    connection -- which is every failed probe during discovery -- that call
    raises and the socket close beneath it never runs. Sweeping a /24 would
    then leak one file descriptor per address. So after the polite close we
    reach in and shut the socket regardless.
    """
    if client is None:
        return
    try:
        client.disconnect()
    except Exception:  # noqa: BLE001
        pass
    try:
        low_level = getattr(client, "_llcom", None)
        sock = getattr(low_level, "_tcpsock", None)
        if sock is not None:
            sock.close()
    except Exception:  # noqa: BLE001
        pass


def _load_pylsv2():
    """Prefer the bundled copy, fall back to a system-installed one."""
    try:
        from ..vendor import pyLSV2  # type: ignore[attr-defined]

        return pyLSV2
    except ImportError:
        pass
    try:
        import pyLSV2  # type: ignore[no-redef]

        return pyLSV2
    except ImportError:
        return None


class HeidenhainLsv2Driver(Driver):
    """Read-only LSV2 client for TNC 640 and its siblings."""

    name = "heidenhain_lsv2"

    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg
        self._lsv2 = _load_pylsv2()
        self._client = None
        self._version_text: Optional[str] = None
        # Which calls actually returned something on this machine. Probed once
        # at connect so a control without the DNC option does not retry six
        # failing logins on every single tick.
        self._can: Dict[str, bool] = {}

    # ---------------------------------------------------------------- connect

    def describe(self) -> str:
        target = f"{self.cfg.tnc_ip}:{self.cfg.tnc_port or DEFAULT_PORT}"
        if self._version_text:
            return f"Heidenhain LSV2 — {self._version_text} @ {target}"
        return f"Heidenhain LSV2 @ {target}"

    def connect(self) -> None:
        if self._lsv2 is None:
            raise DriverError(
                "pyLSV2 kütüphanesi bulunamadı. Program klasöründeki "
                "cnclog/vendor/pyLSV2 dizini eksik olabilir."
            )
        if not self.cfg.tnc_ip:
            raise DriverError(
                "Tezgah adresi tanımlı değil. config.ini içinde [surucu] "
                "tnc_ip satırını doldurun."
            )

        _quiet_pylsv2()

        client = None
        try:
            client = self._lsv2.LSV2(
                self.cfg.tnc_ip,
                port=self.cfg.tnc_port or DEFAULT_PORT,
                timeout=self.cfg.timeout_s,
                safe_mode=False,  # see module docstring -- DNC login needs this
            )
            client.connect()
        except Exception as exc:  # noqa: BLE001 - any failure means "no link"
            # A failed connect still leaves a socket behind; the collector
            # retries on a timer, so leaking one per attempt would add up.
            close_client(client)
            raise DriverError(
                f"Tezgaha bağlanılamadı ({self.cfg.tnc_ip}:"
                f"{self.cfg.tnc_port or DEFAULT_PORT}): {exc}"
            ) from exc

        self._client = client
        try:
            versions = client.versions
            self._version_text = f"{versions.control} {versions.nc_sw}".strip()
        except Exception:  # noqa: BLE001 - cosmetic only
            self._version_text = None

        self._probe()
        if not any(self._can.values()):
            self.disconnect()
            raise DriverError(
                "Bağlantı kuruldu ama tezgahtan hiçbir veri okunamıyor.\n"
                "Sırasıyla şunlara bakın:\n"
                "  1) Dış erişim açık mı?  Programlama modu → MOD →\n"
                "     External access = ON → END\n"
                "  2) DNC yazılım opsiyonu (Opsiyon 18) lisanslı mı?\n"
                "     MOD menüsünden opsiyon listesine bakın.\n"
                "  3) Kontrolün PLC programı çalışıyor mu?\n"
                "Bunlar olmadan LSV2 bağlanır ama veri vermez."
            )

    def _probe(self) -> None:
        """Try each read once and remember which ones this control supports."""
        checks = {
            "program_status": lambda c: c.program_status(),
            "execution_state": lambda c: c.execution_state(),
            "override_state": lambda c: c.override_state(),
            "error_messages": lambda c: c.get_error_messages(),
            "program_stack": lambda c: c.program_stack(),
            "tool": lambda c: c.spindle_tool_status(),
        }
        for key, call in checks.items():
            try:
                result = call(self._client)
                self._can[key] = result is not None
            except Exception:  # noqa: BLE001 - unsupported call, not a crash
                self._can[key] = False

    def disconnect(self) -> None:
        close_client(self._client)
        self._client = None

    @property
    def capabilities(self) -> Dict[str, bool]:
        """Which reads work on this machine. Used by --test-baglanti."""
        return dict(self._can)

    # ------------------------------------------------------------------- read

    def read(self) -> Snapshot:
        if self._client is None:
            raise DriverError("Bağlantı kurulmadı")

        snap = Snapshot()
        const = self._lsv2

        mode = self._safe("execution_state", lambda c: c.execution_state())
        pgm = self._safe("program_status", lambda c: c.program_status())
        snap.exec_state = self._derive_exec_state(mode, pgm, const)

        override = self._safe("override_state", lambda c: c.override_state())
        if override is not None:
            snap.feed_override = _as_float(getattr(override, "feed", None))
            snap.spindle_override = _as_float(getattr(override, "spindle", None))
            snap.rapid_override = _as_float(getattr(override, "rapid", None))

        stack = self._safe("program_stack", lambda c: c.program_stack())
        if stack is not None:
            name = getattr(stack, "current", None) or getattr(stack, "main", None)
            snap.program_name = _basename(name)
            line = getattr(stack, "line_no", None)
            snap.block_number = int(line) if isinstance(line, int) and line >= 0 else None

        tool = self._safe("tool", lambda c: c.spindle_tool_status())
        if tool is not None:
            number = getattr(tool, "number", None)
            snap.tool_number = int(number) if isinstance(number, int) and number >= 0 else None

        messages = self._safe("error_messages", lambda c: c.get_error_messages())
        if messages:
            snap.alarms = [_to_alarm(m) for m in messages]

        # feed_actual / spindle_actual stay None on purpose: LSV2 does not
        # expose them. The UI shows "—" and the state machine falls back to
        # program state, which is the honest behaviour.
        return snap

    def _safe(self, key: str, call):
        """Run a probed-as-working call; a later failure downgrades it to off."""
        if not self._can.get(key, False):
            return None
        try:
            return call(self._client)
        except Exception:  # noqa: BLE001
            # A read that used to work just failed. Treat it as a dropped link
            # so the collector reconnects rather than logging junk.
            raise DriverError(f"'{key}' okunamadı — bağlantı kopmuş olabilir")

    def _derive_exec_state(self, mode, pgm, const) -> ExecState:
        """Map pyLSV2's two separate notions onto our single exec_state.

        `execution_state` is the operating *mode* (manual, MDI, automatic);
        `program_status` is whether the program is actually advancing. Manual
        and MDI mean somebody is setting up, whatever the program says.
        """
        try:
            exec_enum = const.ExecState
            pgm_enum = const.PgmState
        except AttributeError:
            return ExecState.UNKNOWN

        if mode in (exec_enum.MANUAL, exec_enum.MDI, exec_enum.PASS_REFERENCES):
            return ExecState.MANUAL

        if pgm is None:
            return ExecState.UNKNOWN
        if pgm == pgm_enum.STARTED:
            return ExecState.RUNNING
        if pgm == pgm_enum.ERROR:
            return ExecState.ERROR
        if pgm == pgm_enum.INTERRUPTED:
            return ExecState.INTERRUPTED
        if pgm == pgm_enum.UNDEFINED:
            return ExecState.UNKNOWN
        # STOPPED, FINISHED, CANCELLED, IDLE, ERROR_CLEARED
        return ExecState.STOPPED


# ---------------------------------------------------------------- helpers


def _as_float(value) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _basename(path: Optional[str]) -> Optional[str]:
    """TNC paths look like TNC:\\nc_prog\\PARCA_12.H; show just the file."""
    if not path:
        return None
    text = str(path).replace("\\", "/")
    return text.rsplit("/", 1)[-1] or text


def _to_alarm(message) -> Alarm:
    e_class = getattr(message, "e_class", -1)
    e_group = getattr(message, "e_group", -1)
    e_number = getattr(message, "e_number", -1)
    text = (getattr(message, "e_text", "") or "").strip() or "Açıklama yok"
    return Alarm(code=f"{e_class}.{e_group}.{e_number}", text=text, severity="error")
