#!/usr/bin/env python3
"""Seed the local Postgres instance with demo semiconductor data + multi-hop routing model.

Reads CSVs from semiconductor_data/ and loads them into the database referenced by
PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import os
import time
from collections import Counter
from datetime import date, datetime
from math import atan2, cos, radians, sin, sqrt
from pathlib import Path
from typing import Callable, Dict, List, Tuple

import psycopg2
from psycopg2 import extras

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "semiconductor_data"

if load_dotenv:
    env_file = ROOT / ".env"
    if env_file.exists():
        load_dotenv(env_file)


def conv_int(value: str) -> int | None:
    value = (value or "").strip()
    return int(value) if value else None


def conv_float(value: str) -> float | None:
    value = (value or "").strip()
    return float(value) if value else None


def conv_date(value: str) -> datetime | None:
    value = (value or "").strip()
    return datetime.strptime(value, "%Y-%m-%d").date() if value else None


def conv_text(value: str) -> str | None:
    value = (value or "").strip()
    return value or None


TABLES: List[Dict] = [
    {
        "name": "technology",
        "file": "technology.csv",
        "columns": [
            "tech_id",
            "tech_name",
            "category",
            "node_nm",
            "transistor_type",
            "process_technologies",
            "material",
        ],
        "key_columns": ["tech_id"],
        "converters": {"node_nm": conv_int},
        "ddl": """
            CREATE TABLE IF NOT EXISTS technology (
                tech_id TEXT PRIMARY KEY,
                tech_name TEXT,
                category TEXT,
                node_nm INTEGER,
                transistor_type TEXT,
                process_technologies TEXT,
                material TEXT
            )
        """,
    },
    {
        "name": "fab",
        "file": "fab.csv",
        "columns": [
            "fab_id",
            "name",
            "location",
            "owner",
            "status",
            "start_year",
            "business_model",
            "clean_room_size_sqm",
            "total_wafer_starts_per_month",
            "site_type",
        ],
        "key_columns": ["fab_id"],
        "converters": {
            "start_year": conv_int,
            "clean_room_size_sqm": conv_int,
            "total_wafer_starts_per_month": conv_int,
        },
        "ddl": """
            CREATE TABLE IF NOT EXISTS fab (
                fab_id TEXT PRIMARY KEY,
                name TEXT,
                location TEXT,
                owner TEXT,
                status TEXT,
                start_year INTEGER,
                business_model TEXT,
                clean_room_size_sqm INTEGER,
                total_wafer_starts_per_month INTEGER,
                site_type TEXT
            )
        """,
    },
    {
        "name": "fab_capability",
        "file": "fab_capability.csv",
        "columns": ["fab_id", "tech_id", "wafer_size_mm", "status"],
        "key_columns": ["fab_id", "tech_id"],
        "converters": {"wafer_size_mm": conv_int},
        "ddl": """
            CREATE TABLE IF NOT EXISTS fab_capability (
                fab_id TEXT REFERENCES fab(fab_id),
                tech_id TEXT REFERENCES technology(tech_id),
                wafer_size_mm INTEGER,
                status TEXT,
                PRIMARY KEY (fab_id, tech_id)
            )
        """,
    },
    {
        "name": "operational_constraint",
        "file": "operational_constraint.csv",
        "columns": [
            "constraint_id",
            "constraint_type",
            "description",
            "rule",
            "affected_fab_id",
            "affected_tech_id",
        ],
        "key_columns": ["constraint_id"],
        "ddl": """
            CREATE TABLE IF NOT EXISTS operational_constraint (
                constraint_id TEXT PRIMARY KEY,
                constraint_type TEXT,
                description TEXT,
                rule TEXT,
                affected_fab_id TEXT,
                affected_tech_id TEXT
            )
        """,
    },
    {
        "name": "process_flow_stage",
        "file": "process_flow_stage.csv",
        "columns": [
            "stage_id",
            "stage_name",
            "description",
            "site_type",
            "typical_location",
        ],
        "key_columns": ["stage_id"],
        "ddl": """
            CREATE TABLE IF NOT EXISTS process_flow_stage (
                stage_id TEXT PRIMARY KEY,
                stage_name TEXT,
                description TEXT,
                site_type TEXT,
                typical_location TEXT
            )
        """,
    },
    {
        "name": "production_lot",
        "file": "production_lot.csv",
        "columns": [
            "lot_id",
            "fab_id",
            "tech_id",
            "start_date",
            "wafers_started",
            "yield_pct",
            "lot_hist",
            "proc_node",
            "status",
        ],
        "key_columns": ["lot_id"],
        "converters": {
            "start_date": conv_date,
            "wafers_started": conv_int,
            "yield_pct": conv_float,
        },
        "ddl": """
            CREATE TABLE IF NOT EXISTS production_lot (
                lot_id TEXT PRIMARY KEY,
                fab_id TEXT REFERENCES fab(fab_id),
                tech_id TEXT REFERENCES technology(tech_id),
                start_date DATE,
                wafers_started INTEGER,
                yield_pct NUMERIC,
                lot_hist TEXT,
                proc_node TEXT,
                status TEXT
            )
        """,
    },
    {
        "name": "tool",
        "file": "tool.csv",
        "columns": ["tool_id", "tool_name", "tool_type", "fab_id", "qualification_status"],
        "key_columns": ["tool_id"],
        "ddl": """
            CREATE TABLE IF NOT EXISTS tool (
                tool_id TEXT PRIMARY KEY,
                tool_name TEXT,
                tool_type TEXT,
                fab_id TEXT REFERENCES fab(fab_id),
                qualification_status TEXT
            )
        """,
    },
    {
        "name": "tool_qualification",
        "file": "tool_qualification.csv",
        "columns": ["tool_id", "tech_id", "qualification_date", "expiry_date"],
        "key_columns": ["tool_id", "tech_id"],
        "converters": {
            "qualification_date": conv_date,
            "expiry_date": conv_date,
        },
        "ddl": """
            CREATE TABLE IF NOT EXISTS tool_qualification (
                tool_id TEXT REFERENCES tool(tool_id),
                tech_id TEXT REFERENCES technology(tech_id),
                qualification_date DATE,
                expiry_date DATE,
                PRIMARY KEY (tool_id, tech_id)
            )
        """,
    },
]


# User-verified site capability map (source of truth for runtime routing model).
VERIFIED_CAPABILITIES: Dict[str, List[str]] = {
    'AZ_F12': ['wafer_fab'],
    'AZ_F22': ['wafer_fab'],
    'AZ_F32': ['wafer_fab'],
    'AZ_F42': ['wafer_fab'],
    'AZ_F52': ['wafer_fab'],
    'AZ_F62': ['wafer_fab'],
    'NM_F11X': ['wafer_fab'],
    'NM_RR': ['assembly', 'test', 'packaging', 'advanced_packaging'],
    'OR_D1X': ['r_d', 'wafer_fab', 'process_development'],
    'OR_D1D': ['wafer_fab', 'process_development', 'sort'],
    'OR_D1C': ['wafer_fab'],
    'OH_1': ['wafer_fab'],
    'IE_F24': ['wafer_fab'],
    'IE_F34': ['wafer_fab'],
    'IL_F28': ['wafer_fab'],
    'IL_F28a': ['wafer_fab'],
    'IL_F38': ['wafer_fab'],
    'IL_JS': ['assembly', 'test'],
    'CN_DL68': ['wafer_fab', 'memory'],
    'CN_CD': ['assembly', 'test', 'packaging'],
    'CN_SH': ['assembly', 'test', 'packaging'],
    'MY_KUL': ['assembly', 'test', 'packaging'],
    'MY_PG': ['assembly', 'test', 'packaging'],
    'VN_HCM': ['assembly', 'test', 'packaging'],
    'PH_CAV': ['assembly', 'test', 'packaging'],
    'CR_SJ': ['assembly', 'test', 'packaging', 'services'],
    # Fictional site is allowed only as demo-only metadata.
    'US_AT': ['assembly', 'test', 'packaging'],
}

CAPABILITY_DESCRIPTIONS: Dict[str, str] = {
    'wafer_fab': 'Front-end wafer fabrication capability.',
    'sort': 'Wafer probe and sort capability.',
    'dicing': 'Wafer dicing capability.',
    'assembly': 'Package assembly capability.',
    'test': 'Electrical/final test capability.',
    'packaging': 'Package finishing capability.',
    'distribution': 'Distribution/shipment capability.',
    'advanced_packaging': 'Advanced packaging capability.',
    'process_development': 'Process development capability.',
    'r_d': 'Research and development capability.',
    'design': 'Product design capability.',
    'sales_marketing': 'Sales and marketing capability.',
    'memory': 'Memory fab specialization.',
    'services': 'Shared service capability.',
}

ROUTING_FLOW_ROWS = [
    ('FLOW_V1_CORE', 'cpu_general', 'v1', True),
    ('FLOW_V1_1_EXPLICIT', 'cpu_general', 'v1.1', False),
]

ROUTING_FLOW_STEPS = [
    ('FLOW_V1_CORE', 1, 'wafer_fab', True, 48),
    ('FLOW_V1_CORE', 2, 'assembly', True, 72),
    ('FLOW_V1_CORE', 3, 'test', True, 72),
    ('FLOW_V1_CORE', 4, 'packaging', True, 48),

    ('FLOW_V1_1_EXPLICIT', 1, 'wafer_fab', True, 48),
    ('FLOW_V1_1_EXPLICIT', 2, 'sort', True, 48),
    ('FLOW_V1_1_EXPLICIT', 3, 'dicing', True, 72),
    ('FLOW_V1_1_EXPLICIT', 4, 'assembly', True, 72),
    ('FLOW_V1_1_EXPLICIT', 5, 'test', True, 72),
    ('FLOW_V1_1_EXPLICIT', 6, 'packaging', True, 48),
]

LOCATION_COORDS: Dict[str, Tuple[float, float]] = {
    'AZ_F12': (33.3, -111.8), 'AZ_F22': (33.3, -111.8), 'AZ_F32': (33.3, -111.8),
    'AZ_F42': (33.3, -111.8), 'AZ_F52': (33.3, -111.8), 'AZ_F62': (33.3, -111.8),
    'NM_F11X': (35.23, -106.66), 'NM_RR': (35.23, -106.66),
    'OR_D1X': (45.52, -122.99), 'OR_D1D': (45.52, -122.99), 'OR_D1C': (45.52, -122.99),
    'OH_1': (40.08, -82.81),
    'IE_F24': (53.3, -6.5), 'IE_F34': (53.3, -6.5),
    'IL_F28': (31.61, 34.77), 'IL_F28a': (31.61, 34.77), 'IL_F38': (31.61, 34.77), 'IL_JS': (31.77, 35.22),
    'CN_DL68': (38.91, 121.61), 'CN_SH': (31.23, 121.47), 'CN_CD': (30.57, 104.07),
    'MY_KUL': (5.41, 100.62), 'MY_PG': (5.41, 100.33),
    'PH_CAV': (14.28, 120.87), 'VN_HCM': (10.82, 106.63), 'CR_SJ': (9.93, -84.08),
}


def ensure_tables(cur) -> None:
    for table in TABLES:
        cur.execute(table['ddl'])


def ensure_routing_tables(cur) -> None:
    cur.execute("ALTER TABLE fab ADD COLUMN IF NOT EXISTS capabilities TEXT[]")
    cur.execute("ALTER TABLE production_lot ADD COLUMN IF NOT EXISTS flow_id TEXT")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS capability_catalog (
            capability_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS site_capability (
            site_id TEXT REFERENCES fab(fab_id),
            capability_id TEXT REFERENCES capability_catalog(capability_id),
            confidence NUMERIC NOT NULL,
            source TEXT NOT NULL,
            valid_from DATE NOT NULL DEFAULT CURRENT_DATE,
            valid_to DATE,
            routing_eligible BOOLEAN NOT NULL DEFAULT TRUE,
            demo_only BOOLEAN NOT NULL DEFAULT FALSE,
            PRIMARY KEY (site_id, capability_id, valid_from)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS process_flow (
            flow_id TEXT PRIMARY KEY,
            product_type TEXT NOT NULL,
            version TEXT NOT NULL,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS process_flow_step (
            flow_id TEXT REFERENCES process_flow(flow_id) ON DELETE CASCADE,
            step_order INTEGER NOT NULL,
            capability_id TEXT REFERENCES capability_catalog(capability_id),
            required BOOLEAN NOT NULL DEFAULT TRUE,
            max_wait_hours INTEGER,
            PRIMARY KEY (flow_id, step_order)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS site_lane (
            from_site TEXT REFERENCES fab(fab_id),
            to_site TEXT REFERENCES fab(fab_id),
            mode TEXT NOT NULL,
            transit_hours NUMERIC NOT NULL,
            cost_usd NUMERIC NOT NULL,
            risk_score NUMERIC NOT NULL,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            PRIMARY KEY (from_site, to_site, mode)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS site_operation_capacity (
            site_id TEXT REFERENCES fab(fab_id),
            capability_id TEXT REFERENCES capability_catalog(capability_id),
            period_start DATE NOT NULL,
            capacity_units NUMERIC NOT NULL,
            utilized_units NUMERIC NOT NULL,
            PRIMARY KEY (site_id, capability_id, period_start)
        )
        """
    )


