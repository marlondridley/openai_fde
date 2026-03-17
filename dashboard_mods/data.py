"""
data.py — external data fetching only.
One responsibility: call APIs, return DataFrames.
All functions follow the same pattern: try live API, fall back to seeded data.
"""
from dotenv import load_dotenv

import requests
import pandas as pd
from datetime import datetime
from typing import Dict, List, Tuple
import streamlit as st

import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
load_dotenv(Path(__file__).resolve().parent.parent / '.env')  # load .env file for environment variables for this file only
from constants import (
    BLS_SERIES, WORLD_BANK_INDICATORS, WORLD_BANK_FALLBACK,
    IMF_INDICATOR, IMF_GROWTH_FALLBACK,
    LPI_INDICATORS, LPI_2023_FALLBACK, LOCATION_TO_ISO3,
    CURRENCY_MAP, VOLATILE_CURRENCIES, FAB_COUNTRY,
    BASE_SHIPPING_COST_PER_WAFER_USD, FX_FALLBACK_RATES,
)

API_TIMEOUT = 10


# ── Shared helpers (DRY: one place for the fetch+fallback pattern) ────────────

def _get(url: str, params: dict = None) -> dict:
    """Make a GET request. Raises on failure so callers can fall back."""
    resp = requests.get(url, params=params or {}, timeout=API_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _fallback_df(rows: list, source: str) -> pd.DataFrame:
    """Wrap a list of rows into a DataFrame and tag its source."""
    df = pd.DataFrame(rows)
    df.attrs['source'] = source
    return df


# ── BLS ───────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=86400)
def fetch_bls_series(series_id: str, start_year: int = 2018) -> pd.DataFrame:
    try:
        payload = _get(
            f"https://api.bls.gov/publicAPI/v1/timeseries/data/{series_id}",
            {'startyear': start_year, 'endyear': datetime.utcnow().year},
        )
        rows = [
            {'date': datetime(int(item['year']), int(item['period'][1:]), 1),
             'value': float(item['value'])}
            for item in payload['Results']['series'][0]['data']
            if item.get('period', '').startswith('M') and item.get('value')
        ]
        if not rows:
            raise ValueError('empty')
        return _fallback_df(sorted(rows, key=lambda r: r['date']), 'BLS API (live)')
    except Exception:
        return _bls_fallback(series_id, start_year)


def _bls_fallback(series_id: str, start_year: int) -> pd.DataFrame:
    end = pd.Timestamp.utcnow().to_period('M').to_timestamp()
    start = max(pd.Timestamp(start_year, 1, 1), end - pd.DateOffset(months=23))
    base, step = (105.0, 0.55) if series_id == 'IPG3344S' else (355.0, 0.35)
    rows = [
        {'date': d.to_pydatetime(), 'value': round(base + i * step, 2)}
        for i, d in enumerate(pd.date_range(start=start, end=end, freq='MS'))
    ]
    return _fallback_df(rows, 'BLS fallback (seeded)')


# ── World Bank ────────────────────────────────────────────────────────────────

@st.cache_data(ttl=86400)
def fetch_worldbank_indicator(indicator: str, iso2_codes: List[str]) -> pd.DataFrame:
    if not iso2_codes:
        return pd.DataFrame()
    try:
        rows, page = [], 1
        while True:
            payload = _get(
                f"https://api.worldbank.org/v2/country/{';'.join(iso2_codes)}/indicator/{indicator}",
                {'format': 'json', 'per_page': 200, 'page': page},
            )
            if len(payload) < 2 or not payload[1]:
                break
            rows += [
                {'country': e['country']['id'], 'country_name': e['country']['value'],
                 'date': e['date'], 'value': float(e['value']), 'indicator': indicator}
                for e in payload[1] if e.get('value') is not None
            ]
            if page >= payload[0].get('pages', 1):
                break
            page += 1
        if not rows:
            raise ValueError('empty')
        return _fallback_df(rows, 'World Bank API (live)')
    except Exception:
        return _worldbank_fallback(indicator, iso2_codes)


def _worldbank_fallback(indicator: str, iso2_codes: List[str]) -> pd.DataFrame:
    values = WORLD_BANK_FALLBACK.get(indicator, {})
    rows = [
        {'country': iso2, 'country_name': iso2,
         'date': str(datetime.utcnow().year), 'value': float(values[iso2]), 'indicator': indicator}
        for iso2 in iso2_codes if iso2 in values
    ]
    return _fallback_df(rows, 'World Bank fallback (seeded)')


# ── LPI ───────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=86400)
def fetch_lpi_data() -> Tuple[pd.DataFrame, str]:
    iso_codes = ';'.join(sorted(set(LOCATION_TO_ISO3.values())))
    indicators = ';'.join(LPI_INDICATORS.keys())
    try:
        data = _get(
            f"https://api.worldbank.org/v2/country/{iso_codes}/indicator/{indicators}"
            f"?format=json&per_page=200&mrv=1"
        )
        if not data or len(data) < 2 or not data[1]:
            raise ValueError('empty')
        rows = {}
        for item in data[1]:
            if item.get('value') is None:
                continue
            iso = item.get('countryiso3code')
            col = LPI_INDICATORS.get(item.get('indicator', {}).get('id'))
            if iso and col:
                rows.setdefault(iso, {'iso3': iso})[col] = round(float(item['value']), 3)
        return pd.DataFrame(rows.values()), 'World Bank API (live)'
    except Exception:
        return pd.DataFrame([{'iso3': k, **v} for k, v in LPI_2023_FALLBACK.items()]), \
               'World Bank LPI 2023 (cached)'


# ── IMF ───────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=86400)
def fetch_imf_growth(iso3_codes: List[str]) -> pd.DataFrame:
    rows, source = [], 'IMF API (live)'
    for code in iso3_codes:
        value = _fetch_imf_one(code)
        if value is not None:
            rows.append({'country': code, 'year': datetime.utcnow().year, 'value': value})
        elif code in IMF_GROWTH_FALLBACK:
            source = 'IMF mixed/fallback'
            rows.append({'country': code, 'year': datetime.utcnow().year,
                         'value': float(IMF_GROWTH_FALLBACK[code])})
    return _fallback_df(rows, source)


def _fetch_imf_one(code: str):
    """Return the latest IMF growth value for one country, or None on failure."""
    try:
        payload = _get(
            f"https://www.imf.org/external/datamapper/api/v1/WEO/{IMF_INDICATOR}/{code}"
        )
        data = payload.get('WEO', {}).get(IMF_INDICATOR, {}).get(code, {})
        valid = [(int(y), float(v)) for y, v in data.items() if v is not None]
        return sorted(valid)[-1][1] if valid else None
    except Exception:
        return None


# ── FX ───────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=3600)
def fetch_fx_rates() -> Tuple[Dict[str, float], str]:
    try:
        payload = _get('https://open.er-api.com/v6/latest/USD')
        rates = payload.get('rates', {})
        if not rates:
            raise ValueError('empty')
        return rates, payload.get('time_last_update_utc', 'FX feed timestamp unavailable')
    except Exception:
        return FX_FALLBACK_RATES, 'FX fallback (seeded rates)'
