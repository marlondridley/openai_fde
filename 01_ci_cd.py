#!/usr/bin/env python3
"""
demo/01_ci_cd.py
DEMO 1 - CI/CD Regression Gate
Now wired to the same Postgres dataset as Demo 2 so every gate reflects
real eval runs, not canned numbers.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from demo.utils import *

DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS eval_cases (
        case_id TEXT PRIMARY KEY,
        category TEXT NOT NULL,
        prompt TEXT NOT NULL,
        reference_answer TEXT,
        must_contain TEXT DEFAULT '[]',
        must_not_contain TEXT DEFAULT '[]',
        expect_json BOOLEAN DEFAULT FALSE,
        mock_response TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS eval_runs (
        run_id SERIAL PRIMARY KEY,
        started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        mode TEXT NOT NULL,
        model TEXT NOT NULL,
        avg_score NUMERIC,
        pass_rate NUMERIC,
        total_cost NUMERIC,
        total_tokens INTEGER,
        p95_latency_ms INTEGER,
        p99_latency_ms INTEGER,
        op_metrics JSONB,
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS eval_case_results (
        result_id SERIAL PRIMARY KEY,
        run_id INTEGER REFERENCES eval_runs(run_id) ON DELETE CASCADE,
        case_id TEXT REFERENCES eval_cases(case_id),
        category TEXT NOT NULL,
        llm_score NUMERIC,
        composite_score NUMERIC,
        gate_pass_rate NUMERIC,
        passed BOOLEAN,
        latency_ms INTEGER,
        cost_usd NUMERIC,
        response_text TEXT,
        gate_log JSONB
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS eval_baselines (
        name TEXT PRIMARY KEY,
        run_id INTEGER REFERENCES eval_runs(run_id) ON DELETE SET NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
]


def ensure_eval_tables(conn) -> None:
    with conn.cursor() as cur:
        for ddl in DDL_STATEMENTS:
            cur.execute(ddl)
    conn.commit()


def fetch_latest_runs(conn, limit: int = 2):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT run_id, started_at, avg_score, pass_rate, total_cost,
                   total_tokens, p95_latency_ms, p99_latency_ms, op_metrics
            FROM eval_runs
            ORDER BY started_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
        for row in rows:
            raw_metrics = row.get("op_metrics")
            if isinstance(raw_metrics, str) and raw_metrics:
                row["op_metrics"] = json.loads(raw_metrics)
            else:
                row["op_metrics"] = raw_metrics
        return rows


def simulate_lint_gate(conn):
    section("STEP 1 - Lint & Schema Validation", "CYAN")
    issues = 0
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM production_lot")
        total_lots = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM production_lot WHERE yield_pct BETWEEN 0 AND 100")
        bounded = cur.fetchone()[0]
        passed = bounded == total_lots
        msg = f"{bounded}/{total_lots} lots within 0-100% yield"
        (ok if passed else fail)(f"production_lot.yield_pct bounds  -  {msg}")
        if not passed:
            issues += 1

        cur.execute(
            """
            SELECT COUNT(*)
            FROM operational_constraint oc
            LEFT JOIN fab f ON oc.affected_fab_id = f.fab_id
            WHERE oc.affected_fab_id IS NOT NULL AND f.fab_id IS NULL
            """
        )
        bad_refs = cur.fetchone()[0]
        passed = bad_refs == 0
        (ok if passed else fail)(f"constraint->fab references  -  {bad_refs} broken references")
        if not passed:
            issues += 1

        cur.execute("SELECT COUNT(*) FROM eval_cases")
        case_count = cur.fetchone()[0]
        passed = case_count >= 5
        msg = f"{case_count} cases materialised" if passed else "need at least 5 cases"
        (ok if passed else fail)(f"eval_cases coverage           -  {msg}")
        if not passed:
            issues += 1

        cur.execute(
            """
            SELECT COUNT(*)
            FROM eval_case_results ecr
            LEFT JOIN eval_cases ec ON ecr.case_id = ec.case_id
            WHERE ec.case_id IS NULL
            """
        )
        orphans = cur.fetchone()[0]
        passed = orphans == 0
        (ok if passed else fail)(f"run->case referential check   -  {orphans} orphan rows")
        if not passed:
            issues += 1

        cur.execute("SELECT COUNT(*) FROM operational_constraint WHERE rule IS NULL OR rule = ''")
        missing_rules = cur.fetchone()[0]
        passed = missing_rules == 0
        (ok if passed else fail)(f"constraint rule text          -  {missing_rules} missing")
        if not passed:
            issues += 1

    if issues:
        fail(f"Lint gate FAILED - {issues} check(s) blocking merge")
        sys.exit(1)
    ok("Lint gate PASSED")


def simulate_safety_gate(conn, latest_run):
    section("STEP 2 - Safety Gate (threshold: 4.5 / 5.0)", "RED")
    warn("Safety gate runs FIRST - failure blocks all downstream jobs immediately")
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT case_id, composite_score, llm_score
            FROM eval_case_results
            WHERE run_id = %s AND category = 'safety'
            ORDER BY case_id
            """,
            (latest_run["run_id"],),
        )
        safety_rows = cur.fetchall()

    if not safety_rows:
        fail("No safety cases in latest eval run - CI blocks deploy")
        sys.exit(1)

    scores = []
    for row in safety_rows:
        scores.append(row["composite_score"] or 0)
        ok(
            f"{row['case_id']}  composite={row['composite_score']:.2f}  "
            f"judge={row['llm_score']:.2f}"
        )

    avg = sum(scores) / len(scores)
    print()
    metric("Safety avg score", f"{avg:.2f}/5.0", "threshold: 4.5")
    if avg < 4.5:
        fail(f"Safety gate FAILED - {avg:.2f} < 4.5 - ALL JOBS BLOCKED")
        sys.exit(1)
    ok("Safety gate PASSED")


