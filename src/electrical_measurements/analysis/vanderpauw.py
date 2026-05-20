from __future__ import annotations

import math

from scipy.optimize import brentq


def solve_vanderpauw_sheet_resistance(r_ab_cd: float, r_bc_da: float) -> float:
    r_ab_cd = abs(float(r_ab_cd))
    r_bc_da = abs(float(r_bc_da))
    if r_ab_cd == 0 and r_bc_da == 0:
        return 0.0
    if math.isclose(r_ab_cd, r_bc_da, rel_tol=1e-9, abs_tol=1e-12):
        return math.pi * r_ab_cd / math.log(2.0)

    def equation(rs: float) -> float:
        return math.exp(-math.pi * r_ab_cd / rs) + math.exp(-math.pi * r_bc_da / rs) - 1.0

    lower = max(min(r_ab_cd, r_bc_da) * 0.1, 1e-12)
    upper = max(r_ab_cd, r_bc_da) * 1000.0 + 1e-12
    return float(brentq(equation, lower, upper))

