from __future__ import annotations

import pandas as pd


def extract_harmonic_components(df: pd.DataFrame, harmonic_col: str = "harmonic", x_col: str = "lockin_x", y_col: str = "lockin_y") -> pd.DataFrame:
    rows = []
    for harmonic, group in df.groupby(harmonic_col):
        rows.append(
            {
                "harmonic": harmonic,
                "x_mean": float(group[x_col].mean()),
                "y_mean": float(group[y_col].mean()),
            }
        )
    return pd.DataFrame(rows)

