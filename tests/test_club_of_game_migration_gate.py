"""The club-flip migration gate, run against fixtures (MLB-200).

`dbt_league/tests/assert_club_of_game_migrated.sql` is the gate that stops
an un-migrated install from building green while its affinity chart goes
silently null. A gate is only worth having if it has been seen to fail, so
these tests run THE SHIPPED SQL -- read off disk, `ref()` substituted --
against two fixtures:

    pre-flip   RAW that never had the backfill run. Every club is null.
               Must FAIL, and must say "NOT MIGRATED" rather than leaving
               the upgrader to guess.
    migrated   RAW after the backfill. Must PASS, and must still exempt
               the FA residual that is deliberately left null.

Reading the real file rather than restating its logic is the point: a test
that reimplements the query proves the reimplementation works.

DuckDB does the SQL. It is not installed in the default venv, so the query
runs in whichever interpreter on this machine can import it -- the same
resolution tests/test_demo_isolation.py uses -- and skips when none can.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(REPO, "dbt_league", "tests",
                    "assert_club_of_game_migrated.sql")
TIMEOUT = 120

COLUMNS = ("league_key", "season_year", "scoring_period", "player_id",
           "lineup_slot", "games_played", "pro_team")


def _duckdb_python():
    for candidate in (r"C:\Users\kyled\.venvs\mlb10-duckdb\Scripts\python.exe",
                      sys.executable):
        if not os.path.exists(candidate):
            continue
        probe = subprocess.run([candidate, "-c", "import duckdb"],
                               capture_output=True, timeout=TIMEOUT)
        if probe.returncode == 0:
            return candidate
    return None


pytestmark = pytest.mark.skipif(
    _duckdb_python() is None, reason="no interpreter here can import duckdb")


def gate_sql():
    """The shipped gate, with dbt's ref() resolved to a plain table name."""
    with open(GATE, encoding="utf-8") as f:
        sql = f.read()
    resolved, n = re.subn(r"\{\{\s*ref\(\s*['\"]stg_box_scores['\"]\s*\)\s*\}\}",
                          "stg_box_scores", sql)
    assert n == 1, (
        f"expected exactly one ref(stg_box_scores) in the gate, found {n}. "
        f"If the gate grew another ref, teach this substitution about it -- "
        f"do not let the test silently run different SQL than ships."
    )
    return resolved


def run_gate(rows):
    """Load `rows` into a stand-in stg_box_scores and run the gate.

    Returns the gate's result rows as a list of dicts. Zero rows = pass.
    """
    script = r"""
import json, sys
import duckdb

rows = json.loads(sys.argv[1])
sql = sys.argv[2]

con = duckdb.connect()
con.execute(
    "create table stg_box_scores ("
    " league_key varchar, season_year integer, scoring_period integer,"
    " player_id integer, lineup_slot varchar, games_played integer,"
    " pro_team varchar)"
)
con.executemany(
    "insert into stg_box_scores values (?, ?, ?, ?, ?, ?, ?)",
    [[r[c] for c in %r] for r in rows],
)
cur = con.execute(sql)
names = [d[0] for d in cur.description]
print(json.dumps([dict(zip(names, r)) for r in cur.fetchall()]))
""" % (COLUMNS,)

    proc = subprocess.run(
        [_duckdb_python(), "-c", script, json.dumps(rows), gate_sql()],
        capture_output=True, text=True, encoding="utf-8", timeout=TIMEOUT)
    assert proc.returncode == 0, (
        f"the gate SQL did not execute:\n{proc.stderr}")
    return json.loads(proc.stdout)


def row(player_id, pro_team, lineup_slot="1B", games_played=1,
        scoring_period=100, season_year=2026, league_key="espn-main"):
    return dict(zip(COLUMNS, (league_key, season_year, scoring_period,
                              player_id, lineup_slot, games_played, pro_team)))


