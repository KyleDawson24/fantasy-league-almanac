"""Shared database + script-startup helpers for the output layer.

Phase 7: extracted from records.py and generate_summary.py (which each
had their own copy of SNOWFLAKE_CONFIG + query_snowflake) and the
script-top boilerplate (utf-8 stdout reconfig + load_dotenv) duplicated
across both output scripts.

Six things live here:
  - init()             one-call script startup: utf-8 stdout + load_dotenv
                       + warm the Snowflake config. Output scripts call
                       this once at start.
  - query_snowflake()  the database entry point, and the only one that
                       opens a connection. Lazily opens a single
                       connection on first call and reuses it for the
                       lifetime of the process. atexit cleanup. Returns
                       warehouse truth, unmodified.
  - query_for_presentation()
                       query_snowflake() PLUS the owner-label fallback.
                       What a module that READS TO RENDER calls, and see
                       below for why it is not the default.
  - use_duckdb()       point those entry points at a local DuckDB file
                       instead of Snowflake (MLB-10 phase 5).
  - set_league() /     the process-wide target league (MLB-57). Every
    league_predicate() league-scoped mart query filters on
                       league_predicate(); scripts with a --league flag
                       call set_league(args.league) right after init().
                       Unset, it lazily resolves the registry default
                       (the ESPN league) -- the pre-registry runbook
                       behaves identically.
  - close()            explicit cleanup; rarely needed (atexit handles it).

RAW BY DEFAULT, PRESENTATION BY REQUEST (MLB-243, Kyle 2026-08-15).

`query_snowflake` returns warehouse truth, always, unmodified. The
owner-label fallback -- swapping a withheld-owner sentinel for the row's
own canonical team name -- is a PRESENTATION rule, and it is applied only
by `query_for_presentation`.

It briefly lived inside `query_snowflake` itself, on the argument that one
seam cannot drift. It cannot, but the wrong things flow through it: the PII
guard's identity inventory, the continuity sheet that becomes owner seeds,
the row-count fixture. Those were all provably untouched -- but only
because of the shape of their SELECT lists, and a boundary that holds by
coincidence is not a boundary. An opt-OUT flag would have been no better,
because it has to be remembered by code that does not exist yet.

So the default is raw and the presentation modules ask for the other
behaviour by name. A new identity, seeding, extraction, contract or
analysis caller that reaches for the obvious function gets the truth
without knowing this rule exists.

Which modules sit on which side, as of MLB-243:

  query_for_presentation  almanac_data, almanac_sheets, cbs_almanac_sheets,
                          cbs_draft_recap_data, espn_points_data,
                          generate_season_report, generate_summary,
                          league_notes, records, records_data
  query_snowflake         build_continuity_sheet (its output becomes owner
                          seeds), league_format, slot_catalog,
                          stat_catalog, tools/check_pii.py,
                          tools/explain_corpus.py,
                          tools/analyze_negative_metrics.py,
                          tests/capture_row_counts.py

The first list and the first four of the second are not a convention
anybody has to remember: tests/test_points_almanac_espn.py asserts them
from module source, and pins the PII guard and the row-count fixture by
name, so a module that changes sides has to say so in a diff. The two
analysis tools are not pinned -- they read nothing that is rendered.

Connection consolidation rationale: pre-Phase-7, every records.py /
generate_summary.py call to query_snowflake opened and closed its own
connection — ~15-20 handshakes per script run. Acceptable at 14-team
scale, expensive at any larger scale. The lazy-singleton pattern below
gives one connection per process without requiring callers to thread
`conn` through every function signature. A v1.x refinement could
promote this to an explicit context-manager pattern; for v1.0 the
implicit shared connection is the right complexity tradeoff.

Library modules only need one import -- `from db import query_snowflake`,
or `query_for_presentation` for the rendering ones -- and the connection
opens lazily on first query whether or not init() was called. init() is a
script-only entry point for the stdout reconfig.

TWO BACKENDS (MLB-10 phase 5). `query_snowflake()` keeps its name -- it is
called from well over a hundred sites and renaming them is a separate
change -- but it dispatches on the selected backend. Snowflake stays the
default and its path is untouched, because it is what the live weekly run
uses. `use_duckdb()` swaps in a local DuckDB file, which is what makes a
clone with no Snowflake account able to render the almanac at all. The
backend switch sits UNDER both entry points, so `query_for_presentation`
follows it without knowing it exists.

Note that `import snowflake.connector` is DELIBERATELY LAZY. At module
scope it made the connector a hard import dependency of the whole output
layer, so a DuckDB-only clone could not import `db` -- let alone render --
without installing a driver it would never open. That import is now
inside the Snowflake connect path, which is the only place that needs it.
"""

import atexit
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

