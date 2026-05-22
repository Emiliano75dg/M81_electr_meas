from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from pathlib import Path
import time
from typing import Any

import numpy as np
import pandas as pd
import yaml

from ..capabilities import instrument_capabilities_from_config, instrument_capabilities_from_controller
from ..exceptions import RunnerInputError
from ..excitation import validate_legacy_ac_cli_args
from ..analysis.antisymmetrize import antisymmetrize_in_field
from ..analysis.hall import compute_hall_density, compute_mobility, fit_hall_slope, infer_carrier_sign
from ..analysis.reciprocity import match_reciprocal_field
from ..analysis.symmetrize import symmetrize_in_field
from ..instruments.m81 import M81Controller
from ..instruments.mock import MockEnvironmentController, MockM81Controller
from ..instruments.teslatron_client import (
    ReadOnlyEnvironmentController,
    StandaloneEnvironmentController,
    TeslatronClient,
    environment_allow_control_from_config,
    environment_mode_from_config,
    environment_supports_control,
)
from ..io.dataset import measurement_points_to_dataframe, save_dataset
from ..io.logging import configure_logging
from ..io.metadata import build_metadata, write_metadata
from ..io.notifications import build_notifier
from ..records import MeasurementRecordBuilder, OutputSpec
from ..protocols.contact_check import ContactCheckProtocol
from ..protocols.hall import HallProtocol
from ..protocols.magnetoresistance import MagnetoresistanceProtocol
from ..protocols.reciprocity import ReciprocityProtocol
from ..protocols.second_harmonic import SecondHarmonicProtocol
from ..protocols.vdp_hall import VanDerPauwHallProtocol
from ..protocols.vanderpauw import VanDerPauwProtocol
from ..sequences import SequenceRunner, load_measurement_sequence, validate_measurement_sequence
from ..switching.contact_map import ContactMap
from ..switching.matrix7709 import Matrix7709, SafeMeasurementSession

LOGGER = logging.getLogger(__name__)
STREAM_RECORD_BUILDER = MeasurementRecordBuilder(context_name="legacy_stream")
PROTOCOL_REGISTRY = {
    "hall": HallProtocol,
    "hallbar_mr": MagnetoresistanceProtocol,
    "vdp": VanDerPauwProtocol,
    "vdp_hall": VanDerPauwHallProtocol,
    "second_harmonic": SecondHarmonicProtocol,
    "reciprocity": ReciprocityProtocol,
    "check_contacts": ContactCheckProtocol,
}


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


def load_yaml(path: str | Path) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text())


def apply_environment_mode_override(config: dict[str, Any], environment_mode: str | None) -> dict[str, Any]:
    if not environment_mode:
        return config
    updated = dict(config)
    instruments = dict(updated.get("instruments", {}))
    environment = dict(instruments.get("environment", {}))
    environment["mode"] = environment_mode
    instruments["environment"] = environment
    updated["instruments"] = instruments
    return updated


def build_instruments(config: dict[str, Any], contact_map: ContactMap, mock: bool = False):
    if mock or config.get("instruments", {}).get("m81", {}).get("connection", {}).get("kind") == "mock":
        m81 = MockM81Controller.from_config(config)
    else:
        m81 = M81Controller.from_config(config)
    matrix = Matrix7709.from_config(config, contact_map=contact_map, m81=m81)
    env_cfg = config.get("instruments", {}).get("environment", {})
    environment_mode = environment_mode_from_config(config)
    allow_control = environment_allow_control_from_config(config)
    if environment_mode == "standalone":
        environment = StandaloneEnvironmentController(
            temperature_k=float(env_cfg.get("initial_temperature_k", 300.0)),
            field_t=float(env_cfg.get("initial_field_t", 0.0)),
        )
    else:
        if mock or env_cfg.get("kind", "mock") == "mock":
            backend = MockEnvironmentController()
        else:
            backend = TeslatronClient.from_config(config)
        if environment_mode == "async-poll" or not allow_control:
            environment = ReadOnlyEnvironmentController(backend=backend, mode=environment_mode)
        else:
            environment = backend
    return m81, matrix, environment


