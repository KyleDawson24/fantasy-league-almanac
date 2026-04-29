"""
extract.py — ESPN Fantasy Baseball data extraction pipeline.

Handles multiple extraction types from a single entry point:
  1. Box scores: daily player-level stats for each matchup period
  2. Scoring settings: league scoring weights per season (opt-in)

Box scores are extracted by default. Scoring settings require an explicit
flag (--include-settings or --settings-only) because they change rarely
and don't need to run on every weekly pull.

Usage:
  py extract/extract.py                              -> recent box scores, current year
  py extract/extract.py --year 2025                  -> recent box scores, 2025
  py extract/extract.py 5                            -> box scores for matchup period 5
  py extract/extract.py --year 2025 1 2 3            -> box scores for specific periods, 2025
  py extract/extract.py --include-settings           -> recent box scores + scoring settings
  py extract/extract.py --settings-only              -> scoring settings only, no box scores
  py extract/extract.py --settings-only --year 2025  -> scoring settings for 2025 only
"""

import argparse
import csv
import json
import os
from datetime import date, timedelta

import requests
from dotenv import load_dotenv
from espn_api.baseball import League
import snowflake.connector

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
load_dotenv()

ESPN_S2 = os.getenv("ESPN_S2")
SWID = os.getenv("SWID")
LEAGUE_ID = int(os.getenv("LEAGUE_ID"))

SNOWFLAKE_CONFIG = {
    "account": os.getenv("SNOWFLAKE_ACCOUNT"),
    "user": os.getenv("SNOWFLAKE_USER"),
    "password": os.getenv("SNOWFLAKE_PASSWORD"),
    "database": os.getenv("SNOWFLAKE_DATABASE"),
    "schema": os.getenv("SNOWFLAKE_SCHEMA"),
    "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE"),
}

ESPN_API_BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/seasons"

# ---------------------------------------------------------------------------
# Schedule loading
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SEED_PATH = os.path.join(SCRIPT_DIR, "..", "dbt_league", "seeds", "matchup_schedule.csv")


