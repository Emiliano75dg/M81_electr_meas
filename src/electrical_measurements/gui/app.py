from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import queue
import threading
import time
import tempfile
import traceback
import tkinter as tk
from tkinter import filedialog, ttk

import pandas as pd
import yaml

from ..runners.run_measurement import (
    apply_environment_mode_override,
    build_instruments,
    build_run_namespace,
    emergency_stop_command,
    load_yaml,
    run_command,
)
from ..instruments.teslatron_client import environment_mode_from_config
from ..protocols.vdp_hall import default_vdp_hall_states
from ..sequences import (
    load_measurement_sequence,
    SequenceRunner,
    measurement_sequence_from_dict,
    measurement_sequence_to_dict,
    validate_measurement_sequence,
)
from ..switching.contact_map import ContactMap

PROTOCOLS = ["hall", "hallbar_mr", "vdp", "vdp_hall", "second_harmonic", "reciprocity", "check_contacts"]
MODES = ["stable", "stream-observe", "stream-ramp"]
RAMP_QUANTITIES = ["field", "temperature"]
ENVIRONMENT_MODES = ["integrated", "async-poll", "standalone"]
PLOT_PREFERRED_COLUMNS = ["field_t", "temperature_k", "rxx_ohm", "rxy_ohm", "lockin_x", "lockin_r", "dc_value"]
MANUAL_SOURCE_MODES = ["DC current", "DC voltage", "AC current", "AC voltage"]
MEASURE_ACQUISITION_MODES = ["Auto", "Lock-in", "DC"]
LOCKIN_ROLLOFFS = ["R6", "R12", "R18", "R24"]
LIVE_PLOT_X_COLUMNS = ["sample_index", "elapsed_s", "field_t", "temperature_k"]
LIVE_PLOT_Y_COLUMNS = ["x", "y", "r", "theta_deg", "value", "x_dual", "r_dual"]
SEQUENCE_EXCITATION_MODES = ["dc", "ac"]
SEQUENCE_SOURCES = ["", "S1", "S2", "S3"]
SEQUENCE_MEASURE_CHANNELS = ["", "M1", "M2", "M3"]
SEQUENCE_PRESET_CATEGORIES = {
    "Van der Pauw": [
        "vdp_full_ac",
        "vdp_full_dc",
        "vdp_fast_hall_ac",
        "vdp_hall_second_harmonic_ac",
        "vdp_reciprocity_check",
        "vdp_hall_with_drift_guard",
        "vdp_lockin_phase_diagnostic",
    ],
    "Hall bar": [
        "hallbar_static_1w_2w",
        "hallbar_static_fast",
        "hallbar_static_drift_guard",
    ],
    "Diagnostics": [
        "second_harmonic_frequency_check",
    ],
}
SEQUENCE_PRESET_PATHS = {
    "vdp_full_ac": "configs/sequences/vdp_full_ac.yaml",
    "vdp_full_dc": "configs/sequences/vdp_full_dc.yaml",
    "vdp_fast_hall_ac": "configs/sequences/vdp_fast_hall_ac.yaml",
    "vdp_hall_second_harmonic_ac": "configs/sequences/vdp_hall_second_harmonic_ac.yaml",
    "vdp_reciprocity_check": "configs/sequences/vdp_reciprocity_check.yaml",
    "vdp_hall_with_drift_guard": "configs/sequences/vdp_hall_with_drift_guard.yaml",
    "vdp_lockin_phase_diagnostic": "configs/sequences/vdp_lockin_phase_diagnostic.yaml",
    "hallbar_static_1w_2w": "configs/sequences/hallbar_static_1w_2w.yaml",
    "hallbar_static_fast": "configs/sequences/hallbar_static_fast.yaml",
    "hallbar_static_drift_guard": "configs/sequences/hallbar_static_drift_guard.yaml",
    "second_harmonic_frequency_check": "configs/sequences/second_harmonic_frequency_check.yaml",
}


def sequence_preset_names_for_category(category: str) -> list[str]:
    return list(SEQUENCE_PRESET_CATEGORIES.get(category, []))


def describe_sequence_preset(preset_name: str) -> dict[str, object]:
    path = SEQUENCE_PRESET_PATHS[preset_name]
    sequence = load_measurement_sequence(path)
    channels: set[str] = set()
    harmonics: set[int] = set()
    features: set[str] = set()
    uses_matrix = str(sequence.defaults.matrix_policy).strip().lower() != "none"
    for step in sequence.steps:
        if step.measure_channel:
            channels.add(step.measure_channel)
        for channel in step.measure_channels or []:
            channels.add(channel)
        if step.measure_specs:
            channels.update(step.measure_specs)
            for spec in step.measure_specs.values():
                if spec.harmonic is not None:
                    harmonics.add(int(spec.harmonic))
                if "theta" in spec.readout or "y" in spec.readout or "r" in spec.readout:
                    features.add("phase")
                if spec.harmonic == 2:
                    features.add("second_harmonic")
        elif step.harmonic is not None:
            harmonics.add(int(step.harmonic))
        if (step.reciprocal_step_of or ""):
            features.add("reciprocity")
        if step.measure_kind in {"hall", "hall_1", "hall_2"} or "hall" in (step.tags or []):
            features.add("hall")
        if "drift_guard" in (step.tags or []) or "reference_repeat" in (step.tags or []):
            features.add("drift_guard")
        if str(step.matrix_policy or "").strip().lower() == "none":
            uses_matrix = False
    if not channels and sequence.defaults.measure_channel:
        channels.add(sequence.defaults.measure_channel)
    if not harmonics and sequence.defaults.harmonic is not None:
        harmonics.add(int(sequence.defaults.harmonic))
    return {
        "name": preset_name,
        "description": sequence.description or "",
        "steps": len(sequence.steps),
        "channels": sorted(channels),
        "harmonics": sorted(harmonics),
        "switching": "matrix" if uses_matrix else "static wiring",
        "features": sorted(features),
        "path": path,
    }


def format_sequence_preset_summary(preset_name: str) -> str:
    summary = describe_sequence_preset(preset_name)
    return "\n".join(
        [
            f"Name: {summary['name']}",
            f"Description: {summary['description']}",
            f"Steps: {summary['steps']}",
            f"Channels: {', '.join(summary['channels']) or '--'}",
            f"Harmonics: {', '.join(str(value) for value in summary['harmonics']) or '--'}",
            f"Switching: {summary['switching']}",
            f"Features: {', '.join(summary['features']) or '--'}",
            f"Path: {summary['path']}",
        ]
    )


def empty_sequence_data(contact_map_path: str | None = None) -> dict[str, object]:
    return {
        "name": "new_sequence",
        "description": "",
        "contact_map": contact_map_path or "",
        "expert_mode": False,
        "defaults": {
            "source_mode": "ac",
            "excitation_mode": "ac",
            "source_quantity": "current",
            "source": "S1",
            "source_channel": "S1",
            "measure_channel": "M1",
            "measure_channels": [],
            "current_a": None,
            "current_rms_a": 1.0e-5,
            "source_value": 1.0e-5,
            "frequency_hz": 13.7,
            "harmonic": 1,
            "measure_mode": "lockin",
            "readout": "x",
            "settle_s": 0.1,
            "repeats": 1,
            "reverse_policy": "none",
            "matrix_policy": "apply_state",
            "lockin": True,
            "metadata": {},
        },
        "steps": [],
    }


