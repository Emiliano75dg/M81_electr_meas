from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any

LOGGER = logging.getLogger(__name__)

SUPPORTED_OUTPUT_TRANSFORMS = {
    "raw",
    "voltage_over_current",
    "lockin_r_over_current",
    "lockin_x_over_current",
    "lockin_y_over_current",
}


@dataclass(frozen=True)
class OutputSpec:
    name: str
    transform: str


def normalize_output_mapping(
    outputs: dict[str, Any] | None,
    *,
    excitation_mode: str,
) -> tuple[dict[str, OutputSpec], list[str]]:
    normalized: dict[str, OutputSpec] = {}
    warnings: list[str] = []
    for channel, raw_spec in (outputs or {}).items():
        if isinstance(raw_spec, str):
            name = raw_spec
            lowered = name.strip().lower()
            if lowered.endswith("_ohm") or lowered.startswith("r"):
                transform = "voltage_over_current" if excitation_mode == "dc" else "lockin_x_over_current"
            else:
                transform = "raw"
                warnings.append(f"Output {channel}:{name} defaulted to raw transform")
            normalized[channel] = OutputSpec(name=name, transform=transform)
            continue
        if isinstance(raw_spec, dict):
            name = str(raw_spec.get("name", "")).strip()
            transform = str(raw_spec.get("transform", "raw")).strip()
            if not name:
                raise ValueError(f"Output spec for channel {channel} must define a name")
            if transform not in SUPPORTED_OUTPUT_TRANSFORMS:
                raise ValueError(f"Unsupported output transform for channel {channel}: {transform}")
            normalized[channel] = OutputSpec(name=name, transform=transform)
            continue
        raise ValueError(f"Unsupported output spec for channel {channel}: {raw_spec}")
    return normalized, warnings


def apply_output_transform(
    output: OutputSpec,
    reading: dict[str, Any],
    *,
    dc_current_a: float | None,
    ac_current_rms_a: float | None,
) -> float | None:
    if output.transform == "raw":
        return reading.get("value") if "value" in reading else reading.get("x")
    if output.transform == "voltage_over_current":
        denominator = abs(dc_current_a) if dc_current_a is not None else ac_current_rms_a
        numerator = reading.get("value") if "value" in reading else reading.get("x")
        return None if denominator in (None, 0) or numerator is None else float(numerator) / float(denominator)
    if output.transform == "lockin_r_over_current":
        numerator = reading.get("r")
        return None if ac_current_rms_a in (None, 0) or numerator is None else float(numerator) / float(ac_current_rms_a)
    if output.transform == "lockin_x_over_current":
        numerator = reading.get("x")
        return None if ac_current_rms_a in (None, 0) or numerator is None else float(numerator) / float(ac_current_rms_a)
    if output.transform == "lockin_y_over_current":
        numerator = reading.get("y")
        return None if ac_current_rms_a in (None, 0) or numerator is None else float(numerator) / float(ac_current_rms_a)
    raise ValueError(f"Unsupported transform: {output.transform}")


@dataclass
class MeasurementRecordBuilder:
    context_name: str

    def build(
        self,
        *,
        sample_id: str,
        geometry: str,
        step_index: int | None,
        step_name: str | None,
        state_name: str,
        relay_channels: tuple[int, ...] | list[int],
        excitation_mode: str,
        source_channel: str,
        measure_channels: list[str],
        readings: dict[str, dict[str, Any]],
        outputs: dict[str, OutputSpec],
        tags: list[str] | None = None,
        reciprocal_step_of: str | None = None,
        timestamp: str | None = None,
        temperature_k: float | None = None,
        field_t: float | None = None,
        dc_current_a: float | None = None,
        bias_polarity: int | None = None,
        ac_current_rms_a: float | None = None,
        frequency_hz: float | None = None,
        harmonic: int | None = None,
        metadata: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record: dict[str, Any] = {
            "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
            "sample_id": sample_id,
            "protocol": self.context_name,
            "geometry": geometry,
            "step_index": step_index,
            "step_name": step_name,
            "state": state_name,
            "matrix_relay_channels": list(relay_channels),
            "excitation_mode": excitation_mode,
            "source_channel": source_channel,
            "measure_channel": measure_channels[0] if measure_channels else None,
            "measure_channels": list(measure_channels),
            "temperature_k": temperature_k,
            "field_t": field_t,
            "bias_polarity": bias_polarity,
            "source_current_a_dc": dc_current_a,
            "source_current_a_rms": ac_current_rms_a,
            "source_current_a_peak": None if ac_current_rms_a is None else ac_current_rms_a * 2**0.5,
            "frequency_hz": frequency_hz,
            "harmonic": harmonic,
            "tags": list(tags or []),
            "reciprocal_step_of": reciprocal_step_of,
            "metadata": dict(metadata or {}),
        }
        for channel, reading in readings.items():
            prefix = channel.lower()
            if excitation_mode == "dc":
                record[f"{prefix}_dc_value"] = reading.get("value")
            else:
                record[f"{prefix}_lockin_x"] = reading.get("x")
                record[f"{prefix}_lockin_y"] = reading.get("y")
                record[f"{prefix}_lockin_r"] = reading.get("r")
                record[f"{prefix}_lockin_theta_deg"] = reading.get("theta_deg")
        primary_channel = measure_channels[0] if measure_channels else None
        if primary_channel:
            primary = readings.get(primary_channel, {})
            if excitation_mode == "dc":
                record["dc_value"] = primary.get("value")
            else:
                record["lockin_x"] = primary.get("x")
                record["lockin_y"] = primary.get("y")
                record["lockin_r"] = primary.get("r")
                record["lockin_theta_deg"] = primary.get("theta_deg")
        for channel, output in outputs.items():
            value = apply_output_transform(
                output,
                readings.get(channel, {}),
                dc_current_a=dc_current_a,
                ac_current_rms_a=ac_current_rms_a,
            )
            record[output.name] = value
        if extra:
            record.update(extra)
        return record
