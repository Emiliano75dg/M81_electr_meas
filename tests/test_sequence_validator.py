import pytest
import yaml

from electrical_measurements.capabilities import InstrumentCapabilities
from electrical_measurements.exceptions import SequenceValidationError
from electrical_measurements.sequences import load_measurement_sequence, resolve_bias_points, validate_measurement_sequence
from electrical_measurements.switching.contact_map import ContactMap


def _contact_map():
    return ContactMap.from_yaml("configs/contact_maps/vdp_4contacts_7709.yaml")


def _write_sequence(tmp_path, payload):
    path = tmp_path / "sequence.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False))
    return load_measurement_sequence(path)


def test_reject_unknown_state(tmp_path):
    sequence = _write_sequence(
        tmp_path,
        {"name": "demo", "defaults": {"excitation_mode": "ac", "source": "S1", "measure_channel": "M1", "current_rms_a": 1e-5, "frequency_hz": 13.7, "harmonic": 1}, "steps": [{"name": "bad", "state": "missing"}]},
    )
    with pytest.raises(SequenceValidationError, match="unknown state"):
        validate_measurement_sequence(sequence, _contact_map())


def test_reject_repeats_lt_1(tmp_path):
    sequence = _write_sequence(
        tmp_path,
        {"name": "demo", "defaults": {"excitation_mode": "ac", "source": "S1", "measure_channel": "M1", "current_rms_a": 1e-5, "frequency_hz": 13.7, "harmonic": 1, "repeats": 0}, "steps": [{"name": "ok", "state": "I_AB_V_CD"}]},
    )
    with pytest.raises(SequenceValidationError, match="repeats"):
        validate_measurement_sequence(sequence, _contact_map())


def test_reject_negative_settle(tmp_path):
    sequence = _write_sequence(
        tmp_path,
        {"name": "demo", "defaults": {"excitation_mode": "ac", "source": "S1", "measure_channel": "M1", "current_rms_a": 1e-5, "frequency_hz": 13.7, "harmonic": 1, "settle_s": -0.1}, "steps": [{"name": "ok", "state": "I_AB_V_CD"}]},
    )
    with pytest.raises(SequenceValidationError, match="settle_s"):
        validate_measurement_sequence(sequence, _contact_map())


def test_reject_invalid_excitation_mode(tmp_path):
    sequence = _write_sequence(
        tmp_path,
        {"name": "demo", "defaults": {"excitation_mode": "pulse", "source": "S1", "measure_channel": "M1"}, "steps": [{"name": "ok", "state": "I_AB_V_CD"}]},
    )
    with pytest.raises(SequenceValidationError, match="dc.*ac"):
        validate_measurement_sequence(sequence, _contact_map())


def test_reject_dc_without_current_a(tmp_path):
    sequence = _write_sequence(
        tmp_path,
        {"name": "demo", "defaults": {"excitation_mode": "dc", "source": "S1", "measure_channel": "M1"}, "steps": [{"name": "ok", "state": "I_AB_V_CD"}]},
    )
    with pytest.raises(SequenceValidationError, match="current_a"):
        validate_measurement_sequence(sequence, _contact_map())


def test_reject_dc_current_non_positive(tmp_path):
    sequence = _write_sequence(
        tmp_path,
        {"name": "demo", "defaults": {"excitation_mode": "dc", "source": "S1", "measure_channel": "M1", "current_a": 0.0}, "steps": [{"name": "ok", "state": "I_AB_V_CD"}]},
    )
    with pytest.raises(SequenceValidationError, match="current_a"):
        validate_measurement_sequence(sequence, _contact_map())


def test_accept_dc_bias_polarity_plus_and_minus():
    contact_map = _contact_map()
    sequence = load_measurement_sequence("configs/sequences/vdp_dc_reverse_bias.yaml")
    resolved = validate_measurement_sequence(sequence, contact_map)
    assert {step.bias_polarity for step in resolved[:2]} == {1, -1}


def test_dc_auto_reverse_generates_two_bias_points(tmp_path):
    sequence = _write_sequence(
        tmp_path,
        {
            "name": "demo",
            "defaults": {"source_mode": "dc", "source": "S1", "measure_channel": "M1", "source_quantity": "current", "source_value": 1e-5, "reverse_policy": "auto"},
            "steps": [{"name": "ok", "state": "I_AB_V_CD"}],
        },
    )
    resolved = validate_measurement_sequence(sequence, _contact_map())
    bias_points = resolve_bias_points(resolved[0])
    assert [point.source_value for point in bias_points] == [1e-05, -1e-05]
    assert len({point.state for point in bias_points}) == 1


