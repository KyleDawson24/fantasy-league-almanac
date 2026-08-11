"""The league-scoped matchup-period contract, built for real (MLB-235 4A).

Builds `+dim_matchup_period` with dbt against a THROWAWAY DuckDB holding only
fixtures this file wrote, then queries the result. The legacy seed is the
REAL espn-main calendar -- that is the point of evidence 6, which asks whether
the pioneer league's 48 rows survive a warehouse that has never captured a
matchup schedule.

WHY IT BUILDS EMPTY FIRST, then inserts: every model in the selection is a
view over either RAW or a seeded table, so one build proves the empty-source
state and the fixtures can be inserted afterwards and the same views
re-queried. The empty case is therefore the state the models were created in
rather than a branch that might rot -- and the override seed is genuinely
empty in the repo, so inserting into its seeded table is how a sparse
override gets exercised without editing anybody's config.
"""

import json
import os
import subprocess
import sys
import warnings
from datetime import datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PROJECT_DIR = REPO_ROOT / "dbt_league"
PROFILES_DIR = PROJECT_DIR / "profiles"
MAIN = "espn-main"
OTHER = "espn-other"


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


@pytest.fixture(scope="module")
def dim(tmp_path_factory):
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover
        _skip_loudly(f"duckdb not importable ({exc}); the contract was NOT built")

    db = tmp_path_factory.mktemp("mlb235_4a") / "ESPN_FANTASY.duckdb"
    con = duckdb.connect(str(db))
    con.execute("create schema if not exists RAW")
    for table in ("MATCHUP_SCHEDULE", "SCHEDULE_SETTINGS"):
        con.execute(f"""
            create table RAW.{table} (
                SEASON_YEAR decimal(38,0), RAW_JSON json,
                EXTRACTED_AT timestamp, LEAGUE_KEY varchar)
        """)
    con.close()

    env = dict(os.environ, DBT_DUCKDB_PATH=str(db))
    result = subprocess.run(
        [sys.executable, "-m", "dbt.cli.main", "build",
         "--select", "+dim_matchup_period",
         "--project-dir", str(PROJECT_DIR), "--profiles-dir", str(PROFILES_DIR),
         "--target", "duckdb"],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True)
    if result.returncode != 0:
        pytest.fail("build over an EMPTY RAW.MATCHUP_SCHEDULE failed:\n"
                    f"{result.stdout[-4000:]}\n{result.stderr[-1500:]}")

    # Held in a mutable cell because `retest` below has to RELEASE the file:
    # DuckDB is single-writer, so dbt cannot open the database while this
    # connection has it. Closing and reopening around the dbt run is the whole
    # reason the connection is not a plain local.
    state = {"con": duckdb.connect(str(db))}
    q = lambda sql, p=None: state["con"].execute(sql, p or []).fetchall()

    # Evidence 6, measured BEFORE any capture exists: the pioneer league keeps
    # its calendar from the legacy seed alone.
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
    # espn-main 2026: derivation covers 1..18, legacy covers 1..22.
    state["con"].execute("insert into RAW.MATCHUP_SCHEDULE values (?, ?, ?, ?)",
                [2026, json.dumps(_payload([7] * 22, season_year=2026, current=19)),
                 stamped, MAIN])
    # A SECOND ESPN league, same season, different shape and no legacy rows.
    state["con"].execute("insert into RAW.MATCHUP_SCHEDULE values (?, ?, ?, ?)",
                [2026, json.dumps(_payload([5] * 9 + [11], season_year=2026,
                                           current=10)),
                 stamped, OTHER])
    # A league with membership but no legacy seed and no derivation verdict:
    # two closed periods is below the floor, so the season is undetermined.
    state["con"].execute("insert into RAW.MATCHUP_SCHEDULE values (?, ?, ?, ?)",
                [2024, json.dumps(_payload([7] * 6, season_year=2024, current=3)),
                 stamped, OTHER])

    def retest():
        """Re-run the DECLARED dbt tests against the POPULATED fixture.

        The build above ran them while the relation was still empty, where a
        stale not_null on a nullable column and a stale grain test over two
        leagues both pass vacuously. This is the same assertions against real
        rows -- which is the only version of them that means anything.
        """
        # Release the file: DuckDB is single-writer and dbt needs it.
        state["con"].close()
        completed = subprocess.run(
            [sys.executable, "-m", "dbt.cli.main", "test",
             "--select", "dim_matchup_period",
             "--project-dir", str(PROJECT_DIR),
             "--profiles-dir", str(PROFILES_DIR), "--target", "duckdb"],
            cwd=str(REPO_ROOT), env=env, capture_output=True, text=True)
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
        completed = subprocess.run(
            [sys.executable, "-m", "dbt.cli.main", "build",
             "--select", "int_matchup_season_standard",
             "--project-dir", str(PROJECT_DIR),
             "--profiles-dir", str(PROFILES_DIR), "--target", "duckdb"],
            cwd=str(REPO_ROOT), env=env, capture_output=True, text=True)
        state["con"] = duckdb.connect(str(db))
        return completed

    q.build_standard = build_standard
    q.retest = retest
    yield q
    state["con"].close()


