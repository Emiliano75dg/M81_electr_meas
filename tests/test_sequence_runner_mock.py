import pandas as pd
import pytest
import yaml

from electrical_measurements.instruments.mock import MockEnvironmentController, MockM81Controller
from electrical_measurements.records import OutputSpec
from electrical_measurements.sequences import SequenceRunner, load_measurement_sequence, validate_measurement_sequence
from electrical_measurements.switching.contact_map import ContactMap


class TrackingM81(MockM81Controller):
    def __post_init__(self) -> None:
        super().__post_init__()
        self.events: list[str] = []
        self.lockin_reads: dict[str, int] = {}

    def disable_all_sources(self) -> None:
        self.events.append("disable_all_sources")
        super().disable_all_sources()

    def enable_source(self, source: str) -> None:
        self.events.append(f"enable_source:{source}")
        super().enable_source(source)

    def read_lockin(self, measure_channel: str) -> dict[str, object]:
        self.lockin_reads[measure_channel] = self.lockin_reads.get(measure_channel, 0) + 1
        return super().read_lockin(measure_channel)


class NoSyncReadStreamRunner(SequenceRunner):
    def _read_channels(self, step):  # type: ignore[override]
        raise AssertionError("Streaming merge must not call _read_channels() for the primary trace channel")

    def _read_channels_for(self, step, channels):  # type: ignore[override]
        raise AssertionError("Streaming merge must not call synchronous channel reads during trace merge")


class MatrixFake:
    def __init__(self, event_sink: list[str]) -> None:
        self.apply_state_calls: list[str] = []
        self.event_sink = event_sink

    def apply_state(self, state_name: str):
        self.apply_state_calls.append(state_name)
        self.event_sink.append(f"apply_state:{state_name}")
        return [1, 2, 3]

    def close_channels(self, *args, **kwargs):
        raise AssertionError("SequenceRunner must not call close_channels directly")


def _runner(sequence_path: str, contact_map_path: str):
    contact_map = ContactMap.from_yaml(contact_map_path)
    m81 = TrackingM81()
    matrix = MatrixFake(m81.events)
    sequence = load_measurement_sequence(sequence_path)
    resolved = validate_measurement_sequence(sequence, contact_map)
    return SequenceRunner(sequence=sequence, contact_map=contact_map, resolved_steps=resolved, m81=m81, matrix=matrix, sample_id="mock-sample"), m81, matrix, resolved


def test_apply_state_called_once_per_step():
    runner, _m81, matrix, _resolved = _runner("configs/sequences/vdp_ac_reciprocity.yaml", "configs/contact_maps/vdp_4contacts_7709.yaml")
    runner.run(temperature_k=300.0, field_t=0.0)
    assert matrix.apply_state_calls == ["I_AB_V_CD", "I_CD_V_AB", "I_BC_V_DA", "I_DA_V_BC"]


def test_runner_never_calls_close_channels_directly():
    runner, _m81, _matrix, _resolved = _runner("configs/sequences/vdp_ac_reciprocity.yaml", "configs/contact_maps/vdp_4contacts_7709.yaml")
    runner.run(temperature_k=300.0, field_t=0.0)


def test_sources_disabled_before_switch_and_enabled_after():
    runner, m81, _matrix, _resolved = _runner("configs/sequences/vdp_ac_reciprocity.yaml", "configs/contact_maps/vdp_4contacts_7709.yaml")
    runner.run(temperature_k=300.0, field_t=0.0)
    first_disable = m81.events.index("disable_all_sources")
    first_apply = m81.events.index("apply_state:I_AB_V_CD")
    first_enable = m81.events.index("enable_source:S1")
    assert first_disable < first_apply < first_enable


def test_source_disabled_after_each_step():
    runner, m81, _matrix, _resolved = _runner("configs/sequences/vdp_ac_reciprocity.yaml", "configs/contact_maps/vdp_4contacts_7709.yaml")
    runner.run(temperature_k=300.0, field_t=0.0)
    assert m81.events.count("disable_all_sources") >= 8


def test_dc_actual_current_uses_bias_polarity():
    runner, _m81, _matrix, _resolved = _runner("configs/sequences/vdp_dc_reverse_bias.yaml", "configs/contact_maps/vdp_4contacts_7709.yaml")
    dataframe = runner.run(temperature_k=300.0, field_t=0.0)
    assert set(dataframe["bias_polarity"]) == {1, -1}
    assert set(round(value, 8) for value in dataframe["source_current_a_dc"]) == {1e-05, -1e-05}


def test_dc_reverse_bias_keeps_same_matrix_state(tmp_path):
    path = tmp_path / "dc_auto.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "name": "dc_auto",
                "defaults": {
                    "source_mode": "dc",
                    "source_quantity": "current",
                    "source": "S1",
                    "measure_channel": "M1",
                    "source_value": 1e-5,
                    "reverse_policy": "auto",
                },
                "steps": [{"name": "dc", "state": "I_AB_V_CD"}],
            },
            sort_keys=False,
        )
    )
    runner, _m81, matrix, _resolved = _runner(str(path), "configs/contact_maps/vdp_4contacts_7709.yaml")
    dataframe = runner.run(temperature_k=300.0, field_t=0.0)
    assert matrix.apply_state_calls == ["I_AB_V_CD", "I_AB_V_CD"]
    assert dataframe["state"].tolist() == ["I_AB_V_CD", "I_AB_V_CD"]
    assert dataframe["source_current_a_dc"].tolist() == [1e-05, -1e-05]



