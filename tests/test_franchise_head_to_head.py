"""The MLB-229 rung-1 rivalry ledger, built for real against synthetic teams.

WHY THIS BUILDS RATHER THAN INSPECTS. Asserting on the model's SQL text proves
somebody typed it, not that it computes anything. This builds the lineage
machinery and `mart_franchise_head_to_head` with dbt against a THROWAWAY DuckDB
holding only fixtures this file wrote, then queries the answers. Nothing here
touches the private warehouse, the checked-in data/duckdb tree, the
maintainer's league_config, ESPN, or a Google account: `tmp_path_factory` puts
both the database and the seed directory under pytest's temp root and they die
with the session.

WHAT IS FIXTURE AND WHAT IS REAL. `mart_team_matchup` and the three staging
relations the franchise spine reads are stood up BY HAND -- they are this
model's inputs, and reproducing ESPN's whole player chain to reach them would
test the chain, not the ledger. Everything from `int_franchise_seasons` down
through `dim_franchise`, `dim_franchise_season` and the mart itself is the REAL
project SQL, built by real dbt. That is the seam that matters: the identity
requirements this ticket is about (re-minted ids, renames, configured display
names, seasons parked on the holding pen) are all decided inside the machinery
under test, not inside the fixtures.

WHY TWO BUILDS. The mart is a table, so a rebuild is the only way its contents
can change. The first build runs over EMPTY inputs -- proving that a league
with no matchups produces an empty ledger and a green test suite rather than a
build failure -- and the second runs over the populated fixture. The empty case
is therefore the state the model was literally created in, not a special path
that might rot.

THE FIXTURE LEAGUE IS A LIST OF EDGE CASES WEARING TEAM NAMES. Every franchise
in `espn-fix` exists to make one rule fail if it is broken: a re-minted id, a
rename, a configured display override, two unrelated franchises sharing a name
exactly, a season parked on the pen, two ids collapsing onto one franchise, a
bye, a tie, and a matchup missing a score on one side. A second league carrying
the SAME ids and the SAME names proves none of it leaks across leagues.
"""

import csv
import os
import shutil
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PROJECT_DIR = REPO_ROOT / "dbt_league"
PROFILES_DIR = PROJECT_DIR / "profiles"
LEAGUE_CONFIG = PROJECT_DIR / "league_config"

LEAGUE = "espn-fix"
OTHER_LEAGUE = "espn-other"
PEN = "9999"

# The models this build is responsible for, plus the singular tests. Named
# explicitly rather than reached with a graph operator: `+mart_franchise_head_
# to_head` would drag in ESPN's entire player chain from RAW, which is exactly
# what standing the inputs up by hand is avoiding.
BUILD_SELECTION = [
    "cbs_franchises",
    "franchise_lineage",
    "int_franchise_seasons",
    "int_franchise_registry",
    "dim_franchise",
    "dim_franchise_season",
    "mart_franchise_head_to_head",
    "assert_head_to_head_reciprocity",
    "assert_head_to_head_pairs_are_mutual",
    "assert_head_to_head_has_no_diagonal",
    "assert_head_to_head_results_partition_meetings",
    "assert_head_to_head_identity_resolves",
]

# One neighbouring singular test rides along on dim_franchise_season and reads
# fct_player_daily_performance, which is the ESPN player chain this fixture
# deliberately does not stand up. Excluded by name so the exclusion is visible:
# a blanket `--exclude test_type:singular` would also have dropped
# assert_franchise_display_anchors_on_latest_era, which DOES belong here -- it
# checks the display anchoring these fixtures exercise, over these fixtures.
BUILD_EXCLUSION = ["assert_team_display_resolves_through_dim"]


