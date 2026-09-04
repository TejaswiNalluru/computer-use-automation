"""Evidence logging: structured JSONL + screenshots on failure."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from computer_use.safety import Policy


class EvidenceWriter:
    def __init__(self, root: Path, run_name: str, policy: Policy | None = None):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.dir = root / f"{stamp}_{run_name}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.dir / "events.jsonl"
        self.policy = policy
        self._write_meta({"run_name": run_name, "started_at": stamp})

    def _write_meta(self, meta: dict[str, Any]) -> None:
        (self.dir / "meta.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )

    def event(self, kind: str, **payload: Any) -> None:
        if self.policy:
            payload = self.policy.redact_mapping(payload)
            if "text" in payload and isinstance(payload["text"], str):
                payload["text"] = self.policy.redact_text(payload["text"])
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            **payload,
        }
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")

    def save_json(self, name: str, data: Any) -> Path:
        path = self.dir / name
        if self.policy and isinstance(data, dict):
            data = self.policy.redact_mapping(data)
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        return path

    def screenshot_path(self, label: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
        return self.dir / f"{safe}.png"
