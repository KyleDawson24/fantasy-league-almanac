"""The MLB-229 Rivalry Matrix contract, built for real against synthetic teams.

WHY THIS BUILDS RATHER THAN INSPECTS. Asserting on model SQL proves somebody
typed it, not that it computes anything. This builds the identity machinery, the
closure evidence, the season-points seam and both rivalry marts with dbt against
a THROWAWAY DuckDB holding only fixtures this file wrote, then queries the
answers. Nothing touches the private warehouse, the checked-in data/duckdb tree,
the maintainer's league_config, ESPN, or a Google account: `tmp_path_factory`
puts the database and the seed directory under pytest's temp root and they die
with the session.

WHAT IS FIXTURE AND WHAT IS REAL. Five relations are stood up by hand -- the
matchup surface, the box scores the franchise spine reads, the two CBS standings
shapes, and the delivered season standings. Those are inputs; reproducing ESPN's
whole player chain to reach them would test the chain, not the ledger.
EVERYTHING ELSE IS THE REAL PROJECT SQL, built by real dbt, including the
closure evidence: RAW.MATCHUP_SCHEDULE carries genuine ESPN-shaped payloads and
`stg_matchup_schedule` -> `int_matchup_period_evidence` derives `is_closed` from
them. That matters, because "the mart honours a boolean somebody handed it" and
"an in-flight Tuesday does not count as a win" are different claims and only the
second one is worth making.

WHY TWO BUILDS. The marts are tables, so a rebuild is the only way their
contents can change. The first build runs over EMPTY inputs -- proving a league
with no history produces empty marts and a green suite rather than a build
failure -- and the second runs over the fixture league.

THE FIXTURE LEAGUE IS A LIST OF RULINGS WEARING TEAM NAMES. Every team exists to
make one rule fail if it is broken: a configured name on an anchor id, a
configured name on a RE-MINTED id, two unrelated franchises sharing ONE
configured name (which must collapse), two unrelated franchises sharing an
OBSERVED name (which must not), a rename, a season parked on the holding pen, a
bye, a tie, an unscored matchup, an OPEN matchup period, the final period of a
COMPLETED season, a season with no schedule capture at all, and a team that
joined late so its rivals have seasons it never played. A second league carrying
the same ids and names proves none of it leaks across leagues.
"""

import csv
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
LEAGUE_CONFIG = PROJECT_DIR / "league_config"

LEAGUE = "espn-fix"
OTHER_LEAGUE = "espn-other"
# One league per closure posture, so each proves one thing and nothing has two
# jobs. Named for the posture rather than the platform -- none of this
# dispatches on platform.
OPEN_LEAGUE = "espn-open"       # latest season, no capture, no final evidence
FINAL_LEAGUE = "espn-final"     # latest season, no capture, final ranks served
BROKEN_LEAGUE = "espn-broken"   # capture present, payload unreadable
POINTS_LEAGUE = "cbs-fix"       # no matchups at all -- the points format
TYPE5_LEAGUE = "espn-season-points"  # ESPN's measured multi-team period
PEN = "9999"

BUILD_SELECTION = [
    "cbs_franchises",
    "franchise_lineage",
    "stg_matchup_schedule",
    "int_matchup_period_evidence",
    "int_league_season_closure",
    "int_franchise_seasons",
    "int_franchise_registry",
    "int_franchise_season_points",
    "int_franchise_current_teams",
    "dim_franchise",
    "dim_franchise_season",
    "dim_franchise_identity",
    "dim_league_format",
    "mart_franchise_rivalry",
    "mart_franchise_rivalry_axes",
    "assert_rivalry_reciprocity",
    "assert_rivalry_pairs_are_mutual",
    "assert_rivalry_has_no_diagonal",
    "assert_rivalry_results_partition_meetings",
    "assert_rivalry_identity_resolves",
    "assert_rivalry_matchups_have_closure_evidence",
    "assert_configured_name_has_no_active_collision",
    "assert_live_season_has_schedule_capture",
]

# Two neighbouring singular tests ride along on models selected above and read
# the ESPN player chain / derived calendar, which this fixture deliberately does
# not stand up. Excluded by name so the exclusion is visible: a blanket
# `--exclude test_type:singular` would also have dropped
# assert_franchise_display_anchors_on_latest_era, which DOES belong here.
BUILD_EXCLUSION = [
    "assert_team_display_resolves_through_dim",
    "assert_one_derived_standard_per_league_season",
]


# ---------------------------------------------------------------------------
# The fixture league
# ---------------------------------------------------------------------------
#
# (team_id, season_year, name, abbrev) -- what the box scores observed. The
# season list per team IS its era: int_franchise_seasons builds its spine from
# exactly these rows.
OBSERVED = [
    (1, 2021, "Alpha Anchors", "ALPH"),
    (1, 2022, "Alpha Anchors", "ALPH"),
    (1, 2023, "Alpha Anchors", "ALPH"),
    (2, 2021, "Beta Bandits", "BETA"),
    (2, 2022, "Beta Bandits", "BETA"),
    (2, 2023, "Beta Bandits", "BETA"),

    # A RENAME. Display follows the latest observed name.
    (3, 2021, "Gamma Ghosts", "GHST"),
    (3, 2022, "Gamma Ghosts", "GHST"),
    (3, 2023, "Gamma Giants", "GIAN"),

    # DEFUNCT after 2022. Keeps its history; loses its axis.
    (4, 2021, "Delta Ducks", "DUCK"),
    (4, 2022, "Delta Ducks", "DUCK"),

    # TWO UNRELATED FRANCHISES, one OBSERVED name, no configured name for
    # either. They must stay apart -- observation is a coincidence.
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

    # A CONFIGURED name on the anchor id.
    (8, 2021, "Observed Eight", "OBS8"),
    (8, 2022, "Observed Eight", "OBS8"),
    (8, 2023, "Observed Eight", "OBS8"),

    # JOINED LATE: no 2021. Its rivals' 2021 seasons are seasons it never
    # played, and must count for nobody.
    (9, 2022, "Ninth Wonder", "NIN9"),
    (9, 2023, "Ninth Wonder", "NIN9"),

    # A RE-MINTED id whose CONFIGURED name is written against the LATER era.
    # The name belongs to the franchise, so both ids must take it.
    (13, 2021, "Echo Era One", "ECH1"),
    (13, 2022, "Echo Era One", "ECH1"),
    (30, 2023, "Echo Era Two", "ECH2"),

    # Two ids the lineage collapses onto one franchise, both live in 2023 --
    # the fallback route to a self-matchup.
    (20, 2023, "Merge Left", "MRGL"),
    (21, 2023, "Merge Right", "MRGR"),

    # TWO SEPARATE FRANCHISES sharing ONE CONFIGURED name, both active. Under
    # the ruling they are one team; the collision diagnostic warns about it.
    (40, 2021, "Bent Spokes East", "BSE"),
    (40, 2022, "Bent Spokes East", "BSE"),
    (40, 2023, "Bent Spokes East", "BSE"),
    (41, 2021, "Bent Spokes West", "BSW"),
    (41, 2022, "Bent Spokes West", "BSW"),
    (41, 2023, "Bent Spokes West", "BSW"),

    # A SEASON-SCOPED configured name lands on 2022 only. Deliberately given no
    # matchups at all, so it forms pairs through the season ledger and cannot
    # perturb any other team's head-to-head record while proving its own point.
    (50, 2021, "Nomad Nine", "NMD"),
    (50, 2022, "Nomad Nine", "NMD"),
    (50, 2023, "Nomad Nine", "NMD"),
]