# ---------------------------------------------------------------------------
# The fixture league
# ---------------------------------------------------------------------------
#
# (team_id, season_year, name, abbrev) -- what the box scores observed. The
# season list per team IS the franchise's era: `int_franchise_seasons` builds
# its spine from exactly these rows, so a team absent from a season did not
# play it.
OBSERVED = [
    # Ordinary franchises, present throughout.
    (1, 2021, "Alpha Anchors", "ALPH"),
    (1, 2022, "Alpha Anchors", "ALPH"),
    (1, 2023, "Alpha Anchors", "ALPH"),
    (2, 2021, "Beta Bandits", "BETA"),
    (2, 2022, "Beta Bandits", "BETA"),
    (2, 2023, "Beta Bandits", "BETA"),

    # A RENAME. Display must follow the latest observed name, not the oldest.
    (3, 2021, "Gamma Ghosts", "GHST"),
    (3, 2022, "Gamma Ghosts", "GHST"),
    (3, 2023, "Gamma Giants", "GIAN"),

    # DEFUNCT after 2022. Must still appear in the ledger -- there is no
    # activity filter, and a franchise that stopped playing still played.
    (4, 2021, "Delta Ducks", "DUCK"),
    (4, 2022, "Delta Ducks", "DUCK"),

    # TWO UNRELATED FRANCHISES, one name, spelled identically. No lineage row
    # links them, so nothing may merge them -- and they meet each other, so a
    # name-keyed aggregation would fold their series onto the diagonal.
    (5, 2021, "Twin Name FC", "TWN1"),
    (5, 2022, "Twin Name FC", "TWN1"),
    (5, 2023, "Twin Name FC", "TWN1"),
    (6, 2021, "Twin Name FC", "TWN2"),
    (6, 2022, "Twin Name FC", "TWN2"),
    (6, 2023, "Twin Name FC", "TWN2"),

    # Its 2022 is parked on the holding pen by a season-scoped lineage row.
    (7, 2021, "Seven Sails", "SAIL"),
    (7, 2022, "Seven Sails", "SAIL"),
    (7, 2023, "Seven Sails", "SAIL"),

    # A CONFIGURED display name. The observed name must lose to the seed.
    (8, 2021, "Observed Eight", "OBS8"),
    (8, 2022, "Observed Eight", "OBS8"),
    (8, 2023, "Observed Eight", "OBS8"),

    # A RE-MINTED id: one franchise, two platform ids, non-overlapping eras.
    (13, 2021, "Echo Era One", "ECH1"),
    (13, 2022, "Echo Era One", "ECH1"),
    (30, 2023, "Echo Era Two", "ECH2"),

    # Two ids the lineage collapses onto ONE franchise, both live in 2023 --
    # the only way a self-matchup can reach the canonical grain.
    (20, 2023, "Merge Left", "MRGL"),
    (21, 2023, "Merge Right", "MRGR"),
]

OTHER_OBSERVED = [
    # Same ids, same names, different league. Nothing may cross.
    (1, 2023, "Alpha Anchors", "ALPH"),
    (2, 2023, "Beta Bandits", "BETA"),
]

# (league_key, franchise_id, season_year, canonical_franchise_id,
#  canonical_name, canonical_abbrev) -- the lineage seed, synthetic throughout.
LINEAGE = [
    # All-season: the re-minted id is the SAME franchise as its first era.
    (LEAGUE, "30", "", "13", "", ""),
    # All-season: two live ids, one franchise.
    (LEAGUE, "21", "", "20", "", ""),
    # All-season, display only: a configured name beats the observed one.
    (LEAGUE, "8", "", "8", "Configured Eight", "CFG8"),
    # SEASON-SCOPED: 2022 belonged to nobody. Only dim_franchise_season can
    # honour this, which is why the mart joins the season-grain dim.
    (LEAGUE, "7", "2022", PEN, "", ""),
]

# (season_year, matchup_period, team_id, opponent_id, points, opponent_points)
# Each becomes TWO rows in mart_team_matchup, one per perspective, exactly as
# the real matchup surface emits them.
MEETINGS = [
    # -- 2021 --------------------------------------------------------------
    (2021, 1, 1, 2, 100.0, 90.0),      # 1 beats 2
    (2021, 1, 3, 4, 80.0, 80.0),       # a TIE
    (2021, 1, 5, 6, 70.5, 60.5),       # the twins meet: 5 beats 6
    (2021, 1, 7, 8, 50.0, 55.0),       # 8 beats 7
    (2021, 1, 13, 1, 40.0, 45.0),      # 1 beats the FIRST era of 13
    (2021, 2, 1, 2, 88.0, 99.0),       # 2 beats 1 -- the series levels

    # -- 2022 --------------------------------------------------------------
    (2022, 1, 1, 2, 110.0, 100.0),     # 1 leads the series 2-1
    (2022, 1, 7, 3, 60.0, 65.0),       # VANISHES: 7's 2022 is on the pen
    (2022, 1, 13, 4, 30.0, 20.0),      # 13 beats 4

    # -- 2023 --------------------------------------------------------------
    (2023, 1, 30, 1, 120.0, 100.0),    # the SECOND era of 13 beats 1
    (2023, 1, 20, 21, 55.0, 45.0),     # VANISHES: both collapse onto 20
    (2023, 1, 3, 6, 90.0, 80.0),       # 3 (now "Gamma Giants") beats 6
    (2023, 1, 7, 2, 70.0, 60.0),       # 7's 2023 is NOT parked -- this counts
    (2023, 1, 5, 6, 65.0, 65.0),       # the twins tie
]