def load_schedule(year):
    """
    Load matchup schedule for a given season year from the dbt seed CSV.
    Returns (season_opener, matchups) where matchups is a list of
    (matchup_period, start_date, end_date) tuples.

    season_opener is derived as the earliest start date for that year,
    rather than being stored separately — one fewer thing to keep in sync.
    """
    matchups = []
    with open(SEED_PATH, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if int(row["season_year"]) != year:
                continue
            matchups.append((
                int(row["matchup_period"]),
                date.fromisoformat(row["start_date"]),
                date.fromisoformat(row["end_date"]),
            ))

    if not matchups:
        raise ValueError(f"No schedule found for season year {year}. "
                         f"Check that {SEED_PATH} contains rows for {year}.")

    season_opener = min(start for _, start, _ in matchups)
    return season_opener, matchups


def date_to_scoring_period(d, season_opener):
    """Convert a calendar date to an ESPN scoring period number."""
    return (d - season_opener).days + 1


def get_scoring_periods(matchup_period, year):
    """Return the list of scoring periods for a given matchup period."""
    season_opener, matchups = load_schedule(year)
    for mp, start, end in matchups:
        if mp == matchup_period:
            num_days = (end - start).days + 1
            first_sp = date_to_scoring_period(start, season_opener)
            return list(range(first_sp, first_sp + num_days))
    raise ValueError(f"Matchup period {matchup_period} not found in {year} schedule.")


# ---------------------------------------------------------------------------
# Snowflake connection
# ---------------------------------------------------------------------------
def get_snowflake_connection():
    """Open a Snowflake connection. Use in a `with` block for automatic cleanup."""
    return snowflake.connector.connect(**SNOWFLAKE_CONFIG)


# ---------------------------------------------------------------------------
# ESPN extraction — box scores
# ---------------------------------------------------------------------------
def connect_espn(year):
    """Authenticate and return the ESPN league object for the given season year."""
    return League(
        league_id=LEAGUE_ID,
        year=year,
        espn_s2=ESPN_S2,
        swid=SWID,
    )


def serialize_box_scores(league, scoring_period, matchup_period):
    """
    Pull box scores for a single scoring period and return a list of
    serialized matchup dicts.

    Both scoring_period AND matchup_period must be passed to the ESPN API
    to get historical player-level stats. Passing scoring_period alone
    returns today's stats regardless of which period was requested.
    """
    box_scores = league.box_scores(
        matchup_period=matchup_period,
        scoring_period=scoring_period,
    )

    def format_owners(owners_list):
        if not owners_list:
            return "Unknown"
        if len(owners_list) == 1:
            o = owners_list[0]
            return f"{o['firstName'].title()} {o['lastName'].title()}"
        return " / ".join(o['firstName'].title() for o in owners_list)

    matchups = []

    for matchup in box_scores:
        home_owners = matchup.home_team.owners
        away_owners = matchup.away_team.owners

        matchup_dict = {
            "home_team": matchup.home_team.team_name,
            "home_team_id": matchup.home_team.team_id,
            "home_owner": format_owners(home_owners),
            "away_team": matchup.away_team.team_name,
            "away_team_id": matchup.away_team.team_id,
            "away_owner": format_owners(away_owners),
            "home_score": matchup.home_score,
            "away_score": matchup.away_score,
            "home_lineup": [],
            "away_lineup": [],
        }

        for side in ["home", "away"]:
            lineup = getattr(matchup, f"{side}_lineup")
            lineup_list = []
            for player in lineup:
                period_stats = player.stats.get(scoring_period, {})
                player_dict = {
                    "name": player.name,
                    "playerId": player.playerId,
                    "position": player.position,
                    "lineupSlot": player.lineupSlot,
                    "proTeam": player.proTeam,
                    "points": period_stats.get("points", 0),
                    "breakdown": period_stats.get("breakdown", {}),
                }
                lineup_list.append(player_dict)
            matchup_dict[f"{side}_lineup"] = lineup_list

        matchups.append(matchup_dict)

    return matchups


def load_box_scores_to_snowflake(conn, records, matchup_period, year):
    """
    Insert raw box score JSON records into Snowflake.
    Creates the target table if it doesn't exist.
    Deletes existing data for this matchup_period + year before inserting,
    making re-runs fully idempotent.
    """
    cursor = conn.cursor()

    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS BOX_SCORES (
                season_year     INTEGER,
                scoring_period  INTEGER,
                matchup_period  INTEGER,
                raw_json        VARIANT,
                loaded_at       TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
            )
        """)

        # Scoped delete: only remove this matchup period for this season year.
        # Without the year filter, re-running 2025 MP1 would wipe 2026 MP1.
        cursor.execute(
            "DELETE FROM BOX_SCORES WHERE matchup_period = %s AND season_year = %s",
            (matchup_period, year)
        )

        for record in records:
            cursor.execute(
                """
                INSERT INTO BOX_SCORES (season_year, scoring_period, matchup_period, raw_json)
                SELECT %s, %s, %s, PARSE_JSON(%s)
                """,
                (
                    year,
                    record["scoring_period"],
                    record["matchup_period"],
                    json.dumps(record["matchups"]),
                ),
            )

        conn.commit()
        print(f"  Loaded {len(records)} scoring periods into Snowflake.")

    finally:
        cursor.close()


def extract_matchup_period(conn, league, matchup_period, year):
    """
    Extract all scoring periods for a matchup period and load to Snowflake.
    """
    scoring_periods = get_scoring_periods(matchup_period, year)

    print(f"  Matchup period {matchup_period} spans {len(scoring_periods)} days "
          f"(scoring periods {scoring_periods[0]}-{scoring_periods[-1]})")

    records = []
    for sp in scoring_periods:
        print(f"  Pulling scoring period {sp}...")
        matchup_data = serialize_box_scores(league, sp, matchup_period)
        records.append({
            "scoring_period": sp,
            "matchup_period": matchup_period,
            "matchups": matchup_data,
        })

    load_box_scores_to_snowflake(conn, records, matchup_period, year)


def get_recent_matchup_periods(year, lookback_days=21):
    """
    Return matchup periods for the given year whose end date falls within
    the last `lookback_days` days (inclusive of today).

    This means:
    - Completed matchup periods are re-extracted (catches scoring adjustments)
    - Very old periods are skipped (no unnecessary API calls)
    - The current in-progress period is included if its end date is within range
    """
    _, matchups = load_schedule(year)
    today = date.today()
    cutoff = today - timedelta(days=lookback_days)

    recent = []
    for mp, start, end in matchups:
        if end >= cutoff and end <= today:
            recent.append(mp)

    return sorted(recent)


# ---------------------------------------------------------------------------
# ESPN extraction — scoring settings
# ---------------------------------------------------------------------------
def fetch_scoring_settings(year):
    """
    Pull scoring settings from ESPN's raw API (not the espn-api wrapper,
    which doesn't expose scoring weights).

    Returns the raw scoringItems array — each item has:
      - statId: ESPN's internal numeric stat ID
      - points: per-unit weight in this league
      - isReverseItem: whether the stat is penalized (e.g., errors)
      - leagueRanking / leagueTotal: league-wide aggregates (not used)

    The raw array is stored as-is in Snowflake. The stat_classification
    seed (with espn_stat_id column) bridges numeric IDs to human-readable
    stat names in the staging layer.
    """
    url = f"{ESPN_API_BASE}/{year}/segments/0/leagues/{LEAGUE_ID}"

    response = requests.get(
        url,
        params={"view": "mSettings"},
        cookies={"swid": SWID, "espn_s2": ESPN_S2},
    )
    response.raise_for_status()

    data = response.json()
    scoring_items = data["settings"]["scoringSettings"]["scoringItems"]

    print(f"  Retrieved {len(scoring_items)} scoring items for {year}")
    return scoring_items


def load_scoring_settings_to_snowflake(conn, scoring_items, year):
    """
    Append scoring settings as a new row in RAW.SCORING_SETTINGS.

    Uses append-only pattern (not delete+insert) so historical snapshots
    are preserved. The staging model picks the latest row per season via
    ROW_NUMBER() OVER (PARTITION BY season_year ORDER BY extracted_at DESC).

    This follows the ELT principle: extraction captures everything,
    transformation decides which version to use.
    """
    cursor = conn.cursor()

    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS SCORING_SETTINGS (
                season_year     INTEGER,
                raw_json        VARIANT,
                extracted_at    TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
            )
        """)

        cursor.execute(
            """
            INSERT INTO SCORING_SETTINGS (season_year, raw_json)
            SELECT %s, PARSE_JSON(%s)
            """,
            (year, json.dumps(scoring_items)),
        )

        conn.commit()
        print(f"  Loaded scoring settings for {year} into Snowflake.")

    finally:
        cursor.close()


