from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, field
import logging
import time
from typing import Any

from ..exceptions import MatrixSwitchError
from ..instruments.daq6510_7709 import DAQ6510Controller
from .contact_map import ContactMap
from .safety import switching_log_entry, validate_contact_map_state

LOGGER = logging.getLogger(__name__)


@dataclass
class Matrix7709:
    controller: Any
    contact_map: ContactMap | None = None
    m81: Any | None = None
    settle_s: float = 0.05
    switching_log: list[dict[str, Any]] = field(default_factory=list)
    closed_channels: set[int] = field(default_factory=set)

    @classmethod
    def from_config(cls, config: dict[str, Any], contact_map: ContactMap | None = None, m81: Any | None = None) -> "Matrix7709":
        daq_cfg = config.get("instruments", {}).get("daq6510", config.get("daq6510", {}))
        resource = daq_cfg.get("resource", "MOCK::DAQ6510")
        if resource.startswith("MOCK"):
            from ..instruments.mock import MockMatrix7709

            controller = MockMatrix7709.from_config(config)
        else:
            controller = DAQ6510Controller.from_config(config)
        return cls(controller=controller, contact_map=contact_map, m81=m81, settle_s=daq_cfg.get("settle_s", 0.05))

    def open_all(self) -> None:
        LOGGER.info("Opening all matrix relays")
        if hasattr(self.controller, "write"):
            self.controller.write("ROUT:OPEN:ALL")
        else:
            self.controller.open_all()
        self.closed_channels.clear()

    def close_channels(self, channels: list[int]) -> None:
        LOGGER.info("Closing matrix channels: %s", channels)
        if hasattr(self.controller, "write"):
            joined = ",".join(str(ch) for ch in channels)
            self.controller.write(f"ROUT:MULT:CLOS (@{joined})")
        else:
            self.controller.close_channels(channels)
        self.closed_channels = set(channels)

    def apply_state(self, state_name: str, override_source_enabled: bool = False, reenable_sources: bool = False) -> list[int]:
        if self.contact_map is None:
            raise MatrixSwitchError("No ContactMap attached to Matrix7709")
        active_sources = bool(self.m81 and self.m81.any_source_enabled())
        validation = validate_contact_map_state(self.contact_map, state_name, active_sources=active_sources, override=override_source_enabled)
        if not validation.ok:
            raise MatrixSwitchError(validation.reason)
        state = self.contact_map.get_state(state_name)
        if self.m81:
            self.m81.disable_all_sources()
        self.open_all()
        time.sleep(self.settle_s)
        channels = state["relay_channels"]
        self.close_channels(channels)
        time.sleep(self.settle_s)
        self.switching_log.append(switching_log_entry(state_name, channels))
        if hasattr(self.controller, "apply_state"):
            self.controller.apply_state(state_name, channels)
        if reenable_sources:
            LOGGER.warning("Matrix7709.apply_state(reenable_sources=True) is deprecated and ignored for hardware safety")
        return channels

    def emergency_stop(self) -> None:
        self.open_all()

    def status_snapshot(self) -> dict[str, Any]:
        controller_snapshot: dict[str, Any] = {}
        if hasattr(self.controller, "status_snapshot"):
            try:
                controller_snapshot = dict(self.controller.status_snapshot())
            except Exception:
                controller_snapshot = {}
        elif hasattr(self.controller, "closed_channels_list"):
            try:
                controller_snapshot["closed_channels"] = self.controller.closed_channels_list()
            except Exception:
                controller_snapshot["closed_channels"] = sorted(self.closed_channels)
        return {
            "closed_channels": sorted(self.closed_channels),
            "switching_log_size": len(self.switching_log),
            "controller": controller_snapshot,
        }


class SafeMeasurementSession(AbstractContextManager["SafeMeasurementSession"]):
    def __init__(self, m81: Any, matrix: Matrix7709) -> None:
        self.m81 = m81
        self.matrix = matrix

    def __enter__(self) -> "SafeMeasurementSession":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.m81.disable_all_sources()
        self.matrix.open_all()
        return False