def parse_csv_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_csv_mapping(value: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in parse_csv_list(value):
        if ":" not in item:
            raise ValueError(f"Expected key:value item, got '{item}'")
        key, mapped = item.split(":", 1)
        key = key.strip()
        mapped = mapped.strip()
        if not key or not mapped:
            raise ValueError(f"Expected key:value item, got '{item}'")
        mapping[key] = mapped
    return mapping


def format_outputs_mapping(value: dict[str, object] | None) -> str:
    if not value:
        return ""
    parts: list[str] = []
    for key, mapped in value.items():
        if isinstance(mapped, dict):
            name = mapped.get("name")
            transform = mapped.get("transform")
            if name and transform:
                parts.append(f"{key}:{name}/{transform}")
            elif name:
                parts.append(f"{key}:{name}")
            else:
                parts.append(f"{key}:{mapped}")
        else:
            parts.append(f"{key}:{mapped}")
    return ",".join(parts)


def stringify_step_tags(step: dict[str, object]) -> str:
    tags = step.get("tags", [])
    return ",".join(tags) if isinstance(tags, list) else ""


def relay_channels_for_state(contact_map: ContactMap | None, state_name: str) -> str:
    if contact_map is None or not state_name or state_name not in contact_map.states:
        return ""
    return ",".join(str(channel) for channel in contact_map.get_state(state_name).get("relay_channels", []))


def sequence_step_table_row(step: dict[str, object], defaults: dict[str, object], contact_map: ContactMap | None, index: int) -> dict[str, str]:
    mode = str(step.get("source_mode") or step.get("excitation_mode") or defaults.get("source_mode") or defaults.get("excitation_mode") or "")
    measure_channels = step.get("measure_channels")
    if not measure_channels:
        single_channel = step.get("measure_channel") or defaults.get("measure_channel")
        measure_channels = [single_channel] if single_channel else defaults.get("measure_channels", [])
    measure_channels = [item for item in measure_channels if item]
    if mode == "dc":
        current_a = step.get("source_value", step.get("current_a"))
        if current_a is None:
            current_a = defaults.get("source_value", defaults.get("current_a"))
        polarity = -1 if str(step.get("reverse_policy", "")).strip().lower() == "dc_source_inversion" and float(current_a or 0.0) < 0 else step.get("bias_polarity", 1)
        current_text = ""
        if current_a is not None:
            current_text = f"{float(current_a) * int(polarity):.6g}"
    else:
        current_rms_a = step.get("source_value", step.get("current_rms_a"))
        if current_rms_a is None:
            current_rms_a = defaults.get("source_value", defaults.get("current_rms_a"))
        current_text = "" if current_rms_a is None else f"{float(current_rms_a):.6g}"
    measure_specs = step.get("measure_specs")
    def _format_channel_spec(channel: str) -> str:
        if isinstance(measure_specs, dict) and isinstance(measure_specs.get(channel), dict):
            spec = measure_specs[channel]
            harmonic = spec.get("harmonic")
            readout = spec.get("readout", "")
            transform = spec.get("transform", "")
            output = spec.get("output", "")
            return "/".join(
                part
                for part in [
                    str(harmonic) if harmonic not in (None, "") else "",
                    str(readout),
                    str(transform),
                    str(output),
                ]
                if part
            )
        if channel in measure_channels:
            harmonic_value = step.get("harmonic") if step.get("harmonic") is not None else defaults.get("harmonic")
            readout_value = step.get("readout") if step.get("readout") is not None else defaults.get("readout") or "value"
            return "/".join(part for part in [str(harmonic_value or ""), str(readout_value)] if part)
        return ""
    return {
        "index": str(index + 1),
        "step_name": str(step.get("name", "")),
        "state_name": str(step.get("state", "")),
        "excitation_mode": mode,
        "source": str(step.get("source_channel") or step.get("source") or defaults.get("source_channel") or defaults.get("source") or ""),
        "current": current_text,
        "frequency_hz": str(step.get("frequency_hz") if step.get("frequency_hz") is not None else defaults.get("frequency_hz") or ""),
        "harmonic": str(step.get("harmonic") if step.get("harmonic") is not None else defaults.get("harmonic") or ""),
        "measure_channels": ",".join(measure_channels),
        "m1_spec": _format_channel_spec("M1"),
        "m2_spec": _format_channel_spec("M2"),
        "m3_spec": _format_channel_spec("M3"),
        "outputs": format_outputs_mapping(step.get("outputs") if isinstance(step.get("outputs"), dict) else None),
        "settle_s": str(step.get("settle_s") if step.get("settle_s") is not None else defaults.get("settle_s") or ""),
        "repeats": str(step.get("repeats") if step.get("repeats") is not None else defaults.get("repeats") or ""),
        "reverse_policy": str(step.get("reverse_policy") or defaults.get("reverse_policy") or ""),
        "matrix_policy": str(step.get("matrix_policy") or defaults.get("matrix_policy") or "apply_state"),
        "tags": stringify_step_tags(step),
        "reciprocal_step_of": str(step.get("reciprocal_step_of") or step.get("reciprocal_of") or ""),
        "relay_channels": relay_channels_for_state(contact_map, str(step.get("state", ""))),
    }


def environment_mode_supports_control(environment_mode: str) -> bool:
    return environment_mode == "integrated"


def protocol_is_multi_state(protocol: str) -> bool:
    return protocol in {"vdp", "vdp_hall", "reciprocity", "check_contacts"}


def recommended_states_for_protocol(protocol: str, contact_map: ContactMap) -> list[str]:
    states = contact_map.states
    if protocol == "hall":
        preferred = [name for name, state in states.items() if "voltage_transverse" in state or state.get("type") in {"hallbar", "hall"}]
        return preferred or list(states.keys())
    if protocol == "hallbar_mr":
        preferred = [name for name, state in states.items() if "voltage_longitudinal" in state or state.get("group") == "longitudinal"]
        return preferred or list(states.keys())
    if protocol == "vdp":
        preferred = [
            name
            for name, state in contact_map.states.items()
            if str(state.get("group", "")).startswith("vdp")
        ]
        return preferred or list(states.keys())
    if protocol == "vdp_hall":
        preferred = default_vdp_hall_states(contact_map)
        return preferred or list(states.keys())
    if protocol == "second_harmonic":
        preferred = [name for name, state in states.items() if state.get("group") == "second_harmonic" or state.get("type") == "second_harmonic"]
        return preferred or list(states.keys())
    return list(states.keys())


def latest_csv_files(output_dir: str | Path) -> list[Path]:
    directory = Path(output_dir)
    if not directory.exists():
        return []
    return sorted(directory.glob("*.csv"), key=lambda path: path.stat().st_mtime, reverse=True)


def preview_csv_text(path: str | Path, rows: int = 12) -> str:
    csv_path = Path(path)
    if not csv_path.exists():
        return f"File not found: {csv_path}"
    dataframe = pd.read_csv(csv_path, nrows=rows)
    return dataframe.to_string(index=False)


def plottable_columns(dataframe: pd.DataFrame) -> list[str]:
    numeric = [
        column
        for column in dataframe.columns
        if pd.api.types.is_numeric_dtype(dataframe[column]) and dataframe[column].notna().any()
    ]
    preferred = [column for column in PLOT_PREFERRED_COLUMNS if column in numeric]
    remaining = [column for column in numeric if column not in preferred]
    return preferred + remaining


def compute_plot_points(
    dataframe: pd.DataFrame,
    x_col: str,
    y_col: str,
    width: int,
    height: int,
    padding: int = 24,
) -> list[tuple[float, float]]:
    if x_col not in dataframe.columns or y_col not in dataframe.columns:
        return []
    subset = dataframe[[x_col, y_col]].dropna()
    if subset.empty:
        return []
    x_values = subset[x_col].astype(float).to_list()
    y_values = subset[y_col].astype(float).to_list()
    x_min, x_max = min(x_values), max(x_values)
    y_min, y_max = min(y_values), max(y_values)
    if x_min == x_max:
        x_min -= 1.0
        x_max += 1.0
    if y_min == y_max:
        y_min -= 1.0
        y_max += 1.0
    usable_width = max(width - 2 * padding, 1)
    usable_height = max(height - 2 * padding, 1)
    points: list[tuple[float, float]] = []
    for x_value, y_value in zip(x_values, y_values):
        x_canvas = padding + (x_value - x_min) / (x_max - x_min) * usable_width
        y_canvas = height - padding - (y_value - y_min) / (y_max - y_min) * usable_height
        points.append((x_canvas, y_canvas))
    return points


def build_live_record(
    sample_index: int,
    elapsed_s: float,
    measure_channel: str,
    reading: dict[str, object],
    temperature_k: float | None,
    field_t: float | None,
    extra_values: dict[str, object] | None = None,
) -> dict[str, object]:
    record: dict[str, object] = {
        "sample_index": sample_index,
        "elapsed_s": elapsed_s,
        "measure_channel": measure_channel,
        "temperature_k": temperature_k,
        "field_t": field_t,
        "timestamp": reading.get("timestamp"),
    }
    for key in LIVE_PLOT_Y_COLUMNS:
        if key in reading:
            record[key] = reading.get(key)
    if extra_values:
        record.update(extra_values)
    return record


def hallbar_live_channels(contact_map: ContactMap | None) -> dict[str, str]:
    if contact_map is None:
        return {}
    channels: dict[str, str] = {}
    vxx = contact_map.instrument_channel("vxx_meter")
    vxy = contact_map.instrument_channel("vxy_meter")
    if vxx:
        channels["vxx"] = vxx
    if vxy:
        channels["vxy"] = vxy
    return channels


def append_buffered_record(records: list[dict[str, object]], record: dict[str, object], limit: int) -> list[dict[str, object]]:
    updated = [*records, record]
    if limit > 0 and len(updated) > limit:
        return updated[-limit:]
    return updated


def ui_measure_mode_to_backend(value: str) -> str:
    normalized = value.strip().lower().replace("-", "")
    if normalized == "lockin":
        return "lockin"
    if normalized == "dc":
        return "dc"
    return "auto"


def backend_measure_mode_to_ui(value: str | None) -> str:
    normalized = str(value or "auto").strip().lower()
    if normalized == "lockin":
        return "Lock-in"
    if normalized == "dc":
        return "DC"
    return "Auto"


def parse_optional_positive_int(value: str, name: str, default: int = 1) -> int:
    stripped = value.strip()
    if not stripped:
        return default
    return parse_required_int(stripped, name, minimum=1)


def parse_optional_positive_float(value: str, name: str, default: float) -> float:
    stripped = value.strip()
    if not stripped:
        return default
    return parse_required_float(stripped, name, positive=True)


def format_measure_summary(measures: dict[str, object] | None) -> str:
    if not isinstance(measures, dict) or not measures:
        return "Measures: --"
    parts: list[str] = []
    for channel in sorted(measures):
        details = measures.get(channel)
        if not isinstance(details, dict):
            parts.append(f"{channel}: --")
            continue
        mode = str(details.get("resolved_mode") or details.get("preferred_mode") or details.get("mode") or "auto").strip().lower()
        if mode == "lockin":
            harmonic = details.get("resolved_harmonic") or details.get("preferred_harmonic") or details.get("harmonic") or 1
            parts.append(f"{channel}: lock-in @ {harmonic}f")
        elif mode == "dc":
            parts.append(f"{channel}: DC")
        else:
            parts.append(f"{channel}: auto")
    return f"Measures: {' | '.join(parts)}"


def format_sequence_measure_spec(spec: dict[str, object] | None) -> str:
    if not isinstance(spec, dict):
        return ""
    return " | ".join(
        f"{label}={value}"
        for label, value in [
            ("mode", spec.get("measure_mode")),
            ("harm", spec.get("harmonic")),
            ("readout", spec.get("readout")),
            ("transform", spec.get("transform")),
            ("output", spec.get("output")),
        ]
        if value not in (None, "")
    )


def parse_required_float(value: str, name: str, *, positive: bool = False, non_negative: bool = False) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a valid number") from exc
    if positive and parsed <= 0:
        raise ValueError(f"{name} must be > 0")
    if non_negative and parsed < 0:
        raise ValueError(f"{name} must be >= 0")
    return parsed


def parse_required_int(value: str, name: str, *, minimum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a valid integer") from exc
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return parsed


class MeasurementGUI:
    def __init__(self, root: tk.Tk, initial_args: argparse.Namespace | None = None) -> None:
        self.root = root
        self.root.title("Electrical Measurements GUI")
        self.root.geometry("1280x860")
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.contact_map: ContactMap | None = None
        self.live_config: dict | None = None
        self.live_m81 = None
        self.live_matrix = None
        self.live_environment = None
        self.preview_dataframe: pd.DataFrame | None = None
        self.live_data: list[dict[str, object]] = []
        self.live_data_lock = threading.Lock()
        self.live_acquire_thread: threading.Thread | None = None
        self.live_acquire_stop = threading.Event()
        self.live_acquire_started_at: float | None = None
        self.setup_widgets: dict[str, tk.Widget] = {}
        self.live_environment_widgets: list[tk.Widget] = []
        self.include_reciprocity_check: ttk.Checkbutton | None = None
        self.include_anisotropy_check: ttk.Checkbutton | None = None
        self.sequence_data: dict[str, object] = {}
        self.sequence_file_path: Path | None = None
        self.sequence_validation_ok = False
        self.sequence_step_index: int | None = None

        self.config_var = tk.StringVar(value=getattr(initial_args, "config", "configs/instruments.yaml"))
        self.contact_map_var = tk.StringVar(value=getattr(initial_args, "contact_map", "configs/contact_maps/hallbar_6contacts_7709.yaml"))
        self.output_var = tk.StringVar(value="data")
        self.sample_id_var = tk.StringVar(value="")
        self.protocol_var = tk.StringVar(value="hallbar_mr")
        self.mode_var = tk.StringVar(value="stable")
        self.mock_var = tk.BooleanVar(value=True)
        self.dry_run_var = tk.BooleanVar(value=False)
        self.include_reciprocity_var = tk.BooleanVar(value=False)
        self.include_anisotropy_var = tk.BooleanVar(value=False)
        self.environment_mode_var = tk.StringVar(value="async-poll")
        self.temperatures_var = tk.StringVar(value="300")
        self.fields_var = tk.StringVar(value="0")
        self.current_var = tk.StringVar(value="1e-5")
        self.frequency_var = tk.StringVar(value="13.7")
        self.harmonic_var = tk.StringVar(value="1")
        self.settle_var = tk.StringVar(value="0.1")
        self.ramp_quantity_var = tk.StringVar(value="field")
        self.ramp_target_var = tk.StringVar(value="0.5")
        self.ramp_rate_var = tk.StringVar(value="60")
        self.stream_samples_var = tk.StringVar(value="20")
        self.stream_interval_var = tk.StringVar(value="0.1")
        self.state_name_var = tk.StringVar(value="")
        self.preview_file_var = tk.StringVar(value="")
        self.plot_x_var = tk.StringVar(value="field_t")
        self.plot_y_var = tk.StringVar(value="rxx_ohm")
        self.manual_source_var = tk.StringVar(value="S1")
        self.manual_source_mode_var = tk.StringVar(value="AC current")
        self.manual_setpoint_var = tk.StringVar(value="1e-5")
        self.manual_frequency_var = tk.StringVar(value="13.7")
        self.manual_harmonic_var = tk.StringVar(value="1")
        self.manual_measure_channel_var = tk.StringVar(value="M1")
        self.live_measure_channel_var = tk.StringVar(value="M1")
        self.measure_mode_vars = {channel: tk.StringVar(value="Auto") for channel in ["M1", "M2", "M3"]}
        self.measure_harmonic_vars = {channel: tk.StringVar(value="1") for channel in ["M1", "M2", "M3"]}
        self.measure_nplc_vars = {channel: tk.StringVar(value="1.0") for channel in ["M1", "M2", "M3"]}
        self.measure_time_constant_vars = {channel: tk.StringVar(value="0.3") for channel in ["M1", "M2", "M3"]}
        self.measure_rolloff_vars = {channel: tk.StringVar(value="R24") for channel in ["M1", "M2", "M3"]}
        self.live_plot_x_var = tk.StringVar(value="elapsed_s")
        self.live_plot_y_var = tk.StringVar(value="x")
        self.live_interval_var = tk.StringVar(value="0.25")
        self.live_buffer_var = tk.StringVar(value="300")
        self.live_env_temperature_target_var = tk.StringVar(value="300")
        self.live_env_field_target_var = tk.StringVar(value="0")
        self.live_env_ramp_rate_var = tk.StringVar(value="60")
        self.status_var = tk.StringVar(value="Ready")
        self.sequence_path_var = tk.StringVar(value="")
        self.sequence_preset_category_var = tk.StringVar(value="Van der Pauw")
        self.sequence_preset_var = tk.StringVar(value="vdp_full_ac")
        self.sequence_name_var = tk.StringVar(value="new_sequence")
        self.sequence_description_var = tk.StringVar(value="")
        self.sequence_contact_map_var = tk.StringVar(value=self.contact_map_var.get())
        self.sequence_expert_mode_var = tk.BooleanVar(value=False)
        self.sequence_default_mode_var = tk.StringVar(value="ac")
        self.sequence_default_source_var = tk.StringVar(value="S1")
        self.sequence_default_measure_channel_var = tk.StringVar(value="M1")
        self.sequence_default_measure_channels_var = tk.StringVar(value="")
        self.sequence_default_current_a_var = tk.StringVar(value="")
        self.sequence_default_current_rms_a_var = tk.StringVar(value="1e-5")
        self.sequence_default_frequency_var = tk.StringVar(value="13.7")
        self.sequence_default_harmonic_var = tk.StringVar(value="1")
        self.sequence_default_settle_var = tk.StringVar(value="0.1")
        self.sequence_default_repeats_var = tk.StringVar(value="1")
        self.sequence_default_lockin_var = tk.BooleanVar(value=True)
        self.sequence_selected_state_var = tk.StringVar(value="")
        self.sequence_step_name_var = tk.StringVar(value="")
        self.sequence_step_state_var = tk.StringVar(value="")
        self.sequence_step_mode_var = tk.StringVar(value="ac")
        self.sequence_step_source_var = tk.StringVar(value="")
        self.sequence_step_measure_channel_var = tk.StringVar(value="")
        self.sequence_step_measure_channels_var = tk.StringVar(value="")
        self.sequence_step_current_a_var = tk.StringVar(value="")
        self.sequence_step_current_rms_a_var = tk.StringVar(value="")
        self.sequence_step_frequency_var = tk.StringVar(value="")
        self.sequence_step_harmonic_var = tk.StringVar(value="")
        self.sequence_step_bias_polarity_var = tk.StringVar(value="1")
        self.sequence_step_settle_var = tk.StringVar(value="")
        self.sequence_step_repeats_var = tk.StringVar(value="")
        self.sequence_step_measure_kind_var = tk.StringVar(value="")
        self.sequence_step_tags_var = tk.StringVar(value="")
        self.sequence_step_reciprocal_var = tk.StringVar(value="")
        self.sequence_step_outputs_var = tk.StringVar(value="")
        self.sequence_step_matrix_policy_var = tk.StringVar(value="apply_state")
        self.sequence_measure_spec_mode_vars = {channel: tk.StringVar(value="") for channel in ["M1", "M2", "M3"]}
        self.sequence_measure_spec_harmonic_vars = {channel: tk.StringVar(value="") for channel in ["M1", "M2", "M3"]}
        self.sequence_measure_spec_readout_vars = {channel: tk.StringVar(value="") for channel in ["M1", "M2", "M3"]}
        self.sequence_measure_spec_output_vars = {channel: tk.StringVar(value="") for channel in ["M1", "M2", "M3"]}
        self.sequence_measure_spec_transform_vars = {channel: tk.StringVar(value="") for channel in ["M1", "M2", "M3"]}
        self.sequence_step_relay_var = tk.StringVar(value="")
        self.sequence_validation_var = tk.StringVar(value="Sequence not validated")
        self.live_temperature_var = tk.StringVar(value="T: --")
        self.live_field_var = tk.StringVar(value="B: --")
        self.live_sources_var = tk.StringVar(value="Sources: --")
        self.live_relays_var = tk.StringVar(value="Relays: --")
        self.live_measures_var = tk.StringVar(value="Measures: --")
        self.live_connection_var = tk.StringVar(value="Disconnected")
        self.live_environment_mode_var = tk.StringVar(value="Env mode: --")
        self.live_environment_action_var = tk.StringVar(value="Env control idle")
        self.live_acquisition_var = tk.StringVar(value="Acquisition stopped")
        self.live_last_reading_var = tk.StringVar(value="Last reading: --")

        self._build_layout()
        self._update_sequence_preset_choices()
        self._new_empty_sequence()
        self._sync_environment_mode_from_config()
        self._load_contact_map()
        self._update_mode_state()
        self._update_protocol_state()
        self.root.after(100, self._drain_logs)
        self.root.after(500, self._poll_live_status)

    def _build_layout(self) -> None:
        container = ttk.Frame(self.root, padding=10)
        container.pack(fill="both", expand=True)
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)

        self.notebook = ttk.Notebook(container)
        self.notebook.grid(row=0, column=0, sticky="nsew")

        self.setup_tab = ttk.Frame(self.notebook, padding=12)
        self.states_tab = ttk.Frame(self.notebook, padding=12)
        self.sequence_tab = ttk.Frame(self.notebook, padding=12)
        self.live_tab = ttk.Frame(self.notebook, padding=12)
        self.log_tab = ttk.Frame(self.notebook, padding=12)
        self.preview_tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(self.setup_tab, text="Setup")
        self.notebook.add(self.states_tab, text="States")
        self.notebook.add(self.sequence_tab, text="Sequence")
        self.notebook.add(self.live_tab, text="Live")
        self.notebook.add(self.log_tab, text="Log")
        self.notebook.add(self.preview_tab, text="Preview")

        self._build_setup_tab()
        self._build_states_tab()
        self._build_sequence_tab()
        self._build_live_tab()
        self._build_log_tab()
        self._build_preview_tab()

    def _build_setup_tab(self) -> None:
        frame = self.setup_tab
        frame.columnconfigure(1, weight=1)
        row = 0
        row = self._add_path_row(frame, row, "Config", self.config_var, self._browse_config)
        row = self._add_path_row(frame, row, "Contact map", self.contact_map_var, self._browse_contact_map)
        row = self._add_path_row(frame, row, "Output", self.output_var, lambda: self._browse_dir(self.output_var))
        row = self._add_entry_row(frame, row, "Sample ID", self.sample_id_var)
        row = self._add_combo_row(frame, row, "Protocol", self.protocol_var, PROTOCOLS, self._update_protocol_state)
        row = self._add_combo_row(frame, row, "Mode", self.mode_var, MODES, self._update_mode_state, widget_key="mode")
        row = self._add_combo_row(frame, row, "Env mode", self.environment_mode_var, ENVIRONMENT_MODES, self._update_environment_mode_state, widget_key="environment_mode")
        row = self._add_combo_row(frame, row, "Ramp quantity", self.ramp_quantity_var, RAMP_QUANTITIES, widget_key="ramp_quantity")
        row = self._add_entry_row(frame, row, "Temperatures", self.temperatures_var, widget_key="temperatures")
        row = self._add_entry_row(frame, row, "Fields", self.fields_var, widget_key="fields")
        row = self._add_entry_row(frame, row, "Current (A)", self.current_var)
        row = self._add_entry_row(frame, row, "Frequency (Hz)", self.frequency_var)
        row = self._add_entry_row(frame, row, "Harmonic", self.harmonic_var)
        row = self._add_entry_row(frame, row, "Settle (s)", self.settle_var)
        row = self._add_entry_row(frame, row, "Ramp target", self.ramp_target_var, widget_key="ramp_target")
        row = self._add_entry_row(frame, row, "Ramp rate", self.ramp_rate_var, widget_key="ramp_rate")
        row = self._add_entry_row(frame, row, "Stream samples", self.stream_samples_var)
        row = self._add_entry_row(frame, row, "Stream interval (s)", self.stream_interval_var)

        options = ttk.Frame(frame)
        options.grid(row=row, column=0, columnspan=3, sticky="w", pady=(8, 8))
        ttk.Checkbutton(options, text="Mock", variable=self.mock_var).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(options, text="Dry run", variable=self.dry_run_var).pack(side="left")
        self.include_reciprocity_check = ttk.Checkbutton(options, text="Include reciprocity", variable=self.include_reciprocity_var)
        self.include_reciprocity_check.pack(side="left", padx=(12, 0))
        self.include_anisotropy_check = ttk.Checkbutton(options, text="Include anisotropy", variable=self.include_anisotropy_var)
        self.include_anisotropy_check.pack(side="left", padx=(12, 0))
        row += 1

        actions = ttk.Frame(frame)
        actions.grid(row=row, column=0, columnspan=3, sticky="ew")
        ttk.Button(actions, text="Run Measurement", command=self._run_measurement).pack(side="left")
        ttk.Button(actions, text="Emergency Stop", command=self._emergency_stop).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Reload Contact Map", command=self._load_contact_map).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Refresh Preview", command=self._refresh_preview).pack(side="left", padx=(8, 0))
        ttk.Label(actions, textvariable=self.status_var).pack(side="right")

    def _build_states_tab(self) -> None:
        frame = self.states_tab
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(4, weight=1)

        ttk.Label(frame, text="Selected state").grid(row=0, column=0, sticky="w")
        self.state_combo = ttk.Combobox(frame, textvariable=self.state_name_var, state="readonly")
        self.state_combo.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=3)

        ttk.Label(frame, text="Multi-state selection").grid(row=1, column=0, sticky="nw", pady=(8, 0))
        multi_frame = ttk.Frame(frame)
        multi_frame.grid(row=1, column=1, sticky="nsew", padx=(8, 0), pady=(8, 0))
        multi_frame.rowconfigure(0, weight=1)
        multi_frame.columnconfigure(0, weight=1)
        self.state_listbox = tk.Listbox(multi_frame, selectmode="extended", exportselection=False, height=8)
        self.state_listbox.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(multi_frame, orient="vertical", command=self.state_listbox.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.state_listbox.configure(yscrollcommand=scrollbar.set)

        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=1, sticky="w", pady=6)
        ttk.Button(buttons, text="Select Recommended", command=self._select_recommended_states).pack(side="left")
        ttk.Button(buttons, text="Select All", command=self._select_all_states).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Clear", command=self._clear_state_selection).pack(side="left", padx=(8, 0))

        ttk.Label(frame, text="State details").grid(row=3, column=0, sticky="nw")
        self.state_details = tk.Text(frame, wrap="word", height=18)
        self.state_details.grid(row=3, column=1, sticky="nsew", padx=(8, 0))
        self.state_combo.bind("<<ComboboxSelected>>", lambda _event: self._update_state_details())
        self.state_listbox.bind("<<ListboxSelect>>", lambda _event: self._update_state_details())

    def _build_sequence_tab(self) -> None:
        frame = self.sequence_tab
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        frame.rowconfigure(2, weight=1)

        top = ttk.Frame(frame)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="Sequence YAML").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.sequence_path_var).grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Button(top, text="Load Sequence YAML", command=self._load_sequence_yaml).grid(row=0, column=2, padx=(0, 6))
        ttk.Button(top, text="Save Sequence YAML", command=self._save_sequence_yaml).grid(row=0, column=3)
        ttk.Label(top, text="Preset category").grid(row=1, column=0, sticky="w", pady=(6, 0))
        preset_category_combo = ttk.Combobox(top, textvariable=self.sequence_preset_category_var, values=list(SEQUENCE_PRESET_CATEGORIES), state="readonly")
        preset_category_combo.grid(row=1, column=1, sticky="ew", padx=(8, 8), pady=(6, 0))
        preset_category_combo.bind("<<ComboboxSelected>>", lambda _event: self._update_sequence_preset_choices())
        ttk.Label(top, text="Preset").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.sequence_preset_combo = ttk.Combobox(top, textvariable=self.sequence_preset_var, values=sequence_preset_names_for_category(self.sequence_preset_category_var.get()), state="readonly")
        self.sequence_preset_combo.grid(row=2, column=1, sticky="ew", padx=(8, 8), pady=(6, 0))
        self.sequence_preset_combo.bind("<<ComboboxSelected>>", lambda _event: self._update_sequence_preset_summary())
        ttk.Button(top, text="Load Preset", command=self._load_sequence_preset).grid(row=2, column=2, padx=(0, 6), pady=(6, 0))
        self.sequence_preset_summary = tk.Text(top, height=7, wrap="word")
        self.sequence_preset_summary.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(6, 0))

        split = ttk.Panedwindow(frame, orient="horizontal")
        split.grid(row=1, column=0, sticky="nsew")

        left = ttk.Frame(split, padding=6)
        center = ttk.Frame(split, padding=6)
        right = ttk.Frame(split, padding=6)
        split.add(left, weight=1)
        split.add(center, weight=3)
        split.add(right, weight=2)

        left.columnconfigure(0, weight=1)
        left.rowconfigure(3, weight=1)
        ttk.Label(left, text="Loaded contact map").grid(row=0, column=0, sticky="w")
        ttk.Label(left, textvariable=self.sequence_contact_map_var).grid(row=1, column=0, sticky="w", pady=(0, 8))
        ttk.Label(left, text="Available states").grid(row=2, column=0, sticky="w")
        self.sequence_state_listbox = tk.Listbox(left, exportselection=False, height=18)
        self.sequence_state_listbox.grid(row=3, column=0, sticky="nsew")
        self.sequence_state_listbox.bind("<<ListboxSelect>>", lambda _event: self._on_sequence_state_list_select())
        left_buttons = ttk.Frame(left)
        left_buttons.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(left_buttons, text="Add State To Sequence", command=self._add_selected_contact_map_state_to_sequence).pack(fill="x")
        ttk.Button(left_buttons, text="New Empty Sequence", command=self._new_empty_sequence).pack(fill="x", pady=(6, 0))

        center.columnconfigure(0, weight=1)
        center.rowconfigure(0, weight=1)
        sequence_columns = (
            "index",
            "step_name",
            "state_name",
            "excitation_mode",
            "source",
            "current",
            "frequency_hz",
            "harmonic",
            "measure_channels",
            "m1_spec",
            "m2_spec",
            "m3_spec",
            "outputs",
            "settle_s",
            "repeats",
            "matrix_policy",
            "tags",
            "reciprocal_step_of",
            "relay_channels",
        )
        self.sequence_tree = ttk.Treeview(center, columns=sequence_columns, show="headings", height=16)
        for column, heading, width in [
            ("index", "#", 40),
            ("step_name", "Step", 120),
            ("state_name", "State", 120),
            ("excitation_mode", "Mode", 70),
            ("source", "Source", 60),
            ("current", "Current", 90),
            ("frequency_hz", "Freq", 80),
            ("harmonic", "Harm", 60),
            ("measure_channels", "Measure", 100),
            ("m1_spec", "M1", 150),
            ("m2_spec", "M2", 150),
            ("m3_spec", "M3", 150),
            ("outputs", "Outputs", 130),
            ("settle_s", "Settle", 70),
            ("repeats", "Repeats", 70),
            ("matrix_policy", "Matrix", 70),
            ("tags", "Tags", 120),
            ("reciprocal_step_of", "Reciprocal", 100),
            ("relay_channels", "Relays", 120),
        ]:
            self.sequence_tree.heading(column, text=heading)
            self.sequence_tree.column(column, width=width, anchor="w")
        self.sequence_tree.grid(row=0, column=0, sticky="nsew")
        self.sequence_tree.bind("<<TreeviewSelect>>", lambda _event: self._on_sequence_tree_select())
        tree_scroll = ttk.Scrollbar(center, orient="vertical", command=self.sequence_tree.yview)
        tree_scroll.grid(row=0, column=1, sticky="ns")
        self.sequence_tree.configure(yscrollcommand=tree_scroll.set)
        center_buttons = ttk.Frame(center)
        center_buttons.grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Button(center_buttons, text="Move Up", command=self._move_sequence_step_up).pack(side="left")
        ttk.Button(center_buttons, text="Move Down", command=self._move_sequence_step_down).pack(side="left", padx=(6, 0))
        ttk.Button(center_buttons, text="Duplicate", command=self._duplicate_sequence_step).pack(side="left", padx=(6, 0))
        ttk.Button(center_buttons, text="Remove", command=self._remove_sequence_step).pack(side="left", padx=(6, 0))

        right.columnconfigure(1, weight=1)
        row = 0
        ttk.Label(right, text="Sequence name").grid(row=row, column=0, sticky="w")
        ttk.Entry(right, textvariable=self.sequence_name_var).grid(row=row, column=1, sticky="ew", padx=(8, 0))
        row += 1
        ttk.Label(right, text="Description").grid(row=row, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(right, textvariable=self.sequence_description_var).grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=(6, 0))
        row += 1
        ttk.Checkbutton(right, text="Expert mode", variable=self.sequence_expert_mode_var).grid(row=row, column=1, sticky="w", pady=(6, 0))
        row += 1
        defaults_box = ttk.LabelFrame(right, text="Defaults", padding=8)
        defaults_box.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        defaults_box.columnconfigure(1, weight=1)
        self._sequence_add_editor_row(defaults_box, 0, "Mode", self.sequence_default_mode_var, combo=SEQUENCE_EXCITATION_MODES)
        self._sequence_add_editor_row(defaults_box, 1, "Source", self.sequence_default_source_var, combo=SEQUENCE_SOURCES)
        self._sequence_add_editor_row(defaults_box, 2, "Measure ch.", self.sequence_default_measure_channel_var, combo=SEQUENCE_MEASURE_CHANNELS)
        self._sequence_add_editor_row(defaults_box, 3, "Measure chs.", self.sequence_default_measure_channels_var)
        self._sequence_add_editor_row(defaults_box, 4, "DC current A", self.sequence_default_current_a_var)
        self._sequence_add_editor_row(defaults_box, 5, "AC current Arms", self.sequence_default_current_rms_a_var)
        self._sequence_add_editor_row(defaults_box, 6, "Frequency Hz", self.sequence_default_frequency_var)
        self._sequence_add_editor_row(defaults_box, 7, "Harmonic", self.sequence_default_harmonic_var)
        self._sequence_add_editor_row(defaults_box, 8, "Settle s", self.sequence_default_settle_var)
        self._sequence_add_editor_row(defaults_box, 9, "Repeats", self.sequence_default_repeats_var)
        ttk.Checkbutton(defaults_box, text="Lock-in", variable=self.sequence_default_lockin_var).grid(row=10, column=1, sticky="w", pady=(6, 0))
        row += 1
        step_box = ttk.LabelFrame(right, text="Selected Step Editor", padding=8)
        step_box.grid(row=row, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        step_box.columnconfigure(1, weight=1)
        self._sequence_add_editor_row(step_box, 0, "Step name", self.sequence_step_name_var)
        self._sequence_add_editor_row(step_box, 1, "State", self.sequence_step_state_var)
        self._sequence_add_editor_row(step_box, 2, "Mode", self.sequence_step_mode_var, combo=SEQUENCE_EXCITATION_MODES, callback=lambda _event=None: self._update_sequence_step_mode_fields())
        self._sequence_add_editor_row(step_box, 3, "Source", self.sequence_step_source_var, combo=SEQUENCE_SOURCES)
        self._sequence_add_editor_row(step_box, 4, "Measure ch.", self.sequence_step_measure_channel_var, combo=SEQUENCE_MEASURE_CHANNELS)
        self._sequence_add_editor_row(step_box, 5, "Measure chs.", self.sequence_step_measure_channels_var)
        self.sequence_step_current_a_entry = self._sequence_add_editor_row(step_box, 6, "DC current A", self.sequence_step_current_a_var)
        self.sequence_step_current_rms_entry = self._sequence_add_editor_row(step_box, 7, "AC current Arms", self.sequence_step_current_rms_a_var)
        self.sequence_step_frequency_entry = self._sequence_add_editor_row(step_box, 8, "Frequency Hz", self.sequence_step_frequency_var)
        self.sequence_step_harmonic_entry = self._sequence_add_editor_row(step_box, 9, "Harmonic", self.sequence_step_harmonic_var)
        self.sequence_step_bias_entry = self._sequence_add_editor_row(step_box, 10, "Bias polarity", self.sequence_step_bias_polarity_var)
        self._sequence_add_editor_row(step_box, 11, "Settle s", self.sequence_step_settle_var)
        self._sequence_add_editor_row(step_box, 12, "Repeats", self.sequence_step_repeats_var)
        self._sequence_add_editor_row(step_box, 13, "Measure kind", self.sequence_step_measure_kind_var)
        self._sequence_add_editor_row(step_box, 14, "Tags", self.sequence_step_tags_var)
        self._sequence_add_editor_row(step_box, 15, "Reciprocal of", self.sequence_step_reciprocal_var)
        self._sequence_add_editor_row(step_box, 16, "Outputs", self.sequence_step_outputs_var)
        self._sequence_add_editor_row(step_box, 17, "Matrix policy", self.sequence_step_matrix_policy_var, combo=["apply_state", "none"])
        spec_row = 18
        for offset, channel in enumerate(["M1", "M2", "M3"]):
            base_row = spec_row + offset
            ttk.Label(step_box, text=f"{channel} spec").grid(row=base_row, column=0, sticky="w", pady=3)
            channel_frame = ttk.Frame(step_box)
            channel_frame.grid(row=base_row, column=1, sticky="ew", padx=(8, 0), pady=3)
            ttk.Entry(channel_frame, textvariable=self.sequence_measure_spec_mode_vars[channel], width=10).pack(side="left")
            ttk.Entry(channel_frame, textvariable=self.sequence_measure_spec_harmonic_vars[channel], width=6).pack(side="left", padx=(4, 0))
            ttk.Entry(channel_frame, textvariable=self.sequence_measure_spec_readout_vars[channel], width=8).pack(side="left", padx=(4, 0))
            ttk.Entry(channel_frame, textvariable=self.sequence_measure_spec_transform_vars[channel], width=18).pack(side="left", padx=(4, 0))
            ttk.Entry(channel_frame, textvariable=self.sequence_measure_spec_output_vars[channel], width=18).pack(side="left", padx=(4, 0))
        ttk.Label(step_box, text="Relay channels").grid(row=21, column=0, sticky="w", pady=(6, 0))
        ttk.Label(step_box, textvariable=self.sequence_step_relay_var).grid(row=21, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
        step_buttons = ttk.Frame(step_box)
        step_buttons.grid(row=22, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(step_buttons, text="Apply Step Changes", command=self._apply_sequence_step_editor).pack(side="left")
        ttk.Button(step_buttons, text="Validate Sequence", command=self._validate_sequence_from_gui).pack(side="left", padx=(6, 0))
        ttk.Button(step_buttons, text="Dry-run Preview", command=self._dry_run_sequence_from_gui).pack(side="left", padx=(6, 0))
        ttk.Button(step_buttons, text="Run Sequence", command=self._run_sequence_from_gui).pack(side="left", padx=(6, 0))

        bottom = ttk.Panedwindow(frame, orient="horizontal")
        bottom.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        bottom_left = ttk.Frame(bottom, padding=6)
        bottom_right = ttk.Frame(bottom, padding=6)
        bottom_left.columnconfigure(0, weight=1)
        bottom_left.rowconfigure(1, weight=1)
        bottom_right.columnconfigure(0, weight=1)
        bottom_right.rowconfigure(1, weight=1)
        ttk.Label(bottom_left, textvariable=self.sequence_validation_var).grid(row=0, column=0, sticky="w")
        self.sequence_validation_text = tk.Text(bottom_left, height=10, wrap="word")
        self.sequence_validation_text.grid(row=1, column=0, sticky="nsew")
        ttk.Label(bottom_right, text="Dry-run preview").grid(row=0, column=0, sticky="w")
        self.sequence_preview_text = tk.Text(bottom_right, height=10, wrap="word")
        self.sequence_preview_text.grid(row=1, column=0, sticky="nsew")
        bottom.add(bottom_left, weight=1)
        bottom.add(bottom_right, weight=2)

    def _sequence_add_editor_row(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar, combo: list[str] | None = None, callback=None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        if combo is None:
            widget = ttk.Entry(parent, textvariable=variable)
        else:
            widget = ttk.Combobox(parent, textvariable=variable, values=combo, state="readonly")
            if callback:
                widget.bind("<<ComboboxSelected>>", callback)
        widget.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=3)
        return widget

    def _build_live_tab(self) -> None:
        frame = self.live_tab
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(3, weight=1)

        top = ttk.Frame(frame)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Button(top, text="Connect", command=self._connect_live_instruments).pack(side="left")
        ttk.Button(top, text="Disconnect", command=self._disconnect_live_instruments).pack(side="left", padx=(8, 0))
        ttk.Button(top, text="Refresh Status", command=self._refresh_live_status).pack(side="left", padx=(8, 0))
        ttk.Label(top, textvariable=self.live_connection_var).pack(side="right")

        summary = ttk.LabelFrame(frame, text="Live Summary", padding=10)
        summary.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        ttk.Label(summary, textvariable=self.live_temperature_var).grid(row=0, column=0, sticky="w")
        ttk.Label(summary, textvariable=self.live_field_var).grid(row=1, column=0, sticky="w")
        ttk.Label(summary, textvariable=self.live_sources_var).grid(row=2, column=0, sticky="w")
        ttk.Label(summary, textvariable=self.live_relays_var).grid(row=3, column=0, sticky="w")
        ttk.Label(summary, textvariable=self.live_measures_var).grid(row=4, column=0, sticky="w", pady=(6, 0))
        ttk.Label(summary, textvariable=self.live_environment_mode_var).grid(row=5, column=0, sticky="w", pady=(6, 0))
        ttk.Label(summary, textvariable=self.live_environment_action_var).grid(row=6, column=0, sticky="w", pady=(6, 0))
        ttk.Label(summary, textvariable=self.live_acquisition_var).grid(row=7, column=0, sticky="w", pady=(6, 0))
        ttk.Label(summary, textvariable=self.live_last_reading_var).grid(row=8, column=0, sticky="w", pady=(6, 0))

        environment = ttk.LabelFrame(frame, text="Environment Controls", padding=10)
        environment.grid(row=2, column=0, sticky="nsew", padx=(0, 8), pady=(10, 8))
        environment.columnconfigure(1, weight=1)
        ttk.Label(environment, text="Temperature target (K)").grid(row=0, column=0, sticky="w")
        env_temp_entry = ttk.Entry(environment, textvariable=self.live_env_temperature_target_var)
        env_temp_entry.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ttk.Label(environment, text="Field target (T)").grid(row=1, column=0, sticky="w", pady=(6, 0))
        env_field_entry = ttk.Entry(environment, textvariable=self.live_env_field_target_var)
        env_field_entry.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(6, 0))
        ttk.Label(environment, text="Ramp rate (/min)").grid(row=2, column=0, sticky="w", pady=(6, 0))
        env_rate_entry = ttk.Entry(environment, textvariable=self.live_env_ramp_rate_var)
        env_rate_entry.grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=(6, 0))
        env_buttons_1 = ttk.Frame(environment)
        env_buttons_1.grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))
        env_set_temp = ttk.Button(env_buttons_1, text="Set T", command=self._live_set_temperature)
        env_set_temp.pack(side="left")
        env_set_field = ttk.Button(env_buttons_1, text="Set B", command=self._live_set_field)
        env_set_field.pack(side="left", padx=(8, 0))
        env_buttons_2 = ttk.Frame(environment)
        env_buttons_2.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
        env_start_field = ttk.Button(env_buttons_2, text="Start B Ramp", command=self._live_start_field_ramp)
        env_start_field.pack(side="left")
        env_stop_field = ttk.Button(env_buttons_2, text="Stop B Ramp", command=self._live_stop_field_ramp)
        env_stop_field.pack(side="left", padx=(8, 0))
        env_buttons_3 = ttk.Frame(environment)
        env_buttons_3.grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))
        env_start_temp = ttk.Button(env_buttons_3, text="Start T Ramp", command=self._live_start_temperature_ramp)
        env_start_temp.pack(side="left")
        env_stop_temp = ttk.Button(env_buttons_3, text="Stop T Ramp", command=self._live_stop_temperature_ramp)
        env_stop_temp.pack(side="left", padx=(8, 0))
        self.live_environment_widgets = [
            env_temp_entry,
            env_field_entry,
            env_rate_entry,
            env_set_temp,
            env_set_field,
            env_start_field,
            env_stop_field,
            env_start_temp,
            env_stop_temp,
        ]

        manual = ttk.LabelFrame(frame, text="Manual Controls", padding=10)
        manual.grid(row=2, column=1, sticky="nsew", pady=(10, 8))
        manual.columnconfigure(1, weight=1)
        ttk.Label(manual, text="State").grid(row=0, column=0, sticky="w")
        self.manual_state_combo = ttk.Combobox(manual, textvariable=self.state_name_var, state="readonly")
        self.manual_state_combo.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ttk.Label(manual, text="Source").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(manual, textvariable=self.manual_source_var, values=["S1", "S2", "S3"], state="readonly").grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(6, 0))
        ttk.Label(manual, text="Source mode").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(manual, textvariable=self.manual_source_mode_var, values=MANUAL_SOURCE_MODES, state="readonly").grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=(6, 0))
        ttk.Label(manual, text="Setpoint").grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(manual, textvariable=self.manual_setpoint_var).grid(row=3, column=1, sticky="ew", padx=(8, 0), pady=(6, 0))
        ttk.Label(manual, text="Frequency (Hz)").grid(row=4, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(manual, textvariable=self.manual_frequency_var).grid(row=4, column=1, sticky="ew", padx=(8, 0), pady=(6, 0))
        ttk.Label(manual, text="Harmonic").grid(row=5, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(manual, textvariable=self.manual_harmonic_var).grid(row=5, column=1, sticky="ew", padx=(8, 0), pady=(6, 0))
        ttk.Label(manual, text="Measure ch.").grid(row=6, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(manual, textvariable=self.manual_measure_channel_var, values=["M1", "M2", "M3"], state="readonly").grid(row=6, column=1, sticky="ew", padx=(8, 0), pady=(6, 0))
        ttk.Label(manual, text="M1 mode").grid(row=7, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(manual, textvariable=self.measure_mode_vars["M1"], values=MEASURE_ACQUISITION_MODES, state="readonly").grid(row=7, column=1, sticky="ew", padx=(8, 0), pady=(6, 0))
        ttk.Label(manual, text="M2 mode").grid(row=8, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(manual, textvariable=self.measure_mode_vars["M2"], values=MEASURE_ACQUISITION_MODES, state="readonly").grid(row=8, column=1, sticky="ew", padx=(8, 0), pady=(6, 0))
        ttk.Label(manual, text="M3 mode").grid(row=9, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(manual, textvariable=self.measure_mode_vars["M3"], values=MEASURE_ACQUISITION_MODES, state="readonly").grid(row=9, column=1, sticky="ew", padx=(8, 0), pady=(6, 0))
        ttk.Label(manual, text="M1 harmonic").grid(row=7, column=2, sticky="w", padx=(12, 0), pady=(6, 0))
        ttk.Entry(manual, textvariable=self.measure_harmonic_vars["M1"], width=8).grid(row=7, column=3, sticky="w", padx=(8, 0), pady=(6, 0))
        ttk.Label(manual, text="M2 harmonic").grid(row=8, column=2, sticky="w", padx=(12, 0), pady=(6, 0))
        ttk.Entry(manual, textvariable=self.measure_harmonic_vars["M2"], width=8).grid(row=8, column=3, sticky="w", padx=(8, 0), pady=(6, 0))
        ttk.Label(manual, text="M3 harmonic").grid(row=9, column=2, sticky="w", padx=(12, 0), pady=(6, 0))
        ttk.Entry(manual, textvariable=self.measure_harmonic_vars["M3"], width=8).grid(row=9, column=3, sticky="w", padx=(8, 0), pady=(6, 0))
        ttk.Label(manual, text="M1 NPLC").grid(row=7, column=4, sticky="w", padx=(12, 0), pady=(6, 0))
        ttk.Entry(manual, textvariable=self.measure_nplc_vars["M1"], width=8).grid(row=7, column=5, sticky="w", padx=(8, 0), pady=(6, 0))
        ttk.Label(manual, text="M2 NPLC").grid(row=8, column=4, sticky="w", padx=(12, 0), pady=(6, 0))
        ttk.Entry(manual, textvariable=self.measure_nplc_vars["M2"], width=8).grid(row=8, column=5, sticky="w", padx=(8, 0), pady=(6, 0))
        ttk.Label(manual, text="M3 NPLC").grid(row=9, column=4, sticky="w", padx=(12, 0), pady=(6, 0))
        ttk.Entry(manual, textvariable=self.measure_nplc_vars["M3"], width=8).grid(row=9, column=5, sticky="w", padx=(8, 0), pady=(6, 0))
        ttk.Label(manual, text="M1 tau (s)").grid(row=7, column=6, sticky="w", padx=(12, 0), pady=(6, 0))
        ttk.Entry(manual, textvariable=self.measure_time_constant_vars["M1"], width=8).grid(row=7, column=7, sticky="w", padx=(8, 0), pady=(6, 0))
        ttk.Label(manual, text="M2 tau (s)").grid(row=8, column=6, sticky="w", padx=(12, 0), pady=(6, 0))
        ttk.Entry(manual, textvariable=self.measure_time_constant_vars["M2"], width=8).grid(row=8, column=7, sticky="w", padx=(8, 0), pady=(6, 0))
        ttk.Label(manual, text="M3 tau (s)").grid(row=9, column=6, sticky="w", padx=(12, 0), pady=(6, 0))
        ttk.Entry(manual, textvariable=self.measure_time_constant_vars["M3"], width=8).grid(row=9, column=7, sticky="w", padx=(8, 0), pady=(6, 0))
        ttk.Label(manual, text="M1 rolloff").grid(row=7, column=8, sticky="w", padx=(12, 0), pady=(6, 0))
        ttk.Combobox(manual, textvariable=self.measure_rolloff_vars["M1"], values=LOCKIN_ROLLOFFS, state="readonly", width=6).grid(row=7, column=9, sticky="w", padx=(8, 0), pady=(6, 0))
        ttk.Label(manual, text="M2 rolloff").grid(row=8, column=8, sticky="w", padx=(12, 0), pady=(6, 0))
        ttk.Combobox(manual, textvariable=self.measure_rolloff_vars["M2"], values=LOCKIN_ROLLOFFS, state="readonly", width=6).grid(row=8, column=9, sticky="w", padx=(8, 0), pady=(6, 0))
        ttk.Label(manual, text="M3 rolloff").grid(row=9, column=8, sticky="w", padx=(12, 0), pady=(6, 0))
        ttk.Combobox(manual, textvariable=self.measure_rolloff_vars["M3"], values=LOCKIN_ROLLOFFS, state="readonly", width=6).grid(row=9, column=9, sticky="w", padx=(8, 0), pady=(6, 0))
        manual_buttons = ttk.Frame(manual)
        manual_buttons.grid(row=10, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Button(manual_buttons, text="Apply Measure Settings", command=self._manual_apply_measure_modes).pack(side="left")
        ttk.Button(manual_buttons, text="Configure Source", command=self._manual_configure_source).pack(side="left")
        ttk.Button(manual_buttons, text="Enable Source", command=self._manual_enable_source).pack(side="left", padx=(8, 0))
        ttk.Button(manual_buttons, text="Disable Source", command=self._manual_disable_selected_source).pack(side="left", padx=(8, 0))
        ttk.Button(manual_buttons, text="Safe Switch Then Enable", command=self._guided_safe_switch_then_enable).pack(side="left", padx=(8, 0))
        guided_buttons = ttk.Frame(manual)
        guided_buttons.grid(row=11, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(guided_buttons, text="Safe Switch Then Read", command=self._guided_safe_switch_then_read).pack(side="left")
        ttk.Button(guided_buttons, text="Disable + Open All", command=self._guided_disable_and_open_all).pack(side="left", padx=(8, 0))
        ttk.Button(manual_buttons, text="Apply State", command=self._manual_apply_state).pack(side="left")
        ttk.Button(manual_buttons, text="Open All", command=self._manual_open_all).pack(side="left", padx=(8, 0))
        ttk.Button(manual_buttons, text="Disable Sources", command=self._manual_disable_sources).pack(side="left", padx=(8, 0))
        ttk.Button(manual_buttons, text="Read M1", command=lambda: self._manual_read_measure("M1")).pack(side="left", padx=(8, 0))
        ttk.Button(manual_buttons, text="Read M2", command=lambda: self._manual_read_measure("M2")).pack(side="left", padx=(8, 0))

        acquisition = ttk.LabelFrame(frame, text="Continuous Live Acquisition", padding=10)
        acquisition.grid(row=3, column=0, columnspan=2, sticky="nsew", pady=(10, 8))
        acquisition.columnconfigure(5, weight=1)
        ttk.Label(acquisition, text="Measure").grid(row=0, column=0, sticky="w")
        ttk.Combobox(acquisition, textvariable=self.live_measure_channel_var, values=["M1", "M2", "M3"], state="readonly", width=8).grid(row=0, column=1, sticky="w", padx=(8, 10))
        ttk.Label(acquisition, text="Interval (s)").grid(row=0, column=2, sticky="w")
        ttk.Entry(acquisition, textvariable=self.live_interval_var, width=10).grid(row=0, column=3, sticky="w", padx=(8, 10))
        ttk.Label(acquisition, text="Buffer").grid(row=0, column=4, sticky="w")
        ttk.Entry(acquisition, textvariable=self.live_buffer_var, width=10).grid(row=0, column=5, sticky="w", padx=(8, 10))
        ttk.Button(acquisition, text="Start Live", command=self._start_live_acquisition).grid(row=0, column=6, sticky="e")
        ttk.Button(acquisition, text="Stop Live", command=self._stop_live_acquisition).grid(row=0, column=7, sticky="e", padx=(8, 0))
        ttk.Label(acquisition, text="X").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(acquisition, textvariable=self.live_plot_x_var, values=LIVE_PLOT_X_COLUMNS, state="readonly", width=12).grid(row=1, column=1, sticky="w", padx=(8, 10), pady=(8, 0))
        ttk.Label(acquisition, text="Y").grid(row=1, column=2, sticky="w", pady=(8, 0))
        ttk.Combobox(acquisition, textvariable=self.live_plot_y_var, values=LIVE_PLOT_Y_COLUMNS, state="readonly", width=12).grid(row=1, column=3, sticky="w", padx=(8, 10), pady=(8, 0))
        ttk.Button(acquisition, text="Refresh Plot", command=self._draw_live_plot).grid(row=1, column=6, sticky="e", pady=(8, 0))
        self.live_plot_canvas = tk.Canvas(acquisition, background="#eef4ea", height=260)
        self.live_plot_canvas.grid(row=2, column=0, columnspan=8, sticky="nsew", pady=(10, 0))
        acquisition.rowconfigure(2, weight=1)

        ttk.Label(frame, text="Live JSON snapshot").grid(row=4, column=0, columnspan=2, sticky="w")
        self.live_snapshot_text = tk.Text(frame, wrap="word", height=18)
        self.live_snapshot_text.grid(row=5, column=0, columnspan=2, sticky="nsew")
        frame.rowconfigure(5, weight=1)

    def _build_log_tab(self) -> None:
        frame = self.log_tab
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        controls = ttk.Frame(frame)
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(controls, text="Clear Log", command=self._clear_log).pack(side="left")
        self.log_widget = tk.Text(frame, height=28, wrap="word")
        self.log_widget.grid(row=1, column=0, sticky="nsew")

    def _build_preview_tab(self) -> None:
        frame = self.preview_tab
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(1, weight=1)
        frame.rowconfigure(2, weight=1)

        top = ttk.Frame(frame)
        top.grid(row=0, column=0, columnspan=3, sticky="ew")
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="CSV preview").grid(row=0, column=0, sticky="w")
        self.preview_combo = ttk.Combobox(top, textvariable=self.preview_file_var, state="readonly")
        self.preview_combo.grid(row=0, column=1, sticky="ew", padx=(8, 8), pady=(0, 8))
        self.preview_combo.bind("<<ComboboxSelected>>", lambda _event: self._show_preview())
        ttk.Button(top, text="Open Latest", command=self._select_latest_preview).grid(row=0, column=2, sticky="ew")

        plot_controls = ttk.Frame(frame)
        plot_controls.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        ttk.Label(plot_controls, text="X").pack(side="left")
        self.plot_x_combo = ttk.Combobox(plot_controls, textvariable=self.plot_x_var, state="readonly", width=18)
        self.plot_x_combo.pack(side="left", padx=(6, 10))
        ttk.Label(plot_controls, text="Y").pack(side="left")
        self.plot_y_combo = ttk.Combobox(plot_controls, textvariable=self.plot_y_var, state="readonly", width=18)
        self.plot_y_combo.pack(side="left", padx=(6, 10))
        ttk.Button(plot_controls, text="Plot", command=self._draw_plot).pack(side="left")

        split = ttk.Panedwindow(frame, orient="horizontal")
        split.grid(row=2, column=0, columnspan=3, sticky="nsew")
        text_frame = ttk.Frame(split)
        plot_frame = ttk.Frame(split)
        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)
        plot_frame.rowconfigure(0, weight=1)
        plot_frame.columnconfigure(0, weight=1)
        self.preview_text = tk.Text(text_frame, wrap="none", height=30)
        self.preview_text.grid(row=0, column=0, sticky="nsew")
        self.plot_canvas = tk.Canvas(plot_frame, background="#f7f4eb", height=360)
        self.plot_canvas.grid(row=0, column=0, sticky="nsew")
        split.add(text_frame, weight=2)
        split.add(plot_frame, weight=1)

    def _add_path_row(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar, browse_command) -> int:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=(8, 8), pady=3)
        ttk.Button(parent, text="Browse", command=browse_command).grid(row=row, column=2, sticky="ew", pady=3)
        return row + 1

    def _add_entry_row(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar, widget_key: str | None = None) -> int:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=3)
        if widget_key:
            self.setup_widgets[widget_key] = entry
        return row + 1

    def _add_combo_row(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        values: list[str],
        callback=None,
        widget_key: str | None = None,
    ) -> int:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        combo = ttk.Combobox(parent, textvariable=variable, values=values, state="readonly")
        combo.grid(row=row, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=3)
        if callback:
            combo.bind("<<ComboboxSelected>>", lambda _event: callback())
        if widget_key:
            self.setup_widgets[widget_key] = combo
        return row + 1

    def _browse_file(self, variable: tk.StringVar) -> None:
        current = Path(variable.get()).expanduser()
        filename = filedialog.askopenfilename(initialdir=str(current.parent if current.parent.exists() else Path.cwd()))
        if filename:
            variable.set(filename)

    def _browse_config(self) -> None:
        self._browse_file(self.config_var)
        self._sync_environment_mode_from_config()

    def _browse_contact_map(self) -> None:
        self._browse_file(self.contact_map_var)
        self._load_contact_map()

    def _browse_sequence_file(self) -> None:
        self._browse_file(self.sequence_path_var)

    def _sync_environment_mode_from_config(self) -> None:
        try:
            config = load_yaml(self.config_var.get())
            self.environment_mode_var.set(environment_mode_from_config(config))
            self._update_environment_mode_state()
        except Exception:
            pass

    def _browse_dir(self, variable: tk.StringVar) -> None:
        current = Path(variable.get()).expanduser()
        dirname = filedialog.askdirectory(initialdir=str(current if current.exists() else Path.cwd()))
        if dirname:
            variable.set(dirname)
            self._refresh_preview()

    def _load_contact_map(self) -> None:
        try:
            self.contact_map = ContactMap.from_yaml(self.contact_map_var.get())
            state_names = list(self.contact_map.states.keys())
            self.state_combo["values"] = state_names
            self.manual_state_combo["values"] = state_names
            self.state_listbox.delete(0, "end")
            for state_name in state_names:
                self.state_listbox.insert("end", state_name)
            recommended = recommended_states_for_protocol(self.protocol_var.get(), self.contact_map)
            if recommended:
                self.state_name_var.set(recommended[0])
            self._select_recommended_states()
            self._update_state_details()
            self.sequence_contact_map_var.set(self.contact_map_var.get())
            self._refresh_sequence_available_states()
            self._refresh_sequence_table()
            self._update_sequence_relay_preview()
            self.log_queue.put(f"Loaded contact map: {self.contact_map.name}\n")
            self.status_var.set(f"Loaded {self.contact_map.name}")
        except Exception as exc:
            self.contact_map = None
            self.log_queue.put(f"Failed to load contact map: {exc}\n")
            self.status_var.set("Contact map error")

    def _refresh_sequence_available_states(self) -> None:
        if not hasattr(self, "sequence_state_listbox"):
            return
        self.sequence_state_listbox.delete(0, "end")
        if self.contact_map is None:
            return
        for state_name in self.contact_map.states:
            self.sequence_state_listbox.insert("end", state_name)
        if self.contact_map.states and not self.sequence_selected_state_var.get():
            self.sequence_selected_state_var.set(next(iter(self.contact_map.states)))

    def _new_empty_sequence(self) -> None:
        self.sequence_data = empty_sequence_data(self.contact_map_var.get())
        self.sequence_file_path = None
        self.sequence_path_var.set("")
        self.sequence_validation_ok = False
        self.sequence_step_index = None
        self._sync_sequence_vars_from_data()
        if hasattr(self, "sequence_validation_text"):
            self.sequence_validation_text.delete("1.0", "end")
            self.sequence_validation_text.insert("end", "New empty sequence created.\n")
        if hasattr(self, "sequence_preview_text"):
            self.sequence_preview_text.delete("1.0", "end")
            self.sequence_preview_text.insert("end", "Dry-run preview will appear here.\n")

    def _load_sequence_preset(self) -> None:
        preset_path = SEQUENCE_PRESET_PATHS.get(self.sequence_preset_var.get())
        if not preset_path:
            self.status_var.set("Unknown preset")
            return
        try:
            self.sequence_data = measurement_sequence_to_dict(load_measurement_sequence(preset_path))
            self.sequence_file_path = Path(preset_path)
            self.sequence_path_var.set(preset_path)
            self.sequence_validation_ok = False
            self._sync_sequence_vars_from_data()
            self._update_sequence_preset_summary()
            self.status_var.set(f"Loaded preset {self.sequence_preset_var.get()}")
        except Exception as exc:
            self.sequence_validation_text.delete("1.0", "end")
            self.sequence_validation_text.insert("end", f"Preset load failed: {exc}\n")
            self.status_var.set("Preset load failed")

    def _update_sequence_preset_choices(self) -> None:
        preset_names = sequence_preset_names_for_category(self.sequence_preset_category_var.get())
        self.sequence_preset_combo["values"] = preset_names
        if preset_names and self.sequence_preset_var.get() not in preset_names:
            self.sequence_preset_var.set(preset_names[0])
        self._update_sequence_preset_summary()

    def _update_sequence_preset_summary(self) -> None:
        if not hasattr(self, "sequence_preset_summary"):
            return
        self.sequence_preset_summary.delete("1.0", "end")
        preset_name = self.sequence_preset_var.get()
        if not preset_name:
            return
        try:
            self.sequence_preset_summary.insert("end", format_sequence_preset_summary(preset_name))
        except Exception as exc:
            self.sequence_preset_summary.insert("end", f"Preset summary unavailable: {exc}")

    def _sync_sequence_vars_from_data(self) -> None:
        data = self.sequence_data
        defaults = data.get("defaults", {})
        self.sequence_name_var.set(str(data.get("name", "")))
        self.sequence_description_var.set(str(data.get("description", "")))
        self.sequence_contact_map_var.set(str(data.get("contact_map", self.contact_map_var.get()) or self.contact_map_var.get()))
        self.sequence_expert_mode_var.set(bool(data.get("expert_mode", False)))
        self.sequence_default_mode_var.set(str(defaults.get("source_mode") or defaults.get("excitation_mode") or "ac"))
        self.sequence_default_source_var.set(str(defaults.get("source", "S1") or ""))
        self.sequence_default_measure_channel_var.set(str(defaults.get("measure_channel", "") or ""))
        self.sequence_default_measure_channels_var.set(",".join(defaults.get("measure_channels", []) or []))
        self.sequence_default_current_a_var.set("" if defaults.get("current_a") is None else str(defaults.get("current_a")))
        self.sequence_default_current_rms_a_var.set("" if defaults.get("current_rms_a") is None else str(defaults.get("current_rms_a")))
        self.sequence_default_frequency_var.set("" if defaults.get("frequency_hz") is None else str(defaults.get("frequency_hz")))
        self.sequence_default_harmonic_var.set("" if defaults.get("harmonic") is None else str(defaults.get("harmonic")))
        self.sequence_default_settle_var.set(str(defaults.get("settle_s", 0.0)))
        self.sequence_default_repeats_var.set(str(defaults.get("repeats", 1)))
        self.sequence_default_lockin_var.set(bool(defaults.get("lockin", True)))
        self._refresh_sequence_table()
        self._load_sequence_step_into_editor(None)

    def _sync_sequence_data_from_vars(self) -> None:
        defaults: dict[str, object] = {
            "excitation_mode": self.sequence_default_mode_var.get(),
            "source": self.sequence_default_source_var.get() or None,
            "measure_channel": self.sequence_default_measure_channel_var.get() or None,
            "measure_channels": parse_csv_list(self.sequence_default_measure_channels_var.get()),
            "current_a": float(self.sequence_default_current_a_var.get()) if self.sequence_default_current_a_var.get().strip() else None,
            "current_rms_a": float(self.sequence_default_current_rms_a_var.get()) if self.sequence_default_current_rms_a_var.get().strip() else None,
            "frequency_hz": float(self.sequence_default_frequency_var.get()) if self.sequence_default_frequency_var.get().strip() else None,
            "harmonic": int(self.sequence_default_harmonic_var.get()) if self.sequence_default_harmonic_var.get().strip() else None,
            "settle_s": float(self.sequence_default_settle_var.get() or 0.0),
            "repeats": int(self.sequence_default_repeats_var.get() or 1),
            "matrix_policy": "apply_state",
            "lockin": bool(self.sequence_default_lockin_var.get()),
            "metadata": {},
        }
        if defaults["measure_channels"] == []:
            defaults["measure_channels"] = None
        self.sequence_data["name"] = self.sequence_name_var.get().strip() or "sequence"
        self.sequence_data["description"] = self.sequence_description_var.get().strip()
        self.sequence_data["contact_map"] = self.sequence_contact_map_var.get().strip() or self.contact_map_var.get()
        self.sequence_data["expert_mode"] = bool(self.sequence_expert_mode_var.get())
        self.sequence_data["defaults"] = defaults

    def _refresh_sequence_table(self) -> None:
        if not hasattr(self, "sequence_tree"):
            return
        self.sequence_tree.delete(*self.sequence_tree.get_children())
        defaults = self.sequence_data.get("defaults", {})
        for index, step in enumerate(self.sequence_data.get("steps", [])):
            row = sequence_step_table_row(step, defaults, self.contact_map, index)
            iid = str(index)
            self.sequence_tree.insert("", "end", iid=iid, values=tuple(row[key] for key in self.sequence_tree["columns"]))

    def _on_sequence_state_list_select(self) -> None:
        selection = self.sequence_state_listbox.curselection()
        if not selection:
            return
        self.sequence_selected_state_var.set(self.sequence_state_listbox.get(selection[0]))

    def _add_selected_contact_map_state_to_sequence(self) -> None:
        if self.contact_map is None:
            self.status_var.set("Load contact map first")
            return
        selection = self.sequence_state_listbox.curselection()
        if not selection:
            self.status_var.set("Select a contact-map state first")
            return
        state_name = self.sequence_state_listbox.get(selection[0])
        step = {
            "name": f"step_{len(self.sequence_data['steps']) + 1}",
            "state": state_name,
            "excitation_mode": None,
            "source": None,
            "measure_channel": None,
            "measure_channels": [],
            "current_a": None,
            "current_rms_a": None,
            "frequency_hz": None,
            "harmonic": None,
            "bias_polarity": 1,
            "settle_s": None,
            "repeats": None,
            "measure_kind": "",
            "tags": [],
            "reciprocal_step_of": "",
            "outputs": {},
            "measure_specs": None,
            "matrix_policy": None,
            "metadata": {},
        }
        self.sequence_data["steps"].append(step)
        self.sequence_step_index = len(self.sequence_data["steps"]) - 1
        self._refresh_sequence_table()
        self._load_sequence_step_into_editor(self.sequence_step_index)

    def _selected_sequence_tree_index(self) -> int | None:
        if not hasattr(self, "sequence_tree"):
            return None
        selection = self.sequence_tree.selection()
        if not selection:
            return None
        return int(selection[0])

    def _on_sequence_tree_select(self) -> None:
        self.sequence_step_index = self._selected_sequence_tree_index()
        self._load_sequence_step_into_editor(self.sequence_step_index)

    def _load_sequence_step_into_editor(self, index: int | None) -> None:
        steps = self.sequence_data.get("steps", [])
        if index is None or index < 0 or index >= len(steps):
            self.sequence_step_index = None
            self.sequence_step_name_var.set("")
            self.sequence_step_state_var.set("")
            self.sequence_step_mode_var.set(self.sequence_default_mode_var.get() or "ac")
            self.sequence_step_source_var.set("")
            self.sequence_step_measure_channel_var.set("")
            self.sequence_step_measure_channels_var.set("")
            self.sequence_step_current_a_var.set("")
            self.sequence_step_current_rms_a_var.set("")
            self.sequence_step_frequency_var.set("")
            self.sequence_step_harmonic_var.set("")
            self.sequence_step_bias_polarity_var.set("1")
            self.sequence_step_settle_var.set("")
            self.sequence_step_repeats_var.set("")
            self.sequence_step_measure_kind_var.set("")
            self.sequence_step_tags_var.set("")
            self.sequence_step_reciprocal_var.set("")
            self.sequence_step_outputs_var.set("")
            self.sequence_step_matrix_policy_var.set("apply_state")
            for channel in ["M1", "M2", "M3"]:
                self.sequence_measure_spec_mode_vars[channel].set("")
                self.sequence_measure_spec_harmonic_vars[channel].set("")
                self.sequence_measure_spec_readout_vars[channel].set("")
                self.sequence_measure_spec_transform_vars[channel].set("")
                self.sequence_measure_spec_output_vars[channel].set("")
            self.sequence_step_relay_var.set("")
            self._update_sequence_step_mode_fields()
            return
        step = steps[index]
        self.sequence_step_name_var.set(str(step.get("name", "")))
        self.sequence_step_state_var.set(str(step.get("state", "")))
        self.sequence_step_mode_var.set(str(step.get("source_mode") or step.get("excitation_mode") or self.sequence_default_mode_var.get() or "ac"))
        self.sequence_step_source_var.set(str(step.get("source") or ""))
        self.sequence_step_measure_channel_var.set(str(step.get("measure_channel") or ""))
        self.sequence_step_measure_channels_var.set(",".join(step.get("measure_channels", []) or []))
        self.sequence_step_current_a_var.set("" if step.get("current_a") is None else str(step.get("current_a")))
        self.sequence_step_current_rms_a_var.set("" if step.get("current_rms_a") is None else str(step.get("current_rms_a")))
        self.sequence_step_frequency_var.set("" if step.get("frequency_hz") is None else str(step.get("frequency_hz")))
        self.sequence_step_harmonic_var.set("" if step.get("harmonic") is None else str(step.get("harmonic")))
        self.sequence_step_bias_polarity_var.set(str(step.get("bias_polarity", 1)))
        self.sequence_step_settle_var.set("" if step.get("settle_s") is None else str(step.get("settle_s")))
        self.sequence_step_repeats_var.set("" if step.get("repeats") is None else str(step.get("repeats")))
        self.sequence_step_measure_kind_var.set(str(step.get("measure_kind", "")))
        self.sequence_step_tags_var.set(",".join(step.get("tags", []) or []))
        self.sequence_step_reciprocal_var.set(str(step.get("reciprocal_step_of") or step.get("reciprocal_of") or ""))
        self.sequence_step_outputs_var.set(format_outputs_mapping(step.get("outputs") if isinstance(step.get("outputs"), dict) else None))
        self.sequence_step_matrix_policy_var.set(
            str(step.get("matrix_policy") or self.sequence_data.get("defaults", {}).get("matrix_policy") or "apply_state")
        )
        measure_specs = step.get("measure_specs", {})
        for channel in ["M1", "M2", "M3"]:
            spec = measure_specs.get(channel, {}) if isinstance(measure_specs, dict) else {}
            self.sequence_measure_spec_mode_vars[channel].set(str(spec.get("measure_mode", "")))
            self.sequence_measure_spec_harmonic_vars[channel].set("" if spec.get("harmonic") is None else str(spec.get("harmonic")))
            self.sequence_measure_spec_readout_vars[channel].set(str(spec.get("readout", "")))
            self.sequence_measure_spec_transform_vars[channel].set(str(spec.get("transform", "")))
            self.sequence_measure_spec_output_vars[channel].set(str(spec.get("output", "")))
        self._update_sequence_relay_preview()
        self._update_sequence_step_mode_fields()

    def _update_sequence_relay_preview(self) -> None:
        self.sequence_step_relay_var.set(relay_channels_for_state(self.contact_map, self.sequence_step_state_var.get()))

    def _update_sequence_step_mode_fields(self) -> None:
        mode = self.sequence_step_mode_var.get().strip().lower() or self.sequence_default_mode_var.get().strip().lower()
        is_dc = mode == "dc"
        self.sequence_step_current_a_entry.configure(state="normal" if is_dc else "disabled")
        self.sequence_step_bias_entry.configure(state="normal" if is_dc else "disabled")
        self.sequence_step_current_rms_entry.configure(state="disabled" if is_dc else "normal")
        self.sequence_step_frequency_entry.configure(state="disabled" if is_dc else "normal")
        self.sequence_step_harmonic_entry.configure(state="disabled" if is_dc else "normal")
        if not is_dc:
            self.sequence_step_bias_polarity_var.set("1")
        self._update_sequence_relay_preview()

    def _apply_sequence_step_editor(self) -> None:
        if self.sequence_step_index is None:
            self.status_var.set("Select a sequence step first")
            return
        try:
            mode = self.sequence_step_mode_var.get().strip().lower() or None
            if mode == "ac" and self.sequence_step_bias_polarity_var.get().strip() not in {"", "1"}:
                raise ValueError("bias_polarity is not allowed in AC mode")
            step = self.sequence_data["steps"][self.sequence_step_index]
            step["name"] = self.sequence_step_name_var.get().strip()
            step["state"] = self.sequence_step_state_var.get().strip()
            step["excitation_mode"] = mode
            step["source"] = self.sequence_step_source_var.get().strip() or None
            step["measure_channel"] = self.sequence_step_measure_channel_var.get().strip() or None
            step["measure_channels"] = parse_csv_list(self.sequence_step_measure_channels_var.get())
            step["current_a"] = float(self.sequence_step_current_a_var.get()) if mode == "dc" and self.sequence_step_current_a_var.get().strip() else None
            step["current_rms_a"] = float(self.sequence_step_current_rms_a_var.get()) if mode != "dc" and self.sequence_step_current_rms_a_var.get().strip() else None
            step["frequency_hz"] = float(self.sequence_step_frequency_var.get()) if mode != "dc" and self.sequence_step_frequency_var.get().strip() else None
            step["harmonic"] = int(self.sequence_step_harmonic_var.get()) if mode != "dc" and self.sequence_step_harmonic_var.get().strip() else None
            step["bias_polarity"] = int(self.sequence_step_bias_polarity_var.get()) if mode == "dc" and self.sequence_step_bias_polarity_var.get().strip() else None
            step["settle_s"] = float(self.sequence_step_settle_var.get()) if self.sequence_step_settle_var.get().strip() else None
            step["repeats"] = int(self.sequence_step_repeats_var.get()) if self.sequence_step_repeats_var.get().strip() else None
            step["measure_kind"] = self.sequence_step_measure_kind_var.get().strip() or None
            step["tags"] = parse_csv_list(self.sequence_step_tags_var.get())
            step["reciprocal_step_of"] = self.sequence_step_reciprocal_var.get().strip() or None
            step["reciprocal_of"] = None
            step["outputs"] = parse_csv_mapping(self.sequence_step_outputs_var.get()) if self.sequence_step_outputs_var.get().strip() else {}
            step["matrix_policy"] = self.sequence_step_matrix_policy_var.get().strip() or None
            measure_specs: dict[str, dict[str, object]] = {}
            for channel in ["M1", "M2", "M3"]:
                mode_value = self.sequence_measure_spec_mode_vars[channel].get().strip()
                harmonic_value = self.sequence_measure_spec_harmonic_vars[channel].get().strip()
                readout_value = self.sequence_measure_spec_readout_vars[channel].get().strip()
                transform_value = self.sequence_measure_spec_transform_vars[channel].get().strip()
                output_value = self.sequence_measure_spec_output_vars[channel].get().strip()
                if not any([mode_value, harmonic_value, readout_value, transform_value, output_value]):
                    continue
                measure_specs[channel] = {
                    "measure_mode": mode_value or ("dc" if mode == "dc" else "lockin"),
                    "harmonic": int(harmonic_value) if harmonic_value else None,
                    "readout": readout_value or ("value" if mode == "dc" else "x"),
                    "transform": transform_value or None,
                    "output": output_value or None,
                }
            step["measure_specs"] = measure_specs or None
            self._refresh_sequence_table()
            self._update_sequence_relay_preview()
            self.sequence_validation_ok = False
            self.sequence_validation_var.set("Sequence changed; validate again")
            self.status_var.set("Sequence step updated")
        except Exception as exc:
            self.sequence_validation_text.delete("1.0", "end")
            self.sequence_validation_text.insert("end", f"Step update failed: {exc}\n")
            self.status_var.set("Sequence edit error")

    def _remove_sequence_step(self) -> None:
        index = self._selected_sequence_tree_index()
        if index is None:
            return
        self.sequence_data["steps"].pop(index)
        self.sequence_step_index = None
        self._refresh_sequence_table()
        self._load_sequence_step_into_editor(None)

    def _duplicate_sequence_step(self) -> None:
        index = self._selected_sequence_tree_index()
        if index is None:
            return
        step = dict(self.sequence_data["steps"][index])
        step["name"] = f"{step.get('name', 'step')}_copy"
        step["tags"] = list(step.get("tags", []) or [])
        step["measure_channels"] = list(step.get("measure_channels", []) or [])
        step["outputs"] = dict(step.get("outputs", {}) or {})
        if isinstance(step.get("measure_specs"), dict):
            step["measure_specs"] = {channel: dict(spec or {}) for channel, spec in step["measure_specs"].items()}
        self.sequence_data["steps"].insert(index + 1, step)
        self._refresh_sequence_table()

    def _move_sequence_step_up(self) -> None:
        index = self._selected_sequence_tree_index()
        if index is None or index == 0:
            return
        steps = self.sequence_data["steps"]
        steps[index - 1], steps[index] = steps[index], steps[index - 1]
        self._refresh_sequence_table()
        self.sequence_tree.selection_set(str(index - 1))
        self._on_sequence_tree_select()

    def _move_sequence_step_down(self) -> None:
        index = self._selected_sequence_tree_index()
        steps = self.sequence_data["steps"]
        if index is None or index >= len(steps) - 1:
            return
        steps[index + 1], steps[index] = steps[index], steps[index + 1]
        self._refresh_sequence_table()
        self.sequence_tree.selection_set(str(index + 1))
        self._on_sequence_tree_select()

    def _build_sequence_model_from_gui(self):
        self._sync_sequence_data_from_vars()
        model = measurement_sequence_from_dict(self.sequence_data, path=self.sequence_file_path or self.sequence_path_var.get() or None)
        if self.contact_map is None:
            raise ValueError("Load a contact map before validating a sequence")
        resolved = validate_measurement_sequence(model, self.contact_map)
        return model, resolved

    def _validate_sequence_from_gui(self) -> None:
        self.sequence_validation_text.delete("1.0", "end")
        try:
            model, resolved = self._build_sequence_model_from_gui()
            self.sequence_validation_ok = True
            self.sequence_validation_var.set(f"Sequence valid: {len(resolved)} steps")
            self.sequence_validation_text.insert(
                "end",
                f"Sequence '{model.name}' is valid.\nRelay switching preview remains on Matrix7709.apply_state().\n",
            )
        except Exception as exc:
            self.sequence_validation_ok = False
            self.sequence_validation_var.set("Sequence validation failed")
            self.sequence_validation_text.insert("end", f"{exc}\n")

    def _dry_run_sequence_from_gui(self) -> None:
        self.sequence_preview_text.delete("1.0", "end")
        try:
            model, resolved = self._build_sequence_model_from_gui()
            runner = SequenceRunner(sequence=model, contact_map=self.contact_map, resolved_steps=resolved, dry_run=True)
            preview = runner.format_preview()
            self.sequence_preview_text.insert(
                "end",
                "Dry-run only. Actual relay switching still uses Matrix7709.apply_state().\n\n",
            )
            self.sequence_preview_text.insert("end", preview)
            self.sequence_validation_ok = True
            self.sequence_validation_var.set(f"Dry-run ready: {len(resolved)} steps")
        except Exception as exc:
            self.sequence_validation_ok = False
            self.sequence_validation_var.set("Dry-run failed")
            self.sequence_preview_text.insert("end", f"{exc}\n")

    def _load_sequence_yaml(self) -> None:
        if not self.sequence_path_var.get():
            self._browse_sequence_file()
        path = self.sequence_path_var.get().strip()
        if not path:
            return
        try:
            payload = yaml.safe_load(Path(path).read_text())
            model = measurement_sequence_from_dict(payload, path=path)
            self.sequence_data = measurement_sequence_to_dict(model)
            self.sequence_file_path = Path(path)
            self.sequence_path_var.set(path)
            self.sequence_validation_ok = False
            self._sync_sequence_vars_from_data()
            self.sequence_validation_text.delete("1.0", "end")
            self.sequence_validation_text.insert("end", f"Loaded sequence: {model.name}\n")
            self.status_var.set("Sequence loaded")
        except Exception as exc:
            self.sequence_validation_text.delete("1.0", "end")
            self.sequence_validation_text.insert("end", f"Failed to load sequence: {exc}\n")
            self.status_var.set("Sequence load failed")

    def _save_sequence_yaml(self) -> None:
        try:
            self._sync_sequence_data_from_vars()
            path = self.sequence_path_var.get().strip()
            if not path:
                current = Path(self.contact_map_var.get()).parent.parent / "sequences"
                filename = filedialog.asksaveasfilename(
                    initialdir=str(current if current.exists() else Path.cwd()),
                    defaultextension=".yaml",
                    filetypes=[("YAML files", "*.yaml"), ("All files", "*.*")],
                )
                if not filename:
                    return
                path = filename
                self.sequence_path_var.set(path)
            Path(path).write_text(yaml.safe_dump(self.sequence_data, sort_keys=False))
            self.sequence_file_path = Path(path)
            self.status_var.set("Sequence saved")
            self.sequence_validation_text.delete("1.0", "end")
            self.sequence_validation_text.insert("end", f"Saved sequence YAML to {path}\n")
        except Exception as exc:
            self.sequence_validation_text.delete("1.0", "end")
            self.sequence_validation_text.insert("end", f"Failed to save sequence: {exc}\n")
            self.status_var.set("Sequence save failed")

    def _selected_states_from_listbox(self) -> list[str]:
        return [self.state_listbox.get(index) for index in self.state_listbox.curselection()]

    def _select_recommended_states(self) -> None:
        if self.contact_map is None:
            return
        recommended = recommended_states_for_protocol(self.protocol_var.get(), self.contact_map)
        self._clear_state_selection()
        for idx, state_name in enumerate(self.state_listbox.get(0, "end")):
            if state_name in recommended:
                self.state_listbox.selection_set(idx)
        if recommended:
            self.state_name_var.set(recommended[0])
        self._update_state_details()

    def _select_all_states(self) -> None:
        self.state_listbox.selection_set(0, "end")
        self._update_state_details()

    def _clear_state_selection(self) -> None:
        self.state_listbox.selection_clear(0, "end")
        self._update_state_details()

    def _update_protocol_state(self) -> None:
        protocol = self.protocol_var.get()
        is_multi = protocol_is_multi_state(protocol)
        self.state_combo.configure(state="disabled" if is_multi else "readonly")
        self.state_listbox.configure(state="normal" if is_multi else "disabled")
        self._update_protocol_specific_options(protocol)
        if self.contact_map is not None:
            recommended = recommended_states_for_protocol(protocol, self.contact_map)
            if recommended:
                self.state_name_var.set(recommended[0])
            self._select_recommended_states()
        self._update_state_details()

    def _set_protocol_option_visibility(self, widget: ttk.Checkbutton | None, visible: bool) -> None:
        if widget is None:
            return
        is_visible = widget.winfo_manager() == "pack"
        if visible and not is_visible:
            widget.pack(side="left", padx=(12, 0))
        elif not visible and is_visible:
            widget.pack_forget()

    def _update_protocol_specific_options(self, protocol: str) -> None:
        show_reciprocity = protocol == "vdp_hall"
        show_anisotropy = protocol == "vdp"
        if not show_reciprocity:
            self.include_reciprocity_var.set(False)
        if not show_anisotropy:
            self.include_anisotropy_var.set(False)
        self._set_protocol_option_visibility(self.include_reciprocity_check, show_reciprocity)
        self._set_protocol_option_visibility(self.include_anisotropy_check, show_anisotropy)

    def _update_mode_state(self) -> None:
        self._update_environment_mode_state()

    def _set_widget_state(self, widget_key: str, enabled: bool) -> None:
        widget = self.setup_widgets.get(widget_key)
        if widget is None:
            return
        if isinstance(widget, ttk.Combobox):
            widget.configure(state="readonly" if enabled else "disabled")
            return
        widget.configure(state="normal" if enabled else "disabled")

    def _update_environment_mode_state(self) -> None:
        env_mode = self.environment_mode_var.get()
        mode = self.mode_var.get()
        environment_is_controlled = environment_mode_supports_control(env_mode)
        supports_stream_ramp = environment_is_controlled

        if mode == "stream-ramp" and not supports_stream_ramp:
            self.mode_var.set("stable")
            mode = "stable"

        for key in ["temperatures", "fields", "ramp_quantity", "ramp_target", "ramp_rate"]:
            self._set_widget_state(key, environment_is_controlled)

        self._set_widget_state("mode", True)
        if mode == "stream-ramp":
            self._set_widget_state("ramp_quantity", True)
            self._set_widget_state("ramp_target", True)
            self._set_widget_state("ramp_rate", True)

        if env_mode == "integrated":
            if mode == "stream-ramp":
                message = "Ready for software-controlled streaming ramp"
            elif mode == "stream-observe":
                message = "Ready to observe an externally controlled ramp"
            else:
                message = "Ready"
        elif env_mode == "async-poll":
            message = "Async poll: read-only Teslatron mode; use stream-observe for external ramps"
        else:
            message = "Standalone: local T/B context only; external environment control is disabled"
        self.status_var.set(message)
        self._update_live_environment_controls_state()

    def _set_live_environment_widgets_enabled(self, enabled: bool) -> None:
        for widget in self.live_environment_widgets:
            if isinstance(widget, ttk.Button):
                widget.configure(state="normal" if enabled else "disabled")
            else:
                widget.configure(state="normal" if enabled else "disabled")

    def _update_live_environment_controls_state(self) -> None:
        connected = self.live_environment is not None
        enabled = connected and environment_mode_supports_control(self.environment_mode_var.get())
        self._set_live_environment_widgets_enabled(enabled)
        if not connected:
            self.live_environment_action_var.set("Env control idle")
        elif enabled:
            self.live_environment_action_var.set("Env control available")
        else:
            self.live_environment_action_var.set("Env control disabled in this mode")

    def _format_state_details(self, state_name: str) -> str:
        if self.contact_map is None:
            return "No contact map loaded."
        state = self.contact_map.get_state(state_name)
        lines = [
            f"State: {state_name}",
            f"Type: {state.get('type')}",
            f"Group: {state.get('group')}",
            f"Current: {state.get('current')}",
            f"Voltage: {state.get('voltage')}",
            f"Voltage longitudinal: {state.get('voltage_longitudinal')}",
            f"Voltage transverse: {state.get('voltage_transverse')}",
            f"Reciprocal: {state.get('reciprocal')}",
            f"Reverse current: {state.get('reverse_current')}",
            f"Relay channels: {state.get('relay_channels')}",
            "Bindings:",
        ]
        bindings = self.contact_map.describe_bindings(state_name)
        if not bindings:
            lines.append("  none")
        else:
            for contact, attached in bindings.items():
                lines.append(f"  {contact}: {attached}")
        return "\n".join(lines)

    def _update_state_details(self) -> None:
        self.state_details.delete("1.0", "end")
        if self.contact_map is None:
            self.state_details.insert("end", "No contact map loaded.")
            return
        if protocol_is_multi_state(self.protocol_var.get()):
            selected = self._selected_states_from_listbox()
            if not selected:
                self.state_details.insert("end", "No states selected.")
                return
            self.state_details.insert("end", "\n\n".join(self._format_state_details(name) for name in selected))
            return
        state_name = self.state_name_var.get()
        self.state_details.insert("end", self._format_state_details(state_name) if state_name else "No state selected.")

    def _append_log(self, text: str) -> None:
        self.log_widget.insert("end", text)
        self.log_widget.see("end")

    def _clear_log(self) -> None:
        self.log_widget.delete("1.0", "end")

    def _drain_logs(self) -> None:
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self._append_log(message)
        self.root.after(100, self._drain_logs)

    def _build_args(self) -> argparse.Namespace:
        selected_states = ",".join(self._selected_states_from_listbox()) if protocol_is_multi_state(self.protocol_var.get()) else None
        state_name = None if protocol_is_multi_state(self.protocol_var.get()) else (self.state_name_var.get() or None)
        return build_run_namespace(
            config=self.config_var.get(),
            contact_map=self.contact_map_var.get(),
            mock=self.mock_var.get(),
            sample_id=self.sample_id_var.get() or None,
            output=self.output_var.get() or None,
            protocol=self.protocol_var.get(),
            temperatures=self.temperatures_var.get() or None,
            fields=self.fields_var.get() or None,
            current=parse_required_float(self.current_var.get(), "Current", positive=True),
            frequency=parse_required_float(self.frequency_var.get(), "Frequency", positive=True),
            harmonic=parse_required_int(self.harmonic_var.get(), "Harmonic", minimum=1),
            settle=parse_required_float(self.settle_var.get(), "Settle", non_negative=True),
            mode=self.mode_var.get(),
            ramp_quantity=self.ramp_quantity_var.get(),
            ramp_target=parse_required_float(self.ramp_target_var.get(), "Ramp target") if self.ramp_target_var.get() else None,
            ramp_rate=parse_required_float(self.ramp_rate_var.get(), "Ramp rate", positive=True) if self.ramp_rate_var.get() else None,
            stream_samples=parse_required_int(self.stream_samples_var.get(), "Stream samples", minimum=1),
            stream_interval=parse_required_float(self.stream_interval_var.get(), "Stream interval", positive=True),
            dry_run=self.dry_run_var.get(),
            state_name=state_name,
            selected_states=selected_states,
            environment_mode=self.environment_mode_var.get(),
            include_reciprocity=self.include_reciprocity_var.get(),
            include_anisotropy=self.include_anisotropy_var.get(),
        )

    def _build_sequence_args(self, sequence_path: str) -> argparse.Namespace:
        return build_run_namespace(
            config=self.config_var.get(),
            contact_map=self.contact_map_var.get(),
            mock=self.mock_var.get(),
            sample_id=self.sample_id_var.get() or None,
            output=self.output_var.get() or None,
            protocol=None,
            temperatures=self.temperatures_var.get() or None,
            fields=self.fields_var.get() or None,
            current=1e-5,
            frequency=13.7,
            harmonic=1,
            settle=parse_required_float(self.settle_var.get(), "Settle", non_negative=True),
            mode=self.mode_var.get(),
            ramp_quantity=self.ramp_quantity_var.get(),
            ramp_target=parse_required_float(self.ramp_target_var.get(), "Ramp target") if self.ramp_target_var.get() else None,
            ramp_rate=parse_required_float(self.ramp_rate_var.get(), "Ramp rate", positive=True) if self.ramp_rate_var.get() else None,
            stream_samples=parse_required_int(self.stream_samples_var.get(), "Stream samples", minimum=1),
            stream_interval=parse_required_float(self.stream_interval_var.get(), "Stream interval", positive=True),
            dry_run=self.dry_run_var.get(),
            state_name=None,
            selected_states=None,
            environment_mode=self.environment_mode_var.get(),
            include_reciprocity=False,
            include_anisotropy=False,
            sequence=sequence_path,
        )

    def _run_measurement(self) -> None:
        if self.worker and self.worker.is_alive():
            self.status_var.set("Measurement already running")
            return
        try:
            args = self._build_args()
        except Exception as exc:
            self.log_queue.put(f"Invalid GUI input: {exc}\n")
            self.status_var.set("Invalid input")
            return

        def target() -> None:
            buffer = io.StringIO()
            try:
                self.log_queue.put("Starting measurement...\n")
                self.status_var.set("Running")
                with redirect_stdout(buffer), redirect_stderr(buffer):
                    exit_code = run_command(args)
                output = buffer.getvalue()
                if output:
                    self.log_queue.put(output)
                self.log_queue.put(f"Measurement finished with exit code {exit_code}\n")
                self.status_var.set("Completed")
                self.root.after(0, self._refresh_preview)
                self.root.after(0, lambda: self.notebook.select(self.preview_tab))
            except Exception:
                self.log_queue.put(buffer.getvalue())
                self.log_queue.put(traceback.format_exc())
                self.status_var.set("Failed")

        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()

    def _run_sequence_from_gui(self) -> None:
        if self.worker and self.worker.is_alive():
            self.status_var.set("Measurement already running")
            return
        try:
            model, _resolved = self._build_sequence_model_from_gui()
        except Exception as exc:
            self.sequence_validation_text.delete("1.0", "end")
            self.sequence_validation_text.insert("end", f"Cannot run sequence: {exc}\n")
            self.status_var.set("Sequence invalid")
            return
        if not self.sequence_validation_ok:
            self.sequence_validation_text.insert("end", "Validate the sequence before running.\n")
            self.status_var.set("Validate sequence first")
            return
        temporary_dir = Path(tempfile.mkdtemp(prefix="electrical-sequence-gui-"))
        temporary_path = temporary_dir / f"{model.name or 'sequence'}.yaml"
        temporary_path.write_text(yaml.safe_dump(measurement_sequence_to_dict(model), sort_keys=False))
        args = self._build_sequence_args(str(temporary_path))

        def target() -> None:
            buffer = io.StringIO()
            try:
                self.log_queue.put("Starting GUI sequence run...\n")
                self.status_var.set("Sequence running")
                with redirect_stdout(buffer), redirect_stderr(buffer):
                    exit_code = run_command(args)
                output = buffer.getvalue()
                if output:
                    self.log_queue.put(output)
                self.log_queue.put(f"Sequence finished with exit code {exit_code}\n")
                self.status_var.set("Sequence completed")
                self.root.after(0, self._refresh_preview)
                self.root.after(0, lambda: self.notebook.select(self.preview_tab))
            except Exception:
                self.log_queue.put(buffer.getvalue())
                self.log_queue.put(traceback.format_exc())
                self.status_var.set("Sequence failed")

        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()

    def _connect_live_instruments(self) -> None:
        if self.contact_map is None:
            self.status_var.set("Load contact map first")
            return
        try:
            self.live_config = apply_environment_mode_override(load_yaml(self.config_var.get()), self.environment_mode_var.get())
            self.live_m81, self.live_matrix, self.live_environment = build_instruments(
                self.live_config,
                self.contact_map,
                mock=self.mock_var.get(),
            )
            self._sync_measure_modes_from_backend()
            self.live_connection_var.set("Connected")
            self.status_var.set("Live instruments connected")
            self._update_live_environment_controls_state()
            self._refresh_live_status()
        except Exception as exc:
            self.live_connection_var.set("Connection failed")
            self.log_queue.put(f"Live connect failed: {exc}\n")
            self.status_var.set("Live connection failed")

    def _disconnect_live_instruments(self) -> None:
        self._stop_live_acquisition()
        self.live_m81 = None
        self.live_matrix = None
        self.live_environment = None
        self.live_connection_var.set("Disconnected")
        self.live_environment_mode_var.set("Env mode: --")
        self._update_live_environment_controls_state()
        self.status_var.set("Disconnected")

    def _live_snapshot(self) -> dict:
        snapshot: dict[str, object] = {"connected": bool(self.live_m81 and self.live_matrix and self.live_environment)}
        if self.live_environment and hasattr(self.live_environment, "status_snapshot"):
            snapshot["environment"] = self.live_environment.status_snapshot()
        else:
            snapshot["environment"] = {
                "temperature_k": self.live_environment.read_temperature() if self.live_environment else None,
                "field_t": self.live_environment.read_field() if self.live_environment else None,
            }
        if self.live_m81 and hasattr(self.live_m81, "status_snapshot"):
            snapshot["m81"] = self.live_m81.status_snapshot()
        if self.live_matrix and hasattr(self.live_matrix, "status_snapshot"):
            snapshot["matrix"] = self.live_matrix.status_snapshot()
        return snapshot

    def _refresh_live_status(self) -> None:
        if not (self.live_m81 and self.live_matrix and self.live_environment):
            self.live_temperature_var.set("T: --")
            self.live_field_var.set("B: --")
            self.live_sources_var.set("Sources: disconnected")
            self.live_relays_var.set("Relays: --")
            self.live_measures_var.set("Measures: --")
            self.live_environment_mode_var.set("Env mode: --")
            self.live_snapshot_text.delete("1.0", "end")
            self.live_snapshot_text.insert("end", "No live connection.")
            self._update_live_environment_controls_state()
            self._draw_live_plot()
            return
        snapshot = self._live_snapshot()
        env = snapshot.get("environment", {}) if isinstance(snapshot, dict) else {}
        m81 = snapshot.get("m81", {}) if isinstance(snapshot, dict) else {}
        matrix = snapshot.get("matrix", {}) if isinstance(snapshot, dict) else {}
        temperature = env.get("temperature_k") if isinstance(env, dict) else None
        field = env.get("field_t") if isinstance(env, dict) else None
        self.live_temperature_var.set(f"T: {temperature:.3f} K" if isinstance(temperature, (int, float)) else "T: --")
        self.live_field_var.set(f"B: {field:.5f} T" if isinstance(field, (int, float)) else "B: --")
        sources = []
        if isinstance(m81, dict):
            for name, details in m81.get("sources", {}).items():
                if details.get("enabled"):
                    sources.append(name)
        self.live_sources_var.set(f"Sources: {', '.join(sources) if sources else 'all disabled'}")
        relays = matrix.get("closed_channels") if isinstance(matrix, dict) else None
        self.live_relays_var.set(f"Relays: {relays}" if relays is not None else "Relays: --")
        measures = m81.get("measures") if isinstance(m81, dict) else None
        self.live_measures_var.set(format_measure_summary(measures if isinstance(measures, dict) else None))
        env_mode = env.get("mode") if isinstance(env, dict) else None
        control_enabled = env.get("control_enabled") if isinstance(env, dict) else None
        if env_mode:
            label = f"Env mode: {env_mode}"
            if control_enabled is False:
                label += " (read-only/local)"
            self.live_environment_mode_var.set(label)
        else:
            self.live_environment_mode_var.set(f"Env mode: {self.environment_mode_var.get()}")
        self._update_live_environment_controls_state()
        self.live_snapshot_text.delete("1.0", "end")
        self.live_snapshot_text.insert("end", json.dumps(snapshot, indent=2, default=str))
        self._draw_live_plot()

    def _poll_live_status(self) -> None:
        if self.live_environment and hasattr(self.live_environment, "advance_time") and self.mode_var.get() in {"stream-ramp", "stream-observe"}:
            try:
                self.live_environment.advance_time(1.0)
            except Exception:
                pass
        try:
            self._refresh_live_status()
        finally:
            self.root.after(500, self._poll_live_status)

    def _manual_source_parameters(self) -> tuple[str, str, float, float, int, str]:
        return (
            self.manual_source_var.get(),
            self.manual_source_mode_var.get(),
            parse_required_float(self.manual_setpoint_var.get(), "Manual setpoint", positive=True),
            parse_required_float(self.manual_frequency_var.get(), "Manual frequency", positive=True),
            parse_required_int(self.manual_harmonic_var.get(), "Manual harmonic", minimum=1),
            self.manual_measure_channel_var.get(),
        )

    def _run_live_environment_command(self, label: str, action) -> None:
        if not self.live_environment:
            self.status_var.set("Connect live instruments first")
            return
        if not environment_mode_supports_control(self.environment_mode_var.get()):
            self.status_var.set("Environment control is disabled in this mode")
            return

        def target() -> None:
            try:
                self._set_stringvar_async(self.live_environment_action_var, f"Env action: {label}...")
                action()
                self.log_queue.put(f"Environment action completed: {label}\n")
                self._set_stringvar_async(self.live_environment_action_var, f"Env action completed: {label}")
                self._set_stringvar_async(self.status_var, f"Environment: {label}")
                self.root.after(0, self._refresh_live_status)
            except Exception:
                self.log_queue.put(traceback.format_exc())
                self._set_stringvar_async(self.live_environment_action_var, f"Env action failed: {label}")
                self._set_stringvar_async(self.status_var, "Environment command failed")

        threading.Thread(target=target, daemon=True).start()

    def _live_set_temperature(self) -> None:
        target_k = parse_required_float(self.live_env_temperature_target_var.get(), "Temperature target")
        self._run_live_environment_command(
            f"set T to {target_k} K",
            lambda: (
                self.live_environment.set_temperature(target_k),
                self.live_environment.wait_temperature_stable(target_k),
            ),
        )

    def _live_set_field(self) -> None:
        target_t = parse_required_float(self.live_env_field_target_var.get(), "Field target")
        self._run_live_environment_command(
            f"set B to {target_t} T",
            lambda: (
                self.live_environment.set_field(target_t),
                self.live_environment.wait_field_stable(target_t),
            ),
        )

    def _live_start_field_ramp(self) -> None:
        target_t = parse_required_float(self.live_env_field_target_var.get(), "Field target")
        rate_t_per_min = parse_required_float(self.live_env_ramp_rate_var.get(), "Ramp rate", positive=True)
        self._run_live_environment_command(
            f"start B ramp to {target_t} T",
            lambda: self.live_environment.start_field_ramp(target_t, rate_t_per_min),
        )

    def _live_stop_field_ramp(self) -> None:
        self._run_live_environment_command("stop B ramp", lambda: self.live_environment.stop_field_ramp())

    def _live_start_temperature_ramp(self) -> None:
        target_k = parse_required_float(self.live_env_temperature_target_var.get(), "Temperature target")
        rate_k_per_min = parse_required_float(self.live_env_ramp_rate_var.get(), "Ramp rate", positive=True)
        self._run_live_environment_command(
            f"start T ramp to {target_k} K",
            lambda: self.live_environment.start_temperature_ramp(target_k, rate_k_per_min),
        )

    def _live_stop_temperature_ramp(self) -> None:
        self._run_live_environment_command("stop T ramp", lambda: self.live_environment.stop_temperature_ramp())

    def _set_stringvar_async(self, variable: tk.StringVar, value: str) -> None:
        self.root.after(0, lambda: variable.set(value))

    def _sync_measure_modes_from_backend(self) -> None:
        if not self.live_m81:
            return
        for channel, variable in self.measure_mode_vars.items():
            settings = self.live_m81.get_measure_settings(channel) if hasattr(self.live_m81, "get_measure_settings") else {}
            preferred = settings.get("preferred_mode") if isinstance(settings, dict) else None
            variable.set(backend_measure_mode_to_ui(preferred))
            preferred_harmonic = settings.get("preferred_harmonic") if isinstance(settings, dict) else 1
            self.measure_harmonic_vars[channel].set(str(preferred_harmonic or 1))
            preferred_nplc = settings.get("preferred_nplc") if isinstance(settings, dict) else 1.0
            self.measure_nplc_vars[channel].set(str(preferred_nplc or 1.0))
            preferred_time_constant = settings.get("preferred_time_constant_s") if isinstance(settings, dict) else 0.3
            self.measure_time_constant_vars[channel].set(str(preferred_time_constant or 0.3))
            preferred_rolloff = settings.get("preferred_rolloff") if isinstance(settings, dict) else "R24"
            self.measure_rolloff_vars[channel].set(str(preferred_rolloff or "R24"))

    def _selected_measure_mode(self, measure_channel: str) -> str:
        variable = self.measure_mode_vars.get(measure_channel)
        return ui_measure_mode_to_backend(variable.get() if variable else "Auto")

    def _selected_measure_harmonic(self, measure_channel: str) -> int:
        variable = self.measure_harmonic_vars.get(measure_channel)
        return parse_optional_positive_int(variable.get() if variable else "1", f"{measure_channel} harmonic", default=1)

    def _selected_measure_nplc(self, measure_channel: str) -> float:
        variable = self.measure_nplc_vars.get(measure_channel)
        return parse_optional_positive_float(variable.get() if variable else "1.0", f"{measure_channel} NPLC", default=1.0)

    def _selected_measure_time_constant(self, measure_channel: str) -> float:
        variable = self.measure_time_constant_vars.get(measure_channel)
        return parse_optional_positive_float(variable.get() if variable else "0.3", f"{measure_channel} time constant", default=0.3)

    def _selected_measure_rolloff(self, measure_channel: str) -> str:
        variable = self.measure_rolloff_vars.get(measure_channel)
        return str(variable.get() if variable else "R24").strip().upper() or "R24"

    def _apply_measure_mode_to_channel(self, measure_channel: str) -> str:
        if not self.live_m81:
            raise ValueError("Connect live instruments first")
        requested_mode = self._selected_measure_mode(measure_channel)
        requested_harmonic = self._selected_measure_harmonic(measure_channel)
        requested_nplc = self._selected_measure_nplc(measure_channel)
        requested_time_constant = self._selected_measure_time_constant(measure_channel)
        requested_rolloff = self._selected_measure_rolloff(measure_channel)
        if hasattr(self.live_m81, "set_preferred_measure_mode"):
            self.live_m81.set_preferred_measure_mode(measure_channel, requested_mode)
        if hasattr(self.live_m81, "set_preferred_measure_harmonic"):
            self.live_m81.set_preferred_measure_harmonic(measure_channel, requested_harmonic)
        if hasattr(self.live_m81, "set_preferred_measure_nplc"):
            self.live_m81.set_preferred_measure_nplc(measure_channel, requested_nplc)
        if hasattr(self.live_m81, "set_preferred_measure_time_constant"):
            self.live_m81.set_preferred_measure_time_constant(measure_channel, requested_time_constant)
        if hasattr(self.live_m81, "set_preferred_measure_rolloff"):
            self.live_m81.set_preferred_measure_rolloff(measure_channel, requested_rolloff)
        resolved_mode = self.live_m81.resolve_measure_mode(measure_channel, requested_mode) if hasattr(self.live_m81, "resolve_measure_mode") else requested_mode
        if resolved_mode == "dc":
            self.live_m81.configure_dc_measure(measure_channel, nplc=requested_nplc)
        elif resolved_mode == "lockin":
            self.live_m81.configure_lockin_measure(
                measure_channel=measure_channel,
                harmonic=requested_harmonic,
                time_constant_s=requested_time_constant,
                reference_source=self.manual_source_var.get(),
                rolloff=requested_rolloff,
            )
        return resolved_mode

    def _manual_apply_measure_modes(self) -> None:
        if not self.live_m81:
            self.status_var.set("Connect live instruments first")
            return
        try:
            applied = [f"{channel}={self._apply_measure_mode_to_channel(channel)}" for channel in ["M1", "M2", "M3"]]
            self.log_queue.put(f"Applied measure modes: {', '.join(applied)}\n")
            self.status_var.set("Measure modes applied")
            self._refresh_live_status()
        except Exception:
            self.log_queue.put(traceback.format_exc())
            self.status_var.set("Measure mode setup failed")

    def _configure_manual_source_backend(self) -> tuple[str, str, float, float, int, str]:
        source, mode, setpoint, frequency_hz, harmonic, measure_channel = self._manual_source_parameters()
        if mode == "DC current":
            self.live_m81.configure_dc_current(source=source, current_a=setpoint)
        elif mode == "DC voltage":
            self.live_m81.configure_dc_voltage(source=source, voltage_v=setpoint)
        elif mode == "AC current":
            self.live_m81.configure_ac_current_lockin(
                source=source,
                current_rms_a=setpoint,
                frequency_hz=frequency_hz,
                measure_channels=[measure_channel],
                harmonic=harmonic,
                reference_source=source,
            )
        elif mode == "AC voltage":
            self.live_m81.configure_ac_voltage_lockin(
                source=source,
                voltage_rms_v=setpoint,
                frequency_hz=frequency_hz,
                measure_channels=[measure_channel],
                harmonic=harmonic,
                reference_source=source,
            )
        else:
            raise ValueError(f"Unsupported source mode: {mode}")
        requested_measure_mode = self._selected_measure_mode(measure_channel)
        requested_measure_harmonic = self._selected_measure_harmonic(measure_channel)
        requested_measure_nplc = self._selected_measure_nplc(measure_channel)
        requested_measure_time_constant = self._selected_measure_time_constant(measure_channel)
        requested_measure_rolloff = self._selected_measure_rolloff(measure_channel)
        if requested_measure_mode == "auto":
            requested_measure_mode = "dc" if mode.startswith("DC") else "lockin"
            if hasattr(self.live_m81, "set_preferred_measure_mode"):
                self.live_m81.set_preferred_measure_mode(measure_channel, "auto")
        else:
            if hasattr(self.live_m81, "set_preferred_measure_mode"):
                self.live_m81.set_preferred_measure_mode(measure_channel, requested_measure_mode)
        if hasattr(self.live_m81, "set_preferred_measure_harmonic"):
            self.live_m81.set_preferred_measure_harmonic(measure_channel, requested_measure_harmonic)
        if hasattr(self.live_m81, "set_preferred_measure_nplc"):
            self.live_m81.set_preferred_measure_nplc(measure_channel, requested_measure_nplc)
        if hasattr(self.live_m81, "set_preferred_measure_time_constant"):
            self.live_m81.set_preferred_measure_time_constant(measure_channel, requested_measure_time_constant)
        if hasattr(self.live_m81, "set_preferred_measure_rolloff"):
            self.live_m81.set_preferred_measure_rolloff(measure_channel, requested_measure_rolloff)
        if requested_measure_mode == "dc":
            self.live_m81.configure_dc_measure(measure_channel, nplc=requested_measure_nplc)
        else:
            self.live_m81.configure_lockin_measure(
                measure_channel=measure_channel,
                harmonic=requested_measure_harmonic,
                time_constant_s=requested_measure_time_constant,
                reference_source=source,
                rolloff=requested_measure_rolloff,
            )
        return source, mode, setpoint, frequency_hz, harmonic, measure_channel

    def _manual_configure_source(self) -> None:
        if not self.live_m81:
            self.status_var.set("Connect live instruments first")
            return
        try:
            source, mode, setpoint, frequency_hz, harmonic, measure_channel = self._configure_manual_source_backend()
            self.log_queue.put(
                f"Configured {source} in {mode} mode with setpoint={setpoint}, frequency={frequency_hz}, harmonic={harmonic}, measure={measure_channel}\n"
            )
            self.status_var.set(f"{source} configured")
            self._refresh_live_status()
        except Exception:
            self.log_queue.put(traceback.format_exc())
            self.status_var.set("Source configuration failed")

    def _manual_enable_source(self) -> None:
        if not self.live_m81:
            self.status_var.set("Connect live instruments first")
            return
        try:
            source = self.manual_source_var.get()
            self.live_m81.enable_source(source)
            self.log_queue.put(f"Enabled source {source}.\n")
            self.status_var.set(f"{source} enabled")
            self._refresh_live_status()
        except Exception:
            self.log_queue.put(traceback.format_exc())
            self.status_var.set("Enable source failed")

    def _manual_disable_selected_source(self) -> None:
        if not self.live_m81:
            self.status_var.set("Connect live instruments first")
            return
        try:
            source = self.manual_source_var.get()
            self.live_m81.disable_source(source)
            self.log_queue.put(f"Disabled source {source}.\n")
            self.status_var.set(f"{source} disabled")
            self._refresh_live_status()
        except Exception:
            self.log_queue.put(traceback.format_exc())
            self.status_var.set("Disable source failed")

    def _manual_apply_state(self) -> None:
        if not (self.live_m81 and self.live_matrix):
            self.status_var.set("Connect live instruments first")
            return
        state_name = self.state_name_var.get()
        if not state_name:
            self.status_var.set("Select a state first")
            return
        try:
            channels = self.live_matrix.apply_state(state_name)
            self.log_queue.put(f"Applied state {state_name}: {channels}\n")
            self._refresh_live_status()
        except Exception:
            self.log_queue.put(traceback.format_exc())
            self.status_var.set("Apply state failed")

    def _manual_open_all(self) -> None:
        if not self.live_matrix:
            self.status_var.set("Connect live instruments first")
            return
        try:
            if self.live_m81:
                self.live_m81.disable_all_sources()
            self.live_matrix.open_all()
            self.log_queue.put("Opened all relays.\n")
            self._refresh_live_status()
        except Exception:
            self.log_queue.put(traceback.format_exc())
            self.status_var.set("Open all failed")

    def _manual_disable_sources(self) -> None:
        if not self.live_m81:
            self.status_var.set("Connect live instruments first")
            return
        try:
            self.live_m81.disable_all_sources()
            self.log_queue.put("Disabled all sources.\n")
            self._refresh_live_status()
        except Exception:
            self.log_queue.put(traceback.format_exc())
            self.status_var.set("Disable sources failed")

    def _manual_read_measure(self, channel: str) -> None:
        if not self.live_m81:
            self.status_var.set("Connect live instruments first")
            return
        try:
            reading = self._read_live_measurement(channel)
            self.log_queue.put(f"{channel} reading: {json.dumps(reading, default=str)}\n")
            if "x" in reading:
                self.live_last_reading_var.set(f"Last reading: {channel} x={reading.get('x')} r={reading.get('r')}")
            else:
                self.live_last_reading_var.set(f"Last reading: {channel} value={reading.get('value')}")
        except Exception:
            self.log_queue.put(traceback.format_exc())
            self.status_var.set("Read failed")

    def _guided_safe_switch_then_enable(self) -> None:
        if not (self.live_m81 and self.live_matrix):
            self.status_var.set("Connect live instruments first")
            return
        state_name = self.state_name_var.get()
        if not state_name:
            self.status_var.set("Select a state first")
            return
        try:
            self.live_m81.disable_all_sources()
            channels = self.live_matrix.apply_state(state_name)
            self._configure_manual_source_backend()
            self.live_m81.enable_source(self.manual_source_var.get())
            self.log_queue.put(f"Guided sequence complete: switched to {state_name}, channels={channels}, enabled {self.manual_source_var.get()}.\n")
            self.status_var.set("Safe switch then enable complete")
            self._refresh_live_status()
        except Exception:
            self.log_queue.put(traceback.format_exc())
            self.status_var.set("Guided enable failed")

    def _guided_safe_switch_then_read(self) -> None:
        if not (self.live_m81 and self.live_matrix):
            self.status_var.set("Connect live instruments first")
            return
        try:
            self._guided_safe_switch_then_enable()
            self._manual_read_measure(self.manual_measure_channel_var.get())
            self.status_var.set("Safe switch then read complete")
        except Exception:
            self.log_queue.put(traceback.format_exc())
            self.status_var.set("Guided read failed")

    def _guided_disable_and_open_all(self) -> None:
        try:
            self._manual_disable_sources()
            self._manual_open_all()
            self.status_var.set("Sources disabled and relays opened")
        except Exception:
            self.log_queue.put(traceback.format_exc())
            self.status_var.set("Guided shutdown failed")

    def _read_live_measurement(self, channel: str) -> dict[str, object]:
        requested_mode = self._selected_measure_mode(channel)
        resolved_mode = self.live_m81.resolve_measure_mode(channel, requested_mode) if hasattr(self.live_m81, "resolve_measure_mode") else requested_mode
        if resolved_mode == "lockin":
            return dict(self.live_m81.read_lockin(channel))
        if resolved_mode == "dc":
            return dict(self.live_m81.read_dc(channel))
        try:
            return dict(self.live_m81.read_lockin(channel))
        except Exception:
            return dict(self.live_m81.read_dc(channel))

    def _live_measurement_plan(self, requested_channel: str) -> tuple[str, dict[str, str]]:
        channels = hallbar_live_channels(self.contact_map)
        if "vxx" in channels and "vxy" in channels:
            primary = requested_channel if requested_channel in channels.values() else channels["vxy"]
            return primary, channels
        return requested_channel, {}

    def _start_live_acquisition(self) -> None:
        if not (self.live_m81 and self.live_environment):
            self.status_var.set("Connect live instruments first")
            return
        if self.live_acquire_thread and self.live_acquire_thread.is_alive():
            self.status_var.set("Live acquisition already running")
            return
        try:
            interval_s = parse_required_float(self.live_interval_var.get(), "Live interval", positive=True)
            buffer_limit = parse_required_int(self.live_buffer_var.get(), "Live buffer", minimum=2)
            measure_channel, extra_channels = self._live_measurement_plan(self.live_measure_channel_var.get())
        except Exception as exc:
            self.log_queue.put(f"Invalid live acquisition settings: {exc}\n")
            self.status_var.set("Invalid live settings")
            return

        self.live_acquire_stop.clear()
        self.live_acquire_started_at = time.monotonic()
        with self.live_data_lock:
            self.live_data = []

        def target() -> None:
            sample_index = 0
            while not self.live_acquire_stop.is_set():
                try:
                    temperature = self.live_environment.read_temperature() if self.live_environment else None
                    field = self.live_environment.read_field() if self.live_environment else None
                    if hasattr(self.live_m81, "temperature_k") and temperature is not None:
                        self.live_m81.temperature_k = temperature
                    if hasattr(self.live_m81, "field_t") and field is not None:
                        self.live_m81.field_t = field
                    reading = self._read_live_measurement(measure_channel)
                    elapsed_s = time.monotonic() - (self.live_acquire_started_at or time.monotonic())
                    extra_values: dict[str, object] = {}
                    latest_parts: list[str] = []
                    if extra_channels:
                        labeled_readings: dict[str, dict[str, object]] = {}
                        for label, channel in extra_channels.items():
                            labeled_readings[label] = reading if channel == measure_channel else self._read_live_measurement(channel)
                        for label, labeled in labeled_readings.items():
                            for key in ["x", "y", "r", "theta_deg", "value"]:
                                if key in labeled:
                                    extra_values[f"{key}_{label}"] = labeled.get(key)
                            latest_value = labeled.get("x", labeled.get("value"))
                            if isinstance(latest_value, (int, float)):
                                latest_parts.append(f"{label}={latest_value:.6g}")
                    record = build_live_record(
                        sample_index,
                        elapsed_s,
                        measure_channel,
                        reading,
                        temperature,
                        field,
                        extra_values=extra_values,
                    )
                    with self.live_data_lock:
                        self.live_data = append_buffered_record(self.live_data, record, buffer_limit)
                    if latest_parts:
                        self._set_stringvar_async(self.live_last_reading_var, f"Last reading: {' '.join(latest_parts)}")
                    elif "x" in reading:
                        self._set_stringvar_async(
                            self.live_last_reading_var,
                            f"Last reading: {measure_channel} x={reading.get('x'):.6g} r={reading.get('r'):.6g}",
                        )
                    elif "value" in reading:
                        self._set_stringvar_async(
                            self.live_last_reading_var,
                            f"Last reading: {measure_channel} value={reading.get('value'):.6g}",
                        )
                    sample_index += 1
                except Exception:
                    self.log_queue.put(traceback.format_exc())
                    self._set_stringvar_async(self.status_var, "Live acquisition failed")
                    break
                self.live_acquire_stop.wait(interval_s)
            self._set_stringvar_async(self.live_acquisition_var, "Acquisition stopped")

        self.live_acquire_thread = threading.Thread(target=target, daemon=True)
        self.live_acquire_thread.start()
        if extra_channels:
            summary = ", ".join(f"{label}:{channel}" for label, channel in extra_channels.items())
            self.live_acquisition_var.set(f"Acquiring dual trace [{summary}] every {interval_s:.3f} s")
        else:
            self.live_acquisition_var.set(f"Acquiring {measure_channel} every {interval_s:.3f} s")
        self.status_var.set("Live acquisition running")

    def _stop_live_acquisition(self) -> None:
        self.live_acquire_stop.set()
        if self.live_acquire_thread and self.live_acquire_thread.is_alive() and self.live_acquire_thread is not threading.current_thread():
            self.live_acquire_thread.join(timeout=0.2)
        self.live_acquisition_var.set("Acquisition stopped")
        self.status_var.set("Live acquisition stopped")

    def _draw_live_plot(self) -> None:
        self.live_plot_canvas.delete("all")
        with self.live_data_lock:
            dataframe = pd.DataFrame(self.live_data)
        width = max(self.live_plot_canvas.winfo_width(), 420)
        height = max(self.live_plot_canvas.winfo_height(), 260)
        if dataframe.empty:
            self.live_plot_canvas.create_text(width / 2, height / 2, text="No live data yet", fill="#444")
            return
        x_col = self.live_plot_x_var.get()
        y_col = self.live_plot_y_var.get()
        self.live_plot_canvas.create_rectangle(24, 24, width - 24, height - 24, outline="#8cab8f")
        latest = dataframe.iloc[-1].to_dict()
        if y_col in {"x_dual", "r_dual"}:
            prefix = "x" if y_col == "x_dual" else "r"
            series = [
                (f"{prefix}_vxx", "#1d5f74", "Vxx"),
                (f"{prefix}_vxy", "#cc6b49", "Vxy"),
            ]
            drawn = 0
            for column, color, label in series:
                points = compute_plot_points(dataframe, x_col, column, width, height)
                if len(points) < 2:
                    continue
                drawn += 1
                flattened = [coordinate for point in points for coordinate in point]
                self.live_plot_canvas.create_line(*flattened, fill=color, width=2, smooth=False)
                for x_coord, y_coord in points[-min(len(points), 8):]:
                    self.live_plot_canvas.create_oval(x_coord - 2, y_coord - 2, x_coord + 2, y_coord + 2, fill=color, outline="")
                self.live_plot_canvas.create_text(48, 12 + (drawn - 1) * 16, text=f"{label}: {column}", anchor="w", fill=color)
            if drawn == 0:
                self.live_plot_canvas.create_text(width / 2, height / 2, text="No dual live data yet", fill="#444")
                return
            self.live_plot_canvas.create_text(
                48,
                height - 10,
                text=f"Latest Vxx/Vxy: {latest.get(f'{prefix}_vxx')} / {latest.get(f'{prefix}_vxy')}",
                anchor="w",
                fill="#333",
            )
        else:
            points = compute_plot_points(dataframe, x_col, y_col, width, height)
            if len(points) < 2:
                self.live_plot_canvas.create_text(width / 2, height / 2, text="Not enough numeric live data", fill="#444")
                return
            flattened = [coordinate for point in points for coordinate in point]
            self.live_plot_canvas.create_line(*flattened, fill="#1d5f74", width=2, smooth=False)
            for x_coord, y_coord in points[-min(len(points), 10):]:
                self.live_plot_canvas.create_oval(x_coord - 2, y_coord - 2, x_coord + 2, y_coord + 2, fill="#cc6b49", outline="")
            self.live_plot_canvas.create_text(48, 12, text=f"Y: {y_col}", anchor="w", fill="#333")
            self.live_plot_canvas.create_text(
                48,
                height - 10,
                text=f"Latest {y_col}: {latest.get(y_col)}",
                anchor="w",
                fill="#333",
            )
        self.live_plot_canvas.create_text(width - 48, height - 10, text=f"X: {x_col}", anchor="e", fill="#333")

    def _emergency_stop(self) -> None:
        if self.worker and self.worker.is_alive():
            self.log_queue.put("Emergency stop requested.\n")
        args = argparse.Namespace(
            config=self.config_var.get(),
            contact_map=self.contact_map_var.get(),
            mock=self.mock_var.get(),
            environment_mode=self.environment_mode_var.get(),
        )
        try:
            emergency_stop_command(args)
            if self.live_m81:
                self.live_m81.emergency_stop()
            if self.live_matrix:
                self.live_matrix.emergency_stop()
            self.log_queue.put("Emergency stop executed.\n")
            self.status_var.set("Emergency stop sent")
            self._refresh_live_status()
        except Exception:
            self.log_queue.put(traceback.format_exc())
            self.status_var.set("Emergency stop failed")

    def _refresh_preview(self) -> None:
        files = latest_csv_files(self.output_var.get())
        values = [str(path) for path in files]
        self.preview_combo["values"] = values
        if values and self.preview_file_var.get() not in values:
            self.preview_file_var.set(values[0])
        self._show_preview()

    def _select_latest_preview(self) -> None:
        files = latest_csv_files(self.output_var.get())
        if files:
            self.preview_file_var.set(str(files[0]))
        self._show_preview()

    def _show_preview(self) -> None:
        self.preview_text.delete("1.0", "end")
        preview_file = self.preview_file_var.get()
        if not preview_file:
            self.preview_text.insert("end", "No CSV files available in output directory.")
            self.preview_dataframe = None
            return
        try:
            self.preview_text.insert("end", preview_csv_text(preview_file))
            self.preview_dataframe = pd.read_csv(preview_file)
            columns = plottable_columns(self.preview_dataframe)
            self.plot_x_combo["values"] = columns
            self.plot_y_combo["values"] = columns
            if columns and self.plot_x_var.get() not in columns:
                self.plot_x_var.set(columns[0])
            if len(columns) > 1 and self.plot_y_var.get() not in columns:
                self.plot_y_var.set(columns[1])
            elif columns and self.plot_y_var.get() not in columns:
                self.plot_y_var.set(columns[0])
            self._draw_plot()
        except Exception as exc:
            self.preview_text.insert("end", f"Failed to load preview: {exc}")
            self.preview_dataframe = None

    def _draw_plot(self) -> None:
        self.plot_canvas.delete("all")
        dataframe = self.preview_dataframe
        if dataframe is None or dataframe.empty:
            self.plot_canvas.create_text(180, 80, text="No preview data", fill="#444")
            return
        width = max(self.plot_canvas.winfo_width(), 360)
        height = max(self.plot_canvas.winfo_height(), 260)
        points = compute_plot_points(dataframe, self.plot_x_var.get(), self.plot_y_var.get(), width, height)
        self.plot_canvas.create_rectangle(24, 24, width - 24, height - 24, outline="#b7ab8b")
        if len(points) < 2:
            self.plot_canvas.create_text(width / 2, height / 2, text="Not enough numeric data to plot", fill="#444")
            return
        flattened = [coordinate for point in points for coordinate in point]
        self.plot_canvas.create_line(*flattened, fill="#114b5f", width=2, smooth=False)
        for x_coord, y_coord in points[-min(len(points), 8):]:
            self.plot_canvas.create_oval(x_coord - 2, y_coord - 2, x_coord + 2, y_coord + 2, fill="#d95d39", outline="")
        self.plot_canvas.create_text(48, 12, text=f"Y: {self.plot_y_var.get()}", anchor="w", fill="#333")
        self.plot_canvas.create_text(width - 48, height - 10, text=f"X: {self.plot_x_var.get()}", anchor="e", fill="#333")


def launch_gui(initial_args: argparse.Namespace | None = None) -> int:
    root = tk.Tk()
    MeasurementGUI(root, initial_args=initial_args)
    root.mainloop()
    return 0
