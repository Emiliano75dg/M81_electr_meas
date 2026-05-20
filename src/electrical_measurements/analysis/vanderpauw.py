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


def compute_vanderpauw_anisotropy(resistances: dict[str, float]) -> dict[str, float | None]:
    family_a_states = ["I_AB_V_CD", "I_CD_V_AB"]
    family_b_states = ["I_BC_V_DA", "I_DA_V_BC"]
    family_a_values = [float(resistances[state]) for state in family_a_states if state in resistances]
    family_b_values = [float(resistances[state]) for state in family_b_states if state in resistances]
    family_a_mean = sum(family_a_values) / len(family_a_values) if family_a_values else None
    family_b_mean = sum(family_b_values) / len(family_b_values) if family_b_values else None
    anisotropy_abs = None
    anisotropy_rel = None
    anisotropy_ratio = None
    if family_a_mean is not None and family_b_mean is not None:
        anisotropy_abs = family_a_mean - family_b_mean
        denom = abs(family_a_mean) + abs(family_b_mean)
        anisotropy_rel = None if denom == 0 else 2.0 * anisotropy_abs / denom
        if family_b_mean != 0:
            anisotropy_ratio = family_a_mean / family_b_mean
    return {
        "family_a_mean_ohm": family_a_mean,
        "family_b_mean_ohm": family_b_mean,
        "anisotropy_abs_ohm": anisotropy_abs,
        "anisotropy_rel": anisotropy_rel,
        "anisotropy_ratio": anisotropy_ratio,
    }