OTHER_MEETINGS = [
    (2023, 1, 1, 2, 200.0, 100.0),
]


def _result(points, opponent_points):
    """The fact's own W/L/T derivation, restated so the fixture carries what
    the real surface would have carried -- including its else-branch, which is
    the trap the unscored meeting below is here to spring."""
    if points is None or opponent_points is None:
        return "T"
    if points > opponent_points:
        return "W"
    if points < opponent_points:
        return "L"
    return "T"


def _matchup_rows():
    """mart_team_matchup rows: both perspectives of every meeting, plus the
    three shapes that must NOT become meetings."""
    rows = []
    for league, meetings in ((LEAGUE, MEETINGS), (OTHER_LEAGUE, OTHER_MEETINGS)):
        for season, period, team, opponent, pts, opp_pts in meetings:
            rows.append((league, season, period, team, opponent, pts, opp_pts,
                         _result(pts, opp_pts)))
            rows.append((league, season, period, opponent, team, opp_pts, pts,
                         _result(opp_pts, pts)))

    # A BYE. The real mart_team_matchup filters these out upstream; the fixture
    # carries one anyway, because the ledger's correctness must not depend on
    # somebody else's WHERE clause staying where it is.
    rows.append((LEAGUE, 2021, 2, 4, None, 77.0, None, None))

    # AN UNSCORED MEETING. Both sides exist and one has no platform score, so
    # the fact's else-branch calls it a TIE on both rows. If the ledger trusted
    # `result` alone, a capture gap would mint a tie in a rivalry record.
    rows.append((LEAGUE, 2022, 1, 5, 8, None, 42.0, "T"))
    rows.append((LEAGUE, 2022, 1, 8, 5, 42.0, None, "T"))

    return rows


# ---------------------------------------------------------------------------
# The throwaway warehouse
# ---------------------------------------------------------------------------
def _skip_loudly(reason):
    """A silent skip reads as a pass in a scroll-back."""
    banner = f"[SKIPPED - NOT VERIFIED] {reason}"
    warnings.warn(banner, UserWarning, stacklevel=2)
    print(f"\n!! {banner}", file=sys.stderr)
    pytest.skip(reason)


def _seed_dir(root):
    """A synthetic league_config: every seed header-only except the lineage.

    Headers are copied from the working tree so the column list cannot drift
    from what dbt's +column_types declares, and only the header -- not one row
    of the maintainer's real league data is read, and this directory is the
    only league_config the build can see.

    REWRITTEN BEFORE EVERY BUILD, ON PURPOSE. dbt writes each node's compiled
    artifact to target/compiled/<project>/<original_file_path>, and joining a
    target path with an ABSOLUTE original path yields the absolute path itself
    -- so a seed reached through an absolute DBT_LEAGUE_CONFIG gets its own CSV
    overwritten with the `create table ...` SQL dbt just compiled. The load
    happens first, so a single-build fixture never notices; this file builds
    twice, and the second build read a CSV whose header had become one column
    of DDL ("Row 3 has 6 values, but Table only has 1 columns"). Regenerating
    is the containable fix: it keeps every write under pytest's temp root,
    where a relative path pointing back out of the project would not.
    """
    seed_dir = root / "fixture_league_config"
    seed_dir.mkdir(exist_ok=True)
    for source in sorted(LEAGUE_CONFIG.glob("*.csv")):
        with source.open(newline="", encoding="utf-8") as handle:
            header = next(csv.reader(handle), [])
        with (seed_dir / source.name).open("w", newline="",
                                           encoding="utf-8") as out:
            writer = csv.writer(out)
            writer.writerow(header)
            if source.name == "franchise_lineage.csv":
                writer.writerows(LINEAGE)
    return seed_dir