OTHER_OBSERVED = [
    (1, 2023, "Alpha Anchors", "ALPH"),
    (2, 2023, "Beta Bandits", "BETA"),
]

# The three closure-posture leagues and the points league. Two teams each --
# the posture is the subject, not the roster.
POSTURE_OBSERVED = [
    (1, 2024, "Open One", "OP1"),
    (2, 2024, "Open Two", "OP2"),
]
POINTS_OBSERVED = [
    (1, 2024, "Points One", "PT1"),
    (2, 2024, "Points Two", "PT2"),
]

# (league_key, franchise_id, season_year, canonical_franchise_id,
#  canonical_name, canonical_abbrev)
LINEAGE = [
    # Re-mint link AND a configured name, written against the NON-ANCHOR id.
    (LEAGUE, "30", "", "13", "Echo Dynasty", "ECHD"),
    # Fallback collapse: linked ids, no configured name.
    (LEAGUE, "21", "", "20", "", ""),
    # Configured name on the anchor id.
    (LEAGUE, "8", "", "8", "Configured Eight", "CFG8"),
    # Two DIFFERENT canonical franchises, one configured name.
    (LEAGUE, "40", "", "40", "Bent Spokes", "BENT"),
    (LEAGUE, "41", "", "41", "Bent Spokes", "BENT"),
    # Season-scoped: 2022 belonged to nobody.
    (LEAGUE, "7", "2022", PEN, "", ""),
    # SEASON-SCOPED CONFIGURED NAME. The seed schema has always allowed a name
    # on a season row; dim_franchise_season used to read only the id from one,
    # so this did nothing at all -- silently. It must now name franchise 50 for
    # 2022 and leave 2021 and 2023 alone.
    (LEAGUE, "50", "2022", "50", "Nomad Alias", "NMD2"),
]

# (season_year, matchup_period, team_id, opponent_id, points, opponent_points)
# Each becomes TWO mart_team_matchup rows, one per perspective.
#
# Period status, from the RAW payloads below:
#   2021 -- NO schedule capture at all. Retained.
#   2022 -- captured and COMPLETE. Both periods closed; mp2 is the final period
#           of a finished season and must count.
#   2023 -- captured and IN FLIGHT (current period 2). mp1 closed, mp2 OPEN.
MEETINGS = [
    # -- 2021, uncaptured ---------------------------------------------------
    (2021, 1, 1, 2, 100.0, 90.0),
    (2021, 1, 3, 4, 80.0, 80.0),        # a TIE
    (2021, 1, 5, 6, 70.5, 60.5),        # the observed-name twins meet
    (2021, 1, 7, 8, 50.0, 55.0),
    (2021, 1, 13, 1, 40.0, 45.0),       # the FIRST era of the re-mint
    (2021, 1, 40, 41, 30.0, 20.0),      # VANISHES: one configured name
    (2021, 2, 1, 2, 88.0, 99.0),

    # -- 2022, captured and closed -----------------------------------------
    (2022, 1, 1, 2, 110.0, 100.0),
    (2022, 1, 7, 3, 60.0, 65.0),        # VANISHES: 7's 2022 is on the pen
    (2022, 1, 13, 4, 30.0, 20.0),
    (2022, 2, 1, 3, 120.0, 110.0),      # the FINAL period of a DONE season
    (2022, 2, 5, 6, 65.0, 65.0),        # a TIE

    # -- 2023, captured; period 1 closed, period 2 OPEN --------------------
    (2023, 1, 30, 1, 120.0, 100.0),     # the SECOND era of the re-mint
    (2023, 1, 3, 6, 90.0, 80.0),
    (2023, 1, 20, 21, 55.0, 45.0),      # VANISHES: fallback collapse
    (2023, 2, 1, 2, 200.0, 10.0),       # MUST NOT COUNT -- period is open
]

OTHER_MEETINGS = [
    (2023, 1, 1, 2, 200.0, 100.0),
]

# Identical running scores in each posture league's latest season. Whether they
# count is decided ENTIRELY by the closure evidence, which is the point: the
# matchup rows are the same, so any difference in the ledger is the gate.
POSTURE_MEETINGS = [
    (2024, 1, 1, 2, 150.0, 50.0),
]

# (season_year, team_id, platform_points) -- the platform's own season totals.
# 2021 has no schedule capture and is not the league's latest season, so it is
# complete by supersession; 2022 is complete by measurement; 2023 is in flight
# and must contribute nothing.
SEASON_POINTS = [
    (2021, 1, 1000.0), (2021, 2, 900.0), (2021, 3, 800.0), (2021, 4, 800.0),
    (2021, 5, 700.0), (2021, 6, 600.0), (2021, 7, 500.0), (2021, 8, 550.0),
    (2021, 13, 400.0), (2021, 40, 300.0), (2021, 41, 200.0),

    (2022, 1, 1100.0), (2022, 2, 1000.0), (2022, 3, 850.0), (2022, 4, 750.0),
    (2022, 5, 650.0), (2022, 6, 620.0), (2022, 7, 520.0), (2022, 8, 560.0),
    (2022, 9, 900.0), (2022, 13, 430.0), (2022, 40, 310.0), (2022, 41, 210.0),

    # 2023 -- in flight. Every one of these must be invisible to the ledger,
    # and the deliberately absurd totals are how a leak would announce itself.
    (2023, 1, 9999.0), (2023, 2, 9999.0), (2023, 3, 9999.0), (2023, 5, 9999.0),
    (2023, 6, 9999.0), (2023, 7, 9999.0), (2023, 8, 9999.0), (2023, 9, 9999.0),
    (2023, 20, 9999.0), (2023, 21, 9999.0), (2023, 30, 9999.0),
    (2023, 40, 9999.0), (2023, 41, 9999.0),
]

# The season-scoped-name franchise, across all three seasons.
SEASON_POINTS += [(2021, 50, 450.0), (2022, 50, 460.0), (2023, 50, 9999.0)]

OTHER_SEASON_POINTS = [(2023, 1, 500.0), (2023, 2, 400.0)]

# One season each for the posture leagues, with the same totals, so the
# season ledger's answer also turns purely on the closure evidence.
POSTURE_SEASON_POINTS = [(2024, 1, 700.0), (2024, 2, 600.0)]


def _result(points, opponent_points):
    """The fact's own W/L/T derivation, restated so the fixture carries what the
    real surface would -- including its else-branch, which is the trap the
    unscored meeting is here to spring."""
    if points is None or opponent_points is None:
        return "T"
    if points > opponent_points:
        return "W"
    if points < opponent_points:
        return "L"
    return "T"


def _matchup_rows():
    rows = []
    for league, meetings in ((LEAGUE, MEETINGS), (OTHER_LEAGUE, OTHER_MEETINGS),
                             (OPEN_LEAGUE, POSTURE_MEETINGS),
                             (FINAL_LEAGUE, POSTURE_MEETINGS),
                             (BROKEN_LEAGUE, POSTURE_MEETINGS)):
        for season, period, team, opponent, pts, opp_pts in meetings:
            rows.append((league, season, period, team, opponent, pts, opp_pts,
                         _result(pts, opp_pts)))
            rows.append((league, season, period, opponent, team, opp_pts, pts,
                         _result(opp_pts, pts)))

    # A BYE. The real mart_team_matchup filters these upstream; the fixture
    # carries one anyway, because the ledger's correctness must not depend on
    # somebody else's WHERE clause staying where it is.
    rows.append((LEAGUE, 2021, 2, 4, None, 77.0, None, None))

    # AN UNSCORED MATCHUP: both sides exist, one has no platform score, so the
    # fact's else-branch calls it a TIE on both rows.
    rows.append((LEAGUE, 2022, 1, 5, 8, None, 42.0, "T"))
    rows.append((LEAGUE, 2022, 1, 8, 5, 42.0, None, "T"))

    return rows


