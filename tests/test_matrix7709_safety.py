import pytest

from electrical_measurements.exceptions import MatrixSwitchError
from electrical_measurements.instruments.mock import MockM81Controller
from electrical_measurements.switching.contact_map import ContactMap
from electrical_measurements.switching.matrix7709 import Matrix7709
from electrical_measurements.switching.safety import validate_contact_map_state


def test_matrix_blocks_switching_with_active_source():
    config = {"instruments": {"daq6510": {"resource": "MOCK::DAQ6510"}}}
    contact_map = ContactMap.from_yaml("configs/contact_maps/vdp_4contacts_7709.yaml")
    m81 = MockM81Controller()
    matrix = Matrix7709.from_config(config, contact_map=contact_map, m81=m81)
    m81.enable_source("S1")
    with pytest.raises(MatrixSwitchError):
        matrix.apply_state("I_AB_V_CD")


def test_contact_map_state_validation_passes_for_valid_state():
    contact_map = ContactMap.from_yaml("configs/contact_maps/vdp_4contacts_7709.yaml")
    result = validate_contact_map_state(contact_map, "I_AB_V_CD")
    assert result.ok


class RecordingM81(MockM81Controller):
    def __post_init__(self) -> None:
        super().__post_init__()
        self.events: list[str] = []

    def disable_all_sources(self) -> None:
        self.events.append("disable_all_sources")
        super().disable_all_sources()

    def enable_source(self, source: str) -> None:
        self.events.append(f"enable_source:{source}")
        super().enable_source(source)


class RecordingController:
    def __init__(self) -> None:
        self.events: list[str] = []

    def open_all(self) -> None:
        self.events.append("open_all")

    def close_channels(self, channels: list[int]) -> None:
        self.events.append(f"close_channels:{channels}")

    def apply_state(self, state_name: str, channels: list[int] | None = None) -> None:
        self.events.append(f"controller_apply_state:{state_name}")


def test_apply_state_disables_sources_before_switching_and_never_reenables():
    contact_map = ContactMap.from_yaml("configs/contact_maps/vdp_4contacts_7709.yaml")
    controller = RecordingController()
    m81 = RecordingM81()
    matrix = Matrix7709(controller=controller, contact_map=contact_map, m81=m81, settle_s=0.0)

    m81.enable_source("S1")
    channels = matrix.apply_state("I_AB_V_CD", override_source_enabled=True, reenable_sources=True)

    assert channels == [17, 26, 35, 44]
    assert m81.events[0] == "enable_source:S1"
    assert m81.events[1:] == ["disable_all_sources"]
    assert controller.events == ["open_all", "close_channels:[17, 26, 35, 44]", "controller_apply_state:I_AB_V_CD"]
