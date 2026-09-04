"""Edge-case demo scenarios for evidence generation."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from computer_use.config import Settings, get_settings
from computer_use.models import CapabilityArtifact, ResultStatus
from computer_use.replay import ReplayEngine
from computer_use.templates_util import default_lookup_artifact


@dataclass
class ScenarioResult:
    name: str
    status: str
    outcome_id: str | None
    evidence_dir: str | None
    ok: bool
    llm_provider: str | None = None


def base_url(settings: Settings | None = None) -> str:
    return (settings or get_settings()).target_url.rstrip("/")


def write_capability(artifact: CapabilityArtifact, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(artifact.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    return path


def lookup_artifact(entry_url: str) -> CapabilityArtifact:
    return default_lookup_artifact(entry_url)


def lookup_without_dismiss(entry_url: str) -> CapabilityArtifact:
    artifact = default_lookup_artifact(entry_url)
    artifact.id = "lookup_without_dismiss"
    artifact.name = "Lookup savings balance (no dismiss step)"
    artifact.steps = [s for s in artifact.steps if s.action.value != "dismiss_dialog"]
    # No recoverable matcher — forces handoff when overlay blocks interaction.
    from computer_use.models import OutcomeKind

    artifact.outcomes = [
        o for o in artifact.outcomes if o.kind != OutcomeKind.RECOVERABLE
    ]
    return artifact


def lookup_broken_checkpoint(entry_url: str) -> CapabilityArtifact:
    artifact = default_lookup_artifact(entry_url)
    artifact.id = "lookup_broken_checkpoint"
    artifact.name = "Lookup savings balance (broken checkpoint)"
    artifact.checkpoint.text_present = "IMPOSSIBLE_CHECKPOINT_TEXT"
    return artifact


def navigate_close_account(entry_url: str) -> CapabilityArtifact:
    from computer_use.models import ActionType, Checkpoint, Step, TargetApp

    member_id = "M-1001"
    return CapabilityArtifact(
        id="navigate_close_account",
        name="Navigate to close-account (policy block demo)",
        description="Attempts to open the irreversible close-account route.",
        target=TargetApp(
            entry_url=entry_url,
            allowed_hosts=["127.0.0.1:3000", "localhost:3000"],
        ),
        parameters=[],
        outputs=[],
        steps=[
            Step(
                id="s1",
                action=ActionType.NAVIGATE,
                description="Open close-account route",
                url=f"{entry_url.rstrip('/')}/members/{member_id}/close",
            )
        ],
        checkpoint=Checkpoint(
            description="Should never reach",
            text_present="Close account",
        ),
    )


def ensure_capability_files(settings: Settings | None = None) -> dict[str, Path]:
    settings = settings or get_settings()
    root = settings.capabilities_dir
    entry = settings.target_url
    paths = {
        "lookup": write_capability(lookup_artifact(entry), root / "lookup_savings_balance.json"),
        "no_dismiss": write_capability(
            lookup_without_dismiss(entry), root / "lookup_without_dismiss.json"
        ),
        "broken_checkpoint": write_capability(
            lookup_broken_checkpoint(entry), root / "lookup_broken_checkpoint.json"
        ),
        "close_nav": write_capability(
            navigate_close_account(entry), root / "navigate_close_account.json"
        ),
    }
    return paths


async def run_discover_demo(settings: Settings | None = None) -> ScenarioResult:
    """Run LLM discovery against live demo_target (OpenAI if key set, else mock)."""
    settings = settings or get_settings()
    from computer_use.agent import DiscoveryAgent

    goal = "look up member M-1001 and read their savings balance"
    result, artifact = await DiscoveryAgent(settings).run(
        goal,
        params={"member_id": "M-1001"},
    )
    provider = "mock"
    if result.evidence_dir:
        events = Path(result.evidence_dir) / "events.jsonl"
        if events.exists():
            first = events.read_text(encoding="utf-8").splitlines()[0]
            import json as _json

            provider = _json.loads(first).get("llm_provider", provider)
    ok = result.status.value == "success" and artifact is not None
    return ScenarioResult(
        name="discover",
        status=result.status.value,
        outcome_id=result.outcome_id,
        evidence_dir=result.evidence_dir,
        ok=ok,
        llm_provider=provider,
    )


async def run_edge_case_demos(settings: Settings | None = None) -> list[ScenarioResult]:
    settings = settings or get_settings()
    paths = ensure_capability_files(settings)
    engine = ReplayEngine(settings)
    base = base_url(settings)
    results: list[ScenarioResult] = []

    async def run_case(
        name: str,
        artifact_path: Path,
        params: dict[str, Any] | None = None,
        *,
        entry_url: str | None = None,
        handoff_wait: bool = False,
        auto_resolve_handoff: bool = False,
        expect_status: set[str] | None = None,
        expect_outcome_id: str | None = None,
    ) -> ScenarioResult:
        params = params or {"member_id": "M-1001"}
        expect_status = expect_status or {"success", "business_outcome"}
        result = await engine.run(
            artifact_path,
            params,
            entry_url_override=entry_url,
            handoff_wait=handoff_wait,
            auto_resolve_handoff=auto_resolve_handoff,
        )
        ok = result.status.value in expect_status
        if expect_outcome_id is not None:
            ok = ok and result.outcome_id == expect_outcome_id
        return ScenarioResult(
            name=name,
            status=result.status.value,
            outcome_id=result.outcome_id,
            evidence_dir=result.evidence_dir,
            ok=ok,
        )

    results.append(
        await run_case(
            "replay_success",
            paths["lookup"],
            {"member_id": "M-1001"},
            expect_status={"success"},
        )
    )
    results.append(
        await run_case(
            "replay_not_found",
            paths["lookup"],
            {"member_id": "M-9999"},
            expect_status={"business_outcome"},
        )
    )
    results.append(
        await run_case(
            "replay_invalid_id",
            paths["lookup"],
            {"member_id": "abc"},
            expect_status={"business_outcome"},
        )
    )
    results.append(
        await run_case(
            "replay_dialog",
            paths["lookup"],
            {"member_id": "M-1001"},
            entry_url=f"{base}/?dialog=1",
            expect_status={"success"},
        )
    )
    results.append(
        await run_case(
            "replay_slow_load",
            paths["lookup"],
            {"member_id": "M-1001"},
            entry_url=f"{base}/?slow=1",
            expect_status={"success"},
        )
    )
    results.append(
        await run_case(
            "policy_close_path",
            paths["close_nav"],
            {},
            expect_status={"blocked_by_policy"},
        )
    )
    results.append(
        await run_case(
            "checkpoint_fail",
            paths["broken_checkpoint"],
            {"member_id": "M-1001"},
            expect_status={"hard_failure"},
        )
    )
    results.append(
        await run_case(
            "handoff_resume",
            write_capability(
                lookup_without_dismiss(f"{base}/?dialog=1"),
                settings.capabilities_dir / "lookup_without_dismiss.json",
            ),
            {"member_id": "M-1001"},
            entry_url=f"{base}/?dialog=1",
            handoff_wait=True,
            auto_resolve_handoff=True,
            expect_status={"success"},
        )
    )
    results.append(
        await run_case(
            "replay_permission_denied",
            paths["lookup"],
            {"member_id": "M-1003"},
            expect_status={"business_outcome"},
            expect_outcome_id="permission_denied",
        )
    )
    results.append(
        await run_case(
            "replay_session_expired",
            write_capability(
                lookup_artifact(f"{base}/?expired=1"),
                settings.capabilities_dir / "lookup_session_expired.json",
            ),
            {"member_id": "M-1001"},
            entry_url=f"{base}/?expired=1",
            expect_status={"success"},
        )
    )
    results.append(
        await run_case(
            "replay_confirm",
            write_capability(
                lookup_artifact(f"{base}/?confirm=1"),
                settings.capabilities_dir / "lookup_with_confirm.json",
            ),
            {"member_id": "M-1001"},
            entry_url=f"{base}/?confirm=1",
            expect_status={"success"},
        )
    )
    return results


def publish_examples(results: list[ScenarioResult], settings: Settings | None = None) -> None:
    """Copy latest evidence runs into evidence/examples/<scenario_name>/."""
    settings = settings or get_settings()
    examples_root = settings.evidence_dir / "examples"
    examples_root.mkdir(parents=True, exist_ok=True)

    for item in results:
        if not item.evidence_dir:
            continue
        src = Path(item.evidence_dir)
        if not src.exists():
            continue
        dest = examples_root / item.name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)

    summary = [
        {
            "name": r.name,
            "status": r.status,
            "outcome_id": r.outcome_id,
            "evidence_dir": r.evidence_dir,
            "ok": r.ok,
            "llm_provider": r.llm_provider,
        }
        for r in results
    ]
    (examples_root / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
