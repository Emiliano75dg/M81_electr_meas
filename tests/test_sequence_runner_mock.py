import pandas as pd

from electrical_measurements.instruments.mock import MockM81Controller
from electrical_measurements.sequences import SequenceRunner, load_measurement_sequence, validate_measurement_sequence
from electrical_measurements.switching.contact_map import ContactMap
from electrical_measurements.switching.matrix7709 import Matrix7709


class TrackingM81(MockM81Controller):
    def __post_init__(self) -> None:
        super().__post_init__()
        self.events: list[str] = []

    def disable_all_sources(self) -> None:
        self.events.append("disable_all_sources")
        super().disable_all_sources()

    def enable_source(self, source: str) -> None:
        self.events.append(f"enable_source:{source}")
        super().enable_source(source)


class TrackingMatrix(Matrix7709):
    def __init__(self, controller, contact_map, m81, event_sink):
        super().__init__(controller=controller, contact_map=contact_map, m81=m81, settle_s=0.0)
        self.events: list[str] = []
        self.low_level_close_calls = 0
        self.event_sink = event_sink

    def close_channels(self, channels: list[int]) -> None:
        self.low_level_close_calls += 1
        return super().close_channels(channels)

    def apply_state(self, state_name: str, override_source_enabled: bool = False, reenable_sources: bool = False) -> list[int]:
        self.events.append(f"apply_state:{state_name}")
        self.event_sink.append(f"apply_state:{state_name}")
        return super().apply_state(state_name, override_source_enabled=override_source_enabled, reenable_sources=reenable_sources)


def _runner(sequence_path: str, contact_map_path: str):
    contact_map = ContactMap.from_yaml(contact_map_path)
    m81 = TrackingM81()
    matrix = TrackingMatrix(
        controller=Matrix7709.from_config({"instruments": {"daq6510": {"resource": "MOCK::DAQ6510", "settle_s": 0.0}}}).controller,
        contact_map=contact_map,
        m81=m81,
        event_sink=m81.events,
    )
    sequence = load_measurement_sequence(sequence_path)
    resolved = validate_measurement_sequence(sequence, contact_map)
    return SequenceRunner(sequence=sequence, contact_map=contact_map, resolved_steps=resolved, m81=m81, matrix=matrix, sample_id="mock-sample"), m81, matrix


def test_apply_state_called_once_per_step():
    runner, _m81, matrix = _runner("configs/sequences/vdp_ac_reciprocity.yaml", "configs/contact_maps/vdp_4contacts_7709.yaml")
    runner.run(temperature_k=300.0, field_t=0.0)
    assert len(matrix.events) == 4


def test_runner_never_calls_controller_close_channels_directly():
    runner, _m81, matrix = _runner("configs/sequences/vdp_ac_reciprocity.yaml", "configs/contact_maps/vdp_4contacts_7709.yaml")
    runner.run(temperature_k=300.0, field_t=0.0)
    assert matrix.low_level_close_calls == 4
    assert matrix.controller.applied_states == ["I_AB_V_CD", "I_CD_V_AB", "I_BC_V_DA", "I_DA_V_BC"]


def test_sources_disabled_before_switch_and_enabled_after():
    runner, m81, matrix = _runner("configs/sequences/vdp_ac_reciprocity.yaml", "configs/contact_maps/vdp_4contacts_7709.yaml")
    runner.run(temperature_k=300.0, field_t=0.0)
    first_disable = m81.events.index("disable_all_sources")
    first_apply = m81.events.index("apply_state:I_AB_V_CD")
    first_enable = m81.events.index("enable_source:S1")
    assert first_disable < first_apply
    assert first_apply < first_enable


def test_source_disabled_after_each_step():
    runner, m81, _matrix = _runner("configs/sequences/vdp_ac_reciprocity.yaml", "configs/contact_maps/vdp_4contacts_7709.yaml")
    runner.run(temperature_k=300.0, field_t=0.0)
    assert m81.events.count("disable_all_sources") >= 8


def test_dc_actual_current_uses_bias_polarity():
    runner, m81, _matrix = _runner("configs/sequences/vdp_dc_reverse_bias.yaml", "configs/contact_maps/vdp_4contacts_7709.yaml")
    dataframe = runner.run(temperature_k=300.0, field_t=0.0)
    assert set(dataframe["bias_polarity"]) == {1, -1}
    assert set(round(value, 8) for value in dataframe["source_current_a_dc"]) == {1e-05, -1e-05}
    assert m81.get_source_settings("S1")["mode"] == "DC"


def test_ac_configuration_does_not_use_bias_polarity():
    runner, _m81, _matrix = _runner("configs/sequences/vdp_ac_reciprocity.yaml", "configs/contact_maps/vdp_4contacts_7709.yaml")
    dataframe = runner.run(temperature_k=300.0, field_t=0.0)
    assert dataframe["bias_polarity"].isna().all()


def test_multiple_measurement_channels_are_read():
    runner, _m81, _matrix = _runner("configs/sequences/hallbar_ac_rxx_rxy.yaml", "configs/contact_maps/hallbar_6contacts_7709.yaml")
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


def test_dry_run_preview_includes_relay_channels():
    contact_map = ContactMap.from_yaml("configs/contact_maps/vdp_4contacts_7709.yaml")
    sequence = load_measurement_sequence("configs/sequences/vdp_ac_reciprocity.yaml")
    resolved = validate_measurement_sequence(sequence, contact_map)
    runner = SequenceRunner(sequence=sequence, contact_map=contact_map, resolved_steps=resolved, dry_run=True)
    preview = runner.format_preview()
    assert "relays" in preview
    assert "17,26,35,44" in preview


def test_sequence_stream_records_initial_and_final_environment_values():
    runner, _m81, _matrix = _runner("configs/sequences/vdp_ac_reciprocity.yaml", "configs/contact_maps/vdp_4contacts_7709.yaml")
    from electrical_measurements.instruments.mock import MockEnvironmentController

    environment = MockEnvironmentController(field_t=0.0, temperature_k=300.0)
    environment.start_field_ramp(1.0, 60.0)
    dataframe = runner.run_stream(environment=environment, stream_samples=3, stream_interval=0.01, temperature_k=300.0, field_t=0.0)
    assert not dataframe.empty
    assert "field_t_initial" in dataframe.columns
    assert "field_t_final" in dataframe.columns
    assert (dataframe["field_t_final"] >= dataframe["field_t_initial"]).all()


def test_sequence_stream_reads_secondary_lockin_channels_for_multichannel_step():
    runner, _m81, _matrix = _runner("configs/sequences/hallbar_ac_rxx_rxy.yaml", "configs/contact_maps/hallbar_6contacts_7709.yaml")
    from electrical_measurements.instruments.mock import MockEnvironmentController

    environment = MockEnvironmentController(field_t=0.0, temperature_k=300.0)
    dataframe = runner.run_stream(environment=environment, stream_samples=2, stream_interval=0.01, temperature_k=300.0, field_t=0.0)
    forward = dataframe[dataframe["step_name"] == "forward_rxx_rxy"]
    assert not forward.empty
    assert "m1_lockin_x" in forward.columns
    assert "m2_lockin_x" in forward.columns
    assert forward["rxx_ohm"].notna().all()
    assert forward["rxy_ohm"].notna().all()
