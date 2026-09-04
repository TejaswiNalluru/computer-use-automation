"""Typed capability artifact schema and run result contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class ActionType(str, Enum):
    NAVIGATE = "navigate"
    CLICK = "click"
    FILL = "fill"
    PRESS = "press"
    EXTRACT = "extract"
    WAIT = "wait"
    ASSERT = "assert"
    DISMISS_DIALOG = "dismiss_dialog"
    CONFIRM_DIALOG = "confirm_dialog"


class LocatorStrategy(str, Enum):
    ROLE = "role"
    LABEL = "label"
    TEXT = "text"
    PLACEHOLDER = "placeholder"
    CSS = "css"
    TEST_ID = "test_id"


class Locator(BaseModel):
    """How to find a control. Prefer semantic strategies over CSS."""

    strategy: LocatorStrategy
    value: str
    role: str | None = None
    name: str | None = None
    exact: bool = False
    nth: int | None = None
    rationale: str = ""


class ParamSpec(BaseModel):
    name: str
    type: Literal["string", "number", "boolean"] = "string"
    description: str = ""
    required: bool = True
    pattern: str | None = None
    example: str | None = None


class OutputSpec(BaseModel):
    name: str
    type: Literal["string", "number", "boolean"] = "string"
    description: str = ""
    locator: Locator | None = None


class Step(BaseModel):
    id: str
    action: ActionType
    description: str = ""
    url: str | None = None
    target: Locator | None = None
    value: str | None = None  # may contain {{param}} templates
    key: str | None = None
    output: str | None = None
    timeout_ms: int = 10_000
    wait_for: Literal["load", "networkidle", "domcontentloaded"] | None = None


class OutcomeKind(str, Enum):
    BUSINESS = "business"  # expected caller-visible result
    RECOVERABLE = "recoverable"
    HARD_FAILURE = "hard_failure"


class OutcomeMatcher(BaseModel):
    text_present: str | None = None
    text_absent: str | None = None
    url_contains: str | None = None
    css_present: str | None = None


class Outcome(BaseModel):
    id: str
    kind: OutcomeKind
    when: OutcomeMatcher
    message: str
    stop: bool = True


class Checkpoint(BaseModel):
    description: str = ""
    text_present: str | None = None
    url_contains: str | None = None
    css_present: str | None = None


class TargetApp(BaseModel):
    kind: Literal["web"] = "web"
    entry_url: str
    allowed_hosts: list[str] = Field(default_factory=list)


class CapabilityArtifact(BaseModel):
    """Versioned, reviewable, agent-invocable capability."""

    schema_version: str = "1.0"
    id: str
    name: str
    description: str
    version: int = 1
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    target: TargetApp
    parameters: list[ParamSpec] = Field(default_factory=list)
    outputs: list[OutputSpec] = Field(default_factory=list)
    steps: list[Step]
    checkpoint: Checkpoint
    outcomes: list[Outcome] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    notes: str = ""


class ResultStatus(str, Enum):
    SUCCESS = "success"
    BUSINESS_OUTCOME = "business_outcome"
    RECOVERABLE = "recoverable"
    HARD_FAILURE = "hard_failure"
    ESCALATED = "escalated"
    BLOCKED_BY_POLICY = "blocked_by_policy"


class StepEvidence(BaseModel):
    step_id: str
    action: str
    ok: bool
    detail: str = ""
    url: str | None = None
    screenshot: str | None = None
    observed_text_snippet: str | None = None


class RunResult(BaseModel):
    status: ResultStatus
    capability_id: str | None = None
    mode: Literal["discover", "replay", "handoff"] = "replay"
    outputs: dict[str, Any] = Field(default_factory=dict)
    outcome_id: str | None = None
    message: str = ""
    steps: list[StepEvidence] = Field(default_factory=list)
    escalation_id: str | None = None
    evidence_dir: str | None = None
    started_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    finished_at: str | None = None


class AgentAction(BaseModel):
    """Structured action proposed by the LLM during discovery."""

    action: ActionType
    rationale: str = ""
    url: str | None = None
    target: Locator | None = None
    value: str | None = None
    key: str | None = None
    output: str | None = None
    done: bool = False
    success: bool | None = None
    escalate: bool = False
    escalate_reason: str | None = None
