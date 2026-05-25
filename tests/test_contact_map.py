import pytest

from electrical_measurements.exceptions import ContactMapError
from electrical_measurements.switching.contact_map import ContactMap


def test_contact_map_parsing_and_generated_relays():
    contact_map = ContactMap.from_yaml("configs/contact_maps/vdp_4contacts_7709.yaml")
    state = contact_map.get_state("I_AB_V_CD")
    assert contact_map.name == "vdp_4contacts_7709"
    assert state["current"] == ["A", "B"]
    assert state["relay_channels"] == [17, 26, 35, 44]


def test_contact_map_reciprocal_and_group_lookup():
    contact_map = ContactMap.from_yaml("configs/contact_maps/vdp_4contacts_7709.yaml")
    reciprocal = contact_map.get_reciprocal_state("I_AB_V_CD")
    assert reciprocal is not None
    assert reciprocal["current"] == ["C", "D"]
    assert "I_AB_V_CD" in contact_map.get_states_for_group("vdp")


def test_contact_map_extracts_instrument_channels():
    contact_map = ContactMap.from_yaml("configs/contact_maps/hallbar_6contacts_7709.yaml")
    assert contact_map.instrument_channel("current_source") == "S1"
    assert contact_map.instrument_channel("vxx_meter") == "M1"
    assert contact_map.instrument_channel("vxy_meter") == "M2"


def test_vdp_contact_map_exposes_new_clockwise_states():
    contact_map = ContactMap.from_yaml("configs/contact_maps/vdp_4contacts_7709.yaml")
    assert contact_map.get_state("I_AB_V_DC")["voltage"] == ["D", "C"]
    assert contact_map.get_state("I_BC_V_AD")["voltage"] == ["A", "D"]
    assert contact_map.get_reciprocal_state("I_AB_V_DC") == contact_map.get_state("I_DC_V_AB")


def test_static_hallbar_contact_map_keeps_empty_relays():
    contact_map = ContactMap.from_yaml("configs/contact_maps/hallbar_6contacts_static.yaml")
    state = contact_map.get_state("static_ad_bc_ce")
    assert state["relay_channels"] == []
    assert state["source"]["S1"] == ["A", "D"]
    assert state["measures"]["M2"] == ["C", "E"]


def test_contact_map_validate_raises_domain_exception_for_missing_name(tmp_path):
    path = tmp_path / "invalid_contact_map.yaml"
    path.write_text(
        "contacts:\n"
        "  A:\n"
        "    column: 1\n"
        "states:\n"
        "  state_1:\n"
        "    relay_channels: [1, 2]\n"
    )
    with pytest.raises(ContactMapError, match="define a name"):
        ContactMap.from_yaml(path)
