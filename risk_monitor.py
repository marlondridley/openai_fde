from datetime import datetime
from math import isfinite
from typing import Any, Dict, List

import pandas as pd

SEVERITY_SCORE = {
    'normal': 0.0,
    'low': 0.25,
    'medium': 0.5,
    'high': 0.75,
    'critical': 1.0,
}

SITE_KEY_NOTES = {
    'CN_CD': "World's largest chip packaging/test center; handles >50% of laptop CPU packaging.",
    'CN_DL68': "Intel's first fab in Asia; $2.5B initial investment; opened in 2010.",
    'VN_HCM': "Intel's largest global chip manufacturing facility.",
    'OH_1': "Ohio One is a two-fab megasite currently under construction.",
    'PH_CAV': "Major assembly/test site serving Asia Pacific demand.",
}

DEFAULT_RISK_EVENTS = [
    {
        'event_type': 'conflict',
        'severity': 'critical',
        'region': 'Middle East',
        'affected_countries': ['ISR'],
        'affected_sites': ['IL_F28', 'IL_F28a', 'IL_F38', 'IL_JS'],
        'start_date': '2026-01-01',
        'end_date': None,
        'description': 'Active armed conflict in Israel; FAA restrictions and elevated logistics risk.',
        'source_url': 'https://www.crisisgroup.org/crisiswatch',
    },
    {
        'event_type': 'maritime',
        'severity': 'high',
        'region': 'Red Sea',
        'affected_countries': ['IRL', 'ISR', 'CHN', 'MYS', 'VNM'],
        'affected_sites': [],
        'start_date': '2026-02-01',
        'end_date': None,
        'description': 'Maritime diversions via Cape of Good Hope increasing lead time and cost.',
        'source_url': 'https://www.lloydslist.com',
    },
]

DEFAULT_TRANSPORT_DISRUPTIONS = [
    {
        'disruption_type': 'tsa_staffing',
        'location_id': 'PHX',
        'location_type': 'airport',
        'severity': 'high',
        'wait_time_minutes': 95,
        'description': 'TSA staffing shortage due to DHS shutdown pressure.',
        'start_time': '2026-03-01T00:00:00Z',
        'end_time': None,
        'source': 'seed:risk-default',
    },
    {
        'disruption_type': 'port_congestion',
        'location_id': 'SGN',
        'location_type': 'seaport',
        'severity': 'medium',
        'wait_time_minutes': 36,
        'description': 'Vessel backlog after typhoon season.',
        'start_time': '2026-03-05T00:00:00Z',
        'end_time': None,
        'source': 'seed:risk-default',
    },
]

