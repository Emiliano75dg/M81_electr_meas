from __future__ import annotations

from typing import Any

from ..analysis.reciprocity import reciprocity_error
from ..analysis.vanderpauw import compute_vanderpauw_anisotropy, solve_vanderpauw_sheet_resistance
from ..sequences.schema import MeasurementSequence, SequenceDefaults, SequenceStep
from .base import MeasurementPoint, MeasurementProtocol


class VanDerPauwProtocol(MeasurementProtocol):
    @classmethod
    def default_sequence(
        cls,
        *,
        contact_map: Any,
        states: list[str] | None = None,
        current_rms_a: float = 10e-6,
        frequency_hz: float = 13.7,
        harmonic: int = 1,
        measure_channel: str = "M1",
        source: str = "S1",
    ) -> MeasurementSequence:
        selected_states = states or ["I_AB_V_CD", "I_BC_V_DA", "I_CD_V_AB", "I_DA_V_BC"]
        return MeasurementSequence(
            name="vanderpauw_default",
            description="Legacy Van der Pauw preset expressed as a measurement sequence.",
            contact_map=str(getattr(contact_map, "path", "") or ""),
            defaults=SequenceDefaults(
                source_mode="ac",
                source_quantity="current",
                source=source,
                source_channel=source,
                measure_channel=measure_channel,
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
            steps=[
                SequenceStep(
                    name=f"measure_{state.lower()}",
                    state=state,
                    outputs={measure_channel: {"name": f"{state.lower()}_ohm", "transform": "lockin_x_over_current"}},
                )
                for state in selected_states
            ],
        )

    def __init__(
        self,
        *,
        states: list[str] | None = None,
        current_rms_a: float = 10e-6,
        frequency_hz: float = 13.7,
        harmonic: int = 1,
        measure_channel: str = "M1",
        source: str = "S1",
        include_anisotropy: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.states = states or ["I_AB_V_CD", "I_BC_V_DA", "I_CD_V_AB", "I_DA_V_BC"]
        self.current_rms_a = current_rms_a
        self.frequency_hz = frequency_hz
        self.harmonic = harmonic
        self.measure_channel = measure_channel
        self.source = source
        self.include_anisotropy = include_anisotropy

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
            harmonic=self.harmonic,
        )
        self._prepare_measure_channel(
            self.measure_channel,
            source=self.source,
            default_lockin=True,
            default_harmonic=self.harmonic,
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
            raw_value = raw.get("x", raw.get("value"))
            resistance = raw_value / self.current_rms_a
            resistances[state] = resistance
            raw_measurements[state] = raw_value
        for state in self.states:
            reciprocal_name = self.contact_map.get_state_name(state, "reciprocal")
            if reciprocal_name and reciprocal_name in resistances and state in resistances:
                reciprocity_checks[f"{state}__{reciprocal_name}"] = reciprocity_error(resistances[state], resistances[reciprocal_name])
        rs = solve_vanderpauw_sheet_resistance(
            resistances.get("I_AB_V_CD", 0.0),
            resistances.get("I_BC_V_DA", 0.0),
        )
        anisotropy = compute_vanderpauw_anisotropy(resistances) if self.include_anisotropy else {}
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
            harmonic=self.harmonic,
            raw=raw_measurements,
            derived={
                "sheet_resistance_ohm_sq": rs,
                "reciprocity_checks": reciprocity_checks,
                **anisotropy,
                **{f"{k}_ohm": v for k, v in resistances.items()},
            },
            metadata={
                "measure_channel": self.measure_channel,
                "source_channel": self.source,
                "include_anisotropy": self.include_anisotropy,
            },
        )
