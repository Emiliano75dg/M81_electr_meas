import pytest

from electrical_measurements.instruments.mock import MockM81Controller
from electrical_measurements.io.dataset import measurement_points_to_dataframe
from electrical_measurements.switching.contact_map import ContactMap
from electrical_measurements.switching.matrix7709 import Matrix7709, SafeMeasurementSession
from electrical_measurements.protocols.hall import HallProtocol
from electrical_measurements.protocols.magnetoresistance import MagnetoresistanceProtocol


def test_mock_protocol_and_emergency_stop():
    config = {"instruments": {"daq6510": {"resource": "MOCK::DAQ6510"}}}
    contact_map = ContactMap.from_yaml("configs/contact_maps/hallbar_6contacts_7709.yaml")
    m81 = MockM81Controller()
    matrix = Matrix7709.from_config(config, contact_map=contact_map, m81=m81)
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
    protocol.setup()
    with SafeMeasurementSession(m81, matrix):
        point = protocol.measure_point(temperature_k=300.0, field_t=0.0)
    assert point.derived["rxx_ohm"] is not None
    protocol.emergency_stop()
    assert matrix.closed_channels == set()
    assert not m81.any_source_enabled()


def test_mock_hall_protocol_generates_density_columns():
    config = {"instruments": {"daq6510": {"resource": "MOCK::DAQ6510"}}}
    contact_map = ContactMap.from_yaml("configs/contact_maps/hallbar_6contacts_7709.yaml")
    m81 = MockM81Controller()
    matrix = Matrix7709.from_config(config, contact_map=contact_map, m81=m81)
    protocol = HallProtocol(
        m81=m81,
        matrix=matrix,
        contact_map=contact_map,
        sample_id="sample",
        settle_s=0.0,
        current_rms_a=10e-6,
        frequency_hz=13.7,
    )
    protocol.setup()
    with SafeMeasurementSession(m81, matrix):
        p1 = protocol.measure_point(temperature_k=300.0, field_t=-1.0)
        m81.field_t = 1.0
        p2 = protocol.measure_point(temperature_k=300.0, field_t=1.0)
    df = measurement_points_to_dataframe([p1, p2])
    assert "hall_density_m2" in df.columns
    assert df["rxx_ohm"].notna().all()
    assert df["rxy_ohm"].notna().all()
    assert p1.metadata["longitudinal_measure_channel"] == "M1"
    assert p1.metadata["transverse_measure_channel"] == "M2"


def test_protocol_setup_rejects_invalid_frequency():
    config = {"instruments": {"daq6510": {"resource": "MOCK::DAQ6510"}}}
    contact_map = ContactMap.from_yaml("configs/contact_maps/hallbar_6contacts_7709.yaml")
    m81 = MockM81Controller()
    matrix = Matrix7709.from_config(config, contact_map=contact_map, m81=m81)
    protocol = HallProtocol(
        m81=m81,
        matrix=matrix,
        contact_map=contact_map,
        sample_id="sample",
        settle_s=0.0,
        current_rms_a=10e-6,
        frequency_hz=0.0,
    )
    with pytest.raises(ValueError, match="frequency_hz"):
        protocol.setup()
