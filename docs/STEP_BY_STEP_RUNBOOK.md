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

This creates and populates core tables such as:
- `fab`
- `production_lot`
- `operational_constraint`
- other supporting semiconductor tables

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

### AI Agent tab (Phase 1)
The `AI Agent` tab provides:
- routing chat + “Propose routing decision” action
- telemetry logging for messages, tool calls, and decisions
- human override workflow (approve/reject/escalate)
- business outcome linkage to each decision
- model pin display (runtime/governance)

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
