"""
models.py — pure business logic.
One responsibility: transform data into decisions and display-ready structures.
No UI. No SQL. No direct API calls (delegates to data.py for those).
"""
import importlib.util
import json
import os
import time
from datetime import datetime
from math import isfinite, radians, sin, cos, sqrt, atan2

import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import requests

from routing_engine import DEFAULT_WEIGHTS, plan_multihop_routes
from constants import (
    AGENT_PLUGIN_DIR,
    BASE_SHIPPING_COST_PER_WAFER_USD,
    COUNTRY_LOOKUP,
    CURRENCY_MAP,
    FAB_COUNTRY,
    FAB_TO_ASM,
    FX_API_URL,
    FX_FALLBACK_RATES,
    IMF_GROWTH_FALLBACK,
    IMF_INDICATOR,
    LPI_2023_FALLBACK,
    LPI_INDICATORS,
    LOCATION_COORDS,
    LOCATION_TO_ISO3,
    ROUTING_DEFAULT_RISK_TOLERANCE,
    VOLATILE_CURRENCIES,
    WORLD_BANK_FALLBACK,
    WORLD_BANK_INDICATORS,
)


# ── JSON safety ───────────────────────────────────────────────────────────────

def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float):
        return value if isfinite(value) else None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    return value


def _json_dumps_safe(value: Any) -> str:
    return json.dumps(_json_safe(value), allow_nan=False)


# ── Numeric helpers ───────────────────────────────────────────────────────────

def _finite_float(
    value: Any,
    default: Optional[float] = 0.0,
    min_value: Optional[float] = None,
    max_value: Optional[float] = None,
) -> Optional[float]:
    try:
        out = float(value)
    except Exception:
        return default
    if not isfinite(out):
        return default
    if min_value is not None and out < min_value:
        out = min_value
    if max_value is not None and out > max_value:
        out = max_value
    return out


def _normalize_route_weights(weights: Optional[Dict[str, float]]) -> Dict[str, float]:
    out = dict(DEFAULT_WEIGHTS)
    if weights:
        for key in ('time', 'cost', 'risk', 'capacity'):
            if key in weights:
                try:
                    out[key] = float(weights[key])
                except Exception:
                    pass
    total = sum(max(v, 0.0) for v in out.values())
    if total <= 0:
        return dict(DEFAULT_WEIGHTS)
    return {k: max(v, 0.0) / total for k, v in out.items()}


# ── Model pins ────────────────────────────────────────────────────────────────

def get_model_pins() -> Dict[str, Any]:
    """Return auditable runtime/governance model config from environment."""
    runtime_model    = os.getenv('AGENT_RUNTIME_MODEL', 'gpt-4o-mini')
    governance_model = os.getenv('GOVERNANCE_EVAL_MODEL', 'gpt-4o-mini')
    approved = [
        m.strip()
        for m in os.getenv('AGENT_APPROVED_MODELS', 'gpt-4o-mini,gpt-4o').split(',')
        if m.strip()
    ]
    return {
        'runtime_model':       runtime_model,
        'governance_model':    governance_model,
        'approved_models':     approved,
        'runtime_approved':    runtime_model in approved,
        'governance_approved': governance_model in approved,
    }


# ── Plugin discovery ──────────────────────────────────────────────────────────

def discover_agent_plugins(agent_dir: Path = AGENT_PLUGIN_DIR) -> Dict[str, Dict[str, Any]]:
    """Scan agents/ for agent_*.py files and return a registry keyed by AGENT_ID."""
    plugins: Dict[str, Dict[str, Any]] = {}
    if not agent_dir.exists():
        return plugins

    for path in sorted(agent_dir.glob('agent_*.py')):
        try:
            module_name = f"demo_agents_{path.stem}"
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            runner = getattr(module, 'run_agent_turn', None)
            if not callable(runner):
                continue

            agent_id = str(getattr(module, 'AGENT_ID', '') or '').strip() or path.stem.replace('agent_', '')
            if not agent_id or agent_id in plugins:
                continue

            plugins[agent_id] = {
                'id':          agent_id,
                'label':       str(getattr(module, 'LABEL', agent_id)),
                'description': str(getattr(module, 'DESCRIPTION', '')),
                'parameters':  getattr(module, 'PARAMETERS', {}),
                'module':      module,
                'runner':      runner,
                'source_path': str(path),
            }
        except Exception:
            continue

    return plugins


