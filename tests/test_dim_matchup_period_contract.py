"""The league-scoped matchup-period contract, built for real (MLB-235 4A).

Builds `+dim_matchup_period` with dbt against a THROWAWAY DuckDB holding only
fixtures this file wrote, then queries the result.

TWO SUITES, AND THE SPLIT IS THE POINT (MLB-235 correction). This file used to
have one, and it read the legacy calendar out of `dbt_league/league_config/` --
which is the maintainer's REAL espn-main seed on his disk and a header-only
template at HEAD. So the whole contract could only be asserted on one laptop,
and CI could never run it. The two claims tangled together there are genuinely
different, so they are now genuinely separate:

  SYNTHETIC CONTRACT (`contract` fixture) -- everything the model PROMISES:
    legacy-only fallback, derived-over-legacy precedence, the sparse override
    seam, cross-league isolation, dates, abnormal labels, eligibility, and the
    published season standard. Built against a temporary seed directory this
    file generates, handed to dbt through DBT_LEAGUE_CONFIG, with the legacy
    league pointed at a synthetic key through the model's own
    `legacy_matchup_schedule_league` var. Every identity, season and value in
    it is invented -- there is no espn-main here and no year anybody played.
    Runs everywhere, and a clean public checkout must finish it with zero
    failures.

  PRIVATE PIONEER EVIDENCE (`pioneer` fixture) -- the one claim that is ABOUT
    the real seed and cannot be made by a synthetic stand-in: that Kyle's 48
    hand-maintained rows and their four recorded anomalies still survive a
    warehouse which has never captured a matchup schedule. It reads his seed
    where it already sits, copies nothing out of it, and skips LOUDLY when
    that file is the committed template. A skip here is the honest answer on
    a machine that does not have the evidence -- it is not permission to skip
    the contract suite, which has no such dependency.

The mutation check that keeps the two apart is
`test_removing_the_seed_directory_override_loses_the_synthetic_calendar`:
strip DBT_LEAGUE_CONFIG from the synthetic build and the legacy rows the
contract asserts on disappear. If that ever passes, the contract has quietly
gone back to reading whatever seed the checkout happens to carry.

WHY IT BUILDS EMPTY FIRST, then inserts: every model in the selection is a
view over either RAW or a seeded table, so one build proves the empty-source
state and the fixtures can be inserted afterwards and the same views
re-queried. The empty case is therefore the state the models were created in
rather than a branch that might rot -- and the override seed is header-only in
the generated directory too, so a sparse override gets exercised by inserting
into its seeded table without any config on disk carrying a row.
"""

import csv
import json
import os
import re
import subprocess
import sys
import warnings
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PROJECT_DIR = REPO_ROOT / "dbt_league"
PROFILES_DIR = PROJECT_DIR / "profiles"
LEAGUE_CONFIG = PROJECT_DIR / "league_config"

# ---------------------------------------------------------------------------
# Synthetic identities. Nothing here names a real league, and the seasons are
# deliberately years this project has no data for -- so if the seed directory
# override ever fails to apply, the real calendar's 2025/2026 rows cannot
# accidentally satisfy an assertion written for 1998.
# ---------------------------------------------------------------------------
ALPHA = "contract-alpha"    # owns the legacy calendar, as the pioneer does
BETA = "contract-beta"      # a second league, no legacy rows of its own
SEASON_A = 1998             # both leagues play it; alpha has a capture
SEASON_B = 1999             # alpha's legacy-only season, never captured
SEASON_SPARSE = 1996        # beta only, and below the evidence floor

# The synthetic legacy calendar, as (season, periods, opener, {period: reason},
# playoff periods). Generated rather than typed out so the shape is readable
# and the dates cannot drift out of step with the period numbers.
LEGACY_SPEC = (
    (SEASON_A, 22, date(1998, 4, 6),
     {1: "Extended opening stretch", 12: "Midseason recess"}, (20, 21, 22)),
    (SEASON_B, 25, date(1999, 4, 5),
     {3: "Weather-shortened stretch", 17: "Doubleheader makeup week"},
     (23, 24, 25)),
)
LEGACY_ROWS = 47            # 22 + 25
LEGACY_ABNORMAL = [(SEASON_A, 1), (SEASON_A, 12), (SEASON_B, 3), (SEASON_B, 17)]
LEGACY_ELIGIBLE = LEGACY_ROWS - len(LEGACY_ABNORMAL)

# The pioneer's recorded shape, which is what the private evidence checks.
# Coordinates and counts only -- no dates, no content, nothing derived from
# the file beyond what was already recorded here when the seed was readable.
PIONEER_LEAGUE = "espn-main"
PIONEER_ROWS = 48
PIONEER_ABNORMAL = [(2025, 1), (2025, 16), (2026, 1), (2026, 15)]
PIONEER_ELIGIBLE = 44


# ---------------------------------------------------------------------------
# Synthetic payloads -- the same wire shape rungs 1-3 use
# ---------------------------------------------------------------------------
def _side(scoring_periods):
    return {"teamId": 987654,
            "pointsByScoringPeriod": {str(sp): 0.0 for sp in scoring_periods}}


def _payload(period_lengths, *, season_year, current=None):
    schedule, sp = [], 1
    for index, length in enumerate(period_lengths, start=1):
        members = list(range(sp, sp + length))
        sp += length
        for _ in range(2):
            schedule.append({"matchupPeriodId": index,
                             "home": _side(members), "away": _side(members)})
    return {"seasonId": season_year,
            "status": {"currentMatchupPeriod": current or len(period_lengths) + 1},
            "schedule": schedule}


