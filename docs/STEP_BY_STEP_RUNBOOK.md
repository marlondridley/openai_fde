# Step-by-Step Runbook (Dashboard + FDE Demos)

This runbook explains exactly how to run the codebase end-to-end, including:
- Postgres setup
- data seeding
- running the demo scripts
- launching the Streamlit dashboard
- using the new AI Agent tab

## 1) Prerequisites

Install the following:
- Python 3.11+ (3.13 also works in this repo)
- Docker Desktop (recommended for Postgres)
- Git

Optional but recommended:
- a virtual environment

## 2) Open the Project

```powershell
cd C:\Users\marlo\source\openai\demo_project\demo
```

## 3) Install Python Dependencies

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 4) Configure Environment Variables

Copy `.env.example` to `.env` and edit values.

```powershell
Copy-Item .env.example .env
notepad .env
```

Set at minimum:
- `PGHOST`
- `PGPORT`
- `PGDATABASE`
- `PGUSER`
- `PGPASSWORD`

Optional for live OpenAI model calls:
- `OPENAI_API_KEY`

Optional model pinning for the dashboard agent:
- `AGENT_RUNTIME_MODEL` (default: `gpt-4o-mini`)
- `GOVERNANCE_EVAL_MODEL` (default: `gpt-4o-mini`)
- `AGENT_APPROVED_MODELS` (comma-separated, default: `gpt-4o-mini,gpt-4o`)

Optional routing defaults (used by both Multi-Hop Router and AI Agent):
- `ROUTING_DEFAULT_TOP_K` (default: `3`)
- `ROUTING_DEFAULT_RISK_TOLERANCE` (`low` | `medium` | `high` | `critical_only`)
- `ROUTING_WEIGHT_TIME` (default: `0.35`)
- `ROUTING_WEIGHT_COST` (default: `0.25`)
- `ROUTING_WEIGHT_RISK` (default: `0.25`)
- `ROUTING_WEIGHT_CAPACITY` (default: `0.15`)

Optional for AviationStack ingestion:
- `AVIATION_API_KEY`
- `AVIATION_MONITORED_FLIGHTS` (example: `AA123,DL456,UA789`)
- `AVIATIONSTACK_BASE_URL` (default: `http://api.aviationstack.com/v1/flights`)

## 5) Start Postgres (Docker)

```powershell
docker compose up -d semiconductor-db
```

Confirm DB container is healthy:

```powershell
docker compose ps
```

## 6) Seed the Semiconductor Dataset

```powershell
python scripts\seed_semiconductor_db.py
```

If you want step-by-step migration (recommended when isolating lock/timeouts), run:

```powershell
python scripts\seed_semiconductor_db.py --step a   # schema
python scripts\seed_semiconductor_db.py --step b   # core CSV load via COPY
python scripts\seed_semiconductor_db.py --step c   # apply verified capabilities
python scripts\seed_semiconductor_db.py --step d   # routing model + validations
python scripts\seed_semiconductor_db.py --step e   # flow assignments/sample lots
```

`--step` aliases are also supported: `tables`, `core`, `capabilities`, `routing`, `lots`, `validate`.

This creates and populates core tables such as:
- `fab` (with `capabilities` mirror column)
- `production_lot`
- `operational_constraint`
- routing v2 tables (`capability_catalog`, `site_capability`, `process_flow`, `process_flow_step`, `site_lane`, `site_operation_capacity`)
- other supporting semiconductor tables


## 6a) Seed Deterministic Risk Demo Scenarios

```powershell
python scripts\seed_risk_demo_data.py
```

This seeds:
- `risk_events`
- `transport_disruptions`
- `flight_status`

## 6b) Ingest Live Flight Delays from AviationStack

Run one cycle:

```powershell
python scripts\ingest_aviationstack.py --once
```

Run continuously every 5 minutes:

```powershell
python scripts\ingest_aviationstack.py --interval-seconds 300
```

## 7) (Optional) Seed Eval Tables / Runs

If you want the full governance pipeline data present:

```powershell
python 02_eval_pipeline.py --mock
python 01_ci_cd.py --mock
```

For live model calls, use `--live` and ensure `OPENAI_API_KEY` exists.

## 8) Launch the Dashboard

```powershell
streamlit run dashboard.py
```

Open the local URL shown by Streamlit (usually `http://localhost:8501`).

## 9) Use the Dashboard Tabs

### Core Ops tabs
- Facility map
- Production lots
- Macro / LPI / FX signals
- Capacity and routing readiness

### Multi-Hop Router tab (Routing v2)
The `Multi-Hop Router` tab provides:
- selectors for product type, lot, and flow version
- top-k multi-hop alternatives
- per-hop time/cost/risk/capacity details
- bottleneck capability identification
- rejected-candidate reasons (`no_lane`, `capacity_blocked`, etc.)
- vector map overlays for chosen path + alternatives

### AI Agent tab (Phase 1)
The `AI Agent` tab provides:
- routing chat + “Propose routing decision” action
- same multi-hop engine as Router tab (lot-implied `flow_id`)
- configurable `risk_tolerance`, `weights`, and `top_k`
- telemetry logging for messages, tool calls, and decisions
- human override workflow (approve/reject/escalate)
- business outcome linkage to each decision
- model pin display (runtime/governance)

### Agent plugin model
Agent profiles are file-based and discovered from `agents/agent_*.py` at runtime.