# ── Fab metadata ──────────────────────────────────────────────────────────────

def prepare_fab_metadata(df_fabs: pd.DataFrame) -> pd.DataFrame:
    """Enrich raw fab rows with map coordinates and country metadata."""
    df = df_fabs.copy()
    df['lat']          = df['fab_id'].map(lambda x: LOCATION_COORDS.get(x, (0, 0))[0])
    df['lon']          = df['fab_id'].map(lambda x: LOCATION_COORDS.get(x, (0, 0))[1])
    df['country_code'] = df['fab_id'].map(FAB_COUNTRY)
    df['country_name'] = df['country_code'].map(lambda c: COUNTRY_LOOKUP.get(c, {}).get('name', 'Unknown'))
    df['iso2']         = df['country_code'].map(lambda c: COUNTRY_LOOKUP.get(c, {}).get('iso2'))
    return df


# ── Macro overlay ─────────────────────────────────────────────────────────────

def build_country_overlay(df_fabs: pd.DataFrame) -> pd.DataFrame:
    """Build country-level macro context table aligned to current fab footprint."""
    from data import fetch_worldbank_indicator, fetch_imf_growth

    countries = sorted({c for c in df_fabs['country_code'].dropna().unique()})
    if not countries:
        return pd.DataFrame()

    iso2 = [COUNTRY_LOOKUP[c]['iso2'] for c in countries]
    iso3 = countries

    indicator_frames = {}
    for code, label in WORLD_BANK_INDICATORS.items():
        df = fetch_worldbank_indicator(code, iso2)
        if df.empty:
            continue
        df['date'] = pd.to_numeric(df['date'], errors='coerce')
        latest = df.sort_values('date').groupby('country').tail(1)
        indicator_frames[code] = (
            latest[['country', 'value']].set_index('country').rename(columns={'value': label})
        )

    imf = fetch_imf_growth(iso3)
    combined = pd.DataFrame(index=iso2)
    for frame in indicator_frames.values():
        combined = combined.join(frame, how='left')
    if not imf.empty:
        imf = imf.set_index('country').rename(columns={'value': 'IMF GDP growth forecast'})
        imf.index = [COUNTRY_LOOKUP.get(code, {}).get('iso2') for code in imf.index]
        combined = combined.join(imf['IMF GDP growth forecast'], how='left')

    combined['Country'] = combined.index.map(
        lambda iso: next((info['name'] for info in COUNTRY_LOOKUP.values() if info['iso2'] == iso), iso)
    )
    combined['Front-end fabs'] = combined.index.map(
        lambda iso: df_fabs[(df_fabs['iso2'] == iso) & (df_fabs['site_type'] == 'Front-End')].shape[0]
    )
    combined['Assembly sites'] = combined.index.map(
        lambda iso: df_fabs[(df_fabs['iso2'] == iso) & (df_fabs['site_type'] != 'Front-End')].shape[0]
    )
    return combined.reset_index(drop=True)


# ── LPI ───────────────────────────────────────────────────────────────────────

def join_lpi_to_fabs(fabs_df: pd.DataFrame):
    """Attach LPI risk fields to each fab, backfilling from cached fallbacks."""
    from data import fetch_lpi_data

    lpi_df, source = fetch_lpi_data()
    fabs = fabs_df.copy()
    fabs['iso3'] = fabs['location'].map(LOCATION_TO_ISO3)
    fabs['iso3'] = fabs['iso3'].fillna(fabs['fab_id'].map(FAB_COUNTRY))
    result = fabs.merge(lpi_df, on='iso3', how='left')

    for metric in LPI_INDICATORS.values():
        if metric not in result.columns:
            result[metric] = np.nan
        for iso3, values in LPI_2023_FALLBACK.items():
            mask = result['iso3'] == iso3
            result.loc[mask, metric] = result.loc[mask, metric].fillna(values.get(metric))

    return result, source