def _create_inputs(con):
    """The relations the build treats as given, empty. Column lists are the
    subset the models under test actually read."""
    con.execute("create schema if not exists ANALYTICS")
    con.execute("""
        create or replace table ANALYTICS.mart_team_matchup (
            league_key      varchar,
            season_year     integer,
            matchup_period  integer,
            team_id         integer,
            opponent_id     integer,
            platform_points double,
            opponent_points double,
            result          varchar
        )
    """)
    con.execute("""
        create or replace table ANALYTICS.stg_box_scores (
            league_key     varchar,
            season_year    integer,
            matchup_period integer,
            team_id        integer,
            team_name      varchar,
            team_abbrev    varchar
        )
    """)
    # The CBS arms of the franchise spine. Present and empty: this league is
    # served by the derived branch, and an absent relation would fail the build
    # rather than contribute nothing.
    con.execute("""
        create or replace table ANALYTICS.stg_cbs__ui_standings (
            league_key   varchar,
            season_year  integer,
            franchise_id varchar
        )
    """)
    con.execute("""
        create or replace table ANALYTICS.stg_cbs__standings (
            league_key  varchar,
            season_year integer,
            team_id     varchar
        )
    """)


def _populate(con):
    for league, observed in ((LEAGUE, OBSERVED), (OTHER_LEAGUE, OTHER_OBSERVED)):
        for team_id, season, name, abbrev in observed:
            con.execute(
                "insert into ANALYTICS.stg_box_scores values (?, ?, ?, ?, ?, ?)",
                [league, season, 1, team_id, name, abbrev])
    for row in _matchup_rows():
        con.execute(
            "insert into ANALYTICS.mart_team_matchup values (?, ?, ?, ?, ?, ?, ?, ?)",
            list(row))


