import math

from electrical_measurements.analysis.vanderpauw import solve_vanderpauw_sheet_resistance


def test_vanderpauw_solver_symmetric_case():
    resistance = 100.0
    result = solve_vanderpauw_sheet_resistance(resistance, resistance)
    expected = math.pi * resistance / math.log(2.0)
    assert math.isclose(result, expected, rel_tol=1e-9)