# ── FX overlay ────────────────────────────────────────────────────────────────

def build_fx_overlay(fabs_df: pd.DataFrame):
    """Compute per-fab shipping exposure in USD with risk labels."""
    from data import fetch_fx_rates

    rates, ts = fetch_fx_rates()
    rows = []
    for _, row in fabs_df.iterrows():
        iso3     = row.get('country_code') or row.get('iso3')
        currency = CURRENCY_MAP.get(iso3, 'USD')
        rate     = rates.get(currency, 1.0) or 1.0
        wafers   = row.get('total_wafer_starts_per_month') or 0
        shipping_usd   = wafers * BASE_SHIPPING_COST_PER_WAFER_USD
        shipping_local = shipping_usd * rate
        cost_per_wafer_local = shipping_local / wafers if wafers else 0
        risk = 'High' if currency in VOLATILE_CURRENCIES else ('Low' if currency == 'USD' else 'Medium')
        rows.append({
            'fab_id':                row['fab_id'],
            'country':               iso3,
            'currency':              currency,
            'fx_rate':               rate,
            'wafers_per_month':      wafers,
            'shipping_cost_usd':     shipping_usd,
            'shipping_cost_local':   shipping_local,
            'cost_per_wafer_local':  cost_per_wafer_local,
            'risk':                  risk,
        })
    return pd.DataFrame(rows), ts


# ── Capacity / routing / forecast ─────────────────────────────────────────────

def _build_flow_distribution(filtered_lots: pd.DataFrame) -> pd.DataFrame:
    lot_flow = (
        filtered_lots.groupby('fab_id')
        .agg({'lot_id': 'count', 'wafers_started': 'sum'})
        .reset_index()
    )
    rows = []
    for _, row in lot_flow.iterrows():
        targets = FAB_TO_ASM.get(row['fab_id']) or []
        if not targets:
            continue
        per_target = row['lot_id'] / len(targets)
        wafers_per = (row['wafers_started'] / len(targets)) if row['wafers_started'] else 0
        for target in targets:
            rows.append({'source': row['fab_id'], 'target': target,
                         'lots': per_target, 'wafers': wafers_per})
    return pd.DataFrame(rows)


def build_capacity_table(filtered_lots: pd.DataFrame, fabs_df: pd.DataFrame) -> pd.DataFrame:
    """Front-end utilization against declared monthly wafer-start capacity."""
    front_end = (
        fabs_df[fabs_df['site_type'] == 'Front-End']
        [['fab_id', 'country_name', 'total_wafer_starts_per_month']]
        .copy()
        .rename(columns={'total_wafer_starts_per_month': 'capacity'})
    )
    demand = filtered_lots.groupby('fab_id')['wafers_started'].sum()
    front_end['demand']      = front_end['fab_id'].map(demand).fillna(0)
    front_end['utilization'] = front_end.apply(
        lambda r: (r['demand'] / r['capacity']) if r['capacity'] else 0, axis=1
    )
    front_end['slack'] = front_end['capacity'] - front_end['demand']

    def _status(util: float) -> str:
        if util >= 1.0: return 'Over capacity'
        if util >= 0.8: return 'Tight'
        return 'Healthy'

    front_end['status']          = front_end['utilization'].apply(_status)
    front_end['utilization_pct'] = (front_end['utilization'] * 100).round(1)
    return front_end.sort_values('utilization', ascending=False)


