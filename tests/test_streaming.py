import argparse

from electrical_measurements.instruments.mock import MockEnvironmentController, MockM81Controller
from electrical_measurements.protocols.magnetoresistance import MagnetoresistanceProtocol
from electrical_measurements.runners.run_measurement import run_stream_ramp_command, synchronize_trace_and_environment
from electrical_measurements.switching.contact_map import ContactMap
from electrical_measurements.switching.matrix7709 import Matrix7709


def test_synchronize_trace_and_environment_merges_field_and_temperature():
    trace_records = [
        {"timestamp": "2026-01-01T00:00:00+00:00", "trace_channel": "M1", "trace_index": 0, "x": 1.0},
        {"timestamp": "2026-01-01T00:00:01+00:00", "trace_channel": "M1", "trace_index": 1, "x": 2.0},
    ]
    environment_records = [
        {"timestamp": "2026-01-01T00:00:00.100000+00:00", "field_t": 0.1, "temperature_k": 300.0},
        {"timestamp": "2026-01-01T00:00:01.100000+00:00", "field_t": 0.2, "temperature_k": 300.1},
    ]
    synced = synchronize_trace_and_environment(trace_records, environment_records, tolerance_s=0.5)
    assert synced["field_t"].tolist() == [0.1, 0.2]
    assert synced["temperature_k"].tolist() == [300.0, 300.1]


def test_stream_ramp_command_mock_writes_stream_dataset(tmp_path):
    config = {
        "sample_id": "sample",
        "instruments": {
            "daq6510": {"resource": "MOCK::DAQ6510"},
            "m81": {"connection": {"kind": "mock"}},
            "environment": {"kind": "mock"},
        },
        "output": {"directory": str(tmp_path)},
    }
    contact_map = ContactMap.from_yaml("configs/contact_maps/hallbar_6contacts_7709.yaml")
    m81 = MockM81Controller()
    matrix = Matrix7709.from_config(config, contact_map=contact_map, m81=m81)
    environment = MockEnvironmentController(field_t=0.0, temperature_k=300.0)
    protocol = MagnetoresistanceProtocol(
        m81=m81,
        matrix=matrix,
        contact_map=contact_map,
        sample_id="sample",
        state="hallbar_forward",
        current_rms_a=10e-6,
        frequency_hz=13.7,
        settle_s=0.0,
    )
    args = argparse.Namespace(
        temperatures="300",
        fields="0",
        stream_samples=5,
        stream_interval=0.01,
        ramp_target=1.0,
        ramp_rate=60.0,
        ramp_quantity="field",
        output=str(tmp_path),
        protocol="hallbar_mr",
    )
    result = run_stream_ramp_command(args, config, contact_map, m81, matrix, environment, protocol)
    assert result == 0
    assert (tmp_path / "hallbar_mr_stream.csv").exists()
    assert environment.field_t > 0.0
