from pathlib import Path

import pandas as pd

from electrical_measurements.gui.app import (
    append_buffered_record,
    backend_measure_mode_to_ui,
    build_live_record,
    compute_plot_points,
    environment_mode_supports_control,
    format_measure_summary,
    hallbar_live_channels,
    latest_csv_files,
    parse_required_float,
    parse_required_int,
    parse_optional_positive_int,
    parse_optional_positive_float,
    plottable_columns,
    preview_csv_text,
    protocol_is_multi_state,
    recommended_states_for_protocol,
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