DEFAULT_SITE_PORT_MAPPING = [
    {'site_id': 'AZ_F12', 'port_id': 'PHX', 'port_type': 'air', 'distance_km': 33, 'transit_mode': 'truck', 'is_primary': True},
    {'site_id': 'AZ_F22', 'port_id': 'PHX', 'port_type': 'air', 'distance_km': 33, 'transit_mode': 'truck', 'is_primary': True},
    {'site_id': 'AZ_F32', 'port_id': 'PHX', 'port_type': 'air', 'distance_km': 33, 'transit_mode': 'truck', 'is_primary': True},
    {'site_id': 'AZ_F42', 'port_id': 'PHX', 'port_type': 'air', 'distance_km': 33, 'transit_mode': 'truck', 'is_primary': True},
    {'site_id': 'AZ_F52', 'port_id': 'PHX', 'port_type': 'air', 'distance_km': 33, 'transit_mode': 'truck', 'is_primary': True},
    {'site_id': 'AZ_F62', 'port_id': 'PHX', 'port_type': 'air', 'distance_km': 33, 'transit_mode': 'truck', 'is_primary': True},
    {'site_id': 'NM_RR', 'port_id': 'ABQ', 'port_type': 'air', 'distance_km': 22, 'transit_mode': 'truck', 'is_primary': True},
    {'site_id': 'OH_1', 'port_id': 'CMH', 'port_type': 'air', 'distance_km': 27, 'transit_mode': 'truck', 'is_primary': True},
    {'site_id': 'IE_F24', 'port_id': 'DUB', 'port_type': 'air', 'distance_km': 20, 'transit_mode': 'truck', 'is_primary': True},
    {'site_id': 'IE_F34', 'port_id': 'DUB', 'port_type': 'air', 'distance_km': 20, 'transit_mode': 'truck', 'is_primary': True},
    {'site_id': 'IL_JS', 'port_id': 'TLV', 'port_type': 'air', 'distance_km': 55, 'transit_mode': 'truck', 'is_primary': True},
    {'site_id': 'CN_CD', 'port_id': 'TFU', 'port_type': 'air', 'distance_km': 22, 'transit_mode': 'truck', 'is_primary': True},
    {'site_id': 'MY_KUL', 'port_id': 'KUL', 'port_type': 'air', 'distance_km': 45, 'transit_mode': 'truck', 'is_primary': True},
    {'site_id': 'MY_PG', 'port_id': 'PEN', 'port_type': 'air', 'distance_km': 26, 'transit_mode': 'truck', 'is_primary': True},
    {'site_id': 'PH_CAV', 'port_id': 'MNL', 'port_type': 'air', 'distance_km': 18, 'transit_mode': 'truck', 'is_primary': True},
    {'site_id': 'VN_HCM', 'port_id': 'SGN', 'port_type': 'air', 'distance_km': 15, 'transit_mode': 'truck', 'is_primary': True},
    {'site_id': 'CR_SJ', 'port_id': 'SJO', 'port_type': 'air', 'distance_km': 14, 'transit_mode': 'truck', 'is_primary': True},
]


def severity_to_score(severity: Any) -> float:
    return float(SEVERITY_SCORE.get(str(severity or '').strip().lower(), 0.0))


def _safe_float(
    value: Any,
    default: float = 0.0,
    lower: float | None = None,
    upper: float | None = None,
) -> float:
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


def score_to_label(score: float) -> str:
    if score >= 0.9:
        return 'critical'
    if score >= 0.7:
        return 'high'
    if score >= 0.4:
        return 'medium'
    if score > 0:
        return 'low'
    return 'normal'


def _normalize_affected_countries(raw_value: Any, country_lookup: Dict[str, Dict[str, str]]) -> List[str]:
    iso2_to_iso3 = {meta['iso2']: iso3 for iso3, meta in country_lookup.items()}
    name_to_iso3 = {meta['name'].upper(): iso3 for iso3, meta in country_lookup.items()}

    if raw_value is None:
        return []
    if isinstance(raw_value, (list, tuple)):
        tokens = [str(v).strip() for v in raw_value if str(v).strip()]
    else:
        cleaned = str(raw_value).strip().strip('{}')
        tokens = [t.strip().strip('"') for t in cleaned.split(',') if t.strip()]

    out = []
    for token in tokens:
        t = token.upper()
        if len(t) == 3 and t in country_lookup:
            out.append(t)
        elif len(t) == 2 and t in iso2_to_iso3:
            out.append(iso2_to_iso3[t])
        elif t in name_to_iso3:
            out.append(name_to_iso3[t])
    return sorted(set(out))


def _normalize_affected_sites(raw_value: Any) -> List[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, (list, tuple)):
        return sorted(set(str(v).strip() for v in raw_value if str(v).strip()))
    cleaned = str(raw_value).strip().strip('{}')
    return sorted(set(s.strip().strip('"') for s in cleaned.split(',') if s.strip()))


def _flight_delay_to_score(delay_minutes: Any, status: Any) -> float:
    status_l = str(status or '').strip().lower()
    if status_l in {'cancelled', 'diverted'}:
        return 1.0

    delay = _safe_float(delay_minutes, default=0.0, lower=0.0)

    if delay >= 180:
        return 1.0
    if delay >= 120:
        return 0.9
    if delay >= 90:
        return 0.75
    if delay >= 60:
        return 0.6
    if delay >= 30:
        return 0.4
    if delay > 0:
        return 0.2
    return 0.0