def _skip_loudly(reason):
    banner = f"[SKIPPED - NOT VERIFIED] {reason}"
    warnings.warn(banner, UserWarning, stacklevel=2)
    print(f"\n!! {banner}", file=sys.stderr)
    pytest.skip(reason)


# ---------------------------------------------------------------------------
# The generated seed directory
# ---------------------------------------------------------------------------
def _synthetic_seed_dir(root):
    """A complete league_config directory carrying nobody's real league.

    Every seed is a header-only copy -- headers read from the working tree so
    the column list cannot drift from what dbt expects, and header-only so not
    one row of the maintainer's data is read, let alone written anywhere. The
    one file that then gets rows is `matchup_schedule.csv`, and they are the
    invented calendar above.

    dbt resolves a seed by FILENAME, so the directory has to be complete: a
    missing file is not an empty seed, it is a broken ref().
    """
    seed_dir = root / "synthetic_league_config"
    seed_dir.mkdir()
    for source in sorted(LEAGUE_CONFIG.glob("*.csv")):
        with source.open(newline="", encoding="utf-8") as handle:
            header = next(csv.reader(handle), [])
        with (seed_dir / source.name).open("w", newline="",
                                           encoding="utf-8") as out:
            csv.writer(out).writerow(header)

    with (seed_dir / "matchup_schedule.csv").open("w", newline="",
                                                  encoding="utf-8") as out:
        writer = csv.writer(out)
        writer.writerow(["season_year", "matchup_period", "start_date",
                         "end_date", "is_abnormal", "abnormal_reason",
                         "is_playoff", "playoff_round"])
        for season, periods, opener, abnormal, playoffs in LEGACY_SPEC:
            for period in range(1, periods + 1):
                start = opener + timedelta(days=(period - 1) * 7)
                writer.writerow([
                    season, period, start.isoformat(),
                    (start + timedelta(days=6)).isoformat(),
                    str(period in abnormal).lower(),
                    abnormal.get(period, ""),
                    str(period in playoffs).lower(),
                    f"Round {playoffs.index(period) + 1}"
                    if period in playoffs else "",
                ])
    return seed_dir


def _dbt(argv, env):
    """One dbt invocation, always carrying the synthetic legacy league.

    The var is the model's own seam (`dim_matchup_period.sql` reads
    `legacy_matchup_schedule_league`, defaulting to espn-main), so pointing it
    at a synthetic key is a supported configuration rather than a test-only
    back door. Every call in this fixture passes it -- a `dbt test` or a
    follow-up `dbt build` that forgot would resolve the legacy rows to a
    different league than the build that created them.

    CAUTIOUS INDIRECT SELECTION (MLB-229), and here for the same reason the var
    is: every selection in this file names a SUBGRAPH, and dbt's default eager
    rule additionally pulls in any test with at least ONE selected parent. A
    test spanning this subgraph and another therefore arrives in a build
    carrying only half of what it reads, and fails on a relation this fixture
    never created. Cautious takes a test only when ALL of its parents are
    built, which is the question this fixture is already asking. Measured: it
    drops exactly the cross-subgraph tests and nothing this build relies on.
    """
    return subprocess.run(
        [sys.executable, "-m", "dbt.cli.main", *argv,
         "--project-dir", str(PROJECT_DIR), "--profiles-dir", str(PROFILES_DIR),
         "--target", "duckdb",
         "--indirect-selection", "cautious",
         "--vars", json.dumps({"legacy_matchup_schedule_league": ALPHA})],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True)


def _empty_raw(db):
    """A DuckDB with the three RAW sources declared and no rows in any.

    MLB_SEASON_CALENDAR is created and left EMPTY on purpose: dim_matchup_period
    derives start/end dates from it, so `+dim_matchup_period` selects
    stg_mlb__season_calendar and the source has to exist -- but every assertion
    here about the legacy seed owning the calendar is measured with no anchor
    captured, which is the state that made those answers legacy-only.
    """
    import duckdb

    con = duckdb.connect(str(db))
    con.execute("create schema if not exists RAW")
    for table in ("MATCHUP_SCHEDULE", "SCHEDULE_SETTINGS", "MLB_SEASON_CALENDAR"):
        con.execute(f"""
            create table RAW.{table} (
                SEASON_YEAR decimal(38,0), RAW_JSON json,
                EXTRACTED_AT timestamp, LEAGUE_KEY varchar)
        """)
    con.close()


