from __future__ import annotations

import logging
from pathlib import Path

from ..capabilities import DEFAULT_INSTRUMENT_CAPABILITIES, InstrumentCapabilities
from ..exceptions import SequenceValidationError
from ..excitation import validate_ac_excitation, validate_dc_excitation
from ..records import normalize_output_mapping
from ..switching.contact_map import ContactMap
from ..switching.safety import validate_contact_map_state
from .schema import BiasPoint, ChannelMeasureSpec, MeasurementSequence, ResolvedSequenceStep, SequenceDefaults, SequenceStep

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


def _validate_source_quantity(value: str | None, context: str) -> str:
    normalized = str(value or "current").strip().lower()
    # Keep accepting syntactically valid schema values here; runner support is enforced
    # later when resolving the concrete hardware-facing source configuration.
    if normalized not in {"current", "voltage"}:
        raise SequenceValidationError(f"{context} must be 'current' or 'voltage'")
    return normalized


def _validate_measure_mode(value: str | None, *, source_mode: str, harmonic: int | None, context: str) -> str:
    normalized = str(value or "auto").strip().lower()
    if normalized not in {"dc", "lockin", "auto"}:
        raise SequenceValidationError(f"{context} must be 'dc', 'lockin', or 'auto'")
    if harmonic is not None and harmonic > 1 and normalized not in {"lockin", "auto"}:
        raise SequenceValidationError(f"{context} must be lockin-compatible when harmonic > 1")
    if source_mode == "ac" and normalized == "dc" and harmonic not in (None, 1):
        raise SequenceValidationError(f"{context} cannot be 'dc' for harmonic > 1")
    return normalized


def _resolve_readout(value: str | None) -> str:
    normalized = str(value or "value").strip().lower()
    parts = [part.strip() for part in normalized.split(",") if part.strip()]
    if not parts:
        raise SequenceValidationError("readout must not be empty")
    invalid = [part for part in parts if part not in {"value", "x", "y", "r", "theta"}]
    if invalid:
        raise SequenceValidationError("readout must be one of 'value', 'x', 'y', 'r', 'theta'")
    return ",".join(parts)


def _resolve_reverse_policy(value: str | None, *, source_mode: str) -> str:
    normalized = str(value or "auto").strip().lower()
    if normalized not in {"none", "auto", "dc_source_inversion", "diagnostic_state"}:
        raise SequenceValidationError(
            "reverse_policy must be one of 'none', 'auto', 'dc_source_inversion', 'diagnostic_state'"
        )
    if normalized == "auto":
        return "dc_source_inversion" if source_mode == "dc" else "none"
    if source_mode == "ac" and normalized == "dc_source_inversion":
        raise SequenceValidationError("AC steps cannot use reverse_policy=dc_source_inversion")
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


def _resolve_matrix_policy(value: str | None, *, context: str) -> str:
    normalized = str(value or "apply_state").strip().lower()
    if normalized not in {"apply_state", "none"}:
        raise SequenceValidationError(f"{context} must be 'apply_state' or 'none'")
    return normalized


def _resolve_source_channel(step: SequenceStep, defaults: SequenceDefaults, capabilities: InstrumentCapabilities) -> str:
    return _validate_source(
        step.source_channel or step.source or defaults.source_channel or defaults.source,
        f"Step '{step.name}' source_channel",
        capabilities,
    )


def _resolve_source_value(step: SequenceStep, defaults: SequenceDefaults, *, source_mode: str, source_quantity: str) -> float:
    explicit_value = step.source_value if step.source_value is not None else defaults.source_value
    legacy_dc = step.current_a if step.current_a is not None else defaults.current_a
    legacy_ac = step.current_rms_a if step.current_rms_a is not None else defaults.current_rms_a
    if explicit_value is None:
        explicit_value = legacy_dc if source_mode == "dc" else legacy_ac
    if explicit_value is None:
        legacy_name = "current_a" if source_mode == "dc" and source_quantity == "current" else "current_rms_a"
        raise SequenceValidationError(f"Step '{step.name}' source_value is required ({legacy_name} is also accepted)")
    value = float(explicit_value)
    if value <= 0:
        raise SequenceValidationError(f"Step '{step.name}' source_value must be > 0")
    # The schema allows voltage so we can validate payload shape cleanly, but the
    # current hardware runner only knows how to program current sourcing.
    if source_quantity != "current":
        raise SequenceValidationError(
            f"Only current sourcing is currently supported by the hardware runner; step '{step.name}' requested source_quantity={source_quantity}"
        )
    return value