To add an agent:
1. Create a new file in `agents/` named `agent_<name>.py`.
2. Define `AGENT_ID`, `LABEL`, and `run_agent_turn(request, context)`.
3. Optionally define `DESCRIPTION` and `PARAMETERS` for UI preview.
4. Refresh the dashboard; the new profile appears in `Agent profile`.

To remove an agent:
1. Delete or rename the file so it no longer matches `agent_*.py`.
2. Refresh the dashboard.

## 10) Verify Agent Telemetry in Postgres

You can query these tables directly:
- `agent_session`
- `agent_message`
- `agent_tool_call`
- `agent_decision`
- `agent_decision_review`
- `agent_decision_outcome`
- `agent_model_registry`

Example:

```sql
SELECT decision_id, created_at, decision_status, model_name, governance_model
FROM agent_decision
ORDER BY decision_id DESC
LIMIT 20;
```

## 11) Run the Full Demo Suite

Mock mode (no OpenAI spend):

```powershell
python run_all.py --mock
```

Live mode:

```powershell
python run_all.py --live
```

Run only one part:

```powershell
python run_all.py --part 6 --mock
```

## 11a) Demo Execution Flow (What to Run During the Demo)

Use this sequence so setup, governance, and dashboard AI behavior are shown clearly.

### Terminal A: keep flight delays updating (optional, requires AviationStack key)

```powershell
python scripts\ingest_aviationstack.py --interval-seconds 300
```

### Terminal B: run the dashboard

```powershell
streamlit run dashboard.py
```

### Terminal C: run governance checks before showing runtime AI

```powershell
python run_all.py --part 1 --mock
python run_all.py --part 2 --mock
python run_all.py --part 8 --mock
python run_all.py --part 9 --mock
python run_all.py --part 10 --mock
```

### In the dashboard UI

1. Open `AI Agent` tab and click `Propose routing decision`.
2. Show that risk context affects route scoring (`risk_score`, `risk_label`).
3. Submit a review action (approve/reject/escalate).
4. Link a business outcome.

### Prove traceability in DB

```sql
SELECT decision_id, created_at, decision_status, confidence, model_name
FROM agent_decision
ORDER BY decision_id DESC
LIMIT 10;

SELECT tool_name, created_at, latency_ms, success
FROM agent_tool_call
ORDER BY tool_call_id DESC
LIMIT 20;

SELECT flight_iata, departure_airport, arrival_airport, status, delay_minutes, last_updated
FROM flight_status
ORDER BY last_updated DESC
LIMIT 20;
```

```sql
SELECT run_id, started_at, feasibility_rate, policy_violation_rate, p95_latency_ms, passed
FROM routing_eval_run
ORDER BY run_id DESC
LIMIT 10;
```

## 10b) Run Routing Eval Gate and Interpret Results

Run directly:

```powershell
python 10_routing_eval.py --mock
```

Interpretation guide (from current gate logic):
- `passed = true`: routing gate passed.
- `feasibility_rate` should be `>= 0.95`.
- `policy_violation_rate` must be `0.00`.
- `p95_latency_ms` must be `<= 2500`.
- `avg_cost_usd` must be `<= 60000`.
- `regression_pass_rate` must be `>= 0.90`.

If any threshold fails, merge should be blocked until routing model/data is fixed.

## 12) Troubleshooting

### "Missing environment variables: PGHOST..."
Your `.env` is missing required Postgres variables. Fill all `PG*` values.

### Dashboard opens but shows no rows
Seed data was not loaded. Run:

```powershell
python scripts\seed_semiconductor_db.py
```

### Agent tab says unavailable
Check DB connectivity and table creation permissions.

### No model-generated narrative in Agent tab
Set `OPENAI_API_KEY` in `.env`. Without it, the app uses deterministic fallback explanations.

### External macro/logistics APIs fail
The dashboard now uses fallback data for Macro/LPI/FX so charts remain available.

## 13) Production Notes

For enterprise deployment:
- keep DB credentials and API keys in secret managers
- use migration tooling for schema versioning
- enforce RBAC for review/outcome actions
- add alerts on decision latency/error/cost
- run CI gates before runtime model promotion


## 14) GitHub Workflow Commands (Branch -> PR -> Checks -> Issues)

Use these commands to show the full governance flow in GitHub.

### A) Prepare branch and push

```powershell
cd C:/Users/marlo/source/openai/demo_project/demo

git remote -v
# If needed:
# git remote set-url origin https://github.com/marlondridley/openai_fde.git

git fetch origin
git checkout main
git pull origin main

git checkout -b feat/dashboard-agent-governance

git add dashboard.py docs/STEP_BY_STEP_RUNBOOK.md
git commit -m "Add dashboard agent governance workflow and runbook"
git push -u origin feat/dashboard-agent-governance
```

### B) Create PR with GitHub CLI (optional)

```powershell
gh auth status

gh pr create `
  --base main `
  --head feat/dashboard-agent-governance `
  --title "Add dashboard AI agent governance and runbook" `
  --body "Implements telemetry, model pinning, review/outcome workflow, and runbook updates."

gh pr checks --watch
```

### C) Issue commands (optional)

```powershell
gh issue list

gh issue create --title "[BUG] <short summary>" --body "Steps, expected vs actual, logs"
gh issue create --title "[FEATURE] <short summary>" --body "Problem, proposal, demo impact"
```

### D) After merge

```powershell
git checkout main
git pull origin main
```