# ===========================================================================
# THE SYNTHETIC CONTRACT FIXTURE
# ===========================================================================
@pytest.fixture(scope="module")
def contract(tmp_path_factory):
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover
        _skip_loudly(f"duckdb not importable ({exc}); the contract was NOT built")

    root = tmp_path_factory.mktemp("mlb235_4a")
    db = root / "ESPN_FANTASY.duckdb"
    seed_dir = _synthetic_seed_dir(root)
    _empty_raw(db)

    env = dict(os.environ, DBT_DUCKDB_PATH=str(db),
               DBT_LEAGUE_CONFIG=str(seed_dir))
    result = _dbt(["build", "--select", "+dim_matchup_period"], env)
    if result.returncode != 0:
        pytest.fail("build over an EMPTY RAW.MATCHUP_SCHEDULE failed:\n"
                    f"{result.stdout[-4000:]}\n{result.stderr[-1500:]}")

    # Held in a mutable cell because `retest` below has to RELEASE the file:
    # DuckDB is single-writer, so dbt cannot open the database while this
    # connection has it. Closing and reopening around the dbt run is the whole
    # reason the connection is not a plain local.
    state = {"con": duckdb.connect(str(db))}
    q = lambda sql, p=None: state["con"].execute(sql, p or []).fetchall()

    # Measured BEFORE any capture exists: with nothing derived, every answer
    # has to come from the legacy calendar alone.
    q.legacy_only = {
        "rows": q("select count(*) from ANALYTICS.dim_matchup_period")[0][0],
        "leagues": [r[0] for r in q(
            "select distinct league_key from ANALYTICS.dim_matchup_period")],
        "sources": dict(q("""select effective_source, count(*)
                             from ANALYTICS.dim_matchup_period group by 1""")),
        "abnormal": [(int(a), int(b)) for a, b in q("""
            select season_year, matchup_period from ANALYTICS.dim_matchup_period
            where is_abnormal order by 1, 2""")],
        "eligible": q("""select count(*) from ANALYTICS.dim_matchup_period
                         where is_record_eligible""")[0][0],
        "dated": q("""select count(*) from ANALYTICS.dim_matchup_period
                      where start_date is not null""")[0][0],
    }

    stamped = datetime(2026, 8, 11, 12, 0, 0)
    # alpha SEASON_A: derivation covers 1..18, legacy covers 1..22. Period 12
    # is eleven days against a standard of seven, so it is abnormal by
    # DERIVATION as well as in the seed -- which is the case that proves the
    # human label wins over the generated one.
    state["con"].execute("insert into RAW.MATCHUP_SCHEDULE values (?, ?, ?, ?)",
                [SEASON_A, json.dumps(_payload([7] * 11 + [11] + [7] * 10,
                                               season_year=SEASON_A, current=19)),
                 stamped, ALPHA])
    # A SECOND league, same season, different shape and no legacy rows.
    state["con"].execute("insert into RAW.MATCHUP_SCHEDULE values (?, ?, ?, ?)",
                [SEASON_A, json.dumps(_payload([5] * 9 + [11],
                                               season_year=SEASON_A, current=10)),
                 stamped, BETA])
    # A league with membership but no legacy seed and no derivation verdict:
    # two closed periods is below the floor, so the season is undetermined.
    state["con"].execute("insert into RAW.MATCHUP_SCHEDULE values (?, ?, ?, ?)",
                [SEASON_SPARSE, json.dumps(_payload([7] * 6,
                                                    season_year=SEASON_SPARSE,
                                                    current=3)),
                 stamped, BETA])

    def retest():
        """Re-run the DECLARED dbt tests against the POPULATED fixture.

        The build above ran them while the relation was still empty, where a
        stale not_null on a nullable column and a stale grain test over two
        leagues both pass vacuously. This is the same assertions against real
        rows -- which is the only version of them that means anything.
        """
        # Release the file: DuckDB is single-writer and dbt needs it.
        state["con"].close()
        completed = _dbt(["test", "--select", "dim_matchup_period"], env)
        state["con"] = duckdb.connect(str(db))
        return completed

    def build_standard(fallback_seasons):
        """Build int_matchup_season_standard over a STUBBED daily fact.

        The real fct_player_daily_performance sits on the whole box-score
        chain, which no fixture can synthesize cheaply -- but dbt's ref()
        compiles to a relation NAME, so creating that relation ourselves lets
        the standard-resolution model build against exactly the rows we choose.
        `fallback_seasons` are the (league, season) pairs that get gameplay
        days; everything else has a derived standard and NO fallback row,
        which is the case the old fallback-anchored spelling dropped.
        """
        state["con"].execute("drop table if exists ANALYTICS.fct_player_daily_performance")
        state["con"].execute("""
            create table ANALYTICS.fct_player_daily_performance (
                league_key varchar, season_year bigint,
                matchup_period bigint, scoring_period bigint)
        """)
        for league, season, periods, days in fallback_seasons:
            for mp in range(1, periods + 1):
                for sp in range(1, days + 1):
                    state["con"].execute(
                        "insert into ANALYTICS.fct_player_daily_performance "
                        "values (?, ?, ?, ?)", [league, season, mp, mp * 100 + sp])
        state["con"].close()
        completed = _dbt(["build", "--select", "int_matchup_season_standard"], env)
        state["con"] = duckdb.connect(str(db))
        return completed

    q.seed_dir = seed_dir
    q.db_root = root
    q.build_standard = build_standard
    q.retest = retest
    yield q
    state["con"].close()


def _row(contract, league, season, period):
    rows = contract("""select is_abnormal, is_record_eligible, effective_source,
                         scoring_period_count, abnormal_reason, start_date,
                         is_playoff, playoff_round, derivation_status
                  from ANALYTICS.dim_matchup_period
                  where league_key = ? and season_year = ? and matchup_period = ?""",
                    [league, season, period])
    assert len(rows) <= 1, "grain violated"
    if not rows:
        return None
    keys = ("is_abnormal", "is_record_eligible", "effective_source",
            "scoring_period_count", "abnormal_reason", "start_date",
            "is_playoff", "playoff_round", "derivation_status")
    return dict(zip(keys, rows[0]))


def _override(contract, league, season, period, is_abnormal, reason=None):
    contract("insert into ANALYTICS.matchup_period_overrides values (?, ?, ?, ?, ?)",
             [league, season, period, is_abnormal, reason])


