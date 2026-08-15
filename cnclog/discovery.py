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

import socket
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


def local_addresses() -> List[str]:
    """IPv4 addresses this machine holds, best effort and without dependencies."""
    found = []

    # The UDP-connect trick: no packet is sent, but the kernel picks the
    # source address it would use, which is the address on the working NIC.
    for probe_target in ("192.168.1.1", "10.0.0.1", "8.8.8.8"):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(0.2)
            sock.connect((probe_target, 9))
            address = sock.getsockname()[0]
            if address and not address.startswith("127.") and address not in found:
                found.append(address)
        except OSError:
            pass
        finally:
            sock.close()

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if address and not address.startswith("127.") and address not in found:
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


def verify_lsv2(host: str, port: int, timeout: float) -> Optional[str]:
    """Confirm a real Heidenhain control answers here.

    Returns a short description of the control, or None. An open port is not
    proof: an unrelated service could sit on 19000, and connecting the logger
    to it would produce nonsense rather than an error.
    """
    from .drivers.heidenhain_lsv2 import _load_pylsv2, _quiet_pylsv2, close_client

    module = _load_pylsv2()
    if module is None:
        return None
    _quiet_pylsv2()

    client = None
    try:
        client = module.LSV2(host, port=port, timeout=timeout, safe_mode=False)
        client.connect()
        versions = client.versions
        control = (getattr(versions, "control", "") or "").strip()
        software = (getattr(versions, "nc_sw", "") or "").strip()
        label = " ".join(part for part in (control, software) if part)
        return label or "Heidenhain kontrol"
    except Exception:  # noqa: BLE001 - any failure means "not a control"
        return None
    finally:
        # Must not leak: this runs once per candidate during a subnet sweep.
        close_client(client)


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
) -> Optional[Found]:
    """Look for a Heidenhain control and return the first one that answers.

    `should_continue` is checked between steps so a shutdown does not have to
    wait out a subnet sweep. Without it, Ctrl+C during discovery would leave
    the collector thread running while the database is being closed.
    """

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
        description = verify_lsv2(host, port, timeout)
        if description:
            return Found(host, port, description)

    if not scan_subnet or not carry_on():
        return None

    # Stage 1b: sweep the subnet we are actually on.
    for own in local_addresses():
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
            description = verify_lsv2(host, port, timeout)
            if description:
                return Found(host, port, description)

    return None
