import pandas as pd

from electrical_measurements.analysis.reciprocity import match_reciprocal_field, reciprocity_error


def test_reciprocity_error_at_zero_field():
    result = reciprocity_error(10.0, 9.0)
    assert result["r_average"] == 9.5
    assert result["error_abs"] == 1.0


def test_match_reciprocal_field_uses_opposite_b():
    df = pd.DataFrame(
        [
            {"state": "I_AB_V_CD", "reciprocal_state": "I_CD_V_AB", "field_t": 1.0, "r_ohm": 10.0},
            {"state": "I_CD_V_AB", "reciprocal_state": "I_AB_V_CD", "field_t": -1.0, "r_ohm": 9.8},
        ]
    )
    result = match_reciprocal_field(df)
    assert result.iloc[0]["field_pairing"] == "opposite_B"

