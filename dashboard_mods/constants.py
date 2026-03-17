"""
constants.py — static lookup tables and environment-derived config.
One responsibility: be the single source of truth for all fixed data.
No UI. No SQL. No business logic.
"""
import os
from math import isfinite

import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))

from typing import Dict, List

from routing_engine import DEFAULT_WEIGHTS, RISK_TOLERANCE_THRESHOLDS

# ── Paths ─────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent   # repo root
AGENT_PLUGIN_DIR = ROOT / 'agents'

# ── BLS / World Bank / IMF series IDs ────────────────────────────────────────

BLS_SERIES = {
    'IPG3344S':       'Industrial Production: Semiconductors',
    'CES3133440001':  'Employment in Semiconductor Mfg (thousands)',
}

WORLD_BANK_INDICATORS = {
    'LP.LPI.OVRL.XQ':  'Logistics Performance Index (overall)',
    'NV.IND.MANF.ZS':  'Manufacturing value added (% of GDP)',
    'NY.GDP.MKTP.KD.ZG': 'GDP growth (annual %)',
}

WORLD_BANK_FALLBACK = {
    'LP.LPI.OVRL.XQ':    {'US': 3.99, 'IE': 3.74, 'IL': 3.44, 'MY': 3.22, 'VN': 3.27, 'CR': 2.87, 'CN': 3.65, 'PH': 3.30},
    'NV.IND.MANF.ZS':    {'US': 10.8, 'IE': 32.5, 'IL': 11.7, 'MY': 23.4, 'VN': 24.1, 'CR': 13.6, 'CN': 27.4, 'PH': 19.4},
    'NY.GDP.MKTP.KD.ZG': {'US': 2.3,  'IE': 1.8,  'IL': 2.0,  'MY': 4.1,  'VN': 5.6,  'CR': 3.8,  'CN': 4.8,  'PH': 5.1},
}

IMF_INDICATOR = 'NGDP_RPCH'
IMF_GROWTH_FALLBACK = {
    'USA': 2.1, 'IRL': 1.9, 'ISR': 2.0, 'MYS': 4.0,
    'VNM': 5.5, 'CRI': 3.7, 'CHN': 4.6, 'PHL': 5.2,
}

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
    'PHL': {'lpi_overall': 3.30, 'lpi_customs': 3.08, 'lpi_infrastructure': 3.20, 'lpi_timeliness': 3.62, 'lpi_tracking': 3.31, 'lpi_intl_shipments': 3.14},
}

# ── Fab geography ─────────────────────────────────────────────────────────────

FAB_COUNTRY: Dict[str, str] = {
    'AZ_F12': 'USA', 'AZ_F22': 'USA', 'AZ_F32': 'USA', 'AZ_F42': 'USA', 'AZ_F52': 'USA', 'AZ_F62': 'USA',
    'NM_F11X': 'USA', 'NM_RR': 'USA',
    'OR_D1X': 'USA', 'OR_D1D': 'USA', 'OR_D1C': 'USA',
    'OH_1': 'USA',
    'IE_F24': 'IRL', 'IE_F34': 'IRL',
    'IL_F28': 'ISR', 'IL_F28a': 'ISR', 'IL_F38': 'ISR', 'IL_JS': 'ISR',
    'CN_DL68': 'CHN', 'CN_SH': 'CHN', 'CN_CD': 'CHN',
    'MY_KUL': 'MYS', 'MY_PG': 'MYS',
    'PH_CAV': 'PHL', 'VN_HCM': 'VNM', 'CR_SJ': 'CRI',
}

FAB_TO_ASM: Dict[str, List[str]] = {
    'AZ_F12':  ['NM_RR', 'CR_SJ', 'MY_KUL'],
    'AZ_F22':  ['NM_RR', 'CR_SJ', 'MY_KUL'],
    'AZ_F32':  ['NM_RR', 'MY_KUL', 'MY_PG'],
    'AZ_F42':  ['NM_RR', 'MY_KUL', 'MY_PG'],
    'AZ_F52':  ['NM_RR', 'MY_PG', 'VN_HCM'],
    'AZ_F62':  ['NM_RR', 'MY_PG', 'VN_HCM'],
    'NM_F11X': ['NM_RR', 'CR_SJ', 'MY_KUL'],
    'OR_D1X':  ['NM_RR', 'MY_KUL'],
    'OR_D1D':  ['NM_RR', 'MY_KUL'],
    'OR_D1C':  ['NM_RR', 'CR_SJ'],
    'OH_1':    ['NM_RR', 'MY_PG', 'VN_HCM'],
    'IE_F24':  ['IL_JS', 'CN_SH', 'MY_PG'],
    'IE_F34':  ['IL_JS', 'CN_SH', 'MY_PG'],
    'IL_F28':  ['IL_JS', 'VN_HCM', 'CN_CD'],
    'IL_F28a': ['IL_JS', 'VN_HCM', 'CN_CD'],
    'IL_F38':  ['IL_JS', 'VN_HCM', 'CN_CD'],
    'CN_DL68': ['CN_CD', 'CN_SH', 'MY_PG'],
}