def _validate_no_duplicate_keys(table_cfg: Dict, rows: List[tuple]) -> None:
    key_columns: List[str] = table_cfg.get('key_columns', [])
    if not key_columns:
        return

    col_idx = {name: idx for idx, name in enumerate(table_cfg['columns'])}
    key_positions = [col_idx[name] for name in key_columns]
    key_values = [tuple(row[pos] for pos in key_positions) for row in rows]
    counts = Counter(key_values)
    dupes = [(k, c) for k, c in counts.items() if c > 1]
    if not dupes:
        return

    sample = ', '.join(f"{k} x{c}" for k, c in dupes[:5])
    raise ValueError(f"Duplicate key rows in {table_cfg['file']} for key {key_columns}: {sample}")


def load_table(cur, table_cfg) -> None:
    csv_path = DATA_DIR / table_cfg['file']
    if not csv_path.exists():
        print(f"[skip] {csv_path} missing")
        return

    converters: Dict[str, Callable[[str], object]] = table_cfg.get('converters', {})
    rows = []
    with csv_path.open(newline='', encoding='utf-8') as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            row = []
            for col in table_cfg['columns']:
                value = raw.get(col)
                conv = converters.get(col, conv_text)
                row.append(conv(value) if value is not None else None)
            rows.append(tuple(row))

    if not rows:
        print(f"[warn] No rows found in {csv_path}")
        return

    _validate_no_duplicate_keys(table_cfg, rows)
    cur.execute(f"TRUNCATE {table_cfg['name']} RESTART IDENTITY CASCADE")
    extras.execute_values(
        cur,
        f"INSERT INTO {table_cfg['name']} ({', '.join(table_cfg['columns'])}) VALUES %s",
        rows,
    )
    print(f"[ok] Loaded {len(rows)} rows into {table_cfg['name']}")


