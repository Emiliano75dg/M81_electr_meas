import pandas as pd

from electrical_measurements.analysis.antisymmetrize import antisymmetrize_in_field, current_reversal_average
from electrical_measurements.analysis.hall import compute_hall_density, infer_carrier_sign


def test_hall_antisymmetrization():
    df = pd.DataFrame({"field_t": [1.0, -1.0], "rxy": [5.0, -5.0]})
    result = antisymmetrize_in_field(df, "rxy")
    assert float(result.iloc[0]["rxy_odd"]) == 5.0


def test_current_reversal_average():
    df = pd.DataFrame(
        {
            "field_t": [0.0, 0.0],
            "current_sign": [1, -1],
            "value": [2.0, -2.0],
        }
    )
    result = current_reversal_average(df)
    assert float(result.iloc[0]["value_current_reversal_avg"]) == 2.0


def test_hall_density_and_sign():
    density = compute_hall_density(-2.0)
    assert density["carrier_density_2d_m2"] is not None
    assert infer_carrier_sign(-2.0) == "electrons"