# ===========================================================================
# SYNTHETIC CONTRACT -- the seed directory override is load-bearing
# ===========================================================================
def test_the_contract_reads_the_generated_seed_and_not_the_checkout(contract):
    """The suite is asserting on the calendar THIS FILE wrote.

    Cheap to state and worth stating: every expectation below is a number
    chosen here, so if the build ever resolved a different seed directory the
    failures would be a confusing pile rather than one sentence.
    """
    assert contract.legacy_only["leagues"] == [ALPHA]
    assert contract.legacy_only["rows"] == LEGACY_ROWS


def test_removing_the_seed_directory_override_loses_the_synthetic_calendar(
        contract, tmp_path):
    """THE MUTATION CHECK. Build the same selection with DBT_LEAGUE_CONFIG
    stripped and the contract's legacy rows are gone.

    This is what stops the public suite from drifting back onto whatever
    calendar the checkout happens to carry. Without the override dbt seeds
    from `dbt_league/league_config/`, which is header-only at HEAD and the
    maintainer's real espn-main calendar on his disk -- so the rows that
    arrive are either none at all or 48 rows of 2025/2026 stamped onto the
    synthetic league key. Neither can satisfy an assertion about 1998, and
    that is true on both machines, which is what makes this checkable
    anywhere.

    Asserted as an ABSENCE of the synthetic calendar rather than as a build
    failure: the build still succeeds, which is exactly why the old
    arrangement could go unnoticed for as long as it did.
    """
    # The filename is not cosmetic: the duckdb profile derives `database` from
    # the path stem, so anything but ESPN_FANTASY.duckdb fails on credentials
    # before it ever reaches a seed -- which would "pass" this test for
    # entirely the wrong reason.
    db = tmp_path / "ESPN_FANTASY.duckdb"
    _empty_raw(db)

    env = dict(os.environ, DBT_DUCKDB_PATH=str(db))
    env.pop("DBT_LEAGUE_CONFIG", None)
    result = _dbt(["build", "--select", "+dim_matchup_period"], env)
    assert result.returncode == 0, (
        "the no-override build was supposed to SUCCEED and merely lose the "
        f"synthetic rows:\n{result.stdout[-3000:]}")

    import duckdb
    con = duckdb.connect(str(db))
    try:
        synthetic = con.execute(
            """select count(*) from ANALYTICS.dim_matchup_period
               where league_key = ? and season_year in (?, ?)""",
            [ALPHA, SEASON_A, SEASON_B]).fetchone()[0]
    finally:
        con.close()

    assert synthetic == 0, (
        "the synthetic legacy calendar survived without DBT_LEAGUE_CONFIG, so "
        "the contract suite is no longer proving it reads a generated seed")
    # And the fixture that DID set it has them, so the difference is the var.
    assert contract.legacy_only["rows"] == LEGACY_ROWS


# ===========================================================================
# SYNTHETIC CONTRACT -- legacy-only fallback (no capture anywhere)
# ===========================================================================
def test_a_legacy_only_league_keeps_its_calendar_with_no_capture(contract):
    """With an EMPTY RAW.MATCHUP_SCHEDULE the seeded calendar must still
    produce every row and every date. Existing behaviour cannot disappear
    because a warehouse has not backfilled the new capture."""
    assert contract.legacy_only["rows"] == LEGACY_ROWS
    assert contract.legacy_only["leagues"] == [ALPHA]
    assert contract.legacy_only["dated"] == LEGACY_ROWS


def test_with_no_capture_every_answer_comes_from_the_legacy_seed(contract):
    assert contract.legacy_only["sources"] == {"legacy_seed": LEGACY_ROWS}


def test_the_legacy_abnormal_flags_are_reproduced_exactly(contract):
    """The seeded anomalies, unchanged, and the eligibility that follows."""
    assert contract.legacy_only["abnormal"] == LEGACY_ABNORMAL
    assert contract.legacy_only["eligible"] == LEGACY_ELIGIBLE


# ===========================================================================
# SYNTHETIC CONTRACT -- Evidence 1: no cross-league bleed
# ===========================================================================
def test_a_second_league_does_not_inherit_the_legacy_calendar(contract):
    """The bug this rung exists to close: the seed carries no league_key, so
    its rows used to join to ANY league with the same period numbers."""
    other = contract("""select count(*) from ANALYTICS.dim_matchup_period
                        where league_key = ? and start_date is not null""",
                     [BETA])
    assert other == [(0,)], "the legacy league's dates reached another league"


def test_a_second_league_gets_its_own_shape(contract):
    """Same season, different period lengths. Nine fives and one eleven, so
    five is the standard and the long period is still live."""
    assert _row(contract, BETA, SEASON_A, 1)["scoring_period_count"] == 5
    assert _row(contract, BETA, SEASON_A, 1)["is_abnormal"] is False
    assert _row(contract, BETA, SEASON_A, 10) is None, "period 10 is the live one"

    abnormal = contract("""select matchup_period from ANALYTICS.dim_matchup_period
                           where league_key = ? and season_year = ? and is_abnormal
                           order by 1""", [BETA, SEASON_A])
    assert abnormal == []


def test_the_second_league_inherits_no_flags_or_playoff_labels(contract):
    rows = contract("""select count(*) from ANALYTICS.dim_matchup_period
                       where league_key = ?
                         and (is_playoff or playoff_round is not null)""",
                    [BETA])
    assert rows == [(0,)]


def test_the_legacy_league_keeps_its_own_playoff_labels(contract):
    """The other direction of the same isolation: the seed's playoff flags
    reach the league that owns them, and nothing captured a settings boundary
    to produce them any other way."""
    row = _row(contract, ALPHA, SEASON_A, 21)

    assert row["is_playoff"] is True
    assert row["playoff_round"] == "Round 2"


