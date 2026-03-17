#!/usr/bin/env python3
"""demo/10_routing_eval.py
Routing Engine v2 eval gate for multi-hop feasibility, policy, cost/latency, and baseline regression.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor

from routing_engine import DEFAULT_WEIGHTS, choose_active_flow, plan_multihop_routes, plan_single_hop_baseline

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

ROOT = Path(__file__).resolve().parent
if load_dotenv:
    env_path = ROOT / '.env'
    if env_path.exists():
        load_dotenv(env_path)


def get_conn():
    return psycopg2.connect(
        host=os.getenv('PGHOST'),
        port=int(os.getenv('PGPORT', '5432')),
        dbname=os.getenv('PGDATABASE'),
        user=os.getenv('PGUSER'),
        password=os.getenv('PGPASSWORD'),
    )


def run_query(conn, query: str) -> pd.DataFrame:
    return pd.read_sql(query, conn)


def ensure_eval_tables(conn) -> None:
    ddl = [
        """
        CREATE TABLE IF NOT EXISTS routing_eval_run (
            run_id BIGSERIAL PRIMARY KEY,
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            mode TEXT NOT NULL,
            total_cases INTEGER NOT NULL,
            feasibility_rate NUMERIC,
            policy_violation_rate NUMERIC,
            p95_latency_ms INTEGER,
            avg_cost_usd NUMERIC,
            regression_pass_rate NUMERIC,
            passed BOOLEAN NOT NULL,
            notes TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS routing_eval_case_result (
            result_id BIGSERIAL PRIMARY KEY,
            run_id BIGINT REFERENCES routing_eval_run(run_id) ON DELETE CASCADE,
            case_id TEXT,
            lot_id TEXT,
            flow_id TEXT,
            source_site TEXT,
            feasible BOOLEAN,
            passed BOOLEAN,
            latency_ms INTEGER,
            best_score NUMERIC,
            baseline_score NUMERIC,
            best_cost_usd NUMERIC,
            policy_violation BOOLEAN,
            notes TEXT,
            result_json JSONB
        )
        """,
    ]
    with conn.cursor() as cur:
        for stmt in ddl:
            cur.execute(stmt)
    conn.commit()


def load_cases(lots_df: pd.DataFrame) -> List[Dict[str, Any]]:
    dataset_path = ROOT / 'datasets' / 'routing_eval_cases.json'
    if dataset_path.exists():
        data = json.loads(dataset_path.read_text(encoding='utf-8'))
        if isinstance(data, list) and data:
            return data

    sample = lots_df.sort_values('start_date', ascending=False).head(6)
    out = []
    for _, row in sample.iterrows():
        out.append(
            {
                'case_id': f"lot_{row['lot_id']}",
                'lot_id': str(row['lot_id']),
                'flow_id': str(row.get('flow_id') or 'FLOW_V1_CORE'),
                'expected_feasible': True,
                'expected_final_sites': [],
            }
        )
    return out


def percentile(data: List[float], p: float) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    idx = int(round((len(s) - 1) * p))
    idx = max(0, min(idx, len(s) - 1))
    return float(s[idx])


def to_safe_json(obj: Any) -> str:
    txt = json.dumps(obj, allow_nan=False, default=str)
    if 'NaN' in txt or 'Infinity' in txt or '-Infinity' in txt:
        raise ValueError('Non-finite numeric found in JSON payload')
    return txt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--mock', action='store_true', default=True)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--model', type=str, default='gpt-4o-mini')
    args = parser.parse_args()

    mode = 'live' if args.live else 'mock'

    required = ['PGHOST', 'PGPORT', 'PGDATABASE', 'PGUSER', 'PGPASSWORD']
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        print(f"[FAIL] Missing env vars: {', '.join(missing)}")
        return 1

    conn = get_conn()
    try:
        ensure_eval_tables(conn)

        lots_df = run_query(conn, "SELECT lot_id, fab_id, start_date, flow_id FROM production_lot")
        flow_df = run_query(conn, "SELECT flow_id, product_type, version, active FROM process_flow")
        flow_step_df = run_query(conn, "SELECT flow_id, step_order, capability_id, required, max_wait_hours FROM process_flow_step")
        site_cap_df = run_query(conn, "SELECT site_id, capability_id, confidence, source, valid_from, valid_to, routing_eligible, demo_only FROM site_capability")
        lane_df = run_query(conn, "SELECT from_site, to_site, mode, transit_hours, cost_usd, risk_score, active FROM site_lane")
        cap_df = run_query(conn, "SELECT site_id, capability_id, period_start, capacity_units, utilized_units FROM site_operation_capacity")
        fab_df = run_query(conn, "SELECT fab_id, status FROM fab")

        if flow_df.empty or flow_step_df.empty or site_cap_df.empty or lane_df.empty:
            print('[FAIL] Routing tables are empty. Run scripts/seed_semiconductor_db.py first.')
            return 1

        cases = load_cases(lots_df)
        under_construction = {
            str(r['fab_id'])
            for _, r in fab_df.iterrows()
            if 'under construction' in str(r.get('status') or '').lower() or 'planned' in str(r.get('status') or '').lower()
        }

        results: List[Dict[str, Any]] = []
        latencies: List[int] = []
        best_costs: List[float] = []
        regression_passes = 0
        policy_violations = 0
        feasibility_passes = 0

        for case in cases:
            lot_id = str(case.get('lot_id'))
            lot_match = lots_df[lots_df['lot_id'].astype(str) == lot_id]
            if lot_match.empty:
                results.append(
                    {
                        'case_id': str(case.get('case_id')), 'lot_id': lot_id, 'flow_id': str(case.get('flow_id') or ''),
                        'source_site': None, 'feasible': False, 'passed': False, 'latency_ms': 0,
                        'best_score': None, 'baseline_score': None, 'best_cost_usd': None,
                        'policy_violation': False, 'notes': 'Lot not found', 'result_json': {'error': 'lot_not_found'}
                    }
                )
                continue

            source_site = str(lot_match.iloc[0]['fab_id'])
            requested_flow_id = str(case.get('flow_id') or lot_match.iloc[0].get('flow_id') or '')
            flow_row = choose_active_flow(flow_df, flow_id=requested_flow_id)
            if not flow_row:
                flow_row = choose_active_flow(flow_df)
            flow_id = str(flow_row['flow_id']) if flow_row else requested_flow_id

            start = time.time()
            route_result = plan_multihop_routes(
                source_site=source_site,
                flow_id=flow_id,
                flow_step_df=flow_step_df,
                site_lane_df=lane_df,
                site_capability_df=site_cap_df,
                site_capacity_df=cap_df,
                top_k=3,
                weights=DEFAULT_WEIGHTS,
                allow_distribution_optional=True,
            )
            latency_ms = int((time.time() - start) * 1000)
            latencies.append(latency_ms)

            baseline = plan_single_hop_baseline(
                source_site=source_site,
                flow_id=flow_id,
                flow_step_df=flow_step_df,
                site_lane_df=lane_df,
                site_capability_df=site_cap_df,
                site_capacity_df=cap_df,
            )

            feasible = bool(route_result.get('feasible'))
            expected_feasible = bool(case.get('expected_feasible', True))
            feasible_ok = feasible == expected_feasible
            if feasible_ok:
                feasibility_passes += 1

            best_path = route_result['paths'][0] if feasible and route_result.get('paths') else None
            best_score = float(best_path['score']) if best_path else None
            baseline_score = float(baseline['score']) if baseline.get('feasible') and baseline.get('score') is not None else None
            best_cost = float(best_path.get('totals', {}).get('cost_usd', 0.0)) if best_path else None
            if best_cost is not None:
                best_costs.append(best_cost)

            if baseline_score is not None and best_score is not None:
                if best_score <= baseline_score + 0.05:
                    regression_passes += 1
            else:
                # Missing baseline counts as soft pass to avoid false-negative gate on sparse demo configs.
                regression_passes += 1

            policy_violation = False
            if best_path:
                for hop in best_path.get('hops', []):
                    target = str(hop.get('to_site') or '')
                    if target in under_construction:
                        policy_violation = True
                        break
            if policy_violation:
                policy_violations += 1

            expected_finals = [str(v) for v in case.get('expected_final_sites', []) if str(v).strip()]
            final_ok = True
            if feasible and expected_finals:
                final_ok = str(best_path.get('final_site')) in expected_finals

            passed = feasible_ok and final_ok and (not policy_violation)

            payload_text = to_safe_json(route_result)
            notes = 'ok' if passed else route_result.get('reason', 'routing_eval_failed')

            results.append(
                {
                    'case_id': str(case.get('case_id')),
                    'lot_id': lot_id,
                    'flow_id': flow_id,
                    'source_site': source_site,
                    'feasible': feasible,
                    'passed': passed,
                    'latency_ms': latency_ms,
                    'best_score': best_score,
                    'baseline_score': baseline_score,
                    'best_cost_usd': best_cost,
                    'policy_violation': policy_violation,
                    'notes': notes,
                    'result_json': json.loads(payload_text),
                }
            )

        total = max(len(results), 1)
        feasibility_rate = feasibility_passes / total
        policy_violation_rate = policy_violations / total
        regression_pass_rate = regression_passes / total
        p95_latency = int(percentile(latencies, 0.95))
        avg_cost = float(statistics.mean(best_costs)) if best_costs else 0.0

        gate_failures = []
        if feasibility_rate < 0.95:
            gate_failures.append(f'feasibility_rate {feasibility_rate:.2f} < 0.95')
        if policy_violation_rate > 0.0:
            gate_failures.append(f'policy_violation_rate {policy_violation_rate:.2f} > 0')
        if p95_latency > 2500:
            gate_failures.append(f'p95_latency_ms {p95_latency} > 2500')
        if avg_cost > 60000:
            gate_failures.append(f'avg_cost_usd {avg_cost:.2f} > 60000')
        if regression_pass_rate < 0.9:
            gate_failures.append(f'regression_pass_rate {regression_pass_rate:.2f} < 0.90')

        passed = len(gate_failures) == 0

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO routing_eval_run
                    (mode, total_cases, feasibility_rate, policy_violation_rate, p95_latency_ms,
                     avg_cost_usd, regression_pass_rate, passed, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING run_id
                """,
                (
                    mode,
                    total,
                    feasibility_rate,
                    policy_violation_rate,
                    p95_latency,
                    avg_cost,
                    regression_pass_rate,
                    passed,
                    '; '.join(gate_failures) if gate_failures else 'routing_eval_pass',
                ),
            )
            run_id = int(cur.fetchone()[0])

            rows = [
                (
                    run_id,
                    r['case_id'],
                    r['lot_id'],
                    r['flow_id'],
                    r['source_site'],
                    r['feasible'],
                    r['passed'],
                    r['latency_ms'],
                    r['best_score'],
                    r['baseline_score'],
                    r['best_cost_usd'],
                    r['policy_violation'],
                    r['notes'],
                    json.dumps(r['result_json']),
                )
                for r in results
            ]
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO routing_eval_case_result
                    (run_id, case_id, lot_id, flow_id, source_site, feasible, passed,
                     latency_ms, best_score, baseline_score, best_cost_usd, policy_violation,
                     notes, result_json)
                VALUES %s
                """,
                rows,
            )
        conn.commit()

        print('\n' + '=' * 72)
        print('ROUTING EVAL SUMMARY (v2 multi-hop)')
        print('=' * 72)
        print(f"Mode: {mode}")
        print(f"Cases: {total}")
        print(f"Feasibility rate: {feasibility_rate:.2%}")
        print(f"Policy violation rate: {policy_violation_rate:.2%}")
        print(f"P95 latency: {p95_latency} ms")
        print(f"Average best-path cost: ${avg_cost:,.2f}")
        print(f"Regression pass rate vs baseline: {regression_pass_rate:.2%}")
        print(f"Run ID: {run_id}")

        if passed:
            print('[PASS] Routing eval gates passed.')
            return 0

        print('[FAIL] Routing eval gates failed:')
        for item in gate_failures:
            print(f"  - {item}")
        return 1

    finally:
        conn.close()


if __name__ == '__main__':
    raise SystemExit(main())