# ---------------------------------------------------------------------------
# ESPN-shaped schedule payloads -- the real closure evidence
# ---------------------------------------------------------------------------
def _side(scoring_periods):
    return {"teamId": 1, "totalPoints": 0.0,
            "pointsByScoringPeriod": {str(sp): 0.0 for sp in scoring_periods}}


def _schedule_payload(period_lengths, *, season_year, current, complete):
    """Periods 1..N of consecutive scoring periods, two matchups each.

    Both sides carry the SAME membership keys, which is what lets
    int_matchup_period_evidence call a period well-shaped. A completed season
    additionally reports latestScoringPeriod strictly past finalScoringPeriod,
    which is the proof that promotes its final period to closed.
    """
    schedule, sp = [], 1
    for index, length in enumerate(period_lengths, start=1):
        members = list(range(sp, sp + length))
        sp += length
        for _ in range(2):
            schedule.append({"id": index * 100, "matchupPeriodId": index,
                             "winner": "HOME",
                             "home": _side(members), "away": _side(members)})
    status = {"currentMatchupPeriod": current}
    if complete:
        total = sum(period_lengths)
        status["finalScoringPeriod"] = total
        status["latestScoringPeriod"] = total + 1
    return {"seasonId": season_year, "status": status, "schedule": schedule}


SCHEDULE_CAPTURES = [
    # Finished: the pointer rests on the final period and the last scoring day
    # has passed, so period 2 is closed by promotion rather than by being
    # behind the pointer.
    (LEAGUE, 2022, _schedule_payload([7, 7], season_year=2022, current=2,
                                     complete=True)),
    # In flight: same shape, same pointer, no completion evidence. Period 2
    # must stay OPEN.
    (LEAGUE, 2023, _schedule_payload([7, 7], season_year=2023, current=2,
                                     complete=False)),
    # 2021 is deliberately absent -- the historical season nobody captured.

    # The other league's only season, finished, so its cross-league isolation
    # meeting still counts.
    (OTHER_LEAGUE, 2023, _schedule_payload([7], season_year=2023, current=1,
                                           complete=True)),

    # PRESENT BUT UNREADABLE. A capture exists -- so has_schedule_capture is
    # true and the season cannot fall back to "uncaptured history" -- but
    # nothing downstream can derive a period from it. The gate must therefore
    # find no closed period and count nothing.
    (BROKEN_LEAGUE, 2024, {"seasonId": 2024, "status": {}, "schedule": []}),

    # ESPN season-long points, measured in the stranger rehearsal. One live
    # multi-team period has no opponent pairing, but currentLeagueType=5 is
    # positive format evidence rather than an empty install.
    (TYPE5_LEAGUE, 2026, {
        "seasonId": 2026,
        "status": {"currentMatchupPeriod": 1, "latestScoringPeriod": 142,
                   "currentLeagueType": 5, "createdAsLeagueType": 5},
        "schedule": [{"matchupPeriodId": 1}],
    }),

    # espn-open and espn-final have NO capture at all, deliberately.
]


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

    REWRITTEN BEFORE EVERY BUILD, ON PURPOSE. dbt writes each node's compiled
    artifact to target/compiled/<project>/<original_file_path>, and joining a
    target path with an ABSOLUTE original path yields the absolute path itself
    -- so a seed reached through an absolute DBT_LEAGUE_CONFIG gets its own CSV
    overwritten with the `create table ...` SQL dbt just compiled. The load
    happens first, so a single-build fixture never notices; this file builds
    twice, and the second build read a CSV whose header had become one column of
    DDL. Regenerating keeps every write under pytest's temp root, where a
    relative path pointing back out of the project would not.
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
    con.execute("create schema if not exists RAW")
    con.execute("create schema if not exists ANALYTICS")
    # The RAW contract's shape, as tools/load_parquet_to_duckdb.py lands it.
    con.execute("""
        create or replace table RAW.MATCHUP_SCHEDULE (
            SEASON_YEAR decimal(38,0), RAW_JSON json,
            EXTRACTED_AT timestamp, LEAGUE_KEY varchar)
    """)
    con.execute("""
        create or replace table ANALYTICS.mart_team_matchup (
            league_key varchar, season_year integer, matchup_period integer,
            team_id integer, opponent_id integer,
            platform_points double, opponent_points double, result varchar)
    """)
    con.execute("""
        create or replace table ANALYTICS.stg_box_scores (
            league_key varchar, season_year integer, matchup_period integer,
            team_id integer, team_name varchar, team_abbrev varchar)
    """)
    con.execute("""
        create or replace table ANALYTICS.stg_team_standings (
            league_key varchar, season_year integer, team_id integer,
            platform_points double, final_rank integer)
    """)
    # dim_league_format's two signals. mart_period_standings exists only where
    # a league has no matchups to be scored on, which is the points-format
    # tell; stg_matchup_pairs is the positive evidence for the other side.
    con.execute("""
        create or replace table ANALYTICS.mart_period_standings (
            league_key varchar, season_year integer, period integer,
            team_id varchar)
    """)
    con.execute("""
        create or replace table ANALYTICS.stg_matchup_pairs (
            league_key varchar, season_year integer, matchup_period integer,
            home_team_id integer, away_team_id integer)
    """)
    # The CBS arms, present and empty: this league is served by the derived /
    # delivered branches, and an absent relation would fail the build rather
    # than contribute nothing.
    con.execute("""
        create or replace table ANALYTICS.stg_cbs__ui_standings (
            league_key varchar, season_year integer, franchise_id varchar,
            total_points double)
    """)
    con.execute("""
        create or replace table ANALYTICS.stg_cbs__standings (
            league_key varchar, season_year integer, team_id varchar)
    """)


