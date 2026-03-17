#!/usr/bin/env python3
"""Ingest AviationStack flight status into Postgres.

Usage:
  python scripts/ingest_aviationstack.py --once
  python scripts/ingest_aviationstack.py --interval-seconds 300

Environment:
  AVIATION_API_KEY (required)
  AVIATIONSTACK_BASE_URL (optional, default: http://api.aviationstack.com/v1/flights)
  AVIATION_MONITORED_FLIGHTS (optional CSV, e.g. AA123,DL456,UA789)
  PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg2
import requests

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

DEFAULT_FLIGHTS = ['AA123', 'DL456', 'UA789']


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace('Z', '+00:00')
    try:
        dt = datetime.fromisoformat(text)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _to_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == '':
            return None
        return int(float(value))
    except Exception:
        return None


def _extract_first_result(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    # AviationStack standard payload uses "data", but we also support legacy/custom wrappers.
    candidates = payload.get('data')
    if isinstance(candidates, list) and candidates:
        return candidates[0]
    candidates = payload.get('results')
    if isinstance(candidates, list) and candidates:
        return candidates[0]
    return None


def get_monitored_flights() -> List[str]:
    raw = os.getenv('AVIATION_MONITORED_FLIGHTS', '')
    if raw.strip():
        flights = [f.strip().upper() for f in raw.split(',') if f.strip()]
        deduped = sorted(set(flights))
        if deduped:
            return deduped
    return DEFAULT_FLIGHTS


def fetch_flight(api_key: str, base_url: str, flight_iata: str) -> Optional[Dict[str, Any]]:
    params = {'access_key': api_key, 'flight_iata': flight_iata}
    try:
        resp = requests.get(base_url, params=params, timeout=20)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        print(f"[warn] fetch failed for {flight_iata}: {exc}")
        return None

    row = _extract_first_result(payload)
    if not row:
        print(f"[info] no flight data for {flight_iata}")
    return row


def upsert_flight(cur, flight_iata: str, row: Dict[str, Any]) -> None:
    departure = row.get('departure') or {}
    arrival = row.get('arrival') or {}
    airline = row.get('airline') or {}

    cur.execute(
        """
        INSERT INTO flight_status (
            flight_iata,
            departure_airport,
            arrival_airport,
            status,
            delay_minutes,
            scheduled_departure,
            actual_departure,
            scheduled_arrival,
            actual_arrival,
            airline,
            source,
            last_updated
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (flight_iata)
        DO UPDATE SET
            departure_airport = EXCLUDED.departure_airport,
            arrival_airport = EXCLUDED.arrival_airport,
            status = EXCLUDED.status,
            delay_minutes = EXCLUDED.delay_minutes,
            scheduled_departure = EXCLUDED.scheduled_departure,
            actual_departure = EXCLUDED.actual_departure,
            scheduled_arrival = EXCLUDED.scheduled_arrival,
            actual_arrival = EXCLUDED.actual_arrival,
            airline = EXCLUDED.airline,
            source = EXCLUDED.source,
            last_updated = EXCLUDED.last_updated
        """,
        (
            flight_iata,
            (departure.get('iata') or '').strip().upper() or None,
            (arrival.get('iata') or '').strip().upper() or None,
            row.get('flight_status'),
            _to_int(departure.get('delay')),
            _parse_dt(departure.get('scheduled')),
            _parse_dt(departure.get('actual')),
            _parse_dt(arrival.get('scheduled')),
            _parse_dt(arrival.get('actual')),
            airline.get('name'),
            'aviationstack',
        ),
    )


def run_once() -> int:
    api_key = os.getenv('AVIATION_API_KEY', '').strip()
    if not api_key:
        print('[fail] AVIATION_API_KEY is not set')
        return 1

    required_pg = ['PGHOST', 'PGPORT', 'PGDATABASE', 'PGUSER', 'PGPASSWORD']
    missing = [k for k in required_pg if not os.getenv(k)]
    if missing:
        print(f"[fail] missing DB env vars: {', '.join(missing)}")
        return 1

    base_url = os.getenv('AVIATIONSTACK_BASE_URL', 'http://api.aviationstack.com/v1/flights').strip()
    flights = get_monitored_flights()
    if not flights:
        print('[fail] no flights configured')
        return 1

    conn = psycopg2.connect(
        host=os.getenv('PGHOST'),
        port=int(os.getenv('PGPORT')),
        dbname=os.getenv('PGDATABASE'),
        user=os.getenv('PGUSER'),
        password=os.getenv('PGPASSWORD'),
    )

    inserted = 0
    try:
        ensure_risk_tables(conn)
        with conn.cursor() as cur:
            for flight_iata in flights:
                row = fetch_flight(api_key, base_url, flight_iata)
                if not row:
                    continue
                upsert_flight(cur, flight_iata, row)
                inserted += 1
        conn.commit()
    except Exception as exc:
        conn.rollback()
        print(f'[fail] ingestion failed: {exc}')
        return 1
    finally:
        conn.close()

    print(f'[ok] upserted {inserted} flight rows')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true', help='Run one ingestion cycle and exit')
    parser.add_argument('--interval-seconds', type=int, default=0, help='If >0, run continuously on this interval')
    args = parser.parse_args()

    if args.once or args.interval_seconds <= 0:
        return run_once()

    print(f'[info] running ingestion loop every {args.interval_seconds}s')
    while True:
        rc = run_once()
        if rc != 0:
            print('[warn] last cycle failed; retrying on next interval')
        time.sleep(args.interval_seconds)


if __name__ == '__main__':
    raise SystemExit(main())
