"""
extract.py — ESPN Fantasy Baseball data extraction pipeline.

Handles multiple extraction types from a single entry point:
  1. Box scores: daily player-level stats for each matchup period
  2. League settings: scoring weights + roster settings per season (opt-in)

Box scores are extracted by default. League settings require an explicit
flag (--include-settings or --settings-only) because they change rarely
and don't need to run on every weekly pull.

Usage:
  py extract/extract.py                              -> recent box scores, current year
  py extract/extract.py --year 2025                  -> recent box scores, 2025
  py extract/extract.py 5                            -> box scores for matchup period 5
  py extract/extract.py --year 2025 1 2 3            -> box scores for specific periods, 2025
  py extract/extract.py --year 2026 --all            -> all COMPLETED matchup periods for 2026 (full backfill)
  py extract/extract.py --include-settings           -> recent box scores + league settings
  py extract/extract.py --settings-only              -> league settings only, no box scores
  py extract/extract.py --settings-only --year 2025  -> league settings for 2025 only
  py extract/extract.py --year 2025 --all --backfill-club-of-game
                                                     -> add club-of-game to 2025 in place
  py extract/extract.py --raw-target local            -> write RAW as parquet, no warehouse

WHERE RAW LANDS (MLB-208). `--raw-target snowflake` is the default and is
unchanged. `--raw-target local` writes the parquet + _manifest.json artifacts
that tools/load_parquet_to_duckdb.py already consumes, so a fresh clone can
go league-id -> extract -> DuckDB -> render without a warehouse account
existing anywhere. EXTRACT_RAW_TARGET sets it by environment; the flag wins.
Both targets share the schedule, the settle window, the refusals and the
league_key stamp -- only the write differs. See extract/raw_sink.py.

Re-extracting a matchup period that has already been loaded and has settled
(ended more than LIVE_CAPTURE_WINDOW_DAYS ago) replaces its rows with a
thinner answer than the one already stored — club-of-game labels ESPN will
not serve again, and free agents kona has aged out — and is refused; see
`settled_loaded_periods` (MLB-188). To put a new field on settled periods,
use --backfill-club-of-game, which updates in place.
"""

import argparse
import csv
import json
import os
import sys
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv
from espn_api.baseball import League, constant as espn_baseball_constant
import snowflake.connector

# League registry (MLB-57): repo root on sys.path so the shared
# config/ namespace package resolves when this runs as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config.league_registry import LeagueRegistryError, get_league, league_keys

# Sibling import by bare name, which is the house pattern (tests/conftest.py
# puts output/ on the path for exactly this reason). NOT `from extract.raw_sink
# import ...`: this file is `extract/extract.py`, so when it runs as a script
# the name `extract` resolves to THIS MODULE FILE -- a regular module beats a
# namespace-package directory anywhere on the path -- and the dotted form dies
# with "'extract' is not a package" while re-executing this file. The explicit
# insert makes the bare name work when imported as `extract.extract` too.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from raw_sink import LocalParquetSink


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
# The live-capture window (MLB-188)
# ---------------------------------------------------------------------------
# How far back the default weekly run reaches, and — the same number, on
# purpose — how long a loaded matchup period stays eligible for a rewrite.
#
# Inside this window a rewrite is the point: it is how a period acquires its
# club-of-game labels and its free-agent rows at all, and it catches scoring
# adjustments. Outside it, the same rewrite is a downgrade — kona answers
# about a long-settled period out of TODAY's player universe, so players who
# have since aged out of it come back unlabelled or not at all, and the
# stored rows were the only copy. See the guard on `settled_loaded_periods`
# for the full account of what is protected.
#
# Both readers take the number from here. A second hardcoded 21 that drifts
# from this one would silently widen or narrow the guard (MLB-175's scar:
# the twin that was right until it wasn't).
LIVE_CAPTURE_WINDOW_DAYS = 21

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
    "database": os.getenv("SNOWFLAKE_DATABASE"),
    "schema": os.getenv("SNOWFLAKE_SCHEMA"),
    "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE"),
}

# Auth: prefer key-pair when SNOWFLAKE_PRIVATE_KEY_PATH is set
# (required for MFA-enforced accounts; the connector can't satisfy an
# interactive MFA prompt). Falls back to password otherwise. Mirrors
# output/db.py::_build_config; keep the two in sync. See SETUP.md §4.
_private_key_path = os.getenv("SNOWFLAKE_PRIVATE_KEY_PATH")
if _private_key_path:
    SNOWFLAKE_CONFIG["private_key_file"] = _private_key_path
    _passphrase = os.getenv("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE")
    if _passphrase:
        SNOWFLAKE_CONFIG["private_key_file_pwd"] = _passphrase
else:
    SNOWFLAKE_CONFIG["password"] = os.getenv("SNOWFLAKE_PASSWORD")

ESPN_API_BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/seasons"

# ---------------------------------------------------------------------------
# Schedule loading
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# The schedule is USER CONFIG, so it lives in league_config/ -- not seeds/,
# which is reference vocabulary that is the same for every league (MLB-114's
# three-directory rule). This constant still named seeds/ after the file
# moved, so `load_schedule` raised FileNotFoundError and EVERY box-score path
# was dead: the default weekly run, --all, explicit periods and
# --backfill-club-of-game alike. Found while tracing MLB-208's acceptance,
# and it predates that work (verified against 05c3cbb).
#
# Resolved the same way dbt resolves it -- dbt_project.yml has
# `seed-paths: ["seeds", "{{ env_var('DBT_LEAGUE_CONFIG', 'league_config') }}"]`
# -- so pointing the extract at the demo fixture works exactly like pointing
# dbt at it, with one variable and no second convention to remember.
LEAGUE_CONFIG_DIR = os.getenv("DBT_LEAGUE_CONFIG", "league_config")
SEED_PATH = os.path.join(SCRIPT_DIR, "..", "dbt_league",
                         LEAGUE_CONFIG_DIR, "matchup_schedule.csv")


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
class KonaUnavailable(RuntimeError):
    """The kona endpoint did not give a usable answer (MLB-199).

    Distinct from a valid empty response, and the distinction is the whole
    point. This used to be encoded as `{}` -- the same value a genuine
    no-games day produces -- so every caller read "the fetch failed" and
    "nobody played" as the same thing. On the backfill path that meant a
    network blip rewrote a scoring period's stored `clubOfGame` to null for
    every player and committed it; a wholesale outage during a run could
    erase the requested range's enrichment. Failure now has its own type,
    and no caller writes when it is raised.
    """