def _populate(con):
    stamped = datetime(2026, 8, 11, 12, 0, 0)
    for league, season, payload in SCHEDULE_CAPTURES:
        con.execute("insert into RAW.MATCHUP_SCHEDULE values (?, ?, ?, ?)",
                    [season, json.dumps(payload), stamped, league])
    observed_by_league = [
        (LEAGUE, OBSERVED), (OTHER_LEAGUE, OTHER_OBSERVED),
        (OPEN_LEAGUE, POSTURE_OBSERVED), (FINAL_LEAGUE, POSTURE_OBSERVED),
        (BROKEN_LEAGUE, POSTURE_OBSERVED), (POINTS_LEAGUE, POINTS_OBSERVED),
    ]
    for league, observed in observed_by_league:
        for team_id, season, name, abbrev in observed:
            con.execute(
                "insert into ANALYTICS.stg_box_scores values (?, ?, ?, ?, ?, ?)",
                [league, season, 1, team_id, name, abbrev])

    # final_rank is NULL everywhere except espn-final, whose latest season has
    # no schedule capture and must still count BECAUSE the platform published
    # final ranks for it.
    for league, points in ((LEAGUE, SEASON_POINTS),
                           (OTHER_LEAGUE, OTHER_SEASON_POINTS),
                           (OPEN_LEAGUE, POSTURE_SEASON_POINTS),
                           (FINAL_LEAGUE, POSTURE_SEASON_POINTS),
                           (BROKEN_LEAGUE, POSTURE_SEASON_POINTS)):
        for season, team_id, total in points:
            con.execute(
                "insert into ANALYTICS.stg_team_standings values (?, ?, ?, ?, ?)",
                [league, season, team_id, total,
                 team_id if league == FINAL_LEAGUE else None])

    # The points league: delivered period standings and parsed final standings,
    # and NO matchups anywhere. Its rivalry is season points by construction.
    for team_id, season, _name, _abbrev in POINTS_OBSERVED:
        con.execute(
            "insert into ANALYTICS.mart_period_standings values (?, ?, ?, ?)",
            [POINTS_LEAGUE, season, 1, str(team_id)])
    for season, team_id, total in ((2024, 1, 880.0), (2024, 2, 770.0)):
        con.execute(
            "insert into ANALYTICS.stg_cbs__ui_standings values (?, ?, ?, ?)",
            [POINTS_LEAGUE, season, str(team_id), total])
        # Its live team feed -- how a points league says which teams exist,
        # and therefore where its matrix axes come from.
        con.execute(
            "insert into ANALYTICS.stg_cbs__standings values (?, ?, ?)",
            [POINTS_LEAGUE, season, str(team_id)])

    for row in _matchup_rows():
        con.execute(
            "insert into ANALYTICS.mart_team_matchup values "
            "(?, ?, ?, ?, ?, ?, ?, ?)", list(row))
        # The format signal mirrors the matchup surface: a league with pairings
        # is head-to-head. Byes carry a NULL opponent and are not a pairing.
        if row[4] is not None:
            con.execute(
                "insert into ANALYTICS.stg_matchup_pairs values (?, ?, ?, ?, ?)",
                [row[0], row[1], row[2], row[3], row[4]])


def _build(root, db_path):
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
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover
        _skip_loudly(f"duckdb not importable ({exc}); the marts were NOT built")
    if not (PROJECT_DIR / "dbt_packages").is_dir():
        _skip_loudly("dbt_packages missing (run `dbt deps`); the marts were "
                     "NOT built")

    root = tmp_path_factory.mktemp("mlb229")
    db_path = root / "ESPN_FANTASY.duckdb"

    con = duckdb.connect(str(db_path))
    _create_inputs(con)
    con.close()

    empty = _build(root, db_path)
    if empty.returncode != 0:
        pytest.fail("the build over EMPTY inputs failed, so a league with no "
                    "history cannot produce an empty matrix:\n"
                    f"{empty.stdout[-6000:]}\n{empty.stderr[-2000:]}")

    con = duckdb.connect(str(db_path))
    empty_counts = {
        model: con.execute(f"select count(*) from ANALYTICS.{model}").fetchone()[0]
        for model in ("dim_franchise_identity", "int_franchise_season_points",
                      "int_franchise_current_teams", "int_league_season_closure",
                      "dim_league_format", "mart_franchise_rivalry",
                      "mart_franchise_rivalry_axes")
    }
    _populate(con)
    con.close()

    populated = _build(root, db_path)
    if populated.returncode != 0:
        pytest.fail("the build over the fixture league failed:\n"
                    f"{populated.stdout[-8000:]}\n{populated.stderr[-2000:]}")

    con = duckdb.connect(str(db_path))

    def query(sql, params=None):
        return con.execute(sql, params or []).fetchall()

    query.empty_counts = empty_counts
    query.empty_output = empty.stdout
    query.build_output = populated.stdout

    yield query
    con.close()


def _pair(built, row_key, opp_key, league=LEAGUE):
    """One ordered pair's whole row as a dict, or None."""
    cols = ("matchup_meetings", "matchup_wins", "matchup_losses", "matchup_ties",
            "points_for", "points_against", "points_margin", "matchup_win_pct",
            "first_meeting_season", "last_meeting_season",
            "season_meetings", "season_wins", "season_losses", "season_ties",
            "season_points_for", "season_points_against", "season_win_pct",
            "first_season_compared", "last_season_compared",
            "row_team_name", "opponent_team_name", "row_identity_source")
    rows = built(
        f"select {', '.join(cols)} from ANALYTICS.mart_franchise_rivalry "
        "where league_key = ? and row_identity_key = ? "
        "and opponent_identity_key = ?", [league, row_key, opp_key])
    return dict(zip(cols, rows[0])) if rows else None


def _matchup_record(built, row_key, opp_key, league=LEAGUE):
    p = _pair(built, row_key, opp_key, league)
    return None if p is None else (p["matchup_meetings"], p["matchup_wins"],
                                   p["matchup_losses"], p["matchup_ties"])


def _season_record(built, row_key, opp_key, league=LEAGUE):
    p = _pair(built, row_key, opp_key, league)
    return None if p is None else (p["season_meetings"], p["season_wins"],
                                   p["season_losses"], p["season_ties"])


# ===========================================================================
# Empty source
# ===========================================================================
def test_an_empty_league_builds_an_empty_matrix(built):
    """A league that has captured nothing is a supported state -- the
    stranger's first install, and every league before its first extract."""
    assert built.empty_counts["mart_franchise_rivalry"] == 0
    assert built.empty_counts["mart_franchise_rivalry_axes"] == 0


def test_the_empty_build_invents_no_teams_or_seasons(built):
    assert built.empty_counts["dim_franchise_identity"] == 0
    assert built.empty_counts["int_franchise_season_points"] == 0
    assert built.empty_counts["int_franchise_current_teams"] == 0


def test_the_tests_ran_in_the_empty_build(built):
    """`dbt build`, not `dbt run`: the grain, reciprocity and diagonal tests are
    part of the evidence that empty is green, not just that it compiled."""
    assert ("PASS" in built.empty_output
            or "Completed successfully" in built.empty_output)


# ===========================================================================
# Controls -- proof the rest of this file is measuring something
# ===========================================================================
def test_the_fixture_league_produced_a_populated_matrix(built):
    """THE CONTROL. Every "must be absent" assertion below would pass just as
    well against an empty table."""
    rows, matchups, seasons = built("""
        select count(*), sum(matchup_meetings), sum(season_meetings)
        from ANALYTICS.mart_franchise_rivalry
    """)[0]

    assert rows > 0 and matchups > 0 and seasons > 0


def test_the_contract_tests_ran_against_the_populated_matrix(built):
    """The reciprocity, diagonal, partition and identity assertions are dbt
    singular tests, so they run inside the build rather than here. If a rename
    drops one from the selection it stops running silently."""
    for singular in ("assert_rivalry_reciprocity",
                     "assert_rivalry_pairs_are_mutual",
                     "assert_rivalry_has_no_diagonal",
                     "assert_rivalry_results_partition_meetings",
                     "assert_rivalry_identity_resolves"):
        assert f"PASS {singular}" in built.build_output, singular


# ===========================================================================
# Identity: configured names
# ===========================================================================
def test_a_configured_name_on_a_reminted_id_names_the_whole_franchise(built):
    """The lineage writes "Echo Dynasty" against id 30, the LATER era. Id 13 is
    the same franchise, so both eras take that identity -- a configured name
    belongs to the team, not to the row it was typed on."""
    keys = built("""
        select distinct franchise_id, identity_key, identity_source
        from ANALYTICS.dim_franchise_identity
        where league_key = ? and franchise_id in ('13', '30') order by 1
    """, [LEAGUE])

    assert keys == [("13", "name:Echo Dynasty", "configured_name"),
                    ("30", "name:Echo Dynasty", "configured_name")]