def test_the_dimension_grain_holds_across_leagues(contract):
    """Evidence 8. Two leagues share season and period numbers, so a grain
    slip would show up here rather than as a silent row multiplication."""
    dupes = contract("""select count(*) from (
                          select league_key, season_year, matchup_period
                          from ANALYTICS.dim_matchup_period
                          group by 1, 2, 3 having count(*) > 1)""")
    assert dupes == [(0,)]


# ===========================================================================
# SYNTHETIC CONTRACT -- Evidence 2: derivation wins when there is no override
# ===========================================================================
def test_derivation_supersedes_the_legacy_seed_where_both_answer(contract):
    """alpha SEASON_A period 1: the seed calls it abnormal, the platform says
    seven scoring periods in this fixture. The platform wins, and the row
    still carries the seed's dates."""
    row = _row(contract, ALPHA, SEASON_A, 1)

    assert row["effective_source"] == "derived"
    assert row["is_abnormal"] is False
    assert row["is_record_eligible"] is True
    assert row["scoring_period_count"] == 7
    assert row["start_date"] is not None, "legacy calendar must survive"


def test_a_period_derivation_cannot_reach_falls_back_to_the_seed(contract):
    """alpha SEASON_A periods 19-22 are at or past the live pointer, so the
    platform declines and the league keeps its existing answer."""
    row = _row(contract, ALPHA, SEASON_A, 20)

    assert row["effective_source"] == "legacy_seed"
    assert row["is_abnormal"] is False
    assert row["start_date"] is not None


def test_a_reason_does_not_survive_onto_a_row_now_called_normal(contract):
    """The seed's reason describes a verdict the platform has replaced. It
    must not stay attached to a row that is no longer abnormal."""
    assert _row(contract, ALPHA, SEASON_A, 1)["abnormal_reason"] is None


# ===========================================================================
# SYNTHETIC CONTRACT -- Evidence 5: unknown is not normal
# ===========================================================================
def test_unknown_yields_null_abnormality_and_false_eligibility(contract):
    """A league with membership but too little of it, and no legacy seed to
    fall back to: abnormality is genuinely unknown."""
    row = _row(contract, BETA, SEASON_SPARSE, 1)

    assert row["effective_source"] == "unknown"
    assert row["is_abnormal"] is None, "unknown must not be coerced to false"
    assert row["is_record_eligible"] is False, "unknown must not be eligible"
    assert row["derivation_status"] == "insufficient_evidence"


def test_the_gate_is_never_null_anywhere(contract):
    nulls = contract("""select count(*) from ANALYTICS.dim_matchup_period
                        where is_record_eligible is null""")
    assert nulls == [(0,)]


def test_eligibility_is_exactly_known_and_not_abnormal(contract):
    """coalesce(is_abnormal = false, false), asserted over every row rather
    than argued from the SQL."""
    wrong = contract("""select count(*) from ANALYTICS.dim_matchup_period
                        where is_record_eligible
                              <> coalesce(is_abnormal = false, false)""")
    assert wrong == [(0,)]


# ===========================================================================
# SYNTHETIC CONTRACT -- Evidence 3 and 4: the sparse override seam
# ===========================================================================
def test_an_override_of_true_excludes_a_derived_normal_period(contract):
    """The case the seam exists for: a commissioner-declared oddity whose
    scoring-period length is perfectly normal, so derivation cannot see it."""
    _override(contract, ALPHA, SEASON_A, 5, True, "Commissioner-voided week")
    row = _row(contract, ALPHA, SEASON_A, 5)

    assert row["effective_source"] == "override"
    assert row["is_abnormal"] is True
    assert row["is_record_eligible"] is False
    assert row["abnormal_reason"] == "Commissioner-voided week"


def test_an_override_of_false_admits_a_derived_abnormal_period(contract):
    """Explicit false must beat a derived true -- coalesce keys on NULL, not
    on falsehood, which is the property that makes this work."""
    _override(contract, BETA, SEASON_A, 10, False, None)
    _override(contract, BETA, SEASON_SPARSE, 2, False, "Counted by league vote")

    row = _row(contract, BETA, SEASON_SPARSE, 2)
    assert row["effective_source"] == "override"
    assert row["is_abnormal"] is False
    assert row["is_record_eligible"] is True


def test_an_override_needs_no_row_for_ordinary_periods(contract):
    """Sparse means sparse: the seam is exercised above with three rows, and
    every other period in the fixture still resolves without one."""
    resolved = contract("""select count(*) from ANALYTICS.dim_matchup_period
                           where effective_source in ('derived', 'legacy_seed')""")
    assert resolved[0][0] > 50


def test_the_committed_override_seed_is_empty():
    """A blanket false row for ordinary periods would re-create the
    hand-maintained flag this ticket removed.

    About the CHECKOUT rather than the fixture, so it takes no dbt build and
    holds on a clone: both committed override seeds are header-only.
    """
    for path in (REPO_ROOT / "dbt_league" / "league_config",
                 REPO_ROOT / "demo" / "league_config"):
        rows = list(csv.DictReader(
            (path / "matchup_period_overrides.csv").open(newline="")))
        assert rows == [], f"{path} ships override rows"


