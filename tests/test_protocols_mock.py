import pytest

from electrical_measurements.exceptions import InstrumentConfigError, ProtocolConfigError
from electrical_measurements.instruments.mock import MockM81Controller
from electrical_measurements.io.dataset import measurement_points_to_dataframe
from electrical_measurements.switching.contact_map import ContactMap
from electrical_measurements.switching.matrix7709 import Matrix7709, SafeMeasurementSession
from electrical_measurements.protocols.hall import HallProtocol
from electrical_measurements.protocols.magnetoresistance import MagnetoresistanceProtocol
from electrical_measurements.protocols.vdp_hall import VanDerPauwHallProtocol
from electrical_measurements.protocols.vanderpauw import VanDerPauwProtocol


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
    with pytest.raises(ProtocolConfigError, match="frequency_hz"):
        protocol.setup()


def test_mock_controller_rejects_invalid_measure_mode_with_domain_exception():
    m81 = MockM81Controller()
    with pytest.raises(InstrumentConfigError, match="Unsupported measure mode"):
        m81.set_preferred_measure_mode("M1", "invalid")


def test_mock_controller_loads_per_channel_harmonics_from_config():
    config = {
        "instruments": {
            "m81": {
                "measure_modes": {"M1": "lockin", "M2": "dc"},
                "measure_harmonics": {"M1": 3, "M3": 5},
                "measure_nplc": {"M2": 2.5},
                "measure_time_constants_s": {"M1": 0.7, "M3": 1.2},
                "measure_rolloffs": {"M1": "R12"},
            }
        }
    }
    m81 = MockM81Controller.from_config(config)
    assert m81.get_measure_settings("M1")["preferred_harmonic"] == 3
    assert m81.get_measure_settings("M3")["preferred_harmonic"] == 5
    assert m81.get_measure_settings("M2")["preferred_mode"] == "dc"
    assert m81.get_measure_settings("M2")["preferred_nplc"] == 2.5
    assert m81.get_measure_settings("M1")["preferred_time_constant_s"] == 0.7
    assert m81.get_measure_settings("M1")["preferred_rolloff"] == "R12"


def test_protocol_setup_uses_per_channel_lockin_harmonic():
    config = {"instruments": {"daq6510": {"resource": "MOCK::DAQ6510"}}}
    contact_map = ContactMap.from_yaml("configs/contact_maps/hallbar_6contacts_7709.yaml")
    m81 = MockM81Controller()
    m81.set_preferred_measure_harmonic("M2", 3)
    matrix = Matrix7709.from_config(config, contact_map=contact_map, m81=m81)
    protocol = HallProtocol(
        m81=m81,
        matrix=matrix,
        contact_map=contact_map,
        sample_id="sample",
        settle_s=0.0,
        current_rms_a=10e-6,
        frequency_hz=13.7,
        harmonic=1,
    )
    protocol.setup()
    assert m81.get_measure_settings("M2")["resolved_harmonic"] == 3


def test_protocol_setup_uses_per_channel_dc_nplc():
    config = {"instruments": {"daq6510": {"resource": "MOCK::DAQ6510"}}}
    contact_map = ContactMap.from_yaml("configs/contact_maps/vdp_4contacts_7709.yaml")
    m81 = MockM81Controller()
    m81.set_preferred_measure_mode("M1", "dc")
    m81.set_preferred_measure_nplc("M1", 2.5)
    matrix = Matrix7709.from_config(config, contact_map=contact_map, m81=m81)
    protocol = VanDerPauwProtocol(
        m81=m81,
        matrix=matrix,
        contact_map=contact_map,
        sample_id="sample",
        settle_s=0.0,
        current_rms_a=10e-6,
        frequency_hz=13.7,
    )
    protocol.setup()
    settings = m81.get_measure_settings("M1")
    assert settings["resolved_mode"] == "dc"
    assert settings["nplc"] == 2.5


def test_protocol_setup_uses_per_channel_lockin_time_constant_and_rolloff():
    config = {"instruments": {"daq6510": {"resource": "MOCK::DAQ6510"}}}
    contact_map = ContactMap.from_yaml("configs/contact_maps/hallbar_6contacts_7709.yaml")
    m81 = MockM81Controller()
    m81.set_preferred_measure_time_constant("M2", 0.9)
    m81.set_preferred_measure_rolloff("M2", "R12")
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
    settings = m81.get_measure_settings("M2")
    assert settings["resolved_mode"] == "lockin"
    assert settings["time_constant_s"] == 0.9
    assert settings["rolloff"] == "R12"


def test_vanderpauw_can_run_with_dc_measure_mode():
    config = {"instruments": {"daq6510": {"resource": "MOCK::DAQ6510"}}}
    contact_map = ContactMap.from_yaml("configs/contact_maps/vdp_4contacts_7709.yaml")
    m81 = MockM81Controller()
    m81.set_preferred_measure_mode("M1", "dc")
    matrix = Matrix7709.from_config(config, contact_map=contact_map, m81=m81)
    protocol = VanDerPauwProtocol(
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
        point = protocol.measure_point(temperature_k=300.0, field_t=0.0)
    assert point.derived["sheet_resistance_ohm_sq"] is not None


def test_vdp_hall_can_optionally_include_reciprocity_states():
    config = {"instruments": {"daq6510": {"resource": "MOCK::DAQ6510"}}}
    contact_map = ContactMap.from_yaml("configs/contact_maps/vdp_4contacts_7709.yaml")
    m81 = MockM81Controller()
    matrix = Matrix7709.from_config(config, contact_map=contact_map, m81=m81)
    protocol = VanDerPauwHallProtocol(
        m81=m81,
        matrix=matrix,
        contact_map=contact_map,
        sample_id="sample",
        settle_s=0.0,
        current_rms_a=10e-6,
        frequency_hz=13.7,
        include_reciprocity=True,
    )
    protocol.setup()
    with SafeMeasurementSession(m81, matrix):
        point = protocol.measure_point(temperature_k=300.0, field_t=0.1)
    assert point.derived["rxy_ohm"] is not None
    assert point.metadata["include_reciprocity"] is True
    measured_states = point.metadata["measured_states"]
    assert "I_AB_V_CD" in measured_states
    assert "I_CD_V_AB" in measured_states
    assert point.derived["reciprocity_pairs"]


def test_vanderpauw_can_optionally_include_anisotropy_check():
    config = {"instruments": {"daq6510": {"resource": "MOCK::DAQ6510"}}}
    contact_map = ContactMap.from_yaml("configs/contact_maps/vdp_4contacts_7709.yaml")
    m81 = MockM81Controller()
    matrix = Matrix7709.from_config(config, contact_map=contact_map, m81=m81)
    protocol = VanDerPauwProtocol(
        m81=m81,
        matrix=matrix,
        contact_map=contact_map,
        sample_id="sample",
        settle_s=0.0,
        current_rms_a=10e-6,
        frequency_hz=13.7,
        include_anisotropy=True,
    )
    protocol.setup()
    with SafeMeasurementSession(m81, matrix):
        point = protocol.measure_point(temperature_k=300.0, field_t=0.0)
    assert point.metadata["include_anisotropy"] is True
    assert "anisotropy_ratio" in point.derived
    assert point.derived["family_a_mean_ohm"] is not None
    assert point.derived["family_b_mean_ohm"] is not None
