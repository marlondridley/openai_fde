#!/usr/bin/env python3
"""
run_all.py
Master runner - executes every demo in sequence.
Usage:
    python run_all.py --mock
    python run_all.py --live
    python run_all.py --part 3
"""
import argparse
import os
import subprocess
import sys
import time

DEMOS = [
    ("1", "01_ci_cd.py", "CI/CD Regression Gate"),
    ("2", "02_eval_pipeline.py", "Golden Dataset Eval + LLM Judge"),
    ("3", "03_sft_dataset.py", "SFT Data Quality Filter"),
    ("4", "04_rft_grader.py", "RFT Grader + Gradability Test"),
    ("5", "05_reasoning_tradeoff.py", "Reasoning Token Budget Tradeoff"),
    ("6", "06_scaffolding.py", "Agent + Connector Registry"),
    ("7", "07_chaos_engineering.py", "Chaos Engineering + Fault Injection"),
    ("8", "08_security_guardrails.py", "4-Layer Guardrail + Red Team"),
    ("9", "09_cost_optimisation.py", "Semantic Cache + Cost ROI"),
    ("10", "10_routing_eval.py", "Multi-hop Routing Eval Gate"),
]

DIVIDER = "-" * 70


def print_header(part, title):
    print(f"\n{DIVIDER}")
    print(f"  DEMO {part} - {title}")
    print(DIVIDER)


def run_demo(script, mode_flag, model):
    cmd = [sys.executable, script, mode_flag]
    if model:
        cmd.extend(["--model", model])
    result = subprocess.run(cmd, capture_output=False, text=True)
    return result.returncode


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock", action="store_true", default=True, help="Run in mock mode")
    parser.add_argument("--live", action="store_true", help="Run with real OpenAI API calls")
    parser.add_argument("--part", type=str, default=None, help="Run only one part (e.g. --part 3)")
    parser.add_argument("--model", type=str, default="gpt-4o-mini", help="Model to use for live runs")
    args = parser.parse_args()

    mode_flag = "--live" if args.live else "--mock"

    if args.live and not os.getenv("OPENAI_API_KEY"):
        print("[FAIL] OPENAI_API_KEY not set.")
        print("Set it in your environment or .env before --live runs.")
        sys.exit(1)

    demos_to_run = DEMOS
    if args.part:
        demos_to_run = [d for d in DEMOS if d[0] == args.part]
        if not demos_to_run:
            print(f"[FAIL] No demo found for part {args.part}")
            sys.exit(1)

    mode_label = "LIVE (real API calls)" if args.live else "MOCK (no API calls)"
    print(f"\n{'=' * 70}")
    print("  AI DEPLOYMENT ENGINEER - DEMO SUITE")
    print(f"  Mode: {mode_label}")
    print(f"  Running {len(demos_to_run)} demo(s)")
    print(f"{'=' * 70}")

    results = []
    total_start = time.time()

    for part, script, title in demos_to_run:
        print_header(part, title)
        t0 = time.time()
        rc = run_demo(script, mode_flag, args.model)
        elapsed = time.time() - t0
        status = "PASS" if rc == 0 else "FAIL"
        results.append((part, title, status, elapsed))

        if rc != 0 and args.part:
            sys.exit(rc)
        time.sleep(0.2)

    total_elapsed = time.time() - total_start
    print(f"\n{'=' * 70}")
    print(f"  SUMMARY - {len(results)} demos in {total_elapsed:.1f}s")
    print(f"{'=' * 70}")
    for part, title, status, elapsed in results:
        print(f"  [{status}] Part {part}: {title} ({elapsed:.1f}s)")
    print()

    failures = [r for r in results if r[2] == "FAIL"]
    if failures:
        print(f"[FAIL] {len(failures)} demo(s) failed")
        sys.exit(1)

    print("[PASS] All demos passed")


if __name__ == "__main__":
    main()
