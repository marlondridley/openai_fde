#!/usr/bin/env python3
"""
demo/06_scaffolding.py
DEMO 6 - Agent + Connector Registry Full Loop
Live data now comes from Postgres-backed connector state.
"""
import json
import os
import sys
import time
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from demo.utils import *

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "crm_get_account",
            "description": "Get account details: ARR, health score, owner, renewal date, NPS",
            "parameters": {
                "type": "object",
                "properties": {"account_id": {"type": "string"}},
                "required": ["account_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dw_query_metric",
            "description": "Query a metric (monthly_revenue, churn_rate, nps_score) from warehouse",
            "parameters": {
                "type": "object",
                "properties": {
                    "metric": {"type": "string"},
                    "period": {"type": "string", "default": "latest"},
                },
                "required": ["metric"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "jira_get_open_tickets",
            "description": "Get open tickets for a customer by company code",
            "parameters": {
                "type": "object",
                "properties": {"company_code": {"type": "string"}},
                "required": ["company_code"],
            },
        },
    },
]

CONNECTOR_STATE = {
    "crm": {},
    "metrics": {},
    "tickets": {},
    "meta": {},
}
AUDIT_LOG = []


def compute_health(avg_yield):
    if avg_yield >= 90:
        return "green"
    if avg_yield >= 80:
        return "yellow"
    return "red"


def hydrate_connector_state(conn):
    state = {"crm": {}, "metrics": {}, "tickets": {}, "meta": {}}

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                f.fab_id,
                f.name,
                f.owner,
                f.status,
                COALESCE(AVG(pl.yield_pct), 0) AS avg_yield,
                COALESCE(SUM(pl.wafers_started), 0) AS wafers_total,
                MAX(pl.start_date) AS last_start
            FROM fab f
            LEFT JOIN production_lot pl ON pl.fab_id = f.fab_id
            GROUP BY f.fab_id, f.name, f.owner, f.status
            ORDER BY wafers_total DESC, f.fab_id
            LIMIT 3
            """
        )
        fabs = cur.fetchall()

        cur.execute(
            """
            SELECT
                DATE_TRUNC('month', start_date) AS month_start,
                SUM(wafers_started * COALESCE(yield_pct, 0) / 100.0) AS out_units,
                AVG(CASE WHEN UPPER(status) LIKE 'HOLD%' THEN 1.0 ELSE 0.0 END) * 100 AS hold_pct,
                AVG(yield_pct) AS avg_yield
            FROM production_lot
            GROUP BY DATE_TRUNC('month', start_date)
            ORDER BY month_start DESC
            LIMIT 3
            """
        )
        monthly = cur.fetchall()

        cur.execute(
            """
            SELECT constraint_id, description, constraint_type, affected_fab_id
            FROM operational_constraint
            ORDER BY constraint_id
            LIMIT 12
            """
        )
        constraints = cur.fetchall()

    if not fabs:
        raise SystemExit("No fab rows found. Seed DB first with scripts/seed_semiconductor_db.py.")

    account_aliases = {}
    for i, fab in enumerate(fabs, start=1):
        account_id = f"ACC-{i:03d}"
        company_code = "".join(ch for ch in fab["fab_id"].upper() if ch.isalnum())[:4] or f"C{i:03d}"
        avg_yield = float(fab["avg_yield"] or 0)
        health = compute_health(avg_yield)
        nps = max(20, min(80, int(20 + avg_yield * 0.6)))
        renewal_date = fab["last_start"] + timedelta(days=180) if fab["last_start"] else None

        arr = int((fab["wafers_total"] or 0) * 9)
        state["crm"][account_id] = {
            "name": fab["name"],
            "fab_id": fab["fab_id"],
            "arr": arr,
            "health": health,
            "owner": fab["owner"] or "Unassigned",
            "renewal_date": str(renewal_date) if renewal_date else "TBD",
            "nps": nps,
            "company_code": company_code,
        }
        account_aliases[fab["fab_id"]] = account_id

    revenue = {}
    churn = {}
    nps_score = {}
    for row in reversed(monthly):
        key = row["month_start"].strftime("%b").lower()
        revenue[key] = round(float(row["out_units"] or 0) / 1000.0, 2)
        churn[key] = round(float(row["hold_pct"] or 0), 2)
        nps_score[key] = max(20, min(80, int(20 + float(row["avg_yield"] or 0) * 0.6)))

    state["metrics"] = {
        "monthly_revenue": {
            **revenue,
            "unit": "M equivalent throughput",
            "trend": "derived from production_lot output",
        },
        "churn_rate": {
            **churn,
            "unit": "% lots on hold",
            "trend": "derived from production_lot status",
        },
        "nps_score": {
            **nps_score,
            "unit": "pts (proxy)",
            "trend": "derived from average yield",
        },
    }

    for acc in state["crm"].values():
        state["tickets"][acc["company_code"]] = []

    for c in constraints:
        fab_id = c["affected_fab_id"]
        if not fab_id:
            continue
        account_id = account_aliases.get(fab_id)
        if not account_id:
            continue
        company_code = state["crm"][account_id]["company_code"]
        priority = "High" if "export" in (c["constraint_type"] or "").lower() else "Medium"
        state["tickets"][company_code].append(
            {
                "id": c["constraint_id"],
                "summary": c["description"],
                "priority": priority,
                "status": "In Progress" if priority == "High" else "Open",
            }
        )

    first_account_id = next(iter(state["crm"].keys()))
    first = state["crm"][first_account_id]
    state["meta"] = {
        "primary_account_id": first_account_id,
        "primary_company_code": first["company_code"],
        "task": (
            f"I need a pre-call briefing for {first['name']} ({first_account_id}) before renewal. "
            "Pull account health, check Q1 trend metrics, and flag open high-priority tickets. "
            "Give me the 3 most important points for the call."
        ),
    }
    return state


def execute_tool(tool_name, args):
    if tool_name == "crm_get_account":
        return CONNECTOR_STATE["crm"].get(args.get("account_id"), {"error": "account not found"})

    if tool_name == "dw_query_metric":
        metric_name = args.get("metric", "")
        metric_obj = CONNECTOR_STATE["metrics"].get(metric_name)
        if not metric_obj:
            return {"error": f"Unknown metric: {metric_name}", "available": list(CONNECTOR_STATE["metrics"].keys())}

        period = args.get("period", "latest")
        value = None
        if period == "latest":
            scalar_keys = [k for k in metric_obj.keys() if k not in {"unit", "trend"}]
            value = metric_obj[scalar_keys[-1]] if scalar_keys else None
        else:
            value = metric_obj.get(period.lower())

        return {
            "metric": metric_name,
            "period": period,
            "value": value,
            "unit": metric_obj.get("unit"),
            "trend": metric_obj.get("trend"),
        }

    if tool_name == "jira_get_open_tickets":
        code = (args.get("company_code") or "").upper()
        return CONNECTOR_STATE["tickets"].get(code, [])

    return {"error": f"Unknown tool: {tool_name}"}


def log_tool_call(tool_name, args, result, latency_ms):
    AUDIT_LOG.append(
        {
            "tool": tool_name,
            "args": args,
            "result": result,
            "latency_ms": latency_ms,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )


def build_mock_tool_sequence():
    account_id = CONNECTOR_STATE["meta"]["primary_account_id"]
    company_code = CONNECTOR_STATE["meta"]["primary_company_code"]
    return [
        ("crm_get_account", {"account_id": account_id}),
        ("dw_query_metric", {"metric": "monthly_revenue", "period": "latest"}),
        ("dw_query_metric", {"metric": "churn_rate", "period": "latest"}),
        ("jira_get_open_tickets", {"company_code": company_code}),
    ]


def main():
    args = parse_demo_args("Demo 6: Scaffolding Agent")
    mode = "MOCK" if is_mock(args) else "LIVE"
    client = get_client(is_mock(args))

    global CONNECTOR_STATE
    with get_db_connection() as conn:
        CONNECTOR_STATE = hydrate_connector_state(conn)

    task = CONNECTOR_STATE["meta"]["task"]

    print(f"\n{'=' * 65}")
    print(f"  DEMO 6 - AGENT + CONNECTOR REGISTRY FULL LOOP  [{mode}]")
    print("  Postgres-backed connectors · full tool-use loop · compliance audit log")
    print(f"{'=' * 65}")

    section("STEP 1 - Registered Connectors", "CYAN")
    connectors = [
        ("crm", 1, "crm_get_account"),
        ("data-warehouse", 1, "dw_query_metric"),
        ("jira", 1, "jira_get_open_tickets"),
    ]
    for name, n_tools, tools in connectors:
        ok(f"{name:<24} ({n_tools} tool)  -  {tools}")
    info(f"Total tools available to agent: {sum(c[1] for c in connectors)}")

    section("STEP 2 - Task", "BLUE")
    print(f"\n  User: {task}\n")

    section("STEP 3 - Agentic Tool-Use Loop", "YELLOW")
    info("Agent decides which tools to call, executes, feeds results back, repeats until done")
    print()

    messages = [
        {
            "role": "system",
            "content": (
                "You are a business AI assistant. Use tools to gather information. "
                "Never invent data. If a tool errors, state the error explicitly."
            ),
        },
        {"role": "user", "content": task},
    ]

    tool_calls_made = []
    iterations = 0
    final_answer = ""

    if is_mock(args):
        for step, (tool_name, tool_args) in enumerate(build_mock_tool_sequence(), start=1):
            time.sleep(0.1)
            t0 = time.time()
            result = execute_tool(tool_name, tool_args)
            latency = int((time.time() - t0) * 1000) + 40
            log_tool_call(tool_name, tool_args, result, latency)
            tool_calls_made.append(tool_name)
            print(f"  [{step}] CALL  {tool_name}({json.dumps(tool_args)})")
            print(f"      RESULT {json.dumps(result)[:120]}...")
            print(f"      latency={latency}ms\n")

        iterations = 3
        primary_id = CONNECTOR_STATE["meta"]["primary_account_id"]
        account = CONNECTOR_STATE["crm"][primary_id]
        tickets = CONNECTOR_STATE["tickets"].get(account["company_code"], [])
        high_tickets = [t for t in tickets if t.get("priority") == "High"]
        final_answer = (
            f"Pre-call briefing for {account['name']} ({primary_id}):\n\n"
            f"1) Health={account['health']} | ARR=${account['arr']:,} | NPS={account['nps']}.\n"
            "2) Latest trend metrics from warehouse should anchor the value narrative.\n"
            f"3) Open high-priority tickets: {len(high_tickets)}. "
            "Lead with proactive status updates on those items."
        )
    else:
        for iteration in range(10):
            resp = client.chat.completions.create(
                model=args.model,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.2,
            )
            msg = resp.choices[0].message

            assistant_message = {"role": "assistant", "content": msg.content or ""}
            if msg.tool_calls:
                assistant_message["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ]
            messages.append(assistant_message)

            if not msg.tool_calls:
                final_answer = msg.content or ""
                iterations = iteration + 1
                break

            for tc in msg.tool_calls:
                tool_args = json.loads(tc.function.arguments or "{}")
                t0 = time.time()
                result = execute_tool(tc.function.name, tool_args)
                latency = int((time.time() - t0) * 1000)
                log_tool_call(tc.function.name, tool_args, result, latency)
                tool_calls_made.append(tc.function.name)
                print(f"  CALL  {tc.function.name}({json.dumps(tool_args)})")
                print(f"  ->    {json.dumps(result)[:120]}...")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result),
                    }
                )

        if not final_answer:
            final_answer = "Unable to complete full briefing within iteration limit."
            iterations = 10

    section("STEP 4 - Final Answer", "GREEN")
    print(f"\n{final_answer}\n")

    section("STEP 5 - Compliance Audit Log", "BLUE")
    info(f"Every tool call logged - {len(AUDIT_LOG)} entries")
    for entry in AUDIT_LOG:
        print(f"  {entry['timestamp']}  {entry['tool']:<24} {entry['latency_ms']}ms")

    section("STEP 6 - Summary", "CYAN")
    metric("Tool calls made", str(len(tool_calls_made)), str(tool_calls_made))
    metric("Agent iterations", str(iterations))
    metric("Total latency", f"{sum(e['latency_ms'] for e in AUDIT_LOG)}ms", "sum of all tool calls")
    metric("Audit entries", str(len(AUDIT_LOG)), "timestamped and queryable")

    so_what(
        [
            "Connector outputs are now hydrated from live Postgres tables, not in-file mock dicts.",
            "Agent behavior is auditable: every tool call captures inputs, outputs, latency, and timestamp.",
            "Standardized tool schemas keep model orchestration stable while data sources change.",
            "This is the scaffolding layer that makes model responses operationally useful.",
        ]
    )
    recruiter_line(
        "The model is only one layer. Reliable connectors and audit logging are what make AI systems "
        "usable in enterprise workflows."
    )


if __name__ == "__main__":
    main()
