from electrical_measurements.records import MeasurementRecordBuilder, OutputSpec, apply_output_transform, normalize_output_mapping


def test_raw_transform():
    output = OutputSpec(name="vxx_v", transform="raw")
    assert apply_output_transform(output, {"value": 1.2}, dc_current_a=1e-5, ac_current_rms_a=None) == 1.2


def test_voltage_over_current_transform():
    output = OutputSpec(name="rxx_ohm", transform="voltage_over_current")
    assert apply_output_transform(output, {"value": 2.0}, dc_current_a=1.0, ac_current_rms_a=None) == 2.0


def test_lockin_r_over_current_transform():
    output = OutputSpec(name="rxx_ohm", transform="lockin_r_over_current")
    assert apply_output_transform(output, {"r": 4.0}, dc_current_a=None, ac_current_rms_a=2.0) == 2.0


def test_explicit_output_mapping():
    outputs, warnings = normalize_output_mapping({"M1": {"name": "vxx_v", "transform": "raw"}}, excitation_mode="dc")
    assert warnings == []
    assert outputs["M1"] == OutputSpec(name="vxx_v", transform="raw")


def test_backward_compatible_string_output_mapping():
    outputs, warnings = normalize_output_mapping({"M1": "rxx_ohm"}, excitation_mode="ac")
    assert outputs["M1"] == OutputSpec(name="rxx_ohm", transform="lockin_x_over_current")
    assert warnings == []


def test_consistent_record_columns_between_contexts():
    builder = MeasurementRecordBuilder(context_name="sequence")
    record_a = builder.build(
        sample_id="sample",
        geometry="geom",
        step_index=0,
        step_name="step_a",
        state_name="state_a",
        relay_channels=(1, 2),
        excitation_mode="ac",
        source_channel="S1",
        measure_channels=["M1"],
        readings={"M1": {"x": 2.0, "y": 0.0, "r": 2.0, "theta_deg": 0.0}},
        outputs={"M1": OutputSpec(name="rxx_ohm", transform="lockin_x_over_current")},
        ac_current_rms_a=1.0,
        frequency_hz=13.7,
        harmonic=1,
    )
    record_b = builder.build(
        sample_id="sample",
        geometry="geom",
        step_index=1,
        step_name="step_b",
        state_name="state_b",
        relay_channels=(1, 2),
        excitation_mode="ac",
        source_channel="S1",
        measure_channels=["M1"],
        readings={"M1": {"x": 3.0, "y": 0.0, "r": 3.0, "theta_deg": 0.0}},
        outputs={"M1": OutputSpec(name="rxx_ohm", transform="lockin_x_over_current")},
        ac_current_rms_a=1.5,
        frequency_hz=13.7,
        harmonic=1,
    )
    assert set(record_a) == set(record_b)