def _build(root, db_path):
    """One `dbt build` of the selection, over a freshly written seed dir."""
    seed_dir = _seed_dir(root)
    env = dict(os.environ,
               DBT_DUCKDB_PATH=str(db_path),
               DBT_LEAGUE_CONFIG=str(seed_dir))
    return subprocess.run(
        [sys.executable, "-m", "dbt.cli.main", "build",
         "--select", *BUILD_SELECTION,
         "--exclude", *BUILD_EXCLUSION,
         "--project-dir", str(PROJECT_DIR),
         "--profiles-dir", str(PROFILES_DIR),
         "--target", "duckdb"],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """Build over empty inputs, then over the fixture league.

    Returns a callable running SQL against the result, carrying the empty
    build's row counts and both builds' stdout.
    """
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover
        _skip_loudly(f"duckdb not importable ({exc}); the ledger was NOT built")
    if not (PROJECT_DIR / "dbt_packages").is_dir():
        _skip_loudly("dbt_packages missing (run `dbt deps`); the ledger was "
                     "NOT built")

    root = tmp_path_factory.mktemp("mlb229_rung1")
    db_path = root / "ESPN_FANTASY.duckdb"

    con = duckdb.connect(str(db_path))
    _create_inputs(con)
    con.close()

    empty = _build(root, db_path)
    if empty.returncode != 0:
        pytest.fail("the build over EMPTY inputs failed, so a league with no "
                    "matchups cannot produce an empty ledger:\n"
                    f"{empty.stdout[-5000:]}\n{empty.stderr[-2000:]}")

    con = duckdb.connect(str(db_path))
    empty_counts = {
        model: con.execute(f"select count(*) from ANALYTICS.{model}").fetchone()[0]
        for model in ("dim_franchise", "dim_franchise_season",
                      "mart_franchise_head_to_head")
    }
    _populate(con)
    con.close()

    populated = _build(root, db_path)
    if populated.returncode != 0:
        pytest.fail("the build over the fixture league failed:\n"
                    f"{populated.stdout[-6000:]}\n{populated.stderr[-2000:]}")

    con = duckdb.connect(str(db_path))

    def query(sql, params=None):
        return con.execute(sql, params or []).fetchall()

    query.empty_counts = empty_counts
    query.empty_output = empty.stdout
    query.build_output = populated.stdout

    yield query
    con.close()


def _pair(built, row, opponent, league=LEAGUE):
    """One ordered pair's whole ledger row, or None."""
    rows = built("""
        select meetings, wins, losses, ties,
               points_for, points_against, points_margin, win_pct,
               first_meeting_season, last_meeting_season,
               row_franchise_name, opponent_franchise_name
        from ANALYTICS.mart_franchise_head_to_head
        where league_key = ?
          and row_canonical_franchise_id = ?
          and opponent_canonical_franchise_id = ?
    """, [league, str(row), str(opponent)])
    return rows[0] if rows else None


# ===========================================================================
# Empty source
# ===========================================================================
def test_an_empty_league_builds_an_empty_ledger(built):
    """A league that has captured no matchups is a supported state -- the
    stranger's first install, and every league before its first extract. It
    has to build to zero rows, not fail."""
    assert built.empty_counts["mart_franchise_head_to_head"] == 0


def test_the_empty_build_invents_no_franchises(built):
    """The franchise spine is observation-driven, so no matchups means no
    franchises -- not a holding pen sitting alone in a dimension."""
    assert built.empty_counts["dim_franchise_season"] == 0


def test_the_tests_ran_in_the_empty_build(built):
    """`dbt build`, not `dbt run`: the grain, reciprocity and diagonal tests
    are part of the evidence that empty is green, not just that it compiled."""
    assert ("PASS" in built.empty_output
            or "Completed successfully" in built.empty_output)


# ===========================================================================
# Controls -- proof the rest of this file is measuring something
# ===========================================================================
def test_the_fixture_league_actually_produced_a_ledger(built):
    """THE CONTROL. Twelve countable meetings across eight pairs in espn-fix
    plus one in espn-other, each pair told from both sides: 18 rows and 26
    counted meetings. Every "must be absent" assertion below would pass just
    as well against an empty table, so the numbers are pinned here first."""
    assert built("""
        select count(*), sum(meetings)
        from ANALYTICS.mart_franchise_head_to_head
    """) == [(18, 26)]


def test_the_contract_tests_ran_against_the_populated_ledger(built):
    """The reciprocity, diagonal, partition and identity assertions are dbt
    singular tests, so they run inside the build rather than here. If a rename
    ever drops one from the selection it stops running silently -- this is what
    notices."""
    for singular in ("assert_head_to_head_reciprocity",
                     "assert_head_to_head_pairs_are_mutual",
                     "assert_head_to_head_has_no_diagonal",
                     "assert_head_to_head_results_partition_meetings",
                     "assert_head_to_head_identity_resolves"):
        assert f"PASS {singular}" in built.build_output, singular


# ===========================================================================
# Grain and the diagonal
# ===========================================================================
def test_the_grain_is_one_row_per_ordered_pair(built):
    assert built("""
        select count(*) from (
            select league_key, row_canonical_franchise_id,
                   opponent_canonical_franchise_id
            from ANALYTICS.mart_franchise_head_to_head
            group by 1, 2, 3 having count(*) > 1)
    """) == [(0,)]


def test_the_diagonal_is_absent_not_zero(built):
    """A blank cell says "not a thing that happens"; a 0-0 says "they played
    and nobody scored"."""
    assert built("""
        select count(*) from ANALYTICS.mart_franchise_head_to_head
        where row_canonical_franchise_id = opponent_canonical_franchise_id
    """) == [(0,)]


def test_every_pair_is_told_from_both_sides(built):
    assert built("""
        select count(*) from ANALYTICS.mart_franchise_head_to_head a
        left join ANALYTICS.mart_franchise_head_to_head b
          on a.league_key = b.league_key
         and a.row_canonical_franchise_id = b.opponent_canonical_franchise_id
         and a.opponent_canonical_franchise_id = b.row_canonical_franchise_id
        where b.league_key is null
    """) == [(0,)]


# ===========================================================================
# The measures
# ===========================================================================
def test_a_three_meeting_series_aggregates_exactly(built):
    """1 vs 2: won 100-90, lost 88-99, won 110-100. Hand-checkable on purpose
    -- every measure in the contract, one assertion."""
    (meetings, wins, losses, ties, pf, pa, margin, win_pct,
     first_season, last_season, _, _) = _pair(built, 1, 2)

    assert (meetings, wins, losses, ties) == (3, 2, 1, 0)
    assert (pf, pa, margin) == (298.0, 289.0, 9.0)
    assert win_pct == pytest.approx(2 / 3)
    assert (first_season, last_season) == (2021, 2022)


def test_the_reverse_row_is_the_exact_mirror(built):
    forward = _pair(built, 1, 2)
    reverse = _pair(built, 2, 1)

    assert forward[0] == reverse[0]                    # meetings
    assert (forward[1], forward[2]) == (reverse[2], reverse[1])   # W <-> L
    assert forward[3] == reverse[3]                    # ties
    assert (forward[4], forward[5]) == (reverse[5], reverse[4])   # PF <-> PA
    assert forward[6] == -reverse[6]                   # margin
    assert forward[7] + reverse[7] == pytest.approx(1.0)


def test_a_tie_is_half_a_win(built):
    """5 vs 6: one win, one tie. The project's winning percentage counts a tie
    as half a win -- the same formula the all-time ordering uses."""
    meetings, wins, losses, ties, pf, pa, margin, win_pct = _pair(built, 5, 6)[:8]

    assert (meetings, wins, losses, ties) == (2, 1, 0, 1)
    assert (pf, pa, margin) == (135.5, 125.5, 10.0)
    assert win_pct == pytest.approx(0.75)


def test_results_partition_the_meetings(built):
    assert built("""
        select count(*) from ANALYTICS.mart_franchise_head_to_head
        where wins + losses + ties != meetings
    """) == [(0,)]


# ===========================================================================
# Byes and unscored meetings
# ===========================================================================
def test_a_bye_is_not_a_meeting(built):
    """Team 4 has a bye in 2021 period 2 and two real meetings. A bye that
    leaked in would show as a third."""
    assert built("""
        select sum(meetings) from ANALYTICS.mart_franchise_head_to_head
        where league_key = ? and row_canonical_franchise_id = '4'
    """, [LEAGUE]) == [(2,)]


def test_an_unscored_meeting_does_not_become_a_tie(built):
    """5 vs 8 exists in the fixture with one platform score missing, and the
    fact's else-branch labels both rows 'T'. Trusting `result` alone would
    write a tie into a rivalry record that was never played to a finish."""
    assert _pair(built, 5, 8) is None
    assert _pair(built, 8, 5) is None


def test_no_franchise_carries_a_phantom_tie(built):
    """The stronger reading of the same rule: the only ties in the whole
    ledger are the two genuine ones (3-4 in 2021, 5-6 in 2023), counted from
    both sides."""
    assert built("""
        select sum(ties) from ANALYTICS.mart_franchise_head_to_head
        where league_key = ?
    """, [LEAGUE]) == [(4,)]


# ===========================================================================
# Canonical lineage
# ===========================================================================
def test_a_re_minted_id_is_one_rivalry_across_both_eras(built):
    """13 lost to 1 in 2021 and, as id 30, beat 1 in 2023. One franchise, one
    row, 1-1 -- not two franchises with one result each."""
    meetings, wins, losses, ties, pf, pa, margin = _pair(built, 13, 1)[:7]

    assert (meetings, wins, losses, ties) == (2, 1, 1, 0)
    assert (pf, pa, margin) == (160.0, 145.0, 15.0)


def test_the_re_minted_id_has_no_row_of_its_own(built):
    """Id 30 resolves to canonical 13, so 30 must never be a key here."""
    assert built("""
        select count(*) from ANALYTICS.mart_franchise_head_to_head
        where league_key = ?
          and (row_canonical_franchise_id = '30'
               or opponent_canonical_franchise_id = '30')
    """, [LEAGUE]) == [(0,)]


def test_display_follows_the_latest_era_not_the_oldest(built):
    """A franchise that left and came back shows the name it wears NOW. The
    identity anchor is the earliest id; the DISPLAY anchor is the latest."""
    assert _pair(built, 13, 1)[10] == "Echo Era Two"


def test_a_rename_shows_the_current_name(built):
    """3 played 2021-22 as "Gamma Ghosts" and 2023 as "Gamma Giants"."""
    assert _pair(built, 3, 4)[10] == "Gamma Giants"


def test_a_configured_name_beats_the_observed_one(built):
    """Franchise 8's lineage row names it; the box scores call it something
    else. Configured wins -- that is what configuring it is for."""
    row = built("""
        select distinct row_franchise_name, row_franchise_abbrev
        from ANALYTICS.mart_franchise_head_to_head
        where league_key = ? and row_canonical_franchise_id = '8'
    """, [LEAGUE])

    assert row == [("Configured Eight", "CFG8")]


def test_two_ids_collapsing_onto_one_franchise_make_no_self_row(built):
    """20 and 21 both played 2023 and met each other, and the lineage says
    they are one franchise. That meeting is a franchise playing itself: it
    leaves the ledger rather than landing on the diagonal."""
    assert _pair(built, 20, 20) is None
    assert built("""
        select count(*) from ANALYTICS.mart_franchise_head_to_head
        where league_key = ? and row_canonical_franchise_id in ('20', '21')
    """, [LEAGUE]) == [(0,)]


# ===========================================================================
# Same name, different franchises
# ===========================================================================
def test_identically_named_franchises_stay_separate(built):
    """5 and 6 are both "Twin Name FC" and unrelated. Two canonical ids, two
    sets of rows -- a name-keyed aggregation would have merged them."""
    names = built("""
        select row_canonical_franchise_id, row_franchise_name
        from ANALYTICS.mart_franchise_head_to_head
        where league_key = ? and row_franchise_name = 'Twin Name FC'
        group by 1, 2 order by 1
    """, [LEAGUE])

    assert names == [("5", "Twin Name FC"), ("6", "Twin Name FC")]


def test_the_twins_own_series_survives_the_shared_name(built):
    """They play EACH OTHER, so merging them would not just blur two rows --
    it would move a real series onto the diagonal and delete it."""
    assert _pair(built, 5, 6)[0] == 2
    assert _pair(built, 6, 5)[0] == 2


# ===========================================================================
# The holding pen
# ===========================================================================
def test_a_season_parked_on_the_pen_leaves_the_ledger(built):
    """7's 2022 is declared unowned by a season-scoped lineage row, so its
    only 2022 meeting -- against 3 -- is not 7's to carry."""
    assert _pair(built, 7, 3) is None
    assert _pair(built, 3, 7) is None


def test_the_park_removes_the_meeting_from_both_directions(built):
    """The exclusion sits BEFORE the aggregation for this reason: dropping one
    side only would leave 3 with a win nobody lost."""
    assert built("""
        select sum(meetings) from ANALYTICS.mart_franchise_head_to_head
        where league_key = ? and row_canonical_franchise_id = '3'
    """, [LEAGUE]) == [(2,)]


def test_the_parked_franchise_keeps_its_other_seasons(built):
    """Only 2022 was parked. 7's 2021 loss and 2023 win are still its own --
    a season-scoped row must not rewrite a franchise's whole history."""
    assert _pair(built, 7, 8)[:4] == (1, 0, 1, 0)
    assert _pair(built, 7, 2)[:4] == (1, 1, 0, 0)


def test_the_pen_is_never_a_franchise_in_the_ledger(built):
    assert built("""
        select count(*) from ANALYTICS.mart_franchise_head_to_head
        where row_canonical_franchise_id = ?
           or opponent_canonical_franchise_id = ?
    """, [PEN, PEN]) == [(0,)]


# ===========================================================================
# Defunct franchises, and league scoping
# ===========================================================================
def test_a_defunct_franchise_is_still_in_the_ledger(built):
    """There is no activity filter. 4 stopped playing after 2022 and its
    rivalries are still history."""
    row = _pair(built, 4, 3)

    assert row[:4] == (1, 0, 0, 1)
    assert row[9] == 2021


def test_leagues_do_not_blend(built):
    """espn-other carries the same ids AND the same names. Its 1-vs-2 series
    is one meeting; espn-fix's is three."""
    assert _pair(built, 1, 2, league=OTHER_LEAGUE)[0] == 1
    assert _pair(built, 1, 2)[0] == 3


def test_league_key_is_part_of_the_grain(built):
    assert built("""
        select count(distinct league_key)
        from ANALYTICS.mart_franchise_head_to_head
    """) == [(2,)]