# --------------------------------------------------------------------------
# Pre-flip RAW must fail.
# --------------------------------------------------------------------------

PRE_FLIP = [
    row(1, None), row(2, None), row(3, None),
    row(4, None, lineup_slot="SP"),
    row(5, None, lineup_slot="BE", games_played=0),   # did not play
]

MIGRATED = [
    row(1, "LAD"), row(2, "NYY"), row(3, "SFG"),
    row(4, "ATL", lineup_slot="SP"),
    row(5, None, lineup_slot="BE", games_played=0),   # did not play
    row(6, None, lineup_slot="FA"),                   # MLB-193 residual
]


def test_pre_flip_raw_fails_the_gate():
    """The whole point: an un-migrated install must not build green."""
    hits = run_gate(PRE_FLIP)
    assert hits, (
        "pre-flip RAW passed the gate. An install that never ran the "
        "backfill would build every model green with a null affinity chart."
    )
    assert {h["player_id"] for h in hits} == {1, 2, 3, 4}


def test_pre_flip_failure_says_it_is_a_migration_not_a_data_gap():
    """A refusal the upgrader cannot act on is a refusal they will ignore."""
    hits = run_gate(PRE_FLIP)
    reasons = {h["failure_reason"] for h in hits}
    assert len(reasons) == 1
    reason = reasons.pop()
    assert "NOT MIGRATED" in reason
    assert "--backfill-club-of-game" in reason, (
        "the failure must name the command that fixes it"
    )


def test_a_did_not_play_row_is_never_expected_to_carry_a_club():
    """NULL means 'did not appear that day', which is the honest answer."""
    hits = run_gate(PRE_FLIP)
    assert 5 not in {h["player_id"] for h in hits}


# --------------------------------------------------------------------------
# Migrated RAW must pass -- including the residual it is meant to tolerate.
# --------------------------------------------------------------------------

def test_migrated_raw_passes_the_gate():
    assert run_gate(MIGRATED) == []


def test_the_fa_residual_is_exempt():
    """MLB-193: free agents ESPN no longer serves have no split to name a
    club, and are left null deliberately rather than guessed. Every
    missing-club producing row in the warehouse today is one of these."""
    hits = run_gate(MIGRATED + [row(7, None, lineup_slot="FA"),
                                row(8, None, lineup_slot="FA")])
    assert hits == [], "the exempted FA residual tripped the gate"


def test_a_genuine_gap_in_a_migrated_period_still_fails():
    """The gate is not just a migration check. One unattributed rostered
    player-day inside an otherwise-attributed period is a real defect, and
    it reports as a gap rather than as a missing migration."""
    hits = run_gate(MIGRATED + [row(9, None, lineup_slot="2B")])
    assert [h["player_id"] for h in hits] == [9]
    assert "NOT MIGRATED" not in hits[0]["failure_reason"]
    assert "club evidence missing" in hits[0]["failure_reason"]


def test_one_unmigrated_period_among_migrated_ones_is_caught():
    """Periods are judged individually, so a partially-backfilled warehouse
    -- the state a half-finished backfill leaves -- does not hide behind
    the periods that did land."""
    mixed = MIGRATED + [row(10, None, scoring_period=200),
                        row(11, None, scoring_period=200)]
    hits = run_gate(mixed)
    assert {h["player_id"] for h in hits} == {10, 11}
    assert all("NOT MIGRATED" in h["failure_reason"] for h in hits)


def test_club_attribution_fingerprint_is_preserved():
    """The gate must not change what is attributed, only whether the build
    is allowed to proceed. Every migrated row keeps its club."""
    attributed = {r["player_id"]: r["pro_team"] for r in MIGRATED
                  if r["pro_team"] is not None}
    assert attributed == {1: "LAD", 2: "NYY", 3: "SFG", 4: "ATL"}
    assert run_gate(MIGRATED) == [], (
        "the fingerprint above is the state the gate must call clean"
    )
