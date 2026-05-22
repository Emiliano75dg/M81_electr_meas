from pathlib import Path

import pandas as pd
import pytest
import yaml

from electrical_measurements.exceptions import RunnerInputError
from electrical_measurements.runners.run_measurement import build_parser, build_run_namespace, run_command


def _write_config(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "sample_id": "sequence-sample",
                "instruments": {
                    "m81": {"connection": {"kind": "mock"}},
                    "daq6510": {"resource": "MOCK::DAQ6510", "settle_s": 0.0},
                    "environment": {"kind": "mock", "mode": "standalone", "initial_temperature_k": 300.0, "initial_field_t": 0.0},
                },
                "output": {"directory": str(tmp_path)},
                "logging": {"level": "INFO"},
            },
            sort_keys=False,
        )
    )
    return path


def test_sequence_runs_from_cli_namespace(tmp_path: Path):
    config = _write_config(tmp_path)
    args = build_run_namespace(
        config=str(config),
        contact_map="configs/contact_maps/vdp_4contacts_7709.yaml",
        mock=True,
        output=str(tmp_path),
        temperatures="300",
        fields="0",
        sequence="configs/sequences/vdp_ac_reciprocity.yaml",
    )
    result = run_command(args)
    assert result == 0
    dataframe = pd.read_csv(tmp_path / "vdp_ac_reciprocity.csv")
    assert not dataframe.empty
    assert "sequence_name" in dataframe.columns


def test_sequence_conflicts_with_state_name(tmp_path: Path):
    config = _write_config(tmp_path)
    args = build_run_namespace(
        config=str(config),
        contact_map="configs/contact_maps/vdp_4contacts_7709.yaml",
        mock=True,
        output=str(tmp_path),
        temperatures="300",
        fields="0",
        sequence="configs/sequences/vdp_ac_reciprocity.yaml",
        state_name="I_AB_V_CD",
    )
    with pytest.raises(RunnerInputError, match="cannot be combined"):
        run_command(args)


def test_sequence_conflicts_with_protocol(tmp_path: Path):
    config = _write_config(tmp_path)
    args = build_run_namespace(
        config=str(config),
        contact_map="configs/contact_maps/vdp_4contacts_7709.yaml",
        mock=True,
        output=str(tmp_path),
        protocol="vdp",
        temperatures="300",
        fields="0",
        sequence="configs/sequences/vdp_ac_reciprocity.yaml",
    )
    with pytest.raises(RunnerInputError, match="cannot be combined with --protocol"):
        run_command(args)


def test_sequence_conflicts_with_selected_states(tmp_path: Path):
    config = _write_config(tmp_path)
    args = build_run_namespace(
        config=str(config),
        contact_map="configs/contact_maps/vdp_4contacts_7709.yaml",
        mock=True,
        output=str(tmp_path),
        temperatures="300",
        fields="0",
        sequence="configs/sequences/vdp_ac_reciprocity.yaml",
        selected_states="I_AB_V_CD",
    )
    with pytest.raises(RunnerInputError, match="cannot be combined"):
        run_command(args)


def test_old_state_name_behavior_still_works(tmp_path: Path):
    config = _write_config(tmp_path)
    args = build_run_namespace(
        config=str(config),
        contact_map="configs/contact_maps/hallbar_6contacts_7709.yaml",
        mock=True,
        output=str(tmp_path),
        protocol="hall",
        temperatures="300",
        fields="0",
        current=1e-5,
        frequency=13.7,
        settle=0.0,
        state_name="hallbar_forward",
    )
    assert run_command(args) == 0


def test_old_selected_states_behavior_still_works(tmp_path: Path):
    config = _write_config(tmp_path)
    args = build_run_namespace(
        config=str(config),
        contact_map="configs/contact_maps/vdp_4contacts_7709.yaml",
        mock=True,
        output=str(tmp_path),
        protocol="vdp",
        temperatures="300",
        fields="0",
        current=1e-5,
        frequency=13.7,
        settle=0.0,
        selected_states="I_AB_V_CD,I_BC_V_DA",
    )
    assert run_command(args) == 0


