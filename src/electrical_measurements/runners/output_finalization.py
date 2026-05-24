from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any

import numpy as np
import pandas as pd

from ..analysis.antisymmetrize import antisymmetrize_in_field
from ..analysis.hall import compute_hall_density, compute_mobility, fit_hall_slope, infer_carrier_sign
from ..analysis.reciprocity import match_reciprocal_field
from ..analysis.symmetrize import symmetrize_in_field
from ..io.dataset import save_dataset
from ..io.metadata import build_metadata, write_metadata
from ..records import MeasurementRecordBuilder, OutputSpec
from ..switching.contact_map import ContactMap

LOGGER = logging.getLogger(__name__)
STREAM_RECORD_BUILDER = MeasurementRecordBuilder(context_name="legacy_stream")


@dataclass
class RunSummary:
    protocol: str
    sample_id: str
    output_dir: str
    row_count: int
    started_at: str
    finished_at: str
    last_temperature_k: float | None
    last_field_t: float | None


def build_stream_record(
    protocol: Any,
    contact_map: ContactMap,
    source_channel: str,
    measure_channel: str,
    harmonic: int | None,
    trace_row: dict[str, Any],
    stream_mode: str,
) -> dict[str, Any]:
    current_rms = getattr(protocol, "current_rms_a", None)
    frequency = trace_row.get("frequency_hz") or getattr(protocol, "frequency_hz", None)
    harmonic_value = trace_row.get("harmonic") or harmonic
    protocol_name = protocol.__class__.__name__.replace("Protocol", "").lower()
    outputs: dict[str, OutputSpec] = {}
    if protocol_name == "magnetoresistance":
        outputs[measure_channel] = OutputSpec(name="rxx_ohm", transform="lockin_x_over_current")
    elif protocol_name in {"hall", "hallbar"}:
        outputs[measure_channel] = OutputSpec(name="rxy_ohm", transform="lockin_x_over_current")
    return STREAM_RECORD_BUILDER.build(
        sample_id=protocol.sample_id,
        geometry=contact_map.name,
        step_index=None,
        step_name=None,
        state_name=getattr(protocol, "state", None),
        relay_channels=trace_row.get("matrix_relay_channels") or [],
        excitation_mode="ac",
        source_channel=source_channel,
        measure_channels=[measure_channel],
        readings={measure_channel: dict(trace_row)},
        outputs=outputs,
        tags=[],
        reciprocal_step_of=None,
        timestamp=trace_row["timestamp"],
        temperature_k=trace_row.get("temperature_k"),
        field_t=trace_row.get("field_t"),
        ac_current_rms_a=current_rms,
        frequency_hz=frequency,
        harmonic=harmonic_value,
        metadata={
            "protocol_name": protocol_name,
            "reciprocal_state": contact_map.states.get(getattr(protocol, "state", ""), {}).get("reciprocal"),
            "environment_timestamp": trace_row.get("environment_timestamp"),
            "stream_mode": stream_mode,
        },
        extra={
            "protocol": protocol_name,
            "trace_channel": trace_row.get("trace_channel", measure_channel),
            "trace_index": trace_row.get("trace_index"),
            "environment_timestamp": trace_row.get("environment_timestamp"),
            "stream_mode": stream_mode,
            "reciprocal_state": contact_map.states.get(getattr(protocol, "state", ""), {}).get("reciprocal"),
        },
    )


def extract_last_environment_values(df: pd.DataFrame) -> tuple[float | None, float | None]:
    if df.empty:
        return None, None
    last_temperature = None
    last_field = None
    if "temperature_k" in df.columns:
        series = df["temperature_k"].dropna()
        if not series.empty:
            last_temperature = float(series.iloc[-1])
    if "field_t" in df.columns:
        series = df["field_t"].dropna()
        if not series.empty:
            last_field = float(series.iloc[-1])
    return last_temperature, last_field


def finalize_run_outputs(
    *,
    args: argparse.Namespace,
    config: dict[str, Any],
    contact_map: ContactMap,
    df: pd.DataFrame,
    metadata_notes: str | None = None,
    stem: str | None = None,
    started_at: str,
) -> RunSummary:
    output_dir = args.output or config.get("output", {}).get("directory", "data")
    run_name = stem or args.protocol or "sequence"
    save_dataset(df, output_dir=output_dir, stem=run_name)
    write_metadata(build_metadata(config, contact_map, notes=metadata_notes), output_dir=output_dir, stem=run_name)
    finished_at = datetime.now(timezone.utc).isoformat()
    last_temperature, last_field = extract_last_environment_values(df)
    LOGGER.info("Completed run protocol=%s output_dir=%s rows=%s", run_name, output_dir, len(df))
    return RunSummary(
        protocol=run_name,
        sample_id=getattr(args, "sample_id", None) or config.get("sample_id", "sample"),
        output_dir=str(output_dir),
        row_count=len(df),
        started_at=started_at,
        finished_at=finished_at,
        last_temperature_k=last_temperature,
        last_field_t=last_field,
    )


def postprocess_dataframe(df: pd.DataFrame, protocol_name: str) -> pd.DataFrame:
    if df.empty:
        return df
    if protocol_name in {"hallbar_mr"} and "rxx_ohm" in df.columns and "field_t" in df.columns:
        for col in ["rxx_ohm_even", "rxx_ohm_odd"]:
            if col in df.columns:
                df = df.drop(columns=[col])
        even = symmetrize_in_field(df, "rxx_ohm")
        odd = antisymmetrize_in_field(df, "rxx_ohm")
        if not even.empty:
            df = df.merge(even, on="field_t", how="left")
        if not odd.empty:
            df = df.merge(odd, on="field_t", how="left")
    if protocol_name == "hall" and "rxy_ohm" in df.columns and "field_t" in df.columns:
        for col in ["rxy_ohm_odd", "rxy_ohm_even", "slope_Rxy_vs_B", "hall_density_m2", "hall_density_cm2", "mobility_m2_Vs", "mobility_cm2_Vs", "carrier_sign"]:
            if col in df.columns:
                df = df.drop(columns=[col])
        odd = antisymmetrize_in_field(df, "rxy_ohm")
        even = symmetrize_in_field(df, "rxy_ohm")
        if not odd.empty:
            df = df.merge(odd, on="field_t", how="left")
        if not even.empty:
            df = df.merge(even, on="field_t", how="left")
        valid = df[["field_t", "rxy_ohm"]].dropna()
        if len(valid) >= 2 and not np.allclose(valid["field_t"].to_numpy(), 0.0):
            slope = fit_hall_slope(valid["field_t"].to_numpy(dtype=float), valid["rxy_ohm"].to_numpy(dtype=float))
            hall_density_m2 = compute_hall_density(slope)
            df["slope_Rxy_vs_B"] = slope
            df["hall_density_m2"] = hall_density_m2
            df["hall_density_cm2"] = hall_density_m2 / 1e4
            if "rxx_ohm" in df.columns:
                mobility = compute_mobility(hall_density_m2, float(df["rxx_ohm"].dropna().iloc[0]))
                df["mobility_m2_Vs"] = mobility
                df["mobility_cm2_Vs"] = mobility * 1e4
            df["carrier_sign"] = infer_carrier_sign(slope)
    if protocol_name == "reciprocity":
        rows: list[dict[str, Any]] = []
        for record in df.to_dict(orient="records"):
            if record.get("state") and record.get("field_t") is not None:
                rows.append(record)
        if rows:
            pair_df = match_reciprocal_field(pd.DataFrame(rows))
            if not pair_df.empty:
                df["reciprocity_pair_count"] = len(pair_df)
    return df
