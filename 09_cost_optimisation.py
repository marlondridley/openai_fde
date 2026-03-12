#!/usr/bin/env python3
"""
demo/09_cost_optimisation.py
DEMO 9 - Semantic Cache + Model Tiering + Batch API ROI
Live mode now uses Postgres workloads and real OpenAI calls for cache simulation.
"""
import hashlib
import math
import os
import random
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from demo.utils import *

CACHE = {}
CACHE_HITS = 0
CACHE_MISS = 0

TIERS = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
}


def cost(model, input_tok, output_tok):
    tier = TIERS.get(model, TIERS["gpt-4o-mini"])
    return (input_tok * tier["input"] + output_tok * tier["output"]) / 1_000_000


def mock_embed(text):
    h = hashlib.md5(text.encode()).digest()
    vec = [struct.unpack("f", h[i : i + 4])[0] for i in range(0, 16, 4)]
    random.seed(int.from_bytes(h, "big"))
    vec.extend([random.gauss(0, 0.1) for _ in range(1532)])
    mag = math.sqrt(sum(x * x for x in vec))
    return [x / mag for x in vec]


def embed_text(client, text, mock):
    if mock:
        return mock_embed(text)
    emb = client.embeddings.create(model="text-embedding-3-small", input=text)
    return emb.data[0].embedding


def cosine_sim(a, b):
    return sum(x * y for x, y in zip(a, b))


def semantic_cache_get(client, query, mock, threshold=0.92):
    global CACHE_HITS, CACHE_MISS
    qvec = embed_text(client, query, mock)
    best_score = 0.0
    best_val = None
    for _, (cvec, cval) in CACHE.items():
        sim = cosine_sim(qvec, cvec)
        if sim > best_score:
            best_score = sim
            best_val = cval
    if best_score >= threshold:
        CACHE_HITS += 1
        return best_val, best_score
    CACHE_MISS += 1
    return None, best_score


def semantic_cache_set(client, query, response, mock):
    CACHE[query] = (embed_text(client, query, mock), response)