def _stable_utilization_factor(site_id: str, capability: str) -> float:
    digest = hashlib.md5(f"{site_id}|{capability}".encode('utf-8')).hexdigest()
    bucket = int(digest[:6], 16) % 35
    return 0.52 + (bucket / 100.0)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return r * c


def seed_routing_entities(cur) -> None:
    # Catalog
    catalog_rows = [(cap, cap.replace('_', ' ').title(), CAPABILITY_DESCRIPTIONS.get(cap)) for cap in sorted(CAPABILITY_DESCRIPTIONS)]
    extras.execute_values(
        cur,
        """
        INSERT INTO capability_catalog (capability_id, name, description)
        VALUES %s
        ON CONFLICT (capability_id) DO UPDATE SET
            name = EXCLUDED.name,
            description = EXCLUDED.description
        """,
        catalog_rows,
    )

    # Site capabilities
    cur.execute(
        """
        SELECT fab_id, status, site_type, total_wafer_starts_per_month
        FROM fab
        ORDER BY fab_id
        """
    )
    fab_rows = cur.fetchall()

    today = datetime.utcnow().date()
    site_cap_rows = []
    for fab_id, status, site_type, capacity in fab_rows:
        caps = VERIFIED_CAPABILITIES.get(fab_id, [])
        if not caps:
            continue

        status_l = str(status or '').strip().lower()
        is_under_construction = ('under construction' in status_l) or ('planned' in status_l)
        is_demo_only = str(fab_id).startswith('US_AT')

        for cap in caps:
            confidence = 0.96 if cap in {'wafer_fab', 'assembly', 'test', 'packaging'} else 0.88
            site_cap_rows.append(
                (
                    fab_id,
                    cap,
                    confidence,
                    'intel_inferred_verified',
                    today,
                    None,
                    (not is_under_construction) and (not is_demo_only),
                    is_demo_only,
                )
            )

    cur.execute('TRUNCATE site_capability RESTART IDENTITY')
    extras.execute_values(
        cur,
        """
        INSERT INTO site_capability
            (site_id, capability_id, confidence, source, valid_from, valid_to, routing_eligible, demo_only)
        VALUES %s
        """,
        site_cap_rows,
    )

    # Mirror to fab.capabilities for UI/filtering convenience.
    cur.execute(
        """
        UPDATE fab f
        SET capabilities = subq.capabilities
        FROM (
            SELECT site_id, ARRAY_AGG(DISTINCT capability_id ORDER BY capability_id) AS capabilities
            FROM site_capability
            WHERE valid_to IS NULL
            GROUP BY site_id
        ) subq
        WHERE f.fab_id = subq.site_id
        """
    )

    # Flows + steps
    extras.execute_values(
        cur,
        """
        INSERT INTO process_flow (flow_id, product_type, version, active)
        VALUES %s
        ON CONFLICT (flow_id) DO UPDATE SET
            product_type = EXCLUDED.product_type,
            version = EXCLUDED.version,
            active = EXCLUDED.active
        """,
        ROUTING_FLOW_ROWS,
    )

    cur.execute('TRUNCATE process_flow_step RESTART IDENTITY')
    extras.execute_values(
        cur,
        """
        INSERT INTO process_flow_step (flow_id, step_order, capability_id, required, max_wait_hours)
        VALUES %s
        """,
        ROUTING_FLOW_STEPS,
    )

    cur.execute("UPDATE production_lot SET flow_id = 'FLOW_V1_CORE' WHERE flow_id IS NULL")

    # Lane generation from active routing-eligible sites.
    cur.execute(
        """
        SELECT f.fab_id, f.location, f.status, f.site_type,
               COALESCE(f.total_wafer_starts_per_month, 12000) AS nominal_capacity
        FROM fab f
        """
    )
    all_sites = cur.fetchall()
    site_meta = {sid: {'location': loc, 'status': st, 'site_type': stype, 'nominal_capacity': float(cap or 12000)} for sid, loc, st, stype, cap in all_sites}

    cur.execute(
        """
        SELECT site_id, capability_id
        FROM site_capability
        WHERE routing_eligible = TRUE AND demo_only = FALSE AND valid_to IS NULL
        """
    )
    cap_rows = cur.fetchall()
    cap_to_sites: Dict[str, set] = {}
    site_to_caps: Dict[str, set] = {}
    for sid, cap in cap_rows:
        cap_to_sites.setdefault(cap, set()).add(sid)
        site_to_caps.setdefault(sid, set()).add(cap)

    transitions = []
    for flow_id, _product, _ver, active in ROUTING_FLOW_ROWS:
        if not active:
            continue
        steps = [row for row in ROUTING_FLOW_STEPS if row[0] == flow_id]
        steps.sort(key=lambda r: r[1])
        for i in range(len(steps) - 1):
            transitions.append((steps[i][2], steps[i + 1][2]))

    transition_pairs = sorted(set(transitions))

    lane_rows = []
    for from_cap, to_cap in transition_pairs:
        from_sites = sorted(cap_to_sites.get(from_cap, set()))
        to_sites = sorted(cap_to_sites.get(to_cap, set()))

        for from_site in from_sites:
            for to_site in to_sites:
                if from_site not in site_meta or to_site not in site_meta:
                    continue

                same_site = from_site == to_site
                from_coord = LOCATION_COORDS.get(from_site)
                to_coord = LOCATION_COORDS.get(to_site)

                if same_site:
                    mode = 'internal'
                    transit_hours = 2.0
                    cost_usd = 220.0
                    risk_score = 0.03
                else:
                    if not from_coord or not to_coord:
                        continue
                    distance_km = _haversine_km(from_coord[0], from_coord[1], to_coord[0], to_coord[1])
                    if distance_km > 18000:
                        continue
                    mode = 'air' if distance_km > 650 else 'truck'
                    speed = 700.0 if mode == 'air' else 60.0
                    transit_hours = max(6.0, distance_km / speed + (8.0 if mode == 'air' else 4.0))
                    cost_usd = max(500.0, distance_km * (2.1 if mode == 'air' else 1.0) + 900.0)
                    intl = site_meta[from_site]['location'] != site_meta[to_site]['location']
                    risk_score = 0.08 + (0.12 if intl else 0.04) + (0.06 if mode == 'air' else 0.03)

                lane_rows.append(
                    (
                        from_site,
                        to_site,
                        mode,
                        round(transit_hours, 2),
                        round(cost_usd, 2),
                        round(min(risk_score, 0.95), 4),
                        True,
                    )
                )

    dedup_lanes: Dict[Tuple[str, str, str], Tuple[str, str, str, float, float, float, bool]] = {}
    for row in lane_rows:
        key = (row[0], row[1], row[2])
        if key not in dedup_lanes:
            dedup_lanes[key] = row
            continue
        # Keep the cheaper/faster alternative for identical keys.
        prev = dedup_lanes[key]
        prev_rank = float(prev[3]) + (float(prev[4]) / 1000.0)
        cur_rank = float(row[3]) + (float(row[4]) / 1000.0)
        if cur_rank < prev_rank:
            dedup_lanes[key] = row
    lane_rows = list(dedup_lanes.values())

    cur.execute('TRUNCATE site_lane RESTART IDENTITY')
    extras.execute_values(
        cur,
        """
        INSERT INTO site_lane (from_site, to_site, mode, transit_hours, cost_usd, risk_score, active)
        VALUES %s
        ON CONFLICT (from_site, to_site, mode) DO UPDATE SET
            transit_hours = EXCLUDED.transit_hours,
            cost_usd = EXCLUDED.cost_usd,
            risk_score = EXCLUDED.risk_score,
            active = EXCLUDED.active
        """,
        lane_rows,
    )

    period_start = date.today().replace(day=1)
    cap_multiplier = {
        'wafer_fab': 1.0,
        'sort': 0.9,
        'dicing': 0.85,
        'assembly': 0.85,
        'test': 0.8,
        'packaging': 0.78,
        'advanced_packaging': 0.72,
        'distribution': 0.7,
        'process_development': 0.45,
        'r_d': 0.35,
        'memory': 0.8,
        'services': 0.5,
    }

    capacity_rows = []
    for site_id, caps in site_to_caps.items():
        nominal = site_meta.get(site_id, {}).get('nominal_capacity', 12000.0)
        for cap in sorted(caps):
            mult = cap_multiplier.get(cap, 0.75)
            capacity_units = max(2000.0, nominal * mult)
            utilization_factor = _stable_utilization_factor(site_id, cap)
            utilized_units = min(capacity_units * utilization_factor, capacity_units * 0.97)
            capacity_rows.append(
                (
                    site_id,
                    cap,
                    period_start,
                    round(capacity_units, 2),
                    round(utilized_units, 2),
                )
            )

    cur.execute('TRUNCATE site_operation_capacity RESTART IDENTITY')
    extras.execute_values(
        cur,
        """
        INSERT INTO site_operation_capacity
            (site_id, capability_id, period_start, capacity_units, utilized_units)
        VALUES %s
        """,
        capacity_rows,
    )

    print(f"[ok] Routing seed: capabilities={len(site_cap_rows)}, lanes={len(lane_rows)}, capacities={len(capacity_rows)}")


