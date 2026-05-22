from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class InstrumentCapabilities:
    sources: tuple[str, ...]
    measure_channels: tuple[str, ...]


DEFAULT_INSTRUMENT_CAPABILITIES = InstrumentCapabilities(
    sources=("S1", "S2", "S3"),
    measure_channels=("M1", "M2", "M3"),
)


def instrument_capabilities_from_config(config: dict[str, Any] | None = None) -> InstrumentCapabilities:
    if not isinstance(config, dict):
        return DEFAULT_INSTRUMENT_CAPABILITIES
    instruments = config.get("instruments", {})
    m81_cfg = instruments.get("m81", {})
    configured_sources = m81_cfg.get("sources")
    configured_measures = m81_cfg.get("measure_channels")
    sources = tuple(configured_sources) if isinstance(configured_sources, list) and configured_sources else DEFAULT_INSTRUMENT_CAPABILITIES.sources
    measure_channels = tuple(configured_measures) if isinstance(configured_measures, list) and configured_measures else DEFAULT_INSTRUMENT_CAPABILITIES.measure_channels
    return InstrumentCapabilities(sources=sources, measure_channels=measure_channels)


def instrument_capabilities_from_controller(controller: Any | None) -> InstrumentCapabilities:
    if controller is None:
        return DEFAULT_INSTRUMENT_CAPABILITIES
    sources = getattr(controller, "_sources", None)
    measures = getattr(controller, "_measures", None)
    source_names = tuple(sorted(sources)) if isinstance(sources, dict) and sources else DEFAULT_INSTRUMENT_CAPABILITIES.sources
    measure_names = tuple(sorted(measures)) if isinstance(measures, dict) and measures else DEFAULT_INSTRUMENT_CAPABILITIES.measure_channels
    return InstrumentCapabilities(sources=source_names, measure_channels=measure_names)
