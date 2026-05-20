from electrical_measurements.instruments.mock import MockEnvironmentController, MockM81Controller
from electrical_measurements.switching.contact_map import ContactMap
from electrical_measurements.switching.matrix7709 import Matrix7709


def test_mock_status_snapshots_include_live_fields():
    m81 = MockM81Controller()
    env = MockEnvironmentController(temperature_k=295.0, field_t=0.25)
    m81.enable_source("S1")
    m81_snapshot = m81.status_snapshot()
    env_snapshot = env.status_snapshot()
    assert m81_snapshot["sources"]["S1"]["enabled"] is True
    assert env_snapshot["temperature_k"] == 295.0
    assert env_snapshot["field_t"] == 0.25


def test_matrix_status_snapshot_reports_closed_channels():
    config = {"instruments": {"daq6510": {"resource": "MOCK::DAQ6510"}}}
    contact_map = ContactMap.from_yaml("configs/contact_maps/hallbar_6contacts_7709.yaml")
    matrix = Matrix7709.from_config(config, contact_map=contact_map, m81=MockM81Controller())
    matrix.close_channels([17, 30])
    snapshot = matrix.status_snapshot()
    assert snapshot["closed_channels"] == [17, 30]
