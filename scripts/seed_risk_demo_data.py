#!/usr/bin/env python3
"""Seed deterministic risk demo scenarios into Postgres.

This gives predictable, production-like behavior for the dashboard agent.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg2

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from risk_monitor import ensure_risk_tables

if load_dotenv:
    env_file = ROOT / '.env'
    if env_file.exists():
        load_dotenv(env_file)


def main() -> int:
    required = ['PGHOST', 'PGPORT', 'PGDATABASE', 'PGUSER', 'PGPASSWORD']
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        print(f"[fail] missing DB env vars: {', '.join(missing)}")
        return 1

    conn = psycopg2.connect(
        host=os.getenv('PGHOST'),
        port=int(os.getenv('PGPORT')),
        dbname=os.getenv('PGDATABASE'),
        user=os.getenv('PGUSER'),
        password=os.getenv('PGPASSWORD'),
    )

    try:
        ensure_risk_tables(conn)
        with conn.cursor() as cur:
            # Reset these tables so each seed run is fully deterministic.
            cur.execute('DELETE FROM risk_events')
            cur.execute('DELETE FROM transport_disruptions')
            cur.execute('DELETE FROM flight_status')

            cur.execute(
                """
                INSERT INTO risk_events (event_type, severity, region, affected_sites, description, start_date, end_date, affected_countries, source_url)
                VALUES
                    ('conflict', 'critical', 'Middle East', ARRAY['IL_F28','IL_F28a','IL_F38','IL_JS'],
                     'Active armed conflict in Israel; FAA restrictions.', DATE '2026-01-01', NULL, ARRAY['ISR'], 'seed:risk-demo'),
                    ('maritime', 'high', 'Red Sea', NULL,
                     'Houthi attacks; vessels diverting via Cape of Good Hope.', DATE '2026-02-01', NULL, ARRAY['IRL','ISR','CHN','MYS','VNM'], 'seed:risk-demo')
                """
            )

            cur.execute(
                """
                INSERT INTO transport_disruptions (disruption_type, location_id, location_type, severity, wait_time_minutes, description, start_time, end_time, source)
                VALUES
                    ('tsa_staffing', 'PHX', 'airport', 'high', 95, 'TSA staffing shortage due to DHS shutdown.', TIMESTAMPTZ '2026-03-01 00:00:00+00', NULL, 'seed:risk-demo'),
                    ('port_congestion', 'SGN', 'seaport', 'medium', 36, 'Vessel backlog after typhoon season.', TIMESTAMPTZ '2026-03-05 00:00:00+00', NULL, 'seed:risk-demo')
                """
            )

            cur.execute(
                """
                INSERT INTO flight_status (flight_iata, departure_airport, arrival_airport, status, delay_minutes, last_updated, source)
                VALUES
                    ('AA123', 'PHX', 'DFW', 'delayed', 120, NOW(), 'seed:risk-demo'),
                    ('DL456', 'DUB', 'JFK', 'active', 0, NOW(), 'seed:risk-demo'),
                    ('UA789', 'TLV', 'EWR', 'cancelled', NULL, NOW(), 'seed:risk-demo')
                """
            )

        conn.commit()

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM risk_events")
            risk_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM transport_disruptions")
            disruption_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM flight_status")
            flight_count = cur.fetchone()[0]

        print(f"[ok] seeded risk_events={risk_count}, transport_disruptions={disruption_count}, flight_status={flight_count}")
        return 0
    except Exception as exc:
        conn.rollback()
        print(f"[fail] seed failed: {exc}")
        return 1
    finally:
        conn.close()


if __name__ == '__main__':
    raise SystemExit(main())
