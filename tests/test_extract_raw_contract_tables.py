"""The warehouse sink seeds its conditionally-written RAW tables -- MLB-222 C-5.

Seven RAW tables had their `CREATE TABLE IF NOT EXISTS` INSIDE a
conditional write: DRAFT_PICKS under `if draft_rows:`, TRANSACTIONS under
`if topics:`, TEAM_STANDINGS under `if teams:`, and the four MLB-227
settings blocks under `if block is None: continue`. A league that has
never drafted therefore never created DRAFT_PICKS, and `dbt run` failed
resolving source('raw', 'draft_picks') -- a missing relation, not an
empty read.

The local sink already had this right (LocalParquetSink.ensure_contract_
tables, covered in tests/test_local_raw_writer.py). These tests are the
warehouse half.

Real `SnowflakeSink` over a fake connection, same shape as
test_extract_club_of_game.py -- the cursor-level DDL is the thing under
test, so faking the sink would skip exactly what broke.
"""

import importlib.util
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[1]

load_dotenv()
os.environ.setdefault("LEAGUE_ID", "0")

_spec = importlib.util.spec_from_file_location(
    "extract_contract_under_test", _REPO_ROOT / "extract" / "extract.py")
extract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(extract)


class _FakeCursor:
    """Records SQL and answers the catalog probe from `existing`."""

    def __init__(self, existing):
        self._existing = existing
        self.executed = []
        self._last_count = 0

    def execute(self, sql, params=None):
        self.executed.append(sql)
        if "INFORMATION_SCHEMA.TABLES" in sql:
            self._last_count = 1 if params[0] in self._existing else 0

    def fetchone(self):
        return (self._last_count,)

    def close(self):
        pass


class _FakeConn:
    def __init__(self, existing=()):
        self.existing = set(existing)
        self.cursors = []
        self.commits = 0

    def cursor(self):
        c = _FakeCursor(self.existing)
        self.cursors.append(c)
        return c

    def commit(self):
        self.commits += 1


def _created_tables(sql_log):
    """Table names from CREATE TABLE IF NOT EXISTS statements."""
    out = []
    for sql in sql_log:
        s = " ".join(sql.split())
        marker = "CREATE TABLE IF NOT EXISTS "
        if marker in s:
            out.append(s.split(marker, 1)[1].split(" ", 1)[0].split("(")[0])
    return out


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------
def test_the_two_named_tables_are_in_the_conditional_set():
    """DRAFT_PICKS and TRANSACTIONS are the reported symptom."""
    assert "DRAFT_PICKS" in extract.CONDITIONAL_RAW_TABLES
    assert "TRANSACTIONS" in extract.CONDITIONAL_RAW_TABLES


def test_every_conditionally_written_table_is_covered():
    """Every snapshot table shares the identical defect -- their CREATE
    is inside load_snapshot_to_snowflake, which a `block is None` skip
    never reaches. Fixing only the two named ones would leave a league
    whose payload omits tradeSettings failing the same way."""
    for table in extract.SNAPSHOT_TABLES:
        assert table in extract.CONDITIONAL_RAW_TABLES


def test_all_conditional_tables_are_in_the_raw_schema_contract():
    """Whatever we create has to be a table the contract actually declares
    -- otherwise this seeds a relation the DuckDB path knows nothing about."""
    import json
    contract = json.loads(
        (_REPO_ROOT / "config" / "raw_schema_contract.json").read_text())
    for table in extract.CONDITIONAL_RAW_TABLES:
        assert table in contract["tables"], f"{table} absent from contract"


# ---------------------------------------------------------------------------
# The behaviour
# ---------------------------------------------------------------------------
def test_first_run_creates_every_conditional_table():
    """A league with nothing but box scores still ends up with every
    conditionally-written relation present and empty."""
    conn = _FakeConn(existing=())
    created = extract.SnowflakeSink(conn).ensure_contract_tables()

    assert set(created) == set(extract.CONDITIONAL_RAW_TABLES)
    issued = _created_tables(conn.cursors[0].executed)
    assert set(issued) == set(extract.CONDITIONAL_RAW_TABLES)
    assert conn.commits == 1


def test_repeat_run_reports_nothing_created_but_still_issues_idempotent_ddl():
    """Kyle's warehouse already has them all. The run must stay quiet --
    and must not depend on the catalog probe for correctness, since the
    DDL is IF NOT EXISTS either way."""
    conn = _FakeConn(existing=extract.CONDITIONAL_RAW_TABLES)
    created = extract.SnowflakeSink(conn).ensure_contract_tables()

    assert created == []
    assert set(_created_tables(conn.cursors[0].executed)) == \
        set(extract.CONDITIONAL_RAW_TABLES)


def test_league_key_column_is_ensured_on_every_table():
    """Legacy installs predate the registry; the seeding path has to carry
    the same self-heal the loaders do, or a pre-registry table comes back
    without league_key."""
    conn = _FakeConn(existing=("DRAFT_PICKS",))
    extract.SnowflakeSink(conn).ensure_contract_tables()

    altered = [s for s in conn.cursors[0].executed
               if "ADD COLUMN IF NOT EXISTS league_key" in s]
    assert len(altered) == len(extract.CONDITIONAL_RAW_TABLES)


def test_ddl_carries_the_snapshot_shape():
    """They are all append-only VARIANT snapshots; the staging models pick
    the latest row per league+season via extracted_at."""
    conn = _FakeConn()
    extract.SnowflakeSink(conn).ensure_contract_tables()

    ddl = [s for s in conn.cursors[0].executed
           if "CREATE TABLE IF NOT EXISTS" in s]
    for stmt in ddl:
        flat = " ".join(stmt.split())
        assert "season_year INTEGER" in flat
        assert "raw_json VARIANT" in flat
        assert "extracted_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()" in flat
        assert "league_key VARCHAR" in flat
