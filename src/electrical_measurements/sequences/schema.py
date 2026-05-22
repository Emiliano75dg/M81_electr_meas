from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..records import OutputSpec


@dataclass(frozen=True)
class SequenceDefaults:
    excitation_mode: str
    source: str | None = None
    measure_channel: str | None = None
    measure_channels: list[str] | None = None
    current_a: float | None = None
    current_rms_a: float | None = None
    frequency_hz: float | None = None
    harmonic: int | None = None
    settle_s: float = 0.0
    repeats: int = 1
    lockin: bool | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class SequenceStep:
    name: str
    state: str
    excitation_mode: str | None = None
    source: str | None = None
    measure_channel: str | None = None
    measure_channels: list[str] | None = None
    current_a: float | None = None
    current_rms_a: float | None = None
    frequency_hz: float | None = None
    harmonic: int | None = None
    bias_polarity: int | None = None
    settle_s: float | None = None
    repeats: int | None = None
    measure_kind: str | None = None
    tags: list[str] | None = None
    reciprocal_step_of: str | None = None
    reciprocal_of: str | None = None
    outputs: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


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
    name: str
    state: str
    excitation_mode: str
    source: str
    measure_channels: tuple[str, ...]
    current_a: float | None
    current_rms_a: float | None
    frequency_hz: float | None
    harmonic: int | None
    bias_polarity: int | None
    settle_s: float
    repeats: int
    lockin: bool | None
    measure_kind: str | None
    tags: tuple[str, ...]
    reciprocal_step_of: str | None
    outputs: dict[str, OutputSpec]
    metadata: dict[str, Any]
    relay_channels: tuple[int, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def primary_measure_channel(self) -> str | None:
        return self.measure_channels[0] if self.measure_channels else None

    @property
    def actual_dc_current_a(self) -> float | None:
        if self.excitation_mode != "dc" or self.current_a is None:
            return None
        polarity = self.bias_polarity if self.bias_polarity is not None else 1
        return polarity * self.current_a