def resolve_measurement_environment(
    environment: Any,
    requested_temperature: float | None,
    requested_field: float | None,
) -> tuple[float | None, float | None]:
    current_temperature = environment.read_temperature() if hasattr(environment, "read_temperature") else None
    current_field = environment.read_field() if hasattr(environment, "read_field") else None
    return (
        current_temperature if current_temperature is not None else requested_temperature,
        current_field if current_field is not None else requested_field,
    )


def position_environment(environment: Any, temperature: float, field: float) -> tuple[float | None, float | None]:
    if environment_supports_control(environment):
        environment.set_temperature(temperature)
        environment.wait_temperature_stable(temperature)
        environment.set_field(field)
        environment.wait_field_stable(field)
    return resolve_measurement_environment(environment, requested_temperature=temperature, requested_field=field)


def _common_protocol_kwargs(
    args: argparse.Namespace,
    config: dict[str, Any],
    contact_map: ContactMap,
    m81: Any,
    matrix: Matrix7709,
) -> tuple[dict[str, Any], list[str], str | None]:
    selected_states = [item.strip() for item in (getattr(args, "selected_states", "") or "").split(",") if item.strip()]
    selected_state_name = getattr(args, "state_name", None)
    common = {
        "m81": m81,
        "matrix": matrix,
        "contact_map": contact_map,
        "sample_id": args.sample_id or config.get("sample_id", "sample"),
        "settle_s": args.settle,
        "dry_run": args.dry_run,
    }
    return common, selected_states, selected_state_name


def build_protocol(args: argparse.Namespace, config: dict[str, Any], contact_map: ContactMap, m81: Any, matrix: Matrix7709):
    common, selected_states, selected_state_name = _common_protocol_kwargs(args, config, contact_map, m81, matrix)
    if args.protocol == "hall":
        return HallProtocol(state=selected_state_name or "hallbar_forward", current_rms_a=args.current, frequency_hz=args.frequency, harmonic=args.harmonic, **common)
    if args.protocol == "hallbar_mr":
        return MagnetoresistanceProtocol(state=selected_state_name or "hallbar_forward", current_rms_a=args.current, frequency_hz=args.frequency, harmonic=args.harmonic, **common)
    if args.protocol == "vdp":
        return VanDerPauwProtocol(
            states=selected_states or None,
            current_rms_a=args.current,
            frequency_hz=args.frequency,
            harmonic=args.harmonic,
            include_anisotropy=bool(getattr(args, "include_anisotropy", False)),
            **common,
        )
    if args.protocol == "vdp_hall":
        return VanDerPauwHallProtocol(
            states=selected_states or None,
            current_rms_a=args.current,
            frequency_hz=args.frequency,
            harmonic=args.harmonic,
            include_reciprocity=bool(getattr(args, "include_reciprocity", False)),
            **common,
        )
    if args.protocol == "second_harmonic":
        return SecondHarmonicProtocol(state=selected_state_name or "second_harmonic_vxy", current_rms_a=args.current, frequency_hz=args.frequency, harmonic=args.harmonic, **common)
    if args.protocol == "reciprocity":
        return ReciprocityProtocol(states=selected_states or list(contact_map.states.keys())[:2], current_rms_a=args.current, frequency_hz=args.frequency, harmonic=args.harmonic, **common)
    if args.protocol == "check_contacts":
        return ContactCheckProtocol(states=selected_states or list(contact_map.states.keys()), **common)
    raise RunnerInputError(f"Unsupported protocol: {args.protocol}")


def parse_float_list(value: str | None) -> list[float]:
    if not value:
        return [0.0]
    return [float(item) for item in value.split(",")]


