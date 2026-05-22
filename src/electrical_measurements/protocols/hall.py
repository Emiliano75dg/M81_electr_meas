from __future__ import annotations

from typing import Any

from ..analysis.hall import compute_hall_density, compute_mobility
from ..exceptions import HardwareError, MatrixSwitchError, ProtocolConfigError
from ..sequences.schema import MeasurementSequence, SequenceDefaults, SequenceStep
from .base import MeasurementPoint, MeasurementProtocol


class HallProtocol(MeasurementProtocol):
    @classmethod
    def default_sequence(
        cls,
        *,
        contact_map: Any,
        state: str = "hallbar_forward",
        current_rms_a: float = 10e-6,
        frequency_hz: float = 13.7,
        harmonic: int = 1,
        measure_channel: str = "M2",
        source: str = "S1",
    ) -> MeasurementSequence:
        transverse_channel = contact_map.instrument_channel("vxy_meter") or measure_channel
        longitudinal_channel = contact_map.instrument_channel("vxx_meter")
        channels = [transverse_channel]
        outputs: dict[str, dict[str, str]] = {
            transverse_channel: {"name": "rxy_ohm", "transform": "lockin_x_over_current"},
        }
        if longitudinal_channel and longitudinal_channel not in channels:
            channels.append(longitudinal_channel)
            outputs[longitudinal_channel] = {"name": "rxx_ohm", "transform": "lockin_x_over_current"}
        return MeasurementSequence(
            name="hall_default",
            description="Legacy Hall preset expressed as a measurement sequence.",
            contact_map=str(getattr(contact_map, "path", "") or ""),
            defaults=SequenceDefaults(
                source_mode="ac",
                source_quantity="current",
                source=contact_map.instrument_channel("current_source") or source,
                source_channel=contact_map.instrument_channel("current_source") or source,
                measure_channels=channels,
                source_value=current_rms_a,
                current_rms_a=current_rms_a,
                frequency_hz=frequency_hz,
                harmonic=harmonic,
                measure_mode="lockin",
                readout="x",
                settle_s=0.1,
                repeats=1,
                reverse_policy="none",
                lockin=True,
                metadata={"legacy_protocol": cls.__name__},
            ),
            steps=[SequenceStep(name="hall_forward", state=state, measure_kind="transverse", outputs=outputs)],
        )

    """Hall measurement protocol for carrier concentration extraction.

    Measures transverse (Hall, Rxy) and longitudinal (Rxx) resistances as a
    function of magnetic field to determine carrier type (electrons/holes),
    their density, and mobility.

    This protocol also supports simultaneous measurement of Rxx if a longitudinal
    measurement channel is specified in the contact map (`vxx_meter`).
    Ordinary Hall measurements do not automatically use `reverse_current`
    matrix states. Reverse-bias diagnostics should be requested explicitly
    via a sequence step or dedicated diagnostic workflow.

    Attributes:
        state (str): Name of the contact configuration state from the contact_map.
        current_rms_a (float): AC current amplitude (RMS) in Amperes.
        frequency_hz (float): Lock-in frequency.
        harmonic (int): Lock-in harmonic to measure (e.g., 1 for 1f).
        measure_channel (str): M81 measurement channel for Hall voltage (Vxy).
        longitudinal_measure_channel (str | None): Optional measurement channel for Vxx.
        source (str): M81 source channel for the current.
        sheet_resistance_ohm_sq (float | None): If provided, it is used to calculate
            carrier mobility.
    """

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
        """Initializes the Hall measurement protocol.

        Args:
            state: Name of the measurement state (e.g., "hallbar_forward") defined
                in the contact map file.
            current_rms_a: AC current (RMS) to apply to the sample.
            frequency_hz: Frequency of the AC current source.
            harmonic: Harmonic to measure with the lock-in (usually 1).
            measure_channel: M81 measurement channel for the Hall voltage (Vxy).
                This is overridden if `vxy_meter` is defined in the contact map.
            source: M81 source channel for the current. This is overridden
                if `current_source` is defined in the contact map.
            **kwargs: Additional arguments passed to the base class, such as
                `sheet_resistance_ohm_sq` for mobility calculation.
        """
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
        """Configures the instruments for the Hall measurement.

        Sets up the AC current source and lock-in measurement channels on the M81
        based on the protocol parameters.

        Raises:
            ProtocolConfigError: If the configuration parameters are invalid
                (e.g., non-existent state, negative current).
        """
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
        """Performs a single Hall measurement at a given temperature and field.

        The measurement process includes:
        1. Setting the matrix state for the "forward" measurement.
        2. Enabling the source and measuring Vxy (and Vxx if configured).
        3. Calculating Rxy and Rxx from the configured forward state.
        4. Calculating Hall density and mobility.

        Args:
            temperature_k: The target temperature for the measurement.
            field_t: The target magnetic field for the measurement.

        Returns:
            A MeasurementPoint object containing all raw, derived, and
            metadata from the measurement.

        Raises:
            HardwareError: In case of communication problems with the instruments.
            MatrixSwitchError: If the matrix fails to switch the relays.
        """
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
        raw_reverse_xy = None
        raw_reverse_xx = None
        vxy_forward = raw_forward_xy.get("x", raw_forward_xy.get("value"))
        vxy_reverse = raw_reverse_xy.get("x", raw_reverse_xy.get("value")) if raw_reverse_xy else None
        v_hall = vxy_forward
        rxy = v_hall / self.current_rms_a if self.current_rms_a else None
        vxx_forward = raw_forward_xx.get("x", raw_forward_xx.get("value")) if raw_forward_xx else None
        vxx_reverse = raw_reverse_xx.get("x", raw_reverse_xx.get("value")) if raw_reverse_xx else None
        vxx = vxx_forward
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
