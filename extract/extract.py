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
# espn-api constant maps: stat IDs, position IDs, pro team IDs.
# ---------------------------------------------------------------------------
# STATS_MAP attribute name has varied across espn-api versions (STATS_MAP,
# STAT_ID_TO_NAME, etc.); discover by scanning for the largest dict on the
# constant module — same pattern as dump_stats_map.py. POSITION/team maps
# fall back to known names with defensive getattr.
def _discover_stats_map():
    candidates = [
        getattr(espn_baseball_constant, attr)
        for attr in dir(espn_baseball_constant)
        if not attr.startswith("_")
    ]
    dicts = [c for c in candidates if isinstance(c, dict) and len(c) > 30]
    return max(dicts, key=len) if dicts else {}


_STAT_ID_TO_NAME = _discover_stats_map()

# Override: ESPN's STATS_MAP maps both stat ID 12 (batter HBP, +1) and
# stat ID 42 (pitcher HBP, -1) to the name "HBP". This silently conflates
# the two in the breakdown VARIANT — a player who both pitched and batted
# on the same day (Ohtani) loses sign on one of the two. We rename ID 42
# to 'HBP_P' at the source, matching the seed's row for that ID.
# Phase 4: prior to this, int_player_daily_stats had a CASE rewriting
# stat_name='HBP' → 'HBP_P' when lineup_slot IN ('SP','RP','P'). That
# patch worked for rostered active pitchers but left two-way days broken
# (ESPN returns both ID 12 and 42 entries for Ohtani; the wrapper sums
# them under one name) and broke entirely for FAs (lineup_slot='FA' has
# no role signal). Fixing at extract decouples the seed from the wrapper's
# collision and makes the int CASE unnecessary.
_STAT_ID_TO_NAME[42] = 'HBP_P'

# One-time collision scan: log any stat IDs whose names collide after the
# override so future espn-api versions don't silently regress this fix.
def _log_stats_map_collisions():
    from collections import defaultdict
    reverse = defaultdict(list)
    for stat_id, name in _STAT_ID_TO_NAME.items():
        reverse[name].append(stat_id)
    collisions = {n: ids for n, ids in reverse.items() if len(ids) > 1}
    if collisions:
        print("[warn] STATS_MAP name collisions after overrides:")
        for name, ids in collisions.items():
            print(f"    {name}: ids={ids}")


_log_stats_map_collisions()

