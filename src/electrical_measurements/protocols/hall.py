from __future__ import annotations

from typing import Any

from ..analysis.hall import compute_hall_density, compute_mobility
from .base import MeasurementPoint, MeasurementProtocol


class HallProtocol(MeasurementProtocol):
    def __init__(
        self,
        *,
        state: str = "hallbar_forward",
        current_rms_a: float = 10e-6,
        frequency_hz: float = 13.7,
        harmonic: int = 1,
        measure_channel: str = "M2",
        source: str = "S1",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.state = state
        self.current_rms_a = current_rms_a
        self.frequency_hz = frequency_hz
        self.harmonic = harmonic
        self.measure_channel = self.contact_map.instrument_channel("vxy_meter") or measure_channel
        self.longitudinal_measure_channel = self.contact_map.instrument_channel("vxx_meter")
        self.source = self.contact_map.instrument_channel("current_source") or source
        self.sheet_resistance_ohm_sq: float | None = kwargs.get("sheet_resistance_ohm_sq")

    def setup(self) -> None:
        self._require_state_exists(self.state)
        self._require_positive(self.current_rms_a, "current_rms_a")
        self._require_positive(self.frequency_hz, "frequency_hz")
        measure_channels = [self.measure_channel]
        if self.longitudinal_measure_channel and self.longitudinal_measure_channel not in measure_channels:
            measure_channels.append(self.longitudinal_measure_channel)
        self.m81.configure_ac_current_lockin(
            source=self.source,
            current_rms_a=self.current_rms_a,
            frequency_hz=self.frequency_hz,
            measure_channels=measure_channels,
            harmonic=self.harmonic,
        )
        for channel in measure_channels:
            self._prepare_measure_channel(
                channel,
                source=self.source,
                default_lockin=True,
                default_harmonic=self.harmonic,
            )

    def measure_point(self, temperature_k: float | None = None, field_t: float | None = None) -> MeasurementPoint:
        forward_channels = [self.measure_channel]
        if self.longitudinal_measure_channel and self.longitudinal_measure_channel not in forward_channels:
            forward_channels.append(self.longitudinal_measure_channel)
        channels, raw_forward = self._measure_channels_with_source_enabled(
            state_name=self.state,
            source=self.source,
            measure_channels=forward_channels,
            measure_kind="transverse",
            current_sign=1.0,
            lockin=True,
        )
        raw_forward_xy = raw_forward[self.measure_channel]
        raw_forward_xx = raw_forward.get(self.longitudinal_measure_channel) if self.longitudinal_measure_channel else None
        reverse_state_name = self.contact_map.get_state_name(self.state, "reverse_current")
        raw_reverse_xy = None
        raw_reverse_xx = None
        if reverse_state_name:
            _channels_reverse, raw_reverse = self._measure_channels_with_source_enabled(
                state_name=reverse_state_name,
                source=self.source,
                measure_channels=forward_channels,
                measure_kind="transverse",
                current_sign=-1.0,
                lockin=True,
            )
            raw_reverse_xy = raw_reverse[self.measure_channel]
            raw_reverse_xx = raw_reverse.get(self.longitudinal_measure_channel) if self.longitudinal_measure_channel else None
        vxy_forward = raw_forward_xy.get("x", raw_forward_xy.get("value"))
        vxy_reverse = raw_reverse_xy.get("x", raw_reverse_xy.get("value")) if raw_reverse_xy else None
        v_hall = vxy_forward if vxy_reverse is None else 0.5 * (vxy_forward - vxy_reverse)
        rxy = v_hall / self.current_rms_a if self.current_rms_a else None
        vxx_forward = raw_forward_xx.get("x", raw_forward_xx.get("value")) if raw_forward_xx else None
        vxx_reverse = raw_reverse_xx.get("x", raw_reverse_xx.get("value")) if raw_reverse_xx else None
        vxx = vxx_forward if vxx_reverse is None else 0.5 * (vxx_forward - vxx_reverse)
        rxx = vxx / self.current_rms_a if (self.current_rms_a and vxx is not None) else None
        density = compute_hall_density(rxy / field_t) if (rxy is not None and field_t not in (None, 0.0)) else {"carrier_density_2d_m2": None, "carrier_density_2d_cm2": None}
        mobility = compute_mobility(self.sheet_resistance_ohm_sq, density["carrier_density_2d_m2"])
        point = MeasurementPoint(
            timestamp=self._timestamp(),
            sample_id=self.sample_id,
            protocol="hall",
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
                "Vxy_raw": vxy_forward,
                "Vxy_reverse_raw": vxy_reverse,
                "Vxx_raw": vxx_forward,
                "Vxx_reverse_raw": vxx_reverse,
                "Rxy_raw": rxy,
                "lockin_x": raw_forward_xy["x"],
                "lockin_y": raw_forward_xy["y"],
                "lockin_r": raw_forward_xy["r"],
                "lockin_theta_deg": raw_forward_xy["theta_deg"],
                "lockin_x_vxx": raw_forward_xx["x"] if raw_forward_xx else None,
                "lockin_y_vxx": raw_forward_xx["y"] if raw_forward_xx else None,
                "lockin_r_vxx": raw_forward_xx["r"] if raw_forward_xx else None,
                "lockin_theta_deg_vxx": raw_forward_xx["theta_deg"] if raw_forward_xx else None,
            },
            derived={
                "vxx_v": vxx,
                "vxy_v": v_hall,
                "rxx_ohm": rxx,
                "rxy_ohm": rxy,
                "matrix_relay_channels": channels,
                **density,
                **mobility,
            },
            metadata={
                "measure_channel": self.measure_channel,
                "source_channel": self.source,
                "measure_channels": [channel for channel in [self.longitudinal_measure_channel, self.measure_channel] if channel],
                "longitudinal_measure_channel": self.longitudinal_measure_channel,
                "transverse_measure_channel": self.measure_channel,
            },
        )
        return point