def validate_routing_seed(cur) -> None:
    # 1) No duplicate site-capability keys
    cur.execute(
        """
        SELECT site_id, capability_id, valid_from, COUNT(*)
        FROM site_capability
        GROUP BY site_id, capability_id, valid_from
        HAVING COUNT(*) > 1
        """
    )
    dup_caps = cur.fetchall()
    if dup_caps:
        raise ValueError(f"Duplicate site_capability rows found: {dup_caps[:3]}")

    # 2) Every active required flow step has eligible sites.
    cur.execute(
        """
        SELECT pfs.flow_id, pfs.step_order, pfs.capability_id, COUNT(sc.site_id) AS eligible_sites
        FROM process_flow_step pfs
        JOIN process_flow pf ON pf.flow_id = pfs.flow_id
        LEFT JOIN site_capability sc
          ON sc.capability_id = pfs.capability_id
         AND sc.routing_eligible = TRUE
         AND sc.demo_only = FALSE
         AND sc.valid_to IS NULL
        WHERE pf.active = TRUE
          AND pfs.required = TRUE
        GROUP BY pfs.flow_id, pfs.step_order, pfs.capability_id
        HAVING COUNT(sc.site_id) = 0
        ORDER BY pfs.flow_id, pfs.step_order
        """
    )
    missing_step_sites = cur.fetchall()
    if missing_step_sites:
        raise ValueError(f"Missing eligible sites for required steps: {missing_step_sites}")

    # 3) Every required step transition has at least one active lane.
    cur.execute(
        """
        SELECT pfs.flow_id, pfs.step_order, pfs.capability_id, pfs.required
        FROM process_flow_step pfs
        JOIN process_flow pf ON pf.flow_id = pfs.flow_id
        WHERE pf.active = TRUE
        ORDER BY pfs.flow_id, pfs.step_order
        """
    )
    flow_steps = cur.fetchall()

    steps_by_flow: Dict[str, List[Tuple[int, str, bool]]] = {}
    for flow_id, step_order, cap_id, required in flow_steps:
        steps_by_flow.setdefault(flow_id, []).append((int(step_order), str(cap_id), bool(required)))

    cur.execute(
        """
        SELECT site_id, capability_id
        FROM site_capability
        WHERE routing_eligible = TRUE AND demo_only = FALSE AND valid_to IS NULL
        """
    )
    cap_rows = cur.fetchall()
    cap_to_sites: Dict[str, set] = {}
    for site_id, cap_id in cap_rows:
        cap_to_sites.setdefault(str(cap_id), set()).add(str(site_id))

    cur.execute("SELECT from_site, to_site FROM site_lane WHERE active = TRUE")
    lane_edges = {(str(a), str(b)) for a, b in cur.fetchall()}

    missing_transitions = []
    for flow_id, steps in steps_by_flow.items():
        ordered = sorted(steps, key=lambda x: x[0])
        req = [(o, c) for o, c, r in ordered if r]
        for idx in range(len(req) - 1):
            from_cap = req[idx][1]
            to_cap = req[idx + 1][1]
            from_sites = cap_to_sites.get(from_cap, set())
            to_sites = cap_to_sites.get(to_cap, set())
            found = False
            for fs in from_sites:
                for ts in to_sites:
                    if (fs, ts) in lane_edges:
                        found = True
                        break
                if found:
                    break
            if not found:
                missing_transitions.append((flow_id, from_cap, to_cap))

    if missing_transitions:
        raise ValueError(f"Missing active lanes for required transitions: {missing_transitions}")

    print('[ok] Routing validations passed: duplicates=0, required-steps-covered=1, transitions-covered=1')


