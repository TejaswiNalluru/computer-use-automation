"""CLI: discover / replay / handoff / seed."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import typer
from rich import print as rprint

from computer_use.config import get_settings

app = typer.Typer(
    name="cua",
    help="Computer-use automation: discover → artifact → deterministic replay",
    add_completion=False,
)
handoff_app = typer.Typer(help="Human-in-the-loop escalation controls")
app.add_typer(handoff_app, name="handoff")


@app.command()
def discover(
    goal: str = typer.Argument(..., help="Natural-language goal"),
    member_id: str = typer.Option("M-1001", help="Member ID parameter"),
    url: Optional[str] = typer.Option(None, help="Override target entry URL"),
    seed: bool = typer.Option(
        False, help="Write canonical seed artifact without driving the browser"
    ),
    headed: bool = typer.Option(False, help="Show browser window"),
) -> None:
    """Run LLM (or mock) discovery and save a capability artifact."""
    settings = get_settings()
    if headed:
        settings.headless = False
    from computer_use.agent import DiscoveryAgent

    params = {"member_id": member_id}
    result, artifact = asyncio.run(
        DiscoveryAgent(settings).run(
            goal, params=params, entry_url=url, use_seed_artifact=seed
        )
    )
    rprint(json.dumps(result.model_dump(), indent=2))
    if artifact:
        rprint(f"[green]Artifact:[/green] capabilities/{artifact.id}.json")


@app.command()
def replay(
    artifact: Path = typer.Argument(
        ..., help="Path to capability JSON (e.g. capabilities/lookup_savings_balance.json)"
    ),
    member_id: str = typer.Option("M-1001", help="member_id parameter"),
    entry_url: Optional[str] = typer.Option(
        None, help="Override entry URL (e.g. http://127.0.0.1:3000/?dialog=1)"
    ),
    handoff_wait: bool = typer.Option(
        False, help="Pause on step failure and wait for handoff resolve"
    ),
    auto_resolve_handoff: bool = typer.Option(
        False, help="Auto-dismiss dialog when handoff_wait is enabled (demo only)"
    ),
    headed: bool = typer.Option(False, help="Show browser window"),
) -> None:
    """Deterministically replay a saved capability (no LLM decisions)."""
    settings = get_settings()
    if headed:
        settings.headless = False
    from computer_use.replay import ReplayEngine

    result = asyncio.run(
        ReplayEngine(settings).run(
            artifact,
            {"member_id": member_id},
            entry_url_override=entry_url,
            handoff_wait=handoff_wait,
            auto_resolve_handoff=auto_resolve_handoff,
        )
    )
    rprint(json.dumps(result.model_dump(), indent=2))
    raise SystemExit(0 if result.status.value in {"success", "business_outcome"} else 1)


@app.command("demo")
def demo_flow(
    member_id: str = typer.Option("M-1001", help="Happy-path member"),
    bad_member_id: str = typer.Option("M-9999", help="Not-found member for error path"),
) -> None:
    """End-to-end: seed artifact → replay success → replay business outcome."""
    settings = get_settings()
    from computer_use.agent import DiscoveryAgent
    from computer_use.replay import ReplayEngine

    async def _run() -> None:
        agent = DiscoveryAgent(settings)
        result, artifact = await agent.run(
            "look up member and read savings balance",
            params={"member_id": member_id},
            use_seed_artifact=True,
        )
        rprint("[bold]seed[/bold]", result.status.value)
        assert artifact
        engine = ReplayEngine(settings)
        ok = await engine.run(artifact, {"member_id": member_id})
        rprint("[bold]replay ok[/bold]", ok.status.value, ok.outputs)
        bad = await engine.run(artifact, {"member_id": bad_member_id})
        rprint("[bold]replay not-found[/bold]", bad.status.value, bad.outcome_id)

    asyncio.run(_run())


@app.command("edge-cases")
def edge_cases_demo(
    publish: bool = typer.Option(
        True, help="Copy evidence runs to evidence/examples/<scenario>/"
    ),
) -> None:
    """Run all edge-case replays and optionally publish evidence examples."""
    settings = get_settings()
    from computer_use.demo_scenarios import publish_examples, run_edge_case_demos

    results = asyncio.run(run_edge_case_demos(settings))
    for item in results:
        mark = "[green]ok[/green]" if item.ok else "[red]fail[/red]"
        rprint(
            f"{mark} {item.name}: {item.status}"
            + (f" ({item.outcome_id})" if item.outcome_id else "")
        )
    if publish:
        publish_examples(results, settings)
        rprint("[bold]Published[/bold] evidence/examples/")
    failed = [r.name for r in results if not r.ok]
    raise SystemExit(1 if failed else 0)


@app.command("submit-prep")
def submit_prep(
    skip_discover: bool = typer.Option(
        False, help="Skip discovery run (replay edge cases only)"
    ),
) -> None:
    """Run discovery + all edge cases and publish evidence/examples/ for submission."""
    settings = get_settings()
    from computer_use.demo_scenarios import (
        publish_examples,
        run_discover_demo,
        run_edge_case_demos,
    )

    all_results: list = []

    if not skip_discover:
        discover = asyncio.run(run_discover_demo(settings))
        all_results.append(discover)
        mark = "[green]ok[/green]" if discover.ok else "[red]fail[/red]"
        rprint(
            f"{mark} discover: {discover.status} "
            f"(llm_provider={discover.llm_provider})"
        )
        if discover.llm_provider == "mock":
            rprint(
                "[yellow]Warning:[/yellow] discovery used mock LLM. "
                "Set OPENAI_API_KEY and CUA_MOCK_LLM=0 in .env for submission."
            )

    replay_results = asyncio.run(run_edge_case_demos(settings))
    all_results.extend(replay_results)
    for item in replay_results:
        mark = "[green]ok[/green]" if item.ok else "[red]fail[/red]"
        rprint(
            f"{mark} {item.name}: {item.status}"
            + (f" ({item.outcome_id})" if item.outcome_id else "")
        )

    publish_examples(all_results, settings)
    rprint("[bold]Published[/bold] evidence/examples/ (see summary.json)")

    failed = [r.name for r in all_results if not r.ok]
    raise SystemExit(1 if failed else 0)


@handoff_app.command("list")
def handoff_list() -> None:
    from computer_use.escalation import EscalationManager

    items = EscalationManager().list_open()
    if not items:
        rprint("No open escalations.")
        return
    for esc in items:
        rprint(f"{esc.id}  {esc.status}  {esc.reason}")


@handoff_app.command("take")
def handoff_take(esc_id: str) -> None:
    from computer_use.escalation import EscalationManager

    esc = EscalationManager().take_control(esc_id)
    rprint(f"Taken control of {esc.id} — status={esc.status}")


@handoff_app.command("resolve")
def handoff_resolve(
    esc_id: str,
    notes: str = typer.Option("", help="What the operator did"),
) -> None:
    from computer_use.escalation import EscalationManager

    esc = EscalationManager().resolve(esc_id, notes=notes)
    rprint(f"Resolved {esc.id}")


@app.command()
def seed() -> None:
    """Write the canonical lookup_savings_balance capability JSON."""
    settings = get_settings()
    from computer_use.agent import DiscoveryAgent

    result, artifact = asyncio.run(
        DiscoveryAgent(settings).run(
            "seed",
            params={"member_id": "M-1001"},
            use_seed_artifact=True,
        )
    )
    rprint(json.dumps(result.model_dump(), indent=2))
    if artifact:
        rprint(f"[green]Wrote[/green] capabilities/{artifact.id}.json")


if __name__ == "__main__":
    app()
