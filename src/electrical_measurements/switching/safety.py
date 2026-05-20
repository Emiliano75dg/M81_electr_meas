from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any

LOGGER = logging.getLogger(__name__)


@dataclass
class SafetyValidationResult:
    ok: bool
    reason: str | None = None


def validate_matrix_state(state: dict[str, Any], active_sources: bool = False, override: bool = False) -> SafetyValidationResult:
    if active_sources and not override:
        return SafetyValidationResult(False, "Cannot switch matrix while sources are enabled")
    current = state.get("current", [])
    voltage = state.get("voltage", [])
    if len(current) == 2 and current[0] == current[1]:
        return SafetyValidationResult(False, "Current HI and LO cannot be shorted to the same node")
    if len(voltage) == 2 and voltage[0] == voltage[1]:
        return SafetyValidationResult(False, "Voltage HI and LO cannot be shorted to the same node")
    if len(current) == 2 and len(set(current)) != 2:
        return SafetyValidationResult(False, "Duplicate current contacts are not allowed")
    return SafetyValidationResult(True)


def validate_contact_map_state(contact_map: Any, state_name: str, active_sources: bool = False, override: bool = False) -> SafetyValidationResult:
    state = contact_map.get_state(state_name)
    result = validate_matrix_state(state, active_sources=active_sources, override=override)
    if not result.ok:
        return result
    bindings = contact_map.describe_bindings(state_name)
    for contact, attached in bindings.items():
        driven = [item for item in attached if "current_source" in item[0]]
        if len(driven) > 1:
            return SafetyValidationResult(False, f"Contact {contact} has multiple source outputs attached")
    channels = state.get("relay_channels", [])
    if len(channels) != len(set(channels)):
        return SafetyValidationResult(False, "Duplicate relay channel closure requested")
    return SafetyValidationResult(True)


def switching_log_entry(state_name: str, channels: list[int]) -> dict[str, Any]:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "state": state_name,
        "relay_channels": list(channels),
    }
