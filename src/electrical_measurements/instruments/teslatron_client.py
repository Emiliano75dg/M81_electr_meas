from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Protocol
from urllib import error, parse, request

from ..exceptions import EnvironmentControlError, InstrumentConnectionError, InstrumentTimeoutError


class EnvironmentController(Protocol):
    """Defines the interface for an environment controller (temperature and field).

    This `Protocol` ensures that any environment controller, whether real,
    mock, or read-only, implements a common set of methods for reading and
    setting temperature and magnetic field.
    """

    def set_temperature(self, target_k: float) -> None: ...
    def wait_temperature_stable(self, target_k: float, tolerance: float = 0.05, timeout: float = 300.0) -> None: ...
    def read_temperature(self) -> float | None: ...
    def set_field(self, target_t: float) -> None: ...
    def wait_field_stable(self, target_t: float, tolerance: float = 1e-4, timeout: float = 300.0) -> None: ...
    def read_field(self) -> float | None: ...
    def start_field_ramp(self, target_t: float, rate_t_per_min: float) -> None: ...
    def stop_field_ramp(self) -> None: ...
    def start_temperature_ramp(self, target_k: float, rate_k_per_min: float) -> None: ...
    def stop_temperature_ramp(self) -> None: ...


@dataclass
class StandaloneEnvironmentController:
    """A dummy environment controller that operates in isolation.

    It does not communicate with any external hardware. It maintains an internal
    state for temperature and field, which can be programmatically modified.
    Useful for tests or for measurements where the environment is not controlled
    by the software.
    """

    temperature_k: float | None = 300.0
    field_t: float | None = 0.0
    mode: str = "standalone"

    def read_temperature(self) -> float | None:
        return self.temperature_k

    def read_field(self) -> float | None:
        return self.field_t

    def set_temperature(self, target_k: float) -> None:
        self.temperature_k = float(target_k)

    def wait_temperature_stable(self, target_k: float, tolerance: float = 0.05, timeout: float = 300.0) -> None:
        self.temperature_k = float(target_k)

    def set_field(self, target_t: float) -> None:
        self.field_t = float(target_t)

    def wait_field_stable(self, target_t: float, tolerance: float = 1e-4, timeout: float = 300.0) -> None:
        self.field_t = float(target_t)

    def start_field_ramp(self, target_t: float, rate_t_per_min: float) -> None:
        self.field_t = float(target_t)

    def stop_field_ramp(self) -> None:
        return None

    def start_temperature_ramp(self, target_k: float, rate_k_per_min: float) -> None:
        self.temperature_k = float(target_k)

    def stop_temperature_ramp(self) -> None:
        return None

    def status_snapshot(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "temperature_k": self.temperature_k,
            "field_t": self.field_t,
            "field_ramp_running": False,
            "temperature_ramp_running": False,
            "control_enabled": False,
        }


@dataclass
class ReadOnlyEnvironmentController:
    """A wrapper for an environment controller that only allows reading.

    This controller delegates temperature and field readings to a real backend,
    but raises a `RuntimeError` if an attempt is made to modify the state.
    It is used for the 'async-poll' mode, where the software monitors the
    environment but does not actively control it.
    """

    backend: Any
    mode: str = "async-poll"

    def _raise_control_disabled(self) -> None:
        raise EnvironmentControlError("Environment control is disabled in async-poll mode")

    def read_temperature(self) -> float | None:
        return self.backend.read_temperature()

    def read_field(self) -> float | None:
        return self.backend.read_field()

    def set_temperature(self, target_k: float) -> None:
        self._raise_control_disabled()

    def wait_temperature_stable(self, target_k: float, tolerance: float = 0.05, timeout: float = 300.0) -> None:
        self._raise_control_disabled()

    def set_field(self, target_t: float) -> None:
        self._raise_control_disabled()

    def wait_field_stable(self, target_t: float, tolerance: float = 1e-4, timeout: float = 300.0) -> None:
        self._raise_control_disabled()

    def start_field_ramp(self, target_t: float, rate_t_per_min: float) -> None:
        self._raise_control_disabled()

    def stop_field_ramp(self) -> None:
        self._raise_control_disabled()

    def start_temperature_ramp(self, target_k: float, rate_k_per_min: float) -> None:
        self._raise_control_disabled()

    def stop_temperature_ramp(self) -> None:
        self._raise_control_disabled()

    def advance_time(self, dt_s: float) -> None:
        if hasattr(self.backend, "advance_time"):
            self.backend.advance_time(dt_s)

    def status_snapshot(self) -> dict[str, Any]:
        snapshot: dict[str, Any]
        if hasattr(self.backend, "status_snapshot"):
            snapshot = dict(self.backend.status_snapshot())
        else:
            snapshot = {
                "temperature_k": self.read_temperature(),
                "field_t": self.read_field(),
            }
        snapshot["mode"] = self.mode
        snapshot["control_enabled"] = False
        return snapshot