def _merge_measure_specs(defaults: SequenceDefaults, step: SequenceStep) -> dict[str, ChannelMeasureSpec] | None:
    if step.measure_specs is not None:
        return dict(step.measure_specs)
    if defaults.measure_specs is not None:
        return dict(defaults.measure_specs)
    return None


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


def _validate_measure_specs(
    *,
    measure_specs: dict[str, ChannelMeasureSpec] | None,
    measure_channels: list[str],
    source_mode: str,
    step: SequenceStep,
    defaults: SequenceDefaults,
    capabilities: InstrumentCapabilities,
) -> tuple[list[str], dict[str, ChannelMeasureSpec], dict[str, dict[str, str]]]:
    if not measure_specs:
        channels = _validate_measure_channels(measure_channels, f"Step '{step.name}'", capabilities)
        resolved_harmonic = step.harmonic if step.harmonic is not None else defaults.harmonic
        resolved_measure_mode = _validate_measure_mode(
            step.measure_mode or defaults.measure_mode,
            source_mode=source_mode,
            harmonic=int(resolved_harmonic) if resolved_harmonic is not None else None,
            context=f"Step '{step.name}' measure_mode",
        )
        resolved_readout = _resolve_readout(step.readout or defaults.readout)
        resolved_spec = ChannelMeasureSpec(
            measure_mode=resolved_measure_mode,
            harmonic=int(resolved_harmonic) if resolved_harmonic is not None else None,
            readout=resolved_readout,
            output=None,
            transform=None,
            time_constant_s=step.time_constant_s if step.time_constant_s is not None else defaults.time_constant_s,
            rolloff=step.rolloff if step.rolloff is not None else defaults.rolloff,
            nplc=step.nplc if step.nplc is not None else defaults.nplc,
        )
        return channels, {channel: resolved_spec for channel in channels}, {}

    channels = _validate_measure_channels(list(measure_specs), f"Step '{step.name}' measure_specs", capabilities)
    outputs_from_specs: dict[str, dict[str, str]] = {}
    resolved_specs: dict[str, ChannelMeasureSpec] = {}
    for channel, raw_spec in measure_specs.items():
        if channel not in capabilities.measure_channels:
            raise SequenceValidationError(f"Step '{step.name}' measure_specs contains unknown M81 measurement channel '{channel}'")
        harmonic = raw_spec.harmonic
        if harmonic is not None and int(harmonic) < 1:
            raise SequenceValidationError(f"Step '{step.name}' measure_specs.{channel}.harmonic must be >= 1")
        if source_mode == "dc" and raw_spec.harmonic is not None:
            raise SequenceValidationError(
                f"Step '{step.name}' measure_specs.{channel}.harmonic is incompatible with dc source_mode"
            )
        resolved_mode = _validate_measure_mode(
            raw_spec.measure_mode,
            source_mode=source_mode,
            harmonic=int(harmonic) if harmonic is not None else None,
            context=f"Step '{step.name}' measure_specs.{channel}.measure_mode",
        )
        resolved_readout = _resolve_readout(raw_spec.readout)
        if source_mode == "dc":
            if raw_spec.rolloff is not None or raw_spec.time_constant_s is not None:
                raise SequenceValidationError(
                    f"Step '{step.name}' measure_specs.{channel} lock-in fields are incompatible with dc source_mode"
                )
        resolved_specs[channel] = ChannelMeasureSpec(
            measure_mode=resolved_mode,
            harmonic=int(harmonic) if harmonic is not None else None,
            readout=resolved_readout,
            output=raw_spec.output,
            transform=raw_spec.transform,
            time_constant_s=raw_spec.time_constant_s,
            rolloff=raw_spec.rolloff,
            nplc=raw_spec.nplc,
        )
        output_name = str(raw_spec.output).strip() if raw_spec.output else ""
        if not output_name:
            harmonic_suffix = f"{int(harmonic) if harmonic is not None else 1}w"
            readout_suffix = resolved_readout.replace(",", "_")
            output_name = f"{step.name}_{channel.lower()}_{harmonic_suffix}_{readout_suffix}"
        transform = str(raw_spec.transform).strip() if raw_spec.transform else ""
        if not transform:
            if source_mode == "ac" and resolved_mode != "dc" and (harmonic in (None, 1)) and "x" in resolved_readout.split(","):
                transform = "lockin_x_over_current"
            else:
                transform = "raw"
        outputs_from_specs[channel] = {"name": output_name, "transform": transform}
    return channels, resolved_specs, outputs_from_specs