def validate_run_inputs(args: argparse.Namespace, contact_map: ContactMap) -> None:
    if getattr(args, "sequence", None):
        if getattr(args, "protocol", None):
            raise RunnerInputError("--sequence cannot be combined with --protocol")
        if getattr(args, "state_name", None) or getattr(args, "selected_states", None):
            raise RunnerInputError("--sequence cannot be combined with --state-name or --selected-states")
        if getattr(args, "mode", "stable") not in {"stable", "stream-observe", "stream-ramp"}:
            raise RunnerInputError("--sequence supports only --mode stable, --mode stream-observe, or --mode stream-ramp")
        if getattr(args, "mode", "stable") == "stream-ramp":
            if getattr(args, "ramp_rate", None) is None or float(args.ramp_rate) <= 0:
                raise RunnerInputError("--ramp-rate must be > 0 in stream-ramp mode")
            if getattr(args, "ramp_target", None) is None:
                raise RunnerInputError("--ramp-target is required in stream-ramp mode")
        if getattr(args, "stream_interval", 0.0) <= 0:
            raise RunnerInputError("--stream-interval must be > 0")
        if getattr(args, "stream_samples", 0) <= 0:
            raise RunnerInputError("--stream-samples must be > 0")
        return
    if not getattr(args, "protocol", None):
        raise RunnerInputError("--protocol is required unless --sequence is provided")
    if getattr(args, "settle", 0.0) < 0:
        raise RunnerInputError("--settle must be >= 0")
    if getattr(args, "stream_interval", 0.0) <= 0:
        raise RunnerInputError("--stream-interval must be > 0")
    if getattr(args, "stream_samples", 0) <= 0:
        raise RunnerInputError("--stream-samples must be > 0")
    if getattr(args, "mode", "stable") == "stream-ramp":
        if getattr(args, "ramp_rate", None) is None or float(args.ramp_rate) <= 0:
            raise RunnerInputError("--ramp-rate must be > 0 in stream-ramp mode")
    if getattr(args, "protocol", "") in {"hall", "hallbar_mr", "second_harmonic", "vdp", "vdp_hall", "reciprocity"}:
        validate_legacy_ac_cli_args(
            current=float(getattr(args, "current", 0.0)),
            frequency=float(getattr(args, "frequency", 0.0)),
            harmonic=int(getattr(args, "harmonic", 1)),
        )
    state_name = getattr(args, "state_name", None)
    if state_name and state_name not in contact_map.states:
        raise RunnerInputError(f"Unknown --state-name: {state_name}")
    selected_states = [item.strip() for item in (getattr(args, "selected_states", "") or "").split(",") if item.strip()]
    unknown_states = [state for state in selected_states if state not in contact_map.states]
    if unknown_states:
        raise RunnerInputError(f"Unknown selected states: {unknown_states}")


def build_run_namespace(
    *,
    config: str = "configs/instruments.yaml",
    contact_map: str = "configs/contact_maps/hallbar_6contacts_7709.yaml",
    mock: bool = False,
    sample_id: str | None = None,
    output: str | None = None,
    protocol: str | None = None,
    temperatures: str | None = None,
    fields: str | None = None,
    current: float = 10e-6,
    frequency: float = 13.7,
    harmonic: int = 1,
    settle: float = 0.1,
    mode: str = "stable",
    ramp_quantity: str = "field",
    ramp_target: float | None = None,
    ramp_rate: float | None = None,
    stream_samples: int = 20,
    stream_interval: float = 0.1,
    dry_run: bool = False,
    state_name: str | None = None,
    selected_states: str | None = None,
    environment_mode: str | None = None,
    include_reciprocity: bool = False,
    include_anisotropy: bool = False,
    sequence: str | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        command="run",
        config=config,
        contact_map=contact_map,
        mock=mock,
        sample_id=sample_id,
        output=output,
        protocol=protocol,
        temperatures=temperatures,
        fields=fields,
        current=current,
        frequency=frequency,
        harmonic=harmonic,
        settle=settle,
        mode=mode,
        ramp_quantity=ramp_quantity,
        ramp_target=ramp_target,
        ramp_rate=ramp_rate,
        stream_samples=stream_samples,
        stream_interval=stream_interval,
        dry_run=dry_run,
        state_name=state_name,
        selected_states=selected_states,
        environment_mode=environment_mode,
        include_reciprocity=include_reciprocity,
        include_anisotropy=include_anisotropy,
        sequence=sequence,
    )


def parse_iso_timestamp(value: str) -> pd.Timestamp:
    return pd.to_datetime(value, utc=True)


