from __future__ import annotations

from typing import Any

from .base import MeasurementPoint, MeasurementProtocol


class ContactCheckProtocol(MeasurementProtocol):
    def __init__(self, *, states: list[str], threshold_ohm: float = 1e6, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.states = states
        self.threshold_ohm = threshold_ohm
        self._require_positive(self.threshold_ohm, "threshold_ohm", allow_zero=True)

    def measure_point(self, temperature_k: float | None = None, field_t: float | None = None) -> MeasurementPoint:
        results: dict[str, float] = {}
        flags: dict[str, str] = {}
        for state in self.states:
            self._require_state_exists(state)
            _channels = self._apply_measurement_state(state, current_sign=1.0, measure_kind="longitudinal")
            raw = self._read_average_dc("M1")
            value = abs(raw["value"])
            results[state] = value
            flags[state] = "open_or_high" if value > self.threshold_ohm else "ok"
        return MeasurementPoint(
            timestamp=self._timestamp(),
            sample_id=self.sample_id,
            protocol="contact_check",
            geometry=self.contact_map.name,
            state=",".join(self.states),
            reciprocal_state=None,
            temperature_k=temperature_k,
            field_t=field_t,
            raw=results,
            derived={"contact_quality_flag": flags},
            metadata={},
        )
