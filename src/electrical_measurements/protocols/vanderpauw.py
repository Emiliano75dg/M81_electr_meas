from __future__ import annotations

from typing import Any

from ..analysis.reciprocity import reciprocity_error
from ..analysis.vanderpauw import solve_vanderpauw_sheet_resistance
from .base import MeasurementPoint, MeasurementProtocol


class VanDerPauwProtocol(MeasurementProtocol):
    def __init__(
        self,
        *,
        states: list[str] | None = None,
        current_rms_a: float = 10e-6,
        frequency_hz: float = 13.7,
        measure_channel: str = "M1",
        source: str = "S1",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.states = states or ["I_AB_V_CD", "I_BC_V_DA", "I_CD_V_AB", "I_DA_V_BC"]
        self.current_rms_a = current_rms_a
        self.frequency_hz = frequency_hz
        self.measure_channel = measure_channel
        self.source = source

    def setup(self) -> None:
        self._require_positive(self.current_rms_a, "current_rms_a")
        self._require_positive(self.frequency_hz, "frequency_hz")
        for state in self.states:
            self._require_state_exists(state)
        self.m81.configure_ac_current_lockin(
            source=self.source,
            current_rms_a=self.current_rms_a,
            frequency_hz=self.frequency_hz,
            measure_channels=[self.measure_channel],
            harmonic=1,
        )

    def measure_point(self, temperature_k: float | None = None, field_t: float | None = None) -> MeasurementPoint:
        resistances: dict[str, float] = {}
        raw_measurements: dict[str, float] = {}
        reciprocity_checks: dict[str, dict[str, float | None]] = {}
        for state in self.states:
            _channels, raw = self._measure_with_source_enabled(
                state_name=state,
                source=self.source,
                measure_channel=self.measure_channel,
                measure_kind="longitudinal",
                current_sign=1.0,
                lockin=True,
            )
            resistance = raw["x"] / self.current_rms_a
            resistances[state] = resistance
            raw_measurements[state] = raw["x"]
        for state in self.states:
            reciprocal_name = self.contact_map.get_state_name(state, "reciprocal")
            if reciprocal_name and reciprocal_name in resistances and state in resistances:
                reciprocity_checks[f"{state}__{reciprocal_name}"] = reciprocity_error(resistances[state], resistances[reciprocal_name])
        rs = solve_vanderpauw_sheet_resistance(
            resistances.get("I_AB_V_CD", 0.0),
            resistances.get("I_BC_V_DA", 0.0),
        )
        return MeasurementPoint(
            timestamp=self._timestamp(),
            sample_id=self.sample_id,
            protocol="vanderpauw",
            geometry=self.contact_map.name,
            state=",".join(self.states),
            reciprocal_state=None,
            temperature_k=temperature_k,
            field_t=field_t,
            source_current_a_rms=self.current_rms_a,
            source_current_a_peak=self.current_rms_a * 2**0.5,
            frequency_hz=self.frequency_hz,
            harmonic=1,
            raw=raw_measurements,
            derived={
                "sheet_resistance_ohm_sq": rs,
                "reciprocity_checks": reciprocity_checks,
                **{f"{k}_ohm": v for k, v in resistances.items()},
            },
            metadata={"measure_channel": self.measure_channel, "source_channel": self.source},
        )