# The owner-label presentation fallback (MLB-243). Pure Python with no
# dependencies of its own, and applied ONLY by query_for_presentation below
# -- never by the raw seam. See that function, and owner_labels itself for
# why the rule lives in presentation rather than in dim_owner.
import owner_labels

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
        # Backend selection reads the environment only AFTER load_dotenv(),
        # so a checkout can carry ALMANAC_DB_BACKEND=duckdb in .env and every
        # output script picks it up without a flag. Scripts with an explicit
        # --duckdb call use_duckdb() themselves; this is the default for the
        # ones that have no flag. Building the Snowflake config either way is
        # free -- it is env lookups, and it imports no driver.
        if os.getenv('ALMANAC_DB_BACKEND', '').strip().lower() == 'duckdb':
            use_duckdb()


def _get_conn():
    """Return the process-wide Snowflake connection, opening it on first
    call and registering atexit cleanup."""
    global _conn, _SNOWFLAKE_CONFIG
    if _conn is None:
        if _SNOWFLAKE_CONFIG is None:
            load_dotenv()
            _SNOWFLAKE_CONFIG = _build_config()
        import snowflake.connector  # lazy: see the module docstring
        _conn = snowflake.connector.connect(**_SNOWFLAKE_CONFIG)
        atexit.register(close)
    return _conn


# ---------------------------------------------------------------------------
# DuckDB backend (MLB-10 phase 5). The dialect section further down decides
# how SQL is SPELLED; this decides where it is SENT, and the two have to
# agree -- so use_duckdb() sets both.
#
# WHY THE SCHEMA NAMES LINE UP FOR FREE: dbt_league/profiles/profiles.yml
# pins `database: ESPN_FANTASY` / `schema: ANALYTICS` and names the file
# ESPN_FANTASY.duckdb, because DuckDB takes its catalog name from the file's
# stem. Models therefore land at ESPN_FANTASY.ANALYTICS.<x> on both engines
# and not one line of the output layer's SQL moves. The catalog is read back
# off the file's stem here rather than hard-coded, so pointing
# DBT_DUCKDB_PATH at another league's database works without an edit.
# ---------------------------------------------------------------------------

DEFAULT_DUCKDB_PATH = 'data/duckdb/ESPN_FANTASY.duckdb'

_BACKEND = 'snowflake'
_duck_path = None
_duck_conn = None


def use_duckdb(path=None):
    """Send every subsequent query to a local DuckDB file, not Snowflake.

    `path` defaults to DBT_DUCKDB_PATH, then to the same location the dbt
    profile writes. Also pins the SQL dialect to match -- a DuckDB
    connection fed Snowflake spellings fails at the first LISTAGG, and
    having one switch rather than two is what keeps that from happening.
    """
    global _BACKEND, _duck_path
    _BACKEND = 'duckdb'
    _duck_path = path or os.getenv('DBT_DUCKDB_PATH') or DEFAULT_DUCKDB_PATH
    set_dialect('duckdb')


def backend():
    """'snowflake' (default) or 'duckdb'."""
    return _BACKEND


def _to_qmark(sql):
    """Rewrite pyformat placeholders to DuckDB's qmark style.

    Only outside string literals, quoted identifiers and comments. A flat
    str.replace('%s', '?') happens to be safe on today's queries -- no
    literal '%s' appears inside a quoted string anywhere in output/ -- but
    it is one ordinary predicate away from being wrong: `like '%save%'`
    would silently become `like '?ave%'`, a corrupted filter rather than
    an error. Cheap to do properly, so it is done properly.
    """
    out = []
    i, n = 0, len(sql)
    while i < n:
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j == -1 else j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            j = n if j == -1 else j + 2
        elif sql[i] == '"':
            j = sql.find('"', i + 1)
            j = n if j == -1 else j + 1
        elif sql[i] == "'":
            j = i + 1
            while j < n:
                if sql[j] != "'":
                    j += 1
                elif sql.startswith("''", j):   # the SQL escape for a quote
                    j += 2
                else:
                    j += 1
                    break
        elif sql.startswith("%s", i):
            out.append("?")
            i += 2
            continue
        elif sql.startswith("%%", i):
            out.append("%")
            i += 2
            continue
        else:
            out.append(sql[i])
            i += 1
            continue
        out.append(sql[i:j])
        i = j
    return "".join(out)


