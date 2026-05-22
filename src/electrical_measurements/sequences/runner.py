from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import time
from typing import Any

import pandas as pd

from ..excitation import ACExcitation, DCExcitation, configure_m81_source_for_ac, configure_m81_source_for_dc
from ..records import MeasurementRecordBuilder
from ..switching.contact_map import ContactMap
from .schema import MeasurementSequence, ResolvedSequenceStep
from .validator import resolve_bias_points


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SequenceRunner:
    sequence: MeasurementSequence
    contact_map: ContactMap
    resolved_steps: list[ResolvedSequenceStep]
    m81: Any | None = None
    matrix: Any | None = None
    dry_run: bool = False
    sample_id: str = "sample"

    def __post_init__(self) -> None:
        self.record_builder = MeasurementRecordBuilder(context_name="sequence")

    def build_preview_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for step in self.resolved_steps:
            bias_points = resolve_bias_points(step)
            rows.append(
                {
                    "idx": step.index + 1,
                    "name": step.name,
                    "state": step.state,
                    "enabled": "yes" if step.enabled else "no",
                    "relays": ",".join(str(channel) for channel in step.relay_channels),
                    "source_mode": step.source_mode,
                    "source_quantity": step.source_quantity,
                    "source_value": f"{step.source_value:.6g}",
                    "resolved_bias": ",".join(f"{point.source_value:.6g}@{point.state}" for point in bias_points),
                    "dc_current_a": (
                        f"{step.actual_dc_current_a:.6g}" if step.source_mode == "dc" and step.actual_dc_current_a is not None else ""
                    ),
                    "current_rms_a": (
                        f"{step.current_rms_a:.6g}" if step.source_mode == "ac" and step.current_rms_a is not None else ""
                    ),
                    "frequency_hz": (
                        f"{step.frequency_hz:.6g}" if step.source_mode == "ac" and step.frequency_hz is not None else ""
                    ),
                    "harmonic": step.harmonic if step.source_mode == "ac" and step.harmonic is not None else "",
                    "source": step.source_channel,
                    "measure_mode": step.measure_mode,
                    "readout": step.readout,
                    "measure_channels": ",".join(step.measure_channels),
                    "outputs": ",".join(f"{channel}:{spec.name}/{spec.transform}" for channel, spec in step.outputs.items()),
                    "settle_s": f"{step.settle_s:.6g}",
                    "repeats": step.repeats,
                    "reverse_policy": step.reverse_policy,
                    "tags": ",".join(step.tags),
                    "reciprocal_step_of": step.reciprocal_step_of or "",
                    "warnings": " | ".join(step.warnings),
                }
            )
        return rows

    def format_preview(self) -> str:
        headers = [
            "idx",
            "name",
            "state",
            "enabled",
            "relays",
            "source_mode",
            "source_quantity",
            "source_value",
            "resolved_bias",
            "dc_current_a",
            "current_rms_a",
            "frequency_hz",
            "harmonic",
            "source",
            "measure_mode",
            "readout",
            "measure_channels",
            "outputs",
            "settle_s",
            "repeats",
            "reverse_policy",
            "tags",
            "reciprocal_step_of",
            "warnings",
        ]
        rows = self.build_preview_rows()
        widths = {
            header: max(len(header), *(len(str(row.get(header, ""))) for row in rows))
            for header in headers
        }
        lines = [
            "Dry-run only. Actual relay switching still uses Matrix7709.apply_state().",
            "",
            " ".join(header.ljust(widths[header]) for header in headers),
            " ".join("-" * widths[header] for header in headers),
        ]
        for row in rows:
            lines.append(" ".join(str(row.get(header, "")).ljust(widths[header]) for header in headers))
        return "\n".join(lines)

    def _resolve_dc_excitation(self, step: ResolvedSequenceStep, *, source_value: float) -> DCExcitation:
        return DCExcitation(
            source=step.source_channel,
            current_a=abs(source_value),
            bias_polarity=1 if source_value >= 0 else -1,
            settle_s=step.settle_s,
        )

    def _resolve_ac_excitation(self, step: ResolvedSequenceStep) -> ACExcitation:
        return ACExcitation(
            source=step.source_channel,
            current_rms_a=step.current_rms_a or 0.0,
            frequency_hz=step.frequency_hz or 0.0,
            harmonic=step.harmonic or 1,
            settle_s=step.settle_s,
            lockin=step.lockin,
        )

    def _configure_excitation(self, step: ResolvedSequenceStep, *, source_value: float | None = None) -> DCExcitation | ACExcitation:
        if self.m81 is None:
            raise RuntimeError("SequenceRunner requires m81 in non-dry-run mode")
        if step.source_quantity != "current":
            raise RuntimeError(
                f"Sequence step '{step.name}' requires source_quantity={step.source_quantity}; current sourcing is the only implemented hardware mode"
            )
        if step.source_mode == "dc":
            excitation = self._resolve_dc_excitation(step, source_value=source_value if source_value is not None else step.source_value)
            configure_m81_source_for_dc(self.m81, excitation, list(step.measure_channels))
            return excitation
        excitation = self._resolve_ac_excitation(step)
        configure_m81_source_for_ac(self.m81, excitation, list(step.measure_channels))
        return excitation

    def _read_channels(self, step: ResolvedSequenceStep) -> dict[str, dict[str, Any]]:
        if self.m81 is None:
            return {}
        if step.measure_mode == "dc" or (step.source_mode == "dc" and step.measure_mode != "lockin"):
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
        relay_channels: list[int] | tuple[int, ...],
        readings: dict[str, dict[str, Any]],
        temperature_k: float | None,
        field_t: float | None,
        bias_value: float,
        bias_polarity: int,
        timestamps: dict[str, Any],
        environment_after: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.record_builder.build(
            sample_id=self.sample_id,
            geometry=self.contact_map.name,
            step_index=step.index,
            step_name=step.name,
            state_name=step.state,
            relay_channels=relay_channels,
            excitation_mode=step.source_mode,
            source_channel=step.source_channel,
            measure_channels=list(step.measure_channels),
            readings=readings,
            outputs=step.outputs,
            tags=list(step.tags),
            reciprocal_step_of=step.reciprocal_step_of,
            timestamp=timestamps["measure_end"],
            temperature_k=temperature_k,
            field_t=field_t,
            dc_current_a=bias_value if step.source_mode == "dc" and step.source_quantity == "current" else None,
            bias_polarity=bias_polarity if step.source_mode == "dc" else None,
            ac_current_rms_a=step.current_rms_a,
            frequency_hz=step.frequency_hz,
            harmonic=step.harmonic,
            metadata={
                "sequence_name": self.sequence.name,
                "sequence_description": self.sequence.description,
                "step_label": step.label,
                "step_notes": step.notes,
                **dict(step.metadata),
            },
            extra={
                "sequence_id": self.sequence.name,
                "sequence_cycle": 0,
                "step_index": step.index,
                "step_name": step.name,
                "state_name": step.state,
                "relay_channels": list(relay_channels),
                "source_mode": step.source_mode,
                "source_quantity": step.source_quantity,
                "source_value": bias_value,
                "source_polarity": bias_polarity,
                "measure_mode": step.measure_mode,
                "readout": step.readout,
                "timestamp_matrix_applied": timestamps["matrix_applied"],
                "timestamp_measure_start": timestamps["measure_start"],
                "timestamp_measure_end": timestamps["measure_end"],
                "temperature_before": temperature_k,
                "temperature_after": None if environment_after is None else environment_after.get("temperature_k"),
                "field_before": field_t,
                "field_after": None if environment_after is None else environment_after.get("field_t"),
                "environment_status": None if environment_after is None else environment_after.get("status"),
                "sequence_name": self.sequence.name,
                "sequence_description": self.sequence.description,
                **(extra or {}),
            },
        )

    def run(self, *, temperature_k: float | None = None, field_t: float | None = None) -> pd.DataFrame:
        if self.dry_run:
            return pd.DataFrame(self.build_preview_rows())
        if self.m81 is None or self.matrix is None:
            raise RuntimeError("SequenceRunner requires m81 and matrix in non-dry-run mode")
        rows: list[dict[str, Any]] = []
        for step in self.resolved_steps:
            self.m81.disable_all_sources()
            for bias_point in resolve_bias_points(step):
                relay_channels = self.matrix.apply_state(bias_point.state)
                timestamps = {"matrix_applied": _utcnow()}
                if step.settle_s > 0:
                    time.sleep(step.settle_s)
                self._configure_excitation(step, source_value=bias_point.source_value)
                self.m81.enable_source(step.source_channel)
                try:
                    for repeat_index in range(step.repeats):
                        timestamps["measure_start"] = _utcnow()
                        readings = self._read_channels(step)
                        timestamps["measure_end"] = _utcnow()
                        rows.append(
                            self._build_record(
                                step=step,
                                repeat_index=repeat_index,
                                relay_channels=relay_channels,
                                readings=readings,
                                temperature_k=temperature_k,
                                field_t=field_t,
                                bias_value=bias_point.source_value,
                                bias_polarity=bias_point.source_polarity,
                                timestamps=timestamps,
                                extra={"diagnostic_bias_state": bias_point.diagnostic},
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
            if step.source_mode != "ac":
                raise RuntimeError(f"Streaming sequence mode currently supports only AC steps, got '{step.source_mode}' for step '{step.name}'")
            if step.primary_measure_channel is None:
                raise RuntimeError(f"Streaming sequence step '{step.name}' requires at least one measurement channel")
            self.m81.disable_all_sources()
            relay_channels = self.matrix.apply_state(step.state)
            timestamp_matrix_applied = _utcnow()
            if step.settle_s > 0:
                time.sleep(step.settle_s)
            self._configure_excitation(step)
            initial_temperature = environment.read_temperature() if hasattr(environment, "read_temperature") else temperature_k
            initial_field = environment.read_field() if hasattr(environment, "read_field") else field_t
            if hasattr(self.m81, "temperature_k") and initial_temperature is not None:
                self.m81.temperature_k = initial_temperature
            if hasattr(self.m81, "field_t") and initial_field is not None:
                self.m81.field_t = initial_field
            self.m81.configure_trace_stream(channel=step.primary_measure_channel, points=stream_samples, interval_s=stream_interval)
            self.m81.enable_source(step.source_channel)
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
                    timestamp_measure_start = _utcnow()
                    environment_after = {
                        "temperature_k": current_temperature,
                        "field_t": current_field,
                        "status": getattr(environment, "mode", None),
                    }
                    rows.append(
                        self._build_record(
                            step=step,
                            repeat_index=int(trace_row.get("trace_index", 0)),
                            relay_channels=relay_channels,
                            readings=readings,
                            temperature_k=current_temperature,
                            field_t=current_field,
                            bias_value=step.source_value,
                            bias_polarity=1,
                            timestamps={
                                "matrix_applied": timestamp_matrix_applied,
                                "measure_start": timestamp_measure_start,
                                "measure_end": _utcnow(),
                            },
                            environment_after=environment_after,
                            extra={
                                "protocol": "sequence_stream",
                                "sequence_mode": "single_state" if len(self.resolved_steps) == 1 else "stepped_stream",
                                "trace_channel": trace_row.get("trace_channel", step.primary_measure_channel),
                                "trace_index": trace_row.get("trace_index"),
                                "temperature_k_initial": initial_temperature,
                                "field_t_initial": initial_field,
                                "temperature_k_final": current_temperature,
                                "field_t_final": current_field,
                            },
                        )
                    )
                    time.sleep(min(stream_interval, 0.01))
            finally:
                self.m81.abort_sweep()
                self.m81.disable_all_sources()
        return pd.DataFrame(rows)