def fetch_all_player_stats(year, scoring_period):
    """
    Pull per-player stats for a single scoring period from ESPN's
    kona_player_info endpoint. Returns the full MLB universe (rostered +
    FA) — caller distinguishes via anti-join against the wrapper lineup.

    Raises KonaUnavailable if the endpoint could not be read or answered
    with something that is not a player payload. Returns a dict -- possibly
    empty -- only when ESPN genuinely said so.

    Returns dict[player_id] -> {
        "breakdown":     {stat_name: stat_value, ...}  # summed across splits
        "points":        float                          # summed appliedTotal
        "games_played":  int                            # count of non-empty splits
        "name":          str                            # for FA row construction
        "pro_team":      str                            # MLB team abbreviation
        "club_of_game":  str | None                     # club he played FOR
        "default_position_id": int                      # for diagnostics
    }

    `pro_team` and `club_of_game` answer different questions and disagree
    on any player who changed clubs. `pro_team` is ESPN's person record —
    the club he belongs to at the moment of the fetch, so it decays as the
    fetch date moves away from the period. `club_of_game` comes off the
    period's own splits and does not decay. None means Unattributed.

    Each player carries a stats[] array; filter to (statSplitTypeId == 5
    AND scoringPeriodId == target) for per-period splits. Single games
    yield N=1 (sum is identity); doubleheaders yield N=2 (sum). Sidesteps
    the espn-api wrapper's dict-key collision (Player.__init__ keys
    self.stats by scoringPeriodId, silently overwriting the first DH
    split with the second).

    Limit 1500 with sortPercOwned reliably covers the ~420 players who
    actually accumulate fantasy stats on a typical day (Phase 4 handoff).
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
        raise KonaUnavailable(
            f"kona fetch failed for sp={scoring_period}: {e}"
        ) from e

    # A 200 carrying something that is not a player payload is a failure
    # wearing a success's clothes -- an auth wall or an error document
    # parses as JSON perfectly well. Absent `players` means unavailable;
    # `players: []` means ESPN said nobody played, which is an answer.
    if not isinstance(data, dict) or "players" not in data:
        raise KonaUnavailable(
            f"kona returned no players key for sp={scoring_period} "
            f"(got {type(data).__name__} with keys "
            f"{sorted(data)[:6] if isinstance(data, dict) else 'n/a'})"
        )

    by_player = {}
    for entry in data.get("players", []) or []:
        player = (entry.get("player")) or {}
        player_id = player.get("id")
        if player_id is None:
            continue

        agg_breakdown = {}
        agg_points = 0.0
        games = 0
        # Insertion order is ESPN's payload order — _resolve_club_of_game
        # depends on that to break ties the same way every run.
        club_splits = {}
        for split in player.get("stats", []) or []:
            if split.get("statSplitTypeId") != 5:
                continue
            if split.get("scoringPeriodId") != scoring_period:
                continue
            raw_stats = split.get("stats") or {}
            if not raw_stats:
                # Stat-less split (player exists on this date but didn't play).
                continue

            # MLB-129/MLB-159: proTeamId on the SPLIT is the club the player
            # played that game for. It is period-accurate and stays accurate
            # no matter when the extract runs, which is what makes it
            # different in kind from `pro_team` below. Loop already had it in
            # hand and stepped over it; 22.25% of 2025's active-slot weight
            # is misfiled as a result.
            split_pro_team_id = split.get("proTeamId")
            if split_pro_team_id:
                club = PRO_TEAM_MAP.get(split_pro_team_id, str(split_pro_team_id))
                club_splits[club] = club_splits.get(club, 0) + 1
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
                "club_of_game": _resolve_club_of_game(club_splits),
                "default_position_id": player.get("defaultPositionId"),
                "eligible_slots": eligible_slots,
            }
    return by_player


def _resolve_club_of_game(club_splits):
    """
    Collapse one scoring period's per-game club stamps to a single club.

    `club_splits` maps club abbreviation -> number of that period's splits
    played for it, in ESPN's payload order. Almost always one entry; two
    only when a player changed clubs on a day he played for both, which
    the MLB-129 spike measured at 24 of 332,003 weight units (0.007%).

    Most splits wins. Ties go to the club that appears first in the
    payload — written as a strict `>` over an insertion-ordered dict so
    the tie-break is a stated rule rather than an artifact of whichever
    key `max()` happens to reach first (MLB-128's lesson about ties that
    are only stable by luck). This reproduces the spike's rule exactly,
    which is what lets the Day-2 cross-check compare the two.

    Returns None when no split carried a club. None means Unattributed —
    club unknown — which is deliberately NOT the string 'FA'. MLB-159
    reserves those as separate words and they must not merge.
    """
    best_club, best_splits = None, 0
    for club, splits in club_splits.items():
        if splits > best_splits:
            best_club, best_splits = club, splits
    return best_club


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


def unmapped_lineup_slot_error(exc, matchup_period, scoring_period):
    """Turn espn-api's bare KeyError into something a stranger can act on.

    `BoxPlayer.__init__` does `POSITION_MAP[data['lineupSlotId']]` with no
    default, so a league using a slot id the wrapper does not map raises a
    KeyError carrying nothing but the number, and the extract dies with no
    indication of what went wrong (MLB-222 C-6). The wrapper's own constant
    file admits the gap in a comment: ids 18, 21 and 22 "have appeared but
    unknown what position they correspond to".

    This FAILS rather than coercing the slot to a plausible default, and
    that is deliberate. An unrecognised slot classified as hitting counts a
    benched or minors player as active starter production; classified as
    pitching it deletes his stat line outright. That two-sided damage is
    exactly what MLB-222 F-1 is about, and a wrong number that looks right
    is worse than a run that stops.

    Note `Player.lineupSlot` uses `.get(..., '')` for the same lookup, which
    is where the empty-string slot F-1 has to classify comes from -- the two
    call sites in the same dependency disagree.
    """
    key = exc.args[0] if exc.args else None
    known = sorted(k for k in LINEUP_SLOT_MAP if isinstance(k, int))
    if isinstance(key, int) and key not in LINEUP_SLOT_MAP:
        detail = (f"ESPN returned lineup slot id {key}, which the installed "
                  f"espn-api does not map. Ids it knows: {known}.")
    else:
        detail = (f"espn-api raised KeyError({key!r}) while building box "
                  f"scores.")
    return RuntimeError(
        f"{detail} Stopped at matchup period {matchup_period}, scoring "
        f"period {scoring_period} rather than guess: an unrecognised slot "
        f"is silently counted as active hitting production downstream, so "
        f"guessing would corrupt the totals instead of failing. Report the "
        f"id upstream (espn_api.baseball.constant.POSITION_MAP), or pin an "
        f"espn-api release that maps it, then re-run."
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
    # The guard sits HERE, at the call site, and not in the dependency:
    # espn_api is a pinned requirement, not vendored, so a patch to
    # site-packages would vanish on the next venv rebuild from
    # requirements.txt. See unmapped_lineup_slot_error (MLB-222 C-6).
    try:
        box_scores = league.box_scores(
            matchup_period=matchup_period,
            scoring_period=scoring_period,
        )
    except KeyError as exc:
        raise unmapped_lineup_slot_error(
            exc, matchup_period, scoring_period) from exc

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

    def side_identity(team):
        """Identify one side of a matchup, which may be a BYE.

        In an odd-numbered league a team's opponent does not exist, and the
        wrapper says so by leaving that side as the int `0`:
        `H2HPointsBoxScore._get_team_data` returns `(0, 0, -1, [])` for an
        absent side, and `League.box_scores()` only swaps an int for a
        `Team` when some team's `team_id` equals it -- no team is numbered
        0, so the int survives. Reading `.owners` off it took the WHOLE
        extract down, not one row (MLB-222 C-1).

        The bye is represented rather than dropped, because it is a real
        fact about the week and the team ON the bye still played a full
        slate that has to reach the marts. `team_id` stays NULL: a sentinel
        integer would collide the day a league really does number a team 0,
        and NULL is what the downstream left joins are shaped to expect.
        """
        if not hasattr(team, "team_id"):
            return {"team_name": "BYE", "team_id": None,
                    "team_abbrev": "BYE", "owner": "BYE"}, True
        return {
            "team_name": team.team_name,
            "team_id": team.team_id,
            "team_abbrev": team.team_abbrev,
            "owner": format_owners(team.owners),
        }, False

    matchups = []

    for matchup in box_scores:
        home, home_is_bye = side_identity(matchup.home_team)
        away, away_is_bye = side_identity(matchup.away_team)

        matchup_dict = {
            "home_team": home["team_name"],
            "home_team_id": home["team_id"],
            "home_team_abbrev": home["team_abbrev"],
            "home_owner": home["owner"],
            "away_team": away["team_name"],
            "away_team_id": away["team_id"],
            "away_team_abbrev": away["team_abbrev"],
            "away_owner": away["owner"],
            "home_score": matchup.home_score,
            "away_score": matchup.away_score,
            "is_bye": home_is_bye or away_is_bye,
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
                # Club-of-game lives on kona's splits, so the wrapper
                # fallback below cannot supply it. Those rows stay
                # Unattributed rather than borrowing the person-level club,
                # which would reintroduce the exact defect this field fixes.
                club_of_game = raw["club_of_game"] if raw is not None else None
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
                    "clubOfGame": club_of_game,
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
            "clubOfGame": raw["club_of_game"],
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


def ensure_league_key_column(cursor, table):
    """Idempotent schema self-heal (MLB-57): every RAW table carries a
    league_key column. Pre-registry installs created these tables without
    it; ADD COLUMN IF NOT EXISTS upgrades them in place on the next run.
    Legacy rows are NULL until tools/migrate_raw_league_key.py stamps
    them (all pre-registry rows are by definition the default ESPN
    league's). The payload columns stay verbatim -- league_key is load
    metadata, not a payload mutation."""
    cursor.execute(
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS league_key VARCHAR"
    )


def _table_exists(cursor, table):
    """Whether `table` exists in the connection's schema, asked of the catalog.

    Deliberately not inferred from an exception. Snowflake reports a missing
    object and an object you may not read with the SAME error -- "Object 'X'
    does not exist or not authorized" -- because telling the two apart would
    itself leak information. So the exception cannot answer this question,
    and a guard that reads it as "absent" waves through the permission case.
    """
    cursor.execute(
        """
        SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = CURRENT_SCHEMA() AND TABLE_NAME = %s
        """,
        (table,),
    )
    return cursor.fetchone()[0] > 0


def _table_has_column(cursor, table, column):
    cursor.execute(
        """
        SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = CURRENT_SCHEMA()
          AND TABLE_NAME = %s AND COLUMN_NAME = %s
        """,
        (table, column),
    )
    return cursor.fetchone()[0] > 0


def refuse_unverifiable_guard(exc, year):
    """Build the refusal for a guard that could not be evaluated (MLB-199).

    The guard protects settled history from being deleted and re-extracted.
    If it cannot run, the safe reading is not "nothing is loaded" -- it is
    "we do not know what is loaded", and the extract must not proceed.
    """
    return "\n".join([
        "",
        "=" * 72,
        f"REFUSING TO EXTRACT -- cannot verify settled history for {year}",
        "=" * 72,
        "",
        f"  {type(exc).__name__}: {exc}",
        "",
        "  The settled-history guard reads BOX_SCORES to find periods a",
        "  re-extract would overwrite. That read failed, so the guard cannot",
        "  say whether this run would destroy day-accurate history.",
        "",
        "  This used to be treated as 'no table yet, nothing to protect',",
        "  which meant a permission error, a renamed column or any other",
        "  compilation failure bypassed the guard immediately before the",
        "  loader deleted settled rows (MLB-199).",
        "",
        "  Fix the access or schema problem above and re-run. If you have",
        "  confirmed there is genuinely nothing to lose, the snapshot route",
        "  in the MLB-188 refusal still applies.",
        "",
        "=" * 72,
        "",
    ])


def settled_loaded_periods(sink, year, league_key, periods, today=None):
    """
    MLB-188 guard. Of `periods`, return the ones a re-extract would damage:
    already loaded, and ended longer than LIVE_CAPTURE_WINDOW_DAYS ago.

    Returns [(matchup_period, end_date, last_loaded_at)] sorted by period.
    Empty means the requested set is safe to extract.

    WHAT IS PROTECTED. Settled rows as a class, because a re-extract is
    delete-then-insert and the answer it inserts is thinner than the one it
    drops. Concretely, and each of these is measured:

      * `clubOfGame` on rostered rows — the club of the GAME a player-day's
        production came from, and what the shipped affinity chart reads in
        both books. It is read out of kona's per-scoring-period splits, so
        it can only be captured while kona still returns that period's
        players: 476 player-days across 60 players (2026) already have no
        placeable club because ESPN stopped returning them (MLB-193). The
        gap between a period being lived and being asked about IS the loss
        function, and it only grows.
      * the free-agent rows kona has aged out. Kona is the only source of
        free-agent production; a player who has dropped out of today's
        window does not come back as an unlabelled row, he comes back as no
        row at all.
      * and the fact that 2025 is the demonstration, not the hypothesis:
        all 195 of its rows were written in one ten-minute pass, and it
        shows in the product — 0 of 1,236 player-seasons multi-club in
        2025 against 66 of 1,208 in 2026.

    The asymmetry with `--backfill-club-of-game` is the whole argument. The
    backfill keeps stored evidence when today's fetch cannot confirm it
    ("absence of evidence is not evidence of absence"); the re-extract's
    DELETE cannot, because by the time the INSERT runs the evidence is gone.

    Two ways a period is NOT damageable, and both matter:

      * it has no rows yet — a first extract invents no history, so a
        genuinely new period never trips the guard; and
      * it ended inside the live-capture window — the weekly run revisits
        those on purpose, which is the mechanism that captures the club
        labels and the FA rows in the first place and picks up scoring
        adjustments. A guard the routine path had to bypass would teach
        everyone to bypass it, and the flag would be permanently on by the
        second week.

    Fails closed, in two senses. A period with no schedule row has no
    knowable age, so it counts as settled rather than being waved through.
    And an error evaluating the guard AT ALL refuses the run (MLB-199): the
    previous version caught every ProgrammingError as "no table yet", so a
    pre-registry table missing `league_key` -- the exact legacy shape this
    file documents and self-heals -- threw on the guard's OWN query, read as
    "nothing is loaded", and bypassed protection moments before the loader
    added the column and deleted settled rows. Permission errors and
    identifier typos took the same bypass.

    The legacy shape is now MIGRATED BEFORE THE DECISION rather than
    stumbled over during it, and existence is asked of the catalog instead
    of inferred from an exception.

    MLB-208 split this in two. The engine-specific half -- "what is already
    loaded" -- moved behind `sink.loaded_box_score_periods`, so Snowflake asks
    its catalog and the local sink reads its parquet. The settle/window
    arithmetic and the refusals stayed HERE, shared, because they are the part
    that encodes the policy: two sinks with two copies of this reasoning is
    exactly the drifting-twin shape MLB-175 got bitten by.

    The guard is not warehouse-only and is if anything more load-bearing
    locally -- a Snowflake-free install has no Time Travel and no `_bak`
    clone behind the parquet, so the file IS the history.
    """
    today = today or date.today()
    cutoff = today - timedelta(days=LIVE_CAPTURE_WINDOW_DAYS)

    _, matchups = load_schedule(year)
    end_by_period = {mp: end for mp, _start, end in matchups}

    try:
        last_loaded = sink.loaded_box_score_periods(year, league_key)
    except Exception as exc:
        # Deliberately broad, and the breadth IS the fix. Permissions, a
        # renamed column, schema drift, an unreadable parquet, a typo -- any
        # of them means the guard could not be evaluated, and an unanswered
        # question must not be read as "safe". The narrow version of this
        # catch is what MLB-199/W-02 found bypassing protection moments
        # before the loader deleted settled rows. SystemExit is a
        # BaseException and so passes through untouched.
        raise SystemExit(refuse_unverifiable_guard(exc, year))

    settled = []
    for mp in periods:
        if mp not in last_loaded:
            continue
        end_date = end_by_period.get(mp)
        if end_date is not None and end_date >= cutoff:
            continue
        settled.append((mp, end_date, last_loaded[mp]))
    return sorted(settled)


def refuse_settled_overwrite(settled, year, flag):
    """Build the MLB-188 refusal. Names every offender, the flag, and the
    snapshot — a refusal that does not say how to proceed just gets pattern-
    matched into `--force` by the next person in a hurry.

    MLB-224 raised the bar from "says how to proceed" to "explains itself":
    the message has to carry what is protected and why it cannot be re-
    fetched, not just what to type. Three separate readings of the old
    rationale concluded the guard protected nothing, because the rationale
    described a field (`proTeam`) that nothing reads. Someone who trips this
    should need no human to interpret it."""
    lines = [
        "",
        "=" * 72,
        f"REFUSING TO EXTRACT -- {len(settled)} settled matchup period(s) in {year}",
        "=" * 72,
        "",
        "These periods already hold RAW rows and ended more than "
        f"{LIVE_CAPTURE_WINDOW_DAYS} days ago:",
        "",
        f"  {'period':>7}  {'ended':<12} {'last loaded':<20}",
    ]
    for mp, end_date, loaded_at in settled:
        ended = str(end_date) if end_date else "UNKNOWN (no schedule row)"
        lines.append(f"  {mp:>7}  {ended:<12} {str(loaded_at)[:19]:<20}")
    lines += [
        "",
        "WHAT IS PROTECTED",
        "",
        "  Re-extracting is delete-then-insert, and for a period this old the",
        "  rows it would insert are THINNER than the rows it would drop:",
        "",
        "    * the club-of-game label on every rostered player-day -- the",
        "      club whose GAME the production came from. This is what the",
        "      affinity chart in both books reads. It is only readable while",
        "      kona still returns that period's players, and it already",
        "      cannot be read for 476 player-days of 2026 (60 players) that",
        "      ESPN has stopped returning.",
        "",
        "    * the free agents. Kona is the ONLY source of free-agent",
        "      production. A player who has aged out of today's window does",
        "      not come back unlabelled -- he comes back as no row at all.",
        "",
        "  ESPN will not re-serve the originals: not to this command, not to",
        "  any other, not ever. There is no earlier copy inside the warehouse",
        "  to restore from, and on --raw-target local that parquet file is the",
        "  only copy of these rows in existence anywhere.",
        "",
        "  2025 is what this looks like after it has happened -- written in a",
        "  single pass, and 0 of its 1,236 player-seasons record a mid-season",
        "  club change, against 66 of 1,208 in 2026.",
        "",
        "Nothing was written. Nothing was deleted.",
        "",
        "IF YOU MEANT TO ADD A FIELD RATHER THAN REPLACE THE ROWS",
        "",
        "  Use --backfill-club-of-game. It updates in place, assigns only the",
        "  new key, keeps any stored value today's fetch cannot confirm, and",
        "  deletes nothing. It does not need the flag below.",
        "",
        "IF YOU TRULY MEAN TO OVERWRITE THIS HISTORY",
        "",
        "  1. snapshot RAW first, so the rows survive the decision:",
        "       Snowflake  CREATE TABLE BOX_SCORES_BAK_<date> CLONE BOX_SCORES",
        "       local      copy BOX_SCORES.parquet aside under a dated name",
        f"  2. re-run with {flag}",
        "",
        "  That flag is the deliberate way through. Nothing else bypasses",
        "  this, and it is not a --force: it means the sentence above.",
        "",
        "=" * 72,
    ]
    return "\n".join(lines)


def load_box_scores_to_snowflake(conn, records, matchup_period, year, league_key):
    """
    Insert raw box score JSON records into Snowflake.
    Creates the target table if it doesn't exist.
    Deletes existing data for this league + matchup_period + year before
    inserting, making re-runs fully idempotent.
    """
    cursor = conn.cursor()

    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS BOX_SCORES (
                season_year     INTEGER,
                scoring_period  INTEGER,
                matchup_period  INTEGER,
                raw_json        VARIANT,
                loaded_at       TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
                league_key      VARCHAR
            )
        """)
        ensure_league_key_column(cursor, "BOX_SCORES")

        # Scoped delete: only remove this matchup period for this season year
        # and this league. Without the year filter, re-running 2025 MP1 would
        # wipe 2026 MP1; without the league filter, one league's re-extract
        # would wipe another's rows at the same (year, MP) coordinates. The
        # IS NULL arm self-heals rows that predate the league_key migration
        # (all such rows belong to the default ESPN league).
        #
        # !! MLB-188 -- THIS DELETE IS THE IRREVERSIBLE ONE. What the rows it
        # drops carry, and the INSERT below cannot put back for a settled
        # period: `clubOfGame` on every rostered player-day (the club whose
        # game the production came from, and what the shipped affinity chart
        # reads), and the free-agent rows themselves. Both are read out of
        # kona, which answers about an old scoring period from TODAY's player
        # universe -- players who have aged out come back unlabelled, or as no
        # row at all. 476 player-days of 2026 are already in that state
        # (MLB-193). There is no earlier copy inside this warehouse to restore
        # from. `settled_loaded_periods` is the gate that keeps this from being
        # reachable by accident; do not call this loader around it. Re-running
        # *dbt* is always safe — RAW is the only thing that cannot be rebuilt.
        # Adding a field to a settled period is a job for
        # --backfill-club-of-game, which updates in place and deletes nothing.
        cursor.execute(
            """
            DELETE FROM BOX_SCORES
            WHERE matchup_period = %s AND season_year = %s
              AND (league_key = %s OR league_key IS NULL)
            """,
            (matchup_period, year, league_key)
        )

        for record in records:
            cursor.execute(
                """
                INSERT INTO BOX_SCORES (season_year, scoring_period, matchup_period, raw_json, league_key)
                SELECT %s, %s, %s, PARSE_JSON(%s), %s
                """,
                (
                    year,
                    record["scoring_period"],
                    record["matchup_period"],
                    json.dumps(record["data"]),
                    league_key,
                ),
            )

        conn.commit()
        print(f"  Loaded {len(records)} scoring periods into Snowflake.")

    finally:
        cursor.close()


