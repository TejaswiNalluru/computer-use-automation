"""Deterministic capability replay (no LLM in the decision loop)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from computer_use.config import Settings, get_settings
from computer_use.evidence import EvidenceWriter
from computer_use.models import (
    ActionType,
    CapabilityArtifact,
    OutcomeKind,
    ResultStatus,
    RunResult,
    StepEvidence,
)
from computer_use.safety import Policy, PolicyViolation
from computer_use.surface import WebSurface
from computer_use.templates_util import (
    checkpoint_ok,
    match_outcomes,
    render_template,
)


class ReplayEngine:
    def __init__(self, settings: Settings | None = None, policy: Policy | None = None):
        self.settings = settings or get_settings()
        self.policy = policy or Policy(self.settings.policy_path)

    def load(self, path: Path | str) -> CapabilityArtifact:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return CapabilityArtifact.model_validate(data)

    async def run(
        self,
        artifact: CapabilityArtifact | Path | str,
        params: dict[str, Any],
        *,
        entry_url_override: str | None = None,
        handoff_wait: bool = False,
        auto_resolve_handoff: bool = False,
    ) -> RunResult:
        if not isinstance(artifact, CapabilityArtifact):
            artifact = self.load(artifact)

        evidence = EvidenceWriter(
            self.settings.evidence_dir, f"replay_{artifact.id}", policy=self.policy
        )
        safe_params = self.policy.redact_mapping(dict(params))
        evidence.event(
            "start",
            capability_id=artifact.id,
            params=safe_params,
            handoff_wait=handoff_wait,
        )
        evidence.save_json("artifact.json", artifact.model_dump(mode="json"))

        entry = entry_url_override or artifact.target.entry_url
        surface = WebSurface(
            policy=self.policy,
            headless=self.settings.headless,
            entry_url=entry,
        )
        outputs: dict[str, Any] = {}
        steps_ev: list[StepEvidence] = []

        await surface.start()
        try:
            step_index = 0
            while step_index < len(artifact.steps):
                step = artifact.steps[step_index]

                if surface.current_url().startswith("http"):
                    outcome = await match_outcomes(surface, artifact.outcomes)
                    if (
                        outcome
                        and outcome.kind == OutcomeKind.BUSINESS
                        and outcome.stop
                        and step.action != ActionType.NAVIGATE
                        and steps_ev
                    ):
                        return self._save_result(
                            evidence,
                            RunResult(
                                status=ResultStatus.BUSINESS_OUTCOME,
                                capability_id=artifact.id,
                                mode="replay",
                                outcome_id=outcome.id,
                                message=outcome.message,
                                steps=steps_ev,
                                outputs=outputs,
                                evidence_dir=str(evidence.dir),
                                finished_at=datetime.now(timezone.utc).isoformat(),
                            ),
                        )

                    if outcome and outcome.kind == OutcomeKind.RECOVERABLE:
                        await self._apply_recovery(surface, outcome, evidence)

                handoff_used = False
                while True:
                    try:
                        self.policy.check_action(step.action)
                        detail = await self._exec_step(surface, step, params, outputs)
                        await surface.guard_navigation()
                        steps_ev.append(
                            StepEvidence(
                                step_id=step.id,
                                action=step.action.value,
                                ok=True,
                                detail=str(detail)[:500],
                                url=surface.current_url(),
                            )
                        )
                        evidence.event(
                            "step_ok",
                            step_id=step.id,
                            action=step.action.value,
                            detail=str(detail)[:500],
                        )
                        break
                    except PolicyViolation as e:
                        return await self._blocked_result(
                            artifact, step, e.message, steps_ev, evidence, surface
                        )
                    except Exception as e:
                        if handoff_wait and not handoff_used:
                            await self._pause_for_handoff(
                                artifact=artifact,
                                step=step,
                                error=e,
                                surface=surface,
                                evidence=evidence,
                                params=params,
                                auto_resolve=auto_resolve_handoff,
                            )
                            handoff_used = True
                            continue

                        terminal = await self._step_error_result(
                            artifact, step, e, steps_ev, evidence, surface
                        )
                        return terminal

                if step.action in {
                    ActionType.CLICK,
                    ActionType.PRESS,
                    ActionType.NAVIGATE,
                }:
                    outcome = await match_outcomes(surface, artifact.outcomes)
                    if outcome and outcome.kind == OutcomeKind.BUSINESS and outcome.stop:
                        if outcome.id in {
                            "not_found",
                            "invalid_member_id",
                            "permission_denied",
                        }:
                            return self._save_result(
                                evidence,
                                RunResult(
                                    status=ResultStatus.BUSINESS_OUTCOME,
                                    capability_id=artifact.id,
                                    mode="replay",
                                    outcome_id=outcome.id,
                                    message=outcome.message,
                                    steps=steps_ev,
                                    evidence_dir=str(evidence.dir),
                                    finished_at=datetime.now(timezone.utc).isoformat(),
                                ),
                            )

                step_index += 1

            if not await checkpoint_ok(surface, artifact.checkpoint):
                return await self._checkpoint_fail_result(
                    artifact, steps_ev, evidence, surface
                )

            return self._save_result(
                evidence,
                RunResult(
                    status=ResultStatus.SUCCESS,
                    capability_id=artifact.id,
                    mode="replay",
                    outputs=outputs,
                    message="Replay succeeded",
                    steps=steps_ev,
                    evidence_dir=str(evidence.dir),
                    finished_at=datetime.now(timezone.utc).isoformat(),
                ),
            )
        finally:
            await surface.stop()

    async def _pause_for_handoff(
        self,
        *,
        artifact: CapabilityArtifact,
        step,
        error: Exception,
        surface: WebSurface,
        evidence: EvidenceWriter,
        params: dict[str, Any],
        auto_resolve: bool,
    ) -> None:
        from computer_use.escalation import EscalationManager
        from computer_use.models import Locator, LocatorStrategy

        shot = evidence.screenshot_path(f"handoff_{step.id}")
        await surface.screenshot(str(shot))
        esc_mgr = EscalationManager(self.settings)
        esc = esc_mgr.create(
            reason=f"Replay paused at {step.id}: {error}",
            goal=artifact.name,
            url=surface.current_url(),
            screenshot=str(shot),
            step=step.id,
        )
        evidence.event(
            "handoff_pause",
            escalation_id=esc.id,
            step_id=step.id,
            error=str(error),
            screenshot=str(shot),
        )
        surface.paused = True

        if auto_resolve:
            if await surface._page().locator("div.overlay").count() > 0:
                await surface.navigate(surface.entry_without_query())
                esc_mgr.record_human_action(
                    esc.id,
                    {"action": "navigate_clear_overlay", "url": surface.entry_without_query()},
                )
            else:
                dismissed = await surface.dismiss_dialog_if_present()
                esc_mgr.record_human_action(
                    esc.id,
                    {"action": "dismiss_dialog", "ok": dismissed},
                )
            member_id = params.get("member_id")
            if member_id:
                await surface.fill(
                    Locator(
                        strategy=LocatorStrategy.CSS,
                        value='input[name="member_id"]',
                    ),
                    str(member_id),
                )
                esc_mgr.record_human_action(
                    esc.id,
                    {"action": "refill_member_id", "value": member_id},
                )
            esc_mgr.resolve(
                esc.id,
                notes="Auto-resolved for demo: dismissed overlay and re-filled member ID",
            )
        else:
            await esc_mgr.wait_for_resolve(esc.id)

        surface.paused = False
        evidence.event("handoff_resume", escalation_id=esc.id, step_id=step.id)

    async def _blocked_result(
        self,
        artifact: CapabilityArtifact,
        step,
        message: str,
        steps_ev: list[StepEvidence],
        evidence: EvidenceWriter,
        surface: WebSurface,
    ) -> RunResult:
        shot = evidence.screenshot_path(f"blocked_{step.id}")
        await surface.screenshot(str(shot))
        steps_ev.append(
            StepEvidence(
                step_id=step.id,
                action=step.action.value,
                ok=False,
                detail=message,
                url=surface.current_url(),
                screenshot=str(shot),
            )
        )
        return self._save_result(
            evidence,
            RunResult(
                status=ResultStatus.BLOCKED_BY_POLICY,
                capability_id=artifact.id,
                mode="replay",
                message=message,
                steps=steps_ev,
                evidence_dir=str(evidence.dir),
                finished_at=datetime.now(timezone.utc).isoformat(),
            ),
        )

    async def _step_error_result(
        self,
        artifact: CapabilityArtifact,
        step,
        error: Exception,
        steps_ev: list[StepEvidence],
        evidence: EvidenceWriter,
        surface: WebSurface,
    ) -> RunResult:
        outcome = await match_outcomes(surface, artifact.outcomes)
        shot = evidence.screenshot_path(f"fail_{step.id}")
        await surface.screenshot(str(shot))
        body = (await surface.observe()).text
        steps_ev.append(
            StepEvidence(
                step_id=step.id,
                action=step.action.value,
                ok=False,
                detail=str(error),
                url=surface.current_url(),
                screenshot=str(shot),
                observed_text_snippet=body[:800],
            )
        )
        if outcome and outcome.kind == OutcomeKind.BUSINESS:
            return self._save_result(
                evidence,
                RunResult(
                    status=ResultStatus.BUSINESS_OUTCOME,
                    capability_id=artifact.id,
                    mode="replay",
                    outcome_id=outcome.id,
                    message=outcome.message,
                    steps=steps_ev,
                    evidence_dir=str(evidence.dir),
                    finished_at=datetime.now(timezone.utc).isoformat(),
                ),
            )

        evidence.event(
            "step_error",
            step_id=step.id,
            error=str(error),
            screenshot=str(shot),
        )
        from computer_use.escalation import EscalationManager

        esc = EscalationManager(self.settings).create(
            reason=f"Replay failed at {step.id}: {error}",
            goal=artifact.name,
            url=surface.current_url(),
            screenshot=str(shot),
            step=step.id,
        )
        return self._save_result(
            evidence,
            RunResult(
                status=ResultStatus.ESCALATED,
                capability_id=artifact.id,
                mode="replay",
                message=str(error),
                steps=steps_ev,
                escalation_id=esc.id,
                evidence_dir=str(evidence.dir),
                finished_at=datetime.now(timezone.utc).isoformat(),
            ),
        )

    async def _checkpoint_fail_result(
        self,
        artifact: CapabilityArtifact,
        steps_ev: list[StepEvidence],
        evidence: EvidenceWriter,
        surface: WebSurface,
    ) -> RunResult:
        shot = evidence.screenshot_path("checkpoint_fail")
        await surface.screenshot(str(shot))
        state = await surface.observe()
        outcome = await match_outcomes(surface, artifact.outcomes)
        if outcome and outcome.kind == OutcomeKind.BUSINESS:
            result = RunResult(
                status=ResultStatus.BUSINESS_OUTCOME,
                capability_id=artifact.id,
                mode="replay",
                outcome_id=outcome.id,
                message=outcome.message,
                steps=steps_ev,
                evidence_dir=str(evidence.dir),
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
        else:
            result = RunResult(
                status=ResultStatus.HARD_FAILURE,
                capability_id=artifact.id,
                mode="replay",
                message="Checkpoint not satisfied",
                steps=steps_ev
                + [
                    StepEvidence(
                        step_id="checkpoint",
                        action="assert",
                        ok=False,
                        detail=artifact.checkpoint.description,
                        screenshot=str(shot),
                        observed_text_snippet=state.text[:800],
                    )
                ],
                evidence_dir=str(evidence.dir),
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
        return self._save_result(evidence, result)

    @staticmethod
    async def _apply_recovery(
        surface: WebSurface, outcome, evidence: EvidenceWriter
    ) -> None:
        if outcome.id in {"maintenance_dialog"} or "maintenance" in outcome.id:
            await surface.dismiss_dialog_if_present()
        elif outcome.id == "search_confirm":
            await surface.confirm_dialog_if_present()
        elif outcome.id == "session_expired":
            await surface.navigate(surface.entry_without_query())
        evidence.event("recover", outcome=outcome.id)

    @staticmethod
    def _save_result(evidence: EvidenceWriter, result: RunResult) -> RunResult:
        evidence.save_json("result.json", result.model_dump())
        return result

    async def _exec_step(
        self,
        surface: WebSurface,
        step,
        params: dict[str, Any],
        outputs: dict[str, Any],
    ) -> Any:
        value = render_template(step.value, params)
        match step.action:
            case ActionType.NAVIGATE:
                url = render_template(step.url, params) or surface.entry_url
                assert url
                await surface.navigate(url)
                return url
            case ActionType.CLICK:
                assert step.target
                self.policy.check_locator_risk(step.target)
                await surface.click(step.target)
                return "clicked"
            case ActionType.FILL:
                assert step.target and value is not None
                self.policy.check_locator_risk(step.target)
                await surface.fill(step.target, value)
                return value
            case ActionType.PRESS:
                await surface.press(step.key or "Enter")
                return step.key
            case ActionType.EXTRACT:
                assert step.target
                text = await surface.extract(step.target)
                if step.output:
                    outputs[step.output] = text
                return text
            case ActionType.WAIT:
                if value:
                    await surface.wait_for_text(value, timeout_ms=step.timeout_ms)
                return value
            case ActionType.DISMISS_DIALOG:
                return await surface.dismiss_dialog_if_present()
            case ActionType.CONFIRM_DIALOG:
                return await surface.confirm_dialog_if_present()
            case ActionType.ASSERT:
                return value
            case _:
                raise ValueError(f"Unsupported action: {step.action}")