LOCATION_TO_ISO3: Dict[str, str] = {
    'Chandler, Arizona, USA':       'USA',
    'Rio Rancho, New Mexico, USA':  'USA',
    'Hillsboro, Oregon, USA':       'USA',
    'New Albany, Ohio, USA':        'USA',
    'Leixlip, Ireland':             'IRL',
    'Kiryat Gat, Israel':           'ISR',
    'Jerusalem, Israel':            'ISR',
    'Dalian, Liaoning, China':      'CHN',
    'Shanghai, China':              'CHN',
    'Chengdu, China':               'CHN',
    'San Jose, Costa Rica':         'CRI',
    'Kulim, Malaysia':              'MYS',
    'Penang, Malaysia':             'MYS',
    'Cavite, Philippines':          'PHL',
    'Ho Chi Minh City, Vietnam':    'VNM',
}

LOCATION_COORDS: Dict[str, tuple] = {
    'AZ_F12': (33.3, -111.8), 'AZ_F22': (33.3, -111.8), 'AZ_F32': (33.3, -111.8),
    'AZ_F42': (33.3, -111.8), 'AZ_F52': (33.3, -111.8), 'AZ_F62': (33.3, -111.8),
    'NM_F11X': (35.23, -106.66), 'NM_RR': (35.23, -106.66),
    'OR_D1X': (45.52, -122.99), 'OR_D1D': (45.52, -122.99), 'OR_D1C': (45.52, -122.99),
    'OH_1': (40.08, -82.81),
    'IE_F24': (53.3, -6.5), 'IE_F34': (53.3, -6.5),
    'IL_F28': (31.61, 34.77), 'IL_F28a': (31.61, 34.77), 'IL_F38': (31.61, 34.77),
    'IL_JS': (31.77, 35.22),
    'CN_DL68': (38.91, 121.61), 'CN_SH': (31.23, 121.47), 'CN_CD': (30.57, 104.07),
    'MY_KUL': (5.41, 100.62), 'MY_PG': (5.41, 100.33),
    'PH_CAV': (14.28, 120.87), 'VN_HCM': (10.82, 106.63), 'CR_SJ': (9.93, -84.08),
}

COUNTRY_LOOKUP: Dict[str, Dict[str, str]] = {
    'USA': {'name': 'United States', 'iso2': 'US'},
    'IRL': {'name': 'Ireland',       'iso2': 'IE'},
    'ISR': {'name': 'Israel',        'iso2': 'IL'},
    'CHN': {'name': 'China',         'iso2': 'CN'},
    'CRI': {'name': 'Costa Rica',    'iso2': 'CR'},
    'MYS': {'name': 'Malaysia',      'iso2': 'MY'},
    'PHL': {'name': 'Philippines',   'iso2': 'PH'},
    'VNM': {'name': 'Vietnam',       'iso2': 'VN'},
}

CURRENCY_MAP: Dict[str, str] = {
    'USA': 'USD', 'IRL': 'EUR', 'ISR': 'ILS', 'CHN': 'CNY',
    'CRI': 'CRC', 'MYS': 'MYR', 'PHL': 'PHP', 'VNM': 'VND',
}

# ── FX ────────────────────────────────────────────────────────────────────────

FX_API_URL = 'https://open.er-api.com/v6/latest/USD'
BASE_SHIPPING_COST_PER_WAFER_USD = 120
VOLATILE_CURRENCIES = {'CRC', 'VND'}
FX_FALLBACK_RATES: Dict[str, float] = {
    'USD': 1.0, 'EUR': 0.92, 'ILS': 3.65, 'CNY': 7.18,
    'CRC': 520.0, 'MYR': 4.72, 'VND': 25000.0,
}

# ── Routing defaults (derived from env) ───────────────────────────────────────

def _env_int(name: str, default: int, min_value: int, max_value: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except Exception:
        value = default
    return max(min_value, min(max_value, value))


def _env_float(name: str, default: float, min_value: float = 0.0) -> float:
    try:
        value = float(os.getenv(name, str(default)).strip())
    except Exception:
        value = default
    if not isfinite(value):
        value = default
    return max(min_value, value)


def _default_risk_tolerance() -> str:
    raw = os.getenv('ROUTING_DEFAULT_RISK_TOLERANCE', 'medium').strip().lower()
    if raw in RISK_TOLERANCE_THRESHOLDS:
        return raw
    try:
        numeric = float(raw)
        if isfinite(numeric):
            return f"custom:{max(0.0, min(1.0, numeric)):.2f}"
    except Exception:
        pass
    return 'medium'


ROUTING_DEFAULT_TOP_K = _env_int('ROUTING_DEFAULT_TOP_K', 3, 1, 5)
ROUTING_DEFAULT_RISK_TOLERANCE = _default_risk_tolerance()
ROUTING_DEFAULT_WEIGHTS: Dict[str, float] = {
    'time':     _env_float('ROUTING_WEIGHT_TIME',     DEFAULT_WEIGHTS['time']),
    'cost':     _env_float('ROUTING_WEIGHT_COST',     DEFAULT_WEIGHTS['cost']),
    'risk':     _env_float('ROUTING_WEIGHT_RISK',     DEFAULT_WEIGHTS['risk']),
    'capacity': _env_float('ROUTING_WEIGHT_CAPACITY', DEFAULT_WEIGHTS['capacity']),
}
