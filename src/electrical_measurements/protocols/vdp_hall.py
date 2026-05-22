from __future__ import annotations

from typing import Any

from ..analysis.reciprocity import reciprocity_error
from ..sequences.schema import MeasurementSequence, SequenceDefaults, SequenceStep
from .base import MeasurementPoint, MeasurementProtocol


def default_vdp_hall_states(contact_map: Any) -> list[str]:
    preferred: list[str] = []
    seen_pairs: set[frozenset[str]] = set()
    for state_name in contact_map.get_states_for_group("vdp"):
        reciprocal_name = contact_map.get_state_name(state_name, "reciprocal")
        pair_key = frozenset(name for name in [state_name, reciprocal_name] if name)
        if pair_key and pair_key in seen_pairs:
            continue
        if pair_key:
            seen_pairs.add(pair_key)
        preferred.append(state_name)
    return preferred or list(contact_map.states.keys())


class VanDerPauwHallProtocol(MeasurementProtocol):
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
        include_reciprocity: bool = False,
    ) -> MeasurementSequence:
        selected_states = states or default_vdp_hall_states(contact_map)
        steps: list[SequenceStep] = []
        for state in selected_states:
            steps.append(
                SequenceStep(
                    name=f"measure_{state.lower()}",
                    state=state,
                    outputs={measure_channel: {"name": f"{state.lower()}_ohm", "transform": "lockin_x_over_current"}},
                )
            )
            reciprocal_state = contact_map.get_state_name(state, "reciprocal") if include_reciprocity else None
            if reciprocal_state:
                steps.append(
                    SequenceStep(
                        name=f"measure_{reciprocal_state.lower()}",
                        state=reciprocal_state,
                        reciprocal_step_of=f"measure_{state.lower()}",
                        outputs={measure_channel: {"name": f"{reciprocal_state.lower()}_ohm", "transform": "lockin_x_over_current"}},
                    )
                )
        return MeasurementSequence(
            name="vdp_hall_default",
            description="Legacy Van der Pauw Hall preset expressed as a measurement sequence.",
            contact_map=str(getattr(contact_map, "path", "") or ""),
            defaults=SequenceDefaults(
                excitation_mode="ac",
                source=source,
                measure_channel=measure_channel,
                current_rms_a=current_rms_a,
                frequency_hz=frequency_hz,
                harmonic=harmonic,
                settle_s=0.1,
                repeats=1,
                lockin=True,
                metadata={"legacy_protocol": cls.__name__, "include_reciprocity": include_reciprocity},
            ),
            steps=steps,
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
        include_reciprocity: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.states = states or default_vdp_hall_states(self.contact_map)
        self.current_rms_a = current_rms_a
        self.frequency_hz = frequency_hz
        self.harmonic = harmonic
        self.measure_channel = self.contact_map.instrument_channel("voltage_meter") or measure_channel
        self.source = self.contact_map.instrument_channel("current_source") or source
        self.include_reciprocity = include_reciprocity

    def _expanded_states(self) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        for state_name in self.states:
            for candidate in [state_name, self.contact_map.get_state_name(state_name, "reciprocal") if self.include_reciprocity else None]:
                if candidate and candidate not in seen:
                    ordered.append(candidate)
                    seen.add(candidate)
        return ordered

    def setup(self) -> None:
        self._require_positive(self.current_rms_a, "current_rms_a")
        self._require_positive(self.frequency_hz, "frequency_hz")
        for state in self._expanded_states():
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
        rows: list[dict[str, Any]] = []
        resistances: dict[str, float] = {}
        relay_by_state: dict[str, list[int]] = {}
        for state in self._expanded_states():
            channels, raw = self._measure_with_source_enabled(
                state_name=state,
                source=self.source,
                measure_channel=self.measure_channel,
                measure_kind="transverse",
                current_sign=1.0,
                lockin=True,
            )
            raw_value = raw.get("x", raw.get("value"))
            resistance = raw_value / self.current_rms_a
            resistances[state] = resistance
            relay_by_state[state] = channels
            rows.append(
                {
                    "state": state,
                    "reciprocal_state": self.contact_map.states[state].get("reciprocal"),
                    "raw_value": raw_value,
                    "r_ohm": resistance,
                    "raw": raw,
                    "matrix_relay_channels": channels,
                }
            )

        reciprocity_pairs: list[dict[str, Any]] = []
        if self.include_reciprocity:
            seen_pairs: set[frozenset[str]] = set()
            for state in self.states:
                reciprocal_name = self.contact_map.get_state_name(state, "reciprocal")
                if not reciprocal_name or reciprocal_name not in resistances or state not in resistances:
                    continue
                pair_key = frozenset([state, reciprocal_name])
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                error = reciprocity_error(resistances[state], resistances[reciprocal_name])
                reciprocity_pairs.append(
                    {
                        "state": state,
                        "reciprocal_state": reciprocal_name,
                        "r_ohm": resistances[state],
                        "r_reciprocal_ohm": resistances[reciprocal_name],
                        "reciprocity_error_abs": error["error_abs"],
                        "reciprocity_error_rel": error["error_rel"],
                    }
                )

        mean_hall_resistance = sum(resistances.values()) / len(resistances) if resistances else None
        return MeasurementPoint(
            timestamp=self._timestamp(),
            sample_id=self.sample_id,
            protocol="vdp_hall",
            geometry=self.contact_map.name,
            state=",".join(self._expanded_states()),
            reciprocal_state=None,
            temperature_k=temperature_k,
            field_t=field_t,
            source_current_a_rms=self.current_rms_a,
            source_current_a_peak=self.current_rms_a * 2**0.5,
            frequency_hz=self.frequency_hz,
            harmonic=self.harmonic,
            raw={"rows": rows},
            derived={
                "rxy_ohm": mean_hall_resistance,
                "vdp_hall_resistances_ohm": resistances,
                "vdp_hall_relay_channels": relay_by_state,
                "reciprocity_pairs": reciprocity_pairs,
            },
            metadata={
                "measure_channel": self.measure_channel,
                "source_channel": self.source,
                "selected_states": list(self.states),
                "measured_states": self._expanded_states(),
                "include_reciprocity": self.include_reciprocity,
            },
        )