# ===========================================================================
# SYNTHETIC CONTRACT -- the declared contract over POPULATED rows
# ===========================================================================
def test_the_declared_dbt_tests_pass_with_unknown_rows_present(contract):
    """The gap this pass closes. The build's tests ran over an empty relation,
    so a stale `not_null` on the now-nullable is_abnormal and a stale
    single-league grain test would both have passed vacuously. Re-run them
    over the fixture: two leagues, an unknown period, and overrides."""
    # Overrides from the tests above are already in place; make sure the
    # unknown row and the second league are too.
    assert _row(contract, BETA, SEASON_SPARSE, 1)["is_abnormal"] is None
    assert _row(contract, BETA, SEASON_A, 1) is not None

    result = contract.retest()
    assert result.returncode == 0, (
        "the declared dbt tests fail against populated fixture rows:\n"
        + result.stdout[-4000:])
    assert "ERROR=0" in result.stdout


def test_unknown_abnormality_is_allowed_but_the_gate_is_not(contract):
    """is_abnormal is intentionally nullable; is_record_eligible never is."""
    assert contract("""select count(*) from ANALYTICS.dim_matchup_period
                       where is_abnormal is null""")[0][0] >= 1
    assert contract("""select count(*) from ANALYTICS.dim_matchup_period
                       where is_record_eligible is null""")[0][0] == 0


def test_unknown_playoff_status_is_distinct_from_eligibility(contract):
    """A period can be record-eligible with playoff status unknown -- which is
    why the two are separate predicates. The consumer's `is_playoff = false`
    is what fails closed."""
    rows = contract("""select count(*) from ANALYTICS.dim_matchup_period
                       where is_playoff is null and is_record_eligible""")
    assert rows[0][0] >= 1, "expected an eligible period with unproven playoff status"


# ===========================================================================
# SYNTHETIC CONTRACT -- the derived standard period length
# ===========================================================================
def test_the_derived_standard_is_published_and_constant_per_season(contract):
    """Consumers must stop recomputing it, so it has to be readable here --
    and inconsistent values within one league-season are refused rather than
    silently reduced to one."""
    bad = contract("""select count(*) from (
                        select league_key, season_year
                        from ANALYTICS.dim_matchup_period
                        where standard_period_length is not null
                        group by 1, 2 having count(distinct standard_period_length) > 1)""")
    assert bad == [(0,)], "a league-season published two derived standards"


def test_the_derived_standard_matches_each_league_own_shape(contract):
    """alpha's fixture is sevens; the second league's is fives. A shared
    standard would mean the derivation had leaked across leagues."""
    standards = dict(contract("""select league_key, max(standard_period_length)
                                 from ANALYTICS.dim_matchup_period
                                 where season_year = ?
                                   and standard_period_length is not null
                                 group by 1""", [SEASON_A]))
    assert standards[ALPHA] == 7
    assert standards[BETA] == 5


def test_an_override_does_not_move_the_derived_standard(contract):
    """An override changes eligibility. It is not evidence about the league's
    standard week, and must not rewrite it."""
    before = contract("""select max(standard_period_length)
                         from ANALYTICS.dim_matchup_period
                         where league_key = ? and season_year = ?""",
                      [ALPHA, SEASON_A])[0][0]
    _override(contract, ALPHA, SEASON_A, 7, True, "Commissioner note")
    after = contract("""select max(standard_period_length)
                        from ANALYTICS.dim_matchup_period
                        where league_key = ? and season_year = ?""",
                     [ALPHA, SEASON_A])[0][0]

    assert before == after == 7
    assert _row(contract, ALPHA, SEASON_A, 7)["is_record_eligible"] is False


# ===========================================================================
# SYNTHETIC CONTRACT -- season standard over a universe neither source anchors
# ===========================================================================
def test_a_derived_only_league_season_keeps_its_standard(contract):
    """THE REGRESSION. The resolution used to read

        from legacy_standard ls left join derived_standard ds

    which makes the FALLBACK a prerequisite for the derived answer: a
    league-season with a platform-derived standard and no recomputable
    gameplay days vanished from the standings' denominator entirely. An
    ordinary installation produces both rows, so real data never showed it.

    Here beta SEASON_A is derived-only -- no daily-fact rows at all -- and
    must still publish its derived standard of 5.
    """
    # Only alpha gets gameplay days; beta gets none.
    result = contract.build_standard([(ALPHA, SEASON_A, 18, 7)])
    assert result.returncode == 0, result.stdout[-3000:]

    rows = dict(((r[0], int(r[1])), (r[2], r[3])) for r in contract("""
        select league_key, season_year, standard_matchup_days, standard_source
        from ANALYTICS.int_matchup_season_standard"""))

    assert (BETA, SEASON_A) in rows,         "a derived-only league-season was dropped by the standard resolution"
    assert rows[(BETA, SEASON_A)] == (5, "derived")


def test_a_league_season_with_both_prefers_the_derived_standard(contract):
    rows = dict(((r[0], int(r[1])), (r[2], r[3])) for r in contract("""
        select league_key, season_year, standard_matchup_days, standard_source
        from ANALYTICS.int_matchup_season_standard"""))

    assert rows[(ALPHA, SEASON_A)] == (7, "derived")


def test_a_fallback_only_league_season_keeps_the_recomputation(contract):
    """alpha SEASON_B has no capture in this fixture, so no derived standard
    -- the legacy recomputation must survive on its own."""
    result = contract.build_standard([(ALPHA, SEASON_A, 18, 7),
                                      (ALPHA, SEASON_B, 25, 9)])
    assert result.returncode == 0, result.stdout[-3000:]

    rows = dict(((r[0], int(r[1])), (r[2], r[3])) for r in contract("""
        select league_key, season_year, standard_matchup_days, standard_source
        from ANALYTICS.int_matchup_season_standard"""))

    assert rows[(ALPHA, SEASON_B)] == (9, "legacy_recomputation")