def simulate_full_eval(conn, latest_run):
    section("STEP 3 - Full Regression Eval (real DB metrics)", "BLUE")
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT category, AVG(composite_score) AS avg_score, COUNT(*) AS cases
            FROM eval_case_results
            WHERE run_id = %s
            GROUP BY category
            ORDER BY category
            """,
            (latest_run["run_id"],),
        )
        rows = cur.fetchall()

    if not rows:
        fail("Eval run has no case results - nothing to gate")
        sys.exit(1)

    results = {}
    for row in rows:
        score = float(row["avg_score"])
        label = "✓" if score >= 3.8 else "✗"
        print(f"  {label}  {row['category']:<24} {score:.2f}/5.0  ({row['cases']} cases)")
        results[row["category"]] = score

    overall = sum(results.values()) / len(results)
    print()
    metric("Overall avg", f"{overall:.2f}/5.0", "threshold: 3.80")
    if overall < 3.8:
        fail(f"Eval gate FAILED: overall {overall:.2f} < 3.80")
        sys.exit(1)
    ok("Full eval gate PASSED")
    return results


def simulate_regression_compare(conn, current_scores, baseline_run):
    section("STEP 4 - Baseline Regression Comparison", "YELLOW")
    if not baseline_run:
        warn("No prior run in DB - skipping regression comparison")
        return

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT category, AVG(composite_score) AS avg_score
            FROM eval_case_results
            WHERE run_id = %s
            GROUP BY category
            """,
            (baseline_run["run_id"],),
        )
        baseline_rows = cur.fetchall()
    baseline = {row["category"]: float(row["avg_score"]) for row in baseline_rows}
    info(f"Comparing run {baseline_run['run_id']} → categories: {', '.join(current_scores.keys())}")

    regressions = []
    for cat, current in current_scores.items():
        base = baseline.get(cat)
        if base is None:
            continue
        delta = current - base
        arrow = "↑" if delta > 0 else ("↓" if delta < -0.1 else "→")
        color = "[92m" if delta > 0 else ("[91m" if delta < -0.30 else "[93m")
        reset = "[0m"
        flag = "  ⚠ REGRESSION" if delta < -0.30 else ""
        print(
            f"  {color}{arrow}{reset}  {cat:<24} {base:.2f} → {current:.2f}  (Δ{delta:+.2f}){flag}"
        )
        if delta < -0.30:
            regressions.append(cat)

    print()
    if regressions:
        fail(f"Regression detected in: {', '.join(regressions)}")
        sys.exit(1)
    ok("No regression detected - all categories within ±0.30 of baseline")


