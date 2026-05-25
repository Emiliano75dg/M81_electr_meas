from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..records import OutputSpec


@dataclass(frozen=True)
class ChannelMeasureSpec:
    measure_mode: str
    harmonic: int | None = None
    readout: str = "value"
    output: str | None = None
    transform: str | None = None
    time_constant_s: float | None = None
    rolloff: str | None = None
    nplc: float | None = None


@dataclass(frozen=True)
class SequenceDefaults:
    source_mode: str
    source_quantity: str = "current"
    source_value: float | None = None
    source: str | None = None
    source_channel: str | None = None
    measure_channel: str | None = None
    measure_channels: list[str] | None = None
    current_a: float | None = None
    current_rms_a: float | None = None
    frequency_hz: float | None = None
    harmonic: int | None = None
    measure_mode: str | None = None
    readout: str | None = None
    time_constant_s: float | None = None
    nplc: float | None = None
    rolloff: str | None = None
    measure_specs: dict[str, ChannelMeasureSpec] | None = None
    settle_s: float = 0.0
    repeats: int = 1
    reverse_policy: str = "none"
    matrix_policy: str = "apply_state"
    notes: str | None = None
    lockin: bool | None = None
    metadata: dict[str, Any] | None = None

    @property
    def excitation_mode(self) -> str:
        return self.source_mode


@dataclass(frozen=True)
class MeasurementStep:
    name: str
    state: str
    enabled: bool = True
    order: int | None = None
    label: str | None = None
    source_mode: str | None = None
    source_quantity: str | None = None
    source_value: float | None = None
    excitation_mode: str | None = None
    source: str | None = None
    source_channel: str | None = None
    measure_channel: str | None = None
    measure_channels: list[str] | None = None
    current_a: float | None = None
    current_rms_a: float | None = None
    frequency_hz: float | None = None
    harmonic: int | None = None
    bias_polarity: int | None = None
    settle_s: float | None = None
    repeats: int | None = None
    measure_mode: str | None = None
    readout: str | None = None
    time_constant_s: float | None = None
    nplc: float | None = None
    rolloff: str | None = None
    measure_specs: dict[str, ChannelMeasureSpec] | None = None
    reverse_policy: str | None = None
    matrix_policy: str | None = None
    diagnostic_state: str | None = None
    measure_kind: str | None = None
    tags: list[str] | None = None
    repeat_of: str | None = None
    reciprocity_partner: str | None = None
    reciprocal_step_of: str | None = None
    reciprocal_of: str | None = None
    outputs: dict[str, Any] | None = None
    notes: str | None = None
    metadata: dict[str, Any] | None = None

    @property
    def resolved_source_mode(self) -> str | None:
        return self.source_mode or self.excitation_mode

    @property
    def resolved_source_channel(self) -> str | None:
        return self.source_channel or self.source


SequenceStep = MeasurementStep


@dataclass(frozen=True)
class MeasurementSequence:
    name: str
    description: str | None
    contact_map: str | None
    defaults: SequenceDefaults
    steps: list[SequenceStep]
    expert_mode: bool = False
    path: Path | None = None


@dataclass(frozen=True)
class ResolvedSequenceStep:
    index: int
    enabled: bool
    name: str
    label: str | None
    state: str
    source_mode: str
    source_quantity: str
    source_value: float
    source_channel: str
    measure_channels: tuple[str, ...]
    current_a: float | None
    current_rms_a: float | None
    frequency_hz: float | None
    harmonic: int | None
    bias_polarity: int | None
    measure_mode: str
    readout: str
    time_constant_s: float | None
    nplc: float | None
    rolloff: str | None
    measure_specs: dict[str, ChannelMeasureSpec]
    settle_s: float
    repeats: int
    reverse_policy: str
    matrix_policy: str
    diagnostic_state: str | None
    lockin: bool | None
    measure_kind: str | None
    tags: tuple[str, ...]
    reciprocity_partner: str | None
    reciprocal_step_of: str | None
    outputs: dict[str, OutputSpec]
    notes: str | None
    metadata: dict[str, Any]
    relay_channels: tuple[int, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def excitation_mode(self) -> str:
        return self.source_mode

    @property
    def source(self) -> str:
        return self.source_channel

    @property
    def primary_measure_channel(self) -> str | None:
        return self.measure_channels[0] if self.measure_channels else None

    @property
    def actual_dc_current_a(self) -> float | None:
        if self.source_mode != "dc" or self.source_quantity != "current":
            return None
        return self.source_value

    @property
    def actual_dc_voltage_v(self) -> float | None:
        if self.source_mode != "dc" or self.source_quantity != "voltage":
            return None
        return self.source_value


@dataclass(frozen=True)
class BiasPoint:
    step_index: int
    step_name: str
    state: str
    source_mode: str
    source_quantity: str
    source_value: float
    source_polarity: int
    reverse_policy: str
    diagnostic: bool = False
