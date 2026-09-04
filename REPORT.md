# REPORT

## 1. Architecture

I built this as one Python CLI app, not a multi-service stack. For a take-home that felt like the right call — easy to run, easy to follow.

Main pieces:

- **Surface** (`surface.py`): Playwright Chromium. I observe with page text + accessibility snapshot, and act with locators (role/label/text/CSS).
- **Discover** (`agent.py` + `llm.py`): observe → decide → act. The output is a typed `CapabilityArtifact`, not a raw chat log. Evidence records whether discovery used `openai` or `mock`.
- **Replay** (`replay.py`): runs the saved steps with no LLM. It classifies success, business outcomes, policy blocks, hard failures, or escalation.
- **Safety** (`safety.py` + `policy/allowlist.yaml`): host/path/action allowlist, block irreversible routes, redact secrets.
- **Evidence** (`evidence.py`): JSONL events, result JSON, screenshots on failure. Curated demos live under `evidence/examples/`.
- **Escalation** (`escalation.py`): writes a ticket + operator brief, pauses the same browser session, then resumes after resolve.

Trade-off I accepted: filesystem handoff instead of queues/workers. That keeps the vertical slice small, but the seams are still clear if this grew later.

## 2. Artifact schema

I shaped `CapabilityArtifact` (Pydantic, `schema_version: 1.0`) so it looks like something an agent could call later:

- **Contract**: `id`, `name`, `parameters[]`, `outputs[]`
- **Steps**: ordered actions + locators, with optional `{{param}}` templates. Also `wait`, `dismiss_dialog`, `confirm_dialog`
- **Checkpoint**: what “done” looks like (e.g. “Savings balance” on the member page)
- **Outcomes**: matchers for business results vs recoverable states (see §3)

Each locator has a short `rationale` so a reviewer can see why I picked it. I prefer role/name/text. I fall back to CSS only when the legacy markup has no usable label, like `input[name=member_id]`.

## 3. Determinism & error handling

Replay is deterministic on purpose: substitute params, run steps in order, resolve locators the same way, then check the checkpoint. No LLM in that path.

I treated “not found” as a normal business outcome, not a crash. The demo scenarios under `evidence/examples/` cover the cases I cared about:

| Class | Example | Result | Handling |
|-------|---------|--------|----------|
| Success | M-1001 lookup | `success` | checkpoint + outputs |
| Business outcome | M-9999 not found | `not_found` | stop cleanly |
| Business outcome | invalid ID `abc` | `invalid_member_id` | stop |
| Business outcome | M-1003 restricted | `permission_denied` | stop |
| Recoverable | maintenance overlay | `maintenance_dialog` | dismiss, continue |
| Recoverable | confirm dialog | `search_confirm` | confirm, continue |
| Recoverable | session expired | `session_expired` | go back to entry, continue |
| Recoverable | slow load (`?slow=1`) | — | explicit `wait` step |
| Policy block | `/members/*/close` | `blocked_by_policy` | screenshot, stop |
| Hard failure | broken checkpoint | `hard_failure` | screenshot + snippet |
| HITL | overlay blocks click | handoff → resume | same session, retry |

For UI drift, I mostly rely on checkpoints/outcome mismatch. I did not build a full drift detector — that felt out of scope.

## 4. Heterogeneity & multi-tenant

I kept steps abstract (action + locator) so the web Playwright adapter is swappable. A desktop adapter could reuse the same schema and map `role`/`name` to OS accessibility APIs.

For multi-tenant reuse I would keep a **base artifact** for the vendor flow, then small **tenant overlays** for locator overrides / branding quirks / version pins. If one tenant drifts, specialize the failing steps instead of re-recording everything. I only designed that; I did not implement an overlay store.

## 5. Escalation & handoff

I mark “stuck” when the agent sets `escalate=true`, hits max steps, or a replay step fails with `handoff_wait`.

Handoff flow I implemented:

1. Pause automation and keep the browser open (`CUA_HEADLESS=false` if a human needs to click).
2. Write `handoff/<id>.json` plus an operator markdown brief.
3. Human works in that same session, then `handoff resolve` with notes.
4. Replay retries the failed step. The `handoff_resume` evidence run shows this with auto-resolve for the demo.

I skipped a realtime co-browse UI. The important seam is already there: pause/resume on one Playwright session plus a durable ticket.

## 6. Safety

Guardrails I put in place:

- Host allowlist (`127.0.0.1:3000` / `localhost:3000`)
- Path allowlist, with `/members/*/close` blocked
- Action allowlist; risky button text like “Close account” / “cannot be undone” blocked
- Evidence redaction for password/token/secret-like fields

Limit: text-pattern risk checks are heuristic. In production I would want stronger action classification and dual-control for irreversible ops.

## 7. Cuts

What I deliberately left out:

- Real operator UI (filesystem tickets only; `--auto-resolve-handoff` for demos)
- Multi-tenant overlay runtime (design only)
- Confidence scoring / approval workflow / codegen from artifacts
- Extra HTTP API layer (CLI was enough for the slice)

Discovery can use mock LLM offline (`CUA_MOCK_LLM=1`). For submission I also ran one live OpenAI discovery (`CUA_MOCK_LLM=0`) so `evidence/examples/discover` shows `llm_provider: openai`.

If I had more time next: tenant overlay store, stronger a11y-first locators, one-step LLM recovery on replay miss, and a small capability catalog API.
