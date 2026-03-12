#!/usr/bin/env python3
"""
demo/05_reasoning_tradeoff.py
DEMO 5 - Reasoning Token Budget Tradeoff
Live mode now uses DB-backed prompts and real model/judge calls for profile comparisons.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from demo.utils import *

PROFILES = {
    "latency_optimised": {"reasoning_tokens": 800, "latency_s": 0.8, "quality": 3.4, "cost_per_1k": 0.011},
    "balanced": {"reasoning_tokens": 4000, "latency_s": 3.2, "quality": 4.1, "cost_per_1k": 0.044},
    "accuracy_optimised": {"reasoning_tokens": 12000, "latency_s": 14.5, "quality": 4.7, "cost_per_1k": 0.132},
    "max_intelligence": {"reasoning_tokens": 40000, "latency_s": 58.0, "quality": 4.9, "cost_per_1k": 0.440},
}

MODEL_PRICING = {
    "gpt-4o-mini": {"prompt": 0.00015, "completion": 0.00060},
    "gpt-4o": {"prompt": 0.00075, "completion": 0.00225},
    "o4-mini": {"prompt": 0.00110, "completion": 0.00440},
    "default": {"prompt": 0.00040, "completion": 0.00080},
}

USE_CASE_MAP = [
    ("Real-time customer support chat", "latency_optimised", "P95 < 2s SLO. Long thinking time breaks UX."),
    ("Internal analyst research tool", "balanced", "A few seconds is acceptable; quality matters more."),
    ("Legal / compliance contract review", "accuracy_optimised", "High-stakes output justifies higher latency."),
    ("Overnight batch planning reports", "max_intelligence", "No user waiting; maximise answer quality."),
    ("Live demo to enterprise CTO", "balanced", "Use strong quality without long visible waiting."),
]


def estimate_cost(model, prompt_tokens, completion_tokens):
    pricing = MODEL_PRICING.get(model, MODEL_PRICING["default"])
    return (
        (prompt_tokens / 1000) * pricing["prompt"]
        + (completion_tokens / 1000) * pricing["completion"]
    )


def build_sample_task(conn):
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
            SELECT constraint_id, description, rule
            FROM operational_constraint
            ORDER BY constraint_id
            LIMIT 1
            """
        )
        constraint = cur.fetchone()

    if len(lots) < 2 or not constraint:
        raise SystemExit(
            "Need seeded production_lot and operational_constraint rows "
            "(run scripts/seed_semiconductor_db.py)."
        )

    a, b = lots
    sample_task = (
        f"Lot {a['lot_id']} ({a['fab_id']}) yield is {float(a['yield_pct']):.1f}% with {a['wafers_started']} wafers. "
        f"Lot {b['lot_id']} ({b['fab_id']}) yield is {float(b['yield_pct']):.1f}% with {b['wafers_started']} wafers. "
        f"Constraint {constraint['constraint_id']}: {constraint['description']} / rule: {constraint['rule']}. "
        "Compare readiness risk, quantify yield gap, recommend pursue/hold/escalate, and justify in steps."
    )
    return sample_task


def judge_quality(client, model, task, response_text, mock, fallback):
    if mock:
        return fallback

    resp = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": "Score response quality from 1-5. Return JSON with keys score and reason.",
            },
            {
                "role": "user",
                "content": (
                    f"Task:\n{task}\n\n"
                    f"Response:\n{response_text[:3000]}\n\n"
                    "Judge for correctness, structure, and actionable recommendation quality."
                ),
            },
        ],
        temperature=0,
    )
    raw = json.loads(resp.choices[0].message.content or "{}")
    score = float(raw.get("score", fallback))
    return max(1.0, min(5.0, score))