def test_sequence_dry_run_prints_preview_and_skips_hardware(tmp_path: Path, capsys):
    config = _write_config(tmp_path)
    args = build_run_namespace(
        config=str(config),
        contact_map="configs/contact_maps/vdp_4contacts_7709.yaml",
        output=str(tmp_path),
        temperatures="300",
        fields="0",
        sequence="configs/sequences/vdp_ac_reciprocity.yaml",
        dry_run=True,
    )
    result = run_command(args)
    captured = capsys.readouterr()
    assert result == 0
    assert "r_ab_cd" in captured.out
    assert "relays" in captured.out
    assert not (tmp_path / "vdp_ac_reciprocity.csv").exists()


def test_sequence_dry_run_does_not_build_hardware(tmp_path: Path, capsys, monkeypatch):
    from electrical_measurements.runners import run_measurement

    config = _write_config(tmp_path)
    args = build_run_namespace(
        config=str(config),
        contact_map="configs/contact_maps/vdp_4contacts_7709.yaml",
        output=str(tmp_path),
        temperatures="300",
        fields="0",
        sequence="configs/sequences/vdp_ac_reciprocity.yaml",
        dry_run=True,
    )

    def fail_build(*_args, **_kwargs):
        raise AssertionError("dry-run should not instantiate hardware")

    monkeypatch.setattr(run_measurement, "build_instruments", fail_build)
    result = run_command(args)
    captured = capsys.readouterr()
    assert result == 0
    assert "Dry-run only" in captured.out


def test_parser_accepts_sequence_without_protocol():
    parser = build_parser()
    args = parser.parse_args(["run", "--sequence", "configs/sequences/vdp_ac_reciprocity.yaml"])
    assert args.sequence == "configs/sequences/vdp_ac_reciprocity.yaml"
    assert args.protocol is None


def test_sequence_stream_observe_runs(tmp_path: Path):
    config = _write_config(tmp_path)
    args = build_run_namespace(
        config=str(config),
        contact_map="configs/contact_maps/vdp_4contacts_7709.yaml",
        mock=True,
        output=str(tmp_path),
        temperatures="300",
        fields="0",
        sequence="configs/sequences/vdp_ac_reciprocity.yaml",
        mode="stream-observe",
        stream_samples=3,
        stream_interval=0.01,
    )
    result = run_command(args)
    assert result == 0
    dataframe = pd.read_csv(tmp_path / "vdp_ac_reciprocity_stream.csv")
    assert "field_t_initial" in dataframe.columns
    assert "field_t_final" in dataframe.columns


def test_sequence_stream_ramp_runs(tmp_path: Path):
    config = _write_config(tmp_path)
    args = build_run_namespace(
        config=str(config),
        contact_map="configs/contact_maps/vdp_4contacts_7709.yaml",
        mock=True,
        output=str(tmp_path),
        temperatures="300",
        fields="0",
        sequence="configs/sequences/vdp_ac_reciprocity.yaml",
        mode="stream-ramp",
        stream_samples=3,
        stream_interval=0.01,
        ramp_quantity="field",
        ramp_target=1.0,
        ramp_rate=60.0,
    )
    result = run_command(args)
    assert result == 0
    dataframe = pd.read_csv(tmp_path / "vdp_ac_reciprocity_stream.csv")
    assert "field_t_initial" in dataframe.columns
    assert "field_t_final" in dataframe.columns
    assert dataframe["field_t_final"].iloc[-1] >= dataframe["field_t_initial"].iloc[0]


def test_sequence_stream_multichannel_outputs_are_saved(tmp_path: Path):
    config = _write_config(tmp_path)
    args = build_run_namespace(
        config=str(config),
        contact_map="configs/contact_maps/hallbar_6contacts_7709.yaml",
        mock=True,
        output=str(tmp_path),
        temperatures="300",
        fields="0",
        sequence="configs/sequences/hallbar_ac_rxx_rxy.yaml",
        mode="stream-observe",
        stream_samples=2,
        stream_interval=0.01,
    )
    result = run_command(args)
    assert result == 0
    dataframe = pd.read_csv(tmp_path / "hallbar_ac_rxx_rxy_stream.csv")
    forward = dataframe[dataframe["step_name"] == "forward_rxx_rxy"]
    assert forward["rxx_ohm"].notna().all()
    assert forward["rxy_ohm"].notna().all()
    assert "m1_lockin_x" in dataframe.columns
    assert "m2_lockin_x" in dataframe.columns
