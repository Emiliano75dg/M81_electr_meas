from __future__ import annotations

from typing import Any

import pandas as pd

from ..instruments.teslatron_client import environment_supports_control


def parse_float_list(value: str | None) -> list[float]:
    if not value:
        return [0.0]
    return [float(item) for item in value.split(",")]


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
