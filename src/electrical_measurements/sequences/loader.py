from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from ..exceptions import SequenceValidationError
from .schema import ChannelMeasureSpec, MeasurementSequence, SequenceDefaults, SequenceStep

TOP_LEVEL_FIELDS = {"name", "description", "contact_map", "defaults", "steps", "expert_mode"}
DEFAULT_FIELDS = {
    "source_mode",
    "source_quantity",
    "source_value",
    "excitation_mode",
    "source",
    "source_channel",
    "measure_channel",
    "measure_channels",
    "current_a",
    "current_rms_a",
    "frequency_hz",
    "harmonic",
    "measure_mode",
    "readout",
    "time_constant_s",
    "nplc",
    "rolloff",
    "measure_specs",
    "settle_s",
    "repeats",
    "reverse_policy",
    "matrix_policy",
    "notes",
    "lockin",
    "metadata",
}
STEP_FIELDS = {
    "enabled",
    "order",
    "name",
    "label",
    "state",
    "source_mode",
    "source_quantity",
    "source_value",
    "excitation_mode",
    "source",
    "source_channel",
    "measure_channel",
    "measure_channels",
    "current_a",
    "current_rms_a",
    "frequency_hz",
    "harmonic",
    "bias_polarity",
    "settle_s",
    "repeats",
    "measure_mode",
    "readout",
    "time_constant_s",
    "nplc",
    "rolloff",
    "measure_specs",
    "reverse_policy",
    "matrix_policy",
    "diagnostic_state",
    "measure_kind",
    "tags",
    "repeat_of",
    "reciprocity_partner",
    "reciprocal_step_of",
    "reciprocal_of",
    "outputs",
    "notes",
    "metadata",
}


