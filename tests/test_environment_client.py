import pytest

from electrical_measurements.exceptions import EnvironmentControlError
from electrical_measurements.instruments.teslatron_client import (
    ReadOnlyEnvironmentController,
    StandaloneEnvironmentController,
    TeslatronClient,
)


class FakeTeslatronClient(TeslatronClient):
    def __init__(self) -> None:
        super().__init__(endpoint="http://fake.local", poll_interval_s=0.0)
        self.requests: list[tuple[str, str, dict | None]] = []
        self._temperature_reads = iter([299.0, 299.8, 300.0])
        self._field_reads = iter([0.0, 0.05, 0.1])

    def _request_json(self, path: str, method: str = "GET", payload: dict | None = None):
        self.requests.append((path, method, payload))
        if path == self.state_path:
            return {
                "environment": {
                    "temperature_k": next(self._temperature_reads, 300.0),
                    "field_t": next(self._field_reads, 0.1),
                }
            }
        return {"ok": True}


def test_teslatron_client_reads_nested_state():
    client = FakeTeslatronClient()
    assert client.read_temperature() == 299.0
    assert client.read_field() == 0.05


def test_teslatron_client_waits_until_stable_and_posts_commands():
    client = FakeTeslatronClient()
    client.set_temperature(300.0)
    client.wait_temperature_stable(300.0, tolerance=0.01, timeout=1.0)
    client.start_field_ramp(1.0, 60.0)
    client.stop_field_ramp()
    assert ("/temperature/set", "POST", {"target_k": 300.0}) in client.requests
    assert ("/field/ramp/start", "POST", {"target_t": 1.0, "rate_t_per_min": 60.0}) in client.requests


def test_standalone_environment_tracks_local_state():
    environment = StandaloneEnvironmentController(temperature_k=295.0, field_t=0.2)
    environment.set_temperature(301.0)
    environment.set_field(0.5)
    assert environment.read_temperature() == 301.0
    assert environment.read_field() == 0.5
    snapshot = environment.status_snapshot()
    assert snapshot["mode"] == "standalone"
    assert snapshot["control_enabled"] is False


def test_async_poll_environment_is_read_only():
    backend = FakeTeslatronClient()
    environment = ReadOnlyEnvironmentController(backend=backend)
    assert environment.read_temperature() == 299.0
    assert environment.read_field() == 0.05
    with pytest.raises(EnvironmentControlError):
        environment.set_temperature(300.0)
