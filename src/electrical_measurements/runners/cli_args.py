from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import Any

from ..exceptions import RunnerInputError
from ..excitation import validate_legacy_ac_cli_args
from ..switching.contact_map import ContactMap
from .legacy_runner import ADVERTISED_PROTOCOLS


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


def _resolve_parser_handlers(
    handlers: dict[str, Callable[[argparse.Namespace], int]] | None,
) -> dict[str, Callable[[argparse.Namespace], int]]:
    if handlers is not None:
        return handlers
    from . import run_measurement

    return {
        "run": run_measurement.run_command,
        "list_instruments": run_measurement.list_instruments_command,
        "analyze": run_measurement.analyze_command,
        "emergency_stop": run_measurement.emergency_stop_command,
        "gui": lambda args: __import__("electrical_measurements.gui.app", fromlist=["launch_gui"]).launch_gui(args),
    }


def build_parser(
    handlers: dict[str, Callable[[argparse.Namespace], int]] | None = None,
) -> argparse.ArgumentParser:
    parser_handlers = _resolve_parser_handlers(handlers)

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
    run_parser.set_defaults(func=parser_handlers["run"])

    list_parser = subparsers.add_parser("list-instruments", parents=[common])
    list_parser.set_defaults(func=parser_handlers["list_instruments"])

    contact_parser = subparsers.add_parser("check-contacts", parents=[common])
    contact_parser.add_argument("--sample-id")
    contact_parser.add_argument("--output")
    contact_parser.add_argument("--settle", type=float, default=0.1)
    contact_parser.add_argument("--dry-run", action="store_true")
    contact_parser.set_defaults(
        func=lambda args: parser_handlers["run"](
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
    analyze_parser.set_defaults(func=parser_handlers["analyze"])

    gui_parser = subparsers.add_parser("gui", parents=[common])
    gui_parser.set_defaults(func=parser_handlers["gui"])

    estop_parser = subparsers.add_parser("emergency-stop", parents=[common])
    estop_parser.set_defaults(func=parser_handlers["emergency_stop"])

    return parser
