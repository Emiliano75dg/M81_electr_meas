from __future__ import annotations

from typing import Any

import pandas as pd

from ..analysis.reciprocity import match_reciprocal_field, reciprocity_error
from .base import MeasurementPoint, MeasurementProtocol


class ReciprocityProtocol(MeasurementProtocol):
    def __init__(
        self,
        *,
        states: list[str],
        current_rms_a: float = 10e-6,
        frequency_hz: float = 13.7,
        harmonic: int = 1,
        measure_channel: str = "M1",
        source: str = "S1",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.states = states
        self.current_rms_a = current_rms_a
        self.frequency_hz = frequency_hz
        self.harmonic = harmonic
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
        for state in self.states:
            reciprocal_name = self.contact_map.states[state].get("reciprocal")
            for current_state in [state, reciprocal_name]:
                if not current_state:
                    continue
                _channels, raw = self._measure_with_source_enabled(
                    state_name=current_state,
                    source=self.source,
                    measure_channel=self.measure_channel,
                    measure_kind="longitudinal",
                    current_sign=1.0,
                    lockin=True,
                )
                raw_value = raw.get("x", raw.get("value"))
                resistance = raw_value / self.current_rms_a
                rows.append(
                    {
                        "state": current_state,
                        "reciprocal_state": self.contact_map.states[current_state].get("reciprocal"),
                        "field_t": field_t,
                        "temperature_k": temperature_k,
                        "r_ohm": resistance,
                    }
                )
        dataframe = pd.DataFrame(rows)
        if field_t in (None, 0.0):
            comparisons = []
            for state in self.states:
                reciprocal_name = self.contact_map.states[state].get("reciprocal")
                if not reciprocal_name:
                    continue
                forward = dataframe.loc[dataframe["state"] == state, "r_ohm"]
                reciprocal = dataframe.loc[dataframe["state"] == reciprocal_name, "r_ohm"]
                if not forward.empty and not reciprocal.empty:
                    error = reciprocity_error(float(forward.iloc[0]), float(reciprocal.iloc[0]))
                    error["field_pairing"] = "same_B"
                    comparisons.append(error)
            derived = {"reciprocity_pairs": comparisons}
        else:
            derived = {"reciprocity_pairs": match_reciprocal_field(dataframe).to_dict(orient="records")}
        return MeasurementPoint(
            timestamp=self._timestamp(),
            sample_id=self.sample_id,
            protocol="reciprocity",
            geometry=self.contact_map.name,
            state=",".join(self.states),
            reciprocal_state=None,
            temperature_k=temperature_k,
            field_t=field_t,
            source_current_a_rms=self.current_rms_a,
            source_current_a_peak=self.current_rms_a * 2**0.5,
            frequency_hz=self.frequency_hz,
            harmonic=self.harmonic,
            raw={"rows": rows},
            derived=derived,
            metadata={"measure_channel": self.measure_channel, "source_channel": self.source},
        )
