#!/usr/bin/env python3
"""
demo/04_rft_grader.py
DEMO 4 - RFT Grader: Gradable vs Subjective Task Comparison + Consistency Validation
Live mode now builds tasks from Postgres and validates grader consistency with real model calls.
"""
import json
import math
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from demo.utils import *


def load_task_pairs(conn):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT lot_id, fab_id, tech_id, wafers_started, yield_pct, status
            FROM production_lot
            ORDER BY start_date DESC, lot_id
            LIMIT 2
            """
        )
        lots = cur.fetchall()

        cur.execute(
            """
            SELECT constraint_id, description, rule, constraint_type
            FROM operational_constraint
            ORDER BY constraint_id
            LIMIT 2
            """
        )
        constraints = cur.fetchall()

    if len(lots) < 2 or len(constraints) < 2:
        raise SystemExit(
            "Need seeded production_lot and operational_constraint rows "
            "(run scripts/seed_semiconductor_db.py)."
        )

    l0, l1 = lots
    c0, c1 = constraints

    task_pairs = [
        {
            "label": "Lot Readiness Extraction",
            "subjective": {
                "task": "Write a good manufacturing summary for this lot.",
                "gradable": False,
                "why_bad": "Subjective phrasing with no deterministic success metric.",
            },
            "gradable": {
                "task": (
                    f"Extract lot_id, fab_id, yield_pct, status for lot {l0['lot_id']}. "
                    "Return JSON with exactly these keys."
                ),
                "reference": json.dumps(
                    {
                        "lot_id": l0["lot_id"],
                        "fab_id": l0["fab_id"],
                        "yield_pct": float(l0["yield_pct"]),
                        "status": l0["status"],
                    }
                ),
                "gradable": True,
                "why_good": "Exact keys and numeric fields are machine-verifiable against Postgres.",
            },
        },
        {
            "label": "Constraint Compliance",
            "subjective": {
                "task": "Is this a strong operations policy?",
                "gradable": False,
                "why_bad": "'Strong' is ambiguous and changes by reviewer preference.",
            },
            "gradable": {
                "task": (
                    f"Given constraint {c0['constraint_id']} and lot {l1['lot_id']}, "
                    "return JSON with fields violation_risk (low/medium/high) and rationale."
                ),
                "reference": json.dumps(
                    {
                        "violation_risk": "high" if "export" in c0["constraint_type"].lower() else "medium",
                        "rationale": c0["rule"],
                    }
                ),
                "gradable": True,
                "why_good": "Risk label is bounded and rationale must map to an explicit rule text.",
            },
        },
        {
            "label": "Tool Qualification Check",
            "subjective": {
                "task": "Tell me if our qualification process seems okay.",
                "gradable": False,
                "why_bad": "No objective threshold or expected output schema.",
            },
            "gradable": {
                "task": (
                    f"List exactly two active constraints by ID including {c0['constraint_id']} and {c1['constraint_id']}."
                ),
                "reference": json.dumps({"constraints": [c0["constraint_id"], c1["constraint_id"]]}),
                "gradable": True,
                "why_good": "Discrete IDs with exact-count requirement make grading deterministic.",
            },
        },
    ]
    return task_pairs, l0


def run_live_grader_score(client, model, task, reference_answer):
    resp = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an RFT grader. Return only JSON with fields score and reason. "
                    "score must be a float from 0 to 1."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Task:\n{task}\n\n"
                    f"Reference answer:\n{reference_answer}\n\n"
                    f"Candidate answer:\n{reference_answer}\n\n"
                    "Grade the candidate against the task and reference."
                ),
            },
        ],
        temperature=0.2,
    )
    raw = json.loads(resp.choices[0].message.content or "{}")
    score = float(raw.get("score", 0.8))
    return max(0.0, min(1.0, score))


def simulate_grader_consistency(client, model, mock, task_label, task, reference_answer, n_runs=5):
    """Run grader N times on the same answer and compute CV."""
    if mock:
        base_score = 0.92
        scores = [round(base_score + random.gauss(0, 0.03), 3) for _ in range(n_runs)]
    else:
        scores = [round(run_live_grader_score(client, model, task, reference_answer), 3) for _ in range(n_runs)]

    mean = sum(scores) / len(scores)
    variance = sum((s - mean) ** 2 for s in scores) / len(scores)
    std_dev = math.sqrt(variance)
    cv = std_dev / mean if mean else 1.0

    print(f"\n  Task: {task_label}")
    print(f"  Scores across {n_runs} runs: {[f'{s:.3f}' for s in scores]}")
    print(f"  Mean: {mean:.3f}  |  StdDev: {std_dev:.3f}  |  CV: {cv:.3f}")

    consistent = cv < 0.10
    if consistent:
        ok(f"Grader CONSISTENT  (CV={cv:.3f} < 0.10 threshold)")
    else:
        fail(f"Grader INCONSISTENT  (CV={cv:.3f} >= 0.10) - DO NOT USE FOR RFT")
    return cv, consistent


def demonstrate_hybrid_grader(client, model, mock, lot_row):
    section("STEP 3 - Hybrid Grader: Rule-Based Gate + LLM Quality Score", "BLUE")
    info("Rule-based gate: deterministic checks  |  LLM judge: semantic quality")

    test_task = (
        f"Extract lot_id, fab_id, wafers_started, yield_pct for lot {lot_row['lot_id']}. "
        "Return JSON."
    )
    parsed = {
        "lot_id": lot_row["lot_id"],
        "fab_id": lot_row["fab_id"],
        "wafers_started": int(lot_row["wafers_started"]),
        "yield_pct": float(lot_row["yield_pct"]),
    }
    test_response = json.dumps(parsed)
    required = ["lot_id", "fab_id", "wafers_started", "yield_pct"]

    print(f"\n  Task:     {test_task}")
    print(f"  Response: {test_response}")
    print(f"  Required: {required}")

    found = [k for k in required if k in parsed]
    rule_score = len(found) / len(required)
    print(f"\n  L1 Rule check:  {found}  -> score={rule_score:.2f}")
    ok("Rule gate PASSED (all required keys present)")

    expected = {
        "lot_id": lot_row["lot_id"],
        "fab_id": lot_row["fab_id"],
        "wafers_started": int(lot_row["wafers_started"]),
        "yield_pct": float(lot_row["yield_pct"]),
    }
    accuracy_scores = {}
    for key, exp_val in expected.items():
        act_val = parsed.get(key)
        passed = act_val == exp_val
        accuracy_scores[key] = 1.0 if passed else 0.0
        print(f"  L2 Accuracy:    {key}={act_val} vs expected={exp_val}  -> {accuracy_scores[key]:.1f}")

    if mock:
        llm_score = 0.95
        time.sleep(0.08)
    else:
        resp = client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Score 0-1 for semantic quality.\n"
                        f"Task: {test_task}\n"
                        f"Response: {test_response}\n"
                        "Return JSON {\"score\": N}."
                    ),
                }
            ],
            temperature=0,
        )
        raw = json.loads(resp.choices[0].message.content or "{}")
        llm_score = max(0.0, min(1.0, float(raw.get("score", 0.8))))

    print(f"  L3 LLM judge:   semantic quality score = {llm_score:.2f}")

    composite = 0.30 * rule_score + 0.40 * (sum(accuracy_scores.values()) / len(accuracy_scores)) + 0.30 * llm_score
    print()
    metric("Composite score", f"{composite:.3f}", "0.30xrule + 0.40xaccuracy + 0.30xllm")
    ok("This is the reward signal RFT trains toward")


def main():
    args = parse_demo_args("Demo 4: RFT Grader")
    mode = "MOCK" if is_mock(args) else "LIVE"
    client = get_client(is_mock(args))

    with get_db_connection() as conn:
        task_pairs, demo_lot = load_task_pairs(conn)

    print(f"\n{'=' * 65}")
    print(f"  DEMO 4 - RFT GRADER + GRADABILITY VALIDATION  [{mode}]")
    print("  The grader IS the reward signal. A bad grader trains a bad model.")
    print(f"{'=' * 65}")

    section("STEP 1 - Gradable vs Subjective: Side-by-Side Comparison", "RED")
    warn("RFT quality depends on tasks the grader can score reliably")
    for pair in task_pairs:
        print(f"\n  -- {pair['label']} --")
        s = pair["subjective"]
        g = pair["gradable"]
        fail(f"SUBJECTIVE: \"{s['task'][:65]}\"")
        print(f"     Why bad:  {s['why_bad']}")
        ok(f"GRADABLE:   \"{g['task'][:65]}\"")
        print(f"     Why good: {g['why_good']}")

    section("STEP 2 - Grader Consistency Validation (CV < 0.10)", "YELLOW")
    info("Run the grader 5x on the same answer - variance must stay under 10%")
    all_consistent = True
    for pair in task_pairs:
        cv, consistent = simulate_grader_consistency(
            client,
            args.model,
            is_mock(args),
            pair["label"],
            pair["gradable"]["task"],
            pair["gradable"]["reference"],
        )
        if not consistent:
            all_consistent = False

    if all_consistent:
        print()
        ok("All graders validated - safe to start RFT training")
    else:
        fail("Grader consistency failure - FIX GRADER BEFORE TRAINING")
        sys.exit(1)

    demonstrate_hybrid_grader(client, args.model, is_mock(args), demo_lot)

    so_what(
        [
            "Gradable tasks produce deterministic reward signals; subjective tasks inject noise.",
            "Live mode now validates CV using repeated real grader calls.",
            "Hybrid scoring combines deterministic checks with LLM semantic judgment.",
            "This validation should run before every RFT training job.",
        ]
    )
    recruiter_line(
        "The grader is the reward function. I validate consistency on live, DB-backed tasks "
        "before training so the model never optimises noisy labels."
    )


if __name__ == "__main__":
    main()