def load_workload(conn):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT case_id, category, prompt
            FROM eval_cases
            ORDER BY case_id
            LIMIT 8
            """
        )
        rows = cur.fetchall()

        cur.execute(
            """
            SELECT COUNT(*) AS recent_case_rows
            FROM eval_case_results ecr
            JOIN eval_runs er ON er.run_id = ecr.run_id
            WHERE er.started_at >= NOW() - INTERVAL '30 days'
            """
        )
        recent = cur.fetchone()

    if not rows:
        raise SystemExit(
            "No eval_cases found. Run demo/02_eval_pipeline.py first to seed eval workload data."
        )

    queries = []
    for row in rows:
        prompt = row["prompt"].strip()
        category = row["category"].lower()
        if category in {"reasoning", "safety", "generation"}:
            preferred_model = "gpt-4o"
        else:
            preferred_model = "gpt-4o-mini"
        queries.append({"query": prompt, "base_model": preferred_model, "category": category})

    # Add paraphrases to create realistic cache hit opportunities.
    for row in rows[:3]:
        queries.append(
            {
                "query": f"Summarise this request: {row['prompt'][:120]}",
                "base_model": "gpt-4o-mini",
                "category": "summarisation",
            }
        )

    seed_queries = [
        (rows[0]["prompt"], f"Reference response for {rows[0]['case_id']}"),
        (rows[1]["prompt"], f"Reference response for {rows[1]['case_id']}"),
    ]

    recent_case_rows = int((recent or {}).get("recent_case_rows") or 0)
    monthly_requests = max(200_000, recent_case_rows * 500)
    return queries, seed_queries, monthly_requests


def maybe_call_model(client, mock, model, query):
    if mock:
        time.sleep(0.04)
        return f"Mock answer for: {query[:60]}"
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": query}],
        max_tokens=180,
        temperature=0.2,
    )
    return resp.choices[0].message.content or ""


def main():
    args = parse_demo_args("Demo 9: Cost Optimisation")
    mode = "MOCK" if is_mock(args) else "LIVE"
    client = get_client(is_mock(args))

    with get_db_connection() as conn:
        queries, seed_queries, monthly_requests = load_workload(conn)

    print(f"\n{'=' * 65}")
    print(f"  DEMO 9 - SEMANTIC CACHE + MODEL TIERING ROI  [{mode}]")
    print("  Workload sampled from eval_cases/eval_runs in Postgres")
    print(f"{'=' * 65}")

    section("STEP 1 - Baseline (no optimisation)", "RED")
    warn("Before: cache disabled, all requests go to higher-cost path")
    baseline_cost_per_1k = 5.00
    baseline_monthly = monthly_requests / 1000 * baseline_cost_per_1k
    metric("Monthly requests", f"{monthly_requests:,}")
    metric("Cost per 1K", f"${baseline_cost_per_1k:.2f}", "legacy default routing")
    metric("Monthly cost", f"${baseline_monthly:,.0f}")

    section("STEP 2 - Optimisation 1: Semantic Cache (threshold=0.92)", "BLUE")
    info("Seeding cache from prior eval workload prompts")

    for q, r in seed_queries:
        semantic_cache_set(client, q, r, is_mock(args))
    info(f"Cache seeded with {len(seed_queries)} entries")
    print()

    print(f"  {'Query':<48} {'Cache':>12}  {'$saved'}")
    print(f"  {'-' * 48} {'-' * 12}  {'-' * 8}")
    for item in queries:
        query = item["query"]
        base_model = item["base_model"]
        result, sim = semantic_cache_get(client, query, is_mock(args))
        hit = result is not None
        saved = cost(base_model, 500, 350) if hit else 0
        if not hit:
            answer = maybe_call_model(client, is_mock(args), base_model, query)
            semantic_cache_set(client, query, answer, is_mock(args))
        hit_label = f"HIT {sim:.3f}" if hit else f"MISS {sim:.3f}"
        print(f"  {query[:48]:<48} {hit_label:>12}  ${saved:.5f}")

    hr = CACHE_HITS / (CACHE_HITS + CACHE_MISS) if (CACHE_HITS + CACHE_MISS) else 0
    monthly_cache_savings = monthly_requests * hr * cost("gpt-4o", 500, 350)
    print()
    metric("Cache hit rate", f"{hr:.0%}", f"{CACHE_HITS} hits / {CACHE_HITS + CACHE_MISS} total")
    metric("Monthly savings", f"${monthly_cache_savings:,.0f}", "estimated from observed hit rate")

    section("STEP 3 - Optimisation 2: Model Tiering", "YELLOW")
    info("Routing sampled workload to gpt-4o-mini vs gpt-4o by query complexity")

    counts = {"gpt-4o": 0, "gpt-4o-mini": 0}
    for item in queries:
        counts[item["base_model"]] += 1
    total = len(queries)

    task_split = {
        "mini_path": {
            "pct": counts["gpt-4o-mini"] / total if total else 0,
            "input": 450,
            "output": 220,
            "model": "gpt-4o-mini",
        },
        "full_path": {
            "pct": counts["gpt-4o"] / total if total else 0,
            "input": 850,
            "output": 420,
            "model": "gpt-4o",
        },
    }

    total_tiered_cost_per_req = 0
    print()
    for task, cfg in task_split.items():
        c = cost(cfg["model"], cfg["input"], cfg["output"])
        weighted = c * cfg["pct"]
        total_tiered_cost_per_req += weighted
        print(f"  {task:<18} {cfg['pct']:.0%}  ${c:.5f}  model={cfg['model']}")

    monthly_tiered = monthly_requests * total_tiered_cost_per_req
    print()
    metric("Blended cost/request", f"${total_tiered_cost_per_req:.5f}", f"from {len(queries)} sampled prompts")
    metric("Monthly tiered cost", f"${monthly_tiered:,.0f}")

    section("STEP 4 - Optimisation 3: Batch API (50% discount)", "CYAN")
    info("Assume async-eligible workload receives 50% discount")
    batch_pct = 0.30
    batch_savings = monthly_tiered * batch_pct * 0.50
    metric("Batchable requests", f"{batch_pct:.0%}", "scheduled eval/report workloads")
    metric("Batch savings", f"${batch_savings:,.0f}/month")

    section("STEP 5 - Total ROI Summary", "GREEN")
    final_monthly = monthly_tiered - batch_savings
    reduction_pct = (1 - final_monthly / baseline_monthly) * 100 if baseline_monthly else 0

    print(f"\n  {'Baseline (no optimisation):':<42}  ${baseline_monthly:>10,.0f}/month")
    print(f"  {'After semantic caching:':<42}  ${baseline_monthly - monthly_cache_savings:>10,.0f}/month")
    print(f"  {'After model tiering:':<42}  ${monthly_tiered:>10,.0f}/month")
    print(f"  {'After Batch API:':<42}  ${final_monthly:>10,.0f}/month")
    print(f"  {'-' * 55}")
    print(f"  FINAL MONTHLY COST:  ${final_monthly:>8,.0f}  ({reduction_pct:.0f}% reduction)")
    print(f"  ANNUAL SAVINGS:      ${(baseline_monthly - final_monthly) * 12:>8,.0f}")

    so_what(
        [
            "Workload and savings assumptions are now tied to live eval data in Postgres.",
            "Semantic cache behavior can run against real embeddings in live mode.",
            "Tiering decisions are inferred from sampled query categories.",
            "This frames optimisation as sustainable economics, not just token math."
        ]
    )
    recruiter_line(
        "I show cost optimisation with concrete workload data, measured cache hit behavior, and explicit ROI math "
        "so finance and engineering can align on one operating model."
    )


if __name__ == "__main__":
    main()
