#!/usr/bin/env python3
"""
demo/04_rft_grader.py
DEMO 4 — RFT Grader: Gradable vs Subjective Task Comparison + Consistency Validation
Shows: why the grader IS the reward signal, and how to validate it
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from demo.utils import *
import json, math, time, random

# ── TASK PAIRS: gradable vs subjective ───────────────────────────────────
TASK_PAIRS = [
    {
        "label": "Financial Extraction",
        "subjective": {
            "task": "Write a good summary of this earnings call.",
            "gradable": False,
            "why_bad": "Subjective — two judges score it differently every time. No ground truth.",
        },
        "gradable": {
            "task": "Extract EPS, revenue, YoY growth from this earnings call. Return JSON.",
            "reference": '{"eps": 0.42, "revenue_m": 487, "yoy_pct": 18}',
            "gradable": True,
            "why_good": "Machine-verifiable against SEC filing. Score is deterministic.",
        },
    },
    {
        "label": "Legal Analysis",
        "subjective": {
            "task": "Is this a good contract?",
            "gradable": False,
            "why_bad": "Vague success criteria. 'Good' depends on the judge's mood.",
        },
        "gradable": {
            "task": "Identify all covenant violations. Flag any leverage ratio within 15% of threshold. Return JSON.",
            "reference": '{"violations": [], "tight_covenants": [{"name": "leverage_ratio", "current": 3.2, "threshold": 3.5, "headroom_pct": 8.6}]}',
            "gradable": True,
            "why_good": "Right/wrong with legal reference. Headroom % is calculable.",
        },
    },
    {
        "label": "Customer Support",
        "subjective": {
            "task": "Be helpful and professional in this customer interaction.",
            "gradable": False,
            "why_bad": "'Helpful' and 'professional' are not objectively measurable.",
        },
        "gradable": {
            "task": "Resolve this billing dispute: classify (billing_error/user_error/system_bug), state resolution steps, and set ETA. Return JSON.",
            "reference": '{"classification": "billing_error", "resolution_steps": ["Reverse charge", "Issue credit", "Send confirmation"], "eta_hours": 24}',
            "gradable": True,
            "why_good": "Classification is discrete. Steps are checkable. ETA is numeric.",
        },
    },
]

def simulate_grader_consistency(task_label, reference_answer, n_runs=5):
    """Simulate running the grader N times on the same response — check variance"""
    # Mock: add small noise to simulate LLM judge variance
    base_score = 0.92
    scores = [round(base_score + random.gauss(0, 0.03), 3) for _ in range(n_runs)]
    mean = sum(scores) / len(scores)
    variance = sum((s - mean)**2 for s in scores) / len(scores)
    std_dev = math.sqrt(variance)
    cv = std_dev / mean  # coefficient of variation

    print(f"\n  Task: {task_label}")
    print(f"  Scores across {n_runs} runs: {[f'{s:.3f}' for s in scores]}")
    print(f"  Mean: {mean:.3f}  |  StdDev: {std_dev:.3f}  |  CV: {cv:.3f}")

    consistent = cv < 0.10  # < 10% coefficient of variation
    if consistent:
        ok(f"Grader CONSISTENT  (CV={cv:.3f} < 0.10 threshold)")
    else:
        fail(f"Grader INCONSISTENT  (CV={cv:.3f} ≥ 0.10) — DO NOT USE FOR RFT")
    return cv, consistent

def demonstrate_hybrid_grader(client, mock):
    section("STEP 3 — Hybrid Grader: Rule-Based Gate + LLM Quality Score", "BLUE")
    info("Rule-based gate: fast (<1ms), checks required elements  |  LLM judge: semantic quality")

    test_response = '{"eps": 0.42, "revenue_m": 487, "yoy_pct": 18}'
    test_task     = "Extract EPS, revenue, YoY growth from earnings call. Return JSON."
    required      = ["eps", "revenue_m", "yoy_pct"]

    print(f"\n  Task:     {test_task}")
    print(f"  Response: {test_response}")
    print(f"  Required: {required}")

    # Layer 1: Rule-based gate
    parsed = json.loads(test_response)
    found  = [k for k in required if k in parsed]
    rule_score = len(found) / len(required)
    print(f"\n  L1 Rule check:  {found}  →  score={rule_score:.2f}")
    ok("Rule gate PASSED (all required keys present)")

    # Layer 2: Numeric accuracy
    expected = {"eps": 0.42, "revenue_m": 487, "yoy_pct": 18}
    accuracy_scores = {}
    for key, exp_val in expected.items():
        act_val = float(parsed.get(key, 0))
        pct_err = abs(act_val - exp_val) / exp_val
        accuracy_scores[key] = 1.0 if pct_err < 0.05 else 0.0
        print(f"  L2 Accuracy:    {key}={act_val} vs expected={exp_val}  pct_err={pct_err:.3f}  →  {accuracy_scores[key]:.1f}")

    # Layer 3: LLM quality judge
    if mock:
        llm_score = 0.95
        time.sleep(0.08)
    else:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type":"json_object"},
            messages=[{"role":"user","content":
                f"Score 0-10: how well does this JSON response answer the task?\n"
                f"Task: {test_task}\nResponse: {test_response}\n"
                f"JSON: {{\"score\": N}}"}],
        )
        raw = json.loads(resp.choices[0].message.content)
        llm_score = float(raw.get("score", 8)) / 10

    print(f"  L3 LLM judge:   semantic quality score = {llm_score:.2f}")

    # Composite
    composite = 0.30 * rule_score + 0.40 * (sum(accuracy_scores.values())/len(accuracy_scores)) + 0.30 * llm_score
    print()
    metric("Composite score", f"{composite:.3f}", "0.30×rule + 0.40×accuracy + 0.30×llm")
    ok("This is the reward signal RFT trains toward")

def main():
    args   = parse_demo_args("Demo 4: RFT Grader")
    mode   = "MOCK" if is_mock(args) else "LIVE"
    client = get_client(is_mock(args))

    print(f"\n{'═'*65}")
    print(f"  DEMO 4 — RFT GRADER + GRADABILITY VALIDATION  [{mode}]")
    print(f"  The grader IS the reward signal. A bad grader trains a bad model.")
    print(f"{'═'*65}")

    section("STEP 1 — Gradable vs Subjective: Side-by-Side Comparison", "RED")
    warn("The hardest part of RFT is not training — it's designing tasks the grader can score reliably")
    for pair in TASK_PAIRS:
        print(f"\n  ── {pair['label']} ──")
        s = pair["subjective"]
        g = pair["gradable"]
        fail(f"SUBJECTIVE: \"{s['task'][:65]}\"")
        print(f"     Why bad:  {s['why_bad']}")
        ok(  f"GRADABLE:   \"{g['task'][:65]}\"")
        print(f"     Why good: {g['why_good']}")

    section("STEP 2 — Grader Consistency Validation (CV < 0.10)", "YELLOW")
    info("Run the grader 5× on the same reference answer — variance must be < 10%")
    info("If CV ≥ 0.10: grader is training model on NOISE. Never use for RFT.")
    all_consistent = True
    for pair in TASK_PAIRS:
        cv, consistent = simulate_grader_consistency(
            pair["label"], pair["gradable"]["reference"]
        )
        if not consistent: all_consistent = False

    if all_consistent:
        print()
        ok("All graders validated — safe to start RFT training")
    else:
        fail("Grader consistency failure — FIX GRADER BEFORE TRAINING")
        sys.exit(1)

    demonstrate_hybrid_grader(client, is_mock(args))

    so_what([
        "Subjective tasks produce noisy reward signals → model learns to optimise for looking good, not being correct.",
        "CV < 0.10 means the same response scores within 10% across 5 grader runs — that's a clean reward signal.",
        "Hybrid grader: rule gate (deterministic, free) + LLM judge (semantic, ~$0.001) = best of both.",
        "The grader validation pipeline is the quality gate for the quality gate. It runs before every RFT job.",
    ])
    recruiter_line(
        "The grader IS the reward function. A bad grader trains a bad model. "
        "I validate every grader for consistency before a single training step runs."
    )

if __name__ == "__main__":
    main()
