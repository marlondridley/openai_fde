"""
Semiconductor logistics dashboard with three integrated layers:
1) Operations visibility (DB-backed fabs/lots/constraints)
2) External context overlays (macro, logistics, FX)
3) Runtime AI decision surface (agent + telemetry + review workflow)

This file intentionally keeps logic in one place for demo portability.
In production, split into modules: data, tools, agent, ui, telemetry.
"""

import os
import json
import time
from pathlib import Path
from datetime import datetime
from math import radians, sin, cos, sqrt, atan2
from typing import Dict, List, Any, Optional

import folium
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import psycopg2
import requests
import streamlit as st
from streamlit_folium import st_folium

os.environ['STREAMLIT_TELEMETRY_DISABLED'] = 'true'

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

ROOT = Path(__file__).resolve().parent
if load_dotenv:
    env_path = ROOT / '.env'
    if env_path.exists():
        load_dotenv(env_path)

DB_CONFIG = {
    'host': os.getenv('PGHOST', 'localhost'),
    'port': int(os.getenv('PGPORT', '5432')),
    'database': os.getenv('PGDATABASE', 'semiconductor'),
    'user': os.getenv('PGUSER', ''),
    'password': os.getenv('PGPASSWORD', ''),
}

BLS_SERIES = {
    'IPG3344S': 'Industrial Production: Semiconductors',
    'CES3133440001': 'Employment in Semiconductor Mfg (thousands)'
}

WORLD_BANK_INDICATORS = {
    'LP.LPI.OVRL.XQ': 'Logistics Performance Index (overall)',
    'NV.IND.MANF.ZS': 'Manufacturing value added (% of GDP)',
    'NY.GDP.MKTP.KD.ZG': 'GDP growth (annual %)',
}

WORLD_BANK_FALLBACK = {
    'LP.LPI.OVRL.XQ': {'US': 3.99, 'IE': 3.74, 'IL': 3.44, 'MY': 3.22, 'VN': 3.27, 'CR': 2.87, 'CN': 3.65},
    'NV.IND.MANF.ZS': {'US': 10.8, 'IE': 32.5, 'IL': 11.7, 'MY': 23.4, 'VN': 24.1, 'CR': 13.6, 'CN': 27.4},
    'NY.GDP.MKTP.KD.ZG': {'US': 2.3, 'IE': 1.8, 'IL': 2.0, 'MY': 4.1, 'VN': 5.6, 'CR': 3.8, 'CN': 4.8},
}

IMF_INDICATOR = 'NGDP_RPCH'  # Real GDP growth projections
IMF_GROWTH_FALLBACK = {'USA': 2.1, 'IRL': 1.9, 'ISR': 2.0, 'MYS': 4.0, 'VNM': 5.5, 'CRI': 3.7, 'CHN': 4.6}

LPI_INDICATORS = {
    'LP.LPI.OVRL.XQ': 'lpi_overall',
    'LP.LPI.CUST.XQ': 'lpi_customs',
    'LP.LPI.INFR.XQ': 'lpi_infrastructure',
    'LP.LPI.TIME.XQ': 'lpi_timeliness',
    'LP.LPI.TRAC.XQ': 'lpi_tracking',
    'LP.LPI.ISAL.XQ': 'lpi_intl_shipments',
}

LPI_2023_FALLBACK = {
    'USA': {'lpi_overall': 3.99, 'lpi_customs': 3.74, 'lpi_infrastructure': 4.14, 'lpi_timeliness': 4.38, 'lpi_tracking': 4.17, 'lpi_intl_shipments': 3.79},
    'IRL': {'lpi_overall': 3.74, 'lpi_customs': 3.54, 'lpi_infrastructure': 3.69, 'lpi_timeliness': 4.09, 'lpi_tracking': 3.79, 'lpi_intl_shipments': 3.72},
    'ISR': {'lpi_overall': 3.44, 'lpi_customs': 3.22, 'lpi_infrastructure': 3.45, 'lpi_timeliness': 3.80, 'lpi_tracking': 3.46, 'lpi_intl_shipments': 3.34},
    'MYS': {'lpi_overall': 3.22, 'lpi_customs': 3.03, 'lpi_infrastructure': 3.27, 'lpi_timeliness': 3.61, 'lpi_tracking': 3.22, 'lpi_intl_shipments': 3.10},
    'VNM': {'lpi_overall': 3.27, 'lpi_customs': 3.12, 'lpi_infrastructure': 3.11, 'lpi_timeliness': 3.73, 'lpi_tracking': 3.27, 'lpi_intl_shipments': 3.23},
    'CRI': {'lpi_overall': 2.87, 'lpi_customs': 2.62, 'lpi_infrastructure': 2.75, 'lpi_timeliness': 3.32, 'lpi_tracking': 2.88, 'lpi_intl_shipments': 2.86},
    'CHN': {'lpi_overall': 3.65, 'lpi_customs': 3.44, 'lpi_infrastructure': 3.82, 'lpi_timeliness': 4.04, 'lpi_tracking': 3.68, 'lpi_intl_shipments': 3.46},
}

FAB_COUNTRY = {
    'AZ_F12': 'USA', 'AZ_F22': 'USA', 'AZ_F32': 'USA',
    'IE_F24': 'IRL', 'IL_F28': 'ISR', 'IL_F28a': 'ISR', 'IL_F38': 'ISR',
    'OR_D1x': 'USA', 'CN_SH': 'CHN', 'CN_CD': 'CHN', 'CR_SJ': 'CRI',
    'MY_KUL': 'MYS', 'MY_PG': 'MYS', 'VN_HCM': 'VNM', 'US_AT': 'USA'
}

FAB_TO_ASM = {
    'AZ_F12': ['CR_SJ', 'MY_KUL', 'MY_PG'],
    'AZ_F22': ['CR_SJ', 'MY_KUL', 'MY_PG'],
    'AZ_F32': ['CR_SJ', 'MY_KUL', 'MY_PG'],
    'IE_F24': ['US_AT', 'CN_SH', 'MY_PG'],
    'IL_F28': ['VN_HCM', 'CN_CD'],
    'IL_F28a': ['VN_HCM', 'CN_CD'],
    'IL_F38': ['VN_HCM', 'CN_CD'],
    'OR_D1x': ['US_AT', 'MY_KUL'],
}

LOCATION_TO_ISO3 = {
    'Chandler, Arizona, USA': 'USA',
    'Leixlip, Ireland': 'IRL',
    'Kiryat Gat, Israel': 'ISR',
    'Hillsboro, Oregon, USA': 'USA',
    'Shanghai, China': 'CHN',
    'Chengdu, China': 'CHN',
    'San Jose, Costa Rica': 'CRI',
    'Kulim, Malaysia': 'MYS',
    'Penang, Malaysia': 'MYS',
    'Ho Chi Minh City, Vietnam': 'VNM',
    'US Assembly/Test': 'USA',
}

CURRENCY_MAP = {
    'USA': 'USD',
    'IRL': 'EUR',
    'ISR': 'ILS',
    'CHN': 'CNY',
    'CRI': 'CRC',
    'MYS': 'MYR',
    'VNM': 'VND',
}

COUNTRY_LOOKUP: Dict[str, Dict[str, str]] = {
    'USA': {'name': 'United States', 'iso2': 'US'},
    'IRL': {'name': 'Ireland', 'iso2': 'IE'},
    'ISR': {'name': 'Israel', 'iso2': 'IL'},
    'CHN': {'name': 'China', 'iso2': 'CN'},
    'CRI': {'name': 'Costa Rica', 'iso2': 'CR'},
    'MYS': {'name': 'Malaysia', 'iso2': 'MY'},
    'VNM': {'name': 'Vietnam', 'iso2': 'VN'},
}