def test_a_league_season_neither_source_answers_gets_no_row(contract):
    """No synthetic rows: beta SEASON_SPARSE is undetermined (below the
    evidence floor) and has no gameplay days either."""
    rows = {(r[0], int(r[1])) for r in contract(
        "select league_key, season_year from ANALYTICS.int_matchup_season_standard")}

    assert (BETA, SEASON_SPARSE) not in rows


def test_the_standings_mart_does_not_recompute_the_standard(contract):
    """The value the mart publishes has to BE this one -- a second
    recomputation in the mart would make the ownership contract a fiction."""
    mart = (REPO_ROOT / "dbt_league" / "models" / "marts" / "reporting"
            / "mart_team_season_standings.sql").read_text(encoding="utf-8")

    assert "int_matchup_season_standard" in mart
    assert "mode(sd.scoring_days)" not in mart,         "the standings mart still computes its own standard"


def test_the_consistency_assumption_has_a_standing_dbt_test():
    """max() over standard_period_length is only sound while a league-season
    publishes one value. That has to be guarded by the ordinary test surface,
    not by a comment."""
    test_sql = (REPO_ROOT / "dbt_league" / "tests"
                / "assert_one_derived_standard_per_league_season.sql")

    assert test_sql.exists()
    body = test_sql.read_text(encoding="utf-8")
    assert "count(distinct standard_period_length) > 1" in body


# ---------------------------------------------------------------------------
# Dialect portability -- IS [NOT] TRUE/FALSE
# ---------------------------------------------------------------------------
def test_no_model_uses_the_is_true_predicate():
    """Snowflake has no IS [NOT] TRUE/FALSE predicate and rejects it as a
    SYNTAX error. DuckDB and Postgres accept it, so it reads as ordinary SQL.

    THE REASON THIS IS A TEXT SCAN. Every other claim in this file is proven by
    BUILDING the model -- against DuckDB, which is the one engine that cannot
    catch this. `dim_matchup_period` shipped `is_abnormal is not true`, passed
    this suite green, and then failed to compile the first time anything built
    it against Snowflake. A build-based guard would have the same blind spot,
    so the spelling is asserted on the source text instead.

    Line comments are stripped first: the prose in these models discusses
    NULL/TRUE/FALSE constantly, and a scan that read comments would be noise
    nobody keeps.
    """
    offenders = []
    roots = [PROJECT_DIR / "models", PROJECT_DIR / "tests",
             PROJECT_DIR / "macros"]
    pattern = re.compile(r"\bis\s+(?:not\s+)?(?:true|false)\b", re.IGNORECASE)

    for root in roots:
        for path in sorted(root.rglob("*.sql")):
            for lineno, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), start=1):
                code = line.split("--", 1)[0]
                if pattern.search(code):
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")

    assert not offenders, (
        "Snowflake rejects the IS [NOT] TRUE/FALSE predicate as a syntax "
        "error; use `is distinct from true` or `coalesce(x, false) = false` "
        "instead:\n  " + "\n  ".join(offenders))


# ===========================================================================
# SYNTHETIC CONTRACT -- the abnormal_reason label fallback
# ===========================================================================
def test_a_derived_abnormal_period_keeps_the_human_legacy_label(contract):
    """Documented behaviour, and now asserted head-on rather than
    conditionally: within an abnormal verdict the legacy note is preferred
    over the generated one when the seed agrees the period is abnormal. The
    seed says WHY; the derivation can only say how long.

    alpha SEASON_A period 12 is the case that makes this checkable -- eleven
    scoring periods against a standard of seven, so DERIVATION calls it
    abnormal too. The verdict is the platform's and the sentence is the
    human's, which is the whole point of the ordering.
    """
    row = _row(contract, ALPHA, SEASON_A, 12)

    assert row["effective_source"] == "derived"
    assert row["is_abnormal"] is True
    assert row["is_record_eligible"] is False
    assert row["scoring_period_count"] == 11
    assert row["abnormal_reason"] == "Midseason recess", (
        "the generated label displaced the human one")


def test_a_generated_label_speaks_only_where_the_seed_cannot(contract):
    """The other half of the same rule. beta has no legacy calendar at all,
    so an abnormal period there has nothing human to carry and the derived
    sentence is what a reader gets."""
    _override(contract, BETA, SEASON_A, 3, True, None)
    row = _row(contract, BETA, SEASON_A, 3)

    assert row["is_abnormal"] is True
    assert row["abnormal_reason"] is None, (
        "an override with no reason must not borrow another source's label")


# ===========================================================================
# PRIVATE PIONEER EVIDENCE -- the real seed, read where it already sits
# ===========================================================================
def _pioneer_seed_rows():
    """How many data rows the maintainer's own calendar has, or 0 for the
    committed template. Counted, never copied."""
    seed = LEAGUE_CONFIG / "matchup_schedule.csv"
    if not seed.is_file():
        return 0
    with seed.open(newline="", encoding="utf-8") as handle:
        return sum(1 for _ in csv.DictReader(handle))


