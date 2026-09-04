"""Human-in-the-loop escalation: pause, hand off live session, resume."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from computer_use.config import Settings, get_settings


@dataclass
class EscalationRequest:
    id: str
    status: str  # open | in_progress | resolved | cancelled
    reason: str
    goal: str
    url: str
    step: str
    screenshot: str | None
    created_at: str
    operator_notes: str = ""
    human_actions: list[dict] = field(default_factory=list)
    resolved_at: str | None = None


class EscalationManager:
    """
    Minimal real control-transfer model:
    - Automation writes an escalation ticket + freezes (caller stops driving).
    - Operator inspects the same browser session (non-headless) or follows
      instructions in the handoff file, then marks resume.
    - `wait_for_resume` polls the ticket until status=resolved.
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.settings.handoff_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, esc_id: str) -> Path:
        return self.settings.handoff_dir / f"{esc_id}.json"

    def create(
        self,
        *,
        reason: str,
        goal: str,
        url: str,
        step: str,
        screenshot: str | None = None,
    ) -> EscalationRequest:
        esc = EscalationRequest(
            id=str(uuid.uuid4())[:8],
            status="open",
            reason=reason,
            goal=goal,
            url=url,
            step=step,
            screenshot=screenshot,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._write(esc)
        # Human-readable operator brief
        brief = self.settings.handoff_dir / f"{esc.id}_OPERATOR.md"
        brief.write_text(
            f"""# Intervention required ({esc.id})

**Status:** {esc.status}
**Reason:** {esc.reason}
**Goal:** {esc.goal}
**URL:** {esc.url}
**Step:** {esc.step}
**Screenshot:** {esc.screenshot or 'n/a'}

## Operator steps
1. Open the live browser session (run with `CUA_HEADLESS=false` so the window stays visible),
   or open the target URL above in a browser attached to the same environment.
2. Perform the manual recovery steps needed.
3. Record what you did, then resolve:

```bash
python -m computer_use.cli handoff resolve {esc.id} --notes "what you did"
```

Automation will resume once status is `resolved`.
""",
            encoding="utf-8",
        )
        return esc

    def get(self, esc_id: str) -> EscalationRequest:
        data = json.loads(self._path(esc_id).read_text(encoding="utf-8"))
        return EscalationRequest(**data)

    def take_control(self, esc_id: str) -> EscalationRequest:
        esc = self.get(esc_id)
        esc.status = "in_progress"
        self._write(esc)
        return esc

    def record_human_action(self, esc_id: str, action: dict) -> EscalationRequest:
        esc = self.get(esc_id)
        esc.human_actions.append(
            {**action, "ts": datetime.now(timezone.utc).isoformat()}
        )
        self._write(esc)
        return esc

    def resolve(self, esc_id: str, notes: str = "") -> EscalationRequest:
        esc = self.get(esc_id)
        esc.status = "resolved"
        esc.operator_notes = notes
        esc.resolved_at = datetime.now(timezone.utc).isoformat()
        self._write(esc)
        return esc

    async def wait_for_resolve(
        self,
        esc_id: str,
        *,
        poll_s: float = 0.5,
        timeout_s: float = 300.0,
    ) -> EscalationRequest:
        """Poll until operator marks the escalation resolved."""
        import asyncio
        import time

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            esc = self.get(esc_id)
            if esc.status == "resolved":
                return esc
            await asyncio.sleep(poll_s)
        raise TimeoutError(f"Escalation {esc_id} not resolved within {timeout_s}s")

    def list_open(self) -> list[EscalationRequest]:
        out: list[EscalationRequest] = []
        for path in self.settings.handoff_dir.glob("*.json"):
            if path.name.endswith("_OPERATOR.md"):
                continue
            esc = EscalationRequest(**json.loads(path.read_text(encoding="utf-8")))
            if esc.status in {"open", "in_progress"}:
                out.append(esc)
        return out

    def _write(self, esc: EscalationRequest) -> None:
        self._path(esc.id).write_text(
            json.dumps(asdict(esc), indent=2), encoding="utf-8"
        )