def test_ac_configuration_does_not_use_bias_polarity():
    runner, _m81, _matrix, _resolved = _runner("configs/sequences/vdp_ac_reciprocity.yaml", "configs/contact_maps/vdp_4contacts_7709.yaml")
    dataframe = runner.run(temperature_k=300.0, field_t=0.0)
    assert dataframe["bias_polarity"].isna().all()


def test_multiple_measurement_channels_are_read():
    runner, _m81, _matrix, _resolved = _runner("configs/sequences/hallbar_ac_rxx_rxy.yaml", "configs/contact_maps/hallbar_6contacts_7709.yaml")
    dataframe = runner.run(temperature_k=300.0, field_t=0.0)
    first = dataframe[dataframe["step_name"] == "forward_rxx_rxy"].iloc[0]
    assert first["rxx_ohm"] is not None
    assert first["rxy_ohm"] is not None
    assert "m1_lockin_x" in dataframe.columns
    assert "m2_lockin_x" in dataframe.columns


def test_dry_run_does_not_touch_hardware():
    contact_map = ContactMap.from_yaml("configs/contact_maps/vdp_4contacts_7709.yaml")
    sequence = load_measurement_sequence("configs/sequences/vdp_ac_reciprocity.yaml")
    resolved = validate_measurement_sequence(sequence, contact_map)
    runner = SequenceRunner(sequence=sequence, contact_map=contact_map, resolved_steps=resolved, dry_run=True)
    dataframe = runner.run()
    assert isinstance(dataframe, pd.DataFrame)
    assert "relays" in dataframe.columns
    assert "settle_s" in dataframe.columns
    assert "repeats" in dataframe.columns


def test_dry_run_preview_includes_relay_channels():
    contact_map = ContactMap.from_yaml("configs/contact_maps/vdp_4contacts_7709.yaml")
    sequence = load_measurement_sequence("configs/sequences/vdp_ac_reciprocity.yaml")
    resolved = validate_measurement_sequence(sequence, contact_map)
    runner = SequenceRunner(sequence=sequence, contact_map=contact_map, resolved_steps=resolved, dry_run=True)
    preview = runner.format_preview()
    assert "Matrix7709.apply_state()" in preview
    assert "17,26,35,44" in preview
    assert "dc_current_a" in preview
    assert "current_rms_a" in preview


def test_sequence_stream_records_initial_and_final_environment_values():
    runner, _m81, _matrix, _resolved = _runner("configs/sequences/vdp_ac_reciprocity.yaml", "configs/contact_maps/vdp_4contacts_7709.yaml")
    environment = MockEnvironmentController(field_t=0.0, temperature_k=300.0)
    environment.start_field_ramp(1.0, 60.0)
    dataframe = runner.run_stream(environment=environment, stream_samples=3, stream_interval=0.01, temperature_k=300.0, field_t=0.0)
    assert not dataframe.empty
    assert "field_t_initial" in dataframe.columns
    assert "field_t_final" in dataframe.columns
    assert "sequence_mode" in dataframe.columns
    assert "timestamp_matrix_applied" in dataframe.columns
    assert (dataframe["field_t_final"] >= dataframe["field_t_initial"]).all()


def test_sequence_stream_rejects_multichannel_steps():
    runner, _m81, _matrix, _resolved = _runner("configs/sequences/hallbar_ac_rxx_rxy.yaml", "configs/contact_maps/hallbar_6contacts_7709.yaml")
    environment = MockEnvironmentController(field_t=0.0, temperature_k=300.0)
    with pytest.raises(RuntimeError, match="single primary measurement channel"):
        runner.run_stream(environment=environment, stream_samples=2, stream_interval=0.01, temperature_k=300.0, field_t=0.0)


def test_sequence_stream_rejects_dc_steps():
    runner, _m81, _matrix, _resolved = _runner("configs/sequences/vdp_dc_reverse_bias.yaml", "configs/contact_maps/vdp_4contacts_7709.yaml")
    environment = MockEnvironmentController(field_t=0.0, temperature_k=300.0)
    with pytest.raises(RuntimeError, match="supports only AC steps"):
        runner.run_stream(environment=environment, stream_samples=2, stream_interval=0.01, temperature_k=300.0, field_t=0.0)


def test_sequence_stream_valid_step_does_not_use_sync_reads_for_trace_merge():
    contact_map = ContactMap.from_yaml("configs/contact_maps/vdp_4contacts_7709.yaml")
    sequence = load_measurement_sequence("configs/sequences/vdp_ac_reciprocity.yaml")
    resolved = validate_measurement_sequence(sequence, contact_map)
    m81 = TrackingM81()
    matrix = MatrixFake(m81.events)
    runner = NoSyncReadStreamRunner(
        sequence=sequence,
        contact_map=contact_map,
        resolved_steps=resolved,
        m81=m81,
        matrix=matrix,
        sample_id="mock-sample",
    )
    environment = MockEnvironmentController(field_t=0.0, temperature_k=300.0)

    dataframe = runner.run_stream(environment=environment, stream_samples=2, stream_interval=0.01, temperature_k=300.0, field_t=0.0)

    assert not dataframe.empty


def test_records_built_with_output_specs():
    runner, _m81, _matrix, resolved = _runner("configs/sequences/hallbar_ac_rxx_rxy.yaml", "configs/contact_maps/hallbar_6contacts_7709.yaml")
    assert resolved[0].outputs["M1"] == OutputSpec(name="rxx_ohm", transform="lockin_x_over_current")


def test_resolved_steps_are_not_mutated_at_runtime():
    runner, _m81, _matrix, resolved = _runner("configs/sequences/vdp_ac_reciprocity.yaml", "configs/contact_maps/vdp_4contacts_7709.yaml")
    before = resolved[0].relay_channels
    runner.run(temperature_k=300.0, field_t=0.0)
    assert resolved[0].relay_channels == before