def _validate_csv_duplicates(table_cfg: Dict) -> None:
    csv_path = DATA_DIR / table_cfg['file']
    if not csv_path.exists():
        return

    key_columns: List[str] = table_cfg.get('key_columns', [])
    if not key_columns:
        return

    seen = Counter()
    with csv_path.open(newline='', encoding='utf-8') as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            key = tuple((raw.get(col) or '').strip() for col in key_columns)
            seen[key] += 1

    dupes = [(k, c) for k, c in seen.items() if c > 1]
    if dupes:
        sample = ', '.join(f"{k} x{c}" for k, c in dupes[:5])
        raise ValueError(f"Duplicate key rows in {table_cfg['file']} for key {key_columns}: {sample}")


def copy_table_from_csv(cur, table_cfg) -> None:
    csv_path = DATA_DIR / table_cfg['file']
    if not csv_path.exists():
        print(f"[skip] {csv_path} missing")
        return

    _validate_csv_duplicates(table_cfg)
    cur.execute(f"TRUNCATE {table_cfg['name']} RESTART IDENTITY CASCADE")

    cols = ', '.join(table_cfg['columns'])
    copy_sql = f"COPY {table_cfg['name']} ({cols}) FROM STDIN WITH (FORMAT csv, HEADER true, NULL '')"
    with csv_path.open('r', encoding='utf-8', newline='') as f:
        cur.copy_expert(copy_sql, f)

    cur.execute(f"SELECT COUNT(*) FROM {table_cfg['name']}")
    count = int(cur.fetchone()[0] or 0)
    print(f"[ok] Copied {count} rows into {table_cfg['name']}")


