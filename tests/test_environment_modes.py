from electrical_measurements.runners.run_measurement import build_instruments, resolve_measurement_environment
from electrical_measurements.switching.contact_map import ContactMap


def test_build_instruments_uses_standalone_environment():
    config = {
        "instruments": {
            "m81": {"connection": {"kind": "mock"}},
            "daq6510": {"resource": "MOCK::DAQ6510"},
            "environment": {
                "mode": "standalone",
                "initial_temperature_k": 287.0,
                "initial_field_t": 0.15,
            },
        }
    }
    contact_map = ContactMap.from_yaml("configs/contact_maps/hallbar_6contacts_7709.yaml")
    _m81, _matrix, environment = build_instruments(config, contact_map, mock=True)
    assert environment.read_temperature() == 287.0
    assert environment.read_field() == 0.15


def test_build_instruments_uses_async_poll_wrapper():
    config = {
        "instruments": {
            "m81": {"connection": {"kind": "mock"}},
            "daq6510": {"resource": "MOCK::DAQ6510"},
            "environment": {
                "kind": "mock",
                "mode": "async-poll",
            },
        }
    }
    contact_map = ContactMap.from_yaml("configs/contact_maps/hallbar_6contacts_7709.yaml")
    _m81, _matrix, environment = build_instruments(config, contact_map, mock=True)
    snapshot = environment.status_snapshot()
    assert snapshot["mode"] == "async-poll"
    assert snapshot["control_enabled"] is False


def test_resolve_measurement_environment_prefers_live_reads():
    class StubEnvironment:
        def read_temperature(self):
            return 298.5

        def read_field(self):
            return 0.08

    temperature, field = resolve_measurement_environment(StubEnvironment(), requested_temperature=300.0, requested_field=0.1)
    assert temperature == 298.5
    assert field == 0.08