@pytest.fixture(scope="module")
def pioneer(tmp_path_factory):
    """The pioneer league's calendar, built from the checkout's own seed.

    NO DBT_LEAGUE_CONFIG and no legacy-league var: this is the ordinary
    configuration, which is the only one that can answer the question. On a
    public checkout `dbt_league/league_config/matchup_schedule.csv` is
    header-only and there is nothing to verify, so this skips LOUDLY rather
    than asserting something it cannot see -- and it skips ALONE, because the
    contract suite above has no dependency on this file whatsoever.

    Nothing is copied out. dbt reads the seed in place, the throwaway DuckDB
    is a tmp_path, and the only values this file records about it are the row
    count and the anomaly coordinates that were already committed here.
    """
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover
        _skip_loudly(f"duckdb not importable ({exc}); the evidence was NOT built")

    found = _pioneer_seed_rows()
    if found == 0:
        _skip_loudly(
            "dbt_league/league_config/matchup_schedule.csv is the committed "
            "header-only template, so the pioneer league's hand-maintained "
            "calendar is not present in this checkout and its survival was "
            "NOT verified. This is the expected state on a public clone; the "
            "synthetic contract suite in this file covers the model itself.")

    db = tmp_path_factory.mktemp("mlb235_pioneer") / "ESPN_FANTASY.duckdb"
    _empty_raw(db)

    env = dict(os.environ, DBT_DUCKDB_PATH=str(db))
    env.pop("DBT_LEAGUE_CONFIG", None)
    # CAUTIOUS INDIRECT SELECTION, for the reason `_dbt` documents at length:
    # `+dim_matchup_period` names a subgraph, and dbt's default eager rule also
    # pulls in any test with at least ONE selected parent. The MLB-229 rivalry
    # tests span this subgraph and the rivalry one, so eager handed them a build
    # holding only half of what they read and they errored on mart_team_matchup,
    # a relation this fixture never creates.
    #
    # This invocation cannot simply call `_dbt`: that helper pins
    # `legacy_matchup_schedule_league` to the synthetic league, and this fixture
    # exists precisely to measure the ORDINARY configuration. So it takes the
    # one flag rather than the whole helper.
    #
    # Cautious drops a test only when some parent is unbuilt, so every rivalry
    # assertion still runs in the builds that do create its dependencies.
    result = subprocess.run(
        [sys.executable, "-m", "dbt.cli.main", "build",
         "--select", "+dim_matchup_period",
         "--project-dir", str(PROJECT_DIR), "--profiles-dir", str(PROFILES_DIR),
         "--target", "duckdb",
         "--indirect-selection", "cautious"],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True)
    if result.returncode != 0:
        pytest.fail("the pioneer build over an EMPTY RAW.MATCHUP_SCHEDULE "
                    f"failed:\n{result.stdout[-4000:]}\n{result.stderr[-1500:]}")

    con = duckdb.connect(str(db))
    q = lambda sql, p=None: con.execute(sql, p or []).fetchall()
    measured = {
        "seed_rows": found,
        "rows": q("select count(*) from ANALYTICS.dim_matchup_period")[0][0],
        "leagues": [r[0] for r in q(
            "select distinct league_key from ANALYTICS.dim_matchup_period")],
        "sources": dict(q("""select effective_source, count(*)
                             from ANALYTICS.dim_matchup_period group by 1""")),
        "abnormal": [(int(a), int(b)) for a, b in q("""
            select season_year, matchup_period from ANALYTICS.dim_matchup_period
            where is_abnormal order by 1, 2""")],
        "eligible": q("""select count(*) from ANALYTICS.dim_matchup_period
                         where is_record_eligible""")[0][0],
        "dated": q("""select count(*) from ANALYTICS.dim_matchup_period
                      where start_date is not null""")[0][0],
    }
    con.close()
    yield measured


def test_the_pioneer_league_keeps_its_calendar_with_no_capture(pioneer):
    """PRIVATE EVIDENCE. espn-main with an EMPTY RAW.MATCHUP_SCHEDULE must
    still have its 48 hand-maintained rows, stamped to it and dated. Existing
    behaviour cannot disappear because a warehouse has not backfilled the new
    capture -- and that is a claim about the real calendar, which is why a
    synthetic stand-in cannot make it."""
    assert pioneer["seed_rows"] == PIONEER_ROWS
    assert pioneer["rows"] == PIONEER_ROWS
    assert pioneer["leagues"] == [PIONEER_LEAGUE]
    assert pioneer["dated"] == PIONEER_ROWS
    assert pioneer["sources"] == {"legacy_seed": PIONEER_ROWS}


def test_the_pioneer_recorded_anomaly_shape_is_reproduced_exactly(pioneer):
    """PRIVATE EVIDENCE. The four hand-maintained anomalies and the
    eligibility that follows from them, unchanged."""
    assert pioneer["abnormal"] == PIONEER_ABNORMAL
    assert pioneer["eligible"] == PIONEER_ELIGIBLE


def test_the_committed_pioneer_seed_ships_no_rows():
    """And the other side of it, which DOES hold on every checkout: whatever
    sits on a maintainer's disk, the tracked file is a blank template. The
    evidence above is private because this is true, not in spite of it."""
    committed = subprocess.run(
        ["git", "show", "HEAD:dbt_league/league_config/matchup_schedule.csv"],
        cwd=str(REPO_ROOT), capture_output=True, text=True)
    if committed.returncode != 0:  # pragma: no cover
        pytest.skip("not a git checkout; the committed template is unreadable")

    rows = list(csv.DictReader(committed.stdout.splitlines()))
    assert rows == [], (
        "the pioneer league's hand-maintained calendar is COMMITTED, which it "
        "must never be -- league_config ships blank templates")