# Position, slot, and pro-team maps for FA row construction. The wrapper
# gives readable strings for rostered players; the kona payload returns
# raw numeric IDs that we need to translate ourselves.
#
# Two distinct espn-api maps with similar names — easy to confuse:
#   DEFAULT_POSITION_MAP (11 entries): primary MLB position IDs (1=SP, 2=C,
#       3=1B, ..., 10=DH). Used for the player's `defaultPositionId` field.
#   POSITION_MAP (38 entries): the broader lineup-slot space (0=C, 1=1B,
#       ..., 13=P, 14=SP, 15=RP, 16=BE, 17=IL, etc.). Used for the
#       `eligibleSlots` array which lists every slot a player is eligible for.
DEFAULT_POSITION_MAP = getattr(espn_baseball_constant, "DEFAULT_POSITION_MAP", {})
LINEUP_SLOT_MAP = getattr(espn_baseball_constant, "POSITION_MAP", {})
PRO_TEAM_MAP = getattr(espn_baseball_constant, "PRO_TEAM_MAP", {})

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
# Stat extraction via raw kona_player_info (Phase 4)
#
# History:
#   Phase 3.3 introduced raw-API stat extraction as a doubleheader override
#     gated on MLB-scoreboard DH detection.
#   Phase 3.3.1 made it the default for all scoring periods (mRoster), since
#     the sum-across-splits aggregation handles single games as N=1 (identity)
#     and DHs as N=2 (sum) under one branch. mRoster covers rostered players
#     only, so FAs were absent.
#   Phase 4 swaps mRoster → kona_player_info, which returns the *full* MLB
#     player universe (rostered + FA) with the identical per-game splits
#     shape. One stat source for everyone, no separate FA pipeline.
#
# FA determination is an anti-join, not a flag: any player with stats in
# kona but absent from the wrapper's box_scores lineup for a given
# scoring_period is, by definition, a free agent on that day. This handles
# mid-season transactions correctly without trusting kona's `status` field
# (which reflects current roster status, not historical).
#
# The wrapper still provides matchup structure (home/away pairing,
# lineup_slot per scoring_period, owners, team_ids) for rostered players.
# Wrapper stats remain a per-player fallback if kona misses someone.
# ---------------------------------------------------------------------------
def fetch_all_player_stats(year, scoring_period):
    """
    Pull per-player stats for a single scoring period from ESPN's
    kona_player_info endpoint. Returns the full MLB universe (rostered +
    FA) — caller distinguishes via anti-join against the wrapper lineup.

    Returns dict[player_id] -> {
        "breakdown":     {stat_name: stat_value, ...}  # summed across splits
        "points":        float                          # summed appliedTotal
        "games_played":  int                            # count of non-empty splits
        "name":          str                            # for FA row construction
        "pro_team":      str                            # MLB team abbreviation
        "default_position_id": int                      # for diagnostics
    }

    Each player carries a stats[] array; filter to (statSplitTypeId == 5
    AND scoringPeriodId == target) for per-period splits. Single games
    yield N=1 (sum is identity); doubleheaders yield N=2 (sum). Sidesteps
    the espn-api wrapper's dict-key collision (Player.__init__ keys
    self.stats by scoringPeriodId, silently overwriting the first DH
    split with the second).

    Limit 1500 with sortPercOwned reliably covers the ~420 players who
    actually accumulate fantasy stats on a typical day (Phase 4 handoff).

    Returns {} on any failure — caller falls back to wrapper data per-player.
    """
    url = f"{ESPN_API_BASE}/{year}/segments/0/leagues/{LEAGUE_ID}"
    fantasy_filter = {
        "players": {
            "limit": 1500,
            "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
        }
    }
    try:
        response = requests.get(
            url,
            params={"view": "kona_player_info", "scoringPeriodId": scoring_period},
            cookies={"swid": SWID, "espn_s2": ESPN_S2},
            headers={"x-fantasy-filter": json.dumps(fantasy_filter)},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as e:
        print(f"    [warn] kona fetch failed for sp={scoring_period}: {e}")
        return {}

    by_player = {}
    for entry in data.get("players", []) or []:
        player = (entry.get("player")) or {}
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
                # Stat-less split (player exists on this date but didn't play).
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
            # eligibleSlots is the multi-position eligibility array (numeric
            # slot ids). Mapped via POSITION_MAP to readable strings, e.g.
            # [2, 12, 13, 15] -> ['2B', 'UTIL', 'P', 'RP']. Useful for the
            # "Top Wasted Points" callout once consumers want to show
            # full eligibility (Sanoja as 2B/RP, Ohtani as SP/DH, etc.).
            # Phase 4 v1 output uses primary position only; this field is
            # plumbed through raw for the v2 enhancement.
            eligible_ids = player.get("eligibleSlots") or []
            eligible_slots = [
                LINEUP_SLOT_MAP.get(sid, str(sid)) for sid in eligible_ids
            ]

            by_player[player_id] = {
                "breakdown": agg_breakdown,
                "points": round(agg_points, 4),
                "games_played": games,
                "name": player.get("fullName"),
                "pro_team": PRO_TEAM_MAP.get(player.get("proTeamId"), "FA"),
                "default_position_id": player.get("defaultPositionId"),
                "eligible_slots": eligible_slots,
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


def serialize_box_scores(league, scoring_period, matchup_period):
    """
    Pull box scores for a single scoring period. Returns a dict with two
    keys: `matchups` (list of fantasy matchup dicts, rostered players only)
    and `free_agents` (list of FA player dicts emitted via anti-join).

    Wrapper provides matchup structure (home/away pairing, lineup_slot per
    scoring_period, owners, team_ids). Stats come from kona_player_info,
    which returns the full MLB universe with all per-game splits — handles
    doubleheaders by default (N=2 splits summed) and single games (N=1,
    sum is identity) under one branch. See section comment above
    fetch_all_player_stats for Phase 3.3 → 3.3.1 → 4 history.

    Both scoring_period AND matchup_period must be passed to the wrapper to
    get historical player-level stats. Passing scoring_period alone returns
    today's stats regardless of which period was requested.

    FA determination is anti-join: any player with stats in kona but absent
    from every wrapper lineup is, by definition, a free agent on that day.
    Mid-season transactions handled correctly without trusting kona's
    `status` field (which reflects current roster status, not historical).

    Wrapper stats remain available as a per-player fallback for the rare
    case where kona misses a rostered player (network blip, edge cases at
    trade deadlines / waiver claims).
    """
    box_scores = league.box_scores(
        matchup_period=matchup_period,
        scoring_period=scoring_period,
    )

    # One kona call covers all ~1500 fantasy-relevant players (rostered + FA)
    # in this scoring period; we look up each wrapper-returned player by
    # playerId and use the kona sum if found.
    all_player_stats = fetch_all_player_stats(league.year, scoring_period)
    rostered_ids = set()    # tracks playerIds that appeared in any wrapper lineup
    raw_count = 0           # rostered + kona had stats
    wrapper_count = 0       # rostered + kona missed but wrapper had stats — recovery
    empty_count = 0         # rostered + neither source had stats (didn't play)

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
            "home_team_abbrev": matchup.home_team.team_abbrev,
            "home_owner": format_owners(home_owners),
            "away_team": matchup.away_team.team_name,
            "away_team_id": matchup.away_team.team_id,
            "away_team_abbrev": matchup.away_team.team_abbrev,
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
                rostered_ids.add(player.playerId)

                # Primary path: kona sums (correct on both single-game and
                # doubleheader days; the wrapper's box_scores() collapses
                # DH splits via dict-key collision and silently drops one).
                # Kona absence for a rostered player just means they didn't
                # play — large majority of fallbacks are legitimate empty
                # rows where wrapper also has empty stats.
                raw = all_player_stats.get(player.playerId)
                if raw is not None:
                    breakdown = raw["breakdown"]
                    points = raw["points"]
                    games_played = raw["games_played"]
                    raw_count += 1
                else:
                    period_stats = player.stats.get(scoring_period, {})
                    breakdown = period_stats.get("breakdown", {}) or {}
                    points = period_stats.get("points", 0)
                    games_played = 1 if breakdown else 0
                    if breakdown:
                        # Genuine recovery: wrapper had stats, kona missed.
                        wrapper_count += 1
                    else:
                        # Didn't play. Both sources empty -- expected.
                        empty_count += 1

                # Phase 5: pull eligibleSlots from the wrapper Player object.
                # Kona's view=kona_player_info returns an empty eligibleSlots
                # array for rostered players (it only populates the field for
                # FAs). The wrapper has it for everyone, so we read from there
                # for rostered. FAs continue to source from kona (anti-join
                # path below) where it IS populated.
                eligible_slots = getattr(player, 'eligibleSlots', []) or []

                player_dict = {
                    "name": player.name,
                    "playerId": player.playerId,
                    "position": player.position,
                    "lineupSlot": player.lineupSlot,
                    "proTeam": player.proTeam,
                    "points": points,
                    "breakdown": breakdown,
                    "games_played": games_played,
                    "eligibleSlots": eligible_slots,
                }
                lineup_list.append(player_dict)
            matchup_dict[f"{side}_lineup"] = lineup_list

        matchups.append(matchup_dict)

    # Anti-join: every kona player NOT in any wrapper lineup is a FA today.
    # Lineup slot literal 'FA' (not in espn-api POSITION_MAP) keeps the
    # downstream slot-bucket logic consistent: BE/IL/FA all flow through
    # int_player_daily and split at the inactive facts via wasted_bucket.
    free_agents = []
    for player_id, raw in all_player_stats.items():
        if player_id in rostered_ids:
            continue
        free_agents.append({
            "name": raw["name"],
            "playerId": player_id,
            "position": DEFAULT_POSITION_MAP.get(raw["default_position_id"], "UNK"),
            "lineupSlot": "FA",
            "proTeam": raw["pro_team"],
            "points": raw["points"],
            "breakdown": raw["breakdown"],
            "games_played": raw["games_played"],
            "eligibleSlots": raw["eligible_slots"],
        })

    rostered_played = raw_count + wrapper_count
    fa_played = len(free_agents)
    total_played = rostered_played + fa_played
    # MLB games ≈ distinct proTeams / 2. Off by 1 per DH-pair (DH team
    # only contributes 1 to the distinct set despite playing 2 games).
    # Acceptable for context; the games_played column on each player row
    # carries exact DH info downstream.
    mlb_games = len({p["pro_team"] for p in all_player_stats.values()}) // 2
    fallback_note = f" | fallbacks: {wrapper_count}" if wrapper_count else ""
    print(f"    played in MLB: {total_played} ({rostered_played} rostered, "
          f"{fa_played} FA) over {mlb_games} games | "
          f"{raw_count} tracked by kona{fallback_note}")

    return {"matchups": matchups, "free_agents": free_agents}


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
                    json.dumps(record["data"]),
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
        sp_data = serialize_box_scores(league, sp, matchup_period)
        records.append({
            "scoring_period": sp,
            "matchup_period": matchup_period,
            "data": sp_data,
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