def synchronize_trace_and_environment(
    trace_records: list[dict[str, Any]],
    environment_records: list[dict[str, Any]],
    tolerance_s: float = 0.5,
) -> pd.DataFrame:
    trace_df = pd.DataFrame(trace_records)
    env_df = pd.DataFrame(environment_records)
    if trace_df.empty or env_df.empty:
        return trace_df
    trace_df["timestamp"] = trace_df["timestamp"].map(parse_iso_timestamp)
    env_df["environment_timestamp"] = env_df["timestamp"].map(parse_iso_timestamp)
    env_df = env_df.drop(columns=["timestamp"])
    merged = pd.merge_asof(
        trace_df.sort_values("timestamp"),
        env_df.sort_values("environment_timestamp"),
        left_on="timestamp",
        right_on="environment_timestamp",
        direction="nearest",
        tolerance=pd.Timedelta(seconds=tolerance_s),
    )
    merged["timestamp"] = merged["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%S.%f%z")
    merged["environment_timestamp"] = merged["environment_timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%S.%f%z")
    return merged


def extract_stream_settings(protocol: Any) -> tuple[str, str, str, int | None]:
    state = getattr(protocol, "state", None)
    measure_channel = getattr(protocol, "measure_channel", "M1")
    source_channel = getattr(protocol, "source", "S1")
    harmonic = getattr(protocol, "harmonic", None)
    if state is None:
        raise RunnerInputError("Streaming mode currently requires a single-state protocol")
    return state, measure_channel, source_channel, harmonic


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
    record = STREAM_RECORD_BUILDER.build(
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
    return record


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
            density = compute_hall_density(slope)
            mobility = compute_mobility(
                float(df["sheet_resistance_ohm_sq"].dropna().iloc[0]) if df["sheet_resistance_ohm_sq"].notna().any() else None,
                density["carrier_density_2d_m2"],
            )
            df["slope_Rxy_vs_B"] = slope
            df["hall_density_m2"] = density["carrier_density_2d_m2"]
            df["hall_density_cm2"] = density["carrier_density_2d_cm2"]
            df["mobility_m2_Vs"] = mobility["mobility_m2_Vs"]
            df["mobility_cm2_Vs"] = mobility["mobility_cm2_Vs"]
            df["carrier_sign"] = infer_carrier_sign(slope)
    if protocol_name == "reciprocity":
        rows: list[dict[str, Any]] = []
        for record in df.get("rows", []):
            if isinstance(record, list):
                rows.extend(record)
        if rows:
            pair_df = match_reciprocal_field(pd.DataFrame(rows))
            if not pair_df.empty:
                df["reciprocity_pair_count"] = len(pair_df)
    return df


def run_stream_ramp_command(
    args: argparse.Namespace,
    config: dict[str, Any],
    contact_map: ContactMap,
    m81: Any,
    matrix: Matrix7709,
    environment: Any,
    protocol: Any,
) -> RunSummary:
    if args.ramp_target is None or args.ramp_rate is None:
        raise RunnerInputError("Streaming ramp mode requires --ramp-target and --ramp-rate")
    if not environment_supports_control(environment):
        raise RunnerInputError("Streaming ramp mode requires an environment with control enabled; async-poll mode is read-only")
    state_name, measure_channel, source_channel, harmonic = extract_stream_settings(protocol)
    started_at = datetime.now(timezone.utc).isoformat()
    LOGGER.info("Starting stream-ramp run protocol=%s state=%s quantity=%s target=%s rate=%s", args.protocol, getattr(protocol, "state", None), args.ramp_quantity, args.ramp_target, args.ramp_rate)
    protocol.setup()
    trace_records: list[dict[str, Any]] = []
    environment_records: list[dict[str, Any]] = []
    with SafeMeasurementSession(m81, matrix):
        temperatures = parse_float_list(args.temperatures)
        fields = parse_float_list(args.fields)
        base_temperature = temperatures[0]
        base_field = fields[0]
        current_temperature, current_field = position_environment(environment, base_temperature, base_field)
        if hasattr(m81, "temperature_k"):
            m81.temperature_k = current_temperature if current_temperature is not None else base_temperature
        if hasattr(m81, "field_t"):
            m81.field_t = current_field if current_field is not None else base_field
        protocol._set_measurement_context(
            state_name,
            current_sign=1.0,
            measure_kind="transverse" if measure_channel == "M2" else "longitudinal",
        )
        channels = matrix.apply_state(state_name)
        m81.configure_trace_stream(channel=measure_channel, points=args.stream_samples, interval_s=args.stream_interval)
        m81.enable_source(source_channel)
        if args.ramp_quantity == "field":
            environment.start_field_ramp(args.ramp_target, args.ramp_rate)
        else:
            environment.start_temperature_ramp(args.ramp_target, args.ramp_rate)
        m81.start_trace()
        for _ in range(args.stream_samples):
            if hasattr(environment, "advance_time"):
                environment.advance_time(args.stream_interval)
            current_temperature = environment.read_temperature()
            current_field = environment.read_field()
            if hasattr(m81, "temperature_k") and current_temperature is not None:
                m81.temperature_k = current_temperature
            if hasattr(m81, "field_t") and current_field is not None:
                m81.field_t = current_field
            environment_records.append(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "temperature_k": current_temperature,
                    "field_t": current_field,
                    "state": state_name,
                    "matrix_relay_channels": channels,
                }
            )
            batch = m81.fetch_trace(max_points=1)
            if batch:
                trace_records.extend(batch)
            time.sleep(min(args.stream_interval, 0.01))
        m81.abort_sweep()
        if args.ramp_quantity == "field":
            environment.stop_field_ramp()
        else:
            environment.stop_temperature_ramp()
        m81.disable_all_sources()
    protocol.teardown()
    synced = synchronize_trace_and_environment(
        trace_records,
        environment_records,
        tolerance_s=max(args.stream_interval * 2.0, 0.5),
    )
    if synced.empty:
        synced = pd.DataFrame(trace_records)
    records = [
        build_stream_record(protocol, contact_map, source_channel, measure_channel, harmonic, row, "ramp")
        for row in synced.to_dict(orient="records")
    ]
    df = pd.DataFrame(records)
    return finalize_run_outputs(
        args=args,
        config=config,
        contact_map=contact_map,
        df=df,
        metadata_notes=f"stream ramp {args.ramp_quantity}",
        stem=f"{args.protocol}_stream",
        started_at=started_at,
    )


