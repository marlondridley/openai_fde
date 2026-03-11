#!/usr/bin/env python3
"""
demo/run_all.py
Master runner — executes every demo in sequence.
Usage:
    python demo/run_all.py --mock          # no API calls, instant
    python demo/run_all.py --live          # real API calls
    python demo/run_all.py --part 3        # run only demo 3
    python demo/run_all.py --mock --part 6 # mock a specific demo
"""
import argparse, subprocess, sys, time, os

DEMOS = [
    ("1", "demo/01_ci_cd.py",           "CI/CD Regression Gate"),
    ("2", "demo/02_eval_pipeline.py",    "Golden Dataset Eval + LLM Judge"),
    ("3", "demo/03_sft_dataset.py",      "SFT Data Quality Filter"),
    ("4", "demo/04_rft_grader.py",       "RFT Grader + Gradability Test"),
    ("5", "demo/05_reasoning_tradeoff.py","Reasoning Token Budget Tradeoff"),
    ("6", "demo/06_scaffolding.py",      "Agent + Connector Registry"),
    ("7", "demo/07_chaos_engineering.py","Chaos Engineering + Fault Injection"),
    ("8", "demo/08_security_guardrails.py","4-Layer Guardrail + Red Team"),
    ("9", "demo/09_cost_optimisation.py","Semantic Cache + Cost ROI"),
]

DIVIDER = "─" * 70

def print_header(part, title):
    print(f"\n{DIVIDER}")
    print(f"  DEMO {part} — {title}")
    print(DIVIDER)

def run_demo(script, mode_flag):
    result = subprocess.run(
        [sys.executable, script, mode_flag],
        capture_output=False,
        text=True,
    )
    return result.returncode

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock", action="store_true", default=True,
                        help="Run in mock mode (no API calls)")
    parser.add_argument("--live", action="store_true",
                        help="Run with real OpenAI API calls")
    parser.add_argument("--part", type=str, default=None,
                        help="Run only a specific demo (e.g. --part 3)")
    parser.add_argument("--model", type=str, default="gpt-4o-mini",
                        help="Model to use for live runs")
    args = parser.parse_args()

    mode_flag = "--live" if args.live else "--mock"

    if args.live and not os.getenv("OPENAI_API_KEY"):
        print("❌  OPENAI_API_KEY not set. Run: export OPENAI_API_KEY=sk-...")
        sys.exit(1)

    demos_to_run = DEMOS
    if args.part:
        demos_to_run = [d for d in DEMOS if d[0] == args.part]
        if not demos_to_run:
            print(f"❌  No demo found for part {args.part}")
            sys.exit(1)

    mode_label = "🔴 LIVE (real API calls)" if args.live else "🟢 MOCK (no API calls)"
    print(f"\n{'='*70}")
    print(f"  AI DEPLOYMENT ENGINEER — DEMO SUITE")
    print(f"  Mode: {mode_label}")
    print(f"  Running {len(demos_to_run)} demo(s)")
    print(f"{'='*70}")

    results = []
    total_start = time.time()

    for part, script, title in demos_to_run:
        print_header(part, title)
        t0 = time.time()
        rc = run_demo(script, mode_flag)
        elapsed = time.time() - t0
        status = "✅ PASS" if rc == 0 else "❌ FAIL"
        results.append((part, title, status, elapsed))
        if rc != 0 and args.part:
            sys.exit(rc)
        time.sleep(0.3)

    total_elapsed = time.time() - total_start
    print(f"\n{'='*70}")
    print(f"  SUMMARY — {len(results)} demos in {total_elapsed:.1f}s")
    print(f"{'='*70}")
    for part, title, status, elapsed in results:
        print(f"  {status}  Part {part}: {title}  ({elapsed:.1f}s)")
    print()

    failures = [r for r in results if "FAIL" in r[2]]
    if failures:
        print(f"❌  {len(failures)} demo(s) failed")
        sys.exit(1)
    print(f"✅  All demos passed\n")

if __name__ == "__main__":
    main()
