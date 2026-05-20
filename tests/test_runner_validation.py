import pytest

from electrical_measurements.runners.run_measurement import build_run_namespace, validate_run_inputs
from electrical_measurements.switching.contact_map import ContactMap


def test_validate_run_inputs_rejects_negative_settle():
    args = build_run_namespace(protocol="hall", mock=True, temperatures="300", fields="0", settle=-0.1)
    contact_map = ContactMap.from_yaml("configs/contact_maps/hallbar_6contacts_7709.yaml")
    with pytest.raises(ValueError, match="settle"):
        validate_run_inputs(args, contact_map)


def test_validate_run_inputs_rejects_unknown_state():
    args = build_run_namespace(protocol="hall", mock=True, temperatures="300", fields="0", state_name="missing_state")
    contact_map = ContactMap.from_yaml("configs/contact_maps/hallbar_6contacts_7709.yaml")
    with pytest.raises(ValueError, match="Unknown --state-name"):
        validate_run_inputs(args, contact_map)


def test_validate_run_inputs_rejects_non_positive_current():
    args = build_run_namespace(protocol="hallbar_mr", mock=True, temperatures="300", fields="0", current=0.0)
    contact_map = ContactMap.from_yaml("configs/contact_maps/hallbar_6contacts_7709.yaml")
    with pytest.raises(ValueError, match="current"):
        validate_run_inputs(args, contact_map)
