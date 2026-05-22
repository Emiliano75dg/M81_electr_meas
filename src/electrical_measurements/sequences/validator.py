from __future__ import annotations

import logging
from pathlib import Path

from ..capabilities import DEFAULT_INSTRUMENT_CAPABILITIES, InstrumentCapabilities
from ..exceptions import SequenceValidationError
from ..excitation import validate_ac_excitation, validate_dc_excitation
from ..records import normalize_output_mapping
from ..switching.contact_map import ContactMap
from .schema import MeasurementSequence, ResolvedSequenceStep, SequenceDefaults, SequenceStep

LOGGER = logging.getLogger(__name__)


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


def _validate_source(source: str | None, context: str, capabilities: InstrumentCapabilities) -> str:
    if not source:
        raise SequenceValidationError(f"{context} is required")
    if source not in capabilities.sources:
        raise SequenceValidationError(f"{context} must be one of {list(capabilities.sources)}")
    return source


def _validate_measure_channels(channels: list[str], context: str, capabilities: InstrumentCapabilities) -> list[str]:
    if not channels:
        raise SequenceValidationError(f"{context} must define measure_channel or measure_channels")
    invalid = [channel for channel in channels if channel not in capabilities.measure_channels]
    if invalid:
        raise SequenceValidationError(f"{context} contains unknown M81 measurement channels: {invalid}")
    return channels


def _normalize_reciprocity(step: SequenceStep) -> tuple[str | None, list[str]]:
    warnings: list[str] = []
    if step.reciprocal_step_of and step.reciprocal_of and step.reciprocal_step_of != step.reciprocal_of:
        raise SequenceValidationError(
            f"Step '{step.name}' defines both reciprocal_step_of and reciprocal_of with different values"
        )
    reciprocal_step_of = step.reciprocal_step_of or step.reciprocal_of
    if step.reciprocal_of and not step.reciprocal_step_of:
        warning = f"Step '{step.name}' uses deprecated reciprocal_of; prefer reciprocal_step_of"
        LOGGER.warning(warning)
        warnings.append(warning)
    return reciprocal_step_of, warnings


