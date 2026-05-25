from pathlib import Path

import pandas as pd

from electrical_measurements.gui.app import (
    append_buffered_record,
    backend_measure_mode_to_ui,
    build_live_record,
    compute_plot_points,
    describe_sequence_preset,
    empty_sequence_data,
    environment_mode_supports_control,
    format_sequence_preset_summary,
    format_measure_summary,
    hallbar_live_channels,
    latest_csv_files,
    parse_csv_mapping,
    parse_required_float,
    parse_required_int,
    parse_optional_positive_int,
    parse_optional_positive_float,
    plottable_columns,
    preview_csv_text,
    protocol_is_multi_state,
    recommended_states_for_protocol,
    relay_channels_for_state,
    sequence_preset_names_for_category,
    sequence_step_table_row,
    ui_measure_mode_to_backend,
)
from electrical_measurements.switching.contact_map import ContactMap


def test_protocol_multi_state_detection():
    assert protocol_is_multi_state("vdp") is True
    assert protocol_is_multi_state("reciprocity") is True
    assert protocol_is_multi_state("hall") is False


def test_recommended_states_for_protocol():
    contact_map = ContactMap.from_yaml("configs/contact_maps/hallbar_6contacts_7709.yaml")
    assert "hallbar_forward" in recommended_states_for_protocol("hallbar_mr", contact_map)
    assert "hallbar_forward" in recommended_states_for_protocol("hall", contact_map)


def test_recommended_states_for_vdp_hall_prefers_non_duplicate_pairs():
    contact_map = ContactMap.from_yaml("configs/contact_maps/vdp_4contacts_7709.yaml")
    recommended = recommended_states_for_protocol("vdp_hall", contact_map)
    assert "I_AB_V_CD" in recommended
    assert "I_BC_V_DA" in recommended
    assert "I_CD_V_AB" not in recommended


def test_latest_csv_files_and_preview(tmp_path: Path):
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    a.write_text("x,y\n1,2\n")
    b.write_text("x,y\n3,4\n")
    files = latest_csv_files(tmp_path)
    assert files
    text = preview_csv_text(files[0])
    assert "x" in text
    assert "y" in text


def test_plottable_columns_and_points():
    dataframe = pd.DataFrame({"field_t": [0.0, 1.0, 2.0], "rxx_ohm": [10.0, 11.0, 12.0], "label": ["a", "b", "c"]})
    columns = plottable_columns(dataframe)
    assert "field_t" in columns
    assert "rxx_ohm" in columns
    points = compute_plot_points(dataframe, "field_t", "rxx_ohm", width=300, height=200)
    assert len(points) == 3
    assert all(len(point) == 2 for point in points)


def test_build_live_record_keeps_numeric_fields():
    reading = {"timestamp": "2026-01-01T00:00:00+00:00", "x": 1.2, "r": 1.3, "theta_deg": 45.0}
    record = build_live_record(4, 1.5, "M2", reading, temperature_k=295.0, field_t=0.2, extra_values={"x_vxx": 0.4})
    assert record["sample_index"] == 4
    assert record["elapsed_s"] == 1.5
    assert record["measure_channel"] == "M2"
    assert record["temperature_k"] == 295.0
    assert record["field_t"] == 0.2
    assert record["x"] == 1.2
    assert record["x_vxx"] == 0.4
    assert record["r"] == 1.3
    assert record["theta_deg"] == 45.0


def test_append_buffered_record_trims_old_points():
    records = [{"sample_index": 0}, {"sample_index": 1}]
    updated = append_buffered_record(records, {"sample_index": 2}, limit=2)
    assert [row["sample_index"] for row in updated] == [1, 2]


def test_environment_mode_supports_control():
    assert environment_mode_supports_control("integrated") is True
    assert environment_mode_supports_control("async-poll") is False
    assert environment_mode_supports_control("standalone") is False


def test_hallbar_live_channels_extracts_vxx_and_vxy():
    contact_map = ContactMap.from_yaml("configs/contact_maps/hallbar_6contacts_7709.yaml")
    assert hallbar_live_channels(contact_map) == {"vxx": "M1", "vxy": "M2"}


def test_parse_required_float_and_int_validate_ranges():
    assert parse_required_float("1.5", "Current", positive=True) == 1.5
    assert parse_required_int("3", "Harmonic", minimum=1) == 3


def test_measure_mode_mapping_helpers():
    assert ui_measure_mode_to_backend("Auto") == "auto"
    assert ui_measure_mode_to_backend("Lock-in") == "lockin"
    assert ui_measure_mode_to_backend("DC") == "dc"
    assert backend_measure_mode_to_ui("lockin") == "Lock-in"
    assert backend_measure_mode_to_ui("dc") == "DC"


