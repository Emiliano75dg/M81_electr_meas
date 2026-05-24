from __future__ import annotations

from datetime import datetime, timezone

import pytest

from electrical_measurements.runners import legacy_runner
from electrical_measurements.runners.run_measurement import build_run_namespace
from electrical_measurements.switching.contact_map import ContactMap


class RecordingM81:
    def __init__(self) -> None:
        self.disable_calls = 0
        self.temperature_k = None
        self.field_t = None

    def disable_all_sources(self) -> None:
        self.disable_calls += 1


class RecordingMatrix:
    def __init__(self) -> None:
        self.open_calls = 0

    def open_all(self) -> None:
        self.open_calls += 1


class StubEnvironment:
    def set_temperature(self, target_k: float) -> None:
        return None

    def wait_temperature_stable(self, target_k: float, tolerance: float = 0.05, timeout: float = 300.0) -> None:
        return None

    def read_temperature(self):
        return 300.0

    def set_field(self, target_t: float) -> None:
        return None

    def wait_field_stable(self, target_t: float, tolerance: float = 1e-4, timeout: float = 300.0) -> None:
        return None

    def read_field(self):
        return 0.0


class RaisingProtocol:
    def __init__(self, m81: RecordingM81) -> None:
        self.m81 = m81
        self.setup_calls = 0
        self.teardown_calls = 0
        self.measure_calls = 0

    def setup(self) -> None:
        self.setup_calls += 1

    def teardown(self) -> None:
        self.teardown_calls += 1

    def measure_point(self, temperature_k=None, field_t=None):
        self.measure_calls += 1
        raise RuntimeError("boom")


def test_legacy_runner_teardown_runs_when_measurement_raises(monkeypatch):
    args = build_run_namespace(
        protocol="hall",
        mock=True,
        temperatures="300",
        fields="0",
        current=1e-5,
        frequency=13.7,
        settle=0.0,
    )
    config = {"sample_id": "sample", "output": {"directory": "unused"}}
    contact_map = ContactMap.from_yaml("configs/contact_maps/hallbar_6contacts_7709.yaml")
    m81 = RecordingM81()
    matrix = RecordingMatrix()
    environment = StubEnvironment()
    protocol = RaisingProtocol(m81)

    monkeypatch.setattr(legacy_runner, "build_instruments", lambda *_args, **_kwargs: (m81, matrix, environment))
    monkeypatch.setattr(legacy_runner, "build_protocol", lambda *_args, **_kwargs: protocol)

    with pytest.raises(RuntimeError, match="boom"):
        legacy_runner.run_legacy_protocol_command(
            args,
            config=config,
            contact_map=contact_map,
            started_at=datetime.now(timezone.utc).isoformat(),
        )

    assert protocol.setup_calls == 1
    assert protocol.measure_calls == 1
    assert protocol.teardown_calls == 1
    assert m81.disable_calls >= 1
    assert matrix.open_calls >= 1
