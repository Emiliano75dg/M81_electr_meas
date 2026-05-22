from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..exceptions import SequenceValidationError
from .schema import MeasurementSequence, SequenceDefaults, SequenceStep

TOP_LEVEL_FIELDS = {"name", "description", "contact_map", "defaults", "steps", "expert_mode"}
DEFAULT_FIELDS = {
    "excitation_mode",
    "source",
    "measure_channel",
    "measure_channels",
    "current_a",
    "current_rms_a",
    "frequency_hz",
    "harmonic",
    "settle_s",
    "repeats",
    "lockin",
    "metadata",
}
STEP_FIELDS = {
    "name",
    "state",
    "excitation_mode",
    "source",
    "measure_channel",
    "measure_channels",
    "current_a",
    "current_rms_a",
    "frequency_hz",
    "harmonic",
    "bias_polarity",
    "settle_s",
    "repeats",
    "measure_kind",
    "tags",
    "reciprocal_step_of",
    "reciprocal_of",
    "outputs",
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


def _load_defaults(data: Any) -> SequenceDefaults:
    payload = _require_mapping(data, "defaults")
    _reject_unknown_fields(payload, DEFAULT_FIELDS, "defaults")
    return SequenceDefaults(
        excitation_mode=str(payload.get("excitation_mode")),
        source=payload.get("source"),
        measure_channel=payload.get("measure_channel"),
        measure_channels=_as_optional_str_list(payload.get("measure_channels"), "defaults.measure_channels"),
        current_a=payload.get("current_a"),
        current_rms_a=payload.get("current_rms_a"),
        frequency_hz=payload.get("frequency_hz"),
        harmonic=payload.get("harmonic"),
        settle_s=float(payload.get("settle_s", 0.0)),
        repeats=int(payload.get("repeats", 1)),
        lockin=payload.get("lockin"),
        metadata=_as_optional_metadata(payload.get("metadata"), "defaults.metadata"),
    )


def _load_step(index: int, data: Any) -> SequenceStep:
    payload = _require_mapping(data, f"steps[{index}]")
    _reject_unknown_fields(payload, STEP_FIELDS, f"steps[{index}]")
    return SequenceStep(
        name=str(payload.get("name")),
        state=str(payload.get("state")),
        excitation_mode=payload.get("excitation_mode"),
        source=payload.get("source"),
        measure_channel=payload.get("measure_channel"),
        measure_channels=_as_optional_str_list(payload.get("measure_channels"), f"steps[{index}].measure_channels"),
        current_a=payload.get("current_a"),
        current_rms_a=payload.get("current_rms_a"),
        frequency_hz=payload.get("frequency_hz"),
        harmonic=payload.get("harmonic"),
        bias_polarity=payload.get("bias_polarity"),
        settle_s=payload.get("settle_s"),
        repeats=payload.get("repeats"),
        measure_kind=payload.get("measure_kind"),
        tags=_as_optional_str_list(payload.get("tags"), f"steps[{index}].tags"),
        reciprocal_step_of=payload.get("reciprocal_step_of"),
        reciprocal_of=payload.get("reciprocal_of"),
        outputs=_as_optional_outputs(payload.get("outputs"), f"steps[{index}].outputs"),
        metadata=_as_optional_metadata(payload.get("metadata"), f"steps[{index}].metadata"),
    )


def measurement_sequence_from_dict(data: dict[str, Any], *, path: str | Path | None = None) -> MeasurementSequence:
    payload = _require_mapping(data, "sequence")
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
            "excitation_mode": sequence.defaults.excitation_mode,
            "settle_s": sequence.defaults.settle_s,
            "repeats": sequence.defaults.repeats,
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
        "source": sequence.defaults.source,
        "measure_channel": sequence.defaults.measure_channel,
        "measure_channels": sequence.defaults.measure_channels,
        "current_a": sequence.defaults.current_a,
        "current_rms_a": sequence.defaults.current_rms_a,
        "frequency_hz": sequence.defaults.frequency_hz,
        "harmonic": sequence.defaults.harmonic,
        "lockin": sequence.defaults.lockin,
        "metadata": sequence.defaults.metadata,
    }
    for key, value in optional_defaults.items():
        if value is not None:
            data["defaults"][key] = value
    for step in sequence.steps:
        step_data: dict[str, Any] = {"name": step.name, "state": step.state}
        optional_step_fields = {
            "excitation_mode": step.excitation_mode,
            "source": step.source,
            "measure_channel": step.measure_channel,
            "measure_channels": step.measure_channels,
            "current_a": step.current_a,
            "current_rms_a": step.current_rms_a,
            "frequency_hz": step.frequency_hz,
            "harmonic": step.harmonic,
            "bias_polarity": step.bias_polarity,
            "settle_s": step.settle_s,
            "repeats": step.repeats,
            "measure_kind": step.measure_kind,
            "tags": step.tags,
            "reciprocal_step_of": step.reciprocal_step_of,
            "reciprocal_of": step.reciprocal_of,
            "outputs": step.outputs,
            "metadata": step.metadata,
        }
        for key, value in optional_step_fields.items():
            if value is not None:
                step_data[key] = value
        data["steps"].append(step_data)
    return data


def load_measurement_sequence(path: str | Path) -> MeasurementSequence:
    yaml_path = Path(path)
    data = yaml.safe_load(yaml_path.read_text())
    return measurement_sequence_from_dict(data, path=yaml_path)
