"""
dashboard.py — UI only.
One responsibility: render the page.
No SQL. No API calls. No business logic.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from db import run_query, execute_write
from data import fetch_bls_series, fetch_worldbank_indicator, fetch_lpi_data, fetch_imf_growth, fetch_fx_rates
from models import (
    prepare_fab_metadata, build_country_overlay, join_lpi_to_fabs,
    build_fx_overlay, build_capacity_table, build_assembly_load,
    build_forecast_series, build_route_path_rows,
    get_model_pins, discover_agent_plugins, _json_dumps_safe,
)
from agent import run_agent_turn, run_agent_turn_with_plugin
from visuals import render_global_route_map
from risk_monitor import ensure_risk_tables, fetch_risk_snapshot, render_risk_sidebar
from constants import (
    BLS_SERIES, FAB_COUNTRY, COUNTRY_LOOKUP, AGENT_PLUGIN_DIR,
)

# ── Must be first Streamlit call ──────────────────────────────────────────────
st.set_page_config(page_title="Semiconductor Logistics Dashboard", layout="wide")


# ── Data loaders ──────────────────────────────────────────────────────────────

def _load_routing_tables():
    tables = {k: pd.DataFrame() for k in
              ['flow', 'flow_step', 'site_capability', 'site_lane', 'site_operation_capacity']}
    try:
        tables['flow'] = run_query(
            "SELECT flow_id, product_type, version, active, created_at FROM process_flow"
        )
        tables['flow_step'] = run_query(
            "SELECT flow_id, step_order, capability_id, required, max_wait_hours "
            "FROM process_flow_step ORDER BY flow_id, step_order"
        )
        tables['site_capability'] = run_query(
            "SELECT site_id, capability_id, confidence, source, valid_from, valid_to, "
            "routing_eligible, demo_only FROM site_capability"
        )
        tables['site_lane'] = run_query(
            "SELECT from_site, to_site, mode, transit_hours, cost_usd, risk_score, active "
            "FROM site_lane"
        )
        tables['site_operation_capacity'] = run_query(
            "SELECT site_id, capability_id, period_start, capacity_units, utilized_units "
            "FROM site_operation_capacity"
        )
    except Exception as exc:
        st.warning(f"Routing tables unavailable: {exc}")
    return tables


def _load_risk_snapshot(fabs_df):
    from datetime import datetime
    default = {
        'updated_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC'),
        'events': pd.DataFrame(), 'disruptions': pd.DataFrame(),
        'mapping': pd.DataFrame(), 'risk_by_site': {},
    }
    try:
        from db import get_connection
        ensure_risk_tables(get_connection())
        return fetch_risk_snapshot(run_query, fabs_df, FAB_COUNTRY, COUNTRY_LOOKUP)
    except Exception as exc:
        st.warning(f"Risk data unavailable: {exc}")
        return default


def _get_or_create_session() -> int:
    if 'agent_session_id' in st.session_state:
        return int(st.session_state['agent_session_id'])
    from datetime import datetime
    pins = get_model_pins()
    row = execute_write(
        "INSERT INTO agent_session (user_id, ui_context) VALUES (%s, %s::jsonb) RETURNING session_id",
        ('streamlit_user', _json_dumps_safe({'app': 'dashboard',
                                             'ts': datetime.utcnow().isoformat(),
                                             **pins})),
        fetchone=True,
    )
    st.session_state['agent_session_id'] = int(row[0])
    return int(row[0])


# ── Tab renderers ─────────────────────────────────────────────────────────────

def _render_operations(lots, fabs, constraints):
    st.subheader("Production Lots")
    st.dataframe(lots, use_container_width=True)
    st.subheader("Operational Constraints")
    st.dataframe(constraints, use_container_width=True)


def _render_macro(fabs):
    st.subheader("Country Macro Context")
    overlay = build_country_overlay(fabs)
    if overlay.empty:
        st.info("No macro data available.")
        return
    st.dataframe(overlay, use_container_width=True)

    st.subheader("BLS Semiconductor Series")
    for series_id, label in BLS_SERIES.items():
        df = fetch_bls_series(series_id)
        fig = px.line(df, x='date', y='value', title=label)
        st.plotly_chart(fig, use_container_width=True)


def _render_logistics_risk(fabs):
    fabs_lpi, source = join_lpi_to_fabs(fabs)
    st.caption(f"Source: {source} · api.worldbank.org · CC BY 4.0")

    country_df = (
        fabs_lpi[['iso3', 'lpi_overall', 'lpi_customs', 'lpi_infrastructure', 'lpi_timeliness']]
        .dropna().drop_duplicates('iso3').sort_values('lpi_overall', ascending=False).copy()
    )
    if country_df.empty:
        st.info('No LPI data available.')
        return

    country_df['country_name'] = country_df['iso3'].map(
        lambda iso: COUNTRY_LOOKUP.get(iso, {}).get('name', iso)
    )
    best, worst = country_df.iloc[0], country_df.iloc[-1]
    avg = float(country_df['lpi_overall'].mean())

    col1, col2, col3 = st.columns(3)
    col1.metric('Best Logistics', f"{best['country_name']}", f"LPI {best['lpi_overall']:.2f}/5.0")
    col2.metric('Weakest Logistics', f"{worst['country_name']}", f"LPI {worst['lpi_overall']:.2f}/5.0", delta_color='inverse')
    col3.metric('Network Avg LPI', f"{avg:.2f}/5.0")

    melted = country_df.melt(
        id_vars=['iso3', 'country_name'],
        value_vars=['lpi_overall', 'lpi_customs', 'lpi_infrastructure', 'lpi_timeliness'],
        var_name='Indicator', value_name='Score',
    )
    fig = px.bar(melted, x='country_name', y='Score', color='Indicator',
                 barmode='group', title='LPI sub-scores by country')
    fig.update_layout(yaxis_range=[2, 5])
    st.plotly_chart(fig, use_container_width=True)


def _render_fx(fabs):
    fx_df, ts = build_fx_overlay(fabs)
    if fx_df.empty:
        st.info('FX data unavailable.')
        return
    st.caption(f'Rates: {ts}')
    st.dataframe(fx_df, use_container_width=True)
    fig = px.bar(fx_df, x='fab_id', y='shipping_cost_usd', color='risk',
                 title='Monthly shipping cost per fab (USD)')
    st.plotly_chart(fig, use_container_width=True)


def _render_capacity(lots, fabs):
    capacity_df = build_capacity_table(lots, fabs)
    assembly_df = build_assembly_load(lots, fabs)
    forecast_df = build_forecast_series(lots)

    cap_tab, route_tab, forecast_tab, map_tab = st.tabs(
        ["Front-End Capacity", "Assembly Routing", "Demand Forecast", "Global Route Map"]
    )

    with cap_tab:
        if capacity_df.empty:
            st.info('No capacity data.')
        else:
            st.dataframe(capacity_df[['fab_id', 'country_name', 'capacity', 'demand',
                                      'utilization_pct', 'status']], use_container_width=True)
            fig = px.bar(capacity_df, x='fab_id', y='utilization_pct', color='status',
                         title='Front-end utilization (%)')
            st.plotly_chart(fig, use_container_width=True)

    with route_tab:
        if assembly_df.empty:
            st.info('No assembly routing data.')
        else:
            st.dataframe(assembly_df[['assembly_id', 'inbound_wafers', 'capacity',
                                      'utilization_pct']], use_container_width=True)

    with map_tab:
        if fabs.empty:
            st.info('No fab data available to render global route map.')
        else:
            # 1. Create columns for Map vs Inspection Sidebar
            col_map, col_stats = st.columns([3, 1])

            with col_map:
                # Pass 'lots' so the map can render red glow alerts
                render_global_route_map(fabs, lots_df=lots)

            with col_stats:
                st.write("### Site Inspection")
                # Dropdown to select a site seen on the map
                selected_site = st.selectbox("Inspect Fab Details", 
                                             options=sorted(fabs['fab_id'].unique()))
                
                if selected_site:
                    # Filter data for the specific site
                    site_meta = fabs[fabs['fab_id'] == selected_site].iloc[0]
                    site_lots = lots[lots['fab_id'] == selected_site]
                    
                    # Capacity Metric
                    total_cap = site_meta.get('total_wafer_starts_per_month', 0)
                    active_wafers = site_lots['wafers_started'].sum()
                    util_pct = (active_wafers / total_cap) if total_cap > 0 else 0
                    
                    st.metric("Total Monthly Capacity", f"{total_cap:,} wafers")
                    st.write(f"**Utilization:** {util_pct:.1%}")
                    st.progress(min(util_pct, 1.0))
                    
                    # Quick Status Alerts
                    if util_pct > 0.9:
                        st.error("⚠️ Capacity Saturated")
                    
                    low_yield_count = len(site_lots[site_lots['yield_pct'] < 85])
                    if low_yield_count > 0:
                        st.warning(f"⚠️ {low_yield_count} lots with Low Yield")

    with forecast_tab:
        if forecast_df.empty:
            st.info('Not enough history for a forecast.')
        else:
            fig = px.line(forecast_df, x='month', y='wafers_started', color='type',
                          title='Wafer demand forecast (next 3 months)')
            st.plotly_chart(fig, use_container_width=True)


def _render_route_scorecard(paths):
    rows = build_route_path_rows(paths)
    if not rows:
        return

    df = pd.DataFrame(rows)
    max_time = float(df['time_hours'].max()) or 1.0
    max_cost = float(df['cost_usd'].max()) or 1.0
    max_risk = float(df['avg_risk'].max()) or 1.0

    st.markdown("**Route Scorecard** — lower score = better route")

    cols = st.columns(min(3, len(df)))
    for i, col in enumerate(cols):
        row = df.iloc[i]
        is_best = i == 0
        with col:
            if is_best:
                st.success(f"Recommended → **{row['final_site']}**")
            else:
                st.info(f"Path #{int(row['rank'])} → **{row['final_site']}**")

            st.metric("Score", f"{row['score']:.3f}",
                      delta="lowest" if is_best else None,
                      delta_color="off" if is_best else "off")

            time_pct  = int((row['time_hours'] / max_time) * 100)
            cost_pct  = int((row['cost_usd']   / max_cost) * 100)
            risk_pct  = int((row['avg_risk']   / max_risk) * 100)

            st.caption("Time")
            st.progress(time_pct / 100)
            st.caption(f"{row['time_hours']:.1f} h")

            st.caption("Cost")
            st.progress(cost_pct / 100)
            st.caption(f"${row['cost_usd']:,.0f}")

            st.caption("Risk")
            st.progress(risk_pct / 100)
            st.caption(f"{row['avg_risk']:.2f}")


def _run_and_display_agent(agent_id, registry, lot_id, lots, constraints,
                            routing_tables, risk_snapshot):
    session_id = _get_or_create_session()
    with st.spinner("Routing agent thinking..."):
        answer, recommendation, meta, decision_id = run_agent_turn_with_plugin(
            plugin_registry=registry,
            selected_agent_id=agent_id,
            session_id=session_id,
            prompt=f"Propose best route for lot {lot_id}.",
            lot_id=lot_id,
            flow_id=None,
            lots_df=lots,
            constraints_df=constraints,
            routing_tables=routing_tables,
            risk_by_site=risk_snapshot.get('risk_by_site', {}),
        )
    st.markdown(answer)
    st.json(recommendation)
    paths = recommendation.get('paths', [])
    if paths:
        _render_route_scorecard(paths)


def _render_agent(lots, fabs, constraints, routing_tables, risk_snapshot, process_stage_df):
    st.subheader("Routing Agent")
    plugin_registry = discover_agent_plugins(AGENT_PLUGIN_DIR)
    agent_options   = list(plugin_registry.keys())
    if not agent_options:
        st.warning("No agent plugins found. Add an agent_*.py file to the agents/ folder.")
        return

    selected_agent = st.selectbox("Agent profile", options=agent_options)
    lot_ids        = sorted(lots['lot_id'].astype(str).unique())
    selected_lot   = st.selectbox("Lot", options=lot_ids)

    if st.button("Propose routing decision"):
        _run_and_display_agent(
            selected_agent, plugin_registry, selected_lot,
            lots, constraints, routing_tables, risk_snapshot,
        )


# ── Entry point — all defs above, execution here ─────────────────────────────

def main():
    st.title("Semiconductor Logistics Dashboard")
    st.markdown("Real-time fabs + macro overlays (BLS + World Bank + IMF).")

    lots_df        = run_query("SELECT * FROM production_lot")
    lots_df['start_date'] = pd.to_datetime(lots_df['start_date'])
    fabs_df        = prepare_fab_metadata(
        run_query("SELECT fab_id, name, location, site_type, total_wafer_starts_per_month FROM fab")
    )
    constraints_df = run_query("SELECT * FROM operational_constraint")

    try:
        process_stage_df = run_query(
            "SELECT stage_id, stage_name, description, site_type, typical_location "
            "FROM process_flow_stage ORDER BY stage_id"
        )
    except Exception:
        process_stage_df = pd.DataFrame()

    routing_tables = _load_routing_tables()
    risk_snapshot  = _load_risk_snapshot(fabs_df)

    render_risk_sidebar(st, risk_snapshot)
    available_tech = sorted(lots_df['tech_id'].unique())
    available_fabs = sorted(fabs_df[fabs_df['site_type'] == 'Front-End']['fab_id'].unique())
    tech_filter    = st.sidebar.multiselect("Technology Node", options=available_tech)
    fab_filter     = st.sidebar.multiselect("Fab", options=available_fabs)

    filtered_lots = lots_df.copy()
    if tech_filter:
        filtered_lots = filtered_lots[filtered_lots['tech_id'].isin(tech_filter)]
    if fab_filter:
        filtered_lots = filtered_lots[filtered_lots['fab_id'].isin(fab_filter)]

    tabs = st.tabs(["Operations", "Macro Overlay", "Logistics Risk", "FX Exposure",
                    "Capacity & Routing", "Agent"])

    with tabs[0]:
        _render_operations(filtered_lots, fabs_df, constraints_df)
    with tabs[1]:
        _render_macro(fabs_df)
    with tabs[2]:
        _render_logistics_risk(fabs_df)
    with tabs[3]:
        _render_fx(fabs_df)
    with tabs[4]:
        _render_capacity(filtered_lots, fabs_df)
    with tabs[5]:
        _render_agent(filtered_lots, fabs_df, constraints_df, routing_tables,
                      risk_snapshot, process_stage_df)


if __name__ == "__main__" or st._is_running_with_streamlit:
    main()