def test_ac_auto_reverse_generates_single_bias_point(tmp_path):
    sequence = _write_sequence(
        tmp_path,
        {
            "name": "demo",
            "defaults": {
                "source_mode": "ac",
                "source": "S1",
                "measure_channel": "M1",
                "source_quantity": "current",
                "source_value": 1e-5,
                "frequency_hz": 13.7,
                "harmonic": 1,
                "reverse_policy": "auto",
            },
            "steps": [{"name": "ok", "state": "I_AB_V_CD"}],
        },
    )
    resolved = validate_measurement_sequence(sequence, _contact_map())
    bias_points = resolve_bias_points(resolved[0])
    assert len(bias_points) == 1
    assert bias_points[0].source_value == 1e-05


def test_reject_ac_with_dc_source_inversion(tmp_path):
    sequence = _write_sequence(
        tmp_path,
        {
            "name": "demo",
            "defaults": {
                "source_mode": "ac",
                "source": "S1",
                "measure_channel": "M1",
                "source_quantity": "current",
                "source_value": 1e-5,
                "frequency_hz": 13.7,
                "harmonic": 1,
                "reverse_policy": "dc_source_inversion",
            },
            "steps": [{"name": "bad", "state": "I_AB_V_CD"}],
        },
    )
    with pytest.raises(SequenceValidationError, match="dc_source_inversion"):
        validate_measurement_sequence(sequence, _contact_map())


def test_reject_dc_bias_polarity_other_values(tmp_path):
    sequence = _write_sequence(
        tmp_path,
        {"name": "demo", "defaults": {"excitation_mode": "dc", "source": "S1", "measure_channel": "M1", "current_a": 1e-5}, "steps": [{"name": "ok", "state": "I_AB_V_CD", "bias_polarity": 0}]},
    )
    with pytest.raises(SequenceValidationError, match="bias_polarity"):
        validate_measurement_sequence(sequence, _contact_map())


def test_reject_ac_without_current_rms_a(tmp_path):
    sequence = _write_sequence(
        tmp_path,
        {"name": "demo", "defaults": {"excitation_mode": "ac", "source": "S1", "measure_channel": "M1", "frequency_hz": 13.7, "harmonic": 1}, "steps": [{"name": "ok", "state": "I_AB_V_CD"}]},
    )
    with pytest.raises(SequenceValidationError, match="current_rms_a"):
        validate_measurement_sequence(sequence, _contact_map())


def test_reject_ac_without_frequency_hz(tmp_path):
    sequence = _write_sequence(
        tmp_path,
        {"name": "demo", "defaults": {"excitation_mode": "ac", "source": "S1", "measure_channel": "M1", "current_rms_a": 1e-5, "harmonic": 1}, "steps": [{"name": "ok", "state": "I_AB_V_CD"}]},
    )
    with pytest.raises(SequenceValidationError, match="frequency_hz"):
        validate_measurement_sequence(sequence, _contact_map())


def test_reject_ac_without_harmonic(tmp_path):
    sequence = _write_sequence(
        tmp_path,
        {"name": "demo", "defaults": {"excitation_mode": "ac", "source": "S1", "measure_channel": "M1", "current_rms_a": 1e-5, "frequency_hz": 13.7}, "steps": [{"name": "ok", "state": "I_AB_V_CD"}]},
    )
    with pytest.raises(SequenceValidationError, match="harmonic"):
        validate_measurement_sequence(sequence, _contact_map())


def test_reject_ac_current_non_positive(tmp_path):
    sequence = _write_sequence(
        tmp_path,
        {"name": "demo", "defaults": {"excitation_mode": "ac", "source": "S1", "measure_channel": "M1", "current_rms_a": 0.0, "frequency_hz": 13.7, "harmonic": 1}, "steps": [{"name": "ok", "state": "I_AB_V_CD"}]},
    )
    with pytest.raises(SequenceValidationError, match="current_rms_a"):
        validate_measurement_sequence(sequence, _contact_map())


def test_reject_ac_frequency_non_positive(tmp_path):
    sequence = _write_sequence(
        tmp_path,
        {"name": "demo", "defaults": {"excitation_mode": "ac", "source": "S1", "measure_channel": "M1", "current_rms_a": 1e-5, "frequency_hz": 0.0, "harmonic": 1}, "steps": [{"name": "ok", "state": "I_AB_V_CD"}]},
    )
    with pytest.raises(SequenceValidationError, match="frequency_hz"):
        validate_measurement_sequence(sequence, _contact_map())


