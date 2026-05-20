from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..exceptions import ContactMapError


def row_column_to_channel(row: int, column: int) -> int:
    return (row - 1) * 8 + column


@dataclass
class ContactMap:
    path: Path
    data: dict[str, Any]

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ContactMap":
        yaml_path = Path(path)
        data = yaml.safe_load(yaml_path.read_text())
        instance = cls(path=yaml_path, data=data)
        instance.validate()
        return instance

    @property
    def name(self) -> str:
        return str(self.data["name"])

    @property
    def contacts(self) -> dict[str, Any]:
        return dict(self.data.get("contacts", {}))

    @property
    def instruments(self) -> dict[str, Any]:
        return dict(self.data.get("instruments", {}))

    @property
    def states(self) -> dict[str, Any]:
        return dict(self.data.get("states", {}))

    def get_state(self, state_name: str) -> dict[str, Any]:
        state = dict(self.states[state_name])
        if not state.get("relay_channels"):
            state["relay_channels"] = self.generate_relay_channels(state)
        return state

    def get_reciprocal_state(self, state_name: str) -> dict[str, Any] | None:
        reciprocal = self.states[state_name].get("reciprocal")
        return self.get_state(reciprocal) if reciprocal else None

    def get_reverse_current_state(self, state_name: str) -> dict[str, Any] | None:
        reverse = self.states[state_name].get("reverse_current")
        return self.get_state(reverse) if reverse else None

    def get_state_name(self, state_name: str, relation: str) -> str | None:
        return self.states[state_name].get(relation)

    def get_states_for_group(self, group: str) -> list[str]:
        return [state_name for state_name, state in self.states.items() if state.get("group") == group]

    def instrument_channel(self, instrument_key: str) -> str | None:
        definition = self.instruments.get(instrument_key, {})
        instrument = definition.get("instrument")
        if not isinstance(instrument, str) or ":" not in instrument:
            return None
        return instrument.split(":")[-1].strip() or None

    def instrument_key_for_voltage(self, state: dict[str, Any]) -> str | None:
        if "voltage" in state:
            for instrument_key in self.instruments:
                if "meter" in instrument_key or instrument_key.endswith("_measure"):
                    return instrument_key
        if "voltage_longitudinal" in state:
            return "vxx_meter"
        return None

    def generate_relay_channels(self, state: dict[str, Any]) -> list[int]:
        channels: list[int] = []
        current = state.get("current", [])
        if current and "current_source" in self.instruments:
            src = self.instruments["current_source"]
            channels.extend(
                [
                    row_column_to_channel(src["row_hi"], self.contacts[current[0]]["column"]),
                    row_column_to_channel(src["row_lo"], self.contacts[current[1]]["column"]),
                ]
            )
        if "voltage" in state:
            key = self.instrument_key_for_voltage(state)
            if key:
                meter = self.instruments[key]
                channels.extend(
                    [
                        row_column_to_channel(meter["row_hi"], self.contacts[state["voltage"][0]]["column"]),
                        row_column_to_channel(meter["row_lo"], self.contacts[state["voltage"][1]]["column"]),
                    ]
                )
        if "voltage_longitudinal" in state and "vxx_meter" in self.instruments:
            meter = self.instruments["vxx_meter"]
            channels.extend(
                [
                    row_column_to_channel(meter["row_hi"], self.contacts[state["voltage_longitudinal"][0]]["column"]),
                    row_column_to_channel(meter["row_lo"], self.contacts[state["voltage_longitudinal"][1]]["column"]),
                ]
            )
        if "voltage_transverse" in state and "vxy_meter" in self.instruments:
            meter = self.instruments["vxy_meter"]
            channels.extend(
                [
                    row_column_to_channel(meter["row_hi"], self.contacts[state["voltage_transverse"][0]]["column"]),
                    row_column_to_channel(meter["row_lo"], self.contacts[state["voltage_transverse"][1]]["column"]),
                ]
            )
        return channels

    def describe_bindings(self, state_name: str) -> dict[str, list[tuple[str, str]]]:
        state = self.get_state(state_name)
        bindings: dict[str, list[tuple[str, str]]] = {}
        if "current" in state and "current_source" in self.instruments:
            source = self.instruments["current_source"]
            bindings.setdefault(state["current"][0], []).append(("current_source", f"row_{source['row_hi']}_hi"))
            bindings.setdefault(state["current"][1], []).append(("current_source", f"row_{source['row_lo']}_lo"))
        if "voltage" in state:
            key = self.instrument_key_for_voltage(state)
            if key:
                meter = self.instruments[key]
                bindings.setdefault(state["voltage"][0], []).append((key, f"row_{meter['row_hi']}_hi"))
                bindings.setdefault(state["voltage"][1], []).append((key, f"row_{meter['row_lo']}_lo"))
        if "voltage_longitudinal" in state and "vxx_meter" in self.instruments:
            meter = self.instruments["vxx_meter"]
            bindings.setdefault(state["voltage_longitudinal"][0], []).append(("vxx_meter", f"row_{meter['row_hi']}_hi"))
            bindings.setdefault(state["voltage_longitudinal"][1], []).append(("vxx_meter", f"row_{meter['row_lo']}_lo"))
        if "voltage_transverse" in state and "vxy_meter" in self.instruments:
            meter = self.instruments["vxy_meter"]
            bindings.setdefault(state["voltage_transverse"][0], []).append(("vxy_meter", f"row_{meter['row_hi']}_hi"))
            bindings.setdefault(state["voltage_transverse"][1], []).append(("vxy_meter", f"row_{meter['row_lo']}_lo"))
        return bindings

    def validate(self) -> None:
        if not self.data.get("name"):
            raise ContactMapError("Contact map must define a name")
        if not self.contacts:
            raise ContactMapError("Contact map must define contacts")
        if not self.states:
            raise ContactMapError("Contact map must define states")
        for contact, definition in self.contacts.items():
            if "column" not in definition:
                raise ContactMapError(f"Contact {contact} is missing column mapping")
        for state_name, state in self.states.items():
            for key in ["current", "voltage", "voltage_longitudinal", "voltage_transverse"]:
                for contact in state.get(key, []):
                    if contact not in self.contacts:
                        raise ContactMapError(f"State {state_name} references unknown contact {contact}")
            relay_channels = state.get("relay_channels")
            if relay_channels:
                if len(relay_channels) != len(set(relay_channels)):
                    raise ContactMapError(f"State {state_name} contains duplicate relay channels")
