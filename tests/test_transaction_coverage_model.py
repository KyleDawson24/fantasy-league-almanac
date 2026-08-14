"""stg_transaction_coverage against a real dbt build (MLB-243).

A throwaway DuckDB holding only the two RAW tables the model reads. No
project data, no network, no warehouse, no Google surface.

What matters here is the RESOLUTION: which verdict wins when a league-season
has several, and whether a league captured before coverage existed still
qualifies. Getting that wrong either strands existing leagues out of
`fct_roster_stints` or lets one flaky 401 un-prove an earlier success.
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_DIR = REPO_ROOT / "dbt_league"
PROFILES_DIR = PROJECT_DIR / "profiles"
SEASON = 2026


def _coverage(outcome, topics=None, status=200, attempts=1):
    return json.dumps({
        "outcome": outcome,
        "topic_count": topics,
        "http_status": status,
        "attempts": attempts,
    })


def _topic(msg_type=178):
    """One ACTIVITY_TRANSACTIONS topic in ESPN's shape."""
    return {"id": "t1", "date": 1785536280000,
            "messages": [{"messageTypeId": msg_type, "targetId": 101,
                          "from": 0, "to": 1}]}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    duckdb = pytest.importorskip("duckdb")
    if not (PROJECT_DIR / "dbt_packages").is_dir():
        pytest.skip("dbt_packages missing; run dbt deps")

    root = tmp_path_factory.mktemp("transaction_coverage")
    db_path = root / "ESPN_FANTASY.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("create schema RAW")
    con.execute("create schema ANALYTICS")
    for table in ("TRANSACTIONS", "TRANSACTION_COVERAGE"):
        con.execute(f"""
            create table RAW.{table} (
                SEASON_YEAR decimal(38,0), RAW_JSON json,
                EXTRACTED_AT timestamp, LEAGUE_KEY varchar)
        """)

    early = datetime(2026, 8, 1, 9, 0, 0)
    late = datetime(2026, 8, 14, 9, 0, 0)

    rows = [
        # A league read successfully, with activity.
        (SEASON, _coverage("SERVED_NONEMPTY", 121), early, "served-busy"),
        # A quiet league: read, genuinely nothing. THE case the old gate
        # could not tell from a lockout.
        (SEASON, _coverage("SERVED_EMPTY", 0), early, "served-quiet"),
        # Never got in.
        (SEASON, _coverage("UNAUTHORIZED", None, 401, 5), early, "locked-out"),
        # ESPN serves no board for the season.
        (SEASON, _coverage("NOT_SERVED", None, 404, 1), early, "no-board"),
        # PRESERVATION: read successfully, then a later attempt flapped.
        (SEASON, _coverage("SERVED_NONEMPTY", 47), early, "flapped"),
        (SEASON, _coverage("UNAUTHORIZED", None, 401, 5), late, "flapped"),
    ]
    con.executemany(
        "insert into RAW.TRANSACTION_COVERAGE values (?, ?, ?, ?)", rows)

    # A legacy league: transaction rows captured before coverage existed,
    # and therefore no coverage row at all.
    con.execute(
        "insert into RAW.TRANSACTIONS values (?, ?, ?, ?)",
        [SEASON, json.dumps([_topic()]), early, "legacy-league"])
    # The busy league also has its topics staged, so the legacy branch must
    # not double-count it.
    con.execute(
        "insert into RAW.TRANSACTIONS values (?, ?, ?, ?)",
        [SEASON, json.dumps([_topic()]), early, "served-busy"])
    con.close()

    env = dict(os.environ, DBT_DUCKDB_PATH=str(db_path))
    result = subprocess.run(
        [sys.executable, "-m", "dbt.cli.main", "run", "--select",
         "stg_transactions", "stg_transaction_coverage",
         "--project-dir", str(PROJECT_DIR), "--profiles-dir",
         str(PROFILES_DIR), "--target", "duckdb"],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True)
    if result.returncode != 0:
        pytest.fail("coverage build failed:\n" +
                    result.stdout[-7000:] + result.stderr[-1500:])

    con = duckdb.connect(str(db_path))
    yield lambda sql: con.execute(sql).fetchall()
    con.close()


def _verdict(built, league):
    rows = built(f"""
        select outcome, has_transaction_log, is_proven_empty
        from ANALYTICS.stg_transaction_coverage
        where league_key = '{league}'
    """)
    assert len(rows) == 1, f'{league} has {len(rows)} coverage rows, want 1'
    return rows[0]


def test_a_served_nonempty_log_opens_the_gate(built):
    outcome, gated, proven_empty = _verdict(built, 'served-busy')
    assert outcome == 'SERVED_NONEMPTY'
    assert gated is True
    assert proven_empty is False


def test_a_served_empty_log_opens_the_gate_and_proves_zero(built):
    """THE POINT OF THE TICKET. A quiet league is normal, and its stints
    must build; zero staged rows is not evidence of anything by itself."""
    outcome, gated, proven_empty = _verdict(built, 'served-quiet')
    assert outcome == 'SERVED_EMPTY'
    assert gated is True, 'a read-but-empty board must build roster stints'
    assert proven_empty is True


def test_an_unauthorized_log_keeps_the_gate_shut(built):
    outcome, gated, proven_empty = _verdict(built, 'locked-out')
    assert outcome == 'UNAUTHORIZED'
    assert gated is False
    assert proven_empty is False, (
        'an unavailable feed must never read as proven-zero activity'
    )


def test_a_not_served_season_keeps_the_gate_shut(built):
    outcome, gated, proven_empty = _verdict(built, 'no-board')
    assert outcome == 'NOT_SERVED'
    assert gated is False
    assert proven_empty is False


def test_a_later_unavailable_attempt_cannot_un_prove_an_earlier_success(built):
    """The feed flaps. A 401 after a good capture must not close the gate on
    a league whose log we already hold."""
    outcome, gated, _ = _verdict(built, 'flapped')
    assert outcome == 'SERVED_NONEMPTY'
    assert gated is True


def test_a_league_captured_before_coverage_existed_still_qualifies(built):
    """ADDITIVE. Staged transaction rows are themselves proof the board was
    read, so no league that builds stints today stops building them."""
    outcome, gated, proven_empty = _verdict(built, 'legacy-league')
    assert outcome == 'SERVED_NONEMPTY'
    assert gated is True
    assert proven_empty is False


def test_the_grain_is_one_row_per_league_season(built):
    assert built("""
        select count(*) from (
            select league_key, season_year
            from ANALYTICS.stg_transaction_coverage
            group by 1, 2 having count(*) > 1)
    """) == [(0,)]


def test_roster_stints_gate_reads_coverage_not_staged_rows():
    """Pin the seam itself: gating on exploded rows is what conflated a
    quiet league with a locked-out one."""
    sql = (PROJECT_DIR / 'models' / 'marts' / 'core' /
           'fct_roster_stints.sql').read_text(encoding='utf-8')
    assert "ref('stg_transaction_coverage')" in sql
    assert 'has_transaction_log' in sql
    assert "select distinct league_key, season_year from {{ ref('stg_transactions') }}" \
        not in sql