def test_reject_ac_harmonic_lt_1(tmp_path):
    sequence = _write_sequence(
        tmp_path,
        {"name": "demo", "defaults": {"excitation_mode": "ac", "source": "S1", "measure_channel": "M1", "current_rms_a": 1e-5, "frequency_hz": 13.7, "harmonic": 0}, "steps": [{"name": "ok", "state": "I_AB_V_CD"}]},
    )
    with pytest.raises(SequenceValidationError, match="harmonic"):
        validate_measurement_sequence(sequence, _contact_map())


def test_reject_bias_polarity_in_ac_by_default(tmp_path):
    sequence = _write_sequence(
        tmp_path,
        {"name": "demo", "defaults": {"excitation_mode": "ac", "source": "S1", "measure_channel": "M1", "current_rms_a": 1e-5, "frequency_hz": 13.7, "harmonic": 1}, "steps": [{"name": "ok", "state": "I_AB_V_CD", "bias_polarity": -1}]},
    )
    with pytest.raises(SequenceValidationError, match="bias_polarity"):
        validate_measurement_sequence(sequence, _contact_map())


def test_step_level_overrides_work(tmp_path):
    sequence = _write_sequence(
        tmp_path,
        {
            "name": "demo",
            "defaults": {"excitation_mode": "ac", "source": "S1", "measure_channel": "M1", "current_rms_a": 1e-5, "frequency_hz": 13.7, "harmonic": 1, "repeats": 1},
            "steps": [{"name": "override", "state": "I_AB_V_CD", "measure_channels": ["M1", "M2"], "harmonic": 2, "repeats": 3}],
        },
    )
    resolved = validate_measurement_sequence(sequence, _contact_map())
    assert resolved[0].measure_channels == ("M1", "M2")
    assert resolved[0].harmonic == 2
    assert resolved[0].repeats == 3


def test_measure_specs_allow_mixed_harmonics_in_ac_step():
    sequence = load_measurement_sequence("configs/sequences/vdp_hall_second_harmonic_ac.yaml")
    resolved = validate_measurement_sequence(sequence, _contact_map())
    first = resolved[0]
    assert first.measure_specs["M1"].harmonic == 1
    assert first.measure_specs["M2"].harmonic == 2
    assert first.measure_channels == ("M1", "M2")


def test_reject_dc_measure_specs_with_harmonic(tmp_path):
    sequence = _write_sequence(
        tmp_path,
        {
            "name": "dc_bad_specs",
            "defaults": {"source_mode": "dc", "source": "S1", "measure_channel": "M1", "source_value": 1e-5},
            "steps": [
                {
                    "name": "bad",
                    "state": "I_AB_V_CD",
                    "measure_specs": {
                        "M1": {
                            "measure_mode": "dc",
                            "harmonic": 2,
                            "readout": "value",
                            "output": "raw_value",
                            "transform": "raw",
                        }
                    },
                }
            ],
        },
    )
    with pytest.raises(SequenceValidationError, match="incompatible with dc source_mode"):
        validate_measurement_sequence(sequence, _contact_map())


def test_new_standard_sequence_files_validate():
    cases = [
        ("configs/sequences/vdp_full_ac.yaml", "configs/contact_maps/vdp_4contacts_7709.yaml", 6),
        ("configs/sequences/vdp_full_dc.yaml", "configs/contact_maps/vdp_4contacts_7709.yaml", 6),
        ("configs/sequences/vdp_fast_hall_ac.yaml", "configs/contact_maps/vdp_4contacts_7709.yaml", 4),
        ("configs/sequences/vdp_hall_second_harmonic_ac.yaml", "configs/contact_maps/vdp_4contacts_7709.yaml", 4),
        ("configs/sequences/vdp_reciprocity_check.yaml", "configs/contact_maps/vdp_4contacts_7709.yaml", 4),
        ("configs/sequences/vdp_hall_with_drift_guard.yaml", "configs/contact_maps/vdp_4contacts_7709.yaml", 5),
        ("configs/sequences/vdp_lockin_phase_diagnostic.yaml", "configs/contact_maps/vdp_4contacts_7709.yaml", 2),
        ("configs/sequences/hallbar_static_1w_2w.yaml", "configs/contact_maps/hallbar_6contacts_static.yaml", 2),
        ("configs/sequences/hallbar_static_fast.yaml", "configs/contact_maps/hallbar_6contacts_static.yaml", 1),
        ("configs/sequences/hallbar_static_drift_guard.yaml", "configs/contact_maps/hallbar_6contacts_static.yaml", 3),
        ("configs/sequences/second_harmonic_frequency_check.yaml", "configs/contact_maps/vdp_4contacts_7709.yaml", 3),
    ]
    for sequence_path, contact_map_path, expected_steps in cases:
        resolved = validate_measurement_sequence(
            load_measurement_sequence(sequence_path),
            ContactMap.from_yaml(contact_map_path),
        )
        assert len(resolved) == expected_steps


