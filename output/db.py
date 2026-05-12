"""Shared database + script-startup helpers for the output layer.

Phase 7: extracted from records.py and generate_summary.py (which each
had their own copy of SNOWFLAKE_CONFIG + query_snowflake) and the
script-top boilerplate (utf-8 stdout reconfig + load_dotenv) duplicated
across both output scripts.

Three things live here:
  - init()             one-call script startup: utf-8 stdout + load_dotenv
                       + warm the Snowflake config. Output scripts call
                       this once at start.
  - query_snowflake()  the only Snowflake entry point. Lazily opens a
                       single connection on first call and reuses it for
                       the lifetime of the process. atexit cleanup.
  - close()            explicit cleanup; rarely needed (atexit handles it).

Connection consolidation rationale: pre-Phase-7, every records.py /
generate_summary.py call to query_snowflake opened and closed its own
connection — ~15-20 handshakes per script run. Acceptable at 14-team
scale, expensive at any larger scale. The lazy-singleton pattern below
gives one connection per process without requiring callers to thread
`conn` through every function signature. A v1.x refinement could
promote this to an explicit context-manager pattern; for v1.0 the
implicit shared connection is the right complexity tradeoff.

Library modules (records.py, league_notes.py) only need
`from db import query_snowflake` — the connection opens lazily on first
query whether or not init() was called. init() is a script-only entry
point for the stdout reconfig.
"""

import atexit
import os
import sys

import snowflake.connector
from dotenv import load_dotenv


_SNOWFLAKE_CONFIG = None
_conn = None
_stdout_reconfigured = False


def _build_config():
    """Build the Snowflake connector config from env vars. Called lazily
    so import-time env-var availability doesn't matter — load_dotenv()
    runs before this whether init() was called or query_snowflake() was
    called first."""
    return {
        "account":   os.getenv("SNOWFLAKE_ACCOUNT"),
        "user":      os.getenv("SNOWFLAKE_USER"),
        "password":  os.getenv("SNOWFLAKE_PASSWORD"),
        "database":  os.getenv("SNOWFLAKE_DATABASE"),
        "schema":    "ANALYTICS",
        "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE"),
    }


def init():
    """Idempotent script startup. Output scripts call this once at top:
      - utf-8 stdout reconfig (Windows cp1252 default crashes on emoji
        team names; safe no-op on platforms where reconfigure is missing
        or fails)
      - load_dotenv() so SNOWFLAKE_*, SHEETS_OUTPUT_ID etc. are populated
      - warm the Snowflake config dict so the first query call doesn't
        also do the env work

    Subsequent calls are no-ops. Library modules (records.py, etc.)
    don't need to call this — query_snowflake() lazily opens its own
    connection. init() exists for the stdout reconfig that scripts need
    before printing.
    """
    global _SNOWFLAKE_CONFIG, _stdout_reconfigured

    if not _stdout_reconfigured:
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except (AttributeError, OSError):
            pass
        _stdout_reconfigured = True

    if _SNOWFLAKE_CONFIG is None:
        load_dotenv()
        _SNOWFLAKE_CONFIG = _build_config()


def _get_conn():
    """Return the process-wide Snowflake connection, opening it on first
    call and registering atexit cleanup."""
    global _conn, _SNOWFLAKE_CONFIG
    if _conn is None:
        if _SNOWFLAKE_CONFIG is None:
            load_dotenv()
            _SNOWFLAKE_CONFIG = _build_config()
        _conn = snowflake.connector.connect(**_SNOWFLAKE_CONFIG)
        atexit.register(close)
    return _conn


def query_snowflake(sql, params=None):
    """Run a query and return results as a list of dicts (cols lowercased).

    Uses the process-wide connection (opened lazily on first call,
    closed via atexit at process exit). Cursor is opened and closed per
    query; connection persists.

    Phase 7: replaces the connection-per-call pattern that lived in
    records.py and generate_summary.py prior to consolidation.
    """
    conn = _get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute(sql, params or ())
        columns = [desc[0].lower() for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        cursor.close()


def close():
    """Close the shared Snowflake connection. Idempotent. Registered as
    atexit on first connection, so callers rarely need this directly."""
    global _conn
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
        _conn = None