def extract_matchup_period(sink, league, matchup_period, year, league_key):
    """
    Extract all scoring periods for a matchup period and load them to the sink.

    W-03 / MLB-199 FAIL-CLOSED, AND IT IS STRUCTURAL RATHER THAN CHECKED:
    every scoring period is serialized into `records` BEFORE the single write
    below. A KonaUnavailable anywhere in the loop propagates out of this
    function with nothing written for the period -- so a failed fetch cannot
    produce a period stored without its free agents and with every club label
    null, which the delete-then-insert would have committed over a good one.
    The ordering is the guarantee; keep the write after the loop.
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

    sink.write_box_scores(records, matchup_period, year, league_key)


def _iter_player_entries(blob):
    """Every player dict in a stored box-score blob, rostered and FA alike."""
    for matchup in blob.get("matchups") or []:
        for side in ("home_lineup", "away_lineup"):
            for entry in matchup.get(side) or []:
                yield entry
    for entry in blob.get("free_agents") or []:
        yield entry


def refuse_extract_without_stats(year, matchup_period, why):
    """Build the MLB-199 extract refusal.

    The loader is delete-then-insert. Kona is the ONLY source of free-agent
    production and of club-of-game, so a period serialized without it is
    not merely thinner -- it has no FA rows at all and every club label is
    null. Storing that over a good period is a silent downgrade, and the
    old code did exactly that because a failed fetch returned {}.
    """
    return "\n".join([
        "",
        "=" * 72,
        f"REFUSING TO EXTRACT -- no player stats for {year} "
        f"matchup period {matchup_period}",
        "=" * 72,
        "",
        f"  {why}",
        "",
        "  Nothing was written for this matchup period: the loader runs once",
        "  per period, after every scoring period has been serialized, so a",
        "  failure here happens before the delete-then-insert.",
        "",
        "  kona is the only source of free-agent production and of",
        "  club-of-game. A period built without it carries no FA rows and no",
        "  club labels at all, and storing that over an already-good period",
        "  loses both. Re-run when ESPN answers again.",
        "",
        "=" * 72,
        "",
    ])


def refuse_backfill_without_evidence(year, matchup_period, scoring_period, why):
    """Build the MLB-199 backfill refusal.

    The backfill's whole safety argument is that it only ever ADDS a key.
    That argument holds when the fetch succeeded and fails completely when
    it did not: a failed fetch used to resolve every player to null and
    commit it, turning an additive enrichment into a wholesale erasure of
    the very field it was there to add.
    """
    return "\n".join([
        "",
        "=" * 72,
        f"REFUSING TO BACKFILL -- no club evidence for {year} "
        f"mp={matchup_period} sp={scoring_period}",
        "=" * 72,
        "",
        f"  {why}",
        "",
        "  Nothing was written for this scoring period. Periods completed",
        "  before this point are already committed and are unaffected -- the",
        "  backfill is idempotent, so re-running resumes where it stopped.",
        "",
        "  Writing what this run knows would set clubOfGame to null for",
        "  every player in the period, which is not 'no club' -- it is 'we",
        "  did not ask successfully'. The two used to be the same value.",
        "",
        "=" * 72,
        "",
    ])


def backfill_club_of_game(conn, year, league_key, periods):
    """
    Add `clubOfGame` to periods that are already loaded, and change nothing
    else about them.

    This exists because the obvious way to get a new field onto old rows —
    re-run the extract — is the one thing MLB-188 forbids: the loader's
    delete-then-insert would throw away rows kona can no longer reproduce.
    Both seasons' rows are wanted exactly as they are. 2026's are the only
    near-contemporaneous capture that will ever exist; 2025's are the record
    of what a one-pass backfill produced, which is evidence, not garbage.

    So: read each stored row, set ONE new key on each player, write the row
    back with UPDATE. No DELETE. No other key is assigned, so preservation
    holds by construction rather than by a diff run afterwards — and
    `loaded_at` survives, which matters because it is the only remaining
    evidence of when each period was actually written.

    Idempotent: re-running rewrites the same key with the same value, so a
    half-finished run is resumed simply by running it again.
    """
    cursor = conn.cursor()
    try:
        for mp in periods:
            cursor.execute(
                """
                SELECT scoring_period, raw_json
                FROM BOX_SCORES
                WHERE season_year = %s AND matchup_period = %s
                  AND (league_key = %s OR league_key IS NULL)
                ORDER BY scoring_period
                """,
                (year, mp, league_key),
            )
            rows = cursor.fetchall()
            if not rows:
                print(f"  Matchup period {mp}: no stored rows — skipped.")
                continue

            print(f"  Matchup period {mp}: {len(rows)} scoring period(s)")
            for scoring_period, raw_json in rows:
                blob = json.loads(raw_json)

                # Fetch BEFORE touching the blob, and let an unavailable
                # endpoint stop the run rather than resolve to null. This
                # loop is the one that used to write `clubOfGame = None`
                # over every player in the period whenever the fetch failed.
                try:
                    player_stats = fetch_all_player_stats(year, int(scoring_period))
                except KonaUnavailable as exc:
                    raise SystemExit(refuse_backfill_without_evidence(
                        year, mp, scoring_period, str(exc)))

                # A valid but empty universe is only believable if the
                # stored period agrees that nobody played. If the blob
                # records games and kona reports none, that is an outage
                # answering 200, and writing nulls from it is the erasure
                # this guard exists to prevent.
                played = sum(1 for e in _iter_player_entries(blob)
                             if (e.get("games_played") or 0) > 0)
                if played and not player_stats:
                    raise SystemExit(refuse_backfill_without_evidence(
                        year, mp, scoring_period,
                        f"kona reported an empty player universe, but the "
                        f"stored period records {played} player(s) who played"))

                entries = attributed = preserved = 0
                for entry in _iter_player_entries(blob):
                    stats = player_stats.get(entry.get("playerId"))
                    club = stats["club_of_game"] if stats else None
                    entries += 1
                    if club:
                        entry["clubOfGame"] = club
                        attributed += 1
                    elif entry.get("clubOfGame"):
                        # Positive evidence already stored and none offered
                        # now. Absence of evidence is not evidence of
                        # absence: keep what is there.
                        preserved += 1
                    else:
                        entry["clubOfGame"] = None

                note = f", {preserved} preserved" if preserved else ""
                cursor.execute(
                    """
                    UPDATE BOX_SCORES
                    SET raw_json = PARSE_JSON(%s)
                    WHERE season_year = %s AND matchup_period = %s
                      AND scoring_period = %s
                      AND (league_key = %s OR league_key IS NULL)
                    """,
                    (json.dumps(blob), year, mp, int(scoring_period), league_key),
                )
                print(f"    sp={scoring_period}: {attributed}/{entries} players "
                      f"attributed to a club of game{note}")
            conn.commit()
    finally:
        cursor.close()


def get_recent_matchup_periods(year, lookback_days=LIVE_CAPTURE_WINDOW_DAYS):
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
def fetch_league_payload(year, views):
    """
    Pull one ESPN league document carrying every requested view.

    ESPN's `view` parameter REPEATS rather than replacing -- asking for
    mSettings and mTeam together returns a single document with both
    `settings` and `teams` top-level keys, in one round trip. Verified
    against 2026 and 2025: the `settings` block is byte-identical to what
    a mSettings-only request returns, so adding a view to an existing call
    cannot disturb the payload an existing caller already reads (MLB-227).

    That is why the standings capture costs no extra API call. It is the
    same request that was already being made, asked to hand back a part of
    the response the extract was previously throwing away.
    """
    url = f"{ESPN_API_BASE}/{year}/segments/0/leagues/{LEAGUE_ID}"

    response = requests.get(
        url,
        # A list of pairs, not a dict: a dict cannot express a repeated key,
        # and repeating `view` is precisely the mechanism being used.
        params=[("view", v) for v in views],
        cookies={"swid": SWID, "espn_s2": ESPN_S2},
    )
    response.raise_for_status()

    return response.json()


def fetch_league_settings(year):
    """
    Pull league settings from ESPN's raw API.

    The espn-api wrapper exposes only a subset of settings. The raw
    mSettings payload carries both scoringSettings and rosterSettings.
    We persist the pieces we consume as append-only raw snapshots so dbt
    can build stable contract dims over them.
    """
    return fetch_league_payload(year, ["mSettings"])["settings"]


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
    scoring_items = fetch_league_settings(year)["scoringSettings"]["scoringItems"]

    print(f"  Retrieved {len(scoring_items)} scoring items for {year}")
    return scoring_items


def load_scoring_settings_to_snowflake(conn, scoring_items, year, league_key):
    """
    Append scoring settings as a new row in RAW.SCORING_SETTINGS.

    Uses append-only pattern (not delete+insert) so historical snapshots
    are preserved. The staging model picks the latest row per league +
    season via ROW_NUMBER() OVER (PARTITION BY league_key, season_year
    ORDER BY extracted_at DESC).

    This follows the ELT principle: extraction captures everything,
    transformation decides which version to use.
    """
    cursor = conn.cursor()

    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS SCORING_SETTINGS (
                season_year     INTEGER,
                raw_json        VARIANT,
                extracted_at    TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
                league_key      VARCHAR
            )
        """)
        ensure_league_key_column(cursor, "SCORING_SETTINGS")

        cursor.execute(
            """
            INSERT INTO SCORING_SETTINGS (season_year, raw_json, league_key)
            SELECT %s, PARSE_JSON(%s), %s
            """,
            (year, json.dumps(scoring_items), league_key),
        )

        conn.commit()
        print(f"  Loaded scoring settings for {year} into Snowflake.")

    finally:
        cursor.close()


