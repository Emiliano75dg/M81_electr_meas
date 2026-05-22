from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Protocol
from urllib import error, request

from ..exceptions import InstrumentConnectionError, InstrumentTimeoutError


class Notifier(Protocol):
    def notify(self, message_type: str, payload: dict[str, Any]) -> None: ...


@dataclass
class NoOpNotifier:
    def notify(self, message_type: str, payload: dict[str, Any]) -> None:
        return None


@dataclass
class HttpWebhookNotifier:
    endpoint: str
    timeout_s: float = 5.0
    extra_headers: dict[str, str] = field(default_factory=dict)

    def notify(self, message_type: str, payload: dict[str, Any]) -> None:
        body = json.dumps({"message_type": message_type, **payload}).encode("utf-8")
        headers = {"Content-Type": "application/json", **self.extra_headers}
        req = request.Request(self.endpoint, data=body, headers=headers, method="POST")
        try:
            with request.urlopen(req, timeout=self.timeout_s):
                return None
        except TimeoutError as exc:
            raise InstrumentTimeoutError(f"Notification to {self.endpoint} timed out after {self.timeout_s}s") from exc
        except error.URLError as exc:
            raise InstrumentConnectionError(f"Failed to send notification to {self.endpoint}: {exc.reason}") from exc


def build_notifier(config: dict[str, Any]) -> Notifier:
    cfg = config.get("notifications", {})
    kind = str(cfg.get("kind", "noop")).strip().lower()
    if kind in {"", "noop", "none"}:
        return NoOpNotifier()
    if kind in {"http-webhook", "webhook"}:
        return HttpWebhookNotifier(
            endpoint=str(cfg.get("endpoint", "")),
            timeout_s=float(cfg.get("timeout_s", 5.0)),
            extra_headers=dict(cfg.get("headers", {})),
        )
    raise ValueError(f"Unsupported notification kind: {cfg.get('kind')}")