def apply_verified_capability_updates(cur) -> None:
    for fab_id, caps in VERIFIED_CAPABILITIES.items():
        cur.execute(
            "UPDATE fab SET capabilities = %s WHERE fab_id = %s",
            (caps, fab_id),
        )
    print(f"[ok] Applied verified capability updates for {len(VERIFIED_CAPABILITIES)} sites")


def assign_lot_flows(cur) -> None:
    cur.execute("UPDATE production_lot SET flow_id = 'FLOW_V1_CORE' WHERE flow_id IS NULL")

    # Keep explicit flow for sort/dicing capable Oregon D1D demonstration lots.
    cur.execute(
        """
        UPDATE production_lot
        SET flow_id = 'FLOW_V1_1_EXPLICIT'
        WHERE fab_id = 'OR_D1D'
          AND flow_id = 'FLOW_V1_CORE'
        """
    )

    cur.execute(
        """
        INSERT INTO production_lot
            (lot_id, fab_id, tech_id, start_date, wafers_started, yield_pct, lot_hist, proc_node, status, flow_id)
        VALUES
            ('L2603-MH-AZ42', 'AZ_F42', 'N7', CURRENT_DATE - INTERVAL '2 day', 26, 89.2, 'OP1:OK;OP2:OK;OP3:WIP', 'P7', 'In Progress', 'FLOW_V1_CORE'),
            ('L2603-MH-IE34', 'IE_F34', 'N4', CURRENT_DATE - INTERVAL '1 day', 20, 90.1, 'OP1:OK;OP2:OK;OP3:WIP', 'I4', 'In Progress', 'FLOW_V1_CORE'),
            ('L2603-MH-OR1D', 'OR_D1D', 'N14', CURRENT_DATE - INTERVAL '3 day', 18, 92.0, 'OP1:OK;OP2:OK;OP3:OK', 'P14', 'Completed', 'FLOW_V1_1_EXPLICIT')
        ON CONFLICT (lot_id) DO NOTHING
        """
    )

    cur.execute("SELECT COUNT(*) FROM production_lot WHERE flow_id IS NOT NULL")
    assigned = int(cur.fetchone()[0] or 0)
    print(f"[ok] Flow assignment complete for {assigned} lots")


