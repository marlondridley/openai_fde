#!/usr/bin/env python3
"""
demo/03_sft_dataset.py
DEMO 3 - SFT Data Quality Filter + PII Scrub Gate
Live mode now hydrates candidate examples from Postgres and uses a real LLM judge.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from demo.utils import *

SYSTEM_PROMPT = (
    "You are a semiconductor operations analyst. Respond with: "
    "Executive Summary | Key Metrics | Risks | Recommendation. "
    "Use concrete numbers from the prompt. No AI disclaimers."
)

PII_PATTERNS = {
    "SSN": r"\b\d{3}-\d{2}-\d{4}\b",
    "Credit": r"\b(?:\d{4}[- ]?){3}\d{4}\b",
    "Email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
}


def check_pii(text):
    for label, pattern in PII_PATTERNS.items():
        if re.search(pattern, text):
            return True, label
    return False, None


def load_candidate_examples(conn):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT lot_id, fab_id, tech_id, wafers_started, yield_pct, status, lot_hist
            FROM production_lot
            ORDER BY start_date DESC, lot_id
            LIMIT 4
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

    if len(lots) < 3 or len(constraints) < 2:
        raise SystemExit(
            "Need seeded data in production_lot + operational_constraint "
            "(run scripts/seed_semiconductor_db.py)."
        )

    lot_a, lot_b, lot_c = lots[0], lots[1], lots[2]
    c0, c1 = constraints[0], constraints[1]

    return [
        {
            "id": f"LOT-{lot_a['lot_id']}",
            "user": (
                f"Review lot {lot_a['lot_id']} at fab {lot_a['fab_id']} (node {lot_a['tech_id']}). "
                f"Wafers started {lot_a['wafers_started']}, yield {float(lot_a['yield_pct']):.1f}%, "
                f"status {lot_a['status']}. Give a concise readiness recommendation."
            ),
            "assistant": (
                f"Executive Summary: Lot {lot_a['lot_id']} is in {lot_a['status']} state and tracks at "
                f"{float(lot_a['yield_pct']):.1f}% yield.\n"
                f"Key Metrics: Fab={lot_a['fab_id']}, Wafers={lot_a['wafers_started']}, Node={lot_a['tech_id']}.\n"
                f"Risks: Lot history includes {lot_a['lot_hist']} which may signal step instability.\n"
                "Recommendation: Keep in monitored release and gate escalation on the next yield checkpoint."
            ),
            "expected_score": 4.7,
        },
        {
            "id": f"LOT-{lot_b['lot_id']}-WEAK",
            "user": f"What does lot {lot_b['lot_id']} imply for this week's fab outlook?",
            "assistant": (
                "As an AI language model, I cannot confirm semiconductor operations "
                "without more context."
            ),
            "expected_score": 1.3,
        },
        {
            "id": f"CON-{c0['constraint_id']}",
            "user": (
                f"Summarise operational constraint {c0['constraint_id']} and explain impact on "
                f"lot {lot_c['lot_id']}."
            ),
            "assistant": (
                f"Executive Summary: Constraint {c0['constraint_id']} is active.\n"
                f"Key Metrics: Type={c0['constraint_type']}, Lot={lot_c['lot_id']}.\n"
                f"Risks: {c0['description']}.\n"
                f"Recommendation: Enforce policy '{c0['rule']}' before shipment release."
            ),
            "expected_score": 4.4,
        },
        {
            "id": "PII-BLOCK",
            "user": f"Prepare customer update for lot {lot_c['lot_id']} owner Jane Doe (SSN: 432-56-7890).",
            "assistant": "Executive Summary: Customer status update prepared for Jane Doe.",
            "expected_score": 4.0,
        },
        {
            "id": f"CON-{c1['constraint_id']}-HEDGE",
            "user": f"How should we react to constraint {c1['constraint_id']} this quarter?",
            "assistant": "I think it might be okay but not sure; maybe monitor and see what happens.",
            "expected_score": 1.8,
        },
        {
            "id": f"LOT-{lot_c['lot_id']}-JSON",
            "user": (
                f"Return an operations brief for lot {lot_c['lot_id']} with exact metrics and an "
                "action-oriented recommendation."
            ),
            "assistant": (
                f"Executive Summary: Lot {lot_c['lot_id']} is at fab {lot_c['fab_id']} with "
                f"{float(lot_c['yield_pct']):.1f}% yield.\n"
                f"Key Metrics: wafers_started={lot_c['wafers_started']}, status={lot_c['status']}.\n"
                "Risks: Yield sits below aspirational 90% readiness.\n"
                "Recommendation: Hold release and run engineering review for top defect drivers."
            ),
            "expected_score": 4.6,
        },
    ]


def score_example(client, model, example, mock):
    if mock:
        time.sleep(0.08)
        return float(example["expected_score"]), "mock baseline"

    resp = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "You score SFT examples from 1-5. "
                    "Criteria: format adherence, numeric grounding, clarity, no disclaimers/hedging."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"System prompt:\n{SYSTEM_PROMPT}\n\n"
                    f"User message:\n{example['user']}\n\n"
                    f"Assistant response:\n{example['assistant']}\n\n"
                    "Return JSON {\"score\": <1-5 number>, \"reason\": \"short text\"}."
                ),
            },
        ],
        temperature=0,
    )
    raw = json.loads(resp.choices[0].message.content or "{}")
    score = float(raw.get("score", 1.0))
    score = max(1.0, min(5.0, score))
    return score, str(raw.get("reason", ""))


def save_training_dataset(examples):
    dataset_dir = Path(__file__).resolve().parent / "datasets"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    output = dataset_dir / "sft_training.jsonl"
    with output.open("w", encoding="utf-8") as handle:
        for ex in examples:
            record = {
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": ex["user"]},
                    {"role": "assistant", "content": ex["assistant"]},
                ]
            }
            handle.write(json.dumps(record) + "\n")
    return output


def main():
    args = parse_demo_args("Demo 3: SFT Dataset Quality")
    mode = "MOCK" if is_mock(args) else "LIVE"
    client = get_client(is_mock(args))

    with get_db_connection() as conn:
        candidate_examples = load_candidate_examples(conn)

    print(f"\n{'=' * 65}")
    print(f"  DEMO 3 - SFT DATASET QUALITY FILTER  [{mode}]")
    print(f"  {len(candidate_examples)} DB-backed candidate examples -> quality gate")
    print(f"{'=' * 65}")

    section("STEP 1 - PII Scrub Gate (runs before quality scoring)", "RED")
    warn("Any PII in training data = BLOCKED before model evaluation")
    pii_blocked = []
    for ex in candidate_examples:
        text = ex["user"] + " " + ex["assistant"]
        has_pii, pii_type = check_pii(text)
        if has_pii:
            fail(f"{ex['id']} - BLOCKED: {pii_type} detected")
            pii_blocked.append(ex["id"])
        else:
            ok(f"{ex['id']} - PII clean")
    clean_examples = [e for e in candidate_examples if e["id"] not in pii_blocked]
    print()
    metric("PII blocked", str(len(pii_blocked)), f"{pii_blocked}")
    metric("Passing PII gate", str(len(clean_examples)), "continue to quality filter")

    section("STEP 2 - Auto-Quality-Filter (LLM judge)", "YELLOW")
    info("Threshold: 4.0 / 5.0")
    info(f"Judge model: {args.model}")
    print()
    approved = []
    rejected = []
    for ex in clean_examples:
        score, reason = score_example(client, args.model, ex, is_mock(args))
        passed = score >= 4.0
        icon = "✅" if passed else "❌"
        if not reason and score < 4.0:
            if "AI language model" in ex["assistant"]:
                reason = "Contains forbidden AI disclaimer"
            elif "might" in ex["assistant"].lower():
                reason = "Hedging language detected"
            else:
                reason = "Below quality threshold"
        print(f"  {icon} {ex['id']}  score={score:.1f}/5.0  {'APPROVED' if passed else 'REJECTED: ' + reason}")
        if passed:
            approved.append(ex)
        else:
            rejected.append(ex)

    output_path = save_training_dataset(approved)

    section("STEP 3 - Dataset Summary", "GREEN")
    total_in = len(candidate_examples)
    pii_cnt = len(pii_blocked)
    qual_cnt = len(rejected)
    appr_cnt = len(approved)
    print(f"\n  Input examples:        {total_in}")
    print(f"  Blocked (PII):         {pii_cnt}")
    print(f"  Rejected (quality):    {qual_cnt}")
    print(f"  Approved for training: {appr_cnt}  ({(appr_cnt / total_in) if total_in else 0:.0%})")
    print()
    ok(f"Training dataset saved: {output_path}")
    approx_tokens = sum(len(ex['user']) + len(ex['assistant']) for ex in approved) // 4
    info(f"Approx training volume: {approx_tokens:,} tokens")

    so_what(
        [
            "Examples are now hydrated from live manufacturing data in Postgres.",
            f"PII gate blocked {pii_cnt} record(s) before any judge/model call.",
            "Quality gate in live mode is scored by the real OpenAI model, not a hardcoded number.",
            "Approved rows are persisted into datasets/sft_training.jsonl for training use.",
        ]
    )
    recruiter_line(
        "The SFT filter now runs on live DB examples and a real judge model. "
        "Bad/unsafe examples are removed before they ever touch training."
    )


if __name__ == "__main__":
    main()
