"""LLM-driven discovery loop → capability artifact."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from computer_use.config import Settings, get_settings
from computer_use.evidence import EvidenceWriter
from computer_use.llm import LLMClient
from computer_use.models import (
    ActionType,
    CapabilityArtifact,
    Checkpoint,
    Outcome,
    OutcomeKind,
    OutcomeMatcher,
    OutputSpec,
    ParamSpec,
    ResultStatus,
    RunResult,
    Step,
    StepEvidence,
    TargetApp,
)
from computer_use.safety import Policy, PolicyViolation
from computer_use.surface import WebSurface
from computer_use.templates_util import default_lookup_artifact, render_template


class DiscoveryAgent:
    def __init__(self, settings: Settings | None = None, policy: Policy | None = None):
        self.settings = settings or get_settings()
        self.policy = policy or Policy(self.settings.policy_path)
        self.llm = LLMClient(self.settings)

    async def run(
        self,
        goal: str,
        *,
        params: dict[str, str] | None = None,
        entry_url: str | None = None,
        use_seed_artifact: bool = False,
    ) -> tuple[RunResult, CapabilityArtifact | None]:
        params = params or {}
        entry_url = entry_url or self.settings.target_url
        evidence = EvidenceWriter(
            self.settings.evidence_dir, "discover", policy=self.policy
        )
        evidence.event(
            "start",
            goal=goal,
            params=params,
            entry_url=entry_url,
            llm_provider=self.llm.provider,
            llm_model=self.settings.openai_model if self.llm.provider == "openai" else None,
        )

        if use_seed_artifact:
            artifact = default_lookup_artifact(entry_url)
            path = self._save_artifact(artifact)
            evidence.save_json("artifact.json", artifact.model_dump())
            evidence.event("seed_artifact", path=str(path))
            result = RunResult(
                status=ResultStatus.SUCCESS,
                capability_id=artifact.id,
                mode="discover",
                message=f"Seeded artifact written to {path}",
                evidence_dir=str(evidence.dir),
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
            evidence.save_json("result.json", result.model_dump())
            return result, artifact

        surface = WebSurface(
            policy=self.policy,
            headless=self.settings.headless,
            entry_url=entry_url,
        )
        history: list[dict[str, Any]] = []
        recorded: list[Step] = []
        outputs: dict[str, Any] = {}
        step_evidence: list[StepEvidence] = []

        await surface.start()
        try:
            await surface.navigate(entry_url)
            recorded.append(
                Step(
                    id="s1",
                    action=ActionType.NAVIGATE,
                    description="Open entry URL",
                    url=entry_url,
                )
            )
            evidence.event("navigate", url=entry_url)

            for i in range(self.settings.max_agent_steps):
                state = await surface.observe()
                evidence.event(
                    "observe",
                    url=state.url,
                    dialog=state.dialog_present,
                    text=state.text[:1500],
                )
                action = await self.llm.next_action(
                    goal=goal, state=state, history=history, params=params
                )
                evidence.event(
                    "decide",
                    action=action.model_dump(),
                    llm_provider=self.llm.provider,
                )

                if action.escalate:
                    shot = evidence.screenshot_path(f"escalate_{i}")
                    await surface.screenshot(str(shot))
                    from computer_use.escalation import EscalationManager

                    esc = EscalationManager(self.settings).create(
                        reason=action.escalate_reason or "agent stuck",
                        goal=goal,
                        url=state.url,
                        screenshot=str(shot),
                        step=f"discover-{i}",
                    )
                    result = RunResult(
                        status=ResultStatus.ESCALATED,
                        mode="discover",
                        message=action.escalate_reason or "escalated",
                        steps=step_evidence,
                        escalation_id=esc.id,
                        evidence_dir=str(evidence.dir),
                        finished_at=datetime.now(timezone.utc).isoformat(),
                    )
                    evidence.save_json("result.json", result.model_dump())
                    return result, None

                if action.done and action.action == ActionType.ASSERT:
                    break

                try:
                    self.policy.check_action(action.action)
                    if action.target:
                        hint = action.target.name or action.target.value or ""
                        self.policy.check_locator_risk(action.target, hint)
                    detail = await self._exec(surface, action, params, outputs)
                    await surface.guard_navigation()
                except PolicyViolation as e:
                    shot = evidence.screenshot_path(f"blocked_{i}")
                    await surface.screenshot(str(shot))
                    step_evidence.append(
                        StepEvidence(
                            step_id=f"d{i}",
                            action=action.action.value,
                            ok=False,
                            detail=e.message,
                            url=surface.current_url(),
                            screenshot=str(shot),
                        )
                    )
                    result = RunResult(
                        status=ResultStatus.BLOCKED_BY_POLICY,
                        mode="discover",
                        message=e.message,
                        steps=step_evidence,
                        outputs=outputs,
                        evidence_dir=str(evidence.dir),
                        finished_at=datetime.now(timezone.utc).isoformat(),
                    )
                    evidence.save_json("result.json", result.model_dump())
                    return result, None
                except Exception as e:
                    shot = evidence.screenshot_path(f"fail_{i}")
                    await surface.screenshot(str(shot))
                    step_evidence.append(
                        StepEvidence(
                            step_id=f"d{i}",
                            action=action.action.value,
                            ok=False,
                            detail=str(e),
                            url=surface.current_url(),
                            screenshot=str(shot),
                        )
                    )
                    evidence.event("error", error=str(e))
                    result = RunResult(
                        status=ResultStatus.HARD_FAILURE,
                        mode="discover",
                        message=str(e),
                        steps=step_evidence,
                        outputs=outputs,
                        evidence_dir=str(evidence.dir),
                        finished_at=datetime.now(timezone.utc).isoformat(),
                    )
                    evidence.save_json("result.json", result.model_dump())
                    return result, None

                step_id = f"s{len(recorded) + 1}"
                recorded.append(self._to_step(step_id, action, params))
                hist = {
                    "action": action.action.value,
                    "rationale": action.rationale,
                    "output": action.output,
                    "result": detail,
                }
                history.append(hist)
                step_evidence.append(
                    StepEvidence(
                        step_id=step_id,
                        action=action.action.value,
                        ok=True,
                        detail=str(detail)[:500],
                        url=surface.current_url(),
                    )
                )
                evidence.event("act", **hist)

                if action.done:
                    break
            else:
                shot = evidence.screenshot_path("max_steps")
                await surface.screenshot(str(shot))
                result = RunResult(
                    status=ResultStatus.HARD_FAILURE,
                    mode="discover",
                    message="Max agent steps reached",
                    steps=step_evidence,
                    outputs=outputs,
                    evidence_dir=str(evidence.dir),
                    finished_at=datetime.now(timezone.utc).isoformat(),
                )
                evidence.save_json("result.json", result.model_dump())
                return result, None

            artifact = self._build_artifact(goal, entry_url, params, recorded, outputs)
            path = self._save_artifact(artifact)
            evidence.save_json("artifact.json", artifact.model_dump())
            evidence.event("artifact_saved", path=str(path), outputs=outputs)

            result = RunResult(
                status=ResultStatus.SUCCESS,
                capability_id=artifact.id,
                mode="discover",
                outputs=outputs,
                message=f"Discovery complete; artifact={path}",
                steps=step_evidence,
                evidence_dir=str(evidence.dir),
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
            evidence.save_json("result.json", result.model_dump())
            return result, artifact
        finally:
            await surface.stop()

    async def _exec(
        self,
        surface: WebSurface,
        action,
        params: dict[str, str],
        outputs: dict[str, Any],
    ) -> Any:
        value = render_template(action.value, params)
        match action.action:
            case ActionType.NAVIGATE:
                url = render_template(action.url, params) or surface.entry_url
                await surface.navigate(url)
                return url
            case ActionType.CLICK:
                assert action.target
                await surface.click(action.target)
                return "clicked"
            case ActionType.FILL:
                assert action.target and value is not None
                await surface.fill(action.target, value)
                return value
            case ActionType.PRESS:
                await surface.press(action.key or "Enter")
                return action.key
            case ActionType.EXTRACT:
                assert action.target
                text = await surface.extract(action.target)
                if action.output:
                    outputs[action.output] = text
                return text
            case ActionType.WAIT:
                if value:
                    await surface.wait_for_text(value)
                return value
            case ActionType.DISMISS_DIALOG:
                return await surface.dismiss_dialog_if_present()
            case ActionType.CONFIRM_DIALOG:
                return await surface.confirm_dialog_if_present()
            case ActionType.ASSERT:
                return value
            case _:
                raise ValueError(f"Unsupported action: {action.action}")

    def _to_step(self, step_id: str, action, params: dict[str, str]) -> Step:
        value = action.value
        # Parameterize member_id if present in fill value
        if value and params:
            for k, v in params.items():
                if v and value == v:
                    value = "{{" + k + "}}"
        return Step(
            id=step_id,
            action=action.action,
            description=action.rationale,
            url=action.url,
            target=action.target,
            value=value,
            key=action.key,
            output=action.output,
        )

    def _build_artifact(
        self,
        goal: str,
        entry_url: str,
        params: dict[str, str],
        steps: list[Step],
        outputs: dict[str, Any],
    ) -> CapabilityArtifact:
        param_specs = [
            ParamSpec(
                name=k,
                description=f"Input parameter {k}",
                example=v,
                pattern=r"^M-\d{4}$" if k == "member_id" else None,
            )
            for k, v in params.items()
        ]
        if not param_specs and "member" in goal.lower():
            param_specs = [
                ParamSpec(
                    name="member_id",
                    description="Member ID",
                    example="M-1001",
                    pattern=r"^M-\d{4}$",
                )
            ]
        output_specs = [
            OutputSpec(name=k, description=f"Extracted {k}") for k in outputs
        ]
        return CapabilityArtifact(
            id="lookup_savings_balance",
            name="Lookup member savings balance",
            description=goal,
            target=TargetApp(
                entry_url=entry_url,
                allowed_hosts=["127.0.0.1:3000", "localhost:3000"],
            ),
            parameters=param_specs,
            outputs=output_specs,
            steps=steps,
            checkpoint=Checkpoint(
                description="Detail page shows savings balance",
                text_present="Savings balance",
                url_contains="/members/",
            ),
            outcomes=[
                Outcome(
                    id="not_found",
                    kind=OutcomeKind.BUSINESS,
                    when=OutcomeMatcher(text_present="No member with that ID"),
                    message="No member with that ID",
                ),
                Outcome(
                    id="invalid_member_id",
                    kind=OutcomeKind.BUSINESS,
                    when=OutcomeMatcher(text_present="Invalid member ID"),
                    message="Invalid member ID format",
                ),
            ],
            tags=["demo_target"],
            notes="Produced by discovery agent",
        )

    def _save_artifact(self, artifact: CapabilityArtifact) -> Path:
        self.settings.capabilities_dir.mkdir(parents=True, exist_ok=True)
        path = self.settings.capabilities_dir / f"{artifact.id}.json"
        path.write_text(
            json.dumps(artifact.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )
        return path