def test_the_reminted_franchise_has_one_rivalry_across_both_eras(built):
    """13 lost to 1 in 2021 and, as id 30, beat 1 in 2023 period 1. One team,
    one row, 1-1."""
    assert _matchup_record(built, "name:Echo Dynasty", "fid:1") == (2, 1, 1, 0)


def test_different_canonical_franchises_sharing_a_configured_name_collapse(built):
    """40 and 41 are unrelated -- no lineage link, so different
    canonical_franchise_ids -- and both configured "Bent Spokes". Under the
    ruling they are one team."""
    keys = built("""
        select distinct franchise_id, canonical_franchise_id, identity_key
        from ANALYTICS.dim_franchise_identity
        where league_key = ? and franchise_id in ('40', '41') order by 1
    """, [LEAGUE])

    assert keys == [("40", "40", "name:Bent Spokes"),
                    ("41", "41", "name:Bent Spokes")]


def test_the_collapsed_pair_plays_itself_and_leaves_the_ledger(built):
    """40 met 41 in 2021. Once they are one team that meeting is a team playing
    itself, so it goes -- rather than landing on the diagonal."""
    assert _pair(built, "name:Bent Spokes", "name:Bent Spokes") is None


def test_a_configured_name_beats_the_observed_one(built):
    """Franchise 8's lineage row names it; the box scores call it something
    else. Configured wins -- that is what configuring it is for."""
    names = built("""
        select distinct row_team_name from ANALYTICS.mart_franchise_rivalry
        where league_key = ? and row_identity_key = 'name:Configured Eight'
    """, [LEAGUE])

    assert names == [("Configured Eight",)]


def test_the_collision_diagnostic_warns_rather_than_failing(built):
    """Two teams that are both playing share one configured name. The rule still
    aggregates them -- but the league is told, so an accidental collision can be
    corrected. WARN, not ERROR: a display-seed typo must not stop a build."""
    assert ("Warning in test assert_configured_name_has_no_active_collision"
            in built.build_output), "the collision was not reported at all"
    assert "ERROR=0" in built.build_output, \
        "the diagnostic failed the build instead of warning"


def test_the_collision_is_reported_per_season_it_is_live(built):
    """One row per season both teams were playing under the shared name, so the
    league can see WHEN the collision started rather than only that it exists.
    40 and 41 overlap in all three.

    The ORDER BY inside the aggregate is not decoration, and this test found out
    the hard way: without it the same query returned '40, 41' for one season and
    '41, 40' for the next in a single build. That string is what a human reads
    to find the offending seed rows, so the shipped test spells it with
    listagg_ordered for exactly this reason.
    """
    rows = built("""
        select season_year, identity_name, colliding_franchises, franchise_ids
        from (
            select league_key, season_year, identity_key, identity_name,
                   count(distinct canonical_franchise_id) as colliding_franchises,
                   string_agg(distinct canonical_franchise_id, ', '
                              order by canonical_franchise_id)
                       as franchise_ids
            from ANALYTICS.dim_franchise_identity
            where identity_source = 'configured_name'
            group by 1, 2, 3, 4
            having count(distinct canonical_franchise_id) > 1
        ) where league_key = ? order by season_year
    """, [LEAGUE])

    assert rows == [(2021, "Bent Spokes", 2, "40, 41"),
                    (2022, "Bent Spokes", 2, "40, 41"),
                    (2023, "Bent Spokes", 2, "40, 41")]


# ===========================================================================
# Identity: the fallback
# ===========================================================================
def test_equal_observed_names_without_a_configured_one_stay_separate(built):
    """5 and 6 are both observed "Twin Name FC" and neither is configured.
    Observation is a coincidence; configuration is a statement. Two identities,
    both keyed on the franchise id."""
    rows = built("""
        select distinct franchise_id, identity_key, identity_source,
               identity_name
        from ANALYTICS.dim_franchise_identity
        where league_key = ? and franchise_id in ('5', '6') order by 1
    """, [LEAGUE])

    assert rows == [("5", "fid:5", "franchise_id", "Twin Name FC"),
                    ("6", "fid:6", "franchise_id", "Twin Name FC")]


def test_the_observed_twins_keep_their_own_series(built):
    """They play EACH OTHER, so merging them would not just blur two rows -- it
    would move a real series onto the diagonal and delete it. One 2021 win and
    one 2022 tie."""
    assert _matchup_record(built, "fid:5", "fid:6") == (2, 1, 0, 1)
    assert _matchup_record(built, "fid:6", "fid:5") == (2, 0, 1, 1)


def test_a_fallback_identity_displays_its_latest_observed_name(built):
    """3 played 2021-22 as "Gamma Ghosts" and 2023 as "Gamma Giants". With no
    configured name the best observed one is the label."""
    assert _pair(built, "fid:3", "fid:4")["row_team_name"] == "Gamma Giants"


def test_a_fallback_collapse_still_removes_a_self_matchup(built):
    """20 and 21 are linked by lineage with no configured name, so they share
    fid:20 -- and their 2023 meeting is a team playing itself."""
    assert _pair(built, "fid:20", "fid:20") is None
    assert built("""
        select count(*) from ANALYTICS.mart_franchise_rivalry
        where league_key = ? and row_identity_key = 'fid:21'
    """, [LEAGUE]) == [(0,)]


# ===========================================================================
# Matchup closure
# ===========================================================================
def test_the_closure_evidence_is_derived_not_asserted(built):
    """The control for every closure test below: is_closed comes from real
    payloads through the real models. 2022 is finished so both its periods are
    closed; 2023 is in flight so only period 1 is."""
    rows = built("""
        select season_year, matchup_period, is_closed
        from ANALYTICS.int_matchup_period_evidence
        where league_key = ? order by 1, 2
    """, [LEAGUE])

    assert rows == [(2022, 1, True), (2022, 2, True),
                    (2023, 1, True), (2023, 2, False)]


def test_an_open_matchup_period_does_not_count(built):
    """2023 period 2 is in flight and 1 leads 2 by 190. Counting a running score
    would hand 1 a fourth meeting and a third win."""
    assert _matchup_record(built, "fid:1", "fid:2") == (3, 2, 1, 0)


def test_the_open_period_is_absent_from_the_season_window(built):
    """The stronger reading: not merely uncounted, but invisible. 1 vs 2's last
    meeting is 2022, not the 2023 week currently being played."""
    assert _pair(built, "fid:1", "fid:2")["last_meeting_season"] == 2022


def test_a_completed_seasons_final_period_counts(built):
    """2022 period 2 is the LAST period of a finished season, so it is closed by
    promotion rather than by being behind the pointer -- the case a naive
    `matchup_period < current` would drop. 1 beat 3 there."""
    assert _matchup_record(built, "fid:1", "fid:3") == (1, 1, 0, 0)


def test_historical_data_without_a_schedule_capture_is_retained(built):
    """The schedule capture is opt-in and 2021 predates it. Testing is_closed
    alone would have deleted every season captured before the extract existed --
    1 vs 2's 2021 pair of meetings among them."""
    assert built("""
        select count(*) from ANALYTICS.int_matchup_period_evidence
        where league_key = ? and season_year = 2021
    """, [LEAGUE]) == [(0,)]
    assert _pair(built, "fid:1", "fid:2")["first_meeting_season"] == 2021