def extract_scoring_settings(conn, year):
    """Pull scoring settings from ESPN and load to Snowflake."""
    print(f"\nScoring settings for {year}:")
    scoring_items = fetch_scoring_settings(year)
    load_scoring_settings_to_snowflake(conn, scoring_items, year)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Extract ESPN Fantasy Baseball data into Snowflake.",
        epilog=(
            "By default, extracts recent box scores only. Use --include-settings "
            "to also pull scoring settings, or --settings-only to pull just settings."
        ),
    )
    parser.add_argument(
        "--year", type=int, default=date.today().year,
        help="Season year (default: current calendar year)",
    )
    parser.add_argument(
        "periods", nargs="*", type=int,
        help="Specific matchup periods to extract (default: auto-detect recent)",
    )
    parser.add_argument(
        "--include-settings", action="store_true",
        help="Also extract scoring settings for the season",
    )
    parser.add_argument(
        "--settings-only", action="store_true",
        help="Extract scoring settings only (skip box scores)",
    )
    args = parser.parse_args()

    year = args.year

    # Determine what to extract
    do_box_scores = not args.settings_only
    do_settings = args.settings_only or args.include_settings

    with get_snowflake_connection() as conn:

        # --- Scoring settings ---
        if do_settings:
            extract_scoring_settings(conn, year)

        # --- Box scores ---
        if do_box_scores:
            if args.periods:
                periods = args.periods
                print(f"\nExtracting specified matchup periods for {year}: {periods}")
            else:
                periods = get_recent_matchup_periods(year)
                if not periods:
                    print(f"\nNo completed matchup periods found in the last 21 days for {year}.")
                    if not do_settings:
                        # Only exit if we didn't already do something useful
                        import sys
                        sys.exit(0)
                    else:
                        print("Done.")
                        import sys
                        sys.exit(0)
                print(f"\nExtracting recent matchup periods for {year}: {periods}")

            league = connect_espn(year)

            for mp in periods:
                print(f"\nMatchup period {mp}:")
                extract_matchup_period(conn, league, mp, year)

    print("\nDone.")