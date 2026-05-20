from __future__ import annotations

from typing import Any

from .base import MeasurementPoint, MeasurementProtocol


class SecondHarmonicProtocol(MeasurementProtocol):
    def __init__(
        self,
        *,
        state: str,
        current_rms_a: float,
        frequency_hz: float,
        harmonic: int = 2,
        measure_channel: str = "M2",
        source: str = "S1",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.state = state
        self.current_rms_a = current_rms_a
        self.frequency_hz = frequency_hz
        self.harmonic = harmonic
        self.measure_channel = measure_channel
        self.source = source

    def setup(self) -> None:
        self._require_state_exists(self.state)
        self._require_positive(self.current_rms_a, "current_rms_a")
        self._require_positive(self.frequency_hz, "frequency_hz")
        self.m81.configure_ac_current_lockin(
            source=self.source,
            current_rms_a=self.current_rms_a,
            frequency_hz=self.frequency_hz,
            measure_channels=[self.measure_channel],
            harmonic=self.harmonic,
        )

    def measure_point(self, temperature_k: float | None = None, field_t: float | None = None) -> MeasurementPoint:
        channels, raw = self._measure_with_source_enabled(
            state_name=self.state,
            source=self.source,
            measure_channel=self.measure_channel,
            measure_kind="transverse",
            current_sign=1.0,
            lockin=True,
        )
        return MeasurementPoint(
            timestamp=self._timestamp(),
            sample_id=self.sample_id,
            protocol="second_harmonic",
            geometry=self.contact_map.name,
            state=self.state,
            reciprocal_state=self.contact_map.states[self.state].get("reciprocal"),
            temperature_k=temperature_k,
            field_t=field_t,
            source_current_a_rms=self.current_rms_a,
            source_current_a_peak=self.current_rms_a * 2**0.5,
            frequency_hz=self.frequency_hz,
            harmonic=self.harmonic,
            raw={
                "X_2omega": raw["x"],
                "Y_2omega": raw["y"],
                "R_2omega": raw["r"],
                "theta_2omega": raw["theta_deg"],
            },
            derived={"matrix_relay_channels": channels},
            metadata={"measure_channel": self.measure_channel, "source_channel": self.source},
        )
