"""
agent.py — agent orchestration only.
One responsibility: run a decision turn and log it.
No UI code. No data fetching. No SQL.
"""
import os
import time
from typing import Any, Dict, Optional
import pandas as pd

import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))

from db import execute_write
from constants import FAB_TO_ASM, ROUTING_DEFAULT_RISK_TOLERANCE
from models import (
    query_production_data, evaluate_multihop_routes,
    _normalize_route_weights, _json_dumps_safe, get_model_pins,
)
from risk_monitor import query_risk_signals


# ── Telemetry writes (DRY: one write helper, not four) ───────────────────────

def _log_tool_call(session_id, name, args, result, latency_ms, success):
    execute_write(
        "INSERT INTO agent_tool_call (session_id, tool_name, args_json, result_json, latency_ms, success) "
        "VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, %s)",
        (session_id, name, _json_dumps_safe(args), _json_dumps_safe(result), int(latency_ms), bool(success)),
    )


def _timed_tool(session_id, name, args, fn):
    """Run fn(), log the call, return the result. DRY: replaces 3 repeated t0/t1 blocks."""
    t0 = time.time()
    result = fn()
    _log_tool_call(session_id, name, args, result, int((time.time() - t0) * 1000), True)
    return result


# ── LLM narrative (KISS: one function, fallback string defined once) ──────────

_FALLBACK_TEMPLATE = (
    "Recommended route: {recommended_target}\n"
    "Confidence: {confidence}\n"
    "Reason: {reason}\n"
    "{note}"
)

def generate_answer(prompt: str, context: dict, recommendation: dict, model: str
                    ) -> tuple[str, dict]:
    """Call the LLM. Returns (answer_text, usage_meta). Falls back to template on any failure."""
    api_key = os.getenv('OPENAI_API_KEY')
    if not api_key:
        return _make_fallback_answer(recommendation, "Set OPENAI_API_KEY to enable AI narrative."), _no_usage()

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        t0 = time.time()
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {'role': 'system', 'content':
                    'You are a semiconductor routing copilot. '
                    'Base your answer only on the supplied JSON. Be concise and operational.'},
                {'role': 'user', 'content':
                    f"Prompt: {prompt}\n\n"
                    f"Context: {context}\n\n"
                    f"Recommendation: {_json_dumps_safe(recommendation)}"},
            ],
            temperature=0.2,
            max_tokens=280,
        )
        usage = resp.usage
        return resp.choices[0].message.content.strip(), {
            'model': model,
            'prompt_tokens':     int(getattr(usage, 'prompt_tokens', 0) or 0),
            'completion_tokens': int(getattr(usage, 'completion_tokens', 0) or 0),
            'latency_ms':        int((time.time() - t0) * 1000),
        }
    except Exception as exc:
        return _make_fallback_answer(recommendation, f"LLM unavailable ({exc})."), _no_usage()


def _make_fallback_answer(recommendation: dict, note: str) -> str:
    return _FALLBACK_TEMPLATE.format(
        recommended_target=recommendation.get('recommended_target'),
        confidence=recommendation.get('confidence'),
        reason=recommendation.get('reason'),
        note=note,
    )

def _no_usage() -> dict:
    return {'model': 'fallback-template', 'prompt_tokens': 0, 'completion_tokens': 0, 'latency_ms': 0}


# ── Main agent turn ───────────────────────────────────────────────────────────

def run_agent_turn(
    session_id: int,
    prompt: str,
    lot_id: str,
    flow_id: Optional[str],
    lots_df: pd.DataFrame,
    constraints_df: pd.DataFrame,
    routing_tables: Dict[str, pd.DataFrame],
    risk_tolerance: Optional[str] = None,
    weights: Optional[Dict[str, float]] = None,
    top_k: int = 3,
    risk_by_site: Optional[Dict[str, Any]] = None,
    agent_id: str = 'builtin_multihop',
) -> tuple[str, dict, dict, int]:
    pins = get_model_pins()
    lot_row = lots_df[lots_df['lot_id'].astype(str) == str(lot_id)]
    source_site = str(lot_row.iloc[0]['fab_id']) if not lot_row.empty else 'UNKNOWN'

    # Tool 1: production context
    context = _timed_tool(
        session_id, 'query_production_data', {'fab_id': source_site, 'lot_id': lot_id},
        lambda: query_production_data(source_site, lots_df, constraints_df),
    )
    context['lot_id'] = str(lot_id)

    # Tool 2: risk signals
    risk = _timed_tool(
        session_id, 'query_risk_signals', {'fab_id': source_site, 'lot_id': lot_id},
        lambda: query_risk_signals(source_site, FAB_TO_ASM.get(source_site, []), risk_by_site or {}),
    )

    # Tool 3: multi-hop route scoring
    recommendation = _timed_tool(
        session_id, 'evaluate_multihop_routes',
        {'lot_id': lot_id, 'flow_id': flow_id, 'top_k': top_k,
         'risk_tolerance': risk_tolerance, 'weights': weights, 'agent_id': agent_id},
        lambda: evaluate_multihop_routes(
            lot_id, flow_id, lots_df, routing_tables, top_k, risk_tolerance, weights),
    )
    recommendation['agent_id'] = str(agent_id)

    # LLM narrative
    context['risk_snapshot'] = risk
    answer, meta = generate_answer(prompt, context, recommendation, pins['runtime_model'])

    # Persist decision
    decision_id = execute_write(
        """
        INSERT INTO agent_decision
            (session_id, decision_type, recommendation_json, confidence,
             model_name, governance_model, prompt_tokens, completion_tokens,
             latency_ms, decision_status, notes)
        VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING decision_id
        """,
        (session_id, 'multi_hop_route_recommendation', _json_dumps_safe(recommendation),
         float(recommendation.get('confidence') or 0), meta['model'], pins['governance_model'],
         meta['prompt_tokens'], meta['completion_tokens'], meta['latency_ms'],
         'pending', f"[agent_id={agent_id}] {prompt}"),
        fetchone=True,
    )

    return answer, recommendation, meta, int(decision_id[0])


# ── Plugin wrapper ────────────────────────────────────────────────────────────

def run_agent_turn_with_plugin(plugin_registry, selected_agent_id, **kwargs):
    """Route to plugin if registered, fall back to built-in agent."""
    plugin = plugin_registry.get(selected_agent_id)
    if not plugin:
        return run_agent_turn(**kwargs, agent_id='builtin_multihop')

    context = {
        'run_default_multihop_agent': run_agent_turn,
        'query_production_data':      query_production_data,
        'query_risk_signals':         query_risk_signals,
        'evaluate_multihop_routes':   evaluate_multihop_routes,
        'generate_answer':            generate_answer,
        'log_tool_call':              _log_tool_call,
        'get_model_pins':             get_model_pins,
        'fab_to_asm':                 FAB_TO_ASM,
        'normalize_route_weights':    _normalize_route_weights,
        'routing_default_risk_tolerance': ROUTING_DEFAULT_RISK_TOLERANCE,
    }
    try:
        answer, payload, meta, decision_id = plugin['runner'](
            {**kwargs, 'agent_id': selected_agent_id}, context
        )
        if isinstance(payload, dict):
            payload.setdefault('agent_id', selected_agent_id)
        return answer, payload, meta, decision_id
    except Exception as exc:
        return run_agent_turn(
            **kwargs,
            prompt=f"{kwargs['prompt']}\n[plugin_error={type(exc).__name__}: {exc}]",
            agent_id='builtin_multihop_fallback',
        )
