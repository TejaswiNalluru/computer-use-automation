"""Shared template + outcome helpers."""

from __future__ import annotations

import re
from typing import Any

from computer_use.models import CapabilityArtifact, Checkpoint, Outcome, OutcomeMatcher
from computer_use.surface import ObservedState, WebSurface


_PARAM_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def render_template(value: str | None, params: dict[str, Any]) -> str | None:
    if value is None:
        return None

    def repl(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in params:
            raise KeyError(f"Missing parameter: {key}")
        return str(params[key])

    return _PARAM_RE.sub(repl, value)


async def match_outcomes(surface: WebSurface, outcomes: list[Outcome]) -> Outcome | None:
    state = await surface.observe()
    for outcome in outcomes:
        if matcher_hits(outcome.when, state):
            return outcome
    return None


def matcher_hits(when: OutcomeMatcher, state: ObservedState) -> bool:
    if when.text_present and when.text_present not in state.text:
        return False
    if when.text_absent and when.text_absent in state.text:
        return False
    if when.url_contains and when.url_contains not in state.url:
        return False
    if when.css_present:
        # best-effort: treat as text/css hint in body HTML via page text only for simplicity
        return when.css_present in state.text or when.css_present in state.a11y_snapshot
    return any(
        [
            when.text_present,
            when.text_absent,
            when.url_contains,
            when.css_present,
        ]
    )


async def checkpoint_ok(surface: WebSurface, checkpoint: Checkpoint) -> bool:
    state = await surface.observe()
    if checkpoint.text_present and checkpoint.text_present not in state.text:
        return False
    if checkpoint.url_contains and checkpoint.url_contains not in state.url:
        return False
    return True


def default_lookup_artifact(entry_url: str) -> CapabilityArtifact:
    from computer_use.models import (
        ActionType,
        Locator,
        LocatorStrategy,
        OutcomeKind,
        OutputSpec,
        ParamSpec,
        Step,
        TargetApp,
    )

    return CapabilityArtifact(
        id="lookup_savings_balance",
        name="Lookup member savings balance",
        description=(
            "Open member lookup, search by member_id, and extract savings balance "
            "and name from the detail screen."
        ),
        target=TargetApp(
            kind="web",
            entry_url=entry_url,
            allowed_hosts=["127.0.0.1:3000", "localhost:3000"],
        ),
        parameters=[
            ParamSpec(
                name="member_id",
                type="string",
                description="Member identifier, e.g. M-1001",
                pattern=r"^M-\d{4}$",
                example="M-1001",
            )
        ],
        outputs=[
            OutputSpec(name="savings_balance", type="string", description="Savings balance text"),
            OutputSpec(name="member_name", type="string", description="Member display name"),
        ],
        steps=[
            Step(
                id="s1",
                action=ActionType.NAVIGATE,
                description="Open member lookup",
                url=entry_url,
            ),
            Step(
                id="s2",
                action=ActionType.WAIT,
                description="Wait for lookup page (covers slow loads)",
                value="Member lookup",
                timeout_ms=15_000,
            ),
            Step(
                id="s3",
                action=ActionType.DISMISS_DIALOG,
                description="Dismiss maintenance dialog if present",
            ),
            Step(
                id="s3b",
                action=ActionType.CONFIRM_DIALOG,
                description="Accept unexpected lookup confirmation if present",
            ),
            Step(
                id="s4",
                action=ActionType.FILL,
                description="Type member ID",
                target=Locator(
                    strategy=LocatorStrategy.CSS,
                    value='input[name="member_id"]',
                    rationale="Named form field; no associated label element in legacy markup",
                ),
                value="{{member_id}}",
            ),
            Step(
                id="s5",
                action=ActionType.CLICK,
                description="Click Search",
                target=Locator(
                    strategy=LocatorStrategy.CSS,
                    value='input[type="submit"][value="Search"]',
                    rationale="Submit by type+value; role name 'Search' also viable",
                ),
            ),
            Step(
                id="s6",
                action=ActionType.EXTRACT,
                description="Extract savings balance",
                target=Locator(
                    strategy=LocatorStrategy.CSS,
                    value='table.fields tr:has-text("Savings balance") td:nth-child(2)',
                    rationale="Label cell → value cell in classic fields table",
                ),
                output="savings_balance",
            ),
            Step(
                id="s7",
                action=ActionType.EXTRACT,
                description="Extract member name",
                target=Locator(
                    strategy=LocatorStrategy.CSS,
                    value='table.fields tr:has-text("Name") td:nth-child(2)',
                    rationale="Label cell → value cell",
                ),
                output="member_name",
            ),
        ],
        checkpoint=Checkpoint(
            description="Member detail with savings balance visible",
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
            Outcome(
                id="maintenance_dialog",
                kind=OutcomeKind.RECOVERABLE,
                when=OutcomeMatcher(text_present="Scheduled maintenance window"),
                message="Maintenance dialog present — dismiss and retry",
                stop=False,
            ),
            Outcome(
                id="search_confirm",
                kind=OutcomeKind.RECOVERABLE,
                when=OutcomeMatcher(text_present="Proceed with member lookup"),
                message="Unexpected confirmation — accept and continue",
                stop=False,
            ),
            Outcome(
                id="session_expired",
                kind=OutcomeKind.RECOVERABLE,
                when=OutcomeMatcher(text_present="Session expired"),
                message="Session expired — return to entry and retry",
                stop=False,
            ),
            Outcome(
                id="permission_denied",
                kind=OutcomeKind.BUSINESS,
                when=OutcomeMatcher(text_present="You do not have permission"),
                message="Permission denied for this member",
            ),
        ],
        tags=["demo_target", "member", "read"],
        notes="Recorded against local Demo Core member servicing console.",
    )
