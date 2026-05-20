from __future__ import annotations

import numpy as np

ELEMENTARY_CHARGE_C = 1.602176634e-19


def compute_hall_density(slope_rxy_vs_b: float) -> dict[str, float | None]:
    if slope_rxy_vs_b == 0:
        return {"carrier_density_2d_m2": None, "carrier_density_2d_cm2": None}
    n_2d = 1.0 / (ELEMENTARY_CHARGE_C * slope_rxy_vs_b)
    return {
        "carrier_density_2d_m2": n_2d,
        "carrier_density_2d_cm2": n_2d / 1e4,
    }


def compute_mobility(sheet_resistance_ohm_sq: float | None, carrier_density_2d_m2: float | None) -> dict[str, float | None]:
    if not sheet_resistance_ohm_sq or not carrier_density_2d_m2:
        return {"mobility_m2_Vs": None, "mobility_cm2_Vs": None}
    mu = 1.0 / (ELEMENTARY_CHARGE_C * carrier_density_2d_m2 * sheet_resistance_ohm_sq)
    return {"mobility_m2_Vs": mu, "mobility_cm2_Vs": mu * 1e4}


def fit_hall_slope(field_t: np.ndarray, rxy_ohm: np.ndarray) -> float:
    coeffs = np.polyfit(field_t, rxy_ohm, 1)
    return float(coeffs[0])


def infer_carrier_sign(slope_rxy_vs_b: float) -> str | None:
    if slope_rxy_vs_b == 0:
        return None
    return "holes" if slope_rxy_vs_b > 0 else "electrons"
