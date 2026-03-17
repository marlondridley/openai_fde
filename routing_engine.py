from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


DEFAULT_WEIGHTS = {
    'time': 0.35,
    'cost': 0.25,
    'risk': 0.25,
    'capacity': 0.15,
}

RISK_TOLERANCE_THRESHOLDS = {
    'low': 0.25,
    'medium': 0.45,
    'high': 0.70,
    'critical_only': 1.00,
}


def _safe_float(value: Any, default: float = 0.0, lower: Optional[float] = None, upper: Optional[float] = None) -> float:
    try:
        out = float(value)
    except Exception:
        out = default
    if not isfinite(out):
        out = default
    if lower is not None and out < lower:
        out = lower
    if upper is not None and out > upper:
        out = upper
    return out


@dataclass
class Hop:
    step_order: int
    capability: str
    from_site: str
    to_site: str
    mode: str
    transit_hours: float
    cost_usd: float
    risk_score: float
    capacity_pressure: float
    skipped: bool = False


def _normalize_weights(weights: Optional[Dict[str, float]]) -> Dict[str, float]:
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update(weights)
    total = sum(max(v, 0.0) for v in w.values())
    if total <= 0:
        return dict(DEFAULT_WEIGHTS)
    return {k: max(v, 0.0) / total for k, v in w.items()}


def _resolve_risk_tolerance(risk_tolerance: Optional[Any]) -> Tuple[str, float]:
    if risk_tolerance is None:
        return 'medium', RISK_TOLERANCE_THRESHOLDS['medium']

    if isinstance(risk_tolerance, str):
        label = risk_tolerance.strip().lower()
        if label in RISK_TOLERANCE_THRESHOLDS:
            return label, RISK_TOLERANCE_THRESHOLDS[label]
        parsed = _safe_float(label, default=RISK_TOLERANCE_THRESHOLDS['medium'], lower=0.0, upper=1.0)
        return f'custom:{parsed:.2f}', parsed

    parsed = _safe_float(risk_tolerance, default=RISK_TOLERANCE_THRESHOLDS['medium'], lower=0.0, upper=1.0)
    return f'custom:{parsed:.2f}', parsed


