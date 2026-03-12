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
├── dashboard.py
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

## Start Database and Seed Data

```powershell
docker compose up -d semiconductor-db
python scripts\seed_semiconductor_db.py
```

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

The dashboard writes runtime AI traces to Postgres for auditability and governance.

Key tables:

- `agent_session`
- `agent_message`
- `agent_tool_call`
- `agent_decision`
- `agent_decision_review`
- `agent_decision_outcome`
- `agent_model_registry`

This supports explainability, review actions, and cost/latency/quality monitoring in production.

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
3. Open dashboard AI Agent tab and make routing decisions (runtime intelligence).
4. Query telemetry tables to prove traceability and monitoring (post-deploy governance).

## Full Runbook

Use the full step-by-step guide for setup, PR flow, and operations:

- `docs/STEP_BY_STEP_RUNBOOK.md`

## Troubleshooting

- Missing `PG*` env vars: dashboard and seed script will fail DB connection.
- Missing `OPENAI_API_KEY`: live runs will fail; use `--mock` for rehearsals.
- Empty dashboard tables: run `python scripts\seed_semiconductor_db.py`.
- API outage: dashboard has fallback behavior for macro/logistics/FX surfaces.
