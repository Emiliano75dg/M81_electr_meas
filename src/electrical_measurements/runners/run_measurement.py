from __future__ import annotations

import argparse
from datetime import datetime, timezone

import pandas as pd

from ..exceptions import RunnerInputError
from ..excitation import validate_legacy_ac_cli_args
from ..io.logging import configure_logging
from ..io.notifications import build_notifier
from ..switching.contact_map import ContactMap
from .environment_runtime import (
    parse_float_list,
    parse_iso_timestamp,
    position_environment,
    resolve_measurement_environment,
    synchronize_trace_and_environment,
)
from .instrument_builder import apply_environment_mode_override, build_instruments, load_yaml
from .legacy_runner import (
    ADVERTISED_PROTOCOLS,
    PROTOCOL_BUILDERS,
    build_protocol,
    extract_stream_settings,
    run_legacy_protocol_command,
    run_stream_observe_command,
    run_stream_ramp_command,
)
from .output_finalization import RunSummary, build_stream_record, finalize_run_outputs, postprocess_dataframe
from .sequence_command import run_sequence_command, run_sequence_stream_command


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
    run_parser.add_argument("--protocol", choices=list(ADVERTISED_PROTOCOLS))
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
    analyze_parser.add_argument("--protocol", required=True, choices=list(ADVERTISED_PROTOCOLS))
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
