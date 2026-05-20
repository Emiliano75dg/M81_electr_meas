from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from ..protocols.base import MeasurementPoint

DEFAULT_COLUMNS = [
    "timestamp",
    "sample_id",
    "run_id",
    "protocol",
    "geometry",
    "state",
    "reciprocal_state",
    "matrix_relay_channels",
    "temperature_k",
    "field_t",
    "source_channel",
    "measure_channel",
    "source_current_a_rms",
    "source_current_a_peak",
    "source_voltage_v_rms",
    "source_voltage_v_peak",
    "frequency_hz",
    "harmonic",
    "lockin_x",
    "lockin_y",
    "lockin_r",
    "lockin_theta_deg",
    "dc_value",
    "vxx_v",
    "vxy_v",
    "rxx_ohm",
    "rxy_ohm",
    "sheet_resistance_ohm_sq",
    "hall_density_m2",
    "hall_density_cm2",
    "mobility_m2_Vs",
    "mobility_cm2_Vs",
    "reciprocity_error_abs",
    "reciprocity_error_rel",
    "contact_quality_flag",
    "instrument_status",
    "m81_profile",
    "daq6510_status",
]


def measurement_points_to_dataframe(points: Iterable[MeasurementPoint]) -> pd.DataFrame:
    df = pd.DataFrame([point.to_record() for point in points])
    rename_map = {
        "carrier_density_2d_m2": "hall_density_m2",
        "carrier_density_2d_cm2": "hall_density_cm2",
    }
    df = df.rename(columns=rename_map)
    for column in DEFAULT_COLUMNS:
        if column not in df.columns:
            df[column] = None
    return df


def save_dataset(df: pd.DataFrame, output_dir: str | Path, stem: str = "measurement") -> dict[str, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    csv_path = output_path / f"{stem}.csv"
    parquet_path = output_path / f"{stem}.parquet"
    df.to_csv(csv_path, index=False)
    try:
        df.to_parquet(parquet_path, index=False)
    except Exception:
        parquet_path = output_path / f"{stem}.parquet.unavailable"
        parquet_path.write_text("Parquet export unavailable in this environment.\n")
    return {"csv": csv_path, "parquet": parquet_path}
