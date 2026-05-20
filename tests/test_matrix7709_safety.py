import pytest

from electrical_measurements.instruments.mock import MockM81Controller
from electrical_measurements.switching.contact_map import ContactMap
from electrical_measurements.switching.matrix7709 import Matrix7709
from electrical_measurements.switching.safety import validate_contact_map_state


def test_matrix_blocks_switching_with_active_source():
    config = {"instruments": {"daq6510": {"resource": "MOCK::DAQ6510"}}}
    contact_map = ContactMap.from_yaml("configs/contact_maps/vdp_4contacts_7709.yaml")
    m81 = MockM81Controller()
    matrix = Matrix7709.from_config(config, contact_map=contact_map, m81=m81)
    m81.enable_source("S1")
    with pytest.raises(RuntimeError):
        matrix.apply_state("I_AB_V_CD")


def test_contact_map_state_validation_passes_for_valid_state():
    contact_map = ContactMap.from_yaml("configs/contact_maps/vdp_4contacts_7709.yaml")
    result = validate_contact_map_state(contact_map, "I_AB_V_CD")
    assert result.ok