# ---------------------------------------------------------------------------
# Closure fails CLOSED -- one league per posture, identical matchup rows
# ---------------------------------------------------------------------------
def test_the_closure_postures_are_what_the_fixture_says(built):
    """The control for the four tests below. Same running scores in each
    league's latest season; only the evidence differs."""
    rows = built("""
        select league_key, has_schedule_capture, is_season_complete,
               completion_evidence
        from ANALYTICS.int_league_season_closure
        where season_year = 2024 order by league_key
    """)

    assert rows == [
        (POINTS_LEAGUE, False, True, "parsed_final_standings"),
        (BROKEN_LEAGUE, True, False, "schedule_capture"),
        (FINAL_LEAGUE, False, True, "delivered_final_rank"),
        (OPEN_LEAGUE, False, False, "unproven"),
    ]


def test_a_latest_season_with_no_capture_mints_nothing(built):
    """THE FAIL-OPEN THIS REPLACES. A league that has never run the schedule
    extract has a live season full of running scores, and the first version of
    this ledger read "no capture" as "historical, keep everything" -- counting
    this Tuesday as a win. Absence of evidence is not evidence of
    completion."""
    assert _matchup_record(built, "fid:1", "fid:2", league=OPEN_LEAGUE) is None


def test_a_present_but_unreadable_capture_fails_closed(built):
    """The same trap one layer down. This league HAS a capture, so it cannot
    fall back to uncaptured history -- but the payload yields no period
    evidence at all. A gate that decided capture presence from the derived
    evidence would read zero rows as "never captured" and fail OPEN on exactly
    the season whose payload it could not understand."""
    assert built("""
        select count(*) from ANALYTICS.int_matchup_period_evidence
        where league_key = ?
    """, [BROKEN_LEAGUE]) == [(0,)]
    assert _matchup_record(built, "fid:1", "fid:2", league=BROKEN_LEAGUE) is None


def test_a_latest_completed_season_counts_on_final_ranks_alone(built):
    """ESPN serves rankCalculatedFinal = 0 for every team in a season that has
    not finished, so a non-null final rank is the platform stating the season
    is over. That is enough on its own -- the latest loaded season must not be
    withheld merely because the OPTIONAL schedule capture has not run."""
    assert _matchup_record(built, "fid:1", "fid:2", league=FINAL_LEAGUE) == \
        (1, 1, 0, 0)
    assert _season_record(built, "fid:1", "fid:2", league=FINAL_LEAGUE) == \
        (1, 1, 0, 0)


def test_a_superseded_season_with_no_capture_still_counts(built):
    """espn-fix's 2021 predates the capture and the league has since played
    2022 and 2023. A season the league moved past is over, and its history is
    not the live season's problem."""
    assert built("""
        select is_season_complete, completion_evidence
        from ANALYTICS.int_league_season_closure
        where league_key = ? and season_year = 2021
    """, [LEAGUE]) == [(True, "superseded_season")]
    assert _pair(built, "fid:1", "fid:2")["first_meeting_season"] == 2021


def test_the_missing_capture_is_reported_rather_than_silent(built):
    """The gate is closed and silent, and a maintainer whose current season is
    simply absent cannot tell that from a season in which nothing has happened.
    So a warn-severity test names the league and the remedy."""
    assert ("Warning in test assert_live_season_has_schedule_capture"
            in built.build_output)
    assert "ERROR=0" in built.build_output


def test_playoff_and_abnormal_periods_are_not_the_gate(built):
    """Closure is the only period gate. Every closed period contributes,
    whatever its length or bracket -- a win in a 10-day opening week is a win.
    2022's two periods both count for 1."""
    row = _pair(built, "fid:1", "fid:2")

    assert row["matchup_meetings"] == 3
    assert _matchup_record(built, "fid:1", "fid:3") == (1, 1, 0, 0)


# ===========================================================================
# Matchup measures
# ===========================================================================
def test_a_three_meeting_series_aggregates_exactly(built):
    """1 vs 2: won 100-90, lost 88-99, won 110-100. Hand-checkable on purpose."""
    row = _pair(built, "fid:1", "fid:2")

    assert (row["matchup_meetings"], row["matchup_wins"],
            row["matchup_losses"], row["matchup_ties"]) == (3, 2, 1, 0)
    assert (row["points_for"], row["points_against"],
            row["points_margin"]) == (298.0, 289.0, 9.0)
    assert row["matchup_win_pct"] == pytest.approx(2 / 3)


def test_the_reverse_row_is_the_exact_mirror(built):
    forward = _pair(built, "fid:1", "fid:2")
    reverse = _pair(built, "fid:2", "fid:1")

    assert forward["matchup_meetings"] == reverse["matchup_meetings"]
    assert forward["matchup_wins"] == reverse["matchup_losses"]
    assert forward["points_for"] == reverse["points_against"]
    assert forward["points_margin"] == -reverse["points_margin"]
    assert forward["matchup_win_pct"] + reverse["matchup_win_pct"] == \
        pytest.approx(1.0)


def test_a_tie_is_half_a_win(built):
    row = _pair(built, "fid:5", "fid:6")

    assert row["matchup_win_pct"] == pytest.approx(0.75)


def test_a_bye_is_not_a_meeting(built):
    """Team 4 has a bye in 2021 period 2, plus two real meetings."""
    assert built("""
        select sum(matchup_meetings) from ANALYTICS.mart_franchise_rivalry
        where league_key = ? and row_identity_key = 'fid:4'
    """, [LEAGUE]) == [(2,)]


def test_an_unscored_matchup_does_not_become_a_tie(built):
    """5 vs 8 exists in a CLOSED period with one platform score missing, and the
    fact's else-branch labels both rows 'T'. Trusting `result` alone would write
    a tie into a rivalry record that was never played to a finish."""
    assert _matchup_record(built, "fid:5", "name:Configured Eight") == (0, 0, 0, 0)
    assert _matchup_record(built, "name:Configured Eight", "fid:5") == (0, 0, 0, 0)


# ===========================================================================
# The holding pen
# ===========================================================================
def test_a_season_parked_on_the_pen_leaves_the_ledger(built):
    """7's 2022 is declared unowned by a season-scoped lineage row, so its only
    2022 matchup -- against 3 -- is not 7's to carry."""
    assert _matchup_record(built, "fid:7", "fid:3") == (0, 0, 0, 0)


def test_the_park_removes_the_matchup_from_both_directions(built):
    """The exclusion sits BEFORE the aggregation for this reason: dropping one
    side only would leave 3 with a win nobody lost."""
    assert _matchup_record(built, "fid:3", "fid:7") == (0, 0, 0, 0)


def test_the_parked_season_also_leaves_the_points_ledger(built):
    """A season nobody owned cannot be outscored either. 7 played 2021 and 2022;
    only 2021 is its own, so every rival compares one season with it."""
    assert _season_record(built, "fid:7", "fid:1") == (1, 0, 1, 0)


def test_the_parked_franchise_keeps_its_other_seasons(built):
    """Only 2022 was parked -- a season-scoped row must not rewrite a
    franchise's whole history."""
    assert _matchup_record(built, "fid:7", "name:Configured Eight") == (1, 0, 1, 0)


def test_the_pen_is_never_a_team_in_the_matrix(built):
    assert built("""
        select count(*) from ANALYTICS.mart_franchise_rivalry
        where row_identity_key like ? or opponent_identity_key like ?
    """, [f"%{PEN}%", f"%{PEN}%"]) == [(0,)]