def run_stream_observe_command(
    args: argparse.Namespace,
    config: dict[str, Any],
    contact_map: ContactMap,
    m81: Any,
    matrix: Matrix7709,
    environment: Any,
    protocol: Any,
) -> RunSummary:
    started_at = datetime.now(timezone.utc).isoformat()
    state_name, measure_channel, source_channel, harmonic = extract_stream_settings(protocol)
    LOGGER.info("Starting stream-observe run protocol=%s state=%s", args.protocol, getattr(protocol, "state", None))
    protocol.setup()
    trace_records: list[dict[str, Any]] = []
    environment_records: list[dict[str, Any]] = []
    with SafeMeasurementSession(m81, matrix):
        current_temperature, current_field = resolve_measurement_environment(
            environment,
            requested_temperature=parse_float_list(args.temperatures)[0],
            requested_field=parse_float_list(args.fields)[0],
        )
        if hasattr(m81, "temperature_k"):
            m81.temperature_k = current_temperature
        if hasattr(m81, "field_t"):
            m81.field_t = current_field
        protocol._set_measurement_context(
            state_name,
            current_sign=1.0,
            measure_kind="transverse" if measure_channel == "M2" else "longitudinal",
        )
        channels = matrix.apply_state(state_name)
        m81.configure_trace_stream(channel=measure_channel, points=args.stream_samples, interval_s=args.stream_interval)
        m81.enable_source(source_channel)
        m81.start_trace()
        for _ in range(args.stream_samples):
            if hasattr(environment, "advance_time"):
                environment.advance_time(args.stream_interval)
            current_temperature = environment.read_temperature()
            current_field = environment.read_field()
            if hasattr(m81, "temperature_k") and current_temperature is not None:
                m81.temperature_k = current_temperature
            if hasattr(m81, "field_t") and current_field is not None:
                m81.field_t = current_field
            environment_records.append(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "temperature_k": current_temperature,
                    "field_t": current_field,
                    "state": state_name,
                    "matrix_relay_channels": channels,
                }
            )
            batch = m81.fetch_trace(max_points=1)
            if batch:
                trace_records.extend(batch)
            time.sleep(min(args.stream_interval, 0.01))
        m81.abort_sweep()
        m81.disable_all_sources()
    protocol.teardown()
    synced = synchronize_trace_and_environment(
        trace_records,
        environment_records,
        tolerance_s=max(args.stream_interval * 2.0, 0.5),
    )
    if synced.empty:
        synced = pd.DataFrame(trace_records)
    records = [
        build_stream_record(protocol, contact_map, source_channel, measure_channel, harmonic, row, "observe")
        for row in synced.to_dict(orient="records")
    ]
    df = pd.DataFrame(records)
    return finalize_run_outputs(
        args=args,
        config=config,
        contact_map=contact_map,
        df=df,
        metadata_notes="stream observe",
        stem=f"{args.protocol}_stream",
        started_at=started_at,
    )


