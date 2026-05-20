from __future__ import annotations

import pandas as pd


def antisymmetrize_in_field(df: pd.DataFrame, value_col: str, field_col: str = "field_t", tolerance: float = 1e-9) -> pd.DataFrame:
    rows = []
    for _, row in df.iterrows():
        field = row[field_col]
        match = df.loc[(df[field_col] - (-field)).abs() <= tolerance]
        if match.empty:
            continue
        rows.append({field_col: field, f"{value_col}_odd": 0.5 * (row[value_col] - match.iloc[0][value_col])})
    return pd.DataFrame(rows)


def current_reversal_average(df: pd.DataFrame, value_col: str = "value", current_col: str = "current_sign", group_cols: list[str] | None = None) -> pd.DataFrame:
    group_cols = group_cols or ["field_t"]
    rows = []
    for _, group in df.groupby(group_cols):
        plus = group.loc[group[current_col] > 0, value_col]
        minus = group.loc[group[current_col] < 0, value_col]
        if plus.empty or minus.empty:
            continue
        record = {col: group.iloc[0][col] for col in group_cols}
        record[f"{value_col}_current_reversal_avg"] = 0.5 * (float(plus.mean()) - float(minus.mean()))
        rows.append(record)
    return pd.DataFrame(rows)

