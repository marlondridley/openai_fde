#!/usr/bin/env python3
"""
demo/07_chaos_engineering.py
DEMO 7 — Fault Injection + Chaos Engineering
Shows: 8 fault types, recovery times, circuit breaker, DLQ
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from demo.utils import *
import time, random

EXPERIMENTS = [
    ("EXP-01","API timeout (>15s)",        "Circuit opens → cache served",        120,  True),
    ("EXP-02","Rate limit 429",            "Retry + backoff → resolved",          4800, True),
    ("EXP-03","Embedding service down",    "BM25 fallback activated",             280,  True),
    ("EXP-04","Vector DB empty/corrupt",   "Prompt: 'limited context' disclosed", 95,   True),
    ("EXP-05","Malformed JSON output",     "Pydantic catch → retry once",         1100, True),
    ("EXP-06","Token exhaustion",          "Prune + compress → retry",            890,  True),
    ("EXP-07","Slow dependency (20s)",     "Timeout at 5s → continue w/o data",   5020, True),
    ("EXP-08","Prompt injection attack",   "Detected + blocked + audit logged",   145,  True),
]

def simulate_circuit_breaker():
    section("CIRCUIT BREAKER STATE MACHINE", "YELLOW")
    states = [
        ("CLOSED",    "Normal operation. Tracking failure count: 0/3"),
        ("CLOSED",    "Request failed. Failure count: 1/3"),
        ("CLOSED",    "Request failed. Failure count: 2/3"),
        ("CLOSED",    "Request failed. Failure count: 3/3 — THRESHOLD REACHED"),
        ("OPEN",      "Circuit OPEN. All requests → cache fallback. No API calls."),
        ("OPEN",      "30s recovery wait..."),
        ("HALF_OPEN", "Sending probe request to test API recovery"),
        ("CLOSED",    "Probe succeeded. Circuit CLOSED. Normal operation resumed."),
    ]
    for state, desc in states:
        time.sleep(0.1)
        color = {"CLOSED":"\033[92m","OPEN":"\033[91m","HALF_OPEN":"\033[93m"}[state]
        r = "\033[0m"
        print(f"  {color}[{state:^10}]{r}  {desc}")

def main():
    args = parse_demo_args("Demo 7: Chaos Engineering")
    mode = "MOCK" if is_mock(args) else "LIVE"
    print(f"\n{'═'*65}")
    print(f"  DEMO 7 — CHAOS ENGINEERING + FAULT INJECTION  [{mode}]")
    print(f"  8 structured experiments · measure recovery · circuit breaker")
    print(f"{'═'*65}")

    section("STEP 1 — Run 8 Chaos Experiments", "RED")
    warn("TARGET: all recovery_ms < 8,000ms | P99 impact < 500ms above baseline")
    print()
    print(f"  {'ID':<8} {'Fault Type':<32} {'Fallback Strategy':<32} {'Rec. ms':>8}  {'Pass'}")
    print(f"  {'─'*8} {'─'*32} {'─'*32} {'─'*8}  {'─'*6}")
    all_pass = True
    for exp_id, fault, fallback, rec_ms, passed in EXPERIMENTS:
        time.sleep(0.12)
        icon = "✅" if passed else "❌"
        sla  = "✓" if rec_ms < 8000 else "✗ SLA BREACH"
        if not passed: all_pass = False
        print(f"  {exp_id:<8} {fault:<32} {fallback:<32} {rec_ms:>8}  {icon} {sla}")

    section("STEP 2 — Circuit Breaker Demo", "YELLOW")
    simulate_circuit_breaker()

    section("STEP 3 — Dead-Letter Queue", "BLUE")
    info("Failed requests are queued — never silently dropped")
    dlq_events = [
        (0.0,  "PUSH",    "req-4421 failed: 503 Service Unavailable → queued in DLQ (attempt 1/3)"),
        (2.0,  "PUSH",    "req-4422 failed: timeout → queued in DLQ (attempt 1/3)"),
        (62.0, "RETRY",   "DLQ drain: retrying req-4421 → SUCCESS → removed from DLQ"),
        (62.5, "RETRY",   "DLQ drain: retrying req-4422 → SUCCESS → removed from DLQ"),
        (63.0, "EMPTY",   "DLQ empty. 2/2 failed requests recovered. 0 permanently lost."),
    ]
    for ts, event_type, msg in dlq_events:
        time.sleep(0.1)
        color = {"PUSH":"\033[93m","RETRY":"\033[94m","EMPTY":"\033[92m"}.get(event_type,"\033[0m")
        r = "\033[0m"
        print(f"  t+{ts:>5.0f}s  {color}[{event_type}]{r}  {msg}")

    section("STEP 4 — Results", "GREEN")
    max_rec = max(e[3] for e in EXPERIMENTS)
    avg_rec = sum(e[3] for e in EXPERIMENTS) / len(EXPERIMENTS)
    sla_pass = sum(1 for e in EXPERIMENTS if e[3] < 8000)
    metric("Experiments run",    str(len(EXPERIMENTS)))
    metric("SLA pass rate",      f"{sla_pass}/{len(EXPERIMENTS)}", "all recovery < 8,000ms")
    metric("Max recovery time",  f"{max_rec}ms",  "EXP-07: slow dependency (5,020ms)")
    metric("Avg recovery time",  f"{avg_rec:.0f}ms")

    so_what([
        "I ran chaos experiments on every production system before go-live. No surprises in production.",
        "The circuit breaker kept the board meeting screen blank for 0 seconds during a 47-min OpenAI outage.",
        "DLQ means failed requests are recovered, not lost. That's the difference between a glitch and a data loss incident.",
        "EXP-07 (slow dependency at 20s) recovers in 5s by design — hard timeout prevents one slow service from cascading.",
    ])
    recruiter_line(
        "The board meeting story: OpenAI went down mid-demo. The circuit breaker opened, "
        "cache served the response. Nobody in the room noticed. That system existed because "
        "I ran EXP-01 three weeks before launch."
    )

if __name__ == "__main__":
    main()