def run_sequence_stream_command(
    args: argparse.Namespace,
    config: dict[str, Any],
    contact_map: ContactMap,
    environment: Any,
    runner: SequenceRunner,
) -> RunSummary:
    started_at = datetime.now(timezone.utc).isoformat()
    LOGGER.info("Starting sequence stream run sequence=%s mode=%s", runner.sequence.name, args.mode)
    if args.mode == "stream-ramp":
        if not environment_supports_control(environment):
            raise RunnerInputError("Streaming ramp mode requires an environment with control enabled; async-poll mode is read-only")
        base_temperature = parse_float_list(args.temperatures)[0]
        base_field = parse_float_list(args.fields)[0]
        current_temperature, current_field = position_environment(environment, base_temperature, base_field)
        if hasattr(runner.m81, "temperature_k"):
            runner.m81.temperature_k = current_temperature if current_temperature is not None else base_temperature
        if hasattr(runner.m81, "field_t"):
            runner.m81.field_t = current_field if current_field is not None else base_field
        if args.ramp_quantity == "field":
            environment.start_field_ramp(args.ramp_target, args.ramp_rate)
        else:
            environment.start_temperature_ramp(args.ramp_target, args.ramp_rate)
        try:
            df = runner.run_stream(
                environment=environment,
                stream_samples=args.stream_samples,
                stream_interval=args.stream_interval,
                temperature_k=current_temperature,
                field_t=current_field,
            )
        finally:
            if args.ramp_quantity == "field":
                environment.stop_field_ramp()
            else:
                environment.stop_temperature_ramp()
    else:
        current_temperature, current_field = resolve_measurement_environment(
            environment,
            requested_temperature=parse_float_list(args.temperatures)[0],
            requested_field=parse_float_list(args.fields)[0],
        )
        if hasattr(runner.m81, "temperature_k"):
            runner.m81.temperature_k = current_temperature
        if hasattr(runner.m81, "field_t"):
            runner.m81.field_t = current_field
        df = runner.run_stream(
            environment=environment,
            stream_samples=args.stream_samples,
            stream_interval=args.stream_interval,
            temperature_k=current_temperature,
            field_t=current_field,
        )
    return finalize_run_outputs(
        args=args,
        config=config,
        contact_map=contact_map,
        df=df,
        metadata_notes=f"sequence {runner.sequence.name} {args.mode}",
        stem=f"{runner.sequence.name}_stream",
        started_at=started_at,
    )


def run_sequence_command(
    args: argparse.Namespace,
    *,
    config: dict[str, Any],
    contact_map: ContactMap,
    started_at: str,
) -> RunSummary:
    capabilities = instrument_capabilities_from_config(config)
    sequence = load_measurement_sequence(args.sequence)
    resolved_steps = validate_measurement_sequence(sequence, contact_map, capabilities=capabilities)
    LOGGER.info(
        "Starting sequence run sequence=%s mode=%s environment_mode=%s contact_map=%s mock=%s",
        sequence.name,
        args.mode,
        getattr(args, "environment_mode", None),
        contact_map.name,
        args.mock,
    )
    if args.dry_run:
        preview_runner = SequenceRunner(
            sequence=sequence,
            contact_map=contact_map,
            resolved_steps=resolved_steps,
            dry_run=True,
            sample_id=args.sample_id or config.get("sample_id", "sample"),
        )
        print(preview_runner.format_preview())
        return RunSummary(
            protocol=sequence.name,
            sample_id=args.sample_id or config.get("sample_id", "sample"),
            output_dir=str(args.output or config.get("output", {}).get("directory", "data")),
            row_count=0,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            last_temperature_k=None,
            last_field_t=None,
        )
    m81, matrix, environment = build_instruments(config, contact_map, mock=args.mock)
    runtime_capabilities = instrument_capabilities_from_controller(m81)
    resolved_steps = validate_measurement_sequence(sequence, contact_map, capabilities=runtime_capabilities)
    runner = SequenceRunner(
        sequence=sequence,
        contact_map=contact_map,
        resolved_steps=resolved_steps,
        m81=m81,
        matrix=matrix,
        dry_run=False,
        sample_id=args.sample_id or config.get("sample_id", "sample"),
    )
    with SafeMeasurementSession(m81, matrix):
        if args.mode in {"stream-observe", "stream-ramp"}:
            return run_sequence_stream_command(args, config, contact_map, environment, runner)
        temperatures = parse_float_list(args.temperatures)
        fields = parse_float_list(args.fields)
        frames = []
        for temperature in temperatures:
            for field in fields:
                current_temperature, current_field = position_environment(environment, temperature, field)
                if hasattr(m81, "field_t"):
                    m81.field_t = current_field if current_field is not None else field
                if hasattr(m81, "temperature_k"):
                    m81.temperature_k = current_temperature if current_temperature is not None else temperature
                frames.append(runner.run(temperature_k=current_temperature, field_t=current_field))
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        return finalize_run_outputs(
            args=args,
            config=config,
            contact_map=contact_map,
            df=df,
            metadata_notes=f"sequence {sequence.name}",
            stem=sequence.name,
            started_at=started_at,
        )


