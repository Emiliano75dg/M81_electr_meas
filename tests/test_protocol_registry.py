import pytest

from electrical_measurements.instruments.mock import MockM81Controller
from electrical_measurements.runners.run_measurement import ADVERTISED_PROTOCOLS, build_protocol, build_run_namespace
from electrical_measurements.switching.contact_map import ContactMap
from electrical_measurements.switching.matrix7709 import Matrix7709


@pytest.mark.parametrize(
    ("protocol_name", "contact_map_path"),
    [
        ("hall", "configs/contact_maps/hallbar_6contacts_7709.yaml"),
        ("hallbar_mr", "configs/contact_maps/hallbar_6contacts_7709.yaml"),
        ("vdp", "configs/contact_maps/vdp_4contacts_7709.yaml"),
        ("vdp_hall", "configs/contact_maps/vdp_4contacts_7709.yaml"),
        ("second_harmonic", "configs/contact_maps/hallbar_6contacts_7709.yaml"),
        ("reciprocity", "configs/contact_maps/vdp_4contacts_7709.yaml"),
        ("check_contacts", "configs/contact_maps/hallbar_6contacts_7709.yaml"),
    ],
)
def test_every_advertised_protocol_can_be_built(protocol_name: str, contact_map_path: str):
    assert protocol_name in ADVERTISED_PROTOCOLS
    args = build_run_namespace(
        protocol=protocol_name,
        mock=True,
        temperatures="300",
        fields="0",
        current=1e-5,
        frequency=13.7,
        settle=0.0,
        include_reciprocity=True,
        include_anisotropy=True,
    )
    config = {
        "sample_id": "sample",
        "instruments": {
            "daq6510": {"resource": "MOCK::DAQ6510", "settle_s": 0.0},
            "m81": {"connection": {"kind": "mock"}},
        },
    }
    contact_map = ContactMap.from_yaml(contact_map_path)
    m81 = MockM81Controller()
    matrix = Matrix7709.from_config(config, contact_map=contact_map, m81=m81)

    protocol = build_protocol(args, config, contact_map, m81, matrix)

    assert protocol is not None
