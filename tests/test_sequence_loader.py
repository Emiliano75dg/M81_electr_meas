from pathlib import Path

import pytest
import yaml

from electrical_measurements.exceptions import SequenceValidationError
from electrical_measurements.sequences import (
    load_measurement_sequence,
    measurement_sequence_from_dict,
    measurement_sequence_to_dict,
    save_measurement_sequence,
)


def test_load_valid_ac_sequence():
    sequence = load_measurement_sequence("configs/sequences/vdp_ac_reciprocity.yaml")
    assert sequence.name == "vdp_ac_reciprocity"
    assert sequence.defaults.excitation_mode == "ac"
    assert len(sequence.steps) == 4


def test_load_valid_dc_sequence():
    sequence = load_measurement_sequence("configs/sequences/vdp_dc_reverse_bias.yaml")
    assert sequence.name == "vdp_dc_reverse_bias"
    assert sequence.defaults.source_mode == "dc"
    assert sequence.steps[0].bias_polarity == 1


def test_reject_missing_name(tmp_path: Path):
    path = tmp_path / "missing_name.yaml"
    path.write_text(yaml.safe_dump({"defaults": {"excitation_mode": "ac"}, "steps": [{"name": "a", "state": "b"}]}))
    with pytest.raises(SequenceValidationError, match="define name"):
        load_measurement_sequence(path)


def test_reject_missing_defaults(tmp_path: Path):
    path = tmp_path / "missing_defaults.yaml"
    path.write_text(yaml.safe_dump({"name": "demo", "steps": [{"name": "a", "state": "b"}]}))
    with pytest.raises(SequenceValidationError, match="define defaults"):
        load_measurement_sequence(path)


def test_reject_empty_steps(tmp_path: Path):
    path = tmp_path / "empty_steps.yaml"
    path.write_text(yaml.safe_dump({"name": "demo", "defaults": {"excitation_mode": "ac"}, "steps": []}))
    with pytest.raises(SequenceValidationError, match="at least one step"):
        load_measurement_sequence(path)


def test_reject_duplicate_step_names(tmp_path: Path):
    path = tmp_path / "dupes.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "name": "demo",
                "defaults": {"excitation_mode": "ac"},
                "steps": [
                    {"name": "dup", "state": "a"},
                    {"name": "dup", "state": "b"},
                ],
            }
        )
    )
    with pytest.raises(SequenceValidationError, match="Duplicate step names"):
        load_measurement_sequence(path)


def test_reject_unknown_fields_strict(tmp_path: Path):
    path = tmp_path / "unknown.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "name": "demo",
                "defaults": {"excitation_mode": "ac", "mystery": True},
                "steps": [{"name": "a", "state": "b"}],
            }
        )
    )
    with pytest.raises(SequenceValidationError, match="Unknown fields"):
        load_measurement_sequence(path)


def test_roundtrip_sequence_dict_conversion():
    original = load_measurement_sequence("configs/sequences/hallbar_ac_rxx_rxy.yaml")
    payload = measurement_sequence_to_dict(original)
    rebuilt = measurement_sequence_from_dict(payload)
    assert rebuilt.name == original.name
    assert rebuilt.defaults.measure_channels == ["M1", "M2"]
    assert rebuilt.steps[0].outputs == {
        "M1": {"name": "rxx_ohm", "transform": "lockin_x_over_current"},
        "M2": {"name": "rxy_ohm", "transform": "lockin_x_over_current"},
    }


def test_load_sequence_from_top_level_wrapper():
    wrapped = {
        "sequence": {
            "name": "wrapped",
            "defaults": {"source_mode": "ac", "source": "S1", "measure_channel": "M1", "source_value": 1e-5, "frequency_hz": 13.7, "harmonic": 1},
            "steps": [{"name": "a", "state": "I_AB_V_CD"}],
        }
    }
    sequence = measurement_sequence_from_dict(wrapped)
    assert sequence.name == "wrapped"
    assert sequence.defaults.source_mode == "ac"


def test_save_and_reload_roundtrip(tmp_path: Path):
    original = load_measurement_sequence("configs/sequences/hallbar_ac_rxx_rxy.yaml")
    path = tmp_path / "roundtrip.yaml"
    save_measurement_sequence(original, path)
    reloaded = load_measurement_sequence(path)
    assert measurement_sequence_to_dict(reloaded) == measurement_sequence_to_dict(original)


def test_load_new_sequence_files():
    for path in [
        "configs/sequences/vdp_full_ac.yaml",
        "configs/sequences/vdp_full_dc.yaml",
        "configs/sequences/vdp_fast_hall_ac.yaml",
        "configs/sequences/vdp_hall_second_harmonic_ac.yaml",
        "configs/sequences/vdp_reciprocity_check.yaml",
        "configs/sequences/vdp_hall_with_drift_guard.yaml",
        "configs/sequences/vdp_lockin_phase_diagnostic.yaml",
        "configs/sequences/hallbar_static_1w_2w.yaml",
        "configs/sequences/hallbar_static_fast.yaml",
        "configs/sequences/hallbar_static_drift_guard.yaml",
        "configs/sequences/second_harmonic_frequency_check.yaml",
    ]:
        sequence = load_measurement_sequence(path)
        assert sequence.steps


def test_roundtrip_sequence_with_measure_specs():
    original = load_measurement_sequence("configs/sequences/vdp_hall_second_harmonic_ac.yaml")
    payload = measurement_sequence_to_dict(original)
    rebuilt = measurement_sequence_from_dict(payload)
    assert rebuilt.steps[0].measure_specs is not None
    assert rebuilt.steps[0].measure_specs["M2"].harmonic == 2


def test_sequence_with_repeat_of_roundtrips():
    original = load_measurement_sequence("configs/sequences/vdp_hall_with_drift_guard.yaml")
    payload = measurement_sequence_to_dict(original)
    rebuilt = measurement_sequence_from_dict(payload)
    assert rebuilt.steps[-1].repeat_of == "ab_dc"