def run_legacy_protocol_command(
    args: argparse.Namespace,
    *,
    config: dict[str, Any],
    contact_map: ContactMap,
    started_at: str,
) -> RunSummary:
    LOGGER.info(
        "Starting run protocol=%s mode=%s environment_mode=%s contact_map=%s mock=%s",
        args.protocol,
        args.mode,
        getattr(args, "environment_mode", None),
        contact_map.name,
        args.mock,
    )
    m81, matrix, environment = build_instruments(config, contact_map, mock=args.mock)
    protocol = build_protocol(args, config, contact_map, m81, matrix)
    if args.mode == "stream-ramp":
        return run_stream_ramp_command(args, config, contact_map, m81, matrix, environment, protocol)
    if args.mode == "stream-observe":
        return run_stream_observe_command(args, config, contact_map, m81, matrix, environment, protocol)
    temperatures = parse_float_list(args.temperatures)
    fields = parse_float_list(args.fields)
    points = []
    protocol.setup()
    with SafeMeasurementSession(m81, matrix):
        for temperature in temperatures:
            for field in fields:
                current_temperature, current_field = position_environment(environment, temperature, field)
                if hasattr(m81, "field_t"):
                    m81.field_t = current_field if current_field is not None else field
                if hasattr(m81, "temperature_k"):
                    m81.temperature_k = current_temperature if current_temperature is not None else temperature
                points.append(protocol.measure_point(temperature_k=current_temperature, field_t=current_field))
    protocol.teardown()
    df = measurement_points_to_dataframe(points)
    df = postprocess_dataframe(df, args.protocol)
    return finalize_run_outputs(
        args=args,
        config=config,
        contact_map=contact_map,
        df=df,
        started_at=started_at,
    )


def run_command(args: argparse.Namespace) -> int:
    config = apply_environment_mode_override(load_yaml(args.config), getattr(args, "environment_mode", None))
    configure_logging(config.get("logging", {}).get("level", "INFO"))
    notifier = build_notifier(config)
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        contact_map = ContactMap.from_yaml(args.contact_map)
        validate_run_inputs(args, contact_map)
        summary = (
            run_sequence_command(args, config=config, contact_map=contact_map, started_at=started_at)
            if getattr(args, "sequence", None)
            else run_legacy_protocol_command(args, config=config, contact_map=contact_map, started_at=started_at)
        )
    except Exception as exc:
        notifier.notify(
            "measurement_failed",
            {
                "protocol": args.protocol or getattr(args, "sequence", "sequence"),
                "sample_id": args.sample_id or config.get("sample_id", "sample"),
                "output_dir": str(args.output or config.get("output", {}).get("directory", "data")),
                "started_at": started_at,
                "failed_at": datetime.now(timezone.utc).isoformat(),
                "error": str(exc),
            },
        )
        raise
    notifier.notify(
        "measurement_completed",
        {
            "protocol": summary.protocol,
            "sample_id": summary.sample_id,
            "output_dir": summary.output_dir,
            "row_count": summary.row_count,
            "started_at": summary.started_at,
            "finished_at": summary.finished_at,
            "last_temperature_k": summary.last_temperature_k,
            "last_field_t": summary.last_field_t,
        },
    )
    return 0


