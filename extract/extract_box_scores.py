"""
Extract ESPN Fantasy Baseball box scores and load raw JSON into Snowflake.

Pulls daily box score data for a given matchup period, serializes each
scoring period's results as JSON, and inserts into ESPN_FANTASY.RAW.BOX_SCORES.
"""

import argparse
import csv
import json
import os
from datetime import date, timedelta

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
# ESPN extraction
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
        # Extract owner name (first and last names for 1 owner teams, list of first names for multi-owner teams)

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


# ---------------------------------------------------------------------------
# Snowflake loading
# ---------------------------------------------------------------------------
def load_to_snowflake(records, matchup_period, year):
    """
    Insert raw box score JSON records into Snowflake.
    Creates the target table if it doesn't exist.
    Deletes existing data for this matchup_period + year before inserting,
    making re-runs fully idempotent.
    """
    conn = snowflake.connector.connect(**SNOWFLAKE_CONFIG)
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
        conn.close()


def extract_matchup_period(league, matchup_period, year):
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

    load_to_snowflake(records, matchup_period, year)


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


if __name__ == "__main__":
    # Usage:
    #   py extract/extract_box_scores.py                        -> recent matchup periods, 2026
    #   py extract/extract_box_scores.py --year 2025            -> recent matchup periods, 2025
    #   py extract/extract_box_scores.py 2                      -> specific period, 2026
    #   py extract/extract_box_scores.py --year 2025 1 2 3      -> specific periods, 2025

    parser = argparse.ArgumentParser(description="Extract ESPN box scores into Snowflake.")
    parser.add_argument("--year", type=int, default=date.today().year, help="Season year (default: current calendar year)")
    parser.add_argument("periods", nargs="*", type=int, help="Specific matchup periods to extract")
    args = parser.parse_args()

    year = args.year

    if args.periods:
        periods = args.periods
        print(f"Extracting specified matchup periods for {year}: {periods}")
    else:
        periods = get_recent_matchup_periods(year)
        if not periods:
            print(f"No completed matchup periods found in the last 21 days for {year}.")
            import sys
            sys.exit(0)
        print(f"Extracting recent matchup periods for {year}: {periods}")

    league = connect_espn(year)

    for mp in periods:
        print(f"\nMatchup period {mp}:")
        extract_matchup_period(league, mp, year)

    print("\nDone.")