# ---------------------------------------------------------------------------
# The settings blocks the extract fetched and threw away (MLB-227)
# ---------------------------------------------------------------------------
# ONE RAW TABLE PER SETTINGS BLOCK, and that is Kyle's ruling rather than a
# fallout of the code: scheduleSettings and rosterSettings could share a
# SETTINGS table and will not, because a human troubleshooting roster slots
# would not think to look in the same place as the playoff bracket. Readability
# at RAW outranks table-count economy. It also matches what RAW already does --
# roster and scoring settings have always been two tables, not one.
#
# TEAM_STANDINGS is a different grain again (one payload per season holding
# every team's row) and gets its own table for that reason, exactly as
# TEAM_OWNERS does.
#
# All five are append-only snapshots with the shape the other snapshot tables
# use, so staging picks the latest per (league_key, season_year) by
# extracted_at with no new convention to learn.
SNAPSHOT_TABLES = (
    "SCHEDULE_SETTINGS",
    "DRAFT_SETTINGS",
    "ACQUISITION_SETTINGS",
    "TRADE_SETTINGS",
    "TEAM_STANDINGS",
)

# Every RAW table whose CREATE sits INSIDE a conditional write. A league
# that has never drafted, has no transactions, or whose settings payload
# omits a block never reaches the loader -- so the table was never created
# and `dbt run` died resolving source('raw', ...) rather than reading an
# empty one (MLB-222 C-5). Absent and empty are very different answers to
# "does this install have a draft", and only one of them compiles.
#
# The local sink already got this right: LocalParquetSink.ensure_contract_
# tables() seeds an empty parquet for every contract table before any
# write. This is the same instinct on the warehouse path.
#
# All seven share the snapshot shape, which is why one DDL covers them.
# The loaders keep their own CREATE TABLE IF NOT EXISTS: those are on the
# path Kyle's weekly run depends on, and consolidating proven DML for
# symmetry is how a settled write acquires a new bug (see the note in
# load_snapshot_to_snowflake). Idempotent DDL run twice costs nothing.
CONDITIONAL_RAW_TABLES = SNAPSHOT_TABLES + ("DRAFT_PICKS", "TRANSACTIONS")