# ===========================================================================
# Season points
# ===========================================================================
def test_a_completed_season_is_one_win_regardless_of_margin(built):
    """1 outscored 2 by 100 in 2021 and by 100 in 2022. Two seasons, two wins --
    the size of the gap buys nothing."""
    assert _season_record(built, "fid:1", "fid:2") == (2, 2, 0, 0)
    assert _season_record(built, "fid:2", "fid:1") == (2, 0, 2, 0)


def test_equal_season_totals_are_a_tie(built):
    """3 and 4 both scored 800 in 2021; 3 outscored 4 in 2022."""
    assert _season_record(built, "fid:3", "fid:4") == (2, 1, 0, 1)
    assert _season_record(built, "fid:4", "fid:3") == (2, 0, 1, 1)


def test_an_in_flight_season_contributes_nothing(built):
    """2023 is captured and unfinished. Its fixture totals are deliberately
    absurd, so a leak would announce itself in every comparison."""
    assert _season_record(built, "fid:1", "fid:2") == (2, 2, 0, 0)
    assert _pair(built, "fid:1", "fid:2")["last_season_compared"] == 2022
    assert built("""
        select count(*) from ANALYTICS.int_franchise_season_points
        where league_key = ? and season_year = 2023 and is_season_complete
    """, [LEAGUE]) == [(0,)]


def test_a_season_with_no_capture_is_complete_by_supersession(built):
    """2021 has no schedule capture, and the league has since played 2022 and
    2023. A season the platform has moved past is finished, so it counts -- and
    the evidence column says WHY, so the two routes stay distinguishable."""
    rows = built("""
        select distinct season_year, is_season_complete, completion_evidence
        from ANALYTICS.int_franchise_season_points
        where league_key = ? order by 1
    """, [LEAGUE])

    assert rows == [(2021, True, "superseded_season"),
                    (2022, True, "schedule_capture"),
                    (2023, False, "schedule_capture")]


def test_a_season_a_team_never_played_counts_for_nobody(built):
    """9 joined in 2022. Its rivals played 2021 without it, and an absent team
    neither outscored anyone nor was outscored -- so 9 vs 1 compares ONE season,
    not two, and 1 gains nothing from 9's absence."""
    assert _season_record(built, "fid:9", "fid:1") == (1, 0, 1, 0)
    assert _season_record(built, "fid:1", "fid:9") == (1, 1, 0, 0)
    assert _pair(built, "fid:9", "fid:1")["first_season_compared"] == 2022


def test_a_pair_that_never_met_still_has_a_season_ledger(built):
    """9 played no matchups at all. The pair exists because the season ledger
    has something to say, and the matchup side reads zero rather than NULL --
    which is what lets a renderer show 0-0 without inventing it."""
    row = _pair(built, "fid:9", "fid:1")

    assert row["matchup_meetings"] == 0
    assert row["first_meeting_season"] is None
    assert row["matchup_win_pct"] is None
    assert row["season_meetings"] == 1


def test_an_identitys_platform_ids_are_summed_before_comparing(built):
    """Bent Spokes is 40 + 41. In 2021 that is 300 + 200 = 500 against 1's 1000;
    comparing the ids separately would have produced two losses per season
    instead of one, and a different total."""
    row = _pair(built, "name:Bent Spokes", "fid:1")

    assert (row["season_meetings"], row["season_losses"]) == (2, 2)
    assert row["season_points_for"] == 500.0 + 520.0


def test_season_points_reciprocate(built):
    forward = _pair(built, "name:Bent Spokes", "fid:1")
    reverse = _pair(built, "fid:1", "name:Bent Spokes")

    assert forward["season_wins"] == reverse["season_losses"]
    assert forward["season_losses"] == reverse["season_wins"]
    assert forward["season_points_for"] == reverse["season_points_against"]
    assert forward["first_season_compared"] == reverse["first_season_compared"]


# ===========================================================================
# Season-scoped configured names
# ===========================================================================
def test_a_season_scoped_configured_name_applies_to_that_season(built):
    """The seed schema has always allowed a name on a season-scoped row, and
    dim_franchise_season used to read only the id from one -- so a league could
    name a single season's team and get no effect and no error. 50 is "Nomad
    Alias" in 2022 and itself either side."""
    rows = built("""
        select season_year, identity_key, identity_source
        from ANALYTICS.dim_franchise_identity
        where league_key = ? and franchise_id = '50' order by season_year
    """, [LEAGUE])

    assert rows == [(2021, "fid:50", "franchise_id"),
                    (2022, "name:Nomad Alias", "configured_name"),
                    (2023, "fid:50", "franchise_id")]


def test_the_season_scoped_name_does_not_rewrite_other_seasons(built):
    """A season-scoped row speaks about ONE franchise-season. 50's 2021 points
    belong to fid:50 and its 2022 points to the alias -- one season each,
    against the same rival, rather than both landing on either."""
    assert _season_record(built, "fid:50", "fid:1") == (1, 0, 1, 0)
    assert _season_record(built, "name:Nomad Alias", "fid:1") == (1, 0, 1, 0)
    assert _pair(built, "fid:50", "fid:1")["first_season_compared"] == 2021
    assert _pair(built, "name:Nomad Alias", "fid:1")["first_season_compared"] \
        == 2022


def test_the_season_scoped_name_reaches_the_display_too(built):
    """Honouring it in the identity but not the label would have the matrix
    call a team one thing while the rest of the almanac called it another --
    for the one season the league went out of its way to name."""
    assert built("""
        select canonical_name, configured_name, has_configured_name
        from ANALYTICS.dim_franchise_season
        where league_key = ? and franchise_id = '50' and season_year = 2022
    """, [LEAGUE]) == [("Nomad Alias", "Nomad Alias", True)]


# ===========================================================================
# Format dispatch
# ===========================================================================
def test_the_format_is_read_from_data_not_platform(built):
    """cbs-fix has delivered period standings and no matchups; the espn-*
    leagues have matchups and no period standings. Nothing here looks at the
    platform half of the league key -- a CBS H2H league and an ESPN points
    league both exist and both would be misfiled by that."""
    rows = built("""
        select league_key, league_format, has_period_standings, has_matchups
        from ANALYTICS.dim_league_format order by league_key
    """)

    assert rows == [
        (POINTS_LEAGUE, "points", True, False),
        (BROKEN_LEAGUE, "h2h", False, True),
        (FINAL_LEAGUE, "h2h", False, True),
        (LEAGUE, "h2h", False, True),
        (OPEN_LEAGUE, "h2h", False, True),
        (OTHER_LEAGUE, "h2h", False, True),
        (TYPE5_LEAGUE, "points", False, False),
    ]


def test_espn_type_five_is_positive_points_evidence(built):
    assert built("""
        select league_format, has_season_points_schedule, has_matchups
        from ANALYTICS.dim_league_format where league_key = ?
    """, [TYPE5_LEAGUE]) == [("points", True, False)]


def test_the_axes_carry_the_format_for_the_renderer(built):
    """One query draws the matrix: which axes, and which ledger means anything
    in this league."""
    assert built("""
        select distinct league_format from ANALYTICS.mart_franchise_rivalry_axes
        where league_key = ?
    """, [POINTS_LEAGUE]) == [("points",)]
    assert built("""
        select distinct league_format from ANALYTICS.mart_franchise_rivalry_axes
        where league_key = ?
    """, [LEAGUE]) == [("h2h",)]