def build_assembly_load(filtered_lots: pd.DataFrame, fabs_df: pd.DataFrame) -> pd.DataFrame:
    """Assembly-site inbound load and utilization based on routing map + lot volume."""
    flow = _build_flow_distribution(filtered_lots)
    if flow.empty:
        return pd.DataFrame()

    assembly_load = (
        flow.groupby('target')['wafers'].sum().reset_index()
        .rename(columns={'target': 'assembly_id', 'wafers': 'inbound_wafers'})
    )
    assembly_capacity = (
        fabs_df[fabs_df['site_type'] != 'Front-End']
        [['fab_id', 'total_wafer_starts_per_month']]
        .rename(columns={'fab_id': 'assembly_id', 'total_wafer_starts_per_month': 'capacity'})
    )
    result = assembly_load.merge(assembly_capacity, on='assembly_id', how='left')
    result['capacity']       = result['capacity'].fillna(0)
    result['utilization']    = result.apply(
        lambda r: (r['inbound_wafers'] / r['capacity']) if r['capacity'] else 0, axis=1
    )
    result['utilization_pct'] = (result['utilization'] * 100).round(1)
    return result.sort_values('utilization', ascending=False)


def build_forecast_series(filtered_lots: pd.DataFrame) -> pd.DataFrame:
    """Simple near-term demand forecast from recent monthly wafer-start history."""
    if filtered_lots.empty:
        return pd.DataFrame()

    monthly = filtered_lots.copy()
    monthly['month'] = monthly['start_date'].dt.to_period('M').dt.to_timestamp()
    monthly = monthly.groupby('month')['wafers_started'].sum().reset_index().sort_values('month')
    if monthly.empty:
        return pd.DataFrame()

    history           = monthly.copy()
    history['type']   = 'Actual'
    last_value        = history['wafers_started'].iloc[-1]
    growth = (
        (last_value - history['wafers_started'].iloc[-2]) / history['wafers_started'].iloc[-2]
        if len(history) >= 2 and history['wafers_started'].iloc[-2] > 0
        else 0.05
    )
    growth = max(min(growth, 0.25), -0.1)

    current, last_month = last_value, history['month'].max()
    future_rows = []
    for i in range(1, 4):
        current = max(current * (1 + growth), 0)
        future_rows.append({
            'month': last_month + pd.DateOffset(months=i),
            'wafers_started': current,
            'type': 'Forecast',
        })
    return pd.concat([history, pd.DataFrame(future_rows)], ignore_index=True)


# ── Route path display helpers ────────────────────────────────────────────────

