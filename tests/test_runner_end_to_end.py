from pathlib import Path

import pandas as pd
import pytest
import yaml

from electrical_measurements.exceptions import RunnerInputError
from electrical_measurements.runners.run_measurement import build_run_namespace, emergency_stop_command, run_command


def write_config(tmp_path: Path, *, environment_mode: str = "standalone", environment_kind: str = "mock") -> Path:
    config = {
        "sample_id": "sample-e2e",
        "instruments": {
            "m81": {"connection": {"kind": "mock"}},
            "daq6510": {"resource": "MOCK::DAQ6510", "settle_s": 0.0},
            "environment": {
                "kind": environment_kind,
                "mode": environment_mode,
                "initial_temperature_k": 301.0,
                "initial_field_t": 0.05,
            },
        },
        "output": {"directory": str(tmp_path)},
        "logging": {"level": "INFO"},
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=True))
    return path


def test_run_command_stable_mock_writes_dataset_and_metadata(tmp_path: Path):
    config_path = write_config(tmp_path, environment_mode="standalone")
    args = build_run_namespace(
        config=str(config_path),
        contact_map="configs/contact_maps/hallbar_6contacts_7709.yaml",
        mock=True,
        output=str(tmp_path),
        protocol="hallbar_mr",
        temperatures="300",
        fields="0,0.1",
        current=10e-6,
        frequency=13.7,
        settle=0.0,
    )

    result = run_command(args)

    assert result == 0
    csv_path = tmp_path / "hallbar_mr.csv"
    metadata_path = tmp_path / "hallbar_mr.metadata.json"
    assert csv_path.exists()
    assert metadata_path.exists()
    dataframe = pd.read_csv(csv_path)
    assert not dataframe.empty
    assert "rxx_ohm" in dataframe.columns
    assert dataframe["rxx_ohm"].notna().all()


def test_run_command_stream_ramp_rejects_async_poll_environment(tmp_path: Path):
    config_path = write_config(tmp_path, environment_mode="async-poll", environment_kind="mock")
    args = build_run_namespace(
        config=str(config_path),
        contact_map="configs/contact_maps/hallbar_6contacts_7709.yaml",
        mock=True,
        output=str(tmp_path),
        protocol="hallbar_mr",
        temperatures="300",
        fields="0",
        current=10e-6,
        frequency=13.7,
        settle=0.0,
        mode="stream-ramp",
        ramp_quantity="field",
        ramp_target=0.5,
        ramp_rate=60.0,
        stream_samples=4,
        stream_interval=0.01,
    )

    with pytest.raises(RunnerInputError, match="control enabled"):
        run_command(args)


def test_run_command_stream_observe_accepts_async_poll_environment(tmp_path: Path):
    config_path = write_config(tmp_path, environment_mode="async-poll", environment_kind="mock")
    args = build_run_namespace(
        config=str(config_path),
        contact_map="configs/contact_maps/hallbar_6contacts_7709.yaml",
        mock=True,
        output=str(tmp_path),
        protocol="hallbar_mr",
        temperatures="300",
        fields="0",
        current=10e-6,
        frequency=13.7,
        settle=0.0,
        mode="stream-observe",
        stream_samples=4,
        stream_interval=0.01,
    )

    result = run_command(args)

    assert result == 0
    dataframe = pd.read_csv(tmp_path / "hallbar_mr_stream.csv")
    assert dataframe["stream_mode"].eq("observe").all()


def test_emergency_stop_command_succeeds_with_mock_backends(tmp_path: Path):
    config_path = write_config(tmp_path, environment_mode="standalone")
    args = build_run_namespace(
        config=str(config_path),
        contact_map="configs/contact_maps/hallbar_6contacts_7709.yaml",
        mock=True,
    )

    result = emergency_stop_command(args)

    assert result == 0


def test_run_command_vdp_with_anisotropy_writes_expected_columns(tmp_path: Path):
    config_path = write_config(tmp_path, environment_mode="standalone")
    args = build_run_namespace(
        config=str(config_path),
        contact_map="configs/contact_maps/vdp_4contacts_7709.yaml",
        mock=True,
        output=str(tmp_path),
        protocol="vdp",
        temperatures="300",
        fields="0",
        current=10e-6,
        frequency=13.7,
        settle=0.0,
        include_anisotropy=True,
    )

    result = run_command(args)

    assert result == 0
    dataframe = pd.read_csv(tmp_path / "vdp.csv")
    assert "sheet_resistance_ohm_sq" in dataframe.columns
    assert "anisotropy_ratio" in dataframe.columns
    assert dataframe["sheet_resistance_ohm_sq"].notna().all()


def test_run_command_vdp_hall_with_reciprocity_writes_expected_columns(tmp_path: Path):
    config_path = write_config(tmp_path, environment_mode="standalone")
    args = build_run_namespace(
        config=str(config_path),
        contact_map="configs/contact_maps/vdp_4contacts_7709.yaml",
        mock=True,
        output=str(tmp_path),
        protocol="vdp_hall",
        temperatures="300",
        fields="0.1",
        current=10e-6,
        frequency=13.7,
        settle=0.0,
        include_reciprocity=True,
    )

    result = run_command(args)

    assert result == 0
    dataframe = pd.read_csv(tmp_path / "vdp_hall.csv")
    assert "rxy_ohm" in dataframe.columns
    assert dataframe["rxy_ohm"].notna().all()
    assert (tmp_path / "vdp_hall.metadata.json").exists()


def test_run_command_reciprocity_writes_pair_columns(tmp_path: Path):
    config_path = write_config(tmp_path, environment_mode="standalone")
    args = build_run_namespace(
        config=str(config_path),
        contact_map="configs/contact_maps/vdp_4contacts_7709.yaml",
        mock=True,
        output=str(tmp_path),
        protocol="reciprocity",
        temperatures="300",
        fields="0",
        current=10e-6,
        frequency=13.7,
        settle=0.0,
    )

    result = run_command(args)

    assert result == 0
    dataframe = pd.read_csv(tmp_path / "reciprocity.csv")
    assert "reciprocity_pairs" in dataframe.columns
    assert dataframe["reciprocity_pairs"].notna().all()


def test_run_command_check_contacts_writes_quality_flags(tmp_path: Path):
    config_path = write_config(tmp_path, environment_mode="standalone")
    args = build_run_namespace(
        config=str(config_path),
        contact_map="configs/contact_maps/hallbar_6contacts_7709.yaml",
        mock=True,
        output=str(tmp_path),
        protocol="check_contacts",
        temperatures="300",
        fields="0",
        settle=0.0,
    )

    result = run_command(args)

    assert result == 0
    dataframe = pd.read_csv(tmp_path / "check_contacts.csv")
    assert "contact_quality_flag" in dataframe.columns
    assert dataframe["contact_quality_flag"].notna().all()