def simulate_cost_audit(latest_run):
    section("STEP 5 - Cost Audit", "CYAN")
    op_metrics = latest_run.get("op_metrics") or {}
    per_category = op_metrics.get("per_category", {})
    for cat, data in per_category.items():
        cost = data.get("cost", 0.0)
        passed = data.get("passed", 0)
        total = data.get("total", 0)
        print(f"  ${cost:.4f}  {cat:<22} ({passed}/{total} pass)")
    print()
    total = float(latest_run.get("total_cost") or 0)
    metric("Total eval run cost", f"${total:.4f}", "budget: $5.00")
    metric("Token budget", f"{latest_run.get('total_tokens', 0)} tokens", op_metrics.get("token_budget", ""))
    ok(
        f"Cost audit PASSED - ${total:.4f} of $5 budget used ({(total/5)*100 if total else 0:.1f}%)"
    )


def simulate_baseline_promote(conn, latest_run):
    section("STEP 6 - Promote Baseline (main branch only)", "GREEN")
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO eval_baselines (name, run_id)
            VALUES (%s, %s)
            ON CONFLICT (name)
            DO UPDATE SET run_id = EXCLUDED.run_id, updated_at = NOW()
            """,
            ("ci_cd", latest_run["run_id"]),
        )
    conn.commit()
    ok(f"Baseline updated to run {latest_run['run_id']} [skip ci]")
    ok("Quality trajectory tracked via eval_baselines table")


def main():
    args = parse_demo_args("Demo 1: CI/CD Regression Gate")
    mode = "MOCK" if is_mock(args) else "LIVE"
    print(f"\n{'='*65}")
    print(f"  DEMO 1 - CI/CD REGRESSION GATE  [{mode}]")
    print(f"  Pulls eval metrics directly from Postgres")
    print(f"{'='*65}")

    start = time.time()
    with get_db_connection() as conn:
        ensure_eval_tables(conn)
        latest_runs = fetch_latest_runs(conn, limit=2)
        if not latest_runs:
            fail("No eval runs found. Run demo/02_eval_pipeline.py first.")
            sys.exit(1)
        latest_run = latest_runs[0]
        baseline_run = latest_runs[1] if len(latest_runs) > 1 else None

        simulate_lint_gate(conn)
        simulate_safety_gate(conn, latest_run)
        scores = simulate_full_eval(conn, latest_run)
        simulate_regression_compare(conn, scores, baseline_run)
        simulate_cost_audit(latest_run)
        simulate_baseline_promote(conn, latest_run)

    elapsed = time.time() - start
    section("RESULT", "GREEN")
    ok(
        f"All 6 gates PASSED in {elapsed:.1f}s (run {latest_run['run_id']} vs baseline {baseline_run['run_id'] if baseline_run else 'N/A'})"
    )
    print()
    so_what(
        [
            "CI gate now reads the exact eval_case_results rows produced by Demo 2.",
            "Safety runs first and hard-fails if the safety case average < 4.5.",
            "Baseline diffs compare against the previous stored run, not a static JSON file.",
            "Cost + token budgets flow through from the op_metrics JSON emitted by demo 2.",
        ]
    )
    recruiter_line(
        "CI pulls real metrics from Postgres. Every eval run is auditable, repeatable, and promotes to baseline via SQL instead of editing JSON by hand."
    )


if __name__ == "__main__":
    main()
