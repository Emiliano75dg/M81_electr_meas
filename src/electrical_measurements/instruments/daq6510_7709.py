from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Any

LOGGER = logging.getLogger(__name__)


@dataclass
class DAQ6510Controller:
    resource_name: str
    connection: Any

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "DAQ6510Controller":
        daq_cfg = config.get("instruments", {}).get("daq6510", config.get("daq6510", {}))
        resource_name = daq_cfg.get("resource", "MOCK::DAQ6510")
        import pyvisa

        rm = pyvisa.ResourceManager()
        connection = rm.open_resource(resource_name)
        return cls(resource_name=resource_name, connection=connection)

    def write(self, command: str) -> None:
        LOGGER.debug("DAQ6510 write: %s", command)
        self.connection.write(command)

    def query(self, command: str) -> str:
        LOGGER.debug("DAQ6510 query: %s", command)
        return str(self.connection.query(command))

    def closed_channels(self) -> str:
        return self.query("ROUT:CLOS?")

    def closed_channels_list(self) -> list[int]:
        raw = self.closed_channels()
        return [int(item) for item in re.findall(r"\d+", raw)]

    def status_snapshot(self) -> dict[str, Any]:
        try:
            closed = self.closed_channels_list()
        except Exception:
            closed = []
        return {
            "resource_name": self.resource_name,
            "closed_channels": closed,
        }
