from electrical_measurements.protocols.hall import HallProtocol
from electrical_measurements.protocols.magnetoresistance import MagnetoresistanceProtocol
from electrical_measurements.protocols.reciprocity import ReciprocityProtocol
from electrical_measurements.protocols.second_harmonic import SecondHarmonicProtocol
from electrical_measurements.protocols.vdp_hall import VanDerPauwHallProtocol
from electrical_measurements.protocols.vanderpauw import VanDerPauwProtocol
from electrical_measurements.switching.contact_map import ContactMap


def test_vanderpauw_default_sequence_builds_ac_steps():
    contact_map = ContactMap.from_yaml("configs/contact_maps/vdp_4contacts_7709.yaml")
    sequence = VanDerPauwProtocol.default_sequence(contact_map=contact_map)
    assert sequence.defaults.excitation_mode == "ac"
    assert len(sequence.steps) == 4
    assert sequence.steps[0].outputs == {"M1": {"name": "i_ab_v_cd_ohm", "transform": "lockin_x_over_current"}}


def test_vdp_hall_default_sequence_can_include_reciprocity():
    contact_map = ContactMap.from_yaml("configs/contact_maps/vdp_4contacts_7709.yaml")
    sequence = VanDerPauwHallProtocol.default_sequence(contact_map=contact_map, include_reciprocity=True)
    reciprocal_steps = [step for step in sequence.steps if step.reciprocal_step_of]
    assert reciprocal_steps
    assert reciprocal_steps[0].reciprocal_step_of is not None


def test_reciprocity_default_sequence_pairs_each_state_with_its_reciprocal():
    contact_map = ContactMap.from_yaml("configs/contact_maps/vdp_4contacts_7709.yaml")
    sequence = ReciprocityProtocol.default_sequence(contact_map=contact_map, states=["I_AB_V_CD"])
    assert [step.state for step in sequence.steps] == ["I_AB_V_CD", "I_CD_V_AB"]
    assert sequence.steps[1].reciprocal_step_of == "measure_i_ab_v_cd"


def test_hall_default_sequence_uses_transverse_and_longitudinal_channels():
    contact_map = ContactMap.from_yaml("configs/contact_maps/hallbar_6contacts_7709.yaml")
    sequence = HallProtocol.default_sequence(contact_map=contact_map)
    assert sequence.defaults.measure_channels == ["M2", "M1"]
    assert sequence.steps[0].outputs["M2"]["name"] == "rxy_ohm"
    assert sequence.steps[0].outputs["M1"]["name"] == "rxx_ohm"


def test_magnetoresistance_default_sequence_can_include_reverse_current_state():
    contact_map = ContactMap.from_yaml("configs/contact_maps/hallbar_6contacts_7709.yaml")
    sequence = MagnetoresistanceProtocol.default_sequence(
        contact_map=contact_map,
        state="hallbar_forward",
        current_rms_a=1e-5,
        frequency_hz=13.7,
        reverse_current=True,
    )
    assert [step.name for step in sequence.steps] == ["mr_forward", "mr_reverse"]
    assert sequence.steps[1].reciprocal_step_of == "mr_forward"


def test_second_harmonic_default_sequence_uses_second_harmonic_output():
    contact_map = ContactMap.from_yaml("configs/contact_maps/second_harmonic_7709.yaml")
    sequence = SecondHarmonicProtocol.default_sequence(
        contact_map=contact_map,
        state="second_harmonic_vxy",
        current_rms_a=1e-5,
        frequency_hz=13.7,
    )
    assert sequence.defaults.harmonic == 2
    assert sequence.steps[0].outputs["M2"]["name"] == "r2w_ohm"
