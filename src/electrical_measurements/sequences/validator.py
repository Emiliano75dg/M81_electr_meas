from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..exceptions import SequenceValidationError
from ..switching.contact_map import ContactMap
from .schema import MeasurementSequence, ResolvedSequenceStep, SequenceDefaults, SequenceStep

LOGGER = logging.getLogger(__name__)


def valid_source_channels() -> set[str]:
    return {"S1", "S2", "S3"}


def valid_measure_channels() -> set[str]:
    return {"M1", "M2", "M3"}


def _merged_measure_channels(defaults: SequenceDefaults, step: SequenceStep) -> list[str]:
    if step.measure_channels is not None:
        return list(step.measure_channels)
    if step.measure_channel is not None:
        return [step.measure_channel]
    if defaults.measure_channels is not None:
        return list(defaults.measure_channels)
    if defaults.measure_channel is not None:
        return [defaults.measure_channel]
    return []


def _match_contact_map(sequence: MeasurementSequence, contact_map: ContactMap) -> None:
    if not sequence.contact_map:
        return
    declared = Path(sequence.contact_map)
    if declared == contact_map.path:
        return
    try:
        expected = ContactMap.from_yaml(declared)
    except Exception:
        expected = None
    if expected is not None and expected.name == contact_map.name:
        return
    if declared.name == contact_map.path.name:
        return
    raise SequenceValidationError(
        f"Sequence contact_map '{sequence.contact_map}' does not match loaded contact map '{contact_map.path}'"
    )


def _validate_mode(value: str, context: str) -> str:
    normalized = str(value).strip().lower()
    if normalized not in {"dc", "ac"}:
        raise SequenceValidationError(f"{context} must be 'dc' or 'ac'")
    return normalized


def _validate_positive(value: float | None, context: str) -> float:
    if value is None:
        raise SequenceValidationError(f"{context} is required")
    parsed = float(value)
    if parsed <= 0:
        raise SequenceValidationError(f"{context} must be > 0")
    return parsed


def _validate_non_negative(value: float | None, context: str) -> float:
    if value is None:
        raise SequenceValidationError(f"{context} is required")
    parsed = float(value)
    if parsed < 0:
        raise SequenceValidationError(f"{context} must be >= 0")
    return parsed


def _validate_positive_int(value: int | None, context: str) -> int:
    if value is None:
        raise SequenceValidationError(f"{context} is required")
    parsed = int(value)
    if parsed < 1:
        raise SequenceValidationError(f"{context} must be >= 1")
    return parsed


def _validate_source(source: str | None, context: str) -> str:
    if not source:
        raise SequenceValidationError(f"{context} is required")
    if source not in valid_source_channels():
        raise SequenceValidationError(f"{context} must be one of {sorted(valid_source_channels())}")
    return source


def _validate_measure_channels(channels: list[str], context: str) -> list[str]:
    if not channels:
        raise SequenceValidationError(f"{context} must define measure_channel or measure_channels")
    invalid = [channel for channel in channels if channel not in valid_measure_channels()]
    if invalid:
        raise SequenceValidationError(f"{context} contains unknown M81 measurement channels: {invalid}")
    return channels


