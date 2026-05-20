from __future__ import annotations

from typing import Any

from .base import MeasurementPoint, MeasurementProtocol


class MagnetoresistanceProtocol(MeasurementProtocol):
    def __init__(
        self,
        *,
        state: str,
        current_rms_a: float,
        frequency_hz: float,
        lockin: bool = True,
        harmonic: int = 1,
        reverse_current: bool = False,
        measure_channel: str = "M1",
        source: str = "S1",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.state = state
        self.current_rms_a = current_rms_a
        self.frequency_hz = frequency_hz
        self.lockin = lockin
        self.harmonic = harmonic
        self.reverse_current = reverse_current
        self.measure_channel = measure_channel
        self.source = source

    def setup(self) -> None:
        self._require_state_exists(self.state)
        self._require_positive(self.current_rms_a, "current_rms_a")
        if self.lockin:
            self._require_positive(self.frequency_hz, "frequency_hz")
            self.m81.configure_ac_current_lockin(
                source=self.source,
                current_rms_a=self.current_rms_a,
                frequency_hz=self.frequency_hz,
                measure_channels=[self.measure_channel],
                harmonic=self.harmonic,
            )
        else:
            self.m81.configure_dc_current(source=self.source, current_a=self.current_rms_a)
        self._prepare_measure_channel(
            self.measure_channel,
            source=self.source,
            default_lockin=self.lockin,
            default_harmonic=self.harmonic,
        )

    def measure_point(self, temperature_k: float | None = None, field_t: float | None = None) -> MeasurementPoint:
        channels, raw_forward = self._measure_with_source_enabled(
            state_name=self.state,
            source=self.source,
            measure_channel=self.measure_channel,
            measure_kind="longitudinal",
            current_sign=1.0,
            lockin=self.lockin,
        )
        measurements = {"forward": raw_forward}
        reverse_state_name = self.contact_map.get_state_name(self.state, "reverse_current")
        if self.reverse_current and reverse_state_name:
            _channels_reverse, raw_reverse = self._measure_with_source_enabled(
                state_name=reverse_state_name,
                source=self.source,
                measure_channel=self.measure_channel,
                measure_kind="longitudinal",
                current_sign=-1.0,
                lockin=self.lockin,
            )
            measurements["reverse"] = raw_reverse
        current = self.current_rms_a
        raw_value_forward = raw_forward.get("r", raw_forward.get("value"))
        r_forward = raw_value_forward / current if current else None
        rxx = r_forward
        current_reversal_avg = None
        if "reverse" in measurements:
            raw_value_reverse = measurements["reverse"].get("r", measurements["reverse"].get("value"))
            r_reverse = raw_value_reverse / current if current else None
            if r_forward is not None and r_reverse is not None:
                current_reversal_avg = 0.5 * (r_forward - r_reverse)
                rxx = current_reversal_avg
        point = MeasurementPoint(
            timestamp=self._timestamp(),
            sample_id=self.sample_id,
            protocol="magnetoresistance",
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
                "lockin_x": raw_forward.get("x"),
                "lockin_y": raw_forward.get("y"),
                "lockin_r": raw_forward.get("r"),
                "lockin_theta_deg": raw_forward.get("theta_deg"),
                "raw_forward": raw_forward,
                "raw_reverse": measurements.get("reverse"),
            },
            derived={
                "rxx_ohm": rxx,
                "rxx_forward_ohm": r_forward,
                "rxx_current_reversal_avg_ohm": current_reversal_avg,
                "matrix_relay_channels": channels,
            },
            metadata={"measure_channel": self.measure_channel, "source_channel": self.source},
        )
        return point
