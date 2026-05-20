from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import logging
import time
from typing import Any

LOGGER = logging.getLogger(__name__)


@dataclass
class MeasurementPoint:
    timestamp: str
    sample_id: str
    protocol: str
    geometry: str
    state: str
    reciprocal_state: str | None
    temperature_k: float | None
    field_t: float | None
    source_current_a_rms: float | None = None
    source_current_a_peak: float | None = None
    source_voltage_v_rms: float | None = None
    source_voltage_v_peak: float | None = None
    frequency_hz: float | None = None
    harmonic: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    derived: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record.update(self.raw)
        record.update(self.derived)
        record.update(self.metadata)
        return record


class MeasurementProtocol:
    def __init__(
        self,
        *,
        m81: Any,
        matrix: Any,
        contact_map: Any,
        sample_id: str = "sample",
        settle_s: float = 0.1,
        averaging: int = 1,
        dry_run: bool = False,
    ) -> None:
        self.m81 = m81
        self.matrix = matrix
        self.contact_map = contact_map
        self.sample_id = sample_id
        self.settle_s = settle_s
        self.averaging = averaging
        self.dry_run = dry_run
        self._validate_common_inputs()

    def setup(self) -> None:
        return None

    def teardown(self) -> None:
        self.m81.disable_all_sources()

    def emergency_stop(self) -> None:
        self.m81.disable_all_sources()
        self.matrix.open_all()

    def measure_point(self, temperature_k: float | None = None, field_t: float | None = None) -> MeasurementPoint:
        raise NotImplementedError

    def _timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _validate_common_inputs(self) -> None:
        if self.settle_s < 0:
            raise ValueError("settle_s must be >= 0")
        if self.averaging < 1:
            raise ValueError("averaging must be >= 1")

    def _sleep(self) -> None:
        if self.settle_s > 0:
            time.sleep(self.settle_s)

    def _set_measurement_context(self, state_name: str, current_sign: float = 1.0, measure_kind: str = "longitudinal") -> None:
        if hasattr(self.m81, "set_measurement_context"):
            self.m81.set_measurement_context(
                protocol=self.__class__.__name__,
                state=state_name,
                group=self.contact_map.states.get(state_name, {}).get("group"),
                measure_kind=measure_kind,
                current_sign=current_sign,
            )

    def _read_average_lockin(self, measure_channel: str) -> dict[str, Any]:
        samples = [self.m81.read_lockin(measure_channel) for _ in range(max(self.averaging, 1))]
        keys = ["x", "y", "r", "theta_deg", "frequency_hz", "harmonic", "resistance_ohm"]
        averaged: dict[str, Any] = {"timestamp": samples[-1]["timestamp"]}
        for key in keys:
            values = [sample.get(key) for sample in samples if sample.get(key) is not None]
            if not values:
                averaged[key] = None
            elif isinstance(values[0], (int, float)):
                averaged[key] = sum(float(v) for v in values) / len(values)
            else:
                averaged[key] = values[-1]
        return averaged

    def _read_average_dc(self, measure_channel: str) -> dict[str, Any]:
        samples = [self.m81.read_dc(measure_channel) for _ in range(max(self.averaging, 1))]
        values = [float(sample["value"]) for sample in samples]
        return {"value": sum(values) / len(values), "timestamp": samples[-1]["timestamp"]}

    def _resolve_channel_measure_mode(self, measure_channel: str, default_lockin: bool = True) -> str:
        requested = "lockin" if default_lockin else "dc"
        if hasattr(self.m81, "resolve_measure_mode"):
            resolved = self.m81.resolve_measure_mode(measure_channel, "auto")
            if resolved != "auto":
                return resolved
        return requested

    def _resolve_channel_harmonic(self, measure_channel: str, default_harmonic: int = 1) -> int:
        if hasattr(self.m81, "get_preferred_measure_harmonic"):
            preferred = self.m81.get_preferred_measure_harmonic(measure_channel)
            if preferred is not None:
                return int(preferred)
        if hasattr(self.m81, "resolve_measure_harmonic"):
            return int(self.m81.resolve_measure_harmonic(measure_channel, None))
        return int(default_harmonic)

    def _prepare_measure_channel(
        self,
        measure_channel: str,
        *,
        source: str,
        default_lockin: bool = True,
        default_harmonic: int = 1,
    ) -> tuple[str, int | None]:
        mode = self._resolve_channel_measure_mode(measure_channel, default_lockin=default_lockin)
        if mode == "dc":
            self.m81.configure_dc_measure(measure_channel)
            return mode, None
        harmonic = self._resolve_channel_harmonic(measure_channel, default_harmonic=default_harmonic)
        self.m81.configure_lockin_measure(
            measure_channel=measure_channel,
            harmonic=harmonic,
            reference_source=source,
        )
        return "lockin", harmonic

    def _require_positive(self, value: float | None, name: str, allow_zero: bool = False) -> None:
        if value is None:
            raise ValueError(f"{name} is required")
        if allow_zero:
            if value < 0:
                raise ValueError(f"{name} must be >= 0")
            return
        if value <= 0:
            raise ValueError(f"{name} must be > 0")

    def _require_state_exists(self, state_name: str) -> None:
        if state_name not in self.contact_map.states:
            raise ValueError(f"Unknown state: {state_name}")

    def _apply_measurement_state(self, state_name: str, *, current_sign: float = 1.0, measure_kind: str = "longitudinal") -> list[int]:
        self._require_state_exists(state_name)
        LOGGER.info("Applying state %s for %s", state_name, self.__class__.__name__)
        self._set_measurement_context(state_name, current_sign=current_sign, measure_kind=measure_kind)
        return self.matrix.apply_state(state_name)

    def _measure_with_source_enabled(
        self,
        *,
        state_name: str,
        source: str,
        measure_channel: str,
        measure_kind: str,
        current_sign: float = 1.0,
        lockin: bool = True,
    ) -> tuple[list[int], dict[str, Any]]:
        channels = self._apply_measurement_state(state_name, current_sign=current_sign, measure_kind=measure_kind)
        LOGGER.info(
            "Starting measurement protocol=%s state=%s source=%s channel=%s lockin=%s",
            self.__class__.__name__,
            state_name,
            source,
            measure_channel,
            lockin,
        )
        try:
            self.m81.enable_source(source)
            self._sleep()
            resolved_mode = self._resolve_channel_measure_mode(measure_channel, default_lockin=lockin)
            reading = self._read_average_lockin(measure_channel) if resolved_mode == "lockin" else self._read_average_dc(measure_channel)
            LOGGER.info(
                "Completed measurement protocol=%s state=%s channel=%s",
                self.__class__.__name__,
                state_name,
                measure_channel,
            )
            return channels, reading
        finally:
            self.m81.disable_all_sources()

    def _measure_channels_with_source_enabled(
        self,
        *,
        state_name: str,
        source: str,
        measure_channels: list[str],
        measure_kind: str,
        current_sign: float = 1.0,
        lockin: bool = True,
    ) -> tuple[list[int], dict[str, dict[str, Any]]]:
        if not measure_channels:
            raise ValueError("measure_channels must not be empty")
        channels = self._apply_measurement_state(state_name, current_sign=current_sign, measure_kind=measure_kind)
        LOGGER.info(
            "Starting multi-channel measurement protocol=%s state=%s source=%s channels=%s lockin=%s",
            self.__class__.__name__,
            state_name,
            source,
            measure_channels,
            lockin,
        )
        try:
            self.m81.enable_source(source)
            self._sleep()
            readings = {
                channel: (
                    self._read_average_lockin(channel)
                    if self._resolve_channel_measure_mode(channel, default_lockin=lockin) == "lockin"
                    else self._read_average_dc(channel)
                )
                for channel in measure_channels
            }
            LOGGER.info(
                "Completed multi-channel measurement protocol=%s state=%s channels=%s",
                self.__class__.__name__,
                state_name,
                measure_channels,
            )
            return channels, readings
        finally:
            self.m81.disable_all_sources()
