from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from ..exceptions import HardwareError

LOGGER = logging.getLogger(__name__)


class M81SCPIFallback:
    """Direct SCPI access for features not exposed by the official driver."""

    def __init__(self, instrument: Any) -> None:
        self.instrument = instrument

    def _query(self, command: str) -> str:
        LOGGER.debug("M81 SCPI query: %s", command)
        if not hasattr(self.instrument, "query"):
            raise HardwareError("Underlying M81 connection does not support direct SCPI query")
        return str(self.instrument.query(command))

    def _command(self, command: str) -> None:
        LOGGER.debug("M81 SCPI command: %s", command)
        if hasattr(self.instrument, "command"):
            self.instrument.command(command)
            return
        if hasattr(self.instrument, "write"):
            self.instrument.write(command)
            return
        raise HardwareError("Underlying M81 connection does not support direct SCPI write")

    def configure_trace_stream(self, channel: str, points: int, interval_s: float) -> None:
        self._command(f"TRAC:CONF {channel},{points},{interval_s}")

    def start_trace(self) -> None:
        self._command("TRAC:STAR")

    def fetch_trace(self) -> str:
        return self._query("TRAC:DATA?")

    def abort_sweep(self) -> None:
        self._command("ABOR")

    def get_error_queue(self) -> str:
        return self._query("SYST:ERR?")

    def get_status_register(self) -> str:
        return self._query("*STB?")

    def parse_trace_response(self, raw: str, channel: str, interval_s: float = 0.1) -> list[dict[str, Any]]:
        cleaned = raw.strip()
        if not cleaned:
            return []
        tokens = [token for token in cleaned.replace("\n", ",").replace(";", ",").split(",") if token.strip()]
        values: list[float] = []
        for token in tokens:
            try:
                values.append(float(token.strip()))
            except ValueError:
                continue
        start = datetime.now(timezone.utc)
        records = []
        for index, value in enumerate(values):
            timestamp = start + timedelta(seconds=index * interval_s)
            records.append(
                {
                    "trace_channel": channel,
                    "trace_index": index,
                    "timestamp": timestamp.isoformat(),
                    "x": value,
                }
            )
        return records
