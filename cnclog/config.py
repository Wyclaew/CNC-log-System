"""Configuration loading.

The INI file is written in Turkish because the operator and the maintenance
team read it; the attributes on Config are English like the rest of the code.
Every setting has a default, so the program starts with no config file at all.
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

CONFIG_FILENAME = "config.ini"

DEFAULTS = {
    "genel": {
        "makine_id": "TEZGAH-01",
        "makine_adi": "Heidenhain TNC 640",
        "veri_dizini": "veri",
    },
    "toplama": {
        "ornekleme_araligi_sn": "2.0",
        "durus_esigi_sn": "10",
        "log_araligi_sn": "30",
        "hizli_ilerleme_esigi": "5000",
        "saklama_gun": "90",
    },
    "surucu": {
        "tip": "simulator",
        "tnc_ip": "192.168.1.50",
        "tnc_port": "19000",
        "zaman_asimi_sn": "5.0",
    },
    "opcua": {
        "adres": "",
        "kullanici": "",
        "sifre": "",
        "guvenlik": "",
        "node_program_durumu": "",
        "node_calisma_modu": "",
        "node_feed": "",
        "node_spindle": "",
        "node_program_adi": "",
        "node_takim": "",
    },
    "web": {
        "bind": "127.0.0.1",
        "port": "8760",
        "tarayici_ac": "evet",
    },
    "vardiya": {
        "baslangiclar": "08:00, 16:00, 00:00",
    },
}

_TRUE_WORDS = {"evet", "e", "true", "1", "acik", "açık", "yes", "on"}


@dataclass
class Config:
    """Resolved settings. Constructed by :func:`load_config`."""

    # [genel]
    machine_id: str = "TEZGAH-01"
    machine_name: str = "Heidenhain TNC 640"
    data_dir: str = "veri"

    # [toplama]
    sample_interval_s: float = 2.0
    #: Seconds a stop must last before it becomes an event. 0 disables the
    #: threshold so every pause is logged the instant it happens.
    idle_threshold_s: float = 10.0
    #: How often a routine status line is appended to the text log. State
    #: changes are always written immediately regardless of this.
    log_interval_s: float = 30.0
    #: Feed rate above which a move is treated as rapid (G0) when the control
    #: does not report rapid mode directly.
    rapid_feed_threshold: float = 5000.0
    retention_days: int = 90

    # [surucu]
    driver: str = "simulator"
    tnc_ip: str = "192.168.1.50"
    tnc_port: int = 19000
    timeout_s: float = 5.0

    # [opcua] -- only used by the heidenhain_opcua driver (Option 56).
    # Node ids differ per machine and OEM, so they are configured rather than
    # hard-coded; `--test-baglanti` browses the server and lists candidates.
    opcua_url: str = ""
    opcua_user: str = ""
    opcua_password: str = ""
    opcua_security: str = ""
    opcua_nodes: Dict[str, str] = field(default_factory=dict)

    # [web]
    web_bind: str = "127.0.0.1"
    web_port: int = 8760
    open_browser: bool = True

    # [vardiya]
    shift_starts: List[str] = field(default_factory=lambda: ["08:00", "16:00", "00:00"])

    #: Absolute path the config was loaded from, or None when defaults are used.
    source_path: Optional[str] = None
    #: Root the relative data_dir is resolved against.
    base_dir: str = "."

    @property
    def data_path(self) -> str:
        if os.path.isabs(self.data_dir):
            return self.data_dir
        return os.path.join(self.base_dir, self.data_dir)

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_path, "cnclog.db")

    @property
    def log_dir(self) -> str:
        return os.path.join(self.data_path, "loglar")


def _as_bool(raw: str, fallback: bool) -> bool:
    value = (raw or "").strip().lower()
    if not value:
        return fallback
    return value in _TRUE_WORDS


def _as_float(raw: str, fallback: float) -> float:
    try:
        return float(str(raw).strip().replace(",", "."))
    except (TypeError, ValueError):
        return fallback


def _as_int(raw: str, fallback: int) -> int:
    try:
        return int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return fallback


def load_config(path: Optional[str] = None, base_dir: Optional[str] = None) -> Config:
    """Read config.ini if present, falling back to defaults for anything missing.

    A malformed value never stops the program: it falls back to the default so
    a typo in the shop cannot take the logger down mid-shift.
    """
    base = os.path.abspath(base_dir or os.getcwd())
    parser = configparser.ConfigParser()
    parser.read_dict(DEFAULTS)

    source = None
    candidate = path or os.path.join(base, CONFIG_FILENAME)
    if os.path.isfile(candidate):
        try:
            parser.read(candidate, encoding="utf-8")
            source = os.path.abspath(candidate)
        except (configparser.Error, OSError):
            # Keep the defaults rather than refusing to start.
            source = None

    def get(section: str, option: str) -> str:
        return parser.get(section, option, fallback=DEFAULTS[section][option])

    shifts = [s.strip() for s in get("vardiya", "baslangiclar").split(",") if s.strip()]

    cfg = Config(
        machine_id=get("genel", "makine_id").strip() or "TEZGAH-01",
        machine_name=get("genel", "makine_adi").strip() or "Heidenhain TNC 640",
        data_dir=get("genel", "veri_dizini").strip() or "veri",
        sample_interval_s=max(0.2, _as_float(get("toplama", "ornekleme_araligi_sn"), 2.0)),
        idle_threshold_s=max(0.0, _as_float(get("toplama", "durus_esigi_sn"), 10.0)),
        log_interval_s=max(0.0, _as_float(get("toplama", "log_araligi_sn"), 30.0)),
        rapid_feed_threshold=_as_float(get("toplama", "hizli_ilerleme_esigi"), 5000.0),
        retention_days=max(0, _as_int(get("toplama", "saklama_gun"), 90)),
        driver=get("surucu", "tip").strip().lower() or "simulator",
        tnc_ip=get("surucu", "tnc_ip").strip(),
        tnc_port=_as_int(get("surucu", "tnc_port"), 19000),
        timeout_s=_as_float(get("surucu", "zaman_asimi_sn"), 5.0),
        opcua_url=get("opcua", "adres").strip(),
        opcua_user=get("opcua", "kullanici").strip(),
        opcua_password=get("opcua", "sifre").strip(),
        opcua_security=get("opcua", "guvenlik").strip(),
        opcua_nodes={
            key: get("opcua", f"node_{key}").strip()
            for key in (
                "program_durumu",
                "calisma_modu",
                "feed",
                "spindle",
                "program_adi",
                "takim",
            )
            if get("opcua", f"node_{key}").strip()
        },
        web_bind=get("web", "bind").strip() or "127.0.0.1",
        web_port=_as_int(get("web", "port"), 8760),
        open_browser=_as_bool(get("web", "tarayici_ac"), True),
        shift_starts=shifts or ["08:00", "16:00", "00:00"],
        source_path=source,
        base_dir=base,
    )
    return cfg