def build_route_path_rows(paths: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten path objects into display-ready rows for tables and scorecards."""
    rows = []
    for idx, path_item in enumerate(paths or [], start=1):
        totals = path_item.get('totals', {})
        rows.append({
            'rank':                 idx,
            'final_site':           path_item.get('final_site'),
            'score':                float(_finite_float(path_item.get('score'), 1.0, 0.0, 2.0) or 1.0),
            'time_hours':           float(_finite_float(totals.get('time_hours'), 0.0, 0.0) or 0.0),
            'cost_usd':             float(_finite_float(totals.get('cost_usd'), 0.0, 0.0) or 0.0),
            'avg_risk':             float(_finite_float(totals.get('avg_risk'), 0.0, 0.0, 1.0) or 0.0),
            'max_capacity_pressure': float(_finite_float(totals.get('max_capacity_pressure'), 0.0, 0.0) or 0.0),
            'bottleneck_capability': path_item.get('bottleneck_capability'),
            'bottleneck_site':       path_item.get('bottleneck_site'),
        })
    return rows


# ── Agent tools ───────────────────────────────────────────────────────────────

def query_production_data(
    fab_id: str, lots_df: pd.DataFrame, constraints_df: pd.DataFrame
) -> Dict[str, Any]:
    """Tool 1 — gather normalised production context for the selected fab."""
    fab_lots        = lots_df[lots_df['fab_id'] == fab_id]
    fab_constraints = constraints_df[constraints_df['affected_fab_id'] == fab_id]
    return {
        'fab_id':           fab_id,
        'lot_count':        int(fab_lots.shape[0]),
        'wafers_started':   float(fab_lots['wafers_started'].sum() or 0),
        'avg_yield_pct':    float(fab_lots['yield_pct'].mean() or 0),
        'constraint_count': int(fab_constraints.shape[0]),
    }


def evaluate_multihop_routes(
    lot_id: str,
    flow_id: Optional[str],
    lots_df: pd.DataFrame,
    routing_tables: Dict[str, pd.DataFrame],
    top_k: int,
    risk_tolerance: Optional[str],
    weights: Optional[Dict[str, float]],
) -> Dict[str, Any]:
    """Tool 3 — call the multi-hop planner with lot-implied flow support."""
    lot_match = lots_df[lots_df['lot_id'].astype(str) == str(lot_id)]
    if lot_match.empty:
        return {
            'feasible': False, 'recommended_target': None, 'confidence': 0.0,
            'reason': f'Lot {lot_id} not found under current filters',
            'paths': [], 'candidates': [], 'rejections': {},
            'policy': {'distribution_optional_runtime': True},
        }

    lot_row          = lot_match.sort_values('start_date', ascending=False).iloc[0]
    source_site      = str(lot_row.get('fab_id') or '').strip()
    resolved_flow_id = str(flow_id or lot_row.get('flow_id') or '').strip()

    if not resolved_flow_id:
        return {
            'feasible': False, 'recommended_target': None, 'confidence': 0.0,
            'reason': f'Lot {lot_id} has no flow_id assigned',
            'paths': [], 'candidates': [], 'rejections': {},
            'policy': {'distribution_optional_runtime': True},
        }

    weight_profile = _normalize_route_weights(weights)
    route_plan = plan_multihop_routes(
        source_site=source_site,
        flow_id=resolved_flow_id,
        flow_step_df=routing_tables['flow_step'],
        site_lane_df=routing_tables['site_lane'],
        site_capability_df=routing_tables['site_capability'],
        site_capacity_df=routing_tables['site_operation_capacity'],
        top_k=int(max(1, min(5, int(top_k)))),
        weights=weight_profile,
        risk_tolerance=risk_tolerance,
        allow_distribution_optional=True,
    )

    paths     = route_plan.get('paths') or []
    path_rows = build_route_path_rows(paths)
    best      = paths[0] if paths else None
    best_score = _finite_float(best.get('score') if best else None, 1.0, 0.0, 2.0) or 1.0
    confidence = max(0.2, min(0.98, 1.0 - (best_score / 2.0))) if best else 0.0

    if best:
        totals = best.get('totals', {})
        reason = (
            f"Best path ends at {best.get('final_site')} with score {best_score:.3f}; "
            f"time={_finite_float(totals.get('time_hours'), 0.0, 0.0):.1f}h, "
            f"cost=${_finite_float(totals.get('cost_usd'), 0.0, 0.0):,.0f}, "
            f"risk={_finite_float(totals.get('avg_risk'), 0.0, 0.0, 1.0):.2f}."
        )
    else:
        reason = str(route_plan.get('reason') or 'No feasible multi-hop route found')

    return {
        'tool_name':          'evaluate_multihop_routes',
        'tool_version':       'v2',
        'lot_id':             str(lot_id),
        'flow_id':            resolved_flow_id,
        'source_site':        source_site,
        'risk_tolerance':     str((route_plan.get('policy') or {}).get('risk_tolerance', risk_tolerance or ROUTING_DEFAULT_RISK_TOLERANCE)),
        'weights':            weight_profile,
        'top_k':              int(max(1, min(5, int(top_k)))),
        'feasible':           bool(route_plan.get('feasible')),
        'recommended_target': best.get('final_site') if best else None,
        'confidence':         round(float(confidence), 3),
        'reason':             reason,
        'paths':              paths,
        'candidates':         path_rows,
        'rejections':         route_plan.get('rejections') or {},
        'policy':             route_plan.get('policy') or {},
        'lot_context': {
            'fab_id':          source_site,
            'tech_id':         str(lot_row.get('tech_id') or ''),
            'wafers_started':  float(_finite_float(lot_row.get('wafers_started'), 0.0, 0.0) or 0.0),
            'yield_pct':       float(_finite_float(lot_row.get('yield_pct'), 0.0, 0.0, 100.0) or 0.0),
        },
    }
