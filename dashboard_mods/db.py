"""
db.py — database access only.
One responsibility: run SQL, nothing else.
"""
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))

import psycopg2
import pandas as pd
from typing import Optional
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / '.env')  # load .env file for environment variables for this file only

    
DB_CONFIG = {
    'host':     os.getenv('PGHOST', 'localhost'),
    'port':     int(os.getenv('PGPORT', '5432')),
    'database': os.getenv('PGDATABASE', 'semiconductor'),
    'user':     os.getenv('PGUSER', ''),
    'password': os.getenv('PGPASSWORD', ''),
}


def get_connection():
    return psycopg2.connect(**DB_CONFIG)


def run_query(sql: str) -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql(sql, conn)


def execute_write(sql: str, params: Optional[tuple] = None, fetchone: bool = False):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchone() if fetchone else None