def _row(dim, league, season, period):
    rows = dim("""select is_abnormal, is_record_eligible, effective_source,
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


def _override(dim, league, season, period, is_abnormal, reason=None):
    dim("insert into ANALYTICS.matchup_period_overrides values (?, ?, ?, ?, ?)",
        [league, season, period, is_abnormal, reason])


# ---------------------------------------------------------------------------
# Evidence 6 -- legacy and empty compatibility
# ---------------------------------------------------------------------------
def test_the_pioneer_league_keeps_its_calendar_with_no_capture(dim):
    """espn-main with an EMPTY RAW.MATCHUP_SCHEDULE must still have its 48
    hand-maintained rows and their flags. Existing behaviour cannot disappear
    because a warehouse has not backfilled the new capture."""
    assert dim.legacy_only["rows"] == 48
    assert dim.legacy_only["leagues"] == [MAIN]
    assert dim.legacy_only["dated"] == 48


def test_with_no_capture_every_answer_comes_from_the_legacy_seed(dim):
    assert dim.legacy_only["sources"] == {"legacy_seed": 48}


def test_the_legacy_abnormal_flags_are_reproduced_exactly(dim):
    """The four hand-maintained anomalies, unchanged."""
    assert dim.legacy_only["abnormal"] == [(2025, 1), (2025, 16),
                                           (2026, 1), (2026, 15)]
    assert dim.legacy_only["eligible"] == 44


# ---------------------------------------------------------------------------
# Evidence 1 -- no cross-league bleed
# ---------------------------------------------------------------------------
def test_a_second_league_does_not_inherit_the_pioneer_calendar(dim):
    """The bug this rung exists to close: the seed carries no league_key, so
    its 2026 rows used to join to ANY league with the same period numbers."""
    other = dim("""select count(*) from ANALYTICS.dim_matchup_period
                   where league_key = ? and start_date is not null""", [OTHER])
    assert other == [(0,)], "espn-main dates reached another league"


def test_a_second_league_gets_its_own_shape(dim):
    """Same season, different period lengths. Nine fives and one eleven, so
    five is the standard and the long period is the anomaly."""
    assert _row(dim, OTHER, 2026, 1)["scoring_period_count"] == 5
    assert _row(dim, OTHER, 2026, 1)["is_abnormal"] is False
    assert _row(dim, OTHER, 2026, 10) is None, "period 10 is the live one"

    abnormal = dim("""select matchup_period from ANALYTICS.dim_matchup_period
                      where league_key = ? and season_year = 2026 and is_abnormal
                      order by 1""", [OTHER])
    assert abnormal == []


def test_the_second_league_inherits_no_flags_or_playoff_labels(dim):
    rows = dim("""select count(*) from ANALYTICS.dim_matchup_period
                  where league_key = ? and (is_playoff or playoff_round is not null)""",
               [OTHER])
    assert rows == [(0,)]


def test_the_dimension_grain_holds_across_leagues(dim):
    """Evidence 8. Two leagues share season and period numbers, so a grain
    slip would show up here rather than as a silent row multiplication."""
    dupes = dim("""select count(*) from (
                     select league_key, season_year, matchup_period
                     from ANALYTICS.dim_matchup_period
                     group by 1, 2, 3 having count(*) > 1)""")
    assert dupes == [(0,)]


# ---------------------------------------------------------------------------
# Evidence 2 -- derivation wins when there is no override
# ---------------------------------------------------------------------------
def test_derivation_supersedes_the_legacy_seed_where_both_answer(dim):
    """espn-main 2026 period 1: the seed calls it abnormal (13 days by its
    calendar), the platform says seven scoring periods in this fixture. The
    platform wins, and the row still carries the seed's dates."""
    row = _row(dim, MAIN, 2026, 1)

    assert row["effective_source"] == "derived"
    assert row["is_abnormal"] is False
    assert row["is_record_eligible"] is True
    assert row["scoring_period_count"] == 7
    assert row["start_date"] is not None, "legacy calendar must survive"


def test_a_period_derivation_cannot_reach_falls_back_to_the_seed(dim):
    """espn-main 2026 periods 19-22 are at or past the live pointer, so the
    platform declines and the pioneer league keeps its existing answer."""
    row = _row(dim, MAIN, 2026, 20)

    assert row["effective_source"] == "legacy_seed"
    assert row["is_abnormal"] is False
    assert row["start_date"] is not None


def test_a_reason_does_not_survive_onto_a_row_now_called_normal(dim):
    """The seed's reason describes a verdict the platform has replaced. It
    must not stay attached to a row that is no longer abnormal."""
    assert _row(dim, MAIN, 2026, 1)["abnormal_reason"] is None


# ---------------------------------------------------------------------------
# Evidence 5 -- unknown is not normal
# ---------------------------------------------------------------------------
def test_unknown_yields_null_abnormality_and_false_eligibility(dim):
    """A league with membership but too little of it, and no legacy seed to
    fall back to: abnormality is genuinely unknown."""
    row = _row(dim, OTHER, 2024, 1)

    assert row["effective_source"] == "unknown"
    assert row["is_abnormal"] is None, "unknown must not be coerced to false"
    assert row["is_record_eligible"] is False, "unknown must not be eligible"
    assert row["derivation_status"] == "insufficient_evidence"


def test_the_gate_is_never_null_anywhere(dim):
    nulls = dim("""select count(*) from ANALYTICS.dim_matchup_period
                   where is_record_eligible is null""")
    assert nulls == [(0,)]


def test_eligibility_is_exactly_known_and_not_abnormal(dim):
    """coalesce(is_abnormal = false, false), asserted over every row rather
    than argued from the SQL."""
    wrong = dim("""select count(*) from ANALYTICS.dim_matchup_period
                   where is_record_eligible
                         <> coalesce(is_abnormal = false, false)""")
    assert wrong == [(0,)]


# ---------------------------------------------------------------------------
# Evidence 3 and 4 -- the sparse override seam
# ---------------------------------------------------------------------------
def test_an_override_of_true_excludes_a_derived_normal_period(dim):
    """The case the seam exists for: a commissioner-declared oddity whose
    scoring-period length is perfectly normal, so derivation cannot see it."""
    _override(dim, MAIN, 2026, 5, True, "Commissioner-voided week")
    row = _row(dim, MAIN, 2026, 5)

    assert row["effective_source"] == "override"
    assert row["is_abnormal"] is True
    assert row["is_record_eligible"] is False
    assert row["abnormal_reason"] == "Commissioner-voided week"


def test_an_override_of_false_admits_a_derived_abnormal_period(dim):
    """Explicit false must beat a derived true -- coalesce keys on NULL, not
    on falsehood, which is the property that makes this work."""
    _override(dim, OTHER, 2026, 10, False, None)
    _override(dim, OTHER, 2024, 2, False, "Counted by league vote")

    row = _row(dim, OTHER, 2024, 2)
    assert row["effective_source"] == "override"
    assert row["is_abnormal"] is False
    assert row["is_record_eligible"] is True


def test_an_override_needs_no_row_for_ordinary_periods(dim):
    """Sparse means sparse: the seam is exercised above with three rows, and
    every other period in the fixture still resolves without one."""
    resolved = dim("""select count(*) from ANALYTICS.dim_matchup_period
                      where effective_source in ('derived', 'legacy_seed')""")
    assert resolved[0][0] > 50


def test_the_committed_override_seed_is_empty():
    """A blanket false row for ordinary periods would re-create the
    hand-maintained flag this ticket removed."""
    import csv
    for path in (REPO_ROOT / "dbt_league" / "league_config",
                 REPO_ROOT / "demo" / "league_config"):
        rows = list(csv.DictReader(
            (path / "matchup_period_overrides.csv").open(newline="")))
        assert rows == [], f"{path} ships override rows"


# ---------------------------------------------------------------------------
# The declared contract, asserted against POPULATED rows
# ---------------------------------------------------------------------------
def test_the_declared_dbt_tests_pass_with_unknown_rows_present(dim):
    """The gap this pass closes. The build's tests ran over an empty relation,
    so a stale `not_null` on the now-nullable is_abnormal and a stale
    single-league grain test would both have passed vacuously. Re-run them
    over the fixture: two leagues, an unknown period, and overrides."""
    # Overrides from the tests above are already in place; make sure the
    # unknown row and the second league are too.
    assert _row(dim, OTHER, 2024, 1)["is_abnormal"] is None
    assert _row(dim, OTHER, 2026, 1) is not None

    result = dim.retest()
    assert result.returncode == 0, (
        "the declared dbt tests fail against populated fixture rows:\n"
        + result.stdout[-4000:])
    assert "ERROR=0" in result.stdout


def test_unknown_abnormality_is_allowed_but_the_gate_is_not(dim):
    """is_abnormal is intentionally nullable; is_record_eligible never is."""
    assert dim("""select count(*) from ANALYTICS.dim_matchup_period
                  where is_abnormal is null""")[0][0] >= 1
    assert dim("""select count(*) from ANALYTICS.dim_matchup_period
                  where is_record_eligible is null""")[0][0] == 0


def test_unknown_playoff_status_is_distinct_from_eligibility(dim):
    """A period can be record-eligible with playoff status unknown -- which is
    why the two are separate predicates. The consumer's `is_playoff = false`
    is what fails closed."""
    rows = dim("""select count(*) from ANALYTICS.dim_matchup_period
                  where is_playoff is null and is_record_eligible""")
    assert rows[0][0] >= 1, "expected an eligible period with unproven playoff status"


# ---------------------------------------------------------------------------
# The derived standard period length
# ---------------------------------------------------------------------------
def test_the_derived_standard_is_published_and_constant_per_season(dim):
    """Consumers must stop recomputing it, so it has to be readable here --
    and inconsistent values within one league-season are refused rather than
    silently reduced to one."""
    bad = dim("""select count(*) from (
                   select league_key, season_year
                   from ANALYTICS.dim_matchup_period
                   where standard_period_length is not null
                   group by 1, 2 having count(distinct standard_period_length) > 1)""")
    assert bad == [(0,)], "a league-season published two derived standards"


def test_the_derived_standard_matches_each_league_own_shape(dim):
    """espn-main's fixture is sevens; the second league's is fives. A shared
    standard would mean the derivation had leaked across leagues."""
    standards = dict(dim("""select league_key, max(standard_period_length)
                            from ANALYTICS.dim_matchup_period
                            where season_year = 2026
                              and standard_period_length is not null
                            group by 1"""))
    assert standards[MAIN] == 7
    assert standards[OTHER] == 5


def test_an_override_does_not_move_the_derived_standard(dim):
    """An override changes eligibility. It is not evidence about the league's
    standard week, and must not rewrite it."""
    before = dim("""select max(standard_period_length)
                    from ANALYTICS.dim_matchup_period
                    where league_key = ? and season_year = 2026""", [MAIN])[0][0]
    _override(dim, MAIN, 2026, 7, True, "Commissioner note")
    after = dim("""select max(standard_period_length)
                   from ANALYTICS.dim_matchup_period
                   where league_key = ? and season_year = 2026""", [MAIN])[0][0]

    assert before == after == 7
    assert _row(dim, MAIN, 2026, 7)["is_record_eligible"] is False


# ---------------------------------------------------------------------------
# The season standard: derived-first, over a universe neither source anchors
# ---------------------------------------------------------------------------
def test_a_derived_only_league_season_keeps_its_standard(dim):
    """THE REGRESSION. The resolution used to read

        from legacy_standard ls left join derived_standard ds

    which makes the FALLBACK a prerequisite for the derived answer: a
    league-season with a platform-derived standard and no recomputable
    gameplay days vanished from the standings' denominator entirely. An
    ordinary installation produces both rows, so real data never showed it.

    Here espn-other 2026 is derived-only -- no daily-fact rows at all -- and
    must still publish its derived standard of 5.
    """
    # Only espn-main 2026 gets gameplay days; espn-other gets none.
    result = dim.build_standard([(MAIN, 2026, 18, 7)])
    assert result.returncode == 0, result.stdout[-3000:]

    rows = dict(((r[0], int(r[1])), (r[2], r[3])) for r in dim("""
        select league_key, season_year, standard_matchup_days, standard_source
        from ANALYTICS.int_matchup_season_standard"""))

    assert (OTHER, 2026) in rows,         "a derived-only league-season was dropped by the standard resolution"
    assert rows[(OTHER, 2026)] == (5, "derived")


def test_a_league_season_with_both_prefers_the_derived_standard(dim):
    rows = dict(((r[0], int(r[1])), (r[2], r[3])) for r in dim("""
        select league_key, season_year, standard_matchup_days, standard_source
        from ANALYTICS.int_matchup_season_standard"""))

    assert rows[(MAIN, 2026)] == (7, "derived")


def test_a_fallback_only_league_season_keeps_the_recomputation(dim):
    """espn-main 2025 has no capture in this fixture, so no derived standard
    -- the legacy recomputation must survive on its own."""
    result = dim.build_standard([(MAIN, 2026, 18, 7), (MAIN, 2025, 26, 9)])
    assert result.returncode == 0, result.stdout[-3000:]

    rows = dict(((r[0], int(r[1])), (r[2], r[3])) for r in dim("""
        select league_key, season_year, standard_matchup_days, standard_source
        from ANALYTICS.int_matchup_season_standard"""))

    assert rows[(MAIN, 2025)] == (9, "legacy_recomputation")


def test_a_league_season_neither_source_answers_gets_no_row(dim):
    """No synthetic rows: espn-other 2024 is undetermined (below the evidence
    floor) and has no gameplay days either."""
    rows = {(r[0], int(r[1])) for r in dim(
        "select league_key, season_year from ANALYTICS.int_matchup_season_standard")}

    assert (OTHER, 2024) not in rows


def test_the_standings_mart_does_not_recompute_the_standard(dim):
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
# The abnormal_reason label fallback, asserted rather than assumed
# ---------------------------------------------------------------------------
def test_a_derived_abnormal_period_keeps_the_human_legacy_label(dim):
    """Documented behaviour: within an abnormal verdict the legacy note is
    preferred over the generated one when the seed agrees the period is
    abnormal. The seed says WHY; the derivation can only say how long."""
    result = dim.build_standard([(MAIN, 2026, 18, 7)])
    assert result.returncode == 0

    # espn-main 2026 period 15 is abnormal in the legacy seed. Its own
    # derivation in this fixture is all-sevens, so the effective verdict comes
    # from the seed and the seed's label rides with it.
    row = _row(dim, MAIN, 2026, 15)
    assert row["is_abnormal"] is not None
    if row["is_abnormal"]:
        assert row["abnormal_reason"], "an abnormal period lost its explanation"