def ensure_risk_tables(conn) -> None:
    ddl = [
        """
        CREATE TABLE IF NOT EXISTS risk_events (
            event_id BIGSERIAL PRIMARY KEY,
            event_type VARCHAR(50),
            severity VARCHAR(20),
            region VARCHAR(100),
            affected_countries TEXT[],
            affected_sites TEXT[],
            start_date DATE,
            end_date DATE,
            description TEXT,
            source_url VARCHAR(255),
            last_updated TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        "ALTER TABLE risk_events ADD COLUMN IF NOT EXISTS affected_sites TEXT[]",
        """
        CREATE TABLE IF NOT EXISTS transport_disruptions (
            disruption_id BIGSERIAL PRIMARY KEY,
            disruption_type VARCHAR(50),
            location_id VARCHAR(20),
            location_type VARCHAR(20),
            severity VARCHAR(20),
            wait_time_minutes INTEGER,
            description TEXT,
            start_time TIMESTAMPTZ,
            end_time TIMESTAMPTZ,
            source VARCHAR(100),
            last_updated TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS site_port_mapping (
            mapping_id BIGSERIAL PRIMARY KEY,
            site_id VARCHAR(20) REFERENCES fab(fab_id),
            port_id VARCHAR(20),
            port_type VARCHAR(20),
            distance_km INTEGER,
            transit_mode VARCHAR(50),
            is_primary BOOLEAN NOT NULL DEFAULT TRUE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS flight_status (
            flight_iata VARCHAR(20) PRIMARY KEY,
            departure_airport VARCHAR(10),
            arrival_airport VARCHAR(10),
            status VARCHAR(50),
            delay_minutes INTEGER,
            scheduled_departure TIMESTAMPTZ,
            actual_departure TIMESTAMPTZ,
            scheduled_arrival TIMESTAMPTZ,
            actual_arrival TIMESTAMPTZ,
            airline VARCHAR(100),
            source VARCHAR(50) NOT NULL DEFAULT 'aviationstack',
            last_updated TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_risk_event_key ON risk_events(event_type, region, start_date, COALESCE(end_date, DATE '9999-12-31'))",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_transport_disruption_key ON transport_disruptions(disruption_type, location_id, start_time)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_site_port_mapping_key ON site_port_mapping(site_id, port_id, port_type, transit_mode)",
        "CREATE INDEX IF NOT EXISTS idx_risk_events_country_gin ON risk_events USING GIN (affected_countries)",
        "CREATE INDEX IF NOT EXISTS idx_risk_events_sites_gin ON risk_events USING GIN (affected_sites)",
        "CREATE INDEX IF NOT EXISTS idx_transport_disruptions_active ON transport_disruptions(location_id, end_time)",
        "CREATE INDEX IF NOT EXISTS idx_site_port_mapping_site ON site_port_mapping(site_id)",
        "CREATE INDEX IF NOT EXISTS idx_flight_status_last_updated ON flight_status(last_updated DESC)",
        "CREATE INDEX IF NOT EXISTS idx_flight_status_departure_airport ON flight_status(departure_airport)",
        "CREATE INDEX IF NOT EXISTS idx_flight_status_arrival_airport ON flight_status(arrival_airport)",
    ]

    try:
        with conn.cursor() as cur:
            for stmt in ddl:
                cur.execute(stmt)

            cur.execute('SELECT COUNT(*) FROM risk_events')
            if int(cur.fetchone()[0] or 0) == 0:
                for row in DEFAULT_RISK_EVENTS:
                    cur.execute(
                        """
                        INSERT INTO risk_events (event_type, severity, region, affected_countries, affected_sites, start_date, end_date, description, source_url)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (
                            row['event_type'],
                            row['severity'],
                            row['region'],
                            row['affected_countries'],
                            row.get('affected_sites', []),
                            row['start_date'],
                            row['end_date'],
                            row['description'],
                            row['source_url'],
                        ),
                    )

            cur.execute('SELECT COUNT(*) FROM transport_disruptions')
            if int(cur.fetchone()[0] or 0) == 0:
                for row in DEFAULT_TRANSPORT_DISRUPTIONS:
                    cur.execute(
                        """
                        INSERT INTO transport_disruptions (disruption_type, location_id, location_type, severity, wait_time_minutes, description, start_time, end_time, source)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (row['disruption_type'], row['location_id'], row['location_type'], row['severity'], row['wait_time_minutes'], row['description'], row['start_time'], row['end_time'], row['source']),
                    )

            cur.execute('SELECT COUNT(*) FROM site_port_mapping')
            if int(cur.fetchone()[0] or 0) == 0:
                for row in DEFAULT_SITE_PORT_MAPPING:
                    cur.execute(
                        """
                        INSERT INTO site_port_mapping (site_id, port_id, port_type, distance_km, transit_mode, is_primary)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (row['site_id'], row['port_id'], row['port_type'], row['distance_km'], row['transit_mode'], row['is_primary']),
                    )

        conn.commit()
    except Exception:
        conn.rollback()
        raise


def fetch_risk_snapshot(run_query, fabs_df: pd.DataFrame, fab_country: Dict[str, str], country_lookup: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        'updated_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC'),
        'events': pd.DataFrame(),
        'disruptions': pd.DataFrame(),
        'mapping': pd.DataFrame(),
        'flights': pd.DataFrame(),
        'risk_by_site': {},
    }

    try:
        events_df = run_query(
            """
            SELECT event_id, event_type, severity, region, affected_countries, affected_sites,
                   start_date, end_date, description, source_url, last_updated
            FROM risk_events
            WHERE end_date IS NULL OR end_date >= CURRENT_DATE
            ORDER BY
                CASE LOWER(severity)
                    WHEN 'critical' THEN 4
                    WHEN 'high' THEN 3
                    WHEN 'medium' THEN 2
                    WHEN 'low' THEN 1
                    ELSE 0
                END DESC,
                start_date DESC
            """
        )
    except Exception:
        events_df = pd.DataFrame()

    try:
        disruptions_df = run_query(
            """
            SELECT disruption_id, disruption_type, location_id, location_type, severity, wait_time_minutes,
                   description, start_time, end_time, source, last_updated
            FROM transport_disruptions
            WHERE end_time IS NULL OR end_time >= NOW()
            ORDER BY
                CASE LOWER(severity)
                    WHEN 'critical' THEN 4
                    WHEN 'high' THEN 3
                    WHEN 'medium' THEN 2
                    WHEN 'low' THEN 1
                    ELSE 0
                END DESC,
                start_time DESC
            """
        )
    except Exception:
        disruptions_df = pd.DataFrame()

    try:
        mapping_df = run_query(
            """
            SELECT mapping_id, site_id, port_id, port_type, distance_km, transit_mode, is_primary
            FROM site_port_mapping
            """
        )
    except Exception:
        mapping_df = pd.DataFrame()

    try:
        flights_df = run_query(
            """
            SELECT flight_iata, departure_airport, arrival_airport, status, delay_minutes,
                   scheduled_departure, actual_departure, scheduled_arrival, actual_arrival,
                   airline, source, last_updated
            FROM flight_status
            WHERE last_updated >= NOW() - INTERVAL '24 hours'
            ORDER BY delay_minutes DESC NULLS LAST, last_updated DESC
            """
        )
    except Exception:
        flights_df = pd.DataFrame()

    payload['events'] = events_df
    payload['disruptions'] = disruptions_df
    payload['mapping'] = mapping_df
    payload['flights'] = flights_df

    country_event_score: Dict[str, float] = {}
    site_event_score: Dict[str, float] = {}
    if not events_df.empty:
        for _, row in events_df.iterrows():
            score = severity_to_score(row.get('severity'))
            for iso3 in _normalize_affected_countries(row.get('affected_countries'), country_lookup):
                country_event_score[iso3] = max(country_event_score.get(iso3, 0.0), score)
            for site_id in _normalize_affected_sites(row.get('affected_sites')):
                site_event_score[site_id] = max(site_event_score.get(site_id, 0.0), score)

    disruption_by_location: Dict[str, float] = {}
    if not disruptions_df.empty:
        for _, row in disruptions_df.iterrows():
            loc = str(row.get('location_id') or '').strip().upper()
            if not loc:
                continue
            score = severity_to_score(row.get('severity'))
            wait = _safe_float(row.get('wait_time_minutes'), default=0.0, lower=0.0)
            wait_score = min(1.0, wait / 180.0)
            score = max(score, wait_score)
            disruption_by_location[loc] = max(disruption_by_location.get(loc, 0.0), score)

    if not flights_df.empty:
        for _, row in flights_df.iterrows():
            score = _flight_delay_to_score(row.get('delay_minutes'), row.get('status'))
            dep = str(row.get('departure_airport') or '').strip().upper()
            arr = str(row.get('arrival_airport') or '').strip().upper()
            if dep:
                disruption_by_location[dep] = max(disruption_by_location.get(dep, 0.0), score)
            if arr:
                disruption_by_location[arr] = max(disruption_by_location.get(arr, 0.0), score)

    mapping_by_site: Dict[str, List[Dict[str, Any]]] = {}
    if not mapping_df.empty:
        for _, row in mapping_df.iterrows():
            site = str(row.get('site_id') or '').strip()
            port = str(row.get('port_id') or '').strip().upper()
            if site and port:
                mapping_by_site.setdefault(site, []).append(
                    {
                        'port': port,
                        'distance_km': _safe_float(row.get('distance_km'), default=50.0, lower=0.0),
                        'is_primary': bool(row.get('is_primary', True)),
                    }
                )

    risk_by_site: Dict[str, Dict[str, Any]] = {}
    for site_id in fabs_df['fab_id'].dropna().astype(str).unique():
        country_score = _safe_float(country_event_score.get(fab_country.get(site_id), 0.0), lower=0.0, upper=1.0)
        direct_site_score = _safe_float(site_event_score.get(site_id, 0.0), lower=0.0, upper=1.0)
        geopolitical_score = country_score if direct_site_score == 0 else min(1.0, 0.7 * direct_site_score + 0.3 * country_score)

        site_mappings = mapping_by_site.get(site_id, [])
        ports = sorted({entry['port'] for entry in site_mappings})
        port_scores: List[float] = []
        for entry in site_mappings:
            base_port_score = _safe_float(disruption_by_location.get(entry['port'], 0.0), lower=0.0, upper=1.0)
            distance_km = _safe_float(entry.get('distance_km'), default=50.0, lower=0.0)
            if distance_km <= 50:
                distance_factor = 1.0
            elif distance_km <= 150:
                distance_factor = 0.85
            else:
                distance_factor = 0.70
            primary_factor = 1.0 if entry.get('is_primary', True) else 0.9
            port_scores.append(min(1.0, base_port_score * distance_factor * primary_factor))

        if port_scores:
            transport_peak = max(port_scores)
            transport_mean = sum(port_scores) / len(port_scores)
            transport_score = min(1.0, 0.7 * transport_peak + 0.3 * transport_mean)
        else:
            transport_peak = 0.0
            transport_mean = 0.0
            transport_score = 0.0

        total = min(1.0, 0.6 * geopolitical_score + 0.4 * transport_score)

        risk_by_site[site_id] = {
            'score': round(total, 3),
            'label': score_to_label(total),
            'country_score': round(country_score, 3),
            'site_event_score': round(direct_site_score, 3),
            'transport_score': round(transport_score, 3),
            'transport_peak_score': round(transport_peak, 3),
            'transport_mean_score': round(transport_mean, 3),
            'ports': ports,
            'note': SITE_KEY_NOTES.get(site_id, ''),
        }

    payload['risk_by_site'] = risk_by_site
    return payload


def render_risk_sidebar(st, risk_snapshot: Dict[str, Any], error_text: str = '') -> None:
    st.sidebar.markdown('---')
    st.sidebar.header('Real-Time Risk Monitor')

    if error_text:
        st.sidebar.warning(f'Risk monitor unavailable: {error_text}')
        return

    st.sidebar.caption(f"Updated {risk_snapshot.get('updated_at', 'unknown')}")
    events_df = risk_snapshot.get('events')
    disruptions_df = risk_snapshot.get('disruptions')
    flights_df = risk_snapshot.get('flights')

    if isinstance(events_df, pd.DataFrame) and not events_df.empty:
        st.sidebar.subheader('Conflict Alerts')
        critical = events_df[events_df['severity'].astype(str).str.lower().isin(['critical', 'high'])].head(4)
        for _, row in critical.iterrows():
            text = f"{row.get('region', 'Unknown')}: {row.get('description', 'No description')}"
            if str(row.get('severity', '')).lower() == 'critical':
                st.sidebar.error(text)
            else:
                st.sidebar.warning(text)

        maritime = events_df[events_df['event_type'].astype(str).str.lower() == 'maritime'].head(3)
        if not maritime.empty:
            st.sidebar.subheader('Maritime Risks')
            for _, row in maritime.iterrows():
                st.sidebar.info(f"{row.get('region', 'Route')}: {row.get('description', '')}")

    if isinstance(disruptions_df, pd.DataFrame) and not disruptions_df.empty:
        airports = disruptions_df[disruptions_df['location_type'].astype(str).str.lower() == 'airport'].copy()
        if not airports.empty:
            st.sidebar.subheader('Airport Delays')
            airports['wait_time_minutes'] = pd.to_numeric(airports['wait_time_minutes'], errors='coerce').fillna(0)
            for _, row in airports.sort_values('wait_time_minutes', ascending=False).head(5).iterrows():
                wait = int(row['wait_time_minutes'])
                code = row.get('location_id', 'UNK')
                line = f"{code}: {wait}min wait"
                if wait >= 90:
                    st.sidebar.warning(line)
                elif wait >= 60:
                    st.sidebar.info(line)

    if isinstance(flights_df, pd.DataFrame) and not flights_df.empty:
        st.sidebar.subheader('Flight Delays (AviationStack)')
        flights_df = flights_df.copy()
        flights_df['delay_minutes'] = pd.to_numeric(flights_df['delay_minutes'], errors='coerce').fillna(0)
        top_delays = flights_df.sort_values('delay_minutes', ascending=False).head(4)
        for _, row in top_delays.iterrows():
            delay = int(row['delay_minutes'])
            code = str(row.get('flight_iata') or 'unknown')
            route = f"{row.get('departure_airport', '---')}->{row.get('arrival_airport', '---')}"
            line = f"{code} ({route}) {delay}min"
            if delay >= 90:
                st.sidebar.warning(line)
            elif delay > 0:
                st.sidebar.info(line)


def query_risk_signals(fab_id: str, targets: List[str], risk_by_site: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    risk_by_site = risk_by_site or {}
    source = risk_by_site.get(fab_id, {'score': 0.0, 'label': 'normal'})

    target_rows = []
    for target in targets:
        site_risk = risk_by_site.get(target, {'score': 0.0, 'label': 'normal', 'ports': []})
        target_rows.append(
            {
                'site_id': target,
                'risk_score': _safe_float(site_risk.get('score', 0.0), lower=0.0, upper=1.0),
                'risk_label': site_risk.get('label', 'normal'),
                'ports': site_risk.get('ports', []),
                'note': site_risk.get('note', ''),
            }
        )

    target_rows.sort(key=lambda r: r['risk_score'], reverse=True)
    return {
        'source_site': fab_id,
        'source_risk_score': _safe_float(source.get('score', 0.0), lower=0.0, upper=1.0),
        'source_risk_label': source.get('label', 'normal'),
        'targets': target_rows,
    }
