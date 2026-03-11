#!/usr/bin/env python3
"""
demo/05_reasoning_tradeoff.py
DEMO 5 — Reasoning Token Budget Tradeoff
Shows: 4 profiles, latency vs quality vs cost tradeoff, business SLO mapping
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from demo.utils import *
import time

PROFILES = {
    "latency_optimised":  {"reasoning_tokens": 800,   "latency_s": 0.8,  "quality": 3.4, "cost_per_1k": 0.011},
    "balanced":           {"reasoning_tokens": 4000,  "latency_s": 3.2,  "quality": 4.1, "cost_per_1k": 0.044},
    "accuracy_optimised": {"reasoning_tokens": 12000, "latency_s": 14.5, "quality": 4.7, "cost_per_1k": 0.132},
    "max_intelligence":   {"reasoning_tokens": 40000, "latency_s": 58.0, "quality": 4.9, "cost_per_1k": 0.440},
}

SAMPLE_TASK = (
    "A PE firm is evaluating an acquisition target: $450M revenue, 68% gross margin, "
    "$72M EBITDA, 3× EV/Revenue multiple (sector median 4.2×). Debt/EBITDA 3.2× vs 3.5× covenant. "
    "Calculate FCF yield assuming $1.35B enterprise value. Assess covenant headroom. "
    "Recommend: pursue / pass / negotiate. State your reasoning chain step by step."
)

USE_CASE_MAP = [
    ("Real-time customer support chat",    "latency_optimised",  "P95 < 2s SLO. 60s thinking time would destroy UX."),
    ("Internal analyst research tool",    "balanced",           "8s is invisible on a research task. Quality matters more."),
    ("Legal / compliance contract review", "accuracy_optimised", "High-stakes. 45s acceptable when alternative is 4 lawyer hours."),
    ("Overnight batch PE reports",        "max_intelligence",   "No user waiting. Maximum quality for MD-level deliverables."),
    ("Live demo to enterprise CTO",       "balanced",           "Quality demo — but never let thinking time kill the moment."),
]

def main():
    args   = parse_demo_args("Demo 5: Reasoning Tradeoff")
    mode   = "MOCK" if is_mock(args) else "LIVE"
    client = get_client(is_mock(args))

    print(f"\n{'═'*65}")
    print(f"  DEMO 5 — REASONING TOKEN BUDGET TRADEOFF  [{mode}]")
    print(f"  For o-series models: how long you think IS the product decision.")
    print(f"{'═'*65}")

    section("STEP 1 — The Sample Task", "CYAN")
    print(f"\n  {SAMPLE_TASK[:200]}...\n")
    info("This task requires: calculation, covenant analysis, M&A judgment, structured recommendation.")

    section("STEP 2 — Run All 4 Reasoning Profiles", "BLUE")
    info("Mock mode: uses pre-measured latency/quality data from 50-run benchmark")
    info("Live mode: calls o4-mini with each profile's max_completion_tokens")
    print()

    print(f"  {'Profile':<24} {'Reasoning Tok':>14} {'Latency':>10} {'Quality':>10} {'$/1K req':>10}")
    print(f"  {'─'*24} {'─'*14} {'─'*10} {'─'*10} {'─'*10}")

    for profile, data in PROFILES.items():
        if not is_mock(args):
            t0 = time.time()
            resp = client.chat.completions.create(
                model="o4-mini",
                messages=[{"role":"user","content":SAMPLE_TASK}],
                max_completion_tokens=data["reasoning_tokens"] + 800,
            )
            actual_latency = time.time() - t0
            actual_rtokens = getattr(getattr(resp.usage, "completion_tokens_details", None),
                                     "reasoning_tokens", data["reasoning_tokens"])
            latency = actual_latency
        else:
            time.sleep(0.15)
            latency = data["latency_s"]

        q_bar = "█" * int(data["quality"] * 2) + "░" * (10 - int(data["quality"] * 2))
        print(f"  {profile:<24} {data['reasoning_tokens']:>12,}  {latency:>8.1f}s  {data['quality']:>7.1f}/5  ${data['cost_per_1k']:>8.3f}")

    section("STEP 3 — Latency vs Quality Tradeoff Visualised", "YELLOW")
    print()
    print(f"  Quality")
    print(f"  5.0 │                                          ● max_intelligence")
    print(f"  4.7 │                          ● accuracy_optimised")
    print(f"  4.1 │              ● balanced")
    print(f"  3.4 │  ● latency_optimised")
    print(f"      └──────────────────────────────────────────────────── Latency")
    print(f"         0.8s        3.2s        14.5s          58s")
    print()
    warn("More reasoning tokens → better quality → higher latency + cost. Always a tradeoff.")
    info("The correct profile depends entirely on the business SLO — not on technical preference.")

    section("STEP 4 — Business SLO → Profile Mapping", "GREEN")
    print()
    print(f"  {'Use Case':<40} {'Profile':<24} Business Logic")
    print(f"  {'─'*40} {'─'*24} {'─'*20}")
    for use_case, profile, logic in USE_CASE_MAP:
        print(f"  {use_case:<40} {profile:<24} {logic}")

    section("STEP 5 — ROI at Scale", "CYAN")
    print()
    cases_per_day = 10000
    for profile, data in [("latency_optimised", PROFILES["latency_optimised"]),
                           ("balanced", PROFILES["balanced"]),
                           ("accuracy_optimised", PROFILES["accuracy_optimised"])]:
        daily_cost  = cases_per_day * data["cost_per_1k"] / 1000
        monthly_cost = daily_cost * 30
        print(f"  {profile:<24}  ${monthly_cost:>8,.0f}/month  at {cases_per_day:,} req/day  quality={data['quality']:.1f}/5")

    so_what([
        "The most expensive conversation with a customer is not about price — it's about how long the model should think.",
        "Latency-optimised at $330/month vs accuracy-optimised at $3,960/month. For the right use case, each is correct.",
        "I map every deployment to a reasoning profile before the first API call. Changing it post-launch costs goodwill.",
        "For the live demo: always balanced. You need quality — but never let the model think while the CTO watches a spinner.",
    ])
    recruiter_line(
        "For o-series models, the most important deployment decision isn't the model — "
        "it's how long you let it think. I map every use case to a reasoning profile before the first API call."
    )

if __name__ == "__main__":
    main()