def load_snapshot_to_snowflake(conn, table, payload, year, league_key, label):
    """Append one verbatim payload as a row in RAW.<table>.

    The shared engine for the MLB-227 tables. Written once rather than
    copied five times because these five genuinely are the same write --
    same columns, same append-only semantics, same staging contract. The
    three PRE-EXISTING snapshot loaders are deliberately NOT refactored onto
    it: they are on the path Kyle's weekly run depends on, and consolidating
    proven DML for symmetry is how a settled write acquires a new bug.

    `table` is never user input -- it comes from SNAPSHOT_TABLES -- but it is
    checked anyway, because it lands in DDL by string interpolation and a
    typo should fail here rather than create a table nobody meant.
    """
    if table not in SNAPSHOT_TABLES:
        raise ValueError(
            f"{table!r} is not one of the MLB-227 snapshot tables "
            f"({', '.join(SNAPSHOT_TABLES)})."
        )

    cursor = conn.cursor()

    try:
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {table} (
                season_year     INTEGER,
                raw_json        VARIANT,
                extracted_at    TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
                league_key      VARCHAR
            )
        """)
        ensure_league_key_column(cursor, table)

        cursor.execute(
            f"""
            INSERT INTO {table} (season_year, raw_json, league_key)
            SELECT %s, PARSE_JSON(%s), %s
            """,
            (year, json.dumps(payload), league_key),
        )

        conn.commit()
        print(f"  Loaded {label} for {year} into Snowflake.")

    finally:
        cursor.close()


def load_roster_settings_to_snowflake(conn, roster_settings, year, league_key):
    """
    Append roster settings as a new row in RAW.ROSTER_SETTINGS.

    The full rosterSettings object is stored (lineupSlotCounts,
    positionLimits, lineupSlotStatLimits, etc.). dbt's
    dim_roster_slot_counts model reshapes the two slot-count/maximum
    dictionaries into the consumer contract.
    """
    cursor = conn.cursor()

    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ROSTER_SETTINGS (
                season_year     INTEGER,
                raw_json        VARIANT,
                extracted_at    TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
                league_key      VARCHAR
            )
        """)
        ensure_league_key_column(cursor, "ROSTER_SETTINGS")

        cursor.execute(
            """
            INSERT INTO ROSTER_SETTINGS (season_year, raw_json, league_key)
            SELECT %s, PARSE_JSON(%s), %s
            """,
            (year, json.dumps(roster_settings), league_key),
        )

        conn.commit()
        print(f"  Loaded roster settings for {year} into Snowflake.")

    finally:
        cursor.close()


