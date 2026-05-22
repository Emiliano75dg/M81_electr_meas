from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from .exceptions import RunnerInputError, SequenceValidationError

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class DCExcitation:
    source: str
    current_a: float
    bias_polarity: int = 1
    settle_s: float = 0.0

    @property
    def actual_current_a(self) -> float:
        return self.bias_polarity * self.current_a


@dataclass(frozen=True)
class ACExcitation:
    source: str
    current_rms_a: float
    frequency_hz: float
    harmonic: int
    settle_s: float = 0.0
    lockin: bool | None = True


def _raise(error_cls, message: str):
    raise error_cls(message)


def validate_dc_excitation(
    *,
    source: str | None,
    current_a: float | None,
    bias_polarity: int | None = None,
    settle_s: float | None = None,
    error_cls=SequenceValidationError,
) -> DCExcitation:
    if not source:
        _raise(error_cls, "dc excitation requires a source")
    if current_a is None or float(current_a) <= 0:
        _raise(error_cls, "dc excitation requires current_a > 0")
    resolved_polarity = 1 if bias_polarity is None else int(bias_polarity)
    if resolved_polarity not in {1, -1}:
        _raise(error_cls, "dc excitation bias_polarity must be +1 or -1")
    resolved_settle = 0.0 if settle_s is None else float(settle_s)
    if resolved_settle < 0:
        _raise(error_cls, "dc excitation settle_s must be >= 0")
    return DCExcitation(
        source=str(source),
        current_a=float(current_a),
        bias_polarity=resolved_polarity,
        settle_s=resolved_settle,
    )


def validate_ac_excitation(
    *,
    source: str | None,
    current_rms_a: float | None,
    frequency_hz: float | None,
    harmonic: int | None,
    bias_polarity: int | None = None,
    settle_s: float | None = None,
    lockin: bool | None = True,
    expert_mode: bool = False,
    error_cls=SequenceValidationError,
) -> ACExcitation:
    if not source:
        _raise(error_cls, "ac excitation requires a source")
    if current_rms_a is None or float(current_rms_a) <= 0:
        _raise(error_cls, "ac excitation requires current_rms_a > 0")
    if frequency_hz is None or float(frequency_hz) <= 0:
        _raise(error_cls, "ac excitation requires frequency_hz > 0")
    if harmonic is None or int(harmonic) < 1:
        _raise(error_cls, "ac excitation requires harmonic >= 1")
    if bias_polarity is not None:
        if not expert_mode:
            _raise(error_cls, "ac excitation does not allow bias_polarity by default")
        LOGGER.warning(
            "AC excitation received bias_polarity=%s in expert_mode. Ordinary reverse bias is normally not needed for AC lock-in measurements.",
            bias_polarity,
        )
    resolved_settle = 0.0 if settle_s is None else float(settle_s)
    if resolved_settle < 0:
        _raise(error_cls, "ac excitation settle_s must be >= 0")
    return ACExcitation(
        source=str(source),
        current_rms_a=float(current_rms_a),
        frequency_hz=float(frequency_hz),
        harmonic=int(harmonic),
        settle_s=resolved_settle,
        lockin=lockin,
    )


def validate_legacy_ac_cli_args(
    *,
    current: float,
    frequency: float,
    harmonic: int,
) -> ACExcitation:
    return validate_ac_excitation(
        source="S1",
        current_rms_a=current,
        frequency_hz=frequency,
        harmonic=harmonic,
        error_cls=RunnerInputError,
    )


def configure_m81_source_for_dc(m81: Any, excitation: DCExcitation, measure_channels: list[str]) -> None:
    for measure_channel in measure_channels:
        m81.configure_dc_measure(measure_channel)
    m81.configure_dc_current(excitation.source, excitation.actual_current_a)


def configure_m81_source_for_ac(m81: Any, excitation: ACExcitation, measure_channels: list[str]) -> None:
    m81.configure_ac_current_lockin(
        source=excitation.source,
        current_rms_a=excitation.current_rms_a,
        frequency_hz=excitation.frequency_hz,
        measure_channels=measure_channels,
        harmonic=excitation.harmonic,
    )
