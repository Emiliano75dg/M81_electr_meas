from __future__ import annotations

import pandas as pd


def symmetrize_in_field(df: pd.DataFrame, value_col: str, field_col: str = "field_t", tolerance: float = 1e-9) -> pd.DataFrame:
    rows = []
    for _, row in df.iterrows():
        field = row[field_col]
        match = df.loc[(df[field_col] - (-field)).abs() <= tolerance]
        if match.empty:
            continue
        rows.append({field_col: field, f"{value_col}_even": 0.5 * (row[value_col] + match.iloc[0][value_col])})
    return pd.DataFrame(rows)