def fetch_team_owners(year):
    """Return the season's team -> owner mapping as a list of dicts:
    {team_id, owner_id, first_name, last_name}.

    The box-score extract keeps only a formatted owner *name* string
    (format_owners) and discards the stable ESPN member GUID. This
    captures (team_id, owner_id) so dbt can join the owner_nicknames
    seed on the GUID instead of fuzzy-matching display strings.

    Co-owned teams emit one row per owner. owner_id is read defensively
    via .get('id') since the espn-api owner dict is not contract-locked.
    """
    league = connect_espn(year)
    rows = []
    for team in league.teams:
        for owner in (getattr(team, "owners", None) or []):
            rows.append({
                "team_id": team.team_id,
                "owner_id": owner.get("id"),
                "first_name": owner.get("firstName"),
                "last_name": owner.get("lastName"),
            })
    return rows


def load_team_owners_to_snowflake(conn, team_owners, year, league_key):
    """Append the season's team -> owner mapping as a row in RAW.TEAM_OWNERS.

    Append-only snapshot (mirrors SCORING_SETTINGS / ROSTER_SETTINGS); the
    staging model picks the latest row per league + season via
    extracted_at. The full list is stored as one VARIANT payload per
    (league, season, extract).
    """
    cursor = conn.cursor()

    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS TEAM_OWNERS (
                season_year     INTEGER,
                raw_json        VARIANT,
                extracted_at    TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
                league_key      VARCHAR
            )
        """)
        ensure_league_key_column(cursor, "TEAM_OWNERS")

        cursor.execute(
            """
            INSERT INTO TEAM_OWNERS (season_year, raw_json, league_key)
            SELECT %s, PARSE_JSON(%s), %s
            """,
            (year, json.dumps(team_owners), league_key),
        )

        conn.commit()
        print(f"  Loaded {len(team_owners)} team-owner rows for {year} into Snowflake.")

    finally:
        cursor.close()


def fetch_draft(year):
    """Return the season's draft board as a list of pick dicts:
    {overall_pick, round_num, round_pick, player_id, player_name,
     team_id, keeper}.

    Sourced from the espn-api wrapper's league.draft, which resolves
    player names and the drafting Team for us (the raw mDraftDetail view
    carries only ids). overall_pick is the 1-based position in the draft
    order -- league.draft is built in ESPN's overallPickNumber sequence,
    so the enumerate index is the true overall selection number (snake
    order included).

    keeper flags picks retained from the prior season (this is a keeper
    league): keepers occupy real draft slots but weren't competitively
    drafted, so consumers can label them.

    Returns [] for a season that hasn't drafted yet (the wrapper leaves
    league.draft empty), so the caller can skip the load.
    """
    league = connect_espn(year)
    rows = []
    for overall_pick, pick in enumerate(league.draft, start=1):
        team = getattr(pick, "team", None)
        rows.append({
            "overall_pick": overall_pick,
            "round_num": pick.round_num,
            "round_pick": pick.round_pick,
            "player_id": pick.playerId,
            "player_name": pick.playerName,
            "team_id": getattr(team, "team_id", None),
            "keeper": bool(pick.keeper_status),
        })
    return rows


def load_draft_to_snowflake(conn, draft_rows, year, league_key):
    """Append the season's draft board as a row in RAW.DRAFT_PICKS.

    Append-only snapshot (mirrors TEAM_OWNERS / SCORING_SETTINGS); the
    staging model picks the latest row per league + season via
    extracted_at. The full pick list is stored as one VARIANT payload per
    (league, season, extract).
    """
    cursor = conn.cursor()

    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS DRAFT_PICKS (
                season_year     INTEGER,
                raw_json        VARIANT,
                extracted_at    TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
                league_key      VARCHAR
            )
        """)
        ensure_league_key_column(cursor, "DRAFT_PICKS")

        cursor.execute(
            """
            INSERT INTO DRAFT_PICKS (season_year, raw_json, league_key)
            SELECT %s, PARSE_JSON(%s), %s
            """,
            (year, json.dumps(draft_rows), league_key),
        )

        conn.commit()
        print(f"  Loaded {len(draft_rows)} draft picks for {year} into Snowflake.")

    finally:
        cursor.close()


def fetch_transactions(year):
    """Return the season's transaction activity as verbatim ESPN topic
    objects (add / drop / trade, plus lineup moves that share the feed),
    newest-first across the whole season.

    ESPN keeps the durable, full-season transaction log for baseball in the
    league message board -- the communication endpoint's
    ACTIVITY_TRANSACTIONS topics -- NOT in the mTransactions2 view, which for
    flb returns only the current scoring period (MLB-16 spike, 2026-07-09).
    We page the board to exhaustion and store the topics verbatim; the
    stg_transactions model interprets the messageTypeId vocabulary (178 add /
    179 drop / 224, 239, 244 trade legs / 188 lineup). Extract captures,
    staging interprets -- so no filtering happens here.

    Historical reach: ESPN serves the message board for the CURRENT season
    only. Prior seasons 404 the per-season path, AND leagueHistory doesn't carry
    the board (its /communication/ 404s and its mTransactions2 returns an empty
    array) -- verified 2025 + 2024, 2026-07-09. So this feed is current-season
    only at the source; the warehouse layer is already season-general (every
    grain carries season_year and fct_roster_stints self-scopes to seasons that
    have a log), so a prior season lights up the instant its rows exist -- from
    a similarly-shaped source, not this endpoint. A 404 here is treated as "no
    board for this season," returning [] so a backfill --year cleanly no-ops.

    Returns [] when the board is empty (pre-draft) or not served for the season.
    """
    url = f"{ESPN_API_BASE}/{year}/segments/0/leagues/{LEAGUE_ID}/communication/"
    topics = []
    offset, page_size, max_topics = 0, 200, 20000
    while offset < max_topics:
        fantasy_filter = {
            "topics": {
                "filterType": {"value": ["ACTIVITY_TRANSACTIONS"]},
                "limit": page_size,
                "offset": offset,
                "sortMessageDate": {"sortPriority": 1, "sortAsc": False},
            }
        }
        response = requests.get(
            url,
            params={"view": "kona_league_communication"},
            cookies={"swid": SWID, "espn_s2": ESPN_S2},
            headers={"x-fantasy-filter": json.dumps(fantasy_filter)},
        )
        # A past season's board isn't served (the endpoint 404s). Treat that as
        # "no transactions for this season" rather than an error, so pointing a
        # backfill at any --year is safe.
        if response.status_code == 404:
            print(f"  ESPN serves no transaction board for {year} "
                  f"(current-season only) -- skipping.")
            return []
        response.raise_for_status()
        page = response.json().get("topics") or []
        if not page:
            break
        topics.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return topics