def _get_duck_conn():
    """The process-wide DuckDB connection, opened read-only on first use."""
    global _duck_conn
    if _duck_conn is None:
        # Path first, driver second: the check is free, it needs no import,
        # and "there is no database here" is the more actionable of the two
        # errors a first BYO run can hit. It also means this function can be
        # tested from the Snowflake venv, which has no duckdb and, per
        # CLAUDE.md, must never acquire one.
        path = Path(_duck_path or DEFAULT_DUCKDB_PATH)
        if not path.exists():
            raise FileNotFoundError(
                f"no DuckDB database at {path}. Build one with "
                f"tools/duckdb_run.sh, or point DBT_DUCKDB_PATH at an "
                f"existing file."
            )
        try:
            import duckdb
        except ImportError as exc:
            raise ImportError(
                "reading a local database needs the duckdb package: "
                "pip install duckdb"
            ) from exc
        # Read-only because the output layer only ever reads, and it means a
        # render cannot disturb a warehouse a build may be holding.
        _duck_conn = duckdb.connect(str(path), read_only=True)
        catalog = path.stem
        schema = os.getenv('SNOWFLAKE_ANALYTICS_SCHEMA', 'ANALYTICS')
        _duck_conn.execute(f'USE "{catalog}"."{schema}"')
        atexit.register(close)
    return _duck_conn