def resolve_step(
    *,
    sequence: MeasurementSequence,
    contact_map: ContactMap,
    step: SequenceStep,
    index: int,
    capabilities: InstrumentCapabilities = DEFAULT_INSTRUMENT_CAPABILITIES,
) -> ResolvedSequenceStep:
    if not step.name or step.name == "None":
        raise SequenceValidationError(f"steps[{index}].name is required")
    if not step.state or step.state == "None":
        raise SequenceValidationError(f"steps[{index}].state is required")
    if step.state not in contact_map.states:
        raise SequenceValidationError(f"Step '{step.name}' references unknown state '{step.state}'")
    excitation_mode = _validate_mode(step.excitation_mode or sequence.defaults.excitation_mode, f"Step '{step.name}' excitation_mode")
    source = _validate_source(step.source or sequence.defaults.source, f"Step '{step.name}' source", capabilities)
    measure_channels = _validate_measure_channels(
        _merged_measure_channels(sequence.defaults, step),
        f"Step '{step.name}'",
        capabilities,
    )
    settle_s = _validate_non_negative(
        step.settle_s if step.settle_s is not None else sequence.defaults.settle_s,
        f"Step '{step.name}' settle_s",
    )
    repeats = _validate_positive_int(
        step.repeats if step.repeats is not None else sequence.defaults.repeats,
        f"Step '{step.name}' repeats",
    )
    reciprocal_step_of, warnings = _normalize_reciprocity(step)
    if reciprocal_step_of and reciprocal_step_of == step.name:
        raise SequenceValidationError(f"Step '{step.name}' cannot be reciprocal_step_of itself")
    try:
        outputs, output_warnings = normalize_output_mapping(step.outputs or {}, excitation_mode=excitation_mode)
    except ValueError as exc:
        raise SequenceValidationError(str(exc)) from exc
    warnings.extend(output_warnings)
    if excitation_mode == "dc":
        dc = validate_dc_excitation(
            source=source,
            current_a=step.current_a if step.current_a is not None else sequence.defaults.current_a,
            bias_polarity=step.bias_polarity,
            settle_s=settle_s,
            error_cls=SequenceValidationError,
        )
        if (step.current_rms_a if step.current_rms_a is not None else sequence.defaults.current_rms_a) is not None:
            raise SequenceValidationError(f"Step '{step.name}' cannot define current_rms_a in dc mode")
        if (step.frequency_hz if step.frequency_hz is not None else sequence.defaults.frequency_hz) is not None:
            raise SequenceValidationError(f"Step '{step.name}' cannot define frequency_hz in dc mode")
        if (step.harmonic if step.harmonic is not None else sequence.defaults.harmonic) is not None:
            raise SequenceValidationError(f"Step '{step.name}' cannot define harmonic in dc mode")
        return ResolvedSequenceStep(
            index=index,
            name=step.name,
            state=step.state,
            excitation_mode=excitation_mode,
            source=dc.source,
            measure_channels=tuple(measure_channels),
            current_a=dc.current_a,
            current_rms_a=None,
            frequency_hz=None,
            harmonic=None,
            bias_polarity=dc.bias_polarity,
            settle_s=settle_s,
            repeats=repeats,
            lockin=bool(sequence.defaults.lockin) if sequence.defaults.lockin is not None else False,
            measure_kind=step.measure_kind,
            tags=tuple(step.tags or []),
            reciprocal_step_of=reciprocal_step_of,
            outputs=outputs,
            metadata={**(sequence.defaults.metadata or {}), **(step.metadata or {})},
            relay_channels=tuple(contact_map.get_state(step.state)["relay_channels"]),
            warnings=tuple(warnings),
        )
    ac = validate_ac_excitation(
        source=source,
        current_rms_a=step.current_rms_a if step.current_rms_a is not None else sequence.defaults.current_rms_a,
        frequency_hz=step.frequency_hz if step.frequency_hz is not None else sequence.defaults.frequency_hz,
        harmonic=step.harmonic if step.harmonic is not None else sequence.defaults.harmonic,
        bias_polarity=step.bias_polarity,
        settle_s=settle_s,
        lockin=True if sequence.defaults.lockin is None else bool(sequence.defaults.lockin),
        expert_mode=sequence.expert_mode,
        error_cls=SequenceValidationError,
    )
    if (step.current_a if step.current_a is not None else sequence.defaults.current_a) is not None:
        raise SequenceValidationError(f"Step '{step.name}' cannot define current_a in ac mode")
    return ResolvedSequenceStep(
        index=index,
        name=step.name,
        state=step.state,
        excitation_mode=excitation_mode,
        source=ac.source,
        measure_channels=tuple(measure_channels),
        current_a=None,
        current_rms_a=ac.current_rms_a,
        frequency_hz=ac.frequency_hz,
        harmonic=ac.harmonic,
        bias_polarity=None if not sequence.expert_mode else step.bias_polarity,
        settle_s=settle_s,
        repeats=repeats,
        lockin=ac.lockin,
        measure_kind=step.measure_kind,
        tags=tuple(step.tags or []),
        reciprocal_step_of=reciprocal_step_of,
        outputs=outputs,
        metadata={**(sequence.defaults.metadata or {}), **(step.metadata or {})},
        relay_channels=tuple(contact_map.get_state(step.state)["relay_channels"]),
        warnings=tuple(warnings),
    )


def validate_measurement_sequence(
    sequence: MeasurementSequence,
    contact_map: ContactMap,
    capabilities: InstrumentCapabilities = DEFAULT_INSTRUMENT_CAPABILITIES,
) -> list[ResolvedSequenceStep]:
    if not sequence.name:
        raise SequenceValidationError("Sequence must define name")
    _match_contact_map(sequence, contact_map)
    if not sequence.steps:
        raise SequenceValidationError("Sequence must define at least one step")
    defaults_mode = _validate_mode(sequence.defaults.excitation_mode, "defaults.excitation_mode")
    if defaults_mode == "dc" and sequence.defaults.current_a is not None:
        validate_dc_excitation(
            source=sequence.defaults.source,
            current_a=sequence.defaults.current_a,
            bias_polarity=1,
            settle_s=sequence.defaults.settle_s,
            error_cls=SequenceValidationError,
        )
    if defaults_mode == "ac" and any(
        value is not None for value in (sequence.defaults.current_rms_a, sequence.defaults.frequency_hz, sequence.defaults.harmonic)
    ):
        validate_ac_excitation(
            source=sequence.defaults.source,
            current_rms_a=sequence.defaults.current_rms_a,
            frequency_hz=sequence.defaults.frequency_hz,
            harmonic=sequence.defaults.harmonic,
            settle_s=sequence.defaults.settle_s,
            lockin=sequence.defaults.lockin,
            expert_mode=sequence.expert_mode,
            error_cls=SequenceValidationError,
        )
    resolved = [
        resolve_step(
            sequence=sequence,
            contact_map=contact_map,
            step=step,
            index=index,
            capabilities=capabilities,
        )
        for index, step in enumerate(sequence.steps)
    ]
    names = {step.name for step in resolved}
    for step in resolved:
        if step.reciprocal_step_of and step.reciprocal_step_of not in names:
            raise SequenceValidationError(
                f"Step '{step.name}' reciprocal_step_of references unknown step '{step.reciprocal_step_of}'"
            )
    return resolved
