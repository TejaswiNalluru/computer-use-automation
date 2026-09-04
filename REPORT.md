# REPORT

## 1. Architecture

Vertical slice in one Python process (CLI-driven; no separate API server):

- **Surface** (`surface.py`): Playwright Chromium wrapper. Observe via page text + accessibility snapshot; act via semantic/CSS locators.
- **Discover** (`agent.py` + `llm.py`): observe → decide → act loop. Emits a typed `CapabilityArtifact`, not a raw transcript. Evidence logs `llm_provider` (`openai` vs `mock`).
- **Replay** (`replay.py`): executes artifact steps with no LLM decisions; classifies success / business outcome / policy block / hard failure / escalate.
- **Safety** (`safety.py` + `policy/allowlist.yaml`): host/path/action allowlist, irreversible-route block, redaction.
- **Evidence** (`evidence.py`): JSONL events, result JSON, screenshots on failure. Curated runs under `evidence/examples/`.
- **Escalation** (`escalation.py`): ticket + operator brief; pause/cede/resume on same browser session.

Trade-off: single process + filesystem handoff over queues/services — correct for take-home scope; seams stay clear for later workers.

## 2. Artifact schema

`CapabilityArtifact` (Pydantic, `schema_version: 1.0`):

- **Contract**: `id`, `name`, `parameters[]`, `outputs[]` — callable capability surface.
- **Steps**: ordered `action` + `Locator` (`role|label|text|css|…`) + optional `{{param}}` templates. Includes `wait`, `dismiss_dialog`, `confirm_dialog`.
- **Checkpoint**: success condition after steps (e.g. “Savings balance” on member detail URL).
- **Outcomes**: explicit matchers for business results vs recoverable runtime states (see §3).

Locators carry `rationale` so reviewers see why a control ID was chosen. Prefer name/role/text; CSS used when legacy markup has no label association (`input[name=member_id]`).

## 3. Determinism & error handling

Replay substitutes params, runs steps in order, uses the same locator resolution, then checks checkpoint.

Error taxonomy (11 demonstrated scenarios in `evidence/examples/`):

| Class | Example | `outcome_id` / result | Handling |
|-------|---------|----------------------|----------|
| Success | M-1001 lookup | `success` | checkpoint + outputs |
| Business outcome | M-9999 not found | `not_found` | stop; not a crash |
| Business outcome | invalid ID `abc` | `invalid_member_id` | stop |
| Business outcome | M-1003 restricted | `permission_denied` | stop |
| Recoverable | maintenance overlay | `maintenance_dialog` | dismiss, continue |
| Recoverable | “Proceed with lookup?” confirm | `search_confirm` | confirm, continue |
| Recoverable | session expired page | `session_expired` | navigate to entry, continue |
| Recoverable | slow load (`?slow=1`) | — | explicit `wait` step (15s) |
| Policy block | `/members/*/close` route | `blocked_by_policy` | screenshot, stop |
| Hard failure | broken checkpoint artifact | `hard_failure` | screenshot + snippet |
| HITL | overlay blocks click | `handoff_pause` → resume | same session, retry step |

Waits: Playwright auto-wait on actions; optional `wait` step for explicit text (covers slow loads).

## 4. Heterogeneity & multi-tenant

**Surface seam**: artifact steps speak abstract actions + locators; `WebSurface` is one adapter. Desktop would swap in an a11y/OS adapter with the same step schema (`role`/`name` map cleanly to UI Automation / AXTree).

**Multi-tenant**: keep a **base artifact** (vendor flow + parameterized routes/fields) plus optional **tenant overlays** (locator overrides, branding-specific dismissals, version pins). Detect drift by checkpoint/outcome mismatch rates per tenant; specialize only failing steps rather than re-recording whole flows.

## 5. Escalation & handoff

Stuck detection: agent `escalate=true`, max steps, or replay step failure with `handoff_wait`.

Control transfer:

1. Pause automation; keep browser open (`CUA_HEADLESS=false` for manual control).
2. Write `handoff/<id>.json` + operator markdown (goal, step, URL, screenshot, reason).
3. Operator acts in the same session; `handoff resolve` with notes.
4. Replay resumes failed step (`handoff_resume` scenario demonstrates auto-resolve for demo).

Not built: realtime co-browse console. Seam is real: pause/resume on same Playwright session + durable intervention ticket.

## 6. Safety

- Host allowlist (`127.0.0.1:3000` / `localhost:3000`).
- Path allowlist; `/members/*/close` blocked.
- Action allowlist (`confirm_dialog`, `dismiss_dialog`, etc.); risky text (“Close account”, “cannot be undone”) blocked at locator check.
- Evidence redacts password/token/secret-like fields.

Limit: text-pattern risk detection is heuristic; production needs stronger action classification and dual-control for irreversible ops.

## 7. Cuts

- Mock operator UI (filesystem tickets only; `--auto-resolve-handoff` for automated demo).
- No multi-tenant overlay runtime (design only).
- No confidence scoring / approval workflow / code generation from artifacts.
- No FastAPI HTTP layer (CLI sufficient for vertical slice).
- Offline replays use `CUA_MOCK_LLM=1` or seeded artifact; **submission requires one live OpenAI discovery run** (`CUA_MOCK_LLM=0`, key in `.env`) — see README “Submission checklist”.

Next: tenant overlay store, richer a11y-first locators, bounded one-step LLM recovery on replay miss, capability catalog API for agent tool-calling.