def resolve_step(
    *,
    sequence: MeasurementSequence,
    contact_map: ContactMap,
    step: SequenceStep,
    index: int,
    capabilities: InstrumentCapabilities = DEFAULT_INSTRUMENT_CAPABILITIES,
) -> ResolvedSequenceStep:
    if not step.enabled:
        raise SequenceValidationError(f"Disabled steps must be filtered before resolve_step(), got '{step.name}'")
    if not step.name or step.name == "None":
        raise SequenceValidationError(f"steps[{index}].name is required")
    if not step.state or step.state == "None":
        raise SequenceValidationError(f"steps[{index}].state is required")
    if step.state not in contact_map.states:
        raise SequenceValidationError(f"Step '{step.name}' references unknown state '{step.state}'")
    source_mode = _validate_mode(
        step.source_mode or step.excitation_mode or sequence.defaults.source_mode,
        f"Step '{step.name}' source_mode",
    )
    source_quantity = _validate_source_quantity(
        step.source_quantity or sequence.defaults.source_quantity,
        f"Step '{step.name}' source_quantity",
    )
    source_channel = _resolve_source_channel(step, sequence.defaults, capabilities)
    measure_channels = _merged_measure_channels(sequence.defaults, step)
    measure_channels, resolved_measure_specs, outputs_from_specs = _validate_measure_specs(
        measure_specs=_merge_measure_specs(sequence.defaults, step),
        measure_channels=measure_channels,
        source_mode=source_mode,
        step=step,
        defaults=sequence.defaults,
        capabilities=capabilities,
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
    if step.reciprocity_partner and step.reciprocal_step_of and step.reciprocity_partner != step.reciprocal_step_of:
        raise SequenceValidationError(
            f"Step '{step.name}' defines both reciprocity_partner and reciprocal_step_of with different values"
        )
    try:
        outputs_payload = dict(outputs_from_specs)
        outputs_payload.update(step.outputs or {})
        outputs, output_warnings = normalize_output_mapping(outputs_payload, excitation_mode=source_mode)
    except ValueError as exc:
        raise SequenceValidationError(str(exc)) from exc
    warnings.extend(output_warnings)
    resolved_frequency = step.frequency_hz if step.frequency_hz is not None else sequence.defaults.frequency_hz
    legacy_harmonic = step.harmonic if step.harmonic is not None else sequence.defaults.harmonic
    resolved_harmonic = next(
        (
            spec.harmonic
            for spec in resolved_measure_specs.values()
            if spec.harmonic is not None
        ),
        int(legacy_harmonic) if legacy_harmonic is not None else None,
    )
    measure_mode = next(iter(resolved_measure_specs.values())).measure_mode
    readout = next(iter(resolved_measure_specs.values())).readout
    reverse_policy = _resolve_reverse_policy(
        step.reverse_policy or sequence.defaults.reverse_policy,
        source_mode=source_mode,
    )
    matrix_policy = _resolve_matrix_policy(
        step.matrix_policy or sequence.defaults.matrix_policy,
        context=f"Step '{step.name}' matrix_policy",
    )
    source_value = _resolve_source_value(
        step,
        sequence.defaults,
        source_mode=source_mode,
        source_quantity=source_quantity,
    )
    if matrix_policy == "apply_state":
        safety = validate_contact_map_state(contact_map, step.state)
        if not safety.ok:
            raise SequenceValidationError(f"Step '{step.name}' uses unsafe state '{step.state}': {safety.reason}")
    diagnostic_state = step.diagnostic_state
    if reverse_policy == "diagnostic_state":
        if not diagnostic_state:
            raise SequenceValidationError(f"Step '{step.name}' requires diagnostic_state when reverse_policy=diagnostic_state")
        if diagnostic_state not in contact_map.states:
            raise SequenceValidationError(
                f"Step '{step.name}' diagnostic_state '{diagnostic_state}' does not exist in the contact map"
            )
        diagnostic_safety = validate_contact_map_state(contact_map, diagnostic_state)
        if not diagnostic_safety.ok:
            raise SequenceValidationError(
                f"Step '{step.name}' diagnostic_state '{diagnostic_state}' is unsafe: {diagnostic_safety.reason}"
            )
    if source_mode == "dc":
        dc = validate_dc_excitation(
            source=source_channel,
            current_a=source_value if source_quantity == "current" else 1.0,
            bias_polarity=step.bias_polarity,
            settle_s=settle_s,
            error_cls=SequenceValidationError,
        )
        if (step.current_rms_a if step.current_rms_a is not None else sequence.defaults.current_rms_a) is not None:
            raise SequenceValidationError(f"Step '{step.name}' cannot define current_rms_a in dc mode")
        if resolved_frequency is not None:
            raise SequenceValidationError(f"Step '{step.name}' cannot define frequency_hz in dc mode")
        if resolved_harmonic is not None:
            raise SequenceValidationError(f"Step '{step.name}' cannot define harmonic in dc mode")
        return ResolvedSequenceStep(
            index=index,
            enabled=True,
            name=step.name,
            label=step.label,
            state=step.state,
            source_mode=source_mode,
            source_quantity=source_quantity,
            source_value=dc.actual_current_a if source_quantity == "current" else source_value,
            source_channel=dc.source,
            measure_channels=tuple(measure_channels),
            current_a=source_value if source_quantity == "current" else None,
            current_rms_a=None,
            frequency_hz=None,
            harmonic=None,
            bias_polarity=dc.bias_polarity,
            measure_mode=measure_mode,
            readout=readout,
            time_constant_s=step.time_constant_s if step.time_constant_s is not None else sequence.defaults.time_constant_s,
            nplc=step.nplc if step.nplc is not None else sequence.defaults.nplc,
            rolloff=step.rolloff if step.rolloff is not None else sequence.defaults.rolloff,
            measure_specs=resolved_measure_specs,
            settle_s=settle_s,
            repeats=repeats,
            reverse_policy=reverse_policy,
            matrix_policy=matrix_policy,
            diagnostic_state=diagnostic_state,
            lockin=bool(sequence.defaults.lockin) if sequence.defaults.lockin is not None else False,
            measure_kind=step.measure_kind,
            tags=tuple(step.tags or []),
            reciprocity_partner=step.reciprocity_partner or reciprocal_step_of,
            reciprocal_step_of=reciprocal_step_of,
            outputs=outputs,
            notes=step.notes or sequence.defaults.notes,
            metadata={**(sequence.defaults.metadata or {}), **(step.metadata or {})},
            relay_channels=tuple(contact_map.get_state(step.state).get("relay_channels", [])),
            warnings=tuple(warnings),
        )
    ac = validate_ac_excitation(
        source=source_channel,
        current_rms_a=source_value if source_quantity == "current" else 1.0,
        frequency_hz=resolved_frequency,
        harmonic=resolved_harmonic,
        bias_polarity=step.bias_polarity,
        settle_s=settle_s,
        lockin=(measure_mode != "dc") if sequence.defaults.lockin is None else bool(sequence.defaults.lockin),
        expert_mode=sequence.expert_mode,
        error_cls=SequenceValidationError,
    )
    if (step.current_a if step.current_a is not None else sequence.defaults.current_a) is not None:
        raise SequenceValidationError(f"Step '{step.name}' cannot define current_a in ac mode")
    if resolved_frequency is None:
        raise SequenceValidationError(f"Step '{step.name}' must define frequency_hz in ac mode")
    return ResolvedSequenceStep(
        index=index,
        enabled=True,
        name=step.name,
        label=step.label,
        state=step.state,
        source_mode=source_mode,
        source_quantity=source_quantity,
        source_value=source_value,
        source_channel=ac.source,
        measure_channels=tuple(measure_channels),
        current_a=None,
        current_rms_a=source_value if source_quantity == "current" else None,
        frequency_hz=ac.frequency_hz,
        harmonic=ac.harmonic,
        bias_polarity=None if not sequence.expert_mode else step.bias_polarity,
        measure_mode=measure_mode,
        readout=readout,
        time_constant_s=step.time_constant_s if step.time_constant_s is not None else sequence.defaults.time_constant_s,
        nplc=step.nplc if step.nplc is not None else sequence.defaults.nplc,
        rolloff=step.rolloff if step.rolloff is not None else sequence.defaults.rolloff,
        measure_specs=resolved_measure_specs,
        settle_s=settle_s,
        repeats=repeats,
        reverse_policy=reverse_policy,
        matrix_policy=matrix_policy,
        diagnostic_state=diagnostic_state,
        lockin=ac.lockin,
        measure_kind=step.measure_kind,
        tags=tuple(step.tags or []),
        reciprocity_partner=step.reciprocity_partner or reciprocal_step_of,
        reciprocal_step_of=reciprocal_step_of,
        outputs=outputs,
        notes=step.notes or sequence.defaults.notes,
        metadata={**(sequence.defaults.metadata or {}), **(step.metadata or {})},
        relay_channels=tuple(contact_map.get_state(step.state).get("relay_channels", [])),
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
    defaults_mode = _validate_mode(sequence.defaults.source_mode, "defaults.source_mode")
    if defaults_mode == "dc" and (sequence.defaults.current_a is not None or sequence.defaults.source_value is not None):
        validate_dc_excitation(
            source=sequence.defaults.source_channel or sequence.defaults.source,
            current_a=sequence.defaults.current_a or sequence.defaults.source_value,
            bias_polarity=1,
            settle_s=sequence.defaults.settle_s,
            error_cls=SequenceValidationError,
        )
    if defaults_mode == "ac" and any(
        value is not None
        for value in (
            sequence.defaults.current_rms_a,
            sequence.defaults.source_value,
            sequence.defaults.frequency_hz,
            sequence.defaults.harmonic,
            sequence.defaults.measure_specs,
        )
    ):
        default_harmonic = next(
            (
                spec.harmonic
                for spec in (sequence.defaults.measure_specs or {}).values()
                if spec.harmonic is not None
            ),
            sequence.defaults.harmonic,
        )
        if default_harmonic is not None:
            validate_ac_excitation(
                source=sequence.defaults.source_channel or sequence.defaults.source,
                current_rms_a=sequence.defaults.current_rms_a or sequence.defaults.source_value,
                frequency_hz=sequence.defaults.frequency_hz,
                harmonic=default_harmonic,
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
        if step.enabled
    ]
    names = {step.name for step in resolved}
    for step in resolved:
        if step.reciprocal_step_of and step.reciprocal_step_of not in names:
            raise SequenceValidationError(
                f"Step '{step.name}' reciprocal_step_of references unknown step '{step.reciprocal_step_of}'"
            )
    return resolved


def resolve_bias_points(step: ResolvedSequenceStep) -> list[BiasPoint]:
    if step.source_mode == "dc" and step.reverse_policy == "dc_source_inversion":
        return [
            BiasPoint(
                step_index=step.index,
                step_name=step.name,
                state=step.state,
                source_mode=step.source_mode,
                source_quantity=step.source_quantity,
                source_value=abs(step.source_value),
                source_polarity=1,
                reverse_policy=step.reverse_policy,
            ),
            BiasPoint(
                step_index=step.index,
                step_name=step.name,
                state=step.state,
                source_mode=step.source_mode,
                source_quantity=step.source_quantity,
                source_value=-abs(step.source_value),
                source_polarity=-1,
                reverse_policy=step.reverse_policy,
            ),
        ]
    explicit_polarity = step.bias_polarity if step.bias_polarity in {1, -1} else 1
    points = [
        BiasPoint(
            step_index=step.index,
            step_name=step.name,
            state=step.state,
            source_mode=step.source_mode,
            source_quantity=step.source_quantity,
            source_value=abs(step.source_value) * explicit_polarity,
            source_polarity=explicit_polarity,
            reverse_policy=step.reverse_policy,
        )
    ]
    if step.reverse_policy == "diagnostic_state" and step.diagnostic_state:
        points.append(
            BiasPoint(
                step_index=step.index,
                step_name=step.name,
                state=step.diagnostic_state,
                source_mode=step.source_mode,
                source_quantity=step.source_quantity,
                source_value=abs(step.source_value),
                source_polarity=1,
                reverse_policy=step.reverse_policy,
                diagnostic=True,
            )
        )
    return points
