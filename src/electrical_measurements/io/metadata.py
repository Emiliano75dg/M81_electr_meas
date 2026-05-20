from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from typing import Any


def git_commit_or_none() -> str | None:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True)
        return result.stdout.strip()
    except Exception:
        return None


def build_metadata(config: dict[str, Any], contact_map: Any, notes: str | None = None) -> dict[str, Any]:
    return {
        "sample_id": config.get("sample_id"),
        "geometry": contact_map.name,
        "yaml_file": str(contact_map.path),
        "mapping_contacts": contact_map.contacts,
        "mapping_relay": {name: contact_map.get_state(name).get("relay_channels", []) for name in contact_map.states},
        "instruments": config.get("instruments", {}),
        "driver_versions": {"git_commit": git_commit_or_none()},
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "user": config.get("user"),
        "notes": notes,
    }


def write_metadata(metadata: dict[str, Any], output_dir: str | Path, stem: str = "measurement") -> Path:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    path = output_path / f"{stem}.metadata.json"
    path.write_text(json.dumps(metadata, indent=2, sort_keys=True))
    return path