def _duckdb_query(sql, params=None):
    """query_snowflake()'s DuckDB half. Same contract: list of dicts,
    column names lowercased -- DuckDB does not uniformly lowercase them
    either, so this is load-bearing on both engines."""
    con = _get_duck_conn()
    if params:
        cur = con.execute(_to_qmark(sql), list(params))
    else:
        # No params means no placeholders to rewrite, so the SQL is passed
        # through untouched rather than scanned.
        cur = con.execute(sql)
    columns = [desc[0].lower() for desc in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def query_snowflake(sql, params=None):
    """Run a query and return WAREHOUSE TRUTH as a list of dicts (cols
    lowercased). Nothing here rewrites a value.

    That last sentence is the contract (MLB-243). A caller resolving
    identity, seeding configuration, guarding PII, extracting or
    generating the schema contract wants exactly this and gets it by
    default; `query_for_presentation` below is where a display rule is
    added, and it is asked for by name. See the module docstring for why
    the default runs this way round.

    Uses the process-wide connection (opened lazily on first call,
    closed via atexit at process exit). Cursor is opened and closed per
    query; connection persists.

    Phase 7: replaces the connection-per-call pattern that lived in
    records.py and generate_summary.py prior to consolidation.

    MLB-10: dispatches to DuckDB when use_duckdb() has been called. The
    name is now a mild misnomer and stays anyway -- the call-site count
    is in the hundreds.
    """
    if _BACKEND == 'duckdb':
        return _duckdb_query(sql, params)
    conn = _get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute(sql, params or ())
        columns = [desc[0].lower() for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        cursor.close()


def query_for_presentation(sql, params=None):
    """`query_snowflake`, then the owner-label presentation fallback.

    THE ONE THING THIS DOES that the raw seam does not: where the platform
    withheld every owner name and the warehouse therefore says "Owner
    unavailable", the row's own canonical team name is shown instead. See
    `owner_labels` for why that is a display rule rather than a dim_owner
    one, and `query_snowflake` above for why it is opt-in.

    Use this from a module that RENDERS. Everything else -- identity
    resolution, the continuity sheet that becomes owner seeds, the PII
    guard's inventory, extraction, contract generation, analysis -- wants
    `query_snowflake` and should keep calling it.
    """
    return owner_labels.scrub_rows(query_snowflake(sql, params))


def close():
    """Close the shared connection, whichever backend opened it.
    Idempotent. Registered as atexit on first connection, so callers
    rarely need this directly."""
    global _conn, _duck_conn
    for conn in (_conn, _duck_conn):
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    _conn = None
    _duck_conn = None


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


def latest_by(value, order_by, partition_by=None):
    """The LATEST NON-NULL `value` in a group, as a SELECT-list fragment.

    Pass `partition_by` to get the WINDOW form instead of the aggregate
    one -- same guard, same semantics, `OVER (PARTITION BY ...)` appended.
    That form exists for the row-picking consumers (generate_summary,
    generate_season_report): they choose one row per player with
    ROW_NUMBER and read several columns off it, so the club cannot simply
    be aggregated without also moving position / eligible_slots to a
    different row. The window form leaves the row choice alone and fixes
    only the label. Verified to return the same value on both engines in
    window position as well as aggregate position.

    Mirrors the dbt layer's `latest_by` macro, and exists for the same
    reason (MLB-168): MAX() over a label column picks the string that
    sorts last, not the most recent one. That was invisible while
    MLB-159 froze pro_team to a single value per player, and stops being
    invisible the moment the club label is game-accurate.

    Nulling the ORDERING expression rather than the value is the whole
    trick. Snowflake's MAX_BY returns whatever sits on the greatest
    ordering row, NULL included -- and post-flip a player's pro_team is
    NULL on every day he did not appear, so the unguarded spelling
    blanks the label for anyone whose last day in scope was a rest day.
    A group with no labelled row at all still returns NULL, which is the
    honest answer.

    Deliberately NOT in the dialect section below: the guarded form was
    verified to return the same value on both engines (the UNGUARDED one
    does not -- Snowflake gives NULL where DuckDB skips to the last
    labelled row, which is exactly the divergence this spelling avoids).
    """
    fragment = (f"MAX_BY({value}, CASE WHEN {value} IS NULL "
                f"THEN NULL ELSE {order_by} END)")
    if partition_by is not None:
        fragment += f" OVER (PARTITION BY {partition_by})"
    return fragment


# ---------------------------------------------------------------------------
# SQL dialect (MLB-10 phase 5). Most of the output layer's SQL is portable
# between Snowflake and DuckDB, and where it was not, the fix belonged in the
# SQL itself -- a reserved word renamed, TO_VARCHAR spelled CAST, an ORDER BY
# moved outside its aggregate. Those are gone from here on purpose: a shim is
# the wrong home for something one spelling can satisfy.
#
# LISTAGG is the case where no spelling can. Measured on both engines:
#
#   LISTAGG(v, sep) WITHIN GROUP (ORDER BY ...)   Snowflake OK,  DuckDB no
#   LISTAGG(v, sep ORDER BY ...)                  DuckDB OK,     Snowflake no
#
# DuckDB rejects WITHIN GROUP outright ("Unknown ordered aggregate"), not
# merely with multiple sort keys, and Snowflake rejects the inline form. So
# this is the one construct that genuinely needs per-engine emission, and it
# is the whole reason this section exists rather than a general shim layer.
#
# Emitted as a fragment into an f-string, exactly like league_predicate().
# ---------------------------------------------------------------------------

_dialect = 'snowflake'

_SUPPORTED_DIALECTS = ('snowflake', 'duckdb')


def set_dialect(name):
    """Pin the SQL dialect the output layer emits. Defaults to snowflake,
    which is what the live pipeline runs; the DuckDB target sets this."""
    global _dialect
    if name not in _SUPPORTED_DIALECTS:
        raise ValueError(
            f"unknown dialect {name!r}; expected one of {_SUPPORTED_DIALECTS}"
        )
    _dialect = name


def dialect():
    """The active SQL dialect."""
    return _dialect


def listagg(expr, sep, order_by, distinct=False):
    """Ordered string aggregation, spelled for the active engine.

    `sep` is a Python string and is quoted here, so call sites read
    listagg('lineup_slot', ', ', 'sort_order, lineup_slot') rather than
    carrying their own quoting.

    Note that ordering is NOT optional in this helper. Unordered LISTAGG
    happens to be portable already, so a call site that does not care about
    order should keep writing plain SQL rather than route through here --
    the helper exists for the divergence, not for uniformity.
    """
    quoted_sep = "'" + str(sep).replace("'", "''") + "'"
    prefix = 'DISTINCT ' if distinct else ''
    if _dialect == 'duckdb':
        return f"LISTAGG({prefix}{expr}, {quoted_sep} ORDER BY {order_by})"
    return (f"LISTAGG({prefix}{expr}, {quoted_sep}) "
            f"WITHIN GROUP (ORDER BY {order_by})")


def flatten_array(expr, alias):
    """A JSON array flattened into rows, as a FROM-clause fragment.

    Snowflake's LATERAL FLATTEN has no DuckDB equivalent and DuckDB's
    unnest has no Snowflake one, so this is the second construct that
    genuinely needs per-engine emission. Both spellings mirror the dbt
    layer's `flatten_array` macro, which was verified against RAW: a NULL
    input and an empty array each yield zero rows on both engines.

    Pairs with json_text() -- see the warning there. Flattening without
    unwrapping is the silent-value-change trap, not a style choice.
    """
    if _dialect == 'duckdb':
        return f"unnest(cast({expr} as json[])) as {alias}(value)"
    return f"LATERAL FLATTEN(input => {expr}) {alias}"


def json_text(expr):
    """The bare text of an ALREADY-EXTRACTED JSON value.

    Snowflake's `VARIANT::string` UNWRAPS a JSON string to its text;
    DuckDB's `cast(json as varchar)` SERIALIZES it and keeps the quotes.
    Same SQL, no error, different values -- `'1B'` against `'"1B"'` -- so
    a comparison like `NOT IN ('BE','IL')` quietly stops matching. The
    dbt layer hit exactly this and documents it; this is its output-layer
    twin, and it uses the same DuckDB spelling (`->>'$'`).

    Use this for anything flatten_array() hands back. The two belong
    together: the flatten is loud when it is wrong, this one is silent.
    """
    if _dialect == 'duckdb':
        return f"({expr}->>'$')"
    return f"{expr}::string"


def league_file_tag():
    """League segment for output artifact filenames (MLB-58: log-file
    naming is league-scoped so multi-league runs never collide). Empty
    for the registry's default league -- the pre-registry filenames stay
    byte-identical -- and '<key>_' for every other league."""
    if league_key() == get_league(None).key:
        return ''
    return f"{league_key()}_"