def environment_mode_from_config(config: dict[str, Any]) -> str:
    env_cfg = config.get("instruments", {}).get("environment", {})
    return str(env_cfg.get("mode", "integrated")).strip().lower() or "integrated"


def environment_supports_control(environment: Any) -> bool:
    return not isinstance(environment, ReadOnlyEnvironmentController)


def _deep_find_float(payload: Any, keys: tuple[str, ...]) -> float | None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            lowered = key.lower()
            if lowered in keys:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    pass
            nested = _deep_find_float(value, keys)
            if nested is not None:
                return nested
    if isinstance(payload, list):
        for item in payload:
            nested = _deep_find_float(item, keys)
            if nested is not None:
                return nested
    return None


@dataclass
class TeslatronClient:
    """HTTP client to communicate with a server controlling a cryogenic environment.

    It implements the `EnvironmentController` interface by sending HTTP requests
    to configurable endpoints to read and set temperature and magnetic field.
    It also handles ramps and waiting for parameter stabilization.
    """

    endpoint: str = "http://localhost:8000"
    timeout_s: float = 5.0
    poll_interval_s: float = 0.5
    state_path: str = "/state"
    set_temperature_path: str = "/temperature/set"
    set_field_path: str = "/field/set"
    wait_temperature_path: str | None = None
    wait_field_path: str | None = None
    start_field_ramp_path: str = "/field/ramp/start"
    stop_field_ramp_path: str = "/field/ramp/stop"
    start_temperature_ramp_path: str = "/temperature/ramp/start"
    stop_temperature_ramp_path: str = "/temperature/ramp/stop"
    extra_headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "TeslatronClient":
        """Creates a client instance from the project configuration.

        Args:
            config: The complete project configuration dictionary.

        Returns:
            A configured TeslatronClient instance.
        """
        env_cfg = config.get("instruments", {}).get("environment", {})
        return cls(
            endpoint=env_cfg.get("endpoint", "http://localhost:8000"),
            timeout_s=float(env_cfg.get("timeout_s", 5.0)),
            poll_interval_s=float(env_cfg.get("poll_interval_s", 0.5)),
            state_path=str(env_cfg.get("state_path", "/state")),
            set_temperature_path=str(env_cfg.get("set_temperature_path", "/temperature/set")),
            set_field_path=str(env_cfg.get("set_field_path", "/field/set")),
            wait_temperature_path=env_cfg.get("wait_temperature_path"),
            wait_field_path=env_cfg.get("wait_field_path"),
            start_field_ramp_path=str(env_cfg.get("start_field_ramp_path", "/field/ramp/start")),
            stop_field_ramp_path=str(env_cfg.get("stop_field_ramp_path", "/field/ramp/stop")),
            start_temperature_ramp_path=str(env_cfg.get("start_temperature_ramp_path", "/temperature/ramp/start")),
            stop_temperature_ramp_path=str(env_cfg.get("stop_temperature_ramp_path", "/temperature/ramp/stop")),
            extra_headers=dict(env_cfg.get("headers", {})),
        )

    def _url(self, path: str) -> str:
        return parse.urljoin(self.endpoint.rstrip("/") + "/", path.lstrip("/"))

    def _request_json(self, path: str, method: str = "GET", payload: dict[str, Any] | None = None) -> Any:
        """Executes an HTTP request to an endpoint and returns its body as JSON.

        Args:
            path: The endpoint path (e.g., "/state").
            method: The HTTP method (e.g., "GET", "POST").
            payload: The request body (for methods like POST).

        Returns:
            The JSON-decoded response body.

        Raises:
            InstrumentTimeoutError: If the request times out.
            InstrumentConnectionError: If the connection fails or the response
                is not valid JSON.
        """
        data = None
        headers = {"Content-Type": "application/json", **self.extra_headers}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
        req = request.Request(self._url(path), data=data, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=self.timeout_s) as response:
                body = response.read().decode("utf-8").strip()
        except TimeoutError as e:
            raise InstrumentTimeoutError(f"Request to {self.endpoint}{path} timed out after {self.timeout_s}s") from e
        except error.URLError as e:
            raise InstrumentConnectionError(f"Failed to connect to environment controller at {self.endpoint}{path}: {e.reason}") from e
        if not body:
            return {}
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            raise InstrumentConnectionError(f"Invalid JSON response from {self.endpoint}{path}: {body}") from e

    def _wait_until(self, read_value, target: float, tolerance: float, timeout: float) -> None:
        import time

        deadline = time.monotonic() + timeout
        while True:
            value = read_value()
            if value is not None and abs(value - target) <= tolerance:
                return
            if time.monotonic() >= deadline:
                raise InstrumentTimeoutError(f"Environment did not reach target {target} within {timeout} s")
            time.sleep(self.poll_interval_s)

    def read_state(self) -> dict[str, Any]:
        """Reads the complete environment state from the `state_path` endpoint.

        Returns:
            A dictionary with the current environment state.
        """
        payload = self._request_json(self.state_path, method="GET")
        return payload if isinstance(payload, dict) else {"value": payload}

    def read_temperature(self) -> float | None:
        """Reads the current temperature (in Kelvin) from the environment.

        Extracts the temperature value from the `read_state` JSON response.

        Returns:
            The temperature in Kelvin, or None if not found.
        """
        payload = self.read_state()
        return _deep_find_float(payload, ("temperature_k", "temperature", "temp_k", "temp"))

    def read_field(self) -> float | None:
        """Reads the current magnetic field (in Tesla) from the environment.

        Extracts the field value from the `read_state` JSON response.

        Returns:
            The magnetic field in Tesla, or None if not found.
        """
        payload = self.read_state()
        return _deep_find_float(payload, ("field_t", "field", "magnetic_field_t", "b_t"))

    def set_temperature(self, target_k: float) -> None:
        """Sets a new target temperature.

        Args:
            target_k: The desired temperature in Kelvin.
        """
        self._request_json(self.set_temperature_path, method="POST", payload={"target_k": target_k})

    def wait_temperature_stable(self, target_k: float, tolerance: float = 0.05, timeout: float = 300.0) -> None:
        """Waits for the temperature to stabilize at the target value.

        If `wait_temperature_path` is configured, it delegates the wait to the
        server. Otherwise, it performs local polling until the temperature is
        within the specified tolerance.

        Args:
            target_k: The target temperature to reach.
            tolerance: The allowed tolerance to consider the temperature stable.
            timeout: The maximum waiting time in seconds.

        Raises:
            InstrumentTimeoutError: If the temperature does not stabilize within the timeout.
        """
        if self.wait_temperature_path:
            self._request_json(
                self.wait_temperature_path,
                method="POST",
                payload={"target_k": target_k, "tolerance": tolerance, "timeout": timeout},
            )
            return
        self._wait_until(self.read_temperature, target_k, tolerance, timeout)

    def set_field(self, target_t: float) -> None:
        """Sets a new target magnetic field.

        Args:
            target_t: The desired magnetic field in Tesla.
        """
        self._request_json(self.set_field_path, method="POST", payload={"target_t": target_t})

    def wait_field_stable(self, target_t: float, tolerance: float = 1e-4, timeout: float = 300.0) -> None:
        """Waits for the magnetic field to stabilize at the target value.

        If `wait_field_path` is configured, it delegates the wait to the server.
        Otherwise, it performs local polling until the field is within the
        specified tolerance.

        Args:
            target_t: The target magnetic field to reach.
            tolerance: The allowed tolerance to consider the field stable.
            timeout: The maximum waiting time in seconds.

        Raises:
            InstrumentTimeoutError: If the field does not stabilize within the timeout.
        """
        if self.wait_field_path:
            self._request_json(
                self.wait_field_path,
                method="POST",
                payload={"target_t": target_t, "tolerance": tolerance, "timeout": timeout},
            )
            return
        self._wait_until(self.read_field, target_t, tolerance, timeout)

    def start_field_ramp(self, target_t: float, rate_t_per_min: float) -> None:
        self._request_json(
            self.start_field_ramp_path,
            method="POST",
            payload={"target_t": target_t, "rate_t_per_min": rate_t_per_min},
        )

    def stop_field_ramp(self) -> None:
        self._request_json(self.stop_field_ramp_path, method="POST", payload={})

    def start_temperature_ramp(self, target_k: float, rate_k_per_min: float) -> None:
        self._request_json(
            self.start_temperature_ramp_path,
            method="POST",
            payload={"target_k": target_k, "rate_k_per_min": rate_k_per_min},
        )

    def stop_temperature_ramp(self) -> None:
        self._request_json(self.stop_temperature_ramp_path, method="POST", payload={})