def _require_mapping(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SequenceValidationError(f"{context} must be a mapping")
    return dict(value)


def _reject_unknown_fields(data: dict[str, Any], allowed: set[str], context: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise SequenceValidationError(f"Unknown fields in {context}: {unknown}")


def _as_optional_str_list(value: Any, context: str) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SequenceValidationError(f"{context} must be a list of strings")
    return list(value)


def _as_optional_outputs(value: Any, context: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise SequenceValidationError(f"{context} must be a mapping keyed by strings")
    return dict(value)


def _as_optional_metadata(value: Any, context: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise SequenceValidationError(f"{context} must be a mapping")
    return dict(value)


def _as_optional_measure_specs(value: Any, context: str) -> dict[str, ChannelMeasureSpec] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise SequenceValidationError(f"{context} must be a mapping keyed by measurement channel")
    specs: dict[str, ChannelMeasureSpec] = {}
    for channel, raw_spec in value.items():
        if not isinstance(channel, str):
            raise SequenceValidationError(f"{context} keys must be strings")
        if not isinstance(raw_spec, dict):
            raise SequenceValidationError(f"{context}.{channel} must be a mapping")
        payload = dict(raw_spec)
        specs[channel] = ChannelMeasureSpec(
            measure_mode=str(payload.get("measure_mode", "auto")),
            harmonic=payload.get("harmonic"),
            readout=_normalize_readout_value(payload.get("readout", "value"), f"{context}.{channel}.readout"),
            output=payload.get("output"),
            transform=payload.get("transform"),
            time_constant_s=payload.get("time_constant_s"),
            rolloff=payload.get("rolloff"),
            nplc=payload.get("nplc"),
        )
    return specs


def _normalize_readout_value(value: Any, context: str) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return ",".join(value)
    raise SequenceValidationError(f"{context} must be a string or list of strings")


def _load_defaults(data: Any) -> SequenceDefaults:
    payload = _require_mapping(data, "defaults")
    _reject_unknown_fields(payload, DEFAULT_FIELDS, "defaults")
    source_mode = payload.get("source_mode", payload.get("excitation_mode"))
    source_channel = payload.get("source_channel", payload.get("source"))
    return SequenceDefaults(
        source_mode=str(source_mode),
        source_quantity=str(payload.get("source_quantity", "current")),
        source_value=payload.get("source_value"),
        source=payload.get("source"),
        source_channel=source_channel,
        measure_channel=payload.get("measure_channel"),
        measure_channels=_as_optional_str_list(payload.get("measure_channels"), "defaults.measure_channels"),
        current_a=payload.get("current_a"),
        current_rms_a=payload.get("current_rms_a"),
        frequency_hz=payload.get("frequency_hz"),
        harmonic=payload.get("harmonic"),
        measure_mode=payload.get("measure_mode"),
        readout=_normalize_readout_value(payload.get("readout"), "defaults.readout") if payload.get("readout") is not None else None,
        time_constant_s=payload.get("time_constant_s"),
        nplc=payload.get("nplc"),
        rolloff=payload.get("rolloff"),
        measure_specs=_as_optional_measure_specs(payload.get("measure_specs"), "defaults.measure_specs"),
        settle_s=float(payload.get("settle_s", 0.0)),
        repeats=int(payload.get("repeats", 1)),
        reverse_policy=str(payload.get("reverse_policy", "none")),
        matrix_policy=str(payload.get("matrix_policy", "apply_state")),
        notes=payload.get("notes"),
        lockin=payload.get("lockin"),
        metadata=_as_optional_metadata(payload.get("metadata"), "defaults.metadata"),
    )


def _load_step(index: int, data: Any) -> SequenceStep:
    payload = _require_mapping(data, f"steps[{index}]")
    _reject_unknown_fields(payload, STEP_FIELDS, f"steps[{index}]")
    source_mode = payload.get("source_mode", payload.get("excitation_mode"))
    source_channel = payload.get("source_channel", payload.get("source"))
    return SequenceStep(
        enabled=bool(payload.get("enabled", True)),
        order=payload.get("order"),
        name=str(payload.get("name")),
        state=str(payload.get("state")),
        label=payload.get("label"),
        source_mode=source_mode,
        source_quantity=payload.get("source_quantity"),
        source_value=payload.get("source_value"),
        excitation_mode=payload.get("excitation_mode"),
        source=payload.get("source"),
        source_channel=source_channel,
        measure_channel=payload.get("measure_channel"),
        measure_channels=_as_optional_str_list(payload.get("measure_channels"), f"steps[{index}].measure_channels"),
        current_a=payload.get("current_a"),
        current_rms_a=payload.get("current_rms_a"),
        frequency_hz=payload.get("frequency_hz"),
        harmonic=payload.get("harmonic"),
        bias_polarity=payload.get("bias_polarity"),
        settle_s=payload.get("settle_s"),
        repeats=payload.get("repeats"),
        measure_mode=payload.get("measure_mode"),
        readout=_normalize_readout_value(payload.get("readout"), f"steps[{index}].readout") if payload.get("readout") is not None else None,
        time_constant_s=payload.get("time_constant_s"),
        nplc=payload.get("nplc"),
        rolloff=payload.get("rolloff"),
        measure_specs=_as_optional_measure_specs(payload.get("measure_specs"), f"steps[{index}].measure_specs"),
        reverse_policy=payload.get("reverse_policy"),
        matrix_policy=payload.get("matrix_policy"),
        diagnostic_state=payload.get("diagnostic_state"),
        measure_kind=payload.get("measure_kind"),
        tags=_as_optional_str_list(payload.get("tags"), f"steps[{index}].tags"),
        repeat_of=payload.get("repeat_of"),
        reciprocity_partner=payload.get("reciprocity_partner"),
        reciprocal_step_of=payload.get("reciprocal_step_of"),
        reciprocal_of=payload.get("reciprocal_of"),
        outputs=_as_optional_outputs(payload.get("outputs"), f"steps[{index}].outputs"),
        notes=payload.get("notes"),
        metadata=_as_optional_metadata(payload.get("metadata"), f"steps[{index}].metadata"),
    )


def measurement_sequence_from_dict(data: dict[str, Any], *, path: str | Path | None = None) -> MeasurementSequence:
    raw_payload = data.get("sequence") if isinstance(data, dict) and "sequence" in data else data
    payload = _require_mapping(raw_payload, "sequence")
    _reject_unknown_fields(payload, TOP_LEVEL_FIELDS, "sequence")
    if "name" not in payload:
        raise SequenceValidationError("Sequence must define name")
    if "defaults" not in payload:
        raise SequenceValidationError("Sequence must define defaults")
    if "steps" not in payload:
        raise SequenceValidationError("Sequence must define steps")
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise SequenceValidationError("Sequence must define at least one step")
    sequence = MeasurementSequence(
        name=str(payload["name"]),
        description=payload.get("description"),
        contact_map=payload.get("contact_map"),
        defaults=_load_defaults(payload["defaults"]),
        steps=[_load_step(index, item) for index, item in enumerate(raw_steps)],
        expert_mode=bool(payload.get("expert_mode", False)),
        path=Path(path) if path is not None else None,
    )
    names = [step.name for step in sequence.steps]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise SequenceValidationError(f"Duplicate step names are not allowed: {duplicates}")
    return sequence


def measurement_sequence_to_dict(sequence: MeasurementSequence) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": sequence.name,
        "defaults": {
            "source_mode": sequence.defaults.source_mode,
            "source_quantity": sequence.defaults.source_quantity,
            "settle_s": sequence.defaults.settle_s,
            "repeats": sequence.defaults.repeats,
            "reverse_policy": sequence.defaults.reverse_policy,
        },
        "steps": [],
    }
    if sequence.description:
        data["description"] = sequence.description
    if sequence.contact_map:
        data["contact_map"] = sequence.contact_map
    if sequence.expert_mode:
        data["expert_mode"] = True
    optional_defaults = {
        "source_value": sequence.defaults.source_value,
        "source": sequence.defaults.source,
        "source_channel": sequence.defaults.source_channel,
        "measure_channel": sequence.defaults.measure_channel,
        "measure_channels": sequence.defaults.measure_channels,
        "current_a": sequence.defaults.current_a,
        "current_rms_a": sequence.defaults.current_rms_a,
        "frequency_hz": sequence.defaults.frequency_hz,
        "harmonic": sequence.defaults.harmonic,
        "measure_mode": sequence.defaults.measure_mode,
        "readout": sequence.defaults.readout,
        "time_constant_s": sequence.defaults.time_constant_s,
        "nplc": sequence.defaults.nplc,
            "rolloff": sequence.defaults.rolloff,
            "measure_specs": (
                {
                    channel: {
                        "measure_mode": spec.measure_mode,
                        "harmonic": spec.harmonic,
                        "readout": spec.readout,
                        "output": spec.output,
                        "transform": spec.transform,
                        "time_constant_s": spec.time_constant_s,
                        "rolloff": spec.rolloff,
                        "nplc": spec.nplc,
                    }
                    for channel, spec in sequence.defaults.measure_specs.items()
                }
                if sequence.defaults.measure_specs is not None
                else None
            ),
            "matrix_policy": sequence.defaults.matrix_policy,
            "notes": sequence.defaults.notes,
            "lockin": sequence.defaults.lockin,
            "metadata": sequence.defaults.metadata,
    }
    for key, value in optional_defaults.items():
        if value is not None:
            data["defaults"][key] = value
    for step in sequence.steps:
        step_data: dict[str, Any] = {
            "name": step.name,
            "state": step.state,
            "enabled": step.enabled,
        }
        optional_step_fields = {
            "order": step.order,
            "label": step.label,
            "source_mode": step.source_mode or step.excitation_mode,
            "source_quantity": step.source_quantity,
            "source_value": step.source_value,
            "excitation_mode": step.excitation_mode,
            "source": step.source,
            "source_channel": step.source_channel,
            "measure_channel": step.measure_channel,
            "measure_channels": step.measure_channels,
            "current_a": step.current_a,
            "current_rms_a": step.current_rms_a,
            "frequency_hz": step.frequency_hz,
            "harmonic": step.harmonic,
            "bias_polarity": step.bias_polarity,
            "settle_s": step.settle_s,
            "repeats": step.repeats,
            "measure_mode": step.measure_mode,
            "readout": step.readout,
            "time_constant_s": step.time_constant_s,
            "nplc": step.nplc,
            "rolloff": step.rolloff,
            "measure_specs": (
                {
                    channel: {
                        "measure_mode": spec.measure_mode,
                        "harmonic": spec.harmonic,
                        "readout": spec.readout,
                        "output": spec.output,
                        "transform": spec.transform,
                        "time_constant_s": spec.time_constant_s,
                        "rolloff": spec.rolloff,
                        "nplc": spec.nplc,
                    }
                    for channel, spec in step.measure_specs.items()
                }
                if step.measure_specs is not None
                else None
            ),
            "reverse_policy": step.reverse_policy,
            "matrix_policy": step.matrix_policy,
            "diagnostic_state": step.diagnostic_state,
            "measure_kind": step.measure_kind,
            "tags": step.tags,
            "repeat_of": step.repeat_of,
            "reciprocity_partner": step.reciprocity_partner,
            "reciprocal_step_of": step.reciprocal_step_of,
            "reciprocal_of": step.reciprocal_of,
            "outputs": step.outputs,
            "notes": step.notes,
            "metadata": step.metadata,
        }
        for key, value in optional_step_fields.items():
            if value is not None:
                step_data[key] = value
        data["steps"].append(step_data)
    return data


def load_measurement_sequence(path: str | Path) -> MeasurementSequence:
    sequence_path = Path(path)
    text = sequence_path.read_text()
    if sequence_path.suffix.lower() == ".json":
        data = json.loads(text)
    else:
        data = yaml.safe_load(text)
    return measurement_sequence_from_dict(data, path=sequence_path)


def save_measurement_sequence(sequence: MeasurementSequence, path: str | Path) -> Path:
    sequence_path = Path(path)
    payload = measurement_sequence_to_dict(sequence)
    if sequence_path.suffix.lower() == ".json":
        sequence_path.write_text(json.dumps(payload, indent=2))
    else:
        sequence_path.write_text(yaml.safe_dump(payload, sort_keys=False))
    return sequence_path
