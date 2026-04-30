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
  py extract/extract.py --year 2026 --all            -> all COMPLETED matchup periods for 2026 (full backfill)
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
from espn_api.baseball import League, constant as espn_baseball_constant
import snowflake.connector


# ---------------------------------------------------------------------------
# espn-api STATS_MAP discovery (numeric stat ID -> human-readable name)
# ---------------------------------------------------------------------------
# The dict's attribute name has varied across espn-api versions (STATS_MAP,
# STAT_ID_TO_NAME, etc.), so we discover it by scanning for the largest dict
# on the constant module — the same pattern used in dump_stats_map.py.
def _discover_stats_map():
    candidates = [
        getattr(espn_baseball_constant, attr)
        for attr in dir(espn_baseball_constant)
        if not attr.startswith("_")
    ]
    dicts = [c for c in candidates if isinstance(c, dict) and len(c) > 30]
    return max(dicts, key=len) if dicts else {}


_STAT_ID_TO_NAME = _discover_stats_map()

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
MLB_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"

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


def scoring_period_to_date(sp, season_opener):
    """Convert an ESPN scoring period number back to a calendar date."""
    return season_opener + timedelta(days=sp - 1)


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
# Doubleheader handling (Phase 3.3)
#
# The espn-api wrapper's box_scores() builds a dict keyed by scoringPeriodId.
# When ESPN returns multiple stat splits for the same period (one per game on
# a doubleheader day), the second silently overwrites the first — only one
# game's stats survive. This costs us roughly 3-5 fpts per affected hitter
# every time a team plays a DH (~10-15 times per team per season).
#
# Fix: detect doubleheader days via ESPN's public MLB scoreboard, then for
# affected pro_teams pull stats directly from the raw mRoster endpoint
# (which preserves all per-game splits) and sum them. Non-DH days continue
# to use the wrapper as before — zero added latency.
# ---------------------------------------------------------------------------
def get_doubleheader_pro_teams(d):
    """
    Return the set of MLB pro_team abbreviations playing a doubleheader on date d.

    Hits ESPN's public MLB scoreboard. The endpoint returns one event per
    team-pair even on DH days, but flags doubleheader games via
    events[].notes[].headline = "Doubleheader - Game N" (also surfaces at
    events[].competitions[].notes[]). Any team appearing in such an event
    has a DH that day.

    Returns uppercase abbreviations (e.g., {"MIL", "KC"}). Returns empty set
    on any failure — caller falls back to wrapper-only extraction.
    """
    date_str = d.strftime("%Y%m%d")
    try:
        response = requests.get(
            MLB_SCOREBOARD_URL, params={"dates": date_str}, timeout=10
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as e:
        print(f"    [warn] MLB scoreboard fetch failed for {d}: {e}")
        return set()

    dh_teams = set()
    for event in data.get("events", []) or []:
        # Notes may sit at event level or inside competitions[]; check both.
        notes = list(event.get("notes", []) or [])
        for comp in event.get("competitions", []) or []:
            notes.extend(comp.get("notes", []) or [])
        is_dh = any(
            "Doubleheader" in (n.get("headline") or "") for n in notes
        )
        if not is_dh:
            continue
        for comp in event.get("competitions", []) or []:
            for team in comp.get("competitors", []) or []:
                abbrev = (team.get("team") or {}).get("abbreviation")
                if abbrev:
                    dh_teams.add(abbrev.upper())
    return dh_teams


def fetch_raw_player_stats(year, scoring_period):
    """
    Pull per-player stats for a single scoring period directly from ESPN's
    mRoster endpoint, bypassing the espn-api wrapper.

    Scope: ROSTERED players only. mRoster returns the 14 fantasy teams'
    rosters — FAs are absent by definition. When Phase 4 (wasted points)
    needs unrostered-MLB stats, it will need a separate fetch path
    (`view=kona_player_info` or wrapping `league.free_agents()`) that
    almost certainly has the same DH overwrite bug and will need the same
    sum-across-splits treatment. The DH detection helper above is generic
    and can be reused as-is.

    Returns dict[player_id] -> {
        "breakdown":     {stat_name: stat_value, ...}  # summed across DH games
        "points":        float                          # summed appliedTotal
        "games_played":  int                            # count of non-empty splits
    }

    Each rostered player carries a stats[] array with multiple splits; we
    filter to (statSplitTypeId == 5 AND scoringPeriodId == target) which is
    the per-period split. On DH days that filter yields 2 entries per player
    who appeared in both games; we sum the stats and appliedTotals.

    Returns {} on any failure — caller falls back to wrapper data.
    """
    url = f"{ESPN_API_BASE}/{year}/segments/0/leagues/{LEAGUE_ID}"
    try:
        response = requests.get(
            url,
            params={"view": "mRoster", "scoringPeriodId": scoring_period},
            cookies={"swid": SWID, "espn_s2": ESPN_S2},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as e:
        print(f"    [warn] mRoster fetch failed for sp={scoring_period}: {e}")
        return {}

    by_player = {}
    for team in data.get("teams", []) or []:
        roster = team.get("roster") or {}
        for entry in roster.get("entries", []) or []:
            player = ((entry.get("playerPoolEntry") or {}).get("player")) or {}
            player_id = player.get("id")
            if player_id is None:
                continue

            agg_breakdown = {}
            agg_points = 0.0
            games = 0
            for split in player.get("stats", []) or []:
                if split.get("statSplitTypeId") != 5:
                    continue
                if split.get("scoringPeriodId") != scoring_period:
                    continue
                raw_stats = split.get("stats") or {}
                if not raw_stats:
                    # Stat-less split (player on roster but didn't play this game).
                    continue
                for stat_id_str, val in raw_stats.items():
                    if val is None:
                        continue
                    try:
                        stat_id = int(stat_id_str)
                    except (TypeError, ValueError):
                        continue
                    name = _STAT_ID_TO_NAME.get(stat_id, str(stat_id))
                    agg_breakdown[name] = agg_breakdown.get(name, 0) + val
                applied_total = split.get("appliedTotal")
                if applied_total is not None:
                    agg_points += applied_total
                games += 1

            if games > 0:
                by_player[player_id] = {
                    "breakdown": agg_breakdown,
                    "points": round(agg_points, 4),
                    "games_played": games,
                }
    return by_player


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


def serialize_box_scores(league, scoring_period, matchup_period, season_opener):
    """
    Pull box scores for a single scoring period and return a list of
    serialized matchup dicts.

    Both scoring_period AND matchup_period must be passed to the ESPN API
    to get historical player-level stats. Passing scoring_period alone
    returns today's stats regardless of which period was requested.

    Phase 3.3 — doubleheader handling: before serializing, identify any
    pro_teams playing a DH on this calendar date. For players on those
    teams, replace the wrapper-derived breakdown/points with mRoster-derived
    sums (which include both games' stats). Players on non-DH teams use the
    wrapper data unchanged. games_played is recorded per-player on every row.
    """
    box_scores = league.box_scores(
        matchup_period=matchup_period,
        scoring_period=scoring_period,
    )

    # --- Doubleheader override prep ---------------------------------------
    # Resolve scoring period to its calendar date, then ask the public MLB
    # scoreboard which teams are playing a DH that day. If any, pull raw
    # mRoster stats once for this period (one API call covering all rosters).
    # The mRoster response includes every rostered player league-wide, but
    # we only override players whose proTeam is in dh_pro_teams — log count
    # tallied below reflects actual overrides applied, not response size.
    sp_date = scoring_period_to_date(scoring_period, season_opener)
    dh_pro_teams = get_doubleheader_pro_teams(sp_date)
    raw_player_stats = (
        fetch_raw_player_stats(league.year, scoring_period) if dh_pro_teams else {}
    )
    override_count = 0

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
                wrapper_breakdown = period_stats.get("breakdown", {}) or {}
                wrapper_points = period_stats.get("points", 0)

                # Default games_played from wrapper data: 1 if the player
                # has a non-empty breakdown (they appeared), else 0.
                breakdown = wrapper_breakdown
                points = wrapper_points
                games_played = 1 if wrapper_breakdown else 0

                # Override with raw mRoster sums for DH-affected players.
                pro_team_norm = (player.proTeam or "").upper()
                if pro_team_norm in dh_pro_teams:
                    raw = raw_player_stats.get(player.playerId)
                    if raw is not None:
                        breakdown = raw["breakdown"]
                        points = raw["points"]
                        games_played = raw["games_played"]
                        override_count += 1

                player_dict = {
                    "name": player.name,
                    "playerId": player.playerId,
                    "position": player.position,
                    "lineupSlot": player.lineupSlot,
                    "proTeam": player.proTeam,
                    "points": points,
                    "breakdown": breakdown,
                    "games_played": games_played,
                }
                lineup_list.append(player_dict)
            matchup_dict[f"{side}_lineup"] = lineup_list

        matchups.append(matchup_dict)

    if dh_pro_teams:
        print(f"    Doubleheader on {sp_date} for {sorted(dh_pro_teams)}: "
              f"applied raw mRoster override to {override_count} players.")

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
    season_opener, _ = load_schedule(year)
    scoring_periods = get_scoring_periods(matchup_period, year)

    print(f"  Matchup period {matchup_period} spans {len(scoring_periods)} days "
          f"(scoring periods {scoring_periods[0]}-{scoring_periods[-1]})")

    records = []
    for sp in scoring_periods:
        print(f"  Pulling scoring period {sp}...")
        matchup_data = serialize_box_scores(league, sp, matchup_period, season_opener)
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
        "--all", action="store_true",
        help="Extract all COMPLETED matchup periods for the year (end_date on or before "
             "today; full backfill). Overrides positional periods and the recent-only "
             "default. In-progress and future periods are skipped — the API has no "
             "stable data for them yet.",
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
            if args.all:
                _, all_matchups = load_schedule(year)
                today = date.today()
                periods = sorted(mp for mp, _, end in all_matchups if end <= today)
                print(f"\nExtracting all completed matchup periods for {year}: {periods}")
            elif args.periods:
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