def load_transactions_to_snowflake(conn, topics, year, league_key):
    """Append the season's transaction board as a row in RAW.TRANSACTIONS.

    Append-only snapshot (mirrors DRAFT_PICKS / TEAM_OWNERS); the staging
    model picks the latest row per league + season via extracted_at. The full
    topic list is stored as one verbatim VARIANT payload per (league, season,
    extract).
    """
    cursor = conn.cursor()

    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS TRANSACTIONS (
                season_year     INTEGER,
                raw_json        VARIANT,
                extracted_at    TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
                league_key      VARCHAR
            )
        """)
        ensure_league_key_column(cursor, "TRANSACTIONS")

        cursor.execute(
            """
            INSERT INTO TRANSACTIONS (season_year, raw_json, league_key)
            SELECT %s, PARSE_JSON(%s), %s
            """,
            (year, json.dumps(topics), league_key),
        )

        conn.commit()
        print(f"  Loaded {len(topics)} transaction topics for {year} into Snowflake.")

    finally:
        cursor.close()


# ---------------------------------------------------------------------------
# Sinks: where RAW lands (MLB-208)
# ---------------------------------------------------------------------------
# Two implementations of one small interface. This one is a THIN ADAPTER over
# the `load_*_to_snowflake` functions above -- they did not move. Relocating
# them into a sink module for symmetry would have moved the delete-then-insert
# MLB-188 calls "the irreversible one", plus the comment block explaining why,
# for no behavioural gain, on the path the weekly run depends on. The new
# code went into extract/raw_sink.py instead; the proven code stayed put.
#
# The interface is deliberately small: six writes, one guard read, one
# describe. Everything else about a period -- the schedule, the settle
# window, the refusals -- is engine-neutral and stays in this module.
class SnowflakeSink:
    """RAW lands in Snowflake. The original behaviour, unchanged."""

    name = "snowflake"

    def __init__(self, conn):
        self.conn = conn

    def describe(self):
        db = SNOWFLAKE_CONFIG.get("database")
        schema = SNOWFLAKE_CONFIG.get("schema")
        return f"{self.name} -> {db}.{schema}"

    def loaded_box_score_periods(self, year, league_key):
        """{matchup_period: MAX(loaded_at)} for this league + season.

        Errors PROPAGATE -- `settled_loaded_periods` turns any failure into a
        refusal. This method must never answer "nothing is loaded" because it
        could not tell (MLB-199 / W-02).
        """
        cursor = self.conn.cursor()
        try:
            if not _table_exists(cursor, "BOX_SCORES"):
                # Nothing has ever been loaded, so nothing can be overwritten.
                # The loader creates the table moments from now.
                return {}

            # The documented legacy shape: a pre-registry table with no
            # league_key. Self-heal it HERE, before the guard query needs the
            # column, so the upgrade path cannot masquerade as an absent table.
            if not _table_has_column(cursor, "BOX_SCORES", "LEAGUE_KEY"):
                print("  [guard] BOX_SCORES predates the league registry; "
                      "adding league_key before checking settled history.")
                ensure_league_key_column(cursor, "BOX_SCORES")

            cursor.execute(
                """
                SELECT matchup_period, MAX(loaded_at)
                FROM BOX_SCORES
                WHERE season_year = %s
                  AND (league_key = %s OR league_key IS NULL)
                GROUP BY matchup_period
                """,
                (year, league_key),
            )
            return {int(mp): ts for mp, ts in cursor.fetchall()}
        finally:
            cursor.close()

    def ensure_contract_tables(self):
        """Create every conditionally-written RAW table, EMPTY, up front.

        The warehouse half of LocalParquetSink.ensure_contract_tables, and
        for the same reason: a table this league has no rows for must be
        PRESENT and empty, not missing. See CONDITIONAL_RAW_TABLES.

        Returns the tables it actually created, so a first run can say what
        it added and a repeat run stays silent.
        """
        cursor = self.conn.cursor()
        created = []
        try:
            for table in CONDITIONAL_RAW_TABLES:
                if not _table_exists(cursor, table):
                    created.append(table)
                cursor.execute(f"""
                    CREATE TABLE IF NOT EXISTS {table} (
                        season_year     INTEGER,
                        raw_json        VARIANT,
                        extracted_at    TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
                        league_key      VARCHAR
                    )
                """)
                ensure_league_key_column(cursor, table)
            self.conn.commit()
        finally:
            cursor.close()
        if created:
            print(f"  created {len(created)} empty RAW table(s) this league "
                  f"has no rows for, so dbt can resolve every source: "
                  f"{', '.join(created)}")
        return created

    def write_box_scores(self, records, matchup_period, year, league_key):
        load_box_scores_to_snowflake(
            self.conn, records, matchup_period, year, league_key)

    def write_scoring_settings(self, scoring_items, year, league_key):
        load_scoring_settings_to_snowflake(
            self.conn, scoring_items, year, league_key)

    def write_roster_settings(self, roster_settings, year, league_key):
        load_roster_settings_to_snowflake(
            self.conn, roster_settings, year, league_key)

    def write_team_owners(self, team_owners, year, league_key):
        load_team_owners_to_snowflake(self.conn, team_owners, year, league_key)

    def write_draft(self, draft_rows, year, league_key):
        load_draft_to_snowflake(self.conn, draft_rows, year, league_key)

    def write_transactions(self, topics, year, league_key):
        load_transactions_to_snowflake(self.conn, topics, year, league_key)

    # -- MLB-227: the blocks that were already being fetched ---------------
    def write_schedule_settings(self, payload, year, league_key):
        load_snapshot_to_snowflake(self.conn, "SCHEDULE_SETTINGS", payload,
                                   year, league_key, "schedule settings")

    def write_draft_settings(self, payload, year, league_key):
        load_snapshot_to_snowflake(self.conn, "DRAFT_SETTINGS", payload,
                                   year, league_key, "draft settings")

    def write_acquisition_settings(self, payload, year, league_key):
        load_snapshot_to_snowflake(self.conn, "ACQUISITION_SETTINGS", payload,
                                   year, league_key, "acquisition settings")

    def write_trade_settings(self, payload, year, league_key):
        load_snapshot_to_snowflake(self.conn, "TRADE_SETTINGS", payload,
                                   year, league_key, "trade settings")

    def write_team_standings(self, payload, year, league_key):
        load_snapshot_to_snowflake(self.conn, "TEAM_STANDINGS", payload,
                                   year, league_key, "team standings")

    def backfill_club_of_game(self, year, league_key, periods):
        backfill_club_of_game(self.conn, year, league_key, periods)


@contextmanager
def open_sink(raw_target, parquet_dir=None):
    """Yield the sink for `raw_target`, holding a warehouse connection only
    if one is actually needed.

    The local branch opens nothing, which is the entire point of MLB-208: a
    stranger with a league id and cookies never authenticates to anything but
    ESPN.
    """
    if raw_target == "local":
        sink = LocalParquetSink(parquet_dir)
        # Before any writes, so the landing area describes the whole contract
        # rather than only the tables this run happens to touch. An ESPN-only
        # RAW with the other tables ABSENT does not build -- the convergence
        # layer reads them and fails on a missing relation; present-and-empty
        # builds 74/74. See LocalParquetSink.ensure_contract_tables.
        sink.ensure_contract_tables()
        yield sink
    else:
        with get_snowflake_connection() as conn:
            sink = SnowflakeSink(conn)
            # Same call, same position, same reason as the local branch
            # above: the tables a league happens to have no rows for must
            # exist and be empty before dbt is asked to resolve them.
            sink.ensure_contract_tables()
            yield sink


def extract_transactions(sink, year, league_key):
    """Pull the season transaction board from ESPN and load it to the sink."""
    print(f"\nTransactions for {year}:")
    topics = fetch_transactions(year)
    if topics:
        print(f"  Retrieved {len(topics)} transaction topics for {year}")
        sink.write_transactions(topics, year, league_key)
    else:
        print(f"  No transaction activity found for {year} -- skipping transactions load")


def extract_scoring_settings(sink, year, league_key):
    """Pull scoring settings from ESPN and load them to the sink."""
    print(f"\nScoring settings for {year}:")
    scoring_items = fetch_scoring_settings(year)
    sink.write_scoring_settings(scoring_items, year, league_key)


# Each settings block that gets its own RAW table, paired with the sink
# method that writes it. A tuple rather than five inline calls so the set of
# captured blocks is one readable list -- adding a sixth block is one line
# here plus one sink method, and nothing else moves.
SETTINGS_BLOCK_WRITERS = (
    ("scheduleSettings", "write_schedule_settings", "schedule settings"),
    ("draftSettings", "write_draft_settings", "draft settings"),
    ("acquisitionSettings", "write_acquisition_settings", "acquisition settings"),
    ("tradeSettings", "write_trade_settings", "trade settings"),
)


def extract_league_settings(sink, year, league_key):
    """Pull scoring + roster settings from ESPN and load them to the sink.

    Also captures the four settings blocks the same response has always
    carried but nothing read (MLB-227), plus the team-season standings from
    the mTeam view. One request serves all of it -- see fetch_league_payload
    -- so the added tables cost no extra call.
    """
    print(f"\nLeague settings for {year}:")
    payload = fetch_league_payload(year, ["mSettings", "mTeam"])
    settings = payload["settings"]

    scoring_items = settings["scoringSettings"]["scoringItems"]
    print(f"  Retrieved {len(scoring_items)} scoring items for {year}")
    sink.write_scoring_settings(scoring_items, year, league_key)

    roster_settings = settings["rosterSettings"]
    slot_count = len(roster_settings.get("lineupSlotCounts", {}) or {})
    print(f"  Retrieved roster settings for {year} ({slot_count} slot counts)")
    sink.write_roster_settings(roster_settings, year, league_key)

    # MLB-227. Each block is skipped rather than defaulted when ESPN does not
    # send it: an absent block and an empty one are different facts about the
    # season, and writing `{}` would make a league that has no trade rules
    # indistinguishable from a season whose payload changed shape.
    for key, method, label in SETTINGS_BLOCK_WRITERS:
        block = settings.get(key)
        if block is None:
            print(f"  No {label} in the {year} payload -- skipping")
            continue
        print(f"  Retrieved {label} for {year} ({len(block)} keys)")
        getattr(sink, method)(block, year, league_key)

    # Team-season standings: divisions, records, playoff seeds and final
    # ranks, verbatim. `playoffSeed` is a full standings rank (1..N, assigned
    # to non-qualifiers too), and it is NOT the same number as
    # `rankCalculatedFinal` -- 2025's champion was the 7 seed. Both are kept
    # because they answer different questions, and neither is reconstructable
    # from a record sort: ESPN seeds division winners first.
    teams = payload.get("teams")
    if teams:
        print(f"  Retrieved standings for {len(teams)} teams in {year}")
        sink.write_team_standings(teams, year, league_key)
    else:
        print(f"  No teams in the {year} payload -- skipping standings")

    team_owners = fetch_team_owners(year)
    print(f"  Retrieved {len(team_owners)} team-owner rows for {year}")
    sink.write_team_owners(team_owners, year, league_key)

    draft_rows = fetch_draft(year)
    if draft_rows:
        print(f"  Retrieved {len(draft_rows)} draft picks for {year}")
        sink.write_draft(draft_rows, year, league_key)
    else:
        print(f"  No draft found for {year} (not drafted yet) -- skipping draft load")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Extract ESPN Fantasy Baseball data into Snowflake.",
        epilog=(
            "By default, extracts recent box scores only. Use --include-settings "
            "to also pull league settings, or --settings-only to pull just settings."
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
        help="Also extract league settings for the season",
    )
    parser.add_argument(
        "--settings-only", action="store_true",
        help="Extract league settings only (skip box scores)",
    )
    parser.add_argument(
        "--include-transactions", action="store_true",
        help="Also extract the season transaction log (adds/drops/trades) "
             "from the ESPN message board (MLB-16)",
    )
    parser.add_argument(
        "--transactions-only", action="store_true",
        help="Extract the transaction log only (skip box scores and settings)",
    )
    parser.add_argument(
        "--backfill-club-of-game", action="store_true",
        help="Add the club-of-game field to periods that are already loaded, "
             "updating in place. Adds one field and changes nothing else — "
             "this is the safe way to enrich settled periods, and it does not "
             "need the overwrite flag because it deletes nothing (MLB-129).",
    )
    parser.add_argument(
        "--overwrite-day-accurate-history", action="store_true",
        help="DESTRUCTIVE. Permit re-extracting matchup periods that are "
             "already loaded and ended more than "
             f"{LIVE_CAPTURE_WINDOW_DAYS} days ago. Their stored club-of-game "
             "labels and their free-agent rows are replaced with whatever kona "
             "still returns for that period today, which is less, and ESPN "
             "will not serve the originals again. Snapshot RAW before using "
             "this. To add a new field to old periods you want "
             "--backfill-club-of-game instead (MLB-188).",
    )
    parser.add_argument(
        "--league", default=None, metavar="LEAGUE_KEY",
        help="League registry key to extract (config/leagues.yml). "
             "Default: the registry's default_league (the ESPN league).",
    )
    parser.add_argument(
        "--raw-target", choices=("snowflake", "local"), default=None,
        help="Where RAW lands. 'snowflake' (default) is the original "
             "behaviour. 'local' writes parquet + _manifest.json under "
             "--parquet-dir, which tools/load_parquet_to_duckdb.py already "
             "consumes -- no warehouse account is needed anywhere (MLB-208). "
             "Falls back to the EXTRACT_RAW_TARGET env var; the flag wins.",
    )
    parser.add_argument(
        "--parquet-dir", default=None, metavar="DIR",
        help="Output directory for --raw-target local "
             "(default: data/parquet/raw, where the DuckDB loader looks).",
    )
    args = parser.parse_args()

    year = args.year

    # Target resolution: explicit flag beats ambient shell state, and the
    # DEFAULT STAYS SNOWFLAKE so no existing invocation changes behaviour.
    # The env var exists so the eventual CI (MLB-217) and packaged demo
    # (MLB-11) paths can set it once instead of threading a flag everywhere.
    raw_target = args.raw_target or os.getenv("EXTRACT_RAW_TARGET") or "snowflake"
    if raw_target not in ("snowflake", "local"):
        raise SystemExit(
            f"[raw target] unknown target {raw_target!r} "
            f"(from EXTRACT_RAW_TARGET); expected 'snowflake' or 'local'."
        )

    # League resolution (MLB-57): --league picks a registry entry; the
    # default preserves the pre-registry runbook (the ESPN league). This
    # script IS the espn adapter -- pointing it at another platform's
    # league is a configuration error, not a fallback.
    try:
        target_league = get_league(args.league)
        if target_league.platform != "espn":
            raise LeagueRegistryError(
                f"extract.py implements the espn platform only; league "
                f"'{target_league.key}' is platform '{target_league.platform}'. "
                f"Known leagues: {', '.join(league_keys())}."
            )
        target_league.require_credentials()
    except LeagueRegistryError as e:
        raise SystemExit(f"[league registry] {e}")

    league_key = target_league.key
    print(f"League: {target_league.display_name} "
          f"(league_key={league_key}, platform={target_league.platform})")

    # Determine what to extract
    do_box_scores = not args.settings_only and not args.transactions_only
    do_settings = args.settings_only or args.include_settings
    do_transactions = args.transactions_only or args.include_transactions

    with open_sink(raw_target, args.parquet_dir) as sink:
        print(f"RAW target: {sink.describe()}")

        # --- League settings ---
        if do_settings:
            extract_league_settings(sink, year, league_key)

        # --- Transactions (MLB-16) ---
        if do_transactions:
            extract_transactions(sink, year, league_key)

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

            if args.backfill_club_of_game:
                # Enrichment, not extraction: updates in place, deletes
                # nothing, so it is not what the guard below is guarding.
                print(f"\nBackfilling club-of-game for {year}: {periods}")
                try:
                    sink.backfill_club_of_game(year, league_key, periods)
                except NotImplementedError as exc:
                    raise SystemExit(f"[raw target] {exc}")
                print("\nDone.")
                sys.exit(0)

            # MLB-188: decide on the whole requested set before touching any
            # of it. A per-period check would half-finish — three periods
            # overwritten, the fourth refused — which is a worse state to be
            # handed than a clean refusal.
            if not args.overwrite_day_accurate_history:
                settled = settled_loaded_periods(sink, year, league_key, periods)
                if settled:
                    raise SystemExit(refuse_settled_overwrite(
                        settled, year, "--overwrite-day-accurate-history"))
            elif periods:
                print("\n!! --overwrite-day-accurate-history: for already-loaded "
                      "settled periods, the stored club-of-game labels and the "
                      "free-agent rows will be replaced by whatever kona still "
                      "returns today, and ESPN will not serve the originals "
                      "again.")

            league = connect_espn(year)

            for mp in periods:
                print(f"\nMatchup period {mp}:")
                try:
                    extract_matchup_period(sink, league, mp, year, league_key)
                except KonaUnavailable as exc:
                    raise SystemExit(refuse_extract_without_stats(year, mp, str(exc)))

    print("\nDone.")
