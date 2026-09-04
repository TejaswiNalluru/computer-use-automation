"""LLM client for discovery. Supports OpenAI API + offline mock for demo_target."""

from __future__ import annotations

import json
import re
from typing import Any

from computer_use.config import Settings
from computer_use.models import (
    ActionType,
    AgentAction,
    Locator,
    LocatorStrategy,
)
from computer_use.surface import ObservedState

SYSTEM_PROMPT = """You are a computer-use agent driving a legacy bank member-servicing UI.
You receive an accessibility-oriented snapshot and page text. Propose ONE next action as JSON.

Allowed actions: navigate, click, fill, press, extract, wait, assert, dismiss_dialog.
Prefer semantic locators: role, label, text — avoid brittle CSS when possible.
Never click "Close account" or any irreversible action.
If a maintenance overlay/dialog is present, dismiss it first.
When the goal is satisfied, set done=true and success=true and include any extract outputs already collected.
If stuck after reasonable attempts, set escalate=true with escalate_reason.

Return ONLY compact JSON matching this schema:
{
  "action": "fill|click|navigate|extract|dismiss_dialog|wait|assert|press",
  "rationale": "short why",
  "url": "optional",
  "target": {"strategy":"role|label|text|css","value":"...","role":"optional","name":"optional","exact":false,"rationale":"..."},
  "value": "optional fill value or wait text",
  "key": "optional key for press",
  "output": "optional output name for extract",
  "done": false,
  "success": null,
  "escalate": false,
  "escalate_reason": null
}
"""


class LLMClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def provider(self) -> str:
        if self.settings.mock_llm or not self.settings.openai_api_key:
            return "mock"
        return "openai"

    async def next_action(
        self,
        *,
        goal: str,
        state: ObservedState,
        history: list[dict[str, Any]],
        params: dict[str, str],
    ) -> AgentAction:
        if self.settings.mock_llm or not self.settings.openai_api_key:
            return mock_next_action(goal=goal, state=state, history=history, params=params)

        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=self.settings.openai_api_key,
            base_url=self.settings.openai_base_url or None,
        )
        user = {
            "goal": goal,
            "params": params,
            "url": state.url,
            "title": state.title,
            "dialog_present": state.dialog_present,
            "page_text": state.text[:4000],
            "a11y_snapshot": state.a11y_snapshot[:6000],
            "history": history[-8:],
        }
        resp = await client.chat.completions.create(
            model=self.settings.openai_model,
            temperature=0,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(user)},
            ],
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content or "{}"
        return AgentAction.model_validate(json.loads(content))


def mock_next_action(
    *,
    goal: str,
    state: ObservedState,
    history: list[dict[str, Any]],
    params: dict[str, str],
) -> AgentAction:
    """Deterministic policy for the local demo_target flow (offline / no API key)."""
    member_id = params.get("member_id", "M-1001")
    text = state.text
    actions_done = [h.get("action") for h in history]

    if state.dialog_present:
        return AgentAction(
            action=ActionType.DISMISS_DIALOG,
            rationale="Dismiss maintenance overlay before continuing",
        )

    if "Member lookup" in text and "Savings balance" not in text and "No member" not in text:
        if "fill" not in actions_done:
            return AgentAction(
                action=ActionType.FILL,
                rationale="Enter member ID into the lookup field",
                target=Locator(
                    strategy=LocatorStrategy.CSS,
                    value='input[name="member_id"]',
                    rationale="Legacy table UI: named input is stable; no label for= association",
                ),
                value=member_id,
            )
        return AgentAction(
            action=ActionType.CLICK,
            rationale="Submit member lookup",
            target=Locator(
                strategy=LocatorStrategy.CSS,
                value='input[type="submit"][value="Search"]',
                rationale="Submit control identified by type+value (legacy pattern)",
            ),
        )

    if "No member with that ID" in text:
        return AgentAction(
            action=ActionType.ASSERT,
            rationale="Business outcome: member not found",
            done=True,
            success=True,
            value="not_found",
        )

    if "Invalid member ID" in text:
        return AgentAction(
            action=ActionType.ASSERT,
            rationale="Business outcome: invalid member ID",
            done=True,
            success=True,
            value="invalid",
        )

    if "Savings balance" in text and "Member detail" in text:
        if "extract" not in actions_done:
            return AgentAction(
                action=ActionType.EXTRACT,
                rationale="Read savings balance from detail table",
                target=Locator(
                    strategy=LocatorStrategy.CSS,
                    value='table.fields tr:has-text("Savings balance") td:nth-child(2)',
                    rationale="Row label → adjacent cell; resilient to column order within row",
                ),
                output="savings_balance",
            )
        if any(h.get("output") == "savings_balance" for h in history) and not any(
            h.get("output") == "member_name" for h in history
        ):
            return AgentAction(
                action=ActionType.EXTRACT,
                rationale="Read member name",
                target=Locator(
                    strategy=LocatorStrategy.CSS,
                    value='table.fields tr:has-text("Name") td:nth-child(2)',
                    rationale="Row label → adjacent cell",
                ),
                output="member_name",
            )
        return AgentAction(
            action=ActionType.ASSERT,
            rationale="Goal complete: balance visible on member detail",
            done=True,
            success=True,
        )

    if not history:
        return AgentAction(
            action=ActionType.NAVIGATE,
            rationale="Open target entry URL",
            url=state.url if state.url.startswith("http") else "/",
        )

    return AgentAction(
        action=ActionType.WAIT,
        rationale="Unrecognized state — escalate",
        escalate=True,
        escalate_reason=f"Unrecognized UI state at {state.url}: {text[:200]}",
        done=True,
        success=False,
    )


def looks_like_lookup_goal(goal: str) -> bool:
    return bool(re.search(r"balance|look\s*up|member", goal, re.I))
