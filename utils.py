"""
demo/utils.py
Shared utilities: mock API client, pretty printing, timing, result display.
Every demo imports from here — keeps demo scripts clean and readable.
"""
import argparse
import json
import os
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import psycopg2
from psycopg2.extras import RealDictCursor

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

_ENV_LOADED = False


def load_local_env() -> None:
    """Load .env once if python-dotenv is available."""
    global _ENV_LOADED
    if _ENV_LOADED or load_dotenv is None:
        return

    base = Path(__file__).resolve().parent
    for env_path in (base / ".env", base.parent / ".env"):
        if env_path.exists():
            load_dotenv(dotenv_path=env_path)
            _ENV_LOADED = True
            break


load_local_env()

def parse_demo_args(description="AI DE Demo"):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--mock", action="store_true", default=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--model", default="gpt-4o-mini")
    args = parser.parse_args()
    args.mock = not args.live
    return args

def is_mock(args) -> bool:
    return args.mock


DB_ENV_VARS = ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD")


def get_db_config(**overrides):
    """Return DB config dict using env vars with optional overrides."""
    cfg = {
        "host": os.getenv("PGHOST", "localhost"),
        "port": int(os.getenv("PGPORT", "5432")),
        "dbname": os.getenv("PGDATABASE", "semiconductor"),
        "user": os.getenv("PGUSER"),
        "password": os.getenv("PGPASSWORD"),
    }
    for key, value in overrides.items():
        if value is not None:
            cfg[key] = value
    return cfg


def require_db_env() -> None:
    missing = [key for key in DB_ENV_VARS if not os.getenv(key)]
    if missing:
        raise SystemExit(f"Missing environment variables: {', '.join(missing)}")


def get_db_connection(*, autocommit: bool = False, **overrides):
    """Return a psycopg2 connection using shared config helpers."""
    require_db_env()
    conn = psycopg2.connect(**get_db_config(**overrides))
    conn.autocommit = autocommit
    return conn


def fetch_rows(sql: str, params: Optional[tuple] = None):
    """Return all rows as dicts for convenience."""
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def fetch_one(sql: str, params: Optional[tuple] = None):
    rows = fetch_rows(sql, params)
    return rows[0] if rows else None


