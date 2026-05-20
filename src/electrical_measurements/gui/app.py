from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import queue
import threading
import time
import traceback
import tkinter as tk
from tkinter import filedialog, ttk

import pandas as pd

from ..runners.run_measurement import (
    apply_environment_mode_override,
    build_instruments,
    build_run_namespace,
    emergency_stop_command,
    load_yaml,
    run_command,
)
from ..instruments.teslatron_client import environment_mode_from_config
from ..switching.contact_map import ContactMap

PROTOCOLS = ["hall", "hallbar_mr", "vdp", "second_harmonic", "reciprocity", "check_contacts"]
MODES = ["stable", "stream-ramp"]
RAMP_QUANTITIES = ["field", "temperature"]
ENVIRONMENT_MODES = ["integrated", "async-poll", "standalone"]
PLOT_PREFERRED_COLUMNS = ["field_t", "temperature_k", "rxx_ohm", "rxy_ohm", "lockin_x", "lockin_r", "dc_value"]
MANUAL_SOURCE_MODES = ["DC current", "DC voltage", "AC current", "AC voltage"]
LIVE_PLOT_X_COLUMNS = ["sample_index", "elapsed_s", "field_t", "temperature_k"]
LIVE_PLOT_Y_COLUMNS = ["x", "y", "r", "theta_deg", "value", "x_dual", "r_dual"]


def environment_mode_supports_control(environment_mode: str) -> bool:
    return environment_mode == "integrated"


def protocol_is_multi_state(protocol: str) -> bool:
    return protocol in {"vdp", "reciprocity", "check_contacts"}


def recommended_states_for_protocol(protocol: str, contact_map: ContactMap) -> list[str]:
    states = contact_map.states
    if protocol == "hall":
        preferred = [name for name, state in states.items() if "voltage_transverse" in state or state.get("type") in {"hallbar", "hall"}]
        return preferred or list(states.keys())
    if protocol == "hallbar_mr":
        preferred = [name for name, state in states.items() if "voltage_longitudinal" in state or state.get("group") == "longitudinal"]
        return preferred or list(states.keys())
    if protocol == "vdp":
        preferred = contact_map.get_states_for_group("vdp")
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

        self.config_var = tk.StringVar(value=getattr(initial_args, "config", "configs/instruments.yaml"))
        self.contact_map_var = tk.StringVar(value=getattr(initial_args, "contact_map", "configs/contact_maps/hallbar_6contacts_7709.yaml"))
        self.output_var = tk.StringVar(value="data")
        self.sample_id_var = tk.StringVar(value="")
        self.protocol_var = tk.StringVar(value="hallbar_mr")
        self.mode_var = tk.StringVar(value="stable")
        self.mock_var = tk.BooleanVar(value=True)
        self.dry_run_var = tk.BooleanVar(value=False)
        self.environment_mode_var = tk.StringVar(value="integrated")
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
        self.live_plot_x_var = tk.StringVar(value="elapsed_s")
        self.live_plot_y_var = tk.StringVar(value="x")
        self.live_interval_var = tk.StringVar(value="0.25")
        self.live_buffer_var = tk.StringVar(value="300")
        self.live_env_temperature_target_var = tk.StringVar(value="300")
        self.live_env_field_target_var = tk.StringVar(value="0")
        self.live_env_ramp_rate_var = tk.StringVar(value="60")
        self.status_var = tk.StringVar(value="Ready")
        self.live_temperature_var = tk.StringVar(value="T: --")
        self.live_field_var = tk.StringVar(value="B: --")
        self.live_sources_var = tk.StringVar(value="Sources: --")
        self.live_relays_var = tk.StringVar(value="Relays: --")
        self.live_connection_var = tk.StringVar(value="Disconnected")
        self.live_environment_mode_var = tk.StringVar(value="Env mode: --")
        self.live_environment_action_var = tk.StringVar(value="Env control idle")
        self.live_acquisition_var = tk.StringVar(value="Acquisition stopped")
        self.live_last_reading_var = tk.StringVar(value="Last reading: --")

        self._build_layout()
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
        self.live_tab = ttk.Frame(self.notebook, padding=12)
        self.log_tab = ttk.Frame(self.notebook, padding=12)
        self.preview_tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(self.setup_tab, text="Setup")
        self.notebook.add(self.states_tab, text="States")
        self.notebook.add(self.live_tab, text="Live")
        self.notebook.add(self.log_tab, text="Log")
        self.notebook.add(self.preview_tab, text="Preview")

        self._build_setup_tab()
        self._build_states_tab()
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
        ttk.Label(summary, textvariable=self.live_environment_mode_var).grid(row=4, column=0, sticky="w", pady=(6, 0))
        ttk.Label(summary, textvariable=self.live_environment_action_var).grid(row=5, column=0, sticky="w", pady=(6, 0))
        ttk.Label(summary, textvariable=self.live_acquisition_var).grid(row=6, column=0, sticky="w", pady=(6, 0))
        ttk.Label(summary, textvariable=self.live_last_reading_var).grid(row=7, column=0, sticky="w", pady=(6, 0))

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
        manual_buttons = ttk.Frame(manual)
        manual_buttons.grid(row=7, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Button(manual_buttons, text="Configure Source", command=self._manual_configure_source).pack(side="left")
        ttk.Button(manual_buttons, text="Enable Source", command=self._manual_enable_source).pack(side="left", padx=(8, 0))
        ttk.Button(manual_buttons, text="Disable Source", command=self._manual_disable_selected_source).pack(side="left", padx=(8, 0))
        ttk.Button(manual_buttons, text="Safe Switch Then Enable", command=self._guided_safe_switch_then_enable).pack(side="left", padx=(8, 0))
        guided_buttons = ttk.Frame(manual)
        guided_buttons.grid(row=8, column=0, columnspan=2, sticky="w", pady=(8, 0))
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
            self.log_queue.put(f"Loaded contact map: {self.contact_map.name}\n")
            self.status_var.set(f"Loaded {self.contact_map.name}")
        except Exception as exc:
            self.contact_map = None
            self.log_queue.put(f"Failed to load contact map: {exc}\n")
            self.status_var.set("Contact map error")

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
        is_multi = protocol_is_multi_state(self.protocol_var.get())
        self.state_combo.configure(state="disabled" if is_multi else "readonly")
        self.state_listbox.configure(state="normal" if is_multi else "disabled")
        if self.contact_map is not None:
            recommended = recommended_states_for_protocol(self.protocol_var.get(), self.contact_map)
            if recommended:
                self.state_name_var.set(recommended[0])
            self._select_recommended_states()
        self._update_state_details()

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
            message = "Ready for streaming ramp" if mode == "stream-ramp" else "Ready"
        elif env_mode == "async-poll":
            message = "Async poll: T/B are observed only; setpoints and ramps are disabled"
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
        if self.live_environment and hasattr(self.live_environment, "advance_time") and self.mode_var.get() == "stream-ramp":
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
            reading = self.live_m81.read_lockin(channel)
            self.log_queue.put(f"{channel} reading: {json.dumps(reading, default=str)}\n")
            self.live_last_reading_var.set(f"Last reading: {channel} x={reading.get('x')} r={reading.get('r')}")
        except Exception:
            try:
                reading = self.live_m81.read_dc(channel)
                self.log_queue.put(f"{channel} DC reading: {json.dumps(reading, default=str)}\n")
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