def list_instruments_command(args: argparse.Namespace) -> int:
    config = apply_environment_mode_override(load_yaml(args.config), getattr(args, "environment_mode", None))
    for name, definition in config.get("instruments", {}).items():
        print(f"{name}: {definition}")
    return 0


def emergency_stop_command(args: argparse.Namespace) -> int:
    config = load_yaml(args.config)
    contact_map = ContactMap.from_yaml(args.contact_map)
    m81, matrix, _ = build_instruments(config, contact_map, mock=args.mock)
    m81.emergency_stop()
    matrix.emergency_stop()
    return 0


def analyze_command(args: argparse.Namespace) -> int:
    df = pd.read_csv(args.input)
    df = postprocess_dataframe(df, args.protocol)
    print(df.to_string(index=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="electrical-measure")
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default="configs/instruments.yaml")
    common.add_argument("--contact-map", default="configs/contact_maps/hallbar_6contacts_7709.yaml")
    common.add_argument("--mock", action="store_true")
    common.add_argument("--environment-mode", choices=["integrated", "async-poll", "standalone"])

    run_parser = subparsers.add_parser("run", parents=[common])
    run_parser.add_argument("--sample-id")
    run_parser.add_argument("--output")
    run_parser.add_argument("--protocol", choices=["hall", "hallbar_mr", "vdp", "vdp_hall", "second_harmonic", "reciprocity", "check_contacts"])
    run_parser.add_argument("--sequence")
    run_parser.add_argument("--temperatures")
    run_parser.add_argument("--fields")
    run_parser.add_argument("--current", type=float, default=10e-6)
    run_parser.add_argument("--frequency", type=float, default=13.7)
    run_parser.add_argument("--harmonic", type=int, default=1)
    run_parser.add_argument("--settle", type=float, default=0.1)
    run_parser.add_argument("--mode", choices=["stable", "stream-ramp", "stream-observe"], default="stable")
    run_parser.add_argument("--ramp-quantity", choices=["field", "temperature"], default="field")
    run_parser.add_argument("--ramp-target", type=float)
    run_parser.add_argument("--ramp-rate", type=float)
    run_parser.add_argument("--stream-samples", type=int, default=20)
    run_parser.add_argument("--stream-interval", type=float, default=0.1)
    run_parser.add_argument("--state-name")
    run_parser.add_argument("--selected-states")
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.add_argument("--include-reciprocity", action="store_true")
    run_parser.add_argument("--include-anisotropy", action="store_true")
    run_parser.set_defaults(func=run_command)

    list_parser = subparsers.add_parser("list-instruments", parents=[common])
    list_parser.set_defaults(func=list_instruments_command)

    contact_parser = subparsers.add_parser("check-contacts", parents=[common])
    contact_parser.add_argument("--sample-id")
    contact_parser.add_argument("--output")
    contact_parser.add_argument("--settle", type=float, default=0.1)
    contact_parser.add_argument("--dry-run", action="store_true")
    contact_parser.set_defaults(
        func=lambda args: run_command(
            build_run_namespace(
                config=args.config,
                contact_map=args.contact_map,
                mock=args.mock,
                sample_id=args.sample_id,
                output=args.output,
                protocol="check_contacts",
                temperatures=None,
                fields=None,
                current=0.0,
                frequency=0.0,
                harmonic=1,
                settle=args.settle,
                dry_run=args.dry_run,
            )
        )
    )

    analyze_parser = subparsers.add_parser("analyze")
    analyze_parser.add_argument("--input", required=True)
    analyze_parser.add_argument("--protocol", required=True, choices=["hall", "hallbar_mr", "vdp", "vdp_hall", "second_harmonic", "reciprocity", "check_contacts"])
    analyze_parser.set_defaults(func=analyze_command)

    gui_parser = subparsers.add_parser("gui", parents=[common])
    gui_parser.set_defaults(func=lambda args: __import__("electrical_measurements.gui.app", fromlist=["launch_gui"]).launch_gui(args))

    estop_parser = subparsers.add_parser("emergency-stop", parents=[common])
    estop_parser.set_defaults(func=emergency_stop_command)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
