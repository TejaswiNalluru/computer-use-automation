# Computer-Use Automation System

Discover a UI flow with an LLM (or offline mock), save a **versioned capability artifact**, then **replay it deterministically** against the local Demo Core app — with allowlist safety, evidence, and human escalation.

## Quick start

```powershell
git clone https://github.com/TejaswiNalluru/computer-use-automation.git
cd computer-use-automation
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium
copy .env.example .env
```

Terminal A — demo target:

```powershell
python -m uvicorn demo_target.app:app --host 127.0.0.1 --port 3000
```

Terminal B — automation:

```powershell
# Seed canonical artifact (no browser)
python -m computer_use.cli seed

# Discover against live UI (mock LLM by default via CUA_MOCK_LLM=1)
python -m computer_use.cli discover "look up member and read savings balance" --member-id M-1001

# Replay happy path
python -m computer_use.cli replay capabilities\lookup_savings_balance.json --member-id M-1001

# Replay business outcome (not found)
python -m computer_use.cli replay capabilities\lookup_savings_balance.json --member-id M-9999

# Full demo: seed + success + not-found
python -m computer_use.cli demo

# All edge-case replays + publish evidence/examples/
python -m computer_use.cli edge-cases

# Individual edge cases
python -m computer_use.cli replay capabilities\lookup_savings_balance.json --member-id abc
python -m computer_use.cli replay capabilities\lookup_savings_balance.json --entry-url "http://127.0.0.1:3000/?dialog=1"
python -m computer_use.cli replay capabilities\lookup_savings_balance.json --entry-url "http://127.0.0.1:3000/?slow=1"
python -m computer_use.cli replay capabilities\navigate_close_account.json
python -m computer_use.cli replay capabilities\lookup_broken_checkpoint.json
python -m computer_use.cli replay capabilities\lookup_without_dismiss.json --entry-url "http://127.0.0.1:3000/?dialog=1" --handoff-wait --auto-resolve-handoff
python -m computer_use.cli replay capabilities\lookup_savings_balance.json --member-id M-1003
python -m computer_use.cli replay capabilities\lookup_session_expired.json --entry-url "http://127.0.0.1:3000/?expired=1"
python -m computer_use.cli replay capabilities\lookup_with_confirm.json --entry-url "http://127.0.0.1:3000/?confirm=1"
```

### Live LLM discovery

Set in `.env`:

```
OPENAI_API_KEY=sk-...
CUA_MOCK_LLM=0
OPENAI_MODEL=gpt-4o-mini
```

Then:

```powershell
python -m computer_use.cli discover "look up member M-1001 and read their savings balance" --member-id M-1001
```

## Layout

| Path | Role |
|------|------|
| `demo_target/` | Local legacy-style core banking UI |
| `computer_use/` | Agent, replay, safety, escalation, evidence |
| `policy/allowlist.yaml` | Host/path/action allowlist + risky patterns |
| `capabilities/` | Saved capability artifacts |
| `evidence/` | Per-run JSONL + screenshots |
| `handoff/` | Escalation tickets + operator briefs |
| `REPORT.md` | Design write-up |

## Safety notes

- Allowed host: `127.0.0.1:3000` / `localhost:3000`
- `/members/*/close` blocked (irreversible “Close account”)
- Risky button text patterns blocked
- Secrets redacted from evidence

## Human escalation

On stuck/failure the system writes `handoff/<id>.json` + `*_OPERATOR.md`.

```powershell
python -m computer_use.cli handoff list
python -m computer_use.cli handoff take <id>
python -m computer_use.cli handoff resolve <id> --notes "dismissed dialog; continued"
```

Run headed so the live window stays visible: `--headed` or `CUA_HEADLESS=false`.

## Full evidence demo

With the demo server running and `.env` set (`OPENAI_API_KEY` + `CUA_MOCK_LLM=0` for live discovery, or `CUA_MOCK_LLM=1` for offline mock):

```powershell
python -m computer_use.cli submit-prep
```

This runs discovery + the edge-case replays and writes `evidence/examples/`. Check `evidence/examples/summary.json`, and that `evidence/examples/discover/events.jsonl` records the LLM provider used.
