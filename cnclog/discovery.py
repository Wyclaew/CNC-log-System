"""Finds the Heidenhain control on the network by itself.

The operator should not have to type an IP address. The program looks for a
control the same way you would: try the address we were told about, then the
machine we are running on, then the rest of the local subnet.

Two stages, because they cost very different amounts:

1. A TCP connect to port 19000. Cheap, parallel, tells us "something is
   listening here".
2. An actual LSV2 handshake. Only run on stage-1 hits, because opening a
   protocol session to every address on a shop network would be rude and slow.

Nothing here sends anything to the control beyond what pyLSV2 needs to read
its version block.
"""

from __future__ import annotations

import logging
import os
import re
import socket
import subprocess
from typing import Callable, List, Optional

try:
    from concurrent.futures import ThreadPoolExecutor

    HAVE_POOL = True
except ImportError:  # pragma: no cover - present in every supported Python
    HAVE_POOL = False

LSV2_PORT = 19000
OPCUA_PORT = 4840

#: Per-address timeout for the cheap port check.
PROBE_TIMEOUT = 0.35
#: How many addresses to probe at once. Keeps a /24 sweep under a few seconds
#: without flooding a shop-floor switch.
PROBE_WORKERS = 48

#: Most subnets to sweep. A laptop with VirtualBox, WSL and a VPN can hold
#: half a dozen adapters; sweeping them all would take too long to be useful.
MAX_SUBNETS = 3


def port_open(host: str, port: int, timeout: float = PROBE_TIMEOUT) -> bool:
    """True if something accepts a TCP connection there."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        return True
    except (OSError, socket.timeout):
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass


#: Lines that carry an address in ipconfig / ip / ifconfig output.
_ADDR_LINE = re.compile(r"(?:inet\b|IPv4[^:]*:)\s*(?:addr:)?\s*"
                        r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})")


def _usable(address: str) -> bool:
    if not address or address.startswith(("127.", "0.", "255.")):
        return False
    # 169.254.x is link-local: an interface that failed to get an address.
    return not address.startswith("169.254.")


def _addresses_from_commands() -> List[str]:
    """Every IPv4 address on this machine, by asking the OS.

    The UDP-connect trick below only reveals the interface that carries the
    default route. A TNC 640 programming station commonly runs in VirtualBox
    and answers on a host-only adapter (192.168.56.x) that has no default
    route -- so relying on that trick alone means never finding it.
    """
    found: List[str] = []
    if os.name == "nt":
        commands = [["ipconfig"]]
    else:
        commands = [["ip", "-4", "addr"], ["ifconfig", "-a"], ["ifconfig"]]

    for command in commands:
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        text = result.stdout.decode("utf-8", "replace")
        for match in _ADDR_LINE.finditer(text):
            address = match.group(1)
            if _usable(address) and address not in found:
                found.append(address)
        if found:
            break  # First command that produced anything is enough.
    return found


def local_addresses() -> List[str]:
    """IPv4 addresses this machine holds, best effort and without dependencies."""
    found = _addresses_from_commands()

    # The UDP-connect trick: no packet is sent, but the kernel picks the
    # source address it would use, which is the address on the working NIC.
    # Kept as a fallback for systems where the commands above are absent.
    for probe_target in ("192.168.1.1", "10.0.0.1", "8.8.8.8"):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(0.2)
            sock.connect((probe_target, 9))
            address = sock.getsockname()[0]
            if _usable(address) and address not in found:
                found.append(address)
        except OSError:
            pass
        finally:
            sock.close()

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if _usable(address) and address not in found:
                found.append(address)
    except (OSError, socket.gaierror):
        pass

    return found


def candidate_addresses(configured: Optional[str] = None) -> List[str]:
    """Addresses to try, most likely first.

    Order matters: a configured address should win instantly, and localhost is
    checked early because when this runs on the control's own HEROS desktop the
    control is right here.
    """
    candidates: List[str] = []

    def add(address: str) -> None:
        if address and address not in candidates:
            candidates.append(address)

    if configured:
        add(configured.strip())
    add("127.0.0.1")
    for address in local_addresses():
        add(address)
    return candidates


def subnet_addresses(base: str) -> List[str]:
    """Every host address in the /24 containing `base`, excluding `base`."""
    parts = base.split(".")
    if len(parts) != 4:
        return []
    prefix = ".".join(parts[:3])
    try:
        own = int(parts[3])
    except ValueError:
        own = -1
    return [f"{prefix}.{i}" for i in range(1, 255) if i != own]


def _probe_many(addresses: List[str], port: int) -> List[str]:
    """Port-check a list of addresses, in parallel when possible."""
    if not addresses:
        return []
    if not HAVE_POOL or len(addresses) == 1:
        return [a for a in addresses if port_open(a, port)]

    workers = min(PROBE_WORKERS, len(addresses))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda a: (a, port_open(a, port)), addresses))
    return [address for address, is_open in results if is_open]


#: Set by verify_lsv2 when a control answered but refused us. Read by the
#: caller to explain *why* rather than just "not found".
NO_PRIV_HINT = (
    "Tezgah cevap verdi ama erişimi reddetti (yetki yok).\n"
    "  Dış erişim kapalı. Kontrol ekranında:\n"
    "    1) PROGRAMLAMA moduna geçin (Manual operation'da bu ayar görünmez)\n"
    "    2) MOD tuşuna basın\n"
    "    3) 'External access' / 'Harici erişim' = ON yapın\n"
    "    4) END ile onaylayın"
)


def verify_lsv2(
    host: str, port: int, timeout: float, reason: Optional[list] = None
) -> Optional[str]:
    """Confirm a real Heidenhain control answers here.

    Returns a short description of the control, or None. An open port is not
    proof: an unrelated service could sit on 19000, and connecting the logger
    to it would produce nonsense rather than an error.

    `reason` collects an explanation when a control clearly *is* there but
    turned us away -- the difference between "nothing here" and "it said no"
    is the difference between hunting for an IP and flipping one setting.
    """
    from .drivers.heidenhain_lsv2 import _load_pylsv2, _quiet_pylsv2, close_client

    module = _load_pylsv2()
    if module is None:
        return None
    _quiet_pylsv2()

    client = None
    # pyLSV2 reports the refusal through its logger and then, while failing,
    # logs out -- which resets last_error before we could read it. Capturing
    # the log is the only reliable way to tell "denied" from "not there".
    catcher = _LogCatcher()
    try:
        with catcher:
            client = module.LSV2(host, port=port, timeout=timeout, safe_mode=False)
            client.connect()
            versions = client.versions
        control = (getattr(versions, "control", "") or "").strip()
        software = (getattr(versions, "nc_sw", "") or "").strip()
        label = " ".join(part for part in (control, software) if part)
        return label or "Heidenhain kontrol"
    except Exception:  # noqa: BLE001 - any failure means "not a control"
        if reason is not None and catcher.saw_privilege_error():
            reason.append(f"{host}:{port} — {NO_PRIV_HINT}")
        return None
    finally:
        # Must not leak: this runs once per candidate during a subnet sweep.
        close_client(client)


class _LogCatcher(logging.Handler):
    """Collects pyLSV2's own log lines for the duration of one attempt."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: List[str] = []
        self._saved: List[tuple] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.messages.append(record.getMessage())
        except Exception:  # noqa: BLE001 - logging must never raise here
            pass

    def __enter__(self) -> "_LogCatcher":
        from .drivers.heidenhain_lsv2 import _PYLSV2_LOGGERS

        for name in _PYLSV2_LOGGERS:
            logger = logging.getLogger(name)
            self._saved.append((logger, logger.level))
            logger.setLevel(logging.DEBUG)
            logger.addHandler(self)
        return self

    def __exit__(self, *exc_info) -> None:
        for logger, level in self._saved:
            logger.removeHandler(self)
            logger.setLevel(level)
        self._saved = []

    def saw_privilege_error(self) -> bool:
        """True when the control spoke LSV2 and refused the login.

        Only a real control produces that reply, so this doubles as proof the
        machine was found -- the operator needs a setting, not an IP hunt.
        """
        for message in self.messages:
            upper = message.upper()
            if "NO_PRIV" in upper or "T_ER_NO_PRIV" in upper:
                return True
            if "ERROR LOGGING IN" in upper:
                return True
        return False


