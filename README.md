# OpenAI FDE Demo: Governance + AI Decision Dashboard

This repository demonstrates two connected planes for enterprise AI operations:

1. Deployment governance (evals, gates, security, cost, reliability).
2. Runtime AI decision surface (Streamlit dashboard + Postgres + external signals + agent telemetry).

The goal is to show clients both how AI decisions are made and how those decisions are governed before and after deployment.

## Current State (What Works Now)

- Root-level demo scripts (`01` to `09`) run in mock or live mode.
- `run_all.py` executes the suite from the repository root.
- Streamlit dashboard connects to Postgres (`PG*` env vars) and external macro/logistics/FX APIs.
- Dashboard includes an AI Agent tab with decision logging and governance metadata.
- AI Agent and Multi-Hop Router tabs both call the same routing engine (`plan_multihop_routes`) using lot-implied `flow_id`, risk tolerance, and scoring weights.
- GitHub Actions workflows run live eval gates on PR and on `main` push.

## Repository Layout

```text
.
├── 01_ci_cd.py
├── 02_eval_pipeline.py
├── 03_sft_dataset.py
├── 04_rft_grader.py
├── 05_reasoning_tradeoff.py
├── 06_scaffolding.py
├── 07_chaos_engineering.py
├── 08_security_guardrails.py
├── 09_cost_optimisation.py
├── 10_routing_eval.py
├── routing_engine.py
├── dashboard.py
├── agents/
│   ├── agent_multihop_default.py
│   └── README.md
├── run_all.py
├── eval_gates.py
├── scripts/
│   ├── seed_semiconductor_db.py
│   └── deploy.sh
├── semiconductor_data/
├── docs/
│   ├── STEP_BY_STEP_RUNBOOK.md
│   └── openai_api_key.md
└── .github/workflows/
    ├── eval.yml
    └── live_eval.yml
```

## Prerequisites

- Python 3.11+
- Docker Desktop (recommended for Postgres)
- Git
- Optional: GitHub CLI (`gh`) for PR and issue demos

## Setup

```powershell
cd C:\Users\marlo\source\openai\demo_project\demo

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item .env.example .env
notepad .env
```

Set at least:

- `PGHOST`
- `PGPORT`
- `PGDATABASE`
- `PGUSER`
- `PGPASSWORD`

Optional for live model calls:

- `OPENAI_API_KEY`

Optional routing defaults (used by Multi-Hop Router + AI Agent):

- `ROUTING_DEFAULT_TOP_K` (default: `3`)
- `ROUTING_DEFAULT_RISK_TOLERANCE` (default: `medium`; options: `low`, `medium`, `high`, `critical_only`)
- `ROUTING_WEIGHT_TIME` (default: `0.35`)
- `ROUTING_WEIGHT_COST` (default: `0.25`)
- `ROUTING_WEIGHT_RISK` (default: `0.25`)
- `ROUTING_WEIGHT_CAPACITY` (default: `0.15`)


## Start Database and Seed Data

```powershell
docker compose up -d semiconductor-db
python scripts\seed_semiconductor_db.py
```

Stepwise (A-E) seed path for lock/debug isolation (the script supports `--step`):

```powershell
python scripts\seed_semiconductor_db.py --step a
python scripts\seed_semiconductor_db.py --step b
python scripts\seed_semiconductor_db.py --step c
python scripts\seed_semiconductor_db.py --step d
python scripts\seed_semiconductor_db.py --step e
# aliases also supported: tables/core/capabilities/routing/lots/validate
```

## Risk + Flight Data (DB + API)

Deterministic risk demo seed (conflict, Red Sea, PHX delay, sample flights):

```powershell
python scripts\seed_risk_demo_data.py
```

AviationStack ingestion (writes to `flight_status`):

```powershell
# one cycle
python scripts\ingest_aviationstack.py --once

# continuous polling every 5 minutes
python scripts\ingest_aviationstack.py --interval-seconds 300
```

Required env vars for ingestion:

- `AVIATION_API_KEY`
- `AVIATION_MONITORED_FLIGHTS` (optional, comma-separated)
- `AVIATIONSTACK_BASE_URL` (optional)

## Run Demo Suite

Mock mode (no OpenAI usage):

```powershell
python run_all.py --mock
```

Live mode (uses real API + key):

```powershell
python run_all.py --live
```

Single section:

```powershell
python run_all.py --part 6 --mock
python run_all.py --part 10 --mock
```

## Launch Dashboard

Local:

```powershell
streamlit run dashboard.py
```

Docker profile:

```powershell
docker compose --profile dashboard up dashboard
```

## Dashboard Agent + Telemetry

## Agent Plugins (Plug-and-Play)

The dashboard now discovers agents from `agents/agent_*.py` at runtime.

Required in each agent file:

- `AGENT_ID`
- `LABEL`
- `run_agent_turn(request, context)`

Optional metadata:

- `DESCRIPTION`
- `PARAMETERS`

Add/remove behavior:

- Add a new file in `agents/` to add an agent profile to the dashboard.
- Delete/rename the file to remove it.
- No core dashboard registry edits are required.

The dashboard writes runtime AI traces to Postgres for auditability and governance. The AI Agent tab now uses the exact same multi-hop routing engine as the Multi-Hop Router tab, so decisions are reproducible between conversational and analytical views.

Key tables:

- `agent_session`
- `agent_message`
- `agent_tool_call`
- `agent_decision`
- `agent_decision_review`
- `agent_decision_outcome`
- `agent_model_registry`

Routing v2 tables used for multi-hop planning and governance:

- `capability_catalog`
- `site_capability`
- `process_flow`
- `process_flow_step`
- `site_lane`
- `site_operation_capacity`
- `routing_eval_run`
- `routing_eval_case_result`

This supports explainability, review actions, and cost/latency/quality monitoring in production.

UI note: both tabs include an alternatives scorecard for side-by-side comparison of path score, time, cost, risk, and capacity pressure.

## GitHub Workflow Behavior

- `.github/workflows/eval.yml`
  - Trigger: Pull requests to `main`
  - Runs: `python run_all.py --live`
  - Outcome: PR gate pass/fail with run log artifact and PR comment summary

- `.github/workflows/live_eval.yml`
  - Trigger: Push to `main`
  - Runs: `python run_all.py --live`
  - On pass: executes `scripts/deploy.sh`
  - On fail: blocks deploy

## Demo Narrative (Client-Facing)

Use the repo to show both planes in one flow:

1. Open a PR and let eval gates run (governance before merge).
2. Merge and show deploy workflow outcome (governance at release).
3. Open dashboard Multi-Hop Router tab + AI Agent tab and make routing decisions (runtime intelligence).
4. Query telemetry + routing eval tables to prove traceability and monitoring (post-deploy governance).

## Full Runbook

Use the full step-by-step guide for setup, PR flow, and operations:

- `docs/STEP_BY_STEP_RUNBOOK.md`

## Troubleshooting

- Missing `PG*` env vars: dashboard and seed script will fail DB connection.
- Missing `OPENAI_API_KEY`: live runs will fail; use `--mock` for rehearsals.
- Empty dashboard tables: run `python scripts\seed_semiconductor_db.py`.
- API outage: dashboard has fallback behavior for macro/logistics/FX surfaces.