LOCATION_COORDS = {
    'AZ_F12': (33.3, -111.8), 'AZ_F22': (33.3, -111.8), 'AZ_F32': (33.3, -111.8),
    'IE_F24': (53.3, -6.5), 'IL_F28': (31.6, 34.8), 'IL_F28a': (31.6, 34.8),
    'IL_F38': (31.6, 34.8), 'OR_D1x': (45.5, -122.9),
    'CN_SH': (31.2, 121.5), 'CN_CD': (30.6, 104.1), 'CR_SJ': (9.9, -84.1),
    'MY_KUL': (5.1, 100.4), 'MY_PG': (5.4, 100.3), 'VN_HCM': (10.8, 106.7),
    'US_AT': (35.1, -106.6)
}


# Shared Postgres connection for all data reads/writes used by the dashboard.
# Streamlit caches this resource so every interaction does not create a new socket.
@st.cache_resource
def get_connection():
    missing = [k for k, v in DB_CONFIG.items() if (k != 'password' and not v)]
    if missing:
        raise RuntimeError(f"Database environment not configured: missing {', '.join(missing)}")
    return psycopg2.connect(**DB_CONFIG)


# Thin helper used by the UI to execute read-only SQL and return pandas DataFrames.
def run_query(query: str) -> pd.DataFrame:
    return pd.read_sql(query, get_connection())


# Pull BLS macro time series for semiconductor context.
# If API fails, return deterministic fallback data so charts stay available in demos.
@st.cache_data(ttl=86400)
def fetch_bls_series(series_id: str, start_year: int = 2018) -> pd.DataFrame:
    def build_fallback() -> pd.DataFrame:
        end_month = pd.Timestamp.utcnow().to_period('M').to_timestamp()
        start_month = max(pd.Timestamp(start_year, 1, 1), end_month - pd.DateOffset(months=23))
        dates = pd.date_range(start=start_month, end=end_month, freq='MS')
        if series_id == 'IPG3344S':
            base, step = 105.0, 0.55
        else:
            base, step = 355.0, 0.35
        rows = [{'date': d.to_pydatetime(), 'value': round(base + i * step, 2)} for i, d in enumerate(dates)]
        df = pd.DataFrame(rows)
        df.attrs['source'] = 'BLS fallback (seeded)'
        return df

    url = f"https://api.bls.gov/publicAPI/v1/timeseries/data/{series_id}"
    params = {'startyear': start_year, 'endyear': datetime.utcnow().year}
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        return build_fallback()

    series = payload.get('Results', {}).get('series', [])
    if not series:
        return build_fallback()

    rows = []
    for item in series[0].get('data', []):
        period = item.get('period', '')
        if not period.startswith('M'):
            continue
        month = int(period[1:])
        year = int(item.get('year', datetime.utcnow().year))
        value = item.get('value')
        if value is None:
            continue
        rows.append({'date': datetime(year, month, 1), 'value': float(value)})

    if not rows:
        return build_fallback()

    df = pd.DataFrame(rows).sort_values('date')
    df.attrs['source'] = 'BLS API (live)'
    return df

# Pull selected World Bank indicators by country code for host-country risk context.
# Falls back to curated seeded values when API is unavailable.
@st.cache_data(ttl=86400)
def fetch_worldbank_indicator(indicator: str, iso2_codes: List[str]) -> pd.DataFrame:
    def build_fallback() -> pd.DataFrame:
        values = WORLD_BANK_FALLBACK.get(indicator, {})
        year = str(datetime.utcnow().year)
        rows = []
        for iso2 in iso2_codes:
            if iso2 not in values:
                continue
            rows.append({
                'country': iso2,
                'country_name': iso2,
                'date': year,
                'value': float(values[iso2]),
                'indicator': indicator,
            })
        df = pd.DataFrame(rows)
        df.attrs['source'] = 'World Bank fallback (seeded)'
        return df

    if not iso2_codes:
        return pd.DataFrame()

    joined = ';'.join(iso2_codes)
    url = f"https://api.worldbank.org/v2/country/{joined}/indicator/{indicator}"
    params = {'format': 'json', 'per_page': 200}
    rows = []
    page = 1
    while True:
        params['page'] = page
        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
        except Exception:
            break

        if len(payload) < 2 or not payload[1]:
            break

        for entry in payload[1]:
            value = entry.get('value')
            if value is None:
                continue
            rows.append({
                'country': entry.get('country', {}).get('id'),
                'country_name': entry.get('country', {}).get('value'),
                'date': entry.get('date'),
                'value': float(value),
                'indicator': indicator,
            })

        if page >= payload[0].get('pages', 1):
            break
        page += 1

    if not rows:
        return build_fallback()

    df = pd.DataFrame(rows)
    df.attrs['source'] = 'World Bank API (live)'
    return df