def main():
    args = parse_demo_args("Demo 5: Reasoning Tradeoff")
    mode = "MOCK" if is_mock(args) else "LIVE"
    client = get_client(is_mock(args))

    with get_db_connection() as conn:
        sample_task = build_sample_task(conn)

    print(f"\n{'=' * 65}")
    print(f"  DEMO 5 - REASONING TOKEN BUDGET TRADEOFF  [{mode}]")
    print("  For reasoning models: how long you think is a product decision.")
    print(f"{'=' * 65}")

    section("STEP 1 - The Sample Task (from Postgres)", "CYAN")
    print(f"\n  {sample_task[:260]}...\n")
    info("Task requires arithmetic, constraint interpretation, and recommendation quality.")

    section("STEP 2 - Run All 4 Reasoning Profiles", "BLUE")
    info("Live mode: executes real model calls and scores output quality with a judge model")
    print()
    print(f"  {'Profile':<24} {'Reasoning Tok':>14} {'Latency':>10} {'Quality':>10} {'$/1K req':>10}")
    print(f"  {'-' * 24} {'-' * 14} {'-' * 10} {'-' * 10} {'-' * 10}")

    profile_results = {}

    for profile, data in PROFILES.items():
        if is_mock(args):
            time.sleep(0.15)
            latency = data["latency_s"]
            quality = data["quality"]
            cost_per_1k = data["cost_per_1k"]
        else:
            t0 = time.time()
            response = client.chat.completions.create(
                model=args.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a semiconductor ops strategist. Provide concise, numerical, "
                            "and defensible recommendations."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Reasoning profile: {profile}. "
                            f"Think budget approx {data['reasoning_tokens']} tokens.\n\n{sample_task}"
                        ),
                    },
                ],
                max_tokens=min(data["reasoning_tokens"] // 2 + 500, 3500),
                temperature=0.2,
            )
            latency = time.time() - t0
            response_text = response.choices[0].message.content or ""
            usage = getattr(response, "usage", None)
            prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
            req_cost = estimate_cost(args.model, prompt_tokens, completion_tokens)
            cost_per_1k = req_cost * 1000
            quality = judge_quality(
                client,
                args.model,
                sample_task,
                response_text,
                is_mock(args),
                data["quality"],
            )

        profile_results[profile] = {
            "latency_s": latency,
            "quality": quality,
            "cost_per_1k": cost_per_1k,
            "reasoning_tokens": data["reasoning_tokens"],
        }

        print(
            f"  {profile:<24} {data['reasoning_tokens']:>12,}  {latency:>8.1f}s  "
            f"{quality:>7.1f}/5  ${cost_per_1k:>8.3f}"
        )

    section("STEP 3 - Latency vs Quality", "YELLOW")
    info("Tradeoff is measured for this run's data and model settings")
    print()
    ranked = sorted(profile_results.items(), key=lambda x: x[1]["latency_s"])
    for profile, values in ranked:
        print(
            f"  {profile:<24} latency={values['latency_s']:.1f}s  "
            f"quality={values['quality']:.1f}/5  cost=${values['cost_per_1k']:.3f}/1K"
        )

    section("STEP 4 - Business SLO -> Profile Mapping", "GREEN")
    print()
    print(f"  {'Use Case':<40} {'Profile':<24} Business Logic")
    print(f"  {'-' * 40} {'-' * 24} {'-' * 20}")
    for use_case, profile, logic in USE_CASE_MAP:
        print(f"  {use_case:<40} {profile:<24} {logic}")

    section("STEP 5 - ROI at Scale", "CYAN")
    print()
    cases_per_day = 10000
    for profile in ["latency_optimised", "balanced", "accuracy_optimised"]:
        data = profile_results[profile]
        daily_cost = cases_per_day * data["cost_per_1k"] / 1000
        monthly_cost = daily_cost * 30
        print(
            f"  {profile:<24}  ${monthly_cost:>8,.0f}/month  at {cases_per_day:,} req/day  "
            f"quality={data['quality']:.1f}/5"
        )

    so_what(
        [
            "Reasoning budget is a product control knob linking latency, quality, and spend.",
            "Profiles are now exercised with DB-grounded prompts instead of canned text.",
            "Live mode computes quality from an OpenAI judge rather than static benchmark numbers.",
            "Choose profile by SLO and business risk, not model preference alone.",
        ]
    )
    recruiter_line(
        "I map each workflow to a reasoning budget using live latency/quality data on real DB tasks. "
        "That keeps quality high without missing UX or cost constraints."
    )


if __name__ == "__main__":
    main()
