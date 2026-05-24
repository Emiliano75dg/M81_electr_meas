from __future__ import annotations

import argparse
from datetime import datetime, timezone

import pandas as pd

from ..io.logging import configure_logging
from ..io.notifications import build_notifier
from ..switching.contact_map import ContactMap
from .cli_args import build_parser, build_run_namespace, validate_run_inputs
from .environment_runtime import position_environment, resolve_measurement_environment, synchronize_trace_and_environment
from .instrument_builder import apply_environment_mode_override, build_instruments, load_yaml
from .legacy_runner import (
    ADVERTISED_PROTOCOLS,
    build_protocol,
    run_legacy_protocol_command,
    run_stream_observe_command,
    run_stream_ramp_command,
)
from .output_finalization import postprocess_dataframe
from .sequence_command import run_sequence_command


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


def main() -> int:
    parser = build_parser(
        {
            "run": run_command,
            "list_instruments": list_instruments_command,
            "analyze": analyze_command,
            "emergency_stop": emergency_stop_command,
            "gui": lambda args: __import__("electrical_measurements.gui.app", fromlist=["launch_gui"]).launch_gui(args),
        }
    )
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
