from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..instruments.m81 import M81Controller
from ..instruments.mock import MockEnvironmentController, MockM81Controller
from ..instruments.teslatron_client import (
    ReadOnlyEnvironmentController,
    StandaloneEnvironmentController,
    TeslatronClient,
    environment_allow_control_from_config,
    environment_mode_from_config,
)
from ..switching.contact_map import ContactMap
from ..switching.matrix7709 import Matrix7709


def load_yaml(path: str | Path) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text())


def apply_environment_mode_override(config: dict[str, Any], environment_mode: str | None) -> dict[str, Any]:
    if not environment_mode:
        return config
    updated = dict(config)
    instruments = dict(updated.get("instruments", {}))
    environment = dict(instruments.get("environment", {}))
    environment["mode"] = environment_mode
    instruments["environment"] = environment
    updated["instruments"] = instruments
    return updated


def build_instruments(config: dict[str, Any], contact_map: ContactMap, mock: bool = False):
    if mock or config.get("instruments", {}).get("m81", {}).get("connection", {}).get("kind") == "mock":
        m81 = MockM81Controller.from_config(config)
    else:
        m81 = M81Controller.from_config(config)
    matrix = Matrix7709.from_config(config, contact_map=contact_map, m81=m81)
    env_cfg = config.get("instruments", {}).get("environment", {})
    environment_mode = environment_mode_from_config(config)
    allow_control = environment_allow_control_from_config(config)
    if environment_mode == "standalone":
        environment = StandaloneEnvironmentController(
            temperature_k=float(env_cfg.get("initial_temperature_k", 300.0)),
            field_t=float(env_cfg.get("initial_field_t", 0.0)),
        )
    else:
        if mock or env_cfg.get("kind", "mock") == "mock":
            backend = MockEnvironmentController()
        else:
            backend = TeslatronClient.from_config(config)
        if environment_mode == "async-poll" or not allow_control:
            environment = ReadOnlyEnvironmentController(backend=backend, mode=environment_mode)
        else:
            environment = backend
    return m81, matrix, environment
