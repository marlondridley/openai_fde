#!/usr/bin/env python3
"""Seed the local Postgres instance with demo semiconductor data.

Reads CSVs from semiconductor_data/ and loads them into the database
referenced by PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD.
"""

from __future__ import annotations

import csv
import os
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List

import psycopg2
from psycopg2 import extras

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional helper
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


def ensure_tables(cur) -> None:
    for table in TABLES:
        cur.execute(table["ddl"])


def _validate_no_duplicate_keys(table_cfg: Dict, rows: List[tuple]) -> None:
    key_columns: List[str] = table_cfg.get("key_columns", [])
    if not key_columns:
        return

    col_idx = {name: idx for idx, name in enumerate(table_cfg["columns"])}
    key_positions = [col_idx[name] for name in key_columns]
    key_values = [tuple(row[pos] for pos in key_positions) for row in rows]
    counts = Counter(key_values)
    dupes = [(k, c) for k, c in counts.items() if c > 1]
    if not dupes:
        return

    sample = ", ".join(f"{k} x{c}" for k, c in dupes[:5])
    raise ValueError(
        f"Duplicate key rows in {table_cfg['file']} for key {key_columns}: {sample}"
    )


def load_table(cur, table_cfg) -> None:
    csv_path = DATA_DIR / table_cfg["file"]
    if not csv_path.exists():
        print(f"[skip] {csv_path} missing")
        return

    converters: Dict[str, Callable[[str], object]] = table_cfg.get("converters", {})
    rows = []
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            row = []
            for col in table_cfg["columns"]:
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


def main() -> None:
    required = ["PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD"]
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise SystemExit(f"Missing environment variables: {', '.join(missing)}")

    conn = psycopg2.connect(
        host=os.getenv("PGHOST"),
        port=int(os.getenv("PGPORT")),
        dbname=os.getenv("PGDATABASE"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )
    conn.autocommit = False

    with conn:
        with conn.cursor() as cur:
            ensure_tables(cur)
            for table in TABLES:
                load_table(cur, table)

    conn.close()
    print("Database seed complete.")


if __name__ == "__main__":
    main()
