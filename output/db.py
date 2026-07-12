"""Shared database + script-startup helpers for the output layer.

Phase 7: extracted from records.py and generate_summary.py (which each
had their own copy of SNOWFLAKE_CONFIG + query_snowflake) and the
script-top boilerplate (utf-8 stdout reconfig + load_dotenv) duplicated
across both output scripts.

Four things live here:
  - init()             one-call script startup: utf-8 stdout + load_dotenv
                       + warm the Snowflake config. Output scripts call
                       this once at start.
  - query_snowflake()  the only Snowflake entry point. Lazily opens a
                       single connection on first call and reuses it for
                       the lifetime of the process. atexit cleanup.
  - set_league() /     the process-wide target league (MLB-57). Every
    league_predicate() league-scoped mart query filters on
                       league_predicate(); scripts with a --league flag
                       call set_league(args.league) right after init().
                       Unset, it lazily resolves the registry default
                       (the ESPN league) -- the pre-registry runbook
                       behaves identically.
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
import re
import sys
from pathlib import Path

import snowflake.connector
from dotenv import load_dotenv

# League registry import: repo root on sys.path so the shared config/
# namespace package resolves when output scripts run as files.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from config.league_registry import get_league


_SNOWFLAKE_CONFIG = None
_conn = None
_stdout_reconfigured = False
_league = None


def _build_config():
    """Build the Snowflake connector config from env vars. Called lazily
    so import-time env-var availability doesn't matter — load_dotenv()
    runs before this whether init() was called or query_snowflake() was
    called first.

    Auth: prefers key-pair when SNOWFLAKE_PRIVATE_KEY_PATH is set
    (recommended after MFA enforcement on the account, since password
    auth then triggers an MFA prompt the connector can't satisfy
    interactively). Falls back to password auth otherwise, preserving
    backward compatibility for accounts without MFA.

    SNOWFLAKE_SCHEMA names the raw landing schema (used by extract.py).
    The output scripts read dbt-built models, which live in a different
    schema -- SNOWFLAKE_ANALYTICS_SCHEMA, defaulting to ANALYTICS to
    match the dbt profile convention. Customize if your dbt target
    schema is named differently.
    """
    config = {
        "account":   os.getenv("SNOWFLAKE_ACCOUNT"),
        "user":      os.getenv("SNOWFLAKE_USER"),
        "database":  os.getenv("SNOWFLAKE_DATABASE"),
        "schema":    os.getenv("SNOWFLAKE_ANALYTICS_SCHEMA", "ANALYTICS"),
        "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE"),
    }

    private_key_path = os.getenv("SNOWFLAKE_PRIVATE_KEY_PATH")
    if private_key_path:
        config["private_key_file"] = private_key_path
        # Encrypted private keys: set SNOWFLAKE_PRIVATE_KEY_PASSPHRASE.
        # Unencrypted keys can leave it unset.
        passphrase = os.getenv("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE")
        if passphrase:
            config["private_key_file_pwd"] = passphrase
    else:
        # Password auth fallback (pre-MFA accounts).
        config["password"] = os.getenv("SNOWFLAKE_PASSWORD")

    return config


def init():
    """Idempotent script startup. Output scripts call this once at top:
      - utf-8 stdout reconfig (Windows cp1252 default crashes on emoji
        team names; safe no-op on platforms where reconfigure is missing
        or fails)
      - load_dotenv() so SNOWFLAKE_*, SHEETS_DEV_ID etc. are populated
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


# ---------------------------------------------------------------------------
# League targeting (MLB-57). The warehouse holds every league at once
# (league_key in every grain); an output run renders exactly one. The
# same process-wide pattern as the connection singleton: scripts call
# set_league(args.league) once at startup, library modules read
# league_predicate() when building SQL. Left unset, the registry default
# (the ESPN league) resolves lazily on first use.
# ---------------------------------------------------------------------------

def set_league(key=None):
    """Pin the process's target league. key=None -> registry default.
    Raises LeagueRegistryError (with the known-key list) on unknown keys,
    so a typo'd --league fails before any query runs."""
    global _league
    league = get_league(key)
    # The key is spliced into SQL as a validated literal (see
    # league_predicate); constrain the alphabet rather than trusting the
    # yml blindly.
    if not re.fullmatch(r"[a-z0-9_-]+", league.key):
        raise ValueError(
            f"league_key {league.key!r} contains characters outside "
            f"[a-z0-9_-]; fix the key in config/leagues.yml"
        )
    _league = league


def league():
    """The resolved League object (config.league_registry.League)."""
    if _league is None:
        set_league(None)
    return _league


def league_key():
    """The active league's registry key -- the value stamped in every
    warehouse grain."""
    return league().key


def league_predicate(alias=None):
    """SQL predicate pinning a query to the active league, e.g.
    "league_key = 'espn-main'" (or "t.league_key = ..." with an alias).
    Returned as a literal rather than a bind param so call sites don't
    have to re-thread positional %s params; the key's alphabet is
    validated at set_league time."""
    column = f"{alias}.league_key" if alias else "league_key"
    return f"{column} = '{league_key()}'"


def league_file_tag():
    """League segment for output artifact filenames (MLB-58: log-file
    naming is league-scoped so multi-league runs never collide). Empty
    for the registry's default league -- the pre-registry filenames stay
    byte-identical -- and '<key>_' for every other league."""
    if league_key() == get_league(None).key:
        return ''
    return f"{league_key()}_"