def test_a_points_league_has_a_season_ledger_and_no_matchups(built):
    """The reason format dispatch exists: rendering a matchup grid here would
    be a square of 0-0 that means "this league does not work that way", which
    is not what 0-0 says anywhere else on the tab."""
    row = _pair(built, "fid:1", "fid:2", league=POINTS_LEAGUE)

    assert row["matchup_meetings"] == 0
    assert (row["season_meetings"], row["season_wins"]) == (1, 1)


# ===========================================================================
# Grain and the diagonal
# ===========================================================================
def test_the_grain_is_one_row_per_ordered_pair(built):
    assert built("""
        select count(*) from (
            select league_key, row_identity_key, opponent_identity_key
            from ANALYTICS.mart_franchise_rivalry
            group by 1, 2, 3 having count(*) > 1)
    """) == [(0,)]


def test_the_diagonal_is_absent(built):
    """Absent, not zero: the renderer draws a blank there, and reserves 0-0 for
    two active teams that have genuinely never met."""
    assert built("""
        select count(*) from ANALYTICS.mart_franchise_rivalry
        where row_identity_key = opponent_identity_key
    """) == [(0,)]


def test_every_pair_is_told_from_both_sides(built):
    assert built("""
        select count(*) from ANALYTICS.mart_franchise_rivalry a
        left join ANALYTICS.mart_franchise_rivalry b
          on a.league_key = b.league_key
         and a.row_identity_key = b.opponent_identity_key
         and a.opponent_identity_key = b.row_identity_key
        where b.league_key is null
    """) == [(0,)]


# ===========================================================================
# Active axes
# ===========================================================================
def test_the_axes_are_the_current_seasons_teams(built):
    """Active comes from the latest team capture. 4 folded after 2022 and 13 was
    re-minted as 30, so neither is an axis -- while both keep their history in
    the ledger."""
    axes = {row[0] for row in built(
        "select identity_key from ANALYTICS.mart_franchise_rivalry_axes "
        "where league_key = ?", [LEAGUE])}

    assert "fid:4" not in axes
    assert axes == {"fid:1", "fid:2", "fid:3", "fid:5", "fid:6", "fid:7",
                    "fid:9", "fid:20", "fid:50", "name:Configured Eight",
                    "name:Echo Dynasty", "name:Bent Spokes"}


def test_active_ids_are_deduplicated_onto_one_axis(built):
    """40 and 41 are both live and share one configured name, so they are ONE
    column -- the same collapse their history got. 20 and 21 are the fallback
    route to the same thing."""
    rows = built("""
        select identity_key, active_platform_teams
        from ANALYTICS.mart_franchise_rivalry_axes
        where league_key = ? and active_platform_teams > 1 order by 1
    """, [LEAGUE])

    assert rows == [("fid:20", 2), ("name:Bent Spokes", 2)]


def test_evidence_is_absent_exactly_where_nothing_can_be_proven(built):
    """The signal that stops a matrix claiming results it cannot prove. A
    league has evidence when some season is admissible -- proven finished, or
    carrying a closed period. espn-open never captured anything and espn-broken
    captured something unreadable, so for both, nothing at all is known."""
    rows = built("""
        select league_key, min(has_rivalry_evidence), min(admissible_seasons)
        from ANALYTICS.mart_franchise_rivalry_axes
        group by league_key order by league_key
    """)

    assert rows == [
        (POINTS_LEAGUE, True, 1),
        (BROKEN_LEAGUE, False, 0),
        (FINAL_LEAGUE, True, 1),
        (LEAGUE, True, 3),
        (OPEN_LEAGUE, False, 0),
        (OTHER_LEAGUE, True, 1),
    ]


def test_evidence_is_a_league_property_not_a_pair_property(built):
    """Asking it per pair would make an expansion team's genuine 0-0 look like
    missing evidence. espn-fix has proven seasons, so 9 -- which has played no
    matchups at all -- still sits in a league whose matrix means something."""
    assert built("""
        select distinct has_rivalry_evidence
        from ANALYTICS.mart_franchise_rivalry_axes where league_key = ?
    """, [LEAGUE]) == [(True,)]
    assert _matchup_record(built, "fid:9", "fid:1") == (0, 0, 0, 0)


def test_a_defunct_team_keeps_its_history_without_an_axis(built):
    """Activity applies to the axes, never to the facts. 4 has no column and
    still has every result it earned."""
    assert _matchup_record(built, "fid:4", "fid:3") == (1, 0, 0, 1)
    assert _season_record(built, "fid:4", "fid:1") == (2, 0, 2, 0)


def test_a_reminted_team_keeps_its_old_eras_history_on_its_current_axis(built):
    """The Echo franchise is active as id 30 and its axis carries what id 13
    did."""
    assert built("""
        select count(*) from ANALYTICS.mart_franchise_rivalry_axes
        where league_key = ? and identity_key = 'name:Echo Dynasty'
    """, [LEAGUE]) == [(1,)]
    assert _pair(built, "name:Echo Dynasty", "fid:1")["first_meeting_season"] \
        == 2021


def test_the_axis_order_is_stable_and_total(built):
    orders = built("""
        select sort_order, identity_name
        from ANALYTICS.mart_franchise_rivalry_axes
        where league_key = ? order by sort_order
    """, [LEAGUE])

    assert [o for o, _ in orders] == list(range(1, len(orders) + 1))
    assert [n for _, n in orders] == sorted(n for _, n in orders)


# ===========================================================================
# Cross-league isolation
# ===========================================================================
def test_leagues_do_not_blend(built):
    """espn-other carries the same ids AND the same names."""
    assert _matchup_record(built, "fid:1", "fid:2", league=OTHER_LEAGUE) == \
        (1, 1, 0, 0)
    assert _matchup_record(built, "fid:1", "fid:2") == (3, 2, 1, 0)


def test_a_configured_name_does_not_reach_another_league(built):
    """espn-other has no lineage rows, so its teams are all fallbacks even
    though espn-fix configured names for ids that exist in both."""
    sources = built("""
        select distinct row_identity_source
        from ANALYTICS.mart_franchise_rivalry where league_key = ?
    """, [OTHER_LEAGUE])

    assert sources == [("franchise_id",)]


def test_each_league_resolves_its_own_completeness_and_axes(built):
    """Every league resolves its own horizon and its own evidence. espn-fix's
    latest season is 2023 and unfinished; espn-other's is 2023 and finished;
    the posture leagues sit at 2024. No league's calendar reaches another's --
    which is what stops one league's live season from being read as history
    because a different league has moved past that year."""
    horizons = built("""
        select league_key, max(season_year) filter (where is_season_complete),
               max(season_year)
        from ANALYTICS.int_league_season_closure
        group by league_key order by league_key
    """)

    assert horizons == [
        (POINTS_LEAGUE, 2024, 2024),
        (BROKEN_LEAGUE, None, 2024),   # captured, unreadable -> nothing proven
        (FINAL_LEAGUE, 2024, 2024),    # final ranks close its latest season
        (LEAGUE, 2022, 2023),          # 2023 in flight
        (OPEN_LEAGUE, None, 2024),     # unproven
        (OTHER_LEAGUE, 2023, 2023),
        (TYPE5_LEAGUE, None, 2026),    # live multi-team season
    ]
    assert built("""
        select count(*) from ANALYTICS.mart_franchise_rivalry_axes
        where league_key = ?
    """, [OTHER_LEAGUE]) == [(2,)]