class Found:
    """A control that answered."""

    def __init__(self, host: str, port: int, description: str) -> None:
        self.host = host
        self.port = port
        self.description = description

    def __str__(self) -> str:
        return f"{self.description} ({self.host}:{self.port})"


def find_control(
    configured_ip: Optional[str] = None,
    port: int = LSV2_PORT,
    timeout: float = 5.0,
    scan_subnet: bool = True,
    on_progress: Optional[Callable[[str], None]] = None,
    should_continue: Optional[Callable[[], bool]] = None,
    refusals: Optional[list] = None,
) -> Optional[Found]:
    """Look for a Heidenhain control and return the first one that answers.

    `should_continue` is checked between steps so a shutdown does not have to
    wait out a subnet sweep. Without it, Ctrl+C during discovery would leave
    the collector thread running while the database is being closed.

    `refusals` collects hosts that spoke LSV2 but denied access -- those are
    found machines with a setting to fix, not missing ones to keep hunting.
    """
    if refusals is None:
        refusals = []

    def say(message: str) -> None:
        if on_progress:
            on_progress(message)

    def carry_on() -> bool:
        return should_continue() if should_continue else True

    # Stage 1a: the addresses we have a reason to believe in.
    likely = candidate_addresses(configured_ip)
    say(f"Bilinen adresler deneniyor ({len(likely)} adet)…")
    for host in _probe_many(likely, port):
        if not carry_on():
            return None
        say(f"{host}:{port} açık, doğrulanıyor…")
        description = verify_lsv2(host, port, timeout, reason=refusals)
        if description:
            return Found(host, port, description)

    if not scan_subnet or not carry_on():
        return None

    # Stage 1b: sweep every subnet this machine is on, not just the one with
    # the default route -- a virtualised control often sits on a host-only
    # adapter. Capped so a machine with many virtual adapters cannot turn this
    # into a minutes-long scan.
    for own in local_addresses()[:MAX_SUBNETS]:
        if not carry_on():
            return None
        others = subnet_addresses(own)
        if not others:
            continue
        say(f"{own} ağı taranıyor ({len(others)} adres)…")
        for host in _probe_many(others, port):
            if not carry_on():
                return None
            say(f"{host}:{port} açık, doğrulanıyor…")
            description = verify_lsv2(host, port, timeout, reason=refusals)
            if description:
                return Found(host, port, description)

    return None