def test_parse_optional_positive_int_defaults_and_validates():
    assert parse_optional_positive_int("", "Harmonic", default=3) == 3
    assert parse_optional_positive_int("5", "Harmonic", default=1) == 5


def test_parse_optional_positive_float_defaults_and_validates():
    assert parse_optional_positive_float("", "NPLC", default=2.5) == 2.5
    assert parse_optional_positive_float("0.7", "Time constant", default=0.3) == 0.7


def test_format_measure_summary_renders_modes_and_harmonics():
    summary = format_measure_summary(
        {
            "M1": {"resolved_mode": "dc"},
            "M2": {"resolved_mode": "lockin", "resolved_harmonic": 3},
            "M3": {"preferred_mode": "auto"},
        }
    )
    assert summary == "Measures: M1: DC | M2: lock-in @ 3f | M3: auto"


def test_empty_sequence_data_keeps_contact_map_reference():
    sequence = empty_sequence_data("configs/contact_maps/vdp_4contacts_7709.yaml")
    assert sequence["contact_map"] == "configs/contact_maps/vdp_4contacts_7709.yaml"
    assert sequence["defaults"]["excitation_mode"] == "ac"
    assert sequence["steps"] == []


def test_parse_csv_mapping_parses_outputs():
    mapping = parse_csv_mapping("M1:rxx,M2:rxy")
    assert mapping == {"M1": "rxx", "M2": "rxy"}


def test_sequence_step_table_row_formats_dc_current_and_relays():
    contact_map = ContactMap.from_yaml("configs/contact_maps/vdp_4contacts_7709.yaml")
    defaults = {"excitation_mode": "dc", "source": "S1", "measure_channel": "M1", "current_a": 1e-5, "settle_s": 0.1, "repeats": 1}
    step = {"name": "plus", "state": "I_AB_V_CD", "bias_polarity": -1, "outputs": {"M1": "rxx"}, "tags": ["dc"]}
    row = sequence_step_table_row(step, defaults, contact_map, 0)
    assert row["current"] == "-1e-05"
    assert row["relay_channels"] == "17,26,35,44"
    assert row["outputs"] == "M1:rxx"


def test_sequence_step_table_row_shows_per_channel_measure_specs():
    contact_map = ContactMap.from_yaml("configs/contact_maps/vdp_4contacts_7709.yaml")
    defaults = {"excitation_mode": "ac", "source": "S1", "frequency_hz": 13.7, "harmonic": 1}
    step = {
        "name": "dual",
        "state": "I_AB_V_DC",
        "measure_specs": {
            "M1": {"measure_mode": "lockin", "harmonic": 1, "readout": "x", "transform": "lockin_x_over_current", "output": "r1"},
            "M2": {"measure_mode": "lockin", "harmonic": 2, "readout": "x", "transform": "lockin_x_over_current", "output": "r2"},
        },
    }
    row = sequence_step_table_row(step, defaults, contact_map, 0)
    assert row["m1_spec"] == "1/x/lockin_x_over_current/r1"
    assert row["m2_spec"] == "2/x/lockin_x_over_current/r2"


def test_sequence_presets_are_grouped_in_expected_categories():
    assert sequence_preset_names_for_category("Van der Pauw") == [
        "vdp_full_ac",
        "vdp_full_dc",
        "vdp_fast_hall_ac",
        "vdp_hall_second_harmonic_ac",
        "vdp_reciprocity_check",
        "vdp_hall_with_drift_guard",
        "vdp_lockin_phase_diagnostic",
    ]
    assert sequence_preset_names_for_category("Hall bar") == [
        "hallbar_static_1w_2w",
        "hallbar_static_fast",
        "hallbar_static_drift_guard",
    ]
    assert sequence_preset_names_for_category("Diagnostics") == ["second_harmonic_frequency_check"]


def test_sequence_preset_summary_reports_key_metadata():
    summary = describe_sequence_preset("hallbar_static_1w_2w")
    assert summary["steps"] == 2
    assert summary["switching"] == "static wiring"
    assert summary["channels"] == ["M1", "M2"]
    assert summary["harmonics"] == [1, 2]
    assert "second_harmonic" in summary["features"]
    assert "hallbar_static_1w_2w" in format_sequence_preset_summary("hallbar_static_1w_2w")


def test_relay_channels_for_state_returns_empty_when_missing():
    contact_map = ContactMap.from_yaml("configs/contact_maps/vdp_4contacts_7709.yaml")
    assert relay_channels_for_state(contact_map, "missing") == ""


def test_parse_required_float_and_int_raise_useful_errors():
    try:
        parse_required_float("-1", "Current", positive=True)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "Current" in str(exc)
    try:
        parse_required_int("0", "Harmonic", minimum=1)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "Harmonic" in str(exc)
