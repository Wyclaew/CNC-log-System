"""Driver lookup by config name.

Imports are deferred so that a driver whose optional dependency is missing --
the OPC UA one needs `asyncua` -- cannot break startup for everyone else.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .base import Driver, DriverError

#: config name -> (module, class). Kept as strings for the deferred import.
_DRIVERS: Dict[str, tuple] = {
    "auto": (".auto", "AutoDriver"),
    "simulator": (".simulator", "SimulatorDriver"),
    "heidenhain_lsv2": (".heidenhain_lsv2", "HeidenhainLsv2Driver"),
    "heidenhain_opcua": (".heidenhain_opcua", "HeidenhainOpcUaDriver"),
}

#: Turkish descriptions used in error messages and in the UI.
DRIVER_LABELS: Dict[str, str] = {
    "auto": "Otomatik — tezgahı ağda kendi bulur (varsayılan)",
    "simulator": "Simülatör (sahte tezgah — sadece deneme için)",
    "heidenhain_lsv2": "Heidenhain LSV2 (TNC 640, adres elle verilir)",
    "heidenhain_opcua": "Heidenhain OPC UA NC Server (Opsiyon 56 gerekir)",
}


def available_drivers() -> List[str]:
    return sorted(_DRIVERS)


def create_driver(name: str, cfg: Any) -> Driver:
    """Build the driver named in config.ini, or raise DriverError explaining why not."""
    key = (name or "").strip().lower()
    if key not in _DRIVERS:
        raise DriverError(
            f"Bilinmeyen sürücü: '{name}'. "
            f"Kullanılabilir sürücüler: {', '.join(available_drivers())}"
        )

    module_name, class_name = _DRIVERS[key]
    try:
        from importlib import import_module

        module = import_module(module_name, package=__package__)
    except ImportError as exc:
        raise DriverError(
            f"'{key}' sürücüsü yüklenemedi: {exc}. "
            f"({DRIVER_LABELS.get(key, '')})"
        ) from exc

    driver_class = getattr(module, class_name)
    return driver_class(cfg)
