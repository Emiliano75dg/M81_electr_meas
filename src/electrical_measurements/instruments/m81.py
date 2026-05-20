from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from .m81_scpi_fallback import M81SCPIFallback

LOGGER = logging.getLogger(__name__)
VALID_MEASURE_MODES = {"auto", "dc", "lockin"}
VALID_LOCKIN_ROLLOFFS = {"R6", "R12", "R18", "R24"}

try:
    from lakeshore import SSMSystem
except Exception:  # pragma: no cover - optional dependency
    SSMSystem = None  # type: ignore[assignment]


@dataclass
class SourceConfig:
    mode: str = "off"
    enabled: bool = False
    current_a: float | None = None
    voltage_v: float | None = None
    frequency_hz: float | None = None
    compliance_v: float | None = None
    compliance_a: float | None = None
    autorange: bool = True
    reference_source: str | None = None
    harmonic: int | None = None


class M81Controller:
    """High-level wrapper around the official Lake Shore driver."""

    def __init__(self, system: Any, config: dict[str, Any] | None = None) -> None:
        self.system = system
        self.config = config or {}
        self._sources: dict[str, Any] = {}
        self._measures: dict[str, Any] = {}
        self._source_state: dict[str, SourceConfig] = {}
        self._measure_state: dict[str, dict[str, Any]] = {}
        self._preferred_measure_modes: dict[str, str] = {}
        self._preferred_measure_harmonics: dict[str, int] = {}
        self._preferred_measure_nplc: dict[str, float] = {}
        self._preferred_measure_time_constants: dict[str, float] = {}
        self._preferred_measure_rolloffs: dict[str, str] = {}
        self._measurement_context: dict[str, Any] = {}
        self._trace_config: dict[str, Any] = {}
        self.scpi = M81SCPIFallback(system)
        self._discover_modules()
        self._load_measure_mode_preferences()

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "M81Controller":
        instrument_cfg = config.get("instruments", {}).get("m81", config.get("m81", {}))
        connection_cfg = instrument_cfg.get("connection", {})
        if SSMSystem is None:
            raise RuntimeError("The lakeshore package is not installed. Install it with `pip install lakeshore`.")
        kind = connection_cfg.get("kind", "usb").lower()
        if kind == "usb":
            system = SSMSystem()
        elif kind in {"visa", "tcp", "gpib"}:
            import pyvisa

            resource = connection_cfg["resource"]
            rm = pyvisa.ResourceManager()
            system = SSMSystem(connection=rm.open_resource(resource))
        else:
            raise ValueError(f"Unsupported M81 connection kind: {kind}")
        return cls(system=system, config=instrument_cfg)

    def _discover_modules(self) -> None:
        for idx in range(1, 4):
            source_name = f"S{idx}"
            measure_name = f"M{idx}"
            try:
                self._sources[source_name] = self.system.get_source_module(idx)
                self._source_state[source_name] = SourceConfig()
            except Exception:
                continue
            try:
                self._measures[measure_name] = self.system.get_measure_module(idx)
            except Exception:
                continue

    def _load_measure_mode_preferences(self) -> None:
        configured = self.config.get("measure_modes", {})
        if not isinstance(configured, dict):
            configured = {}
        for measure_channel, mode in configured.items():
            if isinstance(measure_channel, str) and isinstance(mode, str):
                self.set_preferred_measure_mode(measure_channel, mode)
        harmonics = self.config.get("measure_harmonics", {})
        if not isinstance(harmonics, dict):
            harmonics = {}
        for measure_channel, harmonic in harmonics.items():
            if isinstance(measure_channel, str) and isinstance(harmonic, int):
                self.set_preferred_measure_harmonic(measure_channel, harmonic)
        nplc_values = self.config.get("measure_nplc", {})
        if not isinstance(nplc_values, dict):
            nplc_values = {}
        for measure_channel, nplc in nplc_values.items():
            if isinstance(measure_channel, str) and isinstance(nplc, (int, float)):
                self.set_preferred_measure_nplc(measure_channel, float(nplc))
        time_constants = self.config.get("measure_time_constants_s", {})
        if not isinstance(time_constants, dict):
            time_constants = {}
        for measure_channel, time_constant_s in time_constants.items():
            if isinstance(measure_channel, str) and isinstance(time_constant_s, (int, float)):
                self.set_preferred_measure_time_constant(measure_channel, float(time_constant_s))
        rolloffs = self.config.get("measure_rolloffs", {})
        if not isinstance(rolloffs, dict):
            return
        for measure_channel, rolloff in rolloffs.items():
            if isinstance(measure_channel, str) and isinstance(rolloff, str):
                self.set_preferred_measure_rolloff(measure_channel, rolloff)

    def set_preferred_measure_mode(self, measure_channel: str, mode: str) -> None:
        normalized = str(mode).strip().lower()
        if normalized not in VALID_MEASURE_MODES:
            raise ValueError(f"Unsupported measure mode for {measure_channel}: {mode}")
        self._preferred_measure_modes[measure_channel] = normalized

    def set_preferred_measure_harmonic(self, measure_channel: str, harmonic: int) -> None:
        parsed = int(harmonic)
        if parsed < 1:
            raise ValueError(f"Unsupported measure harmonic for {measure_channel}: {harmonic}")
        self._preferred_measure_harmonics[measure_channel] = parsed

    def get_preferred_measure_harmonic(self, measure_channel: str) -> int | None:
        return self._preferred_measure_harmonics.get(measure_channel)

    def set_preferred_measure_nplc(self, measure_channel: str, nplc: float) -> None:
        parsed = float(nplc)
        if parsed <= 0:
            raise ValueError(f"Unsupported measure nplc for {measure_channel}: {nplc}")
        self._preferred_measure_nplc[measure_channel] = parsed

    def set_preferred_measure_time_constant(self, measure_channel: str, time_constant_s: float) -> None:
        parsed = float(time_constant_s)
        if parsed <= 0:
            raise ValueError(f"Unsupported measure time constant for {measure_channel}: {time_constant_s}")
        self._preferred_measure_time_constants[measure_channel] = parsed

    def set_preferred_measure_rolloff(self, measure_channel: str, rolloff: str) -> None:
        normalized = str(rolloff).strip().upper()
        if normalized not in VALID_LOCKIN_ROLLOFFS:
            raise ValueError(f"Unsupported measure rolloff for {measure_channel}: {rolloff}")
        self._preferred_measure_rolloffs[measure_channel] = normalized

    def resolve_measure_harmonic(self, measure_channel: str, requested_harmonic: int | None = None) -> int:
        if requested_harmonic is not None:
            parsed = int(requested_harmonic)
            if parsed < 1:
                raise ValueError(f"Unsupported requested harmonic for {measure_channel}: {requested_harmonic}")
            return parsed
        preferred_harmonic = self._preferred_measure_harmonics.get(measure_channel)
        if isinstance(preferred_harmonic, int) and preferred_harmonic >= 1:
            return int(preferred_harmonic)
        configured_harmonic = self._measure_state.get(measure_channel, {}).get("harmonic")
        if isinstance(configured_harmonic, int) and configured_harmonic >= 1:
            return configured_harmonic
        return 1

    def resolve_measure_mode(self, measure_channel: str, requested_mode: str = "auto") -> str:
        normalized = str(requested_mode).strip().lower()
        if normalized not in VALID_MEASURE_MODES:
            raise ValueError(f"Unsupported requested measure mode for {measure_channel}: {requested_mode}")
        if normalized != "auto":
            return normalized
        preferred_mode = self._preferred_measure_modes.get(measure_channel, "auto")
        if preferred_mode in VALID_MEASURE_MODES - {"auto"}:
            return preferred_mode
        configured_mode = self._measure_state.get(measure_channel, {}).get("mode")
        if isinstance(configured_mode, str) and configured_mode in VALID_MEASURE_MODES - {"auto"}:
            return configured_mode
        return "auto"

    def resolve_measure_nplc(self, measure_channel: str, requested_nplc: float | None = None) -> float:
        if requested_nplc is not None:
            parsed = float(requested_nplc)
            if parsed <= 0:
                raise ValueError(f"Unsupported requested nplc for {measure_channel}: {requested_nplc}")
            return parsed
        preferred_nplc = self._preferred_measure_nplc.get(measure_channel)
        if isinstance(preferred_nplc, (int, float)) and float(preferred_nplc) > 0:
            return float(preferred_nplc)
        configured_nplc = self._measure_state.get(measure_channel, {}).get("nplc")
        if isinstance(configured_nplc, (int, float)) and float(configured_nplc) > 0:
            return float(configured_nplc)
        return 1.0

    def resolve_measure_time_constant(self, measure_channel: str, requested_time_constant_s: float | None = None) -> float:
        if requested_time_constant_s is not None:
            parsed = float(requested_time_constant_s)
            if parsed <= 0:
                raise ValueError(f"Unsupported requested time constant for {measure_channel}: {requested_time_constant_s}")
            return parsed
        preferred_time_constant_s = self._preferred_measure_time_constants.get(measure_channel)
        if isinstance(preferred_time_constant_s, (int, float)) and float(preferred_time_constant_s) > 0:
            return float(preferred_time_constant_s)
        configured_time_constant_s = self._measure_state.get(measure_channel, {}).get("time_constant_s")
        if isinstance(configured_time_constant_s, (int, float)) and float(configured_time_constant_s) > 0:
            return float(configured_time_constant_s)
        return 0.3

    def resolve_measure_rolloff(self, measure_channel: str, requested_rolloff: str | None = None) -> str:
        if requested_rolloff is not None:
            normalized = str(requested_rolloff).strip().upper()
            if normalized not in VALID_LOCKIN_ROLLOFFS:
                raise ValueError(f"Unsupported requested rolloff for {measure_channel}: {requested_rolloff}")
            return normalized
        preferred_rolloff = self._preferred_measure_rolloffs.get(measure_channel)
        if isinstance(preferred_rolloff, str):
            normalized = preferred_rolloff.strip().upper()
            if normalized in VALID_LOCKIN_ROLLOFFS:
                return normalized
        configured_rolloff = self._measure_state.get(measure_channel, {}).get("rolloff")
        if isinstance(configured_rolloff, str):
            normalized = configured_rolloff.strip().upper()
            if normalized in VALID_LOCKIN_ROLLOFFS:
                return normalized
        return "R24"

    def get_source_module(self, source: str) -> Any:
        return self._sources[source]

    def get_measure_module(self, measure_channel: str) -> Any:
        return self._measures[measure_channel]

    def configure_dc_current(self, source: str, current_a: float, compliance_v: float = 1.0, autorange: bool = True) -> None:
        module = self.get_source_module(source)
        if hasattr(module, "go_to_current_mode"):
            module.go_to_current_mode()
        if hasattr(module, "set_shape"):
            module.set_shape("DC")
        if hasattr(module, "apply_dc_current"):
            module.apply_dc_current(float(current_a), output_enable=False)
        elif hasattr(module, "set_current_amplitude"):
            module.set_current_amplitude(float(current_a))
        if hasattr(module, "set_disable_on_compliance"):
            module.set_disable_on_compliance(True)
        self._source_state[source] = SourceConfig(
            mode="dc_current",
            current_a=current_a,
            compliance_v=compliance_v,
            autorange=autorange,
        )

    def configure_dc_voltage(self, source: str, voltage_v: float, compliance_a: float = 1e-3, autorange: bool = True) -> None:
        module = self.get_source_module(source)
        if hasattr(module, "go_to_voltage_mode"):
            module.go_to_voltage_mode()
        if hasattr(module, "set_shape"):
            module.set_shape("DC")
        if hasattr(module, "apply_dc_voltage"):
            module.apply_dc_voltage(float(voltage_v), output_enable=False)
        elif hasattr(module, "set_voltage_amplitude"):
            module.set_voltage_amplitude(float(voltage_v))
        if hasattr(module, "set_disable_on_compliance"):
            module.set_disable_on_compliance(True)
        self._source_state[source] = SourceConfig(
            mode="dc_voltage",
            voltage_v=voltage_v,
            compliance_a=compliance_a,
            autorange=autorange,
        )

    def configure_ac_current_lockin(
        self,
        source: str,
        current_rms_a: float,
        frequency_hz: float,
        measure_channels: list[str],
        harmonic: int = 1,
        time_constant_s: float = 0.3,
        reference_source: str = "S1",
    ) -> None:
        peak_current = current_rms_a * 2**0.5
        module = self.get_source_module(source)
        if hasattr(module, "go_to_current_mode"):
            module.go_to_current_mode()
        if hasattr(module, "set_shape"):
            module.set_shape("SINUSOID")
        if hasattr(module, "set_frequency"):
            module.set_frequency(float(frequency_hz))
        if hasattr(module, "apply_ac_current"):
            module.apply_ac_current(float(frequency_hz), float(peak_current), offset=0.0, output_enable=False)
        elif hasattr(module, "set_current_amplitude"):
            module.set_current_amplitude(float(peak_current))
        for measure_channel in measure_channels:
            self.configure_lockin_measure(
                measure_channel=measure_channel,
                harmonic=harmonic,
                time_constant_s=time_constant_s,
                reference_source=reference_source,
            )
        self._source_state[source] = SourceConfig(
            mode="ac_current",
            current_a=current_rms_a,
            frequency_hz=frequency_hz,
            reference_source=reference_source,
            harmonic=harmonic,
        )

    def configure_ac_voltage_lockin(
        self,
        source: str,
        voltage_rms_v: float,
        frequency_hz: float,
        measure_channels: list[str],
        harmonic: int = 1,
        time_constant_s: float = 0.3,
        reference_source: str = "S1",
    ) -> None:
        peak_voltage = voltage_rms_v * 2**0.5
        module = self.get_source_module(source)
        if hasattr(module, "go_to_voltage_mode"):
            module.go_to_voltage_mode()
        if hasattr(module, "set_shape"):
            module.set_shape("SINUSOID")
        if hasattr(module, "set_frequency"):
            module.set_frequency(float(frequency_hz))
        if hasattr(module, "apply_ac_voltage"):
            module.apply_ac_voltage(float(frequency_hz), float(peak_voltage), offset=0.0, output_enable=False)
        elif hasattr(module, "set_voltage_amplitude"):
            module.set_voltage_amplitude(float(peak_voltage))
        for measure_channel in measure_channels:
            self.configure_lockin_measure(
                measure_channel=measure_channel,
                harmonic=harmonic,
                time_constant_s=time_constant_s,
                reference_source=reference_source,
            )
        self._source_state[source] = SourceConfig(
            mode="ac_voltage",
            voltage_v=voltage_rms_v,
            frequency_hz=frequency_hz,
            reference_source=reference_source,
            harmonic=harmonic,
        )

    def configure_dc_measure(self, measure_channel: str, nplc: float = 1.0) -> None:
        module = self.get_measure_module(measure_channel)
        if hasattr(module, "setup_dc_measurement"):
            module.setup_dc_measurement(nplc=nplc)
        self._measure_state[measure_channel] = {"mode": "dc", "nplc": nplc}

    def configure_lockin_measure(
        self,
        measure_channel: str,
        harmonic: int = 1,
        time_constant_s: float = 0.3,
        reference_source: str = "S1",
        rolloff: str = "R24",
    ) -> None:
        module = self.get_measure_module(measure_channel)
        if hasattr(module, "setup_lock_in_measurement"):
            module.setup_lock_in_measurement(
                reference_source,
                time_constant_s,
                rolloff=rolloff,
                reference_harmonic=harmonic,
            )
        else:  # pragma: no cover - hardware fallback
            self.scpi._command(f"{measure_channel}:LIA:REF {reference_source}")
            self.scpi._command(f"{measure_channel}:LIA:HARM {harmonic}")
        self._measure_state[measure_channel] = {
            "mode": "lockin",
            "harmonic": harmonic,
            "time_constant_s": time_constant_s,
            "reference_source": reference_source,
            "rolloff": rolloff,
        }

    def configure_lockin_harmonic(self, measure_channel: str, harmonic: int) -> None:
        module = self.get_measure_module(measure_channel)
        if hasattr(module, "set_reference_harmonic"):
            module.set_reference_harmonic(harmonic)
        else:  # pragma: no cover - hardware fallback
            self.scpi._command(f"{measure_channel}:LIA:HARM {harmonic}")

    def enable_source(self, source: str) -> None:
        module = self.get_source_module(source)
        module.enable()
        self._source_state[source].enabled = True

    def disable_source(self, source: str) -> None:
        module = self.get_source_module(source)
        module.disable()
        self._source_state[source].enabled = False

    def disable_all_sources(self) -> None:
        for source in list(self._sources):
            try:
                self.disable_source(source)
            except Exception:
                LOGGER.exception("Failed to disable source %s", source)

    def any_source_enabled(self) -> bool:
        return any(state.enabled for state in self._source_state.values())

    def get_source_settings(self, source: str) -> SourceConfig:
        return self._source_state[source]

    def get_measure_settings(self, measure_channel: str) -> dict[str, Any]:
        settings = dict(self._measure_state.get(measure_channel, {}))
        settings["preferred_mode"] = self._preferred_measure_modes.get(measure_channel, "auto")
        settings["resolved_mode"] = self.resolve_measure_mode(measure_channel)
        settings["preferred_harmonic"] = self._preferred_measure_harmonics.get(measure_channel, 1)
        settings["resolved_harmonic"] = self.resolve_measure_harmonic(measure_channel)
        settings["preferred_nplc"] = self._preferred_measure_nplc.get(measure_channel, 1.0)
        settings["resolved_nplc"] = self.resolve_measure_nplc(measure_channel)
        settings["preferred_time_constant_s"] = self._preferred_measure_time_constants.get(measure_channel, 0.3)
        settings["resolved_time_constant_s"] = self.resolve_measure_time_constant(measure_channel)
        settings["preferred_rolloff"] = self._preferred_measure_rolloffs.get(measure_channel, "R24")
        settings["resolved_rolloff"] = self.resolve_measure_rolloff(measure_channel)
        return settings

    def set_measurement_context(self, **context: Any) -> None:
        self._measurement_context = dict(context)

    def _trace_channel_index(self, channel: str) -> int:
        return int(channel[1:])

    def _build_driver_trace_sources(self, channel: str) -> list[tuple[Any, int]]:
        if not hasattr(self.system, "DataSourceMnemonic"):
            return []
        channel_index = self._trace_channel_index(channel)
        mnemonic = self.system.DataSourceMnemonic
        sources: list[tuple[Any, int]] = []
        if hasattr(mnemonic, "RELATIVE_TIME"):
            sources.append((mnemonic.RELATIVE_TIME, 1))
        if channel.startswith("M"):
            for attr in ["MEASURE_X", "MEASURE_Y", "MEASURE_R", "MEASURE_THETA", "MEASURE_DC"]:
                if hasattr(mnemonic, attr):
                    sources.append((getattr(mnemonic, attr), channel_index))
        if channel.startswith("S"):
            for attr in ["SOURCE_OFFSET", "SOURCE_AMPLITUDE"]:
                if hasattr(mnemonic, attr):
                    sources.append((getattr(mnemonic, attr), channel_index))
        return sources

    def _driver_trace_rows_to_records(self, rows: Any, channel: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        start = datetime.now(timezone.utc)
        interval_s = float(self._trace_config.get("interval_s", 0.1))
        for index, row in enumerate(rows or []):
            values = list(row) if isinstance(row, (tuple, list)) else [row]
            timestamp = start + timedelta(seconds=index * interval_s)
            if values and isinstance(values[0], (int, float)):
                timestamp = start + timedelta(seconds=float(values[0]))
            record = {
                "trace_channel": channel,
                "trace_index": index,
                "timestamp": timestamp.isoformat(),
            }
            mapped_keys = ["relative_time_s", "x", "y", "r", "theta_deg", "dc_value"]
            for key, value in zip(mapped_keys, values):
                if key == "relative_time_s":
                    continue
                record[key] = value
            records.append(record)
        return records

    def read_lockin(self, measure_channel: str) -> dict[str, Any]:
        module = self.get_measure_module(measure_channel)
        harmonic = module.get_reference_harmonic() if hasattr(module, "get_reference_harmonic") else None
        data = {
            "x": module.get_lock_in_x(),
            "y": module.get_lock_in_y(),
            "r": module.get_lock_in_r(),
            "theta_deg": module.get_lock_in_theta(),
            "frequency_hz": module.get_lock_in_frequency() if hasattr(module, "get_lock_in_frequency") else None,
            "harmonic": harmonic,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if hasattr(module, "get_resistance"):
            try:
                data["resistance_ohm"] = module.get_resistance()
            except Exception:
                data["resistance_ohm"] = None
        return data

    def read_dc(self, measure_channel: str) -> dict[str, Any]:
        module = self.get_measure_module(measure_channel)
        value = module.get_dc()
        return {"value": value, "timestamp": datetime.now(timezone.utc).isoformat()}

    def configure_current_sweep(self, **kwargs: Any) -> None:
        self.scpi._command(f"SWE:CURR:CONF {kwargs}")

    def configure_voltage_sweep(self, **kwargs: Any) -> None:
        self.scpi._command(f"SWE:VOLT:CONF {kwargs}")

    def configure_trace_stream(self, channel: str, points: int = 1024, interval_s: float = 0.1) -> None:
        self._trace_config = {
            "channel": channel,
            "points": points,
            "interval_s": interval_s,
            "sample_rate_hz": max(int(round(1.0 / interval_s)), 1),
            "data_sources": self._build_driver_trace_sources(channel),
        }
        if hasattr(self.system, "get_data"):
            return
        self.scpi.configure_trace_stream(channel=channel, points=points, interval_s=interval_s)

    def start_trace(self) -> None:
        if hasattr(self.system, "initiate_sweeps"):
            self.system.initiate_sweeps()
            return
        self.scpi.start_trace()

    def fetch_trace(self, max_points: int | None = None) -> Any:
        if hasattr(self.system, "get_data") and self._trace_config:
            count = int(max_points or self._trace_config["points"])
            sample_rate_hz = int(self._trace_config["sample_rate_hz"])
            data_sources = self._trace_config.get("data_sources", [])
            rows = self.system.get_data(sample_rate_hz, count, *data_sources)
            return self._driver_trace_rows_to_records(rows, str(self._trace_config["channel"]))
        raw = self.scpi.fetch_trace()
        if not self._trace_config:
            return raw
        records = self.scpi.parse_trace_response(
            raw,
            channel=str(self._trace_config["channel"]),
            interval_s=float(self._trace_config.get("interval_s", 0.1)),
        )
        if max_points is not None:
            return records[:max_points]
        return records

    def abort_sweep(self) -> None:
        if hasattr(self.system, "abort_sweeps"):
            self.system.abort_sweeps()
            return
        self.scpi.abort_sweep()

    def emergency_stop(self) -> None:
        self.disable_all_sources()

    def status_snapshot(self) -> dict[str, Any]:
        source_status: dict[str, Any] = {}
        for name, settings in self._source_state.items():
            source_status[name] = {
                "mode": settings.mode,
                "enabled": settings.enabled,
                "current_a": settings.current_a,
                "voltage_v": settings.voltage_v,
                "frequency_hz": settings.frequency_hz,
                "harmonic": settings.harmonic,
            }
        measure_status: dict[str, dict[str, Any]] = {}
        for name in self._measures:
            measure_status[name] = self.get_measure_settings(name)
        snapshot = {
            "sources": source_status,
            "measures": measure_status,
            "trace_config": dict(self._trace_config),
        }
        try:
            snapshot["status_register"] = self.scpi.get_status_register()
        except Exception:
            snapshot["status_register"] = None
        try:
            snapshot["error_queue"] = self.scpi.get_error_queue()
        except Exception:
            snapshot["error_queue"] = None
        return snapshot
