"""Heidenhain OPC UA NC Server (Option 56) — the way to get real F and S.

STATUS: this driver is written but UNTESTED against a real control. There was
no licensed machine available while it was built, so treat the node ids as a
starting point, not as verified truth. `--test-baglanti` browses the server and
prints what it actually finds, which is how you fill in config.ini for your
specific machine.

WHY THIS DRIVER EXISTS
----------------------
LSV2 gives program state, overrides, program name and alarms, but not the live
feed rate and spindle speed. The OPC UA NC Server does. If Option 56 is
licensed on the machine, prefer this driver.

DEPENDENCY
----------
Needs the `asyncua` package, which is NOT bundled: unlike pyLSV2 it pulls in
cryptography and other compiled dependencies, which cannot be copied into a
locked-down HEROS system anyway. If it is missing, this driver refuses to load
with an explanation and the rest of the program is unaffected.

    pip3 install asyncua

NODE IDS
--------
Heidenhain's server exposes an address space that varies by control generation
and by what the machine OEM configured. Rather than hard-coding guesses, the
node ids live in config.ini under [opcua]. Run:

    python3 -m cnclog --surucu heidenhain_opcua --test-baglanti

to connect, browse the server and print the readable variables with their node
ids, then paste the ones you want into config.ini.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..model import Alarm, ExecState, Snapshot
from .base import Driver, DriverError

#: Heidenhain's OPC UA NC Server listens here by default.
DEFAULT_PORT = 4840

#: Substrings used when browsing to guess what a variable is for. Only a hint
#: for the operator running --test-baglanti; nothing is read automatically
#: based on a guess.
_HINTS: List[Tuple[str, str]] = [
    ("feed", "feed"),
    ("vorschub", "feed"),
    ("spindle", "spindle"),
    ("spindel", "spindle"),
    ("speed", "spindle"),
    ("drehzahl", "spindle"),
    ("state", "program_durumu"),
    ("status", "program_durumu"),
    ("zustand", "program_durumu"),
    ("mode", "calisma_modu"),
    ("betriebsart", "calisma_modu"),
    ("program", "program_adi"),
    ("tool", "takim"),
    ("werkzeug", "takim"),
]


def _load_client():
    """Return a synchronous OPC UA client class, or None if unavailable."""
    try:
        from asyncua.sync import Client  # type: ignore[import-not-found]

        return Client
    except ImportError:
        pass
    try:
        # The older `opcua` package exposes a compatible-enough sync client.
        from opcua import Client  # type: ignore[import-not-found,no-redef]

        return Client
    except ImportError:
        return None


class HeidenhainOpcUaDriver(Driver):
    """Read-only OPC UA client. Reads only the nodes named in config.ini."""

    name = "heidenhain_opcua"

    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg
        self._client_class = _load_client()
        self._client = None
        self._nodes: Dict[str, Any] = {}

        if self._client_class is None:
            raise DriverError(
                "OPC UA sürücüsü için 'asyncua' paketi gerekli ama kurulu değil.\n"
                "Kurmak için:  pip3 install asyncua\n"
                "Bu paket programla birlikte gelmez (derlenmiş bağımlılıkları var).\n"
                "Alternatif: --surucu heidenhain_lsv2 ile ücretsiz LSV2 yolunu "
                "kullanabilirsiniz\n(gerçek F/S değerleri hariç her şeyi okur)."
            )

    # ---------------------------------------------------------------- connect

    @property
    def url(self) -> str:
        if self.cfg.opcua_url:
            return self.cfg.opcua_url
        if self.cfg.tnc_ip:
            return f"opc.tcp://{self.cfg.tnc_ip}:{DEFAULT_PORT}"
        return ""

    def describe(self) -> str:
        return f"Heidenhain OPC UA NC Server @ {self.url or 'adres tanımsız'}"

    def connect(self) -> None:
        if not self.url:
            raise DriverError(
                "OPC UA adresi tanımlı değil. config.ini içinde [opcua] adres "
                "veya [surucu] tnc_ip satırını doldurun."
            )
        try:
            client = self._client_class(self.url, timeout=self.cfg.timeout_s)
            if self.cfg.opcua_user:
                client.set_user(self.cfg.opcua_user)
                if self.cfg.opcua_password:
                    client.set_password(self.cfg.opcua_password)
            if self.cfg.opcua_security:
                client.set_security_string(self.cfg.opcua_security)
            client.connect()
        except Exception as exc:  # noqa: BLE001
            raise DriverError(
                f"OPC UA sunucusuna bağlanılamadı ({self.url}): {exc}\n"
                "Opsiyon 56 lisanslı mı ve OPC UA NC Server açık mı kontrol edin."
            ) from exc

        self._client = client
        self._resolve_nodes()

        if not self._nodes:
            raise DriverError(
                "OPC UA bağlantısı kuruldu ama config.ini içinde hiçbir node "
                "tanımlı değil.\n"
                "Sunucudaki değişkenleri listelemek için:\n"
                "  python3 -m cnclog --surucu heidenhain_opcua --test-baglanti\n"
                "Çıkan node kimliklerini config.ini [opcua] bölümüne yazın."
            )

    def _resolve_nodes(self) -> None:
        self._nodes = {}
        for key, node_id in (self.cfg.opcua_nodes or {}).items():
            try:
                self._nodes[key] = self._client.get_node(node_id)
            except Exception:  # noqa: BLE001 - a bad id disables just that field
                continue

    def disconnect(self) -> None:
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:  # noqa: BLE001
                pass
        self._client = None
        self._nodes = {}

    # ------------------------------------------------------------------- read

    def read(self) -> Snapshot:
        if self._client is None:
            raise DriverError("Bağlantı kurulmadı")

        snap = Snapshot()
        values = {}
        for key, node in self._nodes.items():
            try:
                values[key] = node.get_value()
            except Exception as exc:  # noqa: BLE001
                raise DriverError(
                    f"'{key}' node'u okunamadı — bağlantı kopmuş olabilir: {exc}"
                ) from exc

        snap.feed_actual = _as_float(values.get("feed"))
        snap.spindle_actual = _as_float(values.get("spindle"))
        name = values.get("program_adi")
        snap.program_name = _basename(str(name)) if name else None
        tool = values.get("takim")
        snap.tool_number = int(tool) if isinstance(tool, (int, float)) else None
        snap.exec_state = _derive_exec_state(
            values.get("program_durumu"), values.get("calisma_modu")
        )
        return snap

    # -------------------------------------------------------------- discovery

    def browse_variables(self, limit: int = 400) -> List[Dict[str, str]]:
        """Walk the address space and list readable variables.

        Used by --test-baglanti so the operator can see what this particular
        machine exposes and fill config.ini in accordingly.
        """
        if self._client is None:
            raise DriverError("Bağlantı kurulmadı")

        found: List[Dict[str, str]] = []
        seen = set()

        def walk(node, depth: int, path: str) -> None:
            if len(found) >= limit or depth > 6:
                return
            try:
                children = node.get_children()
            except Exception:  # noqa: BLE001
                return
            for child in children:
                if len(found) >= limit:
                    return
                try:
                    node_id = child.nodeid.to_string()
                    if node_id in seen:
                        continue
                    seen.add(node_id)
                    name = child.get_browse_name().Name
                    full = f"{path}/{name}" if path else name
                    value = None
                    try:
                        value = child.get_value()
                    except Exception:  # noqa: BLE001 - folders have no value
                        pass
                    if value is not None:
                        found.append(
                            {
                                "yol": full,
                                "node_id": node_id,
                                "deger": str(value)[:60],
                                "tahmin": _guess(full),
                            }
                        )
                    walk(child, depth + 1, full)
                except Exception:  # noqa: BLE001
                    continue

        try:
            walk(self._client.get_objects_node(), 0, "")
        except Exception as exc:  # noqa: BLE001
            raise DriverError(f"Adres uzayı taranamadı: {exc}") from exc
        return found


# ---------------------------------------------------------------- helpers


def _guess(path: str) -> str:
    lowered = path.lower()
    for needle, key in _HINTS:
        if needle in lowered:
            return key
    return ""


def _as_float(value) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _basename(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    text = str(path).replace("\\", "/")
    return text.rsplit("/", 1)[-1] or text


def _derive_exec_state(program_state, mode) -> ExecState:
    """Best-effort mapping. Server values differ per control, so this is
    intentionally forgiving and falls back to UNKNOWN rather than guessing."""
    text = f"{program_state} {mode}".lower()
    if any(word in text for word in ("manual", "handbetrieb", "mdi", "jog")):
        return ExecState.MANUAL
    if any(word in text for word in ("error", "alarm", "fehler")):
        return ExecState.ERROR
    if any(word in text for word in ("run", "start", "active", "automatic", "laufen")):
        return ExecState.RUNNING
    if any(word in text for word in ("interrupt", "hold", "unterbrochen")):
        return ExecState.INTERRUPTED
    if any(word in text for word in ("stop", "idle", "finish", "cancel", "gestoppt")):
        return ExecState.STOPPED
    if isinstance(program_state, bool):
        return ExecState.RUNNING if program_state else ExecState.STOPPED
    return ExecState.UNKNOWN
