import math

from electrical_measurements.analysis.vanderpauw import compute_vanderpauw_anisotropy, solve_vanderpauw_sheet_resistance


def test_vanderpauw_solver_symmetric_case():
    resistance = 100.0
    result = solve_vanderpauw_sheet_resistance(resistance, resistance)
    expected = math.pi * resistance / math.log(2.0)
    assert math.isclose(result, expected, rel_tol=1e-9)


def test_vanderpauw_anisotropy_averages_orthogonal_families():
    result = compute_vanderpauw_anisotropy(
        {
            "I_AB_V_CD": 100.0,
            "I_CD_V_AB": 110.0,
            "I_BC_V_DA": 80.0,
            "I_DA_V_BC": 90.0,
        }
    )
    assert result["family_a_mean_ohm"] == 105.0
    assert result["family_b_mean_ohm"] == 85.0
    assert result["anisotropy_abs_ohm"] == 20.0
