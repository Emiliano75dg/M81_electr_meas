from __future__ import annotations

import pandas as pd


def reciprocity_error(r_forward: float, r_reciprocal: float) -> dict[str, float | None]:
    denom = abs(r_forward) + abs(r_reciprocal)
    return {
        "r_forward": r_forward,
        "r_reciprocal": r_reciprocal,
        "r_average": 0.5 * (r_forward + r_reciprocal),
        "error_abs": r_forward - r_reciprocal,
        "error_rel": None if denom == 0 else 2 * (r_forward - r_reciprocal) / denom,
    }


def match_reciprocal_field(dataframe: pd.DataFrame, field_tolerance: float = 1e-4) -> pd.DataFrame:
    rows: list[dict[str, float | str | None]] = []
    for _, row in dataframe.iterrows():
        reciprocal_state = row.get("reciprocal_state")
        field_t = row.get("field_t")
        if reciprocal_state is None or field_t is None:
            continue
        matches = dataframe.loc[
            (dataframe["state"] == reciprocal_state)
            & ((dataframe["field_t"] - (-field_t)).abs() <= field_tolerance)
        ]
        if matches.empty:
            continue
        reciprocal_row = matches.iloc[0]
        error = reciprocity_error(float(row["r_ohm"]), float(reciprocal_row["r_ohm"]))
        rows.append(
            {
                "state": row["state"],
                "reciprocal_state": reciprocal_state,
                "field_t": field_t,
                "matched_field_t": reciprocal_row["field_t"],
                "field_pairing": "opposite_B",
                "is_reciprocal_pair": True,
                "reciprocity_error_abs": error["error_abs"],
                "reciprocity_error_rel": error["error_rel"],
                "reciprocal_average_ohm": error["r_average"],
            }
        )
    return pd.DataFrame(rows)