def choose_active_flow(
    flow_df: pd.DataFrame,
    flow_id: Optional[str] = None,
    product_type: Optional[str] = None,
    version: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if flow_df is None or flow_df.empty:
        return None

    df = flow_df.copy()
    if 'active' in df.columns:
        df = df[df['active'].astype(bool)]
    if df.empty:
        return None

    if flow_id:
        exact = df[df['flow_id'] == flow_id]
        if not exact.empty:
            return exact.iloc[0].to_dict()

    if product_type:
        df = df[df['product_type'] == product_type]
    if version:
        df = df[df['version'] == version]
    if df.empty:
        return None

    df = df.sort_values(['product_type', 'version', 'flow_id'])
    return df.iloc[0].to_dict()


def get_flow_steps(flow_step_df: pd.DataFrame, flow_id: str) -> List[Dict[str, Any]]:
    if flow_step_df is None or flow_step_df.empty:
        return []
    df = flow_step_df[flow_step_df['flow_id'] == flow_id].copy()
    if df.empty:
        return []
    df = df.sort_values('step_order')

    steps: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        steps.append(
            {
                'step_order': int(row['step_order']),
                'capability_id': str(row['capability_id']),
                'required': bool(row.get('required', True)),
                'max_wait_hours': _safe_float(row.get('max_wait_hours'), default=24.0, lower=0.0),
            }
        )
    return steps


def _build_lane_graph(site_lane_df: pd.DataFrame) -> Dict[str, List[Dict[str, Any]]]:
    graph: Dict[str, List[Dict[str, Any]]] = {}
    if site_lane_df is None or site_lane_df.empty:
        return graph

    df = site_lane_df.copy()
    if 'active' in df.columns:
        df = df[df['active'].astype(bool)]

    for _, row in df.iterrows():
        from_site = str(row.get('from_site') or '').strip()
        to_site = str(row.get('to_site') or '').strip()
        if not from_site or not to_site:
            continue
        graph.setdefault(from_site, []).append(
            {
                'from_site': from_site,
                'to_site': to_site,
                'mode': str(row.get('mode') or 'air'),
                'transit_hours': _safe_float(row.get('transit_hours'), default=48.0, lower=0.0),
                'cost_usd': _safe_float(row.get('cost_usd'), default=10000.0, lower=0.0),
                'risk_score': _safe_float(row.get('risk_score'), default=0.4, lower=0.0, upper=1.0),
            }
        )

    return graph


def _build_site_capability_indexes(site_capability_df: pd.DataFrame, as_of: Optional[date] = None) -> Tuple[Dict[str, set], Dict[str, set]]:
    as_of = as_of or datetime.utcnow().date()
    as_of_ts = pd.Timestamp(as_of)
    cap_to_sites: Dict[str, set] = {}
    site_to_caps: Dict[str, set] = {}

    if site_capability_df is None or site_capability_df.empty:
        return cap_to_sites, site_to_caps

    df = site_capability_df.copy()

    if 'routing_eligible' in df.columns:
        df = df[df['routing_eligible'].astype(bool)]
    if 'demo_only' in df.columns:
        df = df[~df['demo_only'].astype(bool)]

    if 'valid_from' in df.columns:
        vf = pd.to_datetime(df['valid_from'], errors='coerce')
        df = df[vf.isna() | (vf <= as_of_ts)]
    if 'valid_to' in df.columns:
        vt = pd.to_datetime(df['valid_to'], errors='coerce')
        df = df[vt.isna() | (vt >= as_of_ts)]

    for _, row in df.iterrows():
        site = str(row.get('site_id') or '').strip()
        cap = str(row.get('capability_id') or '').strip()
        if not site or not cap:
            continue
        cap_to_sites.setdefault(cap, set()).add(site)
        site_to_caps.setdefault(site, set()).add(cap)

    return cap_to_sites, site_to_caps


def _build_capacity_lookup(site_capacity_df: pd.DataFrame) -> Dict[Tuple[str, str], Dict[str, float]]:
    lookup: Dict[Tuple[str, str], Dict[str, float]] = {}
    if site_capacity_df is None or site_capacity_df.empty:
        return lookup

    df = site_capacity_df.copy()
    if 'period_start' in df.columns:
        df['period_start'] = pd.to_datetime(df['period_start'], errors='coerce')
        df = df.sort_values('period_start').groupby(['site_id', 'capability_id']).tail(1)

    for _, row in df.iterrows():
        site = str(row.get('site_id') or '').strip()
        cap = str(row.get('capability_id') or '').strip()
        if not site or not cap:
            continue
        cap_units = _safe_float(row.get('capacity_units'), default=0.0, lower=0.0)
        util_units = _safe_float(row.get('utilized_units'), default=0.0, lower=0.0)
        pressure = (util_units / cap_units) if cap_units > 0 else 1.5
        lookup[(site, cap)] = {
            'capacity_units': cap_units,
            'utilized_units': util_units,
            'pressure': _safe_float(pressure, default=1.5, lower=0.0),
        }
    return lookup


def _score_from_totals(totals: Dict[str, float], weights: Dict[str, float]) -> float:
    time_norm = min(totals.get('time_hours', 0.0) / 168.0, 2.0)
    cost_norm = min(totals.get('cost_usd', 0.0) / 40000.0, 2.0)
    risk_norm = min(totals.get('avg_risk', 0.0), 1.0)
    cap_norm = min(totals.get('max_capacity_pressure', 0.0), 2.0)
    return (
        weights['time'] * time_norm
        + weights['cost'] * cost_norm
        + weights['risk'] * risk_norm
        + weights['capacity'] * cap_norm
    )


def _best_lane_between(graph: Dict[str, List[Dict[str, Any]]], from_site: str, to_site: str) -> Optional[Dict[str, Any]]:
    edges = [e for e in graph.get(from_site, []) if e['to_site'] == to_site]
    if not edges:
        return None

    # Prefer lower time + cost + risk blended for lane selection.
    return sorted(
        edges,
        key=lambda e: (e['transit_hours'] * 0.4) + (e['cost_usd'] / 1000.0 * 0.4) + (e['risk_score'] * 10.0 * 0.2),
    )[0]


def _empty_plan(reason: str, flow_id: str, rejections: Dict[int, Dict[str, int]], policy: Dict[str, Any]) -> Dict[str, Any]:
    return {
        'feasible': False,
        'flow_id': flow_id,
        'reason': reason,
        'paths': [],
        'rejections': rejections,
        'policy': policy,
    }


def plan_multihop_routes(
    source_site: str,
    flow_id: str,
    flow_step_df: pd.DataFrame,
    site_lane_df: pd.DataFrame,
    site_capability_df: pd.DataFrame,
    site_capacity_df: pd.DataFrame,
    top_k: int = 3,
    weights: Optional[Dict[str, float]] = None,
    risk_tolerance: Optional[Any] = None,
    allow_distribution_optional: bool = True,
) -> Dict[str, Any]:
    weights = _normalize_weights(weights)
    risk_label, max_step_risk = _resolve_risk_tolerance(risk_tolerance)
    flow_steps = get_flow_steps(flow_step_df, flow_id)
    if not flow_steps:
        return _empty_plan('No flow steps configured', flow_id, {}, {'distribution_optional_runtime': allow_distribution_optional})

    graph = _build_lane_graph(site_lane_df)
    cap_to_sites, site_to_caps = _build_site_capability_indexes(site_capability_df)
    cap_lookup = _build_capacity_lookup(site_capacity_df)

    source_site = str(source_site or '').strip()
    if not source_site:
        return _empty_plan('Missing source site', flow_id, {}, {'distribution_optional_runtime': allow_distribution_optional})

    # If the first step capability is already at source, the route can start in-place.
    states: List[Dict[str, Any]] = [
        {
            'current_site': source_site,
            'hops': [],
            'totals': {'time_hours': 0.0, 'cost_usd': 0.0, 'risk_sum': 0.0, 'hop_count': 0, 'max_capacity_pressure': 0.0},
        }
    ]
    rejection_counters: Dict[int, Counter] = {}

    for step in flow_steps:
        order = int(step['step_order'])
        capability = str(step['capability_id'])
        required = bool(step.get('required', True))

        if allow_distribution_optional and capability == 'distribution':
            required = False

        candidate_sites = sorted(cap_to_sites.get(capability, set()))
        if required and not candidate_sites:
            return _empty_plan(
                f"No eligible sites for required capability '{capability}'",
                flow_id,
                {k: dict(v) for k, v in rejection_counters.items()},
                {
                    'distribution_optional_runtime': allow_distribution_optional,
                    'risk_tolerance': risk_label,
                    'max_step_risk': round(max_step_risk, 3),
                },
            )

        step_reject = rejection_counters.setdefault(order, Counter())
        next_states: List[Dict[str, Any]] = []

        for state in states:
            current_site = state['current_site']

            if not required:
                skipped = {
                    'step_order': order,
                    'capability': capability,
                    'from_site': current_site,
                    'to_site': current_site,
                    'mode': 'skip',
                    'transit_hours': 0.0,
                    'cost_usd': 0.0,
                    'risk_score': 0.0,
                    'capacity_pressure': state['totals']['max_capacity_pressure'],
                    'skipped': True,
                }
                next_states.append(
                    {
                        'current_site': current_site,
                        'hops': state['hops'] + [skipped],
                        'totals': dict(state['totals']),
                    }
                )

            for dest in candidate_sites:
                lane: Optional[Dict[str, Any]] = None
                if dest == current_site:
                    if capability not in site_to_caps.get(dest, set()):
                        step_reject['no_capability'] += 1
                        continue
                    lane = {
                        'mode': 'internal',
                        'transit_hours': 1.0,
                        'cost_usd': 120.0,
                        'risk_score': 0.02,
                    }
                else:
                    lane = _best_lane_between(graph, current_site, dest)
                    if lane is None:
                        step_reject['no_lane'] += 1
                        continue

                pressure = cap_lookup.get((dest, capability), {}).get('pressure', 0.7)
                if pressure > 1.2:
                    step_reject['capacity_blocked'] += 1
                    continue
                if _safe_float(lane.get('risk_score'), default=0.0, lower=0.0, upper=1.0) > max_step_risk:
                    step_reject['risk_exceeds_tolerance'] += 1
                    continue

                hop = {
                    'step_order': order,
                    'capability': capability,
                    'from_site': current_site,
                    'to_site': dest,
                    'mode': lane['mode'],
                    'transit_hours': _safe_float(lane['transit_hours'], default=0.0, lower=0.0),
                    'cost_usd': _safe_float(lane['cost_usd'], default=0.0, lower=0.0),
                    'risk_score': _safe_float(lane['risk_score'], default=0.0, lower=0.0, upper=1.0),
                    'capacity_pressure': _safe_float(pressure, default=0.7, lower=0.0),
                    'skipped': False,
                }

                totals = dict(state['totals'])
                totals['time_hours'] += hop['transit_hours']
                totals['cost_usd'] += hop['cost_usd']
                totals['risk_sum'] += hop['risk_score']
                totals['hop_count'] += 1
                totals['max_capacity_pressure'] = max(totals['max_capacity_pressure'], hop['capacity_pressure'])

                next_states.append(
                    {
                        'current_site': dest,
                        'hops': state['hops'] + [hop],
                        'totals': totals,
                    }
                )

        if required and not next_states:
            return _empty_plan(
                f"No feasible candidates for required step {order} ({capability})",
                flow_id,
                {k: dict(v) for k, v in rejection_counters.items()},
                {
                    'distribution_optional_runtime': allow_distribution_optional,
                    'risk_tolerance': risk_label,
                    'max_step_risk': round(max_step_risk, 3),
                },
            )

        # Beam prune to keep planning deterministic and fast.
        pruned = []
        for s in next_states:
            hop_count = max(s['totals']['hop_count'], 1)
            avg_risk = s['totals']['risk_sum'] / hop_count
            score = _score_from_totals(
                {
                    'time_hours': s['totals']['time_hours'],
                    'cost_usd': s['totals']['cost_usd'],
                    'avg_risk': avg_risk,
                    'max_capacity_pressure': s['totals']['max_capacity_pressure'],
                },
                weights,
            )
            pruned.append((score, s))

        pruned.sort(key=lambda x: x[0])
        states = [s for _, s in pruned[: max(top_k * 6, 12)]]

    if not states:
        return _empty_plan(
            'No feasible path candidates produced',
            flow_id,
            {k: dict(v) for k, v in rejection_counters.items()},
            {
                'distribution_optional_runtime': allow_distribution_optional,
                'risk_tolerance': risk_label,
                'max_step_risk': round(max_step_risk, 3),
            },
        )

    paths: List[Dict[str, Any]] = []
    for s in states:
        hop_count = max(s['totals']['hop_count'], 1)
        avg_risk = s['totals']['risk_sum'] / hop_count
        totals = {
            'time_hours': round(_safe_float(s['totals']['time_hours'], 0.0, 0.0), 2),
            'cost_usd': round(_safe_float(s['totals']['cost_usd'], 0.0, 0.0), 2),
            'avg_risk': round(_safe_float(avg_risk, 0.0, 0.0, 1.0), 4),
            'max_capacity_pressure': round(_safe_float(s['totals']['max_capacity_pressure'], 0.0, 0.0), 4),
        }
        score = _score_from_totals(totals, weights)

        bottleneck = None
        non_skipped = [h for h in s['hops'] if not h.get('skipped')]
        if non_skipped:
            bottleneck = sorted(non_skipped, key=lambda h: h['capacity_pressure'], reverse=True)[0]

        paths.append(
            {
                'final_site': s['current_site'],
                'hops': s['hops'],
                'totals': totals,
                'score': round(_safe_float(score, 1.0, 0.0), 4),
                'bottleneck_capability': bottleneck.get('capability') if bottleneck else None,
                'bottleneck_site': bottleneck.get('to_site') if bottleneck else None,
            }
        )

    paths = sorted(paths, key=lambda p: p['score'])[: max(top_k, 1)]
    return {
        'feasible': len(paths) > 0,
        'flow_id': flow_id,
        'paths': paths,
        'rejections': {k: dict(v) for k, v in rejection_counters.items()},
        'policy': {
            'distribution_optional_runtime': allow_distribution_optional,
            'risk_tolerance': risk_label,
            'max_step_risk': round(max_step_risk, 3),
        },
    }


def plan_single_hop_baseline(
    source_site: str,
    flow_id: str,
    flow_step_df: pd.DataFrame,
    site_lane_df: pd.DataFrame,
    site_capability_df: pd.DataFrame,
    site_capacity_df: pd.DataFrame,
) -> Dict[str, Any]:
    # Single-hop baseline: choose one downstream site for first non-source capability, then stay local.
    result = plan_multihop_routes(
        source_site=source_site,
        flow_id=flow_id,
        flow_step_df=flow_step_df,
        site_lane_df=site_lane_df,
        site_capability_df=site_capability_df,
        site_capacity_df=site_capacity_df,
        top_k=1,
        risk_tolerance='high',
        allow_distribution_optional=True,
        weights=DEFAULT_WEIGHTS,
    )
    if not result.get('feasible'):
        return {'feasible': False, 'score': None}

    best = result['paths'][0]
    # Penalize route changes to approximate simpler baseline behavior.
    route_changes = 0
    prev = source_site
    for hop in best['hops']:
        if hop.get('skipped'):
            continue
        if hop['to_site'] != prev:
            route_changes += 1
            prev = hop['to_site']
    penalty = max(route_changes - 1, 0) * 0.2
    return {
        'feasible': True,
        'score': round(_safe_float(best['score'] + penalty, 1.0, 0.0), 4),
    }