def run_seed_step(cur, step: str, use_copy: bool = True) -> None:
    step = step.lower()

    if step in {'a', 'tables'}:
        ensure_tables(cur)
        ensure_routing_tables(cur)
        print('[ok] Step A complete: schema ensured')
        return

    if step in {'b', 'core'}:
        ensure_tables(cur)
        loaders = copy_table_from_csv if use_copy else load_table
        for table in TABLES:
            loaders(cur, table)
        print('[ok] Step B complete: core data loaded')
        return

    if step in {'c', 'capabilities'}:
        ensure_routing_tables(cur)
        apply_verified_capability_updates(cur)
        print('[ok] Step C complete: capabilities updated')
        return

    if step in {'d', 'routing'}:
        ensure_routing_tables(cur)
        seed_routing_entities(cur)
        validate_routing_seed(cur)
        print('[ok] Step D complete: routing model seeded + validated')
        return

    if step in {'e', 'lots'}:
        ensure_routing_tables(cur)
        assign_lot_flows(cur)
        print('[ok] Step E complete: lot flow assignments')
        return

    if step in {'validate'}:
        validate_routing_seed(cur)
        print('[ok] Validation complete')
        return

    raise ValueError(f'Unknown step: {step}')




def main() -> None:
    parser = argparse.ArgumentParser(description='Seed semiconductor + routing v2 database in steps.')
    parser.add_argument(
        '--step',
        default='all',
        choices=['all', 'a', 'b', 'c', 'd', 'e', 'tables', 'core', 'capabilities', 'routing', 'lots', 'validate'],
        help='Run one step (A-E) or all steps sequentially.',
    )
    parser.add_argument(
        '--no-copy',
        action='store_true',
        help='Use execute_values row loader for core tables instead of COPY.',
    )
    args = parser.parse_args()

    required = ['PGHOST', 'PGPORT', 'PGDATABASE', 'PGUSER', 'PGPASSWORD']
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise SystemExit(f"Missing environment variables: {', '.join(missing)}")

    conn = psycopg2.connect(
        host=os.getenv('PGHOST'),
        port=int(os.getenv('PGPORT')),
        dbname=os.getenv('PGDATABASE'),
        user=os.getenv('PGUSER'),
        password=os.getenv('PGPASSWORD'),
        options='-c statement_timeout=0 -c lock_timeout=0',
    )
    conn.autocommit = False

    step_order = ['a', 'b', 'c', 'd', 'e'] if args.step == 'all' else [args.step]

    try:
        with conn:
            with conn.cursor() as cur:
                for step in step_order:
                    start = time.time()
                    run_seed_step(cur, step=step, use_copy=(not args.no_copy))
                    elapsed = time.time() - start
                    print(f"[time] Step {step.upper()} finished in {elapsed:.2f}s")
        print('Database seed complete (stepwise core + routing v2).')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
