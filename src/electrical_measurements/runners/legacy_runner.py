from __future__ import annotations

import argparse
from datetime import datetime, timezone
import logging
import time
from typing import Any, Callable

import pandas as pd

from ..exceptions import RunnerInputError
from ..instruments.teslatron_client import environment_supports_control
from ..io.dataset import measurement_points_to_dataframe
from ..protocols.contact_check import ContactCheckProtocol
from ..protocols.hall import HallProtocol
from ..protocols.magnetoresistance import MagnetoresistanceProtocol
from ..protocols.reciprocity import ReciprocityProtocol
from ..protocols.second_harmonic import SecondHarmonicProtocol
from ..protocols.vdp_hall import VanDerPauwHallProtocol
from ..protocols.vanderpauw import VanDerPauwProtocol
from ..switching.contact_map import ContactMap
from ..switching.matrix7709 import Matrix7709, SafeMeasurementSession
from .environment_runtime import (
    parse_float_list,
    position_environment,
    resolve_measurement_environment,
    synchronize_trace_and_environment,
)
from .instrument_builder import build_instruments
from .output_finalization import RunSummary, build_stream_record, finalize_run_outputs, postprocess_dataframe

LOGGER = logging.getLogger(__name__)
ADVERTISED_PROTOCOLS = ("hall", "hallbar_mr", "vdp", "vdp_hall", "second_harmonic", "reciprocity", "check_contacts")


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


def _build_hall_protocol(args: argparse.Namespace, config: dict[str, Any], contact_map: ContactMap, m81: Any, matrix: Matrix7709):
    common, _selected_states, selected_state_name = _common_protocol_kwargs(args, config, contact_map, m81, matrix)
    return HallProtocol(
        state=selected_state_name or "hallbar_forward",
        current_rms_a=args.current,
        frequency_hz=args.frequency,
        harmonic=args.harmonic,
        **common,
    )


def _build_hallbar_mr_protocol(args: argparse.Namespace, config: dict[str, Any], contact_map: ContactMap, m81: Any, matrix: Matrix7709):
    common, _selected_states, selected_state_name = _common_protocol_kwargs(args, config, contact_map, m81, matrix)
    return MagnetoresistanceProtocol(
        state=selected_state_name or "hallbar_forward",
        current_rms_a=args.current,
        frequency_hz=args.frequency,
        harmonic=args.harmonic,
        **common,
    )


def _build_vdp_protocol(args: argparse.Namespace, config: dict[str, Any], contact_map: ContactMap, m81: Any, matrix: Matrix7709):
    common, selected_states, _selected_state_name = _common_protocol_kwargs(args, config, contact_map, m81, matrix)
    return VanDerPauwProtocol(
        states=selected_states or None,
        current_rms_a=args.current,
        frequency_hz=args.frequency,
        harmonic=args.harmonic,
        include_anisotropy=bool(getattr(args, "include_anisotropy", False)),
        **common,
    )


def _build_vdp_hall_protocol(args: argparse.Namespace, config: dict[str, Any], contact_map: ContactMap, m81: Any, matrix: Matrix7709):
    common, selected_states, _selected_state_name = _common_protocol_kwargs(args, config, contact_map, m81, matrix)
    return VanDerPauwHallProtocol(
        states=selected_states or None,
        current_rms_a=args.current,
        frequency_hz=args.frequency,
        harmonic=args.harmonic,
        include_reciprocity=bool(getattr(args, "include_reciprocity", False)),
        **common,
    )


def _build_second_harmonic_protocol(args: argparse.Namespace, config: dict[str, Any], contact_map: ContactMap, m81: Any, matrix: Matrix7709):
    common, _selected_states, selected_state_name = _common_protocol_kwargs(args, config, contact_map, m81, matrix)
    return SecondHarmonicProtocol(
        state=selected_state_name or "second_harmonic_vxy",
        current_rms_a=args.current,
        frequency_hz=args.frequency,
        harmonic=args.harmonic,
        **common,
    )


def _build_reciprocity_protocol(args: argparse.Namespace, config: dict[str, Any], contact_map: ContactMap, m81: Any, matrix: Matrix7709):
    common, selected_states, _selected_state_name = _common_protocol_kwargs(args, config, contact_map, m81, matrix)
    return ReciprocityProtocol(
        states=selected_states or list(contact_map.states.keys())[:2],
        current_rms_a=args.current,
        frequency_hz=args.frequency,
        harmonic=args.harmonic,
        **common,
    )


def _build_contact_check_protocol(args: argparse.Namespace, config: dict[str, Any], contact_map: ContactMap, m81: Any, matrix: Matrix7709):
    common, selected_states, _selected_state_name = _common_protocol_kwargs(args, config, contact_map, m81, matrix)
    return ContactCheckProtocol(states=selected_states or list(contact_map.states.keys()), **common)


PROTOCOL_BUILDERS: dict[str, Callable[[argparse.Namespace, dict[str, Any], ContactMap, Any, Matrix7709], Any]] = {
    "hall": _build_hall_protocol,
    "hallbar_mr": _build_hallbar_mr_protocol,
    "vdp": _build_vdp_protocol,
    "vdp_hall": _build_vdp_hall_protocol,
    "second_harmonic": _build_second_harmonic_protocol,
    "reciprocity": _build_reciprocity_protocol,
    "check_contacts": _build_contact_check_protocol,
}


def build_protocol(args: argparse.Namespace, config: dict[str, Any], contact_map: ContactMap, m81: Any, matrix: Matrix7709):
    try:
        builder = PROTOCOL_BUILDERS[args.protocol]
    except KeyError as exc:
        raise RunnerInputError(f"Unsupported protocol: {args.protocol}") from exc
    return builder(args, config, contact_map, m81, matrix)


def extract_stream_settings(protocol: Any) -> tuple[str, str, str, int | None]:
    state = getattr(protocol, "state", None)
    measure_channel = getattr(protocol, "measure_channel", "M1")
    source_channel = getattr(protocol, "source", "S1")
    harmonic = getattr(protocol, "harmonic", None)
    if state is None:
        raise RunnerInputError("Streaming mode currently requires a single-state protocol")
    return state, measure_channel, source_channel, harmonic


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
    LOGGER.info(
        "Starting stream-ramp run protocol=%s state=%s quantity=%s target=%s rate=%s",
        args.protocol,
        getattr(protocol, "state", None),
        args.ramp_quantity,
        args.ramp_target,
        args.ramp_rate,
    )
    protocol.setup()
    try:
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
    finally:
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
    try:
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
    finally:
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
    try:
        with SafeMeasurementSession(m81, matrix):
            for temperature in temperatures:
                for field in fields:
                    current_temperature, current_field = position_environment(environment, temperature, field)
                    if hasattr(m81, "field_t"):
                        m81.field_t = current_field if current_field is not None else field
                    if hasattr(m81, "temperature_k"):
                        m81.temperature_k = current_temperature if current_temperature is not None else temperature
                    points.append(protocol.measure_point(temperature_k=current_temperature, field_t=current_field))
    finally:
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
