from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import time
from typing import Any

import pandas as pd

from ..switching.contact_map import ContactMap
from .schema import MeasurementSequence, ResolvedSequenceStep


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _measurement_value(excitation_mode: str, reading: dict[str, Any]) -> float | None:
    key = "x" if excitation_mode == "ac" else "value"
    value = reading.get(key)
    return None if value is None else float(value)


def _semantic_is_resistance_like(name: str) -> bool:
    lowered = name.strip().lower()
    return lowered.endswith("_ohm") or (lowered.startswith("r") and "voltage" not in lowered)


@dataclass
class SequenceRunner:
    sequence: MeasurementSequence
    contact_map: ContactMap
    resolved_steps: list[ResolvedSequenceStep]
    m81: Any | None = None
    matrix: Any | None = None
    dry_run: bool = False
    sample_id: str = "sample"

    def build_preview_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for step in self.resolved_steps:
            rows.append(
                {
                    "idx": step.index + 1,
                    "name": step.name,
                    "state": step.state,
                    "mode": step.excitation_mode,
                    "source": step.source,
                    "current": (
                        f"{step.actual_dc_current_a:.6g} A"
                        if step.excitation_mode == "dc" and step.actual_dc_current_a is not None
                        else f"{step.current_rms_a:.6g} Arms @ {step.frequency_hz:.6g} Hz h{step.harmonic}"
                    ),
                    "measure": ",".join(step.measure_channels),
                    "outputs": ",".join(f"{channel}:{name}" for channel, name in step.outputs.items()),
                    "relays": ",".join(str(channel) for channel in step.relay_channels),
                    "tags": ",".join(step.tags),
                    "reciprocal_of": step.reciprocal_of or "",
                }
            )
        return rows

    def format_preview(self) -> str:
        headers = ["idx", "name", "state", "mode", "source", "current", "measure", "outputs", "relays", "tags", "reciprocal_of"]
        rows = self.build_preview_rows()
        widths = {
            header: max(len(header), *(len(str(row.get(header, ""))) for row in rows))
            for header in headers
        }
        lines = [
            " ".join(header.ljust(widths[header]) for header in headers),
            " ".join("-" * widths[header] for header in headers),
        ]
        for row in rows:
            lines.append(" ".join(str(row.get(header, "")).ljust(widths[header]) for header in headers))
        return "\n".join(lines)

    def _configure_measurement_channels(self, step: ResolvedSequenceStep) -> None:
        if self.m81 is None:
            return
        if step.excitation_mode == "dc":
            for measure_channel in step.measure_channels:
                self.m81.configure_dc_measure(measure_channel)
            self.m81.configure_dc_current(step.source, step.actual_dc_current_a or 0.0)
            return
        self.m81.configure_ac_current_lockin(
            source=step.source,
            current_rms_a=step.current_rms_a or 0.0,
            frequency_hz=step.frequency_hz or 0.0,
            measure_channels=step.measure_channels,
            harmonic=step.harmonic or 1,
        )

    def _read_channels(self, step: ResolvedSequenceStep) -> dict[str, dict[str, Any]]:
        if self.m81 is None:
            return {}
        if step.excitation_mode == "dc":
            return {channel: self.m81.read_dc(channel) for channel in step.measure_channels}
        return {channel: self.m81.read_lockin(channel) for channel in step.measure_channels}

    def _merge_stream_trace_into_readings(
        self,
        step: ResolvedSequenceStep,
        trace_row: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        readings = self._read_channels(step)
        primary = step.primary_measure_channel
        if primary is None:
            return readings
        primary_reading = dict(readings.get(primary, {}))
        primary_reading.update(
            {
                "timestamp": trace_row.get("timestamp", primary_reading.get("timestamp")),
                "x": trace_row.get("x"),
                "y": trace_row.get("y"),
                "r": trace_row.get("r"),
                "theta_deg": trace_row.get("theta_deg"),
                "frequency_hz": trace_row.get("frequency_hz"),
                "harmonic": trace_row.get("harmonic"),
            }
        )
        readings[primary] = primary_reading
        return readings

    def _build_record(
        self,
        *,
        step: ResolvedSequenceStep,
        repeat_index: int,
        readings: dict[str, dict[str, Any]],
        temperature_k: float | None,
        field_t: float | None,
    ) -> dict[str, Any]:
        record: dict[str, Any] = {
            "timestamp": _utcnow(),
            "sample_id": self.sample_id,
            "protocol": "sequence",
            "sequence_name": self.sequence.name,
            "sequence_description": self.sequence.description,
            "geometry": self.contact_map.name,
            "step_name": step.name,
            "step_index": step.index,
            "repeat_index": repeat_index,
            "state": step.state,
            "reciprocal_of": step.reciprocal_of,
            "matrix_relay_channels": step.relay_channels,
            "temperature_k": temperature_k,
            "field_t": field_t,
            "excitation_mode": step.excitation_mode,
            "source_channel": step.source,
            "measure_channel": step.primary_measure_channel,
            "measure_channels": list(step.measure_channels),
            "measure_kind": step.measure_kind,
            "tags": list(step.tags),
            "outputs": dict(step.outputs),
            "bias_polarity": step.bias_polarity,
            "source_current_a_dc": step.actual_dc_current_a,
            "source_current_a_rms": step.current_rms_a,
            "source_current_a_peak": None if step.current_rms_a is None else step.current_rms_a * 2**0.5,
            "frequency_hz": step.frequency_hz,
            "harmonic": step.harmonic,
            "metadata": dict(step.metadata),
        }
        for channel, reading in readings.items():
            prefix = channel.lower()
            if step.excitation_mode == "dc":
                record[f"{prefix}_dc_value"] = reading.get("value")
            else:
                record[f"{prefix}_lockin_x"] = reading.get("x")
                record[f"{prefix}_lockin_y"] = reading.get("y")
                record[f"{prefix}_lockin_r"] = reading.get("r")
                record[f"{prefix}_lockin_theta_deg"] = reading.get("theta_deg")
        for channel, semantic in step.outputs.items():
            reading = readings.get(channel, {})
            value = _measurement_value(step.excitation_mode, reading)
            if value is not None:
                record[semantic] = value if not _semantic_is_resistance_like(semantic) else (
                    value / (abs(step.actual_dc_current_a) if step.excitation_mode == "dc" else (step.current_rms_a or 0.0))
                )
        if step.primary_measure_channel:
            primary = readings.get(step.primary_measure_channel, {})
            if step.excitation_mode == "dc":
                record["dc_value"] = primary.get("value")
            else:
                record["lockin_x"] = primary.get("x")
                record["lockin_y"] = primary.get("y")
                record["lockin_r"] = primary.get("r")
                record["lockin_theta_deg"] = primary.get("theta_deg")
        return record

    def run(self, *, temperature_k: float | None = None, field_t: float | None = None) -> pd.DataFrame:
        if self.dry_run:
            return pd.DataFrame(self.build_preview_rows())
        if self.m81 is None or self.matrix is None:
            raise RuntimeError("SequenceRunner requires m81 and matrix in non-dry-run mode")
        rows: list[dict[str, Any]] = []
        for step in self.resolved_steps:
            self.m81.disable_all_sources()
            relay_channels = self.matrix.apply_state(step.state)
            if relay_channels:
                step.relay_channels[:] = relay_channels
            if step.settle_s > 0:
                time.sleep(step.settle_s)
            self._configure_measurement_channels(step)
            self.m81.enable_source(step.source)
            try:
                for repeat_index in range(step.repeats):
                    readings = self._read_channels(step)
                    rows.append(
                        self._build_record(
                            step=step,
                            repeat_index=repeat_index,
                            readings=readings,
                            temperature_k=temperature_k,
                            field_t=field_t,
                        )
                    )
            finally:
                self.m81.disable_all_sources()
        return pd.DataFrame(rows)

    def run_stream(
        self,
        *,
        environment: Any,
        stream_samples: int,
        stream_interval: float,
        temperature_k: float | None = None,
        field_t: float | None = None,
    ) -> pd.DataFrame:
        if self.dry_run:
            return pd.DataFrame(self.build_preview_rows())
        if self.m81 is None or self.matrix is None:
            raise RuntimeError("SequenceRunner requires m81 and matrix in non-dry-run mode")
        rows: list[dict[str, Any]] = []
        for step in self.resolved_steps:
            if step.excitation_mode != "ac":
                raise RuntimeError(f"Streaming sequence mode currently supports only AC steps, got '{step.excitation_mode}' for step '{step.name}'")
            if step.primary_measure_channel is None:
                raise RuntimeError(f"Streaming sequence step '{step.name}' requires at least one measurement channel")
            self.m81.disable_all_sources()
            relay_channels = self.matrix.apply_state(step.state)
            if relay_channels:
                step.relay_channels[:] = relay_channels
            if step.settle_s > 0:
                time.sleep(step.settle_s)
            self._configure_measurement_channels(step)
            initial_temperature = environment.read_temperature() if hasattr(environment, "read_temperature") else temperature_k
            initial_field = environment.read_field() if hasattr(environment, "read_field") else field_t
            if hasattr(self.m81, "temperature_k") and initial_temperature is not None:
                self.m81.temperature_k = initial_temperature
            if hasattr(self.m81, "field_t") and initial_field is not None:
                self.m81.field_t = initial_field
            self.m81.configure_trace_stream(channel=step.primary_measure_channel, points=stream_samples, interval_s=stream_interval)
            self.m81.enable_source(step.source)
            self.m81.start_trace()
            try:
                for _ in range(stream_samples):
                    if hasattr(environment, "advance_time"):
                        environment.advance_time(stream_interval)
                    current_temperature = environment.read_temperature() if hasattr(environment, "read_temperature") else temperature_k
                    current_field = environment.read_field() if hasattr(environment, "read_field") else field_t
                    if hasattr(self.m81, "temperature_k") and current_temperature is not None:
                        self.m81.temperature_k = current_temperature
                    if hasattr(self.m81, "field_t") and current_field is not None:
                        self.m81.field_t = current_field
                    batch = self.m81.fetch_trace(max_points=1)
                    if not batch:
                        continue
                    trace_row = batch[0]
                    readings = self._merge_stream_trace_into_readings(step, trace_row)
                    record = self._build_record(
                        step=step,
                        repeat_index=int(trace_row.get("trace_index", 0)),
                        readings=readings,
                        temperature_k=current_temperature,
                        field_t=current_field,
                    )
                    record.update(
                        {
                            "protocol": "sequence_stream",
                            "trace_channel": trace_row.get("trace_channel", step.primary_measure_channel),
                            "trace_index": trace_row.get("trace_index"),
                            "temperature_k_initial": initial_temperature,
                            "field_t_initial": initial_field,
                            "temperature_k_final": current_temperature,
                            "field_t_final": current_field,
                        }
                    )
                    rows.append(record)
                    time.sleep(min(stream_interval, 0.01))
            finally:
                self.m81.abort_sweep()
                self.m81.disable_all_sources()
        return pd.DataFrame(rows)