def execute_sql(sql: str, params: Optional[tuple] = None) -> int:
    """Execute a write query and return affected row count."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()
            return cur.rowcount

# ── PRETTY PRINT HELPERS ──────────────────────────────────────────────────
DIVIDER  = "─" * 65
DIVIDER2 = "═" * 65

def section(title, col="BLUE"):
    COLORS = {"BLUE":"\033[94m","GREEN":"\033[92m","RED":"\033[91m",
              "YELLOW":"\033[93m","CYAN":"\033[96m","BOLD":"\033[1m","RESET":"\033[0m"}
    c = COLORS.get(col, "")
    r = COLORS["RESET"]
    print(f"\n{DIVIDER}")
    print(f"  {c}{COLORS['BOLD']}{title}{r}")
    print(DIVIDER)

def ok(msg):    print(f"  \033[92m✅\033[0m  {msg}")
def fail(msg):  print(f"  \033[91m❌\033[0m  {msg}")
def info(msg):  print(f"  \033[94mℹ\033[0m   {msg}")
def warn(msg):  print(f"  \033[93m⚠\033[0m   {msg}")
def metric(label, value, sub=""):
    sub_str = f"  ({sub})" if sub else ""
    print(f"  \033[1m\033[96m{value:>12}\033[0m  {label}{sub_str}")

def so_what(lines):
    print(f"\n  \033[93m💡 SO WHAT:\033[0m")
    for l in lines:
        print(f"     {l}")

def recruiter_line(text):
    print(f"\n  \033[95m🎤 SAY:\033[0m  \033[3m\"{text}\"\033[0m\n")

def show_json(data, indent=4):
    print(json.dumps(data, indent=indent, default=str))

# ── MOCK OPENAI CLIENT ────────────────────────────────────────────────────
@dataclass
class MockUsage:
    prompt_tokens:     int = 120
    completion_tokens: int = 80
    total_tokens:      int = 200

@dataclass
class MockMessage:
    content: str
    tool_calls: Optional[list] = None

@dataclass
class MockChoice:
    message: MockMessage
    finish_reason: str = "stop"

@dataclass
class MockResponse:
    choices: list
    usage:   MockUsage
    model:   str = "gpt-4o-mini"
    id:      str = "mock-resp-001"

class MockOpenAI:
    """Drop-in mock for openai.OpenAI() — returns realistic synthetic data"""

    def __init__(self):
        self.chat       = self._Chat()
        self.files      = self._Files()
        self.fine_tuning = self._FineTuning()
        self.embeddings = self._Embeddings()

    class _Chat:
        class completions:
            @staticmethod
            def create(model="gpt-4o-mini", messages=None, tools=None,
                       tool_choice=None, response_format=None,
                       max_tokens=1024, temperature=0.7, **kwargs):
                import time
                time.sleep(0.05)  # simulate network latency

                # Simulate tool calls if tools are provided
                if tools and random.random() > 0.4:
                    tc = [{
                        "id": f"call_{random.randint(1000,9999)}",
                        "type": "function",
                        "function": {
                            "name": tools[0]["function"]["name"],
                            "arguments": '{"account_id": "ACC-001"}'
                        }
                    }]
                    return MockResponse(
                        choices=[MockChoice(message=MockMessage(content=None, tool_calls=tc))],
                        usage=MockUsage(120, 30, 150),
                    )

                # Simulate structured JSON output for evaluators
                if response_format and getattr(response_format, "get", lambda k,d=None: None)("type") == "json_object" \
                   or (isinstance(response_format, dict) and response_format.get("type") == "json_object"):
                    content = json.dumps({"score": random.randint(3,5),
                                         "reasoning": "Mock evaluation: response is accurate and complete.",
                                         "safe": True, "category": "clean", "confidence": 0.92})
                else:
                    content = "This is a mock response demonstrating the AI system works correctly. In live mode this would be a real model-generated answer with cited sources and structured analysis."

                return MockResponse(
                    choices=[MockChoice(message=MockMessage(content=content))],
                    usage=MockUsage(120, 45, 165),
                )

    class _Files:
        def create(self, file, purpose):
            return type('F', (), {'id': f'file-mock{random.randint(1000,9999)}'})()

    class _FineTuning:
        class jobs:
            @staticmethod
            def create(**kwargs):
                return type('J', (), {
                    'id': f'ftjob-mock{random.randint(10000,99999)}',
                    'status': 'queued',
                    'fine_tuned_model': None,
                    'trained_tokens': 0,
                })()
            @staticmethod
            def retrieve(job_id):
                return type('J', (), {
                    'status': 'succeeded',
                    'fine_tuned_model': f'ft:gpt-4o-mini-2024-07-18:org::{job_id[-6:]}',
                    'trained_tokens': 284000,
                    'error': None,
                })()

    class _Embeddings:
        def create(self, input, model="text-embedding-3-small"):
            import hashlib, struct
            # Deterministic fake embeddings (same input → same vector)
            vectors = []
            for text in (input if isinstance(input, list) else [input]):
                h = hashlib.md5(text.encode()).digest()
                vec = [struct.unpack('f', h[i:i+4])[0] for i in range(0, 16, 4)]
                # Pad to 1536 with pseudo-random values seeded by hash
                random.seed(int.from_bytes(h, 'big'))
                vec.extend([random.gauss(0, 0.1) for _ in range(1532)])
                vectors.append(vec)
            return type('E', (), {
                'data': [type('D', (), {'embedding': v})() for v in vectors],
                'usage': MockUsage(len(str(input))//4, 0, len(str(input))//4),
            })()

def get_client(mock: bool):
    """Return mock or real OpenAI client"""
    if mock:
        return MockOpenAI()
    from openai import OpenAI
    return OpenAI()

# ── TIMING ────────────────────────────────────────────────────────────────
class Timer:
    def __init__(self, label=""):
        self.label = label
    def __enter__(self):
        self._start = time.time()
        return self
    def __exit__(self, *_):
        self.elapsed_ms = int((time.time() - self._start) * 1000)
        if self.label:
            info(f"{self.label}: {self.elapsed_ms}ms")
    @property
    def ms(self):
        return getattr(self, 'elapsed_ms', 0)
