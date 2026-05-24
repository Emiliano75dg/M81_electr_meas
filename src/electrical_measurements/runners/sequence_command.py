from __future__ import annotations

import argparse
from datetime import datetime, timezone
import logging

import pandas as pd

from ..capabilities import instrument_capabilities_from_config, instrument_capabilities_from_controller
from ..exceptions import RunnerInputError
from ..instruments.teslatron_client import environment_supports_control
from ..sequences import SequenceRunner, load_measurement_sequence, validate_measurement_sequence
from ..switching.contact_map import ContactMap
from ..switching.matrix7709 import SafeMeasurementSession
from .environment_runtime import parse_float_list, position_environment, resolve_measurement_environment
from .instrument_builder import build_instruments
from .output_finalization import RunSummary, finalize_run_outputs

LOGGER = logging.getLogger(__name__)


def run_sequence_stream_command(
    args: argparse.Namespace,
    config: dict[str, object],
    contact_map: ContactMap,
    environment,
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
    config: dict[str, object],
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