# Fetch Logistics Performance Index subcomponents used by the logistics risk tab.
# Returns both data and source label for transparency in the UI.
@st.cache_data(ttl=86400)
def fetch_lpi_data() -> tuple[pd.DataFrame, str]:
    iso_codes = ';'.join(sorted(set(LOCATION_TO_ISO3.values())))
    indicators = ';'.join(LPI_INDICATORS.keys())
    url = (
        f"https://api.worldbank.org/v2/country/{iso_codes}/indicator/{indicators}"
        f"?format=json&per_page=200&mrv=1"
    )
    try:
        resp = requests.get(url, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        if not data or len(data) < 2 or not data[1]:
            raise ValueError('Empty response')
        rows = {}
        for item in data[1]:
            value = item.get('value')
            if value is None:
                continue
            iso = item.get('countryiso3code')
            indicator_id = item.get('indicator', {}).get('id')
            col = LPI_INDICATORS.get(indicator_id)
            if iso and col:
                row = rows.setdefault(iso, {'iso3': iso})
                row[col] = round(float(value), 3)
        df = pd.DataFrame(rows.values())
        source = 'World Bank API (live)'
    except Exception:
        df = pd.DataFrame([{'iso3': k, **v} for k, v in LPI_2023_FALLBACK.items()])
        source = 'World Bank LPI 2023 (cached)'
    return df, source


# Pull IMF growth projections used in the macro overlay table.
# Supports mixed live/fallback behavior by country when partial API failures occur.
@st.cache_data(ttl=86400)
def fetch_imf_growth(iso3_codes: List[str]) -> pd.DataFrame:
    rows = []
    source = 'IMF API (live)'
    for code in iso3_codes:
        url = f"https://www.imf.org/external/datamapper/api/v1/WEO/{IMF_INDICATOR}/{code}"
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
            data = payload.get('WEO', {}).get(IMF_INDICATOR, {}).get(code)
        except Exception:
            data = None

        if data:
            valid = [(int(year), float(val)) for year, val in data.items() if val is not None]
            if valid:
                valid.sort()
                year, value = valid[-1]
                rows.append({'country': code, 'year': year, 'value': value})
                continue

        fallback_val = IMF_GROWTH_FALLBACK.get(code)
        if fallback_val is not None:
            source = 'IMF mixed/fallback'
            rows.append({'country': code, 'year': datetime.utcnow().year, 'value': float(fallback_val)})

    df = pd.DataFrame(rows)
    df.attrs['source'] = source
    return df

# Enrich raw fab rows with map coordinates and country metadata used across visuals.
def prepare_fab_metadata(df_fabs: pd.DataFrame) -> pd.DataFrame:
    df = df_fabs.copy()
    df['lat'] = df['fab_id'].map(lambda x: LOCATION_COORDS.get(x, (0, 0))[0])
    df['lon'] = df['fab_id'].map(lambda x: LOCATION_COORDS.get(x, (0, 0))[1])
    df['country_code'] = df['fab_id'].map(FAB_COUNTRY)
    df['country_name'] = df['country_code'].map(lambda c: COUNTRY_LOOKUP.get(c, {}).get('name', 'Unknown'))
    df['iso2'] = df['country_code'].map(lambda c: COUNTRY_LOOKUP.get(c, {}).get('iso2'))
    return df


# Build country-level macro context aligned to current fab footprint.
def build_country_overlay(df_fabs: pd.DataFrame) -> pd.DataFrame:
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
        indicator_frames[code] = latest[['country', 'value']].set_index('country').rename(columns={'value': label})

    imf = fetch_imf_growth(iso3)
    if not imf.empty:
        imf = imf.set_index('country').rename(columns={'value': 'IMF GDP growth forecast'})

    combined = pd.DataFrame(index=iso2)
    for code, frame in indicator_frames.items():
        combined = combined.join(frame, how='left')
    if not imf.empty:
        imf.index = [COUNTRY_LOOKUP.get(code, {}).get('iso2') for code in imf.index]
        combined = combined.join(imf['IMF GDP growth forecast'], how='left')

    combined['Country'] = combined.index.map(lambda iso: next((info['name'] for info in COUNTRY_LOOKUP.values() if info['iso2'] == iso), iso))
    combined['Front-end fabs'] = combined.index.map(lambda iso: (df_fabs[(df_fabs['iso2'] == iso) & (df_fabs['site_type'] == 'Front-End')]).shape[0])
    combined['Assembly sites'] = combined.index.map(lambda iso: (df_fabs[(df_fabs['iso2'] == iso) & (df_fabs['site_type'] != 'Front-End')]).shape[0])
    combined = combined.reset_index(drop=True)
    return combined


# Attach LPI risk fields to each fab, backfilling missing values from cached fallbacks.
def join_lpi_to_fabs(fabs_df: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    lpi_df, source = fetch_lpi_data()
    fabs_df = fabs_df.copy()
    fabs_df['iso3'] = fabs_df['location'].map(LOCATION_TO_ISO3)
    fabs_df['iso3'] = fabs_df['iso3'].fillna(fabs_df['fab_id'].map(FAB_COUNTRY))
    result = fabs_df.merge(lpi_df, on='iso3', how='left')

    # Fill missing metrics from cached fallback values to keep charts populated.
    for metric in LPI_INDICATORS.values():
        if metric not in result.columns:
            result[metric] = np.nan
        for iso3, values in LPI_2023_FALLBACK.items():
            mask = result['iso3'] == iso3
            result.loc[mask, metric] = result.loc[mask, metric].fillna(values.get(metric))

    return result, source

FX_API_URL = 'https://open.er-api.com/v6/latest/USD'
BASE_SHIPPING_COST_PER_WAFER_USD = 120
VOLATILE_CURRENCIES = {'CRC', 'VND'}
FX_FALLBACK_RATES = {'USD': 1.0, 'EUR': 0.92, 'ILS': 3.65, 'CNY': 7.18, 'CRC': 520.0, 'MYR': 4.72, 'VND': 25000.0}


# Render logistics risk cards and charts (country ranking + infra vs capacity scatter).
def render_lpi_section(fabs_df: pd.DataFrame) -> None:
    fabs_lpi, source = join_lpi_to_fabs(fabs_df)
    st.caption(f"Data source: {source} · api.worldbank.org · CC BY 4.0")

    country_cols = ['iso3', 'lpi_overall', 'lpi_customs', 'lpi_infrastructure', 'lpi_timeliness']
    country_df = (
        fabs_lpi[country_cols]
        .dropna()
        .drop_duplicates('iso3')
        .sort_values('lpi_overall', ascending=False)
    )
    if country_df.empty:
        st.info('No LPI data available.')
        return

    col1, col2, col3 = st.columns(3)
    best = country_df.iloc[0]
    worst = country_df.iloc[-1]
    avg = country_df['lpi_overall'].mean()
    with col1:
        st.metric('Best Logistics Country', best['iso3'], f"LPI {best['lpi_overall']:.2f}/5.0")
    with col2:
        st.metric('Weakest Logistics Country', worst['iso3'], f"LPI {worst['lpi_overall']:.2f}/5.0", delta_color='inverse')
    with col3:
        st.metric('Network Avg LPI', f"{avg:.2f}/5.0")

    melted = country_df.melt(
        id_vars='iso3',
        value_vars=['lpi_overall', 'lpi_customs', 'lpi_infrastructure', 'lpi_timeliness'],
        var_name='Indicator',
        value_name='Score',
    )
    color_map = {
        'lpi_overall': '#00b4d8',
        'lpi_customs': '#f77f00',
        'lpi_infrastructure': '#06d6a0',
        'lpi_timeliness': '#ef476f',
    }
    fig = px.bar(
        melted,
        x='iso3',
        y='Score',
        color='Indicator',
        barmode='group',
        title='LPI sub-scores by host country',
        color_discrete_map=color_map,
    )
    fig.update_layout(yaxis_range=[2, 5])
    st.plotly_chart(fig, use_container_width=True)

    fe_df = fabs_lpi[
        (fabs_lpi['site_type'] == 'Front-End')
        & fabs_lpi['lpi_infrastructure'].notna()
        & fabs_lpi['total_wafer_starts_per_month'].notna()
    ]
    if not fe_df.empty:
        fig2 = px.scatter(
            fe_df,
            x='lpi_infrastructure',
            y='total_wafer_starts_per_month',
            text='fab_id',
            color='iso3',
            title='Infrastructure LPI vs fab capacity (wafers/month)',
            labels={'lpi_infrastructure': 'Infrastructure LPI', 'total_wafer_starts_per_month': 'Wafers/month'},
        )
        fig2.update_traces(textposition='top center')
        st.plotly_chart(fig2, use_container_width=True)


# Pull FX rates for shipping exposure analysis. Uses fallback rates if feed is down.
@st.cache_data(ttl=3600)
def fetch_fx_rates() -> tuple[Dict[str, float], str]:
    try:
        resp = requests.get(FX_API_URL, timeout=8)
        resp.raise_for_status()
        payload = resp.json()
        rates = payload.get('rates', {})
        timestamp = payload.get('time_last_update_utc', '')
        if not rates:
            raise ValueError('FX rates missing in response')
        return rates, timestamp or 'FX feed timestamp unavailable'
    except Exception:
        return FX_FALLBACK_RATES, 'FX fallback (seeded rates)'

# Compute per-fab shipping exposure in USD and local currency with risk labels.
@st.cache_data(ttl=3600)
def build_fx_overlay(fabs_df: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    rates, ts = fetch_fx_rates()
    rows = []
    for _, row in fabs_df.iterrows():
        iso3 = row.get('country_code') or row.get('iso3')
        currency = CURRENCY_MAP.get(iso3, 'USD')
        rate = rates.get(currency, 1.0) or 1.0
        wafers = row.get('total_wafer_starts_per_month') or 0
        shipping_usd = wafers * BASE_SHIPPING_COST_PER_WAFER_USD
        shipping_local = shipping_usd * rate
        cost_per_wafer_local = shipping_local / wafers if wafers else 0
        risk = 'High' if currency in VOLATILE_CURRENCIES else ('Low' if currency == 'USD' else 'Medium')
        rows.append({
            'fab_id': row['fab_id'],
            'country': iso3,
            'currency': currency,
            'fx_rate': rate,
            'wafers_per_month': wafers,
            'shipping_cost_usd': shipping_usd,
            'shipping_cost_local': shipping_local,
            'cost_per_wafer_local': cost_per_wafer_local,
            'risk': risk,
        })
    return pd.DataFrame(rows), ts


# Render FX exposure table + chart used by operations/finance stakeholders.
def render_fx_section(fabs_df: pd.DataFrame) -> None:
    fx_df, ts = build_fx_overlay(fabs_df)
    if fx_df.empty:
        st.info('FX data unavailable.')
        return
    st.caption(f'Rates source: {ts} · open.er-api.com/v6/latest/USD')
    display_cols = ['fab_id', 'country', 'currency', 'fx_rate', 'wafers_per_month', 'shipping_cost_usd', 'shipping_cost_local', 'risk']
    st.dataframe(fx_df[display_cols], use_container_width=True)
    fig = px.bar(
        fx_df,
        x='fab_id',
        y='shipping_cost_usd',
        color='risk',
        title='Monthly shipping cost per fab (USD baseline)',
        labels={'shipping_cost_usd': 'USD/month'},
    )
    st.plotly_chart(fig, use_container_width=True)



# Derive synthetic lot/wafers flow from front-end fabs to assembly targets.
def build_flow_distribution(filtered_lots: pd.DataFrame) -> pd.DataFrame:
    lot_flow = (
        filtered_lots.groupby('fab_id').agg({'lot_id': 'count', 'wafers_started': 'sum'}).reset_index()
    )
    rows = []
    for _, row in lot_flow.iterrows():
        fab = row['fab_id']
        lot_count = row['lot_id']
        wafers = row['wafers_started']
        targets = FAB_TO_ASM.get(fab) or []
        if not targets:
            continue
        per_target = lot_count / len(targets)
        wafers_per_target = wafers / len(targets) if wafers else 0
        for target in targets:
            rows.append({'source': fab, 'target': target, 'lots': per_target, 'wafers': wafers_per_target})
    return pd.DataFrame(rows)


# Compute front-end utilization against declared monthly wafer-start capacity.
def build_capacity_table(filtered_lots: pd.DataFrame, fabs_df: pd.DataFrame) -> pd.DataFrame:
    front_end = (
        fabs_df[fabs_df['site_type'] == 'Front-End'][['fab_id', 'country_name', 'total_wafer_starts_per_month']]
        .copy()
        .rename(columns={'total_wafer_starts_per_month': 'capacity'})
    )
    demand = filtered_lots.groupby('fab_id')['wafers_started'].sum()
    front_end['demand'] = front_end['fab_id'].map(demand).fillna(0)
    front_end['utilization'] = front_end.apply(
        lambda row: (row['demand'] / row['capacity']) if row['capacity'] else 0,
        axis=1,
    )
    front_end['slack'] = front_end['capacity'] - front_end['demand']

    def status(util: float) -> str:
        if util >= 1.0:
            return 'Over capacity'
        if util >= 0.8:
            return 'Tight'
        return 'Healthy'

    front_end['status'] = front_end['utilization'].apply(status)
    front_end['utilization_pct'] = (front_end['utilization'] * 100).round(1)
    return front_end.sort_values('utilization', ascending=False)


# Compute assembly-site inbound load and utilization based on routing map + lot volume.
def build_assembly_load(filtered_lots: pd.DataFrame, fabs_df: pd.DataFrame) -> pd.DataFrame:
    flow = build_flow_distribution(filtered_lots)
    if flow.empty:
        return pd.DataFrame()
    assembly_load = flow.groupby('target')['wafers'].sum().reset_index().rename(
        columns={'target': 'assembly_id', 'wafers': 'inbound_wafers'}
    )
    assembly_capacity = (
        fabs_df[fabs_df['site_type'] != 'Front-End'][['fab_id', 'total_wafer_starts_per_month']]
        .rename(columns={'fab_id': 'assembly_id', 'total_wafer_starts_per_month': 'capacity'})
    )
    result = assembly_load.merge(assembly_capacity, on='assembly_id', how='left')
    result['capacity'] = result['capacity'].fillna(0)
    result['utilization'] = result.apply(
        lambda row: (row['inbound_wafers'] / row['capacity']) if row['capacity'] else 0,
        axis=1,
    )
    result['utilization_pct'] = (result['utilization'] * 100).round(1)
    return result.sort_values('utilization', ascending=False)


# Build a simple near-term demand forecast from recent monthly wafer-start history.
def build_forecast_series(filtered_lots: pd.DataFrame) -> pd.DataFrame:
    if filtered_lots.empty:
        return pd.DataFrame()
    monthly = filtered_lots.copy()
    monthly['month'] = monthly['start_date'].dt.to_period('M').dt.to_timestamp()
    monthly = monthly.groupby('month')['wafers_started'].sum().reset_index().sort_values('month')
    if monthly.empty:
        return pd.DataFrame()
    history = monthly.copy()
    history['type'] = 'Actual'
    last_value = history['wafers_started'].iloc[-1]
    if len(history) >= 2 and history['wafers_started'].iloc[-2] > 0:
        growth = (last_value - history['wafers_started'].iloc[-2]) / history['wafers_started'].iloc[-2]
    else:
        growth = 0.05
    growth = max(min(growth, 0.25), -0.1)
    future_rows = []
    current = last_value
    last_month = history['month'].max()
    for i in range(1, 4):
        current = max(current * (1 + growth), 0)
        future_month = last_month + pd.DateOffset(months=i)
        future_rows.append({'month': future_month, 'wafers_started': current, 'type': 'Forecast'})
    forecast_df = pd.DataFrame(future_rows)
    return pd.concat([history, forecast_df], ignore_index=True)




# Runtime/governance model controls.
# These pins make model usage auditable and prevent accidental silent model drift.
def get_model_pins() -> Dict[str, Any]:
    runtime_model = os.getenv('AGENT_RUNTIME_MODEL', 'gpt-4o-mini')
    governance_model = os.getenv('GOVERNANCE_EVAL_MODEL', 'gpt-4o-mini')
    approved = [m.strip() for m in os.getenv('AGENT_APPROVED_MODELS', 'gpt-4o-mini,gpt-4o').split(',') if m.strip()]
    return {
        'runtime_model': runtime_model,
        'governance_model': governance_model,
        'approved_models': approved,
        'runtime_approved': runtime_model in approved,
        'governance_approved': governance_model in approved,
    }


# Create telemetry and workflow tables for runtime AI decisions.
# This keeps decision generation, review, and outcomes fully auditable in Postgres.
def ensure_agent_tables() -> None:
    conn = get_connection()
    ddl = [
        """
        CREATE TABLE IF NOT EXISTS agent_session (
            session_id BIGSERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            user_id TEXT,
            ui_context JSONB
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS agent_message (
            message_id BIGSERIAL PRIMARY KEY,
            session_id BIGINT REFERENCES agent_session(session_id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS agent_tool_call (
            tool_call_id BIGSERIAL PRIMARY KEY,
            session_id BIGINT REFERENCES agent_session(session_id) ON DELETE CASCADE,
            tool_name TEXT NOT NULL,
            args_json JSONB,
            result_json JSONB,
            latency_ms INTEGER,
            success BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS agent_decision (
            decision_id BIGSERIAL PRIMARY KEY,
            session_id BIGINT REFERENCES agent_session(session_id) ON DELETE CASCADE,
            decision_type TEXT NOT NULL,
            recommendation_json JSONB,
            confidence NUMERIC,
            model_name TEXT,
            governance_model TEXT,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            latency_ms INTEGER,
            decision_status TEXT NOT NULL DEFAULT 'pending',
            notes TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS agent_decision_review (
            review_id BIGSERIAL PRIMARY KEY,
            decision_id BIGINT REFERENCES agent_decision(decision_id) ON DELETE CASCADE,
            reviewer TEXT NOT NULL,
            action TEXT NOT NULL,
            note TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS agent_decision_outcome (
            outcome_id BIGSERIAL PRIMARY KEY,
            decision_id BIGINT REFERENCES agent_decision(decision_id) ON DELETE CASCADE,
            outcome_type TEXT NOT NULL,
            outcome_status TEXT NOT NULL,
            outcome_json JSONB,
            observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS agent_model_registry (
            id SMALLINT PRIMARY KEY,
            runtime_model TEXT NOT NULL,
            governance_model TEXT NOT NULL,
            approved_models TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_agent_message_session_created ON agent_message(session_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_agent_tool_call_session_created ON agent_tool_call(session_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_agent_decision_created ON agent_decision(created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_agent_review_decision_created ON agent_decision_review(decision_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_agent_outcome_decision_observed ON agent_decision_outcome(decision_id, observed_at DESC)",
    ]
    try:
        with conn.cursor() as cur:
            for stmt in ddl:
                cur.execute(stmt)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# Shared write helper with explicit transaction handling for logging/review actions.
def execute_write(sql: str, params: Optional[tuple] = None, fetchone: bool = False):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            row = cur.fetchone() if fetchone else None
        conn.commit()
        return row
    except Exception:
        conn.rollback()
        raise


# Persist current model pin config so operators can verify active runtime/governance models.
def upsert_model_registry() -> None:
    pins = get_model_pins()
    execute_write(
        """
        INSERT INTO agent_model_registry (id, runtime_model, governance_model, approved_models)
        VALUES (1, %s, %s, %s)
        ON CONFLICT (id)
        DO UPDATE SET
            runtime_model = EXCLUDED.runtime_model,
            governance_model = EXCLUDED.governance_model,
            approved_models = EXCLUDED.approved_models,
            updated_at = NOW()
        """,
        (
            pins['runtime_model'],
            pins['governance_model'],
            ','.join(pins['approved_models']),
        ),
    )


# One session row per Streamlit browser session for grouped telemetry and chat history.
def get_or_create_agent_session() -> int:
    if 'agent_session_id' in st.session_state:
        return int(st.session_state['agent_session_id'])

    pins = get_model_pins()
    ctx = {
        'app': 'dashboard',
        'ts': datetime.utcnow().isoformat(),
        'runtime_model': pins['runtime_model'],
        'governance_model': pins['governance_model'],
    }
    row = execute_write(
        """
        INSERT INTO agent_session (user_id, ui_context)
        VALUES (%s, %s::jsonb)
        RETURNING session_id
        """,
        ('streamlit_user', json.dumps(ctx)),
        fetchone=True,
    )
    session_id = int(row[0])
    st.session_state['agent_session_id'] = session_id
    return session_id


# Persist user/assistant chat messages for traceability.
def log_agent_message(session_id: int, role: str, content: str) -> None:
    execute_write(
        """
        INSERT INTO agent_message (session_id, role, content)
        VALUES (%s, %s, %s)
        """,
        (session_id, role, content),
    )


# Persist tool execution traces (inputs/outputs/latency/success).
def log_agent_tool_call(
    session_id: int,
    tool_name: str,
    args_payload: Dict[str, Any],
    result_payload: Dict[str, Any],
    latency_ms: int,
    success: bool,
) -> None:
    execute_write(
        """
        INSERT INTO agent_tool_call (session_id, tool_name, args_json, result_json, latency_ms, success)
        VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, %s)
        """,
        (
            session_id,
            tool_name,
            json.dumps(args_payload),
            json.dumps(result_payload),
            int(latency_ms),
            bool(success),
        ),
    )


# Persist model-generated recommendation plus token/cost/latency metadata.
# Decisions start in pending status and can later be approved/rejected/escalated.
def log_agent_decision(
    session_id: int,
    decision_type: str,
    recommendation_payload: Dict[str, Any],
    confidence: float,
    model_name: str,
    governance_model: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: int,
    notes: str,
) -> int:
    row = execute_write(
        """
        INSERT INTO agent_decision (
            session_id,
            decision_type,
            recommendation_json,
            confidence,
            model_name,
            governance_model,
            prompt_tokens,
            completion_tokens,
            latency_ms,
            decision_status,
            notes
        )
        VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING decision_id
        """,
        (
            session_id,
            decision_type,
            json.dumps(recommendation_payload),
            float(confidence),
            model_name,
            governance_model,
            int(prompt_tokens),
            int(completion_tokens),
            int(latency_ms),
            'pending',
            notes,
        ),
        fetchone=True,
    )
    return int(row[0])


# Human-in-the-loop override workflow for production governance.
def submit_decision_review(decision_id: int, reviewer: str, action: str, note: str) -> int:
    action_norm = action.strip().lower()
    row = execute_write(
        """
        INSERT INTO agent_decision_review (decision_id, reviewer, action, note)
        VALUES (%s, %s, %s, %s)
        RETURNING review_id
        """,
        (decision_id, reviewer, action_norm, note),
        fetchone=True,
    )
    execute_write(
        "UPDATE agent_decision SET decision_status = %s WHERE decision_id = %s",
        (action_norm, decision_id),
    )
    return int(row[0])


# Attach observed business outcomes to a specific AI decision for feedback loops.
def link_decision_outcome(
    decision_id: int,
    outcome_type: str,
    outcome_status: str,
    outcome_payload: Dict[str, Any],
) -> int:
    row = execute_write(
        """
        INSERT INTO agent_decision_outcome (decision_id, outcome_type, outcome_status, outcome_json)
        VALUES (%s, %s, %s, %s::jsonb)
        RETURNING outcome_id
        """,
        (decision_id, outcome_type, outcome_status, json.dumps(outcome_payload)),
        fetchone=True,
    )
    return int(row[0])


# Distance primitive used in route scoring.
def geodesic_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return 6371 * c


# Tool 1: gather normalized production context for the selected fab.
def query_production_data(fab_id: str, lots_df: pd.DataFrame, constraints_df: pd.DataFrame) -> Dict[str, Any]:
    fab_lots = lots_df[lots_df['fab_id'] == fab_id]
    fab_constraints = constraints_df[constraints_df['affected_fab_id'] == fab_id]
    return {
        'fab_id': fab_id,
        'lot_count': int(fab_lots.shape[0]),
        'wafers_started': float(fab_lots['wafers_started'].sum() or 0),
        'avg_yield_pct': float(fab_lots['yield_pct'].mean() or 0),
        'constraint_count': int(fab_constraints.shape[0]),
    }


# Tool 2: score candidate assembly routes.
# Composite score combines projected utilization, distance, and local constraint pressure.
def evaluate_route_options(
    fab_id: str,
    lots_df: pd.DataFrame,
    fabs_df: pd.DataFrame,
    constraints_df: pd.DataFrame,
) -> Dict[str, Any]:
    targets = FAB_TO_ASM.get(fab_id, [])
    if not targets:
        return {
            'recommended_target': None,
            'confidence': 0.0,
            'reason': f'No configured assembly targets for {fab_id}',
            'candidates': [],
        }

    fab_row = fabs_df[fabs_df['fab_id'] == fab_id]
    if fab_row.empty:
        return {
            'recommended_target': None,
            'confidence': 0.0,
            'reason': f'Missing metadata for fab {fab_id}',
            'candidates': [],
        }

    origin_lat = float(fab_row.iloc[0]['lat'])
    origin_lon = float(fab_row.iloc[0]['lon'])
    wafers_to_route = float(lots_df[lots_df['fab_id'] == fab_id]['wafers_started'].sum() or 0)

    flow_df = build_flow_distribution(lots_df)
    inbound_by_target = flow_df.groupby('target')['wafers'].sum().to_dict() if not flow_df.empty else {}

    candidates = []
    max_distance = 1.0
    max_constraints = 1
    for target in targets:
        target_row = fabs_df[fabs_df['fab_id'] == target]
        if target_row.empty:
            continue

        target_lat = float(target_row.iloc[0]['lat'])
        target_lon = float(target_row.iloc[0]['lon'])
        distance_km = geodesic_km(origin_lat, origin_lon, target_lat, target_lon)
        max_distance = max(max_distance, distance_km)

        inbound = float(inbound_by_target.get(target, 0))
        capacity = float(target_row.iloc[0]['total_wafer_starts_per_month'] or 0)
        projected_util = ((inbound + wafers_to_route) / capacity) if capacity else 1.5

        constraint_hits = int(constraints_df[constraints_df['affected_fab_id'] == target].shape[0])
        max_constraints = max(max_constraints, constraint_hits)

        candidates.append(
            {
                'target': target,
                'distance_km': round(distance_km, 1),
                'capacity': round(capacity, 2),
                'current_inbound_wafers': round(inbound, 2),
                'projected_utilization': round(projected_util, 3),
                'constraint_hits': constraint_hits,
            }
        )

    if not candidates:
        return {
            'recommended_target': None,
            'confidence': 0.0,
            'reason': 'No route candidates with metadata',
            'candidates': [],
        }

    for item in candidates:
        distance_norm = item['distance_km'] / max_distance
        util_norm = min(item['projected_utilization'], 1.5) / 1.5
        constraint_norm = item['constraint_hits'] / max_constraints if max_constraints else 0
        # Weighted score: lower is better.
        # Utilization pressure is weighted highest to avoid overloading assembly sites.
        score = 0.45 * util_norm + 0.35 * distance_norm + 0.20 * constraint_norm
        item['score'] = round(score, 4)

    ranked = sorted(candidates, key=lambda x: x['score'])
    best = ranked[0]
    # Bound confidence so UI avoids extreme 0/1 certainty from simple heuristic scoring.
    confidence = max(0.25, min(0.95, 1.0 - best['score']))

    return {
        'recommended_target': best['target'],
        'confidence': round(confidence, 3),
        'reason': (
            f"Best composite score from utilization={best['projected_utilization']:.2f}, "
            f"distance={best['distance_km']:.1f}km, constraints={best['constraint_hits']}"
        ),
        'candidates': ranked,
    }


# Optional LLM narrative layer over deterministic recommendation payload.
# Falls back to deterministic text when API key is absent or call fails.
def maybe_generate_agent_answer(
    prompt: str,
    context_payload: Dict[str, Any],
    recommendation_payload: Dict[str, Any],
    runtime_model: str,
) -> tuple[str, Dict[str, Any]]:
    api_key = os.getenv('OPENAI_API_KEY')
    if api_key:
        try:
            from openai import OpenAI

            client = OpenAI(api_key=api_key)
            started = time.time()
            resp = client.chat.completions.create(
                model=runtime_model,
                messages=[
                    {
                        'role': 'system',
                        'content': (
                            'You are a semiconductor routing copilot. Base your answer only on the supplied JSON '
                            'snapshot and recommendation payload. Be concise and operational.'
                        ),
                    },
                    {
                        'role': 'user',
                        'content': (
                            f"User prompt: {prompt}\n\n"
                            f"Context JSON: {json.dumps(context_payload)}\n\n"
                            f"Recommendation JSON: {json.dumps(recommendation_payload)}"
                        ),
                    },
                ],
                temperature=0.2,
                max_tokens=280,
            )
            latency_ms = int((time.time() - started) * 1000)
            usage = getattr(resp, 'usage', None)
            prompt_tokens = int(getattr(usage, 'prompt_tokens', 0) or 0)
            completion_tokens = int(getattr(usage, 'completion_tokens', 0) or 0)
            text_out = (resp.choices[0].message.content or '').strip()
            return text_out, {
                'model': runtime_model,
                'prompt_tokens': prompt_tokens,
                'completion_tokens': completion_tokens,
                'latency_ms': latency_ms,
            }
        except Exception as exc:
            fallback = (
                f"Recommended route: {recommendation_payload.get('recommended_target')}\n"
                f"Confidence: {recommendation_payload.get('confidence')}\n"
                f"Reason: {recommendation_payload.get('reason')}\n"
                f"Note: LLM explanation unavailable ({exc})."
            )
            return fallback, {
                'model': 'fallback-template',
                'prompt_tokens': 0,
                'completion_tokens': 0,
                'latency_ms': 0,
            }

    fallback = (
        f"Recommended route: {recommendation_payload.get('recommended_target')}\n"
        f"Confidence: {recommendation_payload.get('confidence')}\n"
        f"Reason: {recommendation_payload.get('reason')}\n"
        "Set OPENAI_API_KEY to enable model-generated narrative."
    )
    return fallback, {
        'model': 'fallback-template',
        'prompt_tokens': 0,
        'completion_tokens': 0,
        'latency_ms': 0,
    }


# Orchestrate one runtime decision turn: context tool -> scoring tool -> explanation -> telemetry log.
def run_agent_turn(
    session_id: int,
    prompt: str,
    fab_id: str,
    lots_df: pd.DataFrame,
    fabs_df: pd.DataFrame,
    constraints_df: pd.DataFrame,
) -> tuple[str, Dict[str, Any], Dict[str, Any], int]:
    pins = get_model_pins()

    t0 = time.time()
    context_payload = query_production_data(fab_id, lots_df, constraints_df)
    t1 = time.time()
    log_agent_tool_call(
        session_id,
        'query_production_data',
        {'fab_id': fab_id},
        context_payload,
        int((t1 - t0) * 1000),
        True,
    )

    t2 = time.time()
    recommendation_payload = evaluate_route_options(fab_id, lots_df, fabs_df, constraints_df)
    t3 = time.time()
    log_agent_tool_call(
        session_id,
        'evaluate_route_options',
        {'fab_id': fab_id},
        recommendation_payload,
        int((t3 - t2) * 1000),
        recommendation_payload.get('recommended_target') is not None,
    )

    answer, meta = maybe_generate_agent_answer(
        prompt,
        context_payload,
        recommendation_payload,
        pins['runtime_model'],
    )

    decision_id = log_agent_decision(
        session_id=session_id,
        decision_type='route_recommendation',
        recommendation_payload=recommendation_payload,
        confidence=float(recommendation_payload.get('confidence') or 0),
        model_name=meta.get('model', 'unknown'),
        governance_model=pins['governance_model'],
        prompt_tokens=int(meta.get('prompt_tokens', 0)),
        completion_tokens=int(meta.get('completion_tokens', 0)),
        latency_ms=int(meta.get('latency_ms', 0)),
        notes=prompt,
    )

    return answer, recommendation_payload, meta, decision_id


# ------------------------------
# Streamlit UI starts here
# ------------------------------
st.set_page_config(page_title="Semiconductor Logistics Dashboard", layout="wide")
st.title("Mock Semiconductor Logistics Dashboard")
st.markdown("Real-time fabs + macro overlays (BLS + World Bank + IMF).")

lots_df = run_query("SELECT * FROM production_lot")
lots_df['start_date'] = pd.to_datetime(lots_df['start_date'])
fabs_df = prepare_fab_metadata(run_query("SELECT fab_id, name, location, site_type, total_wafer_starts_per_month FROM fab"))
constraints_df = run_query("SELECT * FROM operational_constraint")

st.sidebar.header("Filters")
available_tech = sorted(lots_df['tech_id'].unique())
available_fabs = sorted(fabs_df[fabs_df['site_type'] == 'Front-End']['fab_id'].unique())
tech_filter = st.sidebar.multiselect("Technology Node", options=available_tech)
fab_filter = st.sidebar.multiselect("Fab", options=available_fabs)

filtered_lots = lots_df.copy()
if tech_filter:
    filtered_lots = filtered_lots[filtered_lots['tech_id'].isin(tech_filter)]
if fab_filter:
    filtered_lots = filtered_lots[filtered_lots['fab_id'].isin(fab_filter)]

col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Front-End Fabs", int((fabs_df['site_type'] == 'Front-End').sum()))
with col2:
    st.metric("Production Lots", int(filtered_lots.shape[0]))
with col3:
    wafers = filtered_lots['wafers_started'].sum()
    st.metric("Wafers Started", f"{wafers:,.0f}")
with col4:
    avg_yield = filtered_lots['yield_pct'].mean()
    st.metric("Avg Yield", f"{avg_yield:.1f}%" if pd.notnull(avg_yield) else 'N/A')

st.divider()

st.subheader("??? Facility Locations")
m = folium.Map(location=[20, 0], zoom_start=2)
for _, row in fabs_df.iterrows():
    color = 'blue' if row['site_type'] == 'Front-End' else 'green'
    tooltip = f"{row['fab_id']} ({row['country_name']})"
    folium.Marker(
        location=[row['lat'], row['lon']],
        popup=f"{row['name']}\n{row['location']}",
        tooltip=tooltip,
        icon=folium.Icon(color=color)
    ).add_to(m)

route_options = [fab for fab in fabs_df[fabs_df['site_type'] == 'Front-End']['fab_id'].unique() if fab in FAB_TO_ASM]
selected_fab = st.selectbox('Show routing paths for fab', options=route_options)
if selected_fab:
    targets = FAB_TO_ASM.get(selected_fab, [])
    origin = (fabs_df.set_index('fab_id').loc[selected_fab, 'lat'], fabs_df.set_index('fab_id').loc[selected_fab, 'lon'])
    for target in targets:
        target_row = fabs_df[fabs_df['fab_id'] == target]
        if target_row.empty:
            continue
        dest = (target_row.iloc[0]['lat'], target_row.iloc[0]['lon'])
        folium.PolyLine(locations=[origin, dest], color='orange', weight=3, opacity=0.7).add_to(m)

st_folium(m, width=1000, height=500)
st.divider()

st.subheader("?? Production Lots")
st.dataframe(filtered_lots.sort_values('start_date', ascending=False), use_container_width=True)

st.divider()

st.subheader("Macro, Logistics & FX Signals")
macro_tab, lpi_tab, fx_tab, agent_tab = st.tabs(["Macro Signals", "Logistics Risk (LPI)", "FX Exposure", "AI Agent"])

# Macro tab: combine internal wafer output with external macro context for planning narrative.
with macro_tab:
    monthly_wafers = filtered_lots.copy()
    monthly_wafers['month'] = monthly_wafers['start_date'].dt.to_period('M').dt.to_timestamp()
    monthly_wafers = monthly_wafers.groupby('month')['wafers_started'].sum().reset_index()

    bls_ip = fetch_bls_series('IPG3344S')
    bls_emp = fetch_bls_series('CES3133440001')
    st.caption(
        f"BLS source - IP: {bls_ip.attrs.get('source', 'unknown')} | Employment: {bls_emp.attrs.get('source', 'unknown')}"
    )

    if bls_ip.empty or bls_emp.empty:
        st.info('BLS signal unavailable. Using country overlays only.')
    else:
        col_a, col_b = st.columns(2)
        with col_a:
            merged = pd.merge(bls_ip, monthly_wafers, left_on='date', right_on='month', how='left')
            merged.rename(columns={'value': BLS_SERIES['IPG3344S'], 'wafers_started': 'Wafer starts'}, inplace=True)
            fig = px.line(
                merged,
                x='date',
                y=[BLS_SERIES['IPG3344S'], 'Wafer starts'],
                labels={'value': 'Index', 'date': 'Month'},
            )
            fig.update_layout(title='Industrial Production vs. Internal Output')
            st.plotly_chart(fig, use_container_width=True)

        with col_b:
            emp_df = bls_emp.rename(columns={'value': 'Employment (thousands)'})
            fig = px.line(emp_df, x='date', y='Employment (thousands)', title='US Semiconductor Employment')
            st.plotly_chart(fig, use_container_width=True)

    overlay_df = build_country_overlay(fabs_df)
    if not overlay_df.empty:
        st.dataframe(overlay_df, use_container_width=True)
    else:
        st.info('Country economic overlays unavailable.')

# LPI tab: logistics route quality indicators by country and fab capacity coupling.
with lpi_tab:
    render_lpi_section(fabs_df)

# FX tab: monthly logistics cost exposure under current exchange rates.
with fx_tab:
    render_fx_section(fabs_df)

# AI Agent tab: runtime decision surface with telemetry, review, and outcome linkage workflows.
with agent_tab:
    st.subheader('AI Routing Advisor')
    st.caption('Phase 1 agent: DB-backed routing recommendation + telemetry, review workflow, and outcome linkage.')

    agent_available = True
    agent_error = ''
    session_id = None
    pins = get_model_pins()

    try:
        ensure_agent_tables()
        upsert_model_registry()
        session_id = get_or_create_agent_session()
    except Exception as exc:
        agent_available = False
        agent_error = str(exc)

    st.markdown('**Model Pins**')
    st.code(
        f"runtime={pins['runtime_model']} | governance={pins['governance_model']} | approved={', '.join(pins['approved_models'])}"
    )
    if not pins['runtime_approved']:
        st.warning('Runtime model is not in AGENT_APPROVED_MODELS.')
    if not pins['governance_approved']:
        st.warning('Governance model is not in AGENT_APPROVED_MODELS.')

    if 'agent_messages' not in st.session_state:
        st.session_state['agent_messages'] = [
            {
                'role': 'assistant',
                'content': (
                    'Ask for a routing recommendation. I will evaluate capacity, distance, and constraint pressure '
                    'for the selected fab and log every decision to Postgres.'
                ),
            }
        ]

    for msg in st.session_state['agent_messages']:
        with st.chat_message(msg['role']):
            st.markdown(msg['content'])

    if not agent_available:
        st.error(f'Agent unavailable: {agent_error}')
    else:
        agent_fab_options = route_options if route_options else sorted(fabs_df[fabs_df['site_type'] == 'Front-End']['fab_id'].unique())
        default_idx = 0
        if selected_fab in agent_fab_options:
            default_idx = agent_fab_options.index(selected_fab)
        selected_agent_fab = st.selectbox('Fab context for decision', options=agent_fab_options, index=default_idx, key='agent_fab_context')

        def process_agent_prompt(prompt_text: str) -> None:
            st.session_state['agent_messages'].append({'role': 'user', 'content': prompt_text})
            log_agent_message(session_id, 'user', prompt_text)

            answer, decision_payload, _meta, decision_id = run_agent_turn(
                session_id=session_id,
                prompt=prompt_text,
                fab_id=selected_agent_fab,
                lots_df=filtered_lots,
                fabs_df=fabs_df,
                constraints_df=constraints_df,
            )
            st.session_state['last_agent_decision'] = decision_payload
            st.session_state['last_agent_decision_id'] = int(decision_id)
            st.session_state['agent_messages'].append({'role': 'assistant', 'content': answer})
            log_agent_message(session_id, 'assistant', answer)

        if st.button('Propose routing decision', key='agent_propose_btn'):
            auto_prompt = f'Propose the best assembly routing target for fab {selected_agent_fab} and explain why.'
            process_agent_prompt(auto_prompt)
            st.rerun()

        user_prompt = st.chat_input('Ask the routing agent...', key='agent_chat_input')
        if user_prompt:
            process_agent_prompt(user_prompt)
            st.rerun()

        if 'last_agent_decision' in st.session_state:
            st.markdown('**Latest recommendation payload**')
            st.json(st.session_state['last_agent_decision'])
            st.caption(f"decision_id={st.session_state.get('last_agent_decision_id', 'n/a')}")

        st.markdown('---')
        st.markdown('**Human Override (Approve / Reject / Escalate)**')
        review_decision_id = int(st.session_state.get('last_agent_decision_id', 0) or 0)
        if review_decision_id:
            review_action = st.selectbox('Review action', options=['approve', 'reject', 'escalate'], key='review_action')
            review_note = st.text_input('Review note', key='review_note')
            reviewer = st.text_input('Reviewer', value='ops_manager', key='reviewer_name')
            if st.button('Submit review', key='submit_review_btn'):
                review_id = submit_decision_review(review_decision_id, reviewer, review_action, review_note)
                st.success(f'Review recorded (review_id={review_id}) for decision_id={review_decision_id}.')
                st.rerun()
        else:
            st.info('Generate a decision first to enable review.')

        st.markdown('---')
        st.markdown('**Link Business Outcome**')
        outcome_decision_id = int(st.session_state.get('last_agent_decision_id', 0) or 0)
        if outcome_decision_id:
            outcome_type = st.selectbox('Outcome type', options=['delivery', 'yield', 'cost', 'sla'], key='outcome_type')
            outcome_status = st.selectbox('Outcome status', options=['pending', 'met', 'missed', 'unknown'], key='outcome_status')
            outcome_json_text = st.text_area(
                'Outcome payload JSON',
                value='{"delta_yield_pct": 0.0, "delta_cost_usd": 0.0}',
                key='outcome_json_text',
            )
            if st.button('Link outcome', key='link_outcome_btn'):
                try:
                    payload = json.loads(outcome_json_text)
                except Exception as exc:
                    st.error(f'Invalid JSON payload: {exc}')
                else:
                    outcome_id = link_decision_outcome(outcome_decision_id, outcome_type, outcome_status, payload)
                    st.success(f'Outcome linked (outcome_id={outcome_id}) to decision_id={outcome_decision_id}.')
                    st.rerun()
        else:
            st.info('Generate a decision first to link an outcome.')

        try:
            recent = run_query(
                """
                SELECT decision_id, created_at, decision_type, model_name, governance_model,
                       confidence, decision_status, prompt_tokens, completion_tokens, latency_ms
                FROM agent_decision
                ORDER BY decision_id DESC
                LIMIT 10
                """
            )
            if not recent.empty:
                st.markdown('**Recent decision telemetry**')
                st.dataframe(recent, use_container_width=True)

            reviews = run_query(
                """
                SELECT review_id, decision_id, reviewer, action, note, created_at
                FROM agent_decision_review
                ORDER BY review_id DESC
                LIMIT 10
                """
            )
            if not reviews.empty:
                st.markdown('**Recent reviews**')
                st.dataframe(reviews, use_container_width=True)

            outcomes = run_query(
                """
                SELECT outcome_id, decision_id, outcome_type, outcome_status, outcome_json, observed_at
                FROM agent_decision_outcome
                ORDER BY outcome_id DESC
                LIMIT 10
                """
            )
            if not outcomes.empty:
                st.markdown('**Recent linked outcomes**')
                st.dataframe(outcomes, use_container_width=True)
        except Exception as exc:
            st.info(f'Could not load agent telemetry tables: {exc}')

capacity_df = build_capacity_table(filtered_lots, fabs_df)
assembly_df = build_assembly_load(filtered_lots, fabs_df)
forecast_df = build_forecast_series(filtered_lots)

# Capacity section: operational readiness views for front-end, assembly load, and short-horizon demand.
st.subheader("Capacity & Routing Readiness")
cap_tab, route_tab, forecast_tab = st.tabs(["Front-End Capacity", "Assembly Routing", "Demand Forecast"])

with cap_tab:
    if capacity_df.empty:
        st.info('No capacity data available for current filters.')
    else:
        st.dataframe(capacity_df[['fab_id', 'country_name', 'capacity', 'demand', 'utilization_pct', 'status']], use_container_width=True)
        fig = px.bar(capacity_df, x='fab_id', y='utilization_pct', color='status', title='Front-end utilization (%)', labels={'utilization_pct': 'Utilization %'})
        st.plotly_chart(fig, use_container_width=True)

with route_tab:
    if assembly_df.empty:
        st.info('No assembly routing data for current filters.')
    else:
        st.dataframe(assembly_df[['assembly_id', 'inbound_wafers', 'capacity', 'utilization_pct']], use_container_width=True)
        fig = px.bar(assembly_df, x='assembly_id', y='inbound_wafers', title='Inbound wafers per assembly site', labels={'inbound_wafers': 'Wafers/month'})
        st.plotly_chart(fig, use_container_width=True)

with forecast_tab:
    if forecast_df.empty:
        st.info('Not enough history to build a forecast.')
    else:
        fig = px.line(forecast_df, x='month', y='wafers_started', color='type', title='Wafer demand forecast (next 3 months)', labels={'wafers_started': 'Wafers', 'month': 'Month'})
        st.plotly_chart(fig, use_container_width=True)

st.divider()
st.subheader("?? Yield by Fab and Technology")
yield_df = filtered_lots.groupby(['fab_id', 'tech_id']).agg(
    avg_yield=('yield_pct', 'mean'),
    lot_count=('lot_id', 'count')
).reset_index()
if not yield_df.empty:
    fig = px.bar(yield_df, x='fab_id', y='avg_yield', color='tech_id', hover_data=['lot_count'],
                 barmode='group', title='Average Yield by Fab and Technology')
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("No yield data available for current filters.")

st.divider()


st.subheader("?? Operational Constraints")
st.dataframe(constraints_df, use_container_width=True)

st.divider()


def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c

# Baseline deterministic route table for quick non-agent route sanity checks.
st.subheader("?? Optimized Route Suggestions")
front_end = fabs_df[fabs_df['site_type'] == 'Front-End']
back_end = fabs_df[fabs_df['site_type'] != 'Front-End']
routes = []
for _, fab in front_end.iterrows():
    for _, asm in back_end.iterrows():
        dist = haversine(fab['lat'], fab['lon'], asm['lat'], asm['lon'])
        routes.append({'Fab': fab['fab_id'], 'Assembly': asm['fab_id'], 'Distance_km': round(dist, 1)})

routes_df = pd.DataFrame(routes)
if not routes_df.empty:
    top_routes = routes_df.sort_values(['Fab', 'Distance_km']).groupby('Fab').head(3).reset_index(drop=True)
    st.dataframe(top_routes, use_container_width=True)
else:
    st.info("Route analysis unavailable.")

st.caption("Macro overlays: BLS v1, World Bank, IMF DataMapper — anonymous, no API keys required.")