def resolve_step(
    *,
    sequence: MeasurementSequence,
    contact_map: ContactMap,
    step: SequenceStep,
    index: int,
) -> ResolvedSequenceStep:
    if not step.name or step.name == "None":
        raise SequenceValidationError(f"steps[{index}].name is required")
    if not step.state or step.state == "None":
        raise SequenceValidationError(f"steps[{index}].state is required")
    if step.state not in contact_map.states:
        raise SequenceValidationError(f"Step '{step.name}' references unknown state '{step.state}'")
    excitation_mode = _validate_mode(step.excitation_mode or sequence.defaults.excitation_mode, f"Step '{step.name}' excitation_mode")
    source = _validate_source(step.source or sequence.defaults.source, f"Step '{step.name}' source")
    measure_channels = _validate_measure_channels(
        _merged_measure_channels(sequence.defaults, step),
        f"Step '{step.name}'",
    )
    settle_s = _validate_non_negative(
        step.settle_s if step.settle_s is not None else sequence.defaults.settle_s,
        f"Step '{step.name}' settle_s",
    )
    repeats = _validate_positive_int(
        step.repeats if step.repeats is not None else sequence.defaults.repeats,
        f"Step '{step.name}' repeats",
    )
    outputs = dict(step.outputs or {})
    if step.reciprocal_of and step.reciprocal_of == step.name:
        raise SequenceValidationError(f"Step '{step.name}' cannot be reciprocal_of itself")
    if excitation_mode == "dc":
        current_a = _validate_positive(
            step.current_a if step.current_a is not None else sequence.defaults.current_a,
            f"Step '{step.name}' current_a",
        )
        if (step.current_rms_a if step.current_rms_a is not None else sequence.defaults.current_rms_a) is not None:
            raise SequenceValidationError(f"Step '{step.name}' cannot define current_rms_a in dc mode")
        if (step.frequency_hz if step.frequency_hz is not None else sequence.defaults.frequency_hz) is not None:
            raise SequenceValidationError(f"Step '{step.name}' cannot define frequency_hz in dc mode")
        if (step.harmonic if step.harmonic is not None else sequence.defaults.harmonic) is not None:
            raise SequenceValidationError(f"Step '{step.name}' cannot define harmonic in dc mode")
        bias_polarity = 1 if step.bias_polarity is None else int(step.bias_polarity)
        if bias_polarity not in {1, -1}:
            raise SequenceValidationError(f"Step '{step.name}' bias_polarity must be +1 or -1")
        resolved = ResolvedSequenceStep(
            index=index,
            name=step.name,
            state=step.state,
            excitation_mode=excitation_mode,
            source=source,
            measure_channels=measure_channels,
            current_a=current_a,
            current_rms_a=None,
            frequency_hz=None,
            harmonic=None,
            bias_polarity=bias_polarity,
            settle_s=settle_s,
            repeats=repeats,
            lockin=bool(sequence.defaults.lockin) if sequence.defaults.lockin is not None else False,
            measure_kind=step.measure_kind,
            tags=list(step.tags or []),
            reciprocal_of=step.reciprocal_of,
            outputs=outputs,
            metadata={**(sequence.defaults.metadata or {}), **(step.metadata or {})},
            relay_channels=contact_map.get_state(step.state)["relay_channels"],
        )
        return resolved
    current_rms_a = _validate_positive(
        step.current_rms_a if step.current_rms_a is not None else sequence.defaults.current_rms_a,
        f"Step '{step.name}' current_rms_a",
    )
    frequency_hz = _validate_positive(
        step.frequency_hz if step.frequency_hz is not None else sequence.defaults.frequency_hz,
        f"Step '{step.name}' frequency_hz",
    )
    harmonic = _validate_positive_int(
        step.harmonic if step.harmonic is not None else sequence.defaults.harmonic,
        f"Step '{step.name}' harmonic",
    )
    if (step.current_a if step.current_a is not None else sequence.defaults.current_a) is not None:
        raise SequenceValidationError(f"Step '{step.name}' cannot define current_a in ac mode")
    if step.bias_polarity is not None:
        if not sequence.expert_mode:
            raise SequenceValidationError(
                f"Step '{step.name}' cannot define bias_polarity in ac mode unless expert_mode is enabled"
            )
        LOGGER.warning(
            "AC step '%s' defines bias_polarity in expert_mode. Ordinary reverse bias is normally not needed for AC lock-in measurements.",
            step.name,
        )
    return ResolvedSequenceStep(
        index=index,
        name=step.name,
        state=step.state,
        excitation_mode=excitation_mode,
        source=source,
        measure_channels=measure_channels,
        current_a=None,
        current_rms_a=current_rms_a,
        frequency_hz=frequency_hz,
        harmonic=harmonic,
        bias_polarity=int(step.bias_polarity) if step.bias_polarity is not None else None,
        settle_s=settle_s,
        repeats=repeats,
        lockin=True if sequence.defaults.lockin is None else bool(sequence.defaults.lockin),
        measure_kind=step.measure_kind,
        tags=list(step.tags or []),
        reciprocal_of=step.reciprocal_of,
        outputs=outputs,
        metadata={**(sequence.defaults.metadata or {}), **(step.metadata or {})},
        relay_channels=contact_map.get_state(step.state)["relay_channels"],
    )


def validate_measurement_sequence(sequence: MeasurementSequence, contact_map: ContactMap) -> list[ResolvedSequenceStep]:
    if not sequence.name:
        raise SequenceValidationError("Sequence must define name")
    _match_contact_map(sequence, contact_map)
    if not sequence.steps:
        raise SequenceValidationError("Sequence must define at least one step")
    defaults_mode = _validate_mode(sequence.defaults.excitation_mode, "defaults.excitation_mode")
    if defaults_mode == "dc" and sequence.defaults.current_a is not None and float(sequence.defaults.current_a) <= 0:
        raise SequenceValidationError("defaults.current_a must be > 0")
    if defaults_mode == "ac":
        if sequence.defaults.current_rms_a is not None and float(sequence.defaults.current_rms_a) <= 0:
            raise SequenceValidationError("defaults.current_rms_a must be > 0")
        if sequence.defaults.frequency_hz is not None and float(sequence.defaults.frequency_hz) <= 0:
            raise SequenceValidationError("defaults.frequency_hz must be > 0")
        if sequence.defaults.harmonic is not None and int(sequence.defaults.harmonic) < 1:
            raise SequenceValidationError("defaults.harmonic must be >= 1")
    resolved = [resolve_step(sequence=sequence, contact_map=contact_map, step=step, index=index) for index, step in enumerate(sequence.steps)]
    names = {step.name for step in resolved}
    for step in resolved:
        if step.reciprocal_of and step.reciprocal_of not in names:
            raise SequenceValidationError(
                f"Step '{step.name}' reciprocal_of references unknown step '{step.reciprocal_of}'"
            )
    return resolved