def test_phase_diagnostic_accepts_multi_readout_string():
    sequence = load_measurement_sequence("configs/sequences/vdp_lockin_phase_diagnostic.yaml")
    resolved = validate_measurement_sequence(sequence, _contact_map())
    assert resolved[0].readout == "x,y,r,theta"


def test_invalid_channel_rejected_via_capabilities(tmp_path):
    sequence = _write_sequence(
        tmp_path,
        {
            "name": "demo",
            "defaults": {"excitation_mode": "ac", "source": "S1", "measure_channel": "M9", "current_rms_a": 1e-5, "frequency_hz": 13.7, "harmonic": 1},
            "steps": [{"name": "bad", "state": "I_AB_V_CD"}],
        },
    )
    with pytest.raises(SequenceValidationError, match="unknown M81 measurement channels"):
        validate_measurement_sequence(sequence, _contact_map(), capabilities=InstrumentCapabilities(sources=("S1",), measure_channels=("M1", "M2")))


def test_source_quantity_voltage_fails_during_validate_measurement_sequence(tmp_path):
    sequence = _write_sequence(
        tmp_path,
        {
            "name": "demo",
            "defaults": {
                "source_mode": "dc",
                "source_quantity": "voltage",
                "source": "S1",
                "measure_channel": "M1",
                "source_value": 1e-3,
            },
            "steps": [{"name": "bad", "state": "I_AB_V_CD"}],
        },
    )
    with pytest.raises(SequenceValidationError, match="Only current sourcing is currently supported by the hardware runner"):
        validate_measurement_sequence(sequence, _contact_map())


def test_invalid_source_quantity_value_fails_during_validation(tmp_path):
    sequence = _write_sequence(
        tmp_path,
        {
            "name": "demo",
            "defaults": {
                "source_mode": "ac",
                "source_quantity": "foo",
                "source": "S1",
                "measure_channel": "M1",
                "source_value": 1e-5,
                "frequency_hz": 13.7,
                "harmonic": 1,
            },
            "steps": [{"name": "bad", "state": "I_AB_V_CD"}],
        },
    )
    with pytest.raises(SequenceValidationError, match="must be 'current' or 'voltage'"):
        validate_measurement_sequence(sequence, _contact_map())


def test_custom_capabilities_are_accepted(tmp_path):
    sequence = _write_sequence(
        tmp_path,
        {
            "name": "demo",
            "defaults": {"excitation_mode": "ac", "source": "SRC_A", "measure_channel": "MEAS_A", "current_rms_a": 1e-5, "frequency_hz": 13.7, "harmonic": 1},
            "steps": [{"name": "ok", "state": "I_AB_V_CD"}],
        },
    )
    resolved = validate_measurement_sequence(
        sequence,
        _contact_map(),
        capabilities=InstrumentCapabilities(sources=("SRC_A",), measure_channels=("MEAS_A",)),
    )
    assert resolved[0].source == "SRC_A"
    assert resolved[0].measure_channels == ("MEAS_A",)


def test_reciprocal_of_accepted_and_normalized(tmp_path, caplog):
    sequence = _write_sequence(
        tmp_path,
        {
            "name": "demo",
            "defaults": {"excitation_mode": "ac", "source": "S1", "measure_channel": "M1", "current_rms_a": 1e-5, "frequency_hz": 13.7, "harmonic": 1},
            "steps": [
                {"name": "a", "state": "I_AB_V_CD"},
                {"name": "b", "state": "I_CD_V_AB", "reciprocal_of": "a"},
            ],
        },
    )
    resolved = validate_measurement_sequence(sequence, _contact_map())
    assert resolved[1].reciprocal_step_of == "a"
    assert "deprecated reciprocal_of" in caplog.text


def test_reciprocal_step_of_accepted(tmp_path):
    sequence = _write_sequence(
        tmp_path,
        {
            "name": "demo",
            "defaults": {"excitation_mode": "ac", "source": "S1", "measure_channel": "M1", "current_rms_a": 1e-5, "frequency_hz": 13.7, "harmonic": 1},
            "steps": [
                {"name": "a", "state": "I_AB_V_CD"},
                {"name": "b", "state": "I_CD_V_AB", "reciprocal_step_of": "a"},
            ],
        },
    )
    resolved = validate_measurement_sequence(sequence, _contact_map())
    assert resolved[1].reciprocal_step_of == "a"
