from pathlib import Path

import pytest
import yaml

from electrical_measurements.exceptions import RunnerInputError
from electrical_measurements.io.notifications import HttpWebhookNotifier, build_notifier
from electrical_measurements.runners import run_measurement
from electrical_measurements.runners.run_measurement import build_run_namespace, run_command


class RecordingNotifier:
    def __init__(self) -> None:
        self.messages: list[tuple[str, dict]] = []

    def notify(self, message_type: str, payload: dict) -> None:
        self.messages.append((message_type, payload))


def write_config(tmp_path: Path) -> Path:
    config = {
        "sample_id": "notify-sample",
        "instruments": {
            "m81": {"connection": {"kind": "mock"}},
            "daq6510": {"resource": "MOCK::DAQ6510", "settle_s": 0.0},
            "environment": {"kind": "mock", "mode": "standalone"},
        },
        "output": {"directory": str(tmp_path)},
        "notifications": {"kind": "noop"},
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=True))
    return path


def test_build_notifier_supports_http_webhook():
    notifier = build_notifier(
        {
            "notifications": {
                "kind": "http-webhook",
                "endpoint": "http://localhost/hook",
                "timeout_s": 2.5,
                "headers": {"Authorization": "Bearer token"},
            }
        }
    )
    assert isinstance(notifier, HttpWebhookNotifier)
    assert notifier.endpoint == "http://localhost/hook"
    assert notifier.timeout_s == 2.5
    assert notifier.extra_headers["Authorization"] == "Bearer token"


def test_run_command_sends_measurement_completed_notification(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_path = write_config(tmp_path)
    recorder = RecordingNotifier()
    monkeypatch.setattr(run_measurement, "build_notifier", lambda config: recorder)
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
    )

    result = run_command(args)

    assert result == 0
    assert recorder.messages
    message_type, payload = recorder.messages[-1]
    assert message_type == "measurement_completed"
    assert payload["protocol"] == "hallbar_mr"
    assert payload["sample_id"] == "notify-sample"
    assert payload["output_dir"] == str(tmp_path)
    assert payload["row_count"] > 0
    assert "started_at" in payload
    assert "finished_at" in payload
    assert payload["last_temperature_k"] is not None
    assert payload["last_field_t"] is not None


def test_run_command_sends_measurement_failed_notification(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_path = write_config(tmp_path)
    recorder = RecordingNotifier()
    monkeypatch.setattr(run_measurement, "build_notifier", lambda config: recorder)
    args = build_run_namespace(
        config=str(config_path),
        contact_map="configs/contact_maps/hallbar_6contacts_7709.yaml",
        mock=True,
        output=str(tmp_path),
        protocol="hall",
        temperatures="300",
        fields="0",
        current=0.0,
        frequency=13.7,
        settle=0.0,
    )

    with pytest.raises(RunnerInputError):
        run_command(args)

    assert recorder.messages
    message_type, payload = recorder.messages[-1]
    assert message_type == "measurement_failed"
    assert payload["protocol"] == "hall"
    assert payload["sample_id"] == "notify-sample"
    assert payload["output_dir"] == str(tmp_path)
    assert "started_at" in payload
    assert "failed_at" in payload
    assert "error" in payload
