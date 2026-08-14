"""
extract.py — ESPN Fantasy Baseball data extraction pipeline.

Handles multiple extraction types from a single entry point:
  1. Box scores: daily player-level stats for each matchup period
  2. League settings: scoring weights + roster settings per season (opt-in)
  3. Team standings: records, divisions, playoff seeds and final ranks

Box scores are extracted by default. League settings require an explicit
flag (--include-settings or --settings-only) because they change rarely
and don't need to run on every weekly pull.

Standings ALSO run by default, and deliberately do not share the settings
flag even though they arrive on the same ESPN view (Kyle 2026-08-09). The
two have opposite refresh needs: settings change once a season, standings
change weekly and now ORDER the almanac's standings tables. Left behind the
opt-in flag, a box-score pull advanced the W-L column while the row order
stayed frozen at the last settings capture, so the table disagreed with
itself and looked like a rendering bug. `--no-standings` opts out; a run
that extracts settings writes them from that response instead of fetching
twice.

Usage:
  py extract/extract.py                              -> recent box scores + standings, current year
  py extract/extract.py --year 2025                  -> recent box scores, 2025
  py extract/extract.py 5                            -> box scores for matchup period 5
  py extract/extract.py --year 2025 1 2 3            -> box scores for specific periods, 2025
  py extract/extract.py --year 2026 --all            -> all CLOSED matchup periods for 2026 (full backfill)
  py extract/extract.py --include-settings           -> recent box scores + league settings
  py extract/extract.py --settings-only              -> league settings only, no box scores
  py extract/extract.py --settings-only --year 2025  -> league settings for 2025 only
  py extract/extract.py --year 2025 --all --backfill-club-of-game
                                                     -> add club-of-game to 2025 in place
  py extract/extract.py --raw-target local            -> write RAW as parquet, no warehouse
  py extract/extract.py --matchup-schedule-only --year 2025
                                                     -> capture 2025's matchup-period membership only
                                                        (that season only, one request)
  py extract/extract.py --matchup-schedule-only --all-seasons
                                                     -> the same capture for every season the
                                                        registry bounds; NO historical box scores

NO SCHEDULE SEED IS REQUIRED (MLB-235). A box-score run captures the
season's `mMatchupScore` document once and reads BOTH the eligible matchup
periods and the scoring-period ids inside each one out of it -- ESPN's own
`schedule[].home/away.pointsByScoringPeriod` keys. Selecting periods used to
mean reading dbt_league/league_config/matchup_schedule.csv, which a new user
had to hand-maintain before the first extract would run at all, and which
made the `matchup_period` stamped on every RAW row originate in the seed
rather than in anything the platform said. That CSV is now optional: an
override surface for a commissioner-declared exception and for human labels
ESPN does not serve. Only CLOSED periods are eligible; the period in flight
is excluded because its membership is still filling in.

AND NEITHER IS A CALENDAR (rung 4B-2). ESPN serves no ISO date in that view,
but its scoring periods are DAYS -- so scoring period N is the season's first
scoring date plus N-1, and a matchup period's start/end are its first and
last scoring period. The anchor is MLB's own published regular-season start
(statsapi.mlb.com, public and key-free), captured to RAW.MLB_SEASON_CALENDAR
on the same run and turned into dates by dim_matchup_period. Nobody types a
calendar. If that anchor cannot be established ordinary weekly capture warns
and continues -- box scores need no dates at all -- and the dates stay absent
rather than being guessed. Complete-history public orchestration supplies
``--require-season-calendar`` and refuses that season instead of continuing to
a plausible partial workbook.

WHERE RAW LANDS (MLB-208). `--raw-target snowflake` is the default and is
unchanged. `--raw-target local` writes the parquet + _manifest.json artifacts
that tools/load_parquet_to_duckdb.py already consumes, so a fresh clone can
go league-id -> extract -> DuckDB -> render without a warehouse account
existing anywhere. EXTRACT_RAW_TARGET sets it by environment; the flag wins.
Both targets share the schedule, the settle window, the refusals and the
league_key stamp -- only the write differs. See extract/raw_sink.py.

Re-extracting a matchup period that has already been loaded and has settled
(closed more than LIVE_CAPTURE_WINDOW_DAYS scoring periods ago, an ESPN
scoring period being one day) replaces its rows with a thinner answer than
the one already stored — club-of-game labels ESPN will not serve again, and
free agents kona has aged out — and is refused; see `settled_loaded_periods`
(MLB-188). To put a new field on settled periods, use
--backfill-club-of-game, which updates in place.
"""

import argparse
import csv
import json
import os
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date
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
from matchup_membership import (
    RECENT, SNAPSHOT_KEYS, MatchupMembershipError, classify_recency,
    matchup_schedule_snapshot, parse_matchup_membership, recent_periods,
    season_long_points_window, seasons_to_request)
from season_calendar import (
    SeasonCalendarError, season_calendar_snapshot, season_calendar_url)


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

# A complete-history season-points run makes two authenticated ESPN reads for
# every scoring day. One reset near the end must not throw away the work of
# the preceding hundred-plus days, but neither may a bad credential become a
# long retry loop. These are the waits *after* the initial attempt. Only
# transport failures, timeouts, throttling, and server errors qualify; other
# 4xx responses fail immediately and the caller's no-partial-period guard
# remains authoritative after the final attempt.
ESPN_TRANSIENT_RETRY_DELAYS_SECONDS = (1, 3, 7)

# ---------------------------------------------------------------------------
# The public MLB calendar request (MLB-235 rung 4B-2)
# ---------------------------------------------------------------------------
# How long to wait for MLB's season record before giving up on the anchor.
#
# Finite because this rides EVERY ordinary box-score run. requests' default is
# no timeout at all, which means a host that accepts the connection and then
# stalls hangs the weekly extract forever -- on a call whose entire failure
# plan is "warn and carry on". Ten seconds is generous for one small JSON
# document and short enough that a stalled host costs a pause rather than a
# run. There is deliberately no retry: the next run picks it up, and dates
# stay visibly absent in between.
SEASON_CALENDAR_TIMEOUT_SECONDS = 10

# Identifies the project to a free public API that owes nobody anything --
# the same courtesy extract/mlb_stats.py already extends to the same host.
PUBLIC_API_USER_AGENT = "espn-league-manager/extract"

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
# Schedule loading -- LEGACY, AND NO LONGER ON ANY RUN PATH (MLB-235 rung 4B-1)
# ---------------------------------------------------------------------------
# Everything in this section used to be a prerequisite for extracting a box
# score: `load_schedule` read matchup_schedule.csv, `get_scoring_periods`
# turned its start/end dates into a scoring-period range, and the extract
# stamped that answer onto every RAW.BOX_SCORES row. A stranger who had not
# hand-typed the season's calendar could not run the extract at all, and the
# `matchup_period` in the warehouse originated in the seed rather than in
# anything ESPN said.
#
# Period membership now comes from the platform's own mMatchupScore document
# (see `acquire_matchup_membership`), so NOTHING here is called by the
# default weekly run, --all, an explicit period list, --backfill-club-of-game
# or the settled-history guard. Two tests assert that directly, and mutating
# `extract_matchup_period` to call `get_scoring_periods()` again fails them.
#
# They are KEPT rather than deleted because the CSV survives as the optional
# override/label surface -- a commissioner-declared exception, and human
# names for periods the platform does not label -- and because the season
# opener these dates anchor is what rung 4B-2 replaces with a derived one.
# Deleting the readers before that lands would remove the seam it needs.
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

    LEGACY as of MLB-235 rung 4B-1: no extraction path calls this. See the
    section comment above. It still raises on an empty seed, which is now a
    property of the override surface rather than of the run.
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
    """Return the list of scoring periods for a given matchup period.

    LEGACY as of MLB-235 rung 4B-1, and the single most important call site
    to keep retired: this is where the seed's dates became the membership the
    extract then stamped onto RAW. `MembershipParse.scoring_periods_for` is
    the platform-owned replacement.
    """
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


class RosterUnavailable(KonaUnavailable):
    """ESPN did not return the day-specific roster document.

    A season-long points league has no H2H box-score wrapper to provide team
    attribution, so mRoster is required rather than a fallback. Subclassing
    KonaUnavailable preserves the existing all-or-nothing period refusal:
    either source failing means no partial period is written.
    """


def _is_transient_espn_error(exc):
    """Whether an ESPN request failure is safe and useful to retry."""
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    if isinstance(exc, requests.HTTPError):
        response = exc.response
        status = response.status_code if response is not None else None
        return status in {408, 429} or (status is not None and status >= 500)
    return False


def _espn_json_with_transient_retries(*, label, url, params, headers=None):
    """GET one authenticated ESPN JSON document with bounded backoff.

    The helper retries only failures for which the same request can plausibly
    succeed moments later. A malformed success body and permanent HTTP error
    remain immediate failures; callers still validate the document's shape.
    """
    delays = (0,) + ESPN_TRANSIENT_RETRY_DELAYS_SECONDS
    for attempt, delay in enumerate(delays):
        if delay:
            print(
                f"    [retry] {label}: waiting {delay}s before attempt "
                f"{attempt + 1}/{len(delays)}"
            )
            time.sleep(delay)
        try:
            response = requests.get(
                url,
                params=params,
                cookies={"swid": SWID, "espn_s2": ESPN_S2},
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            if attempt == len(delays) - 1 or not _is_transient_espn_error(exc):
                raise
            print(f"    [retry] {label}: transient ESPN failure: {exc}")

    raise AssertionError("unreachable")


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
        data = _espn_json_with_transient_retries(
            label=f"kona scoring period {scoring_period}",
            url=url,
            params={"view": "kona_player_info", "scoringPeriodId": scoring_period},
            headers={"x-fantasy-filter": json.dumps(fantasy_filter)},
        )
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


def fetch_season_points_rosters(year, scoring_period):
    """The day-specific team rosters for a season-long points league.

    The pinned espn-api wrapper only implements its concrete box-score classes
    for H2H points and H2H categories. On league type 5 it falls back to the
    abstract BoxScore class, so calling ``League.box_scores`` is not merely a
    bad fit: it raises before returning any lineup. ESPN's mRoster view is the
    underlying day-grain source and supplies exactly what this format needs --
    each player's fantasy team and lineup slot on that scoring day -- without
    inventing an opponent.
    """
    url = f"{ESPN_API_BASE}/{year}/segments/0/leagues/{LEAGUE_ID}"
    try:
        data = _espn_json_with_transient_retries(
            label=f"mRoster scoring period {scoring_period}",
            url=url,
            params={"view": "mRoster", "scoringPeriodId": scoring_period},
        )
    except (requests.RequestException, ValueError) as exc:
        raise RosterUnavailable(
            f"mRoster fetch failed for sp={scoring_period}: {exc}") from exc

    if not isinstance(data, dict) or not isinstance(data.get("teams"), list):
        raise RosterUnavailable(
            f"mRoster returned no teams list for sp={scoring_period}")
    return data


def _roster_period_stats(player, scoring_period):
    """Wrapper-independent fallback stats from one mRoster player object."""
    breakdown = {}
    points = 0.0
    games = 0
    for split in player.get("stats", []) or []:
        if split.get("statSplitTypeId") != 5:
            continue
        if split.get("scoringPeriodId") != scoring_period:
            continue
        raw_stats = split.get("stats") or {}
        if not raw_stats:
            continue
        for stat_id_raw, value in raw_stats.items():
            if value is None:
                continue
            try:
                stat_id = int(stat_id_raw)
            except (TypeError, ValueError):
                continue
            stat_name = _STAT_ID_TO_NAME.get(stat_id, str(stat_id))
            breakdown[stat_name] = breakdown.get(stat_name, 0) + value
        if split.get("appliedTotal") is not None:
            points += split["appliedTotal"]
        games += 1
    return breakdown, round(points, 4), games


def _owner_display(owner_ids, members):
    names = []
    for owner_id in owner_ids or []:
        member = members.get(owner_id, {})
        first = (member.get("firstName") or "").strip().title()
        last = (member.get("lastName") or "").strip().title()
        display = " ".join(part for part in (first, last) if part)
        if not display:
            display = (member.get("displayName") or "").strip()
        if display:
            names.append(display)
    if not names:
        return "Unknown"
    if len(names) == 1:
        return names[0]
    return " / ".join(name.split()[0] for name in names)


def _team_display_name(team, identity=None):
    """Use ESPN's direct name when present, else its location + nickname.

    `identity` is the season's mTeam roll-up (see fetch_team_identity). It
    is consulted BEFORE the placeholder because the type-5 mRoster payload
    carries none of the three label fields, and falling through to
    "Team {id}" there is not a graceful degradation -- it is data loss.
    ESPN served the real name on mTeam the whole time; this is where it
    gets used (MLB-243).
    """
    direct = (team.get("name") or "").strip()
    if direct:
        return direct
    parts = ((team.get("location") or "").strip(),
             (team.get("nickname") or "").strip())
    composed = " ".join(part for part in parts if part)
    if composed:
        return composed
    served = (identity or {}).get(team["id"], {}).get("name")
    return served or f"Team {team['id']}"


def _team_display_abbrev(team, identity=None):
    """The team's abbreviation, same precedence as the display name: the
    roster payload if it carries one, then the season's mTeam record, and
    only then the numeric id -- which is an identifier, not a label."""
    direct = (team.get("abbrev") or "").strip()
    if direct:
        return direct
    served = (identity or {}).get(team["id"], {}).get("abbrev")
    return served or str(team["id"])


def fetch_team_identity(year):
    """{team_id: {'name', 'abbrev'}} for the season, from mTeam.

    THE IDENTITY FEED (adapter contract F8). One request, one row per team,
    present whether or not the team has an owner -- which is why it is used
    rather than the owner bridge, whose grain drops an unowned team
    entirely.

    Returns {} rather than raising: a season-points capture that cannot
    reach mTeam should still land its rosters, with the labels degrading to
    what the roster payload carries. The warehouse repairs the same gap
    from RAW.TEAM_STANDINGS on the next build, so a run that hits this is
    recoverable without a re-extract.
    """
    try:
        payload = fetch_league_payload(year, ["mTeam"])
    except Exception as exc:                       # noqa: BLE001 -- reported
        print(f"  !! could not read team identity from mTeam ({exc}); "
              f"team labels will fall back to the roster payload")
        return {}

    identity = {}
    for team in payload.get("teams") or []:
        if not isinstance(team, dict) or team.get("id") is None:
            continue
        identity[team["id"]] = {
            # Team names are user data and can be anything -- numeric-looking
            # strings, emoji, sentinels. Carried verbatim.
            "name": (team.get("name") or "").strip() or None,
            "abbrev": (team.get("abbrev") or "").strip() or None,
        }
    return identity


def serialize_season_points_rosters(year, scoring_period, reporting_period=1,
                                    team_identity=None):
    """Serialize a season-long points day without manufacturing a matchup.

    RAW gains a parallel ``team_rosters`` array. The ordinary ``matchups``
    array stays empty, so matchup-pair and W/L models correctly see no games;
    staging flattens the roster array into the same player-day contract used
    by the format-agnostic player and team season facts.

    `team_identity` is the season's mTeam labels, fetched ONCE by the caller
    and threaded through every scoring day rather than re-requested 142
    times. Without it this path wrote "Team 1" / "1" for every team, because
    the type-5 mRoster document carries no labels at all (MLB-243).
    """
    roster_doc = fetch_season_points_rosters(year, scoring_period)
    all_player_stats = fetch_all_player_stats(year, scoring_period)
    members = {member.get("id"): member
               for member in roster_doc.get("members", []) or []
               if isinstance(member, dict) and member.get("id") is not None}
    rostered_ids = set()
    team_rosters = []
    kona_count = fallback_count = empty_count = 0

    for team in roster_doc["teams"]:
        if not isinstance(team, dict) or team.get("id") is None:
            raise RosterUnavailable(
                f"mRoster scoring period {scoring_period} contains a team "
                "without an id")
        lineup = []
        entries = ((team.get("roster") or {}).get("entries") or [])
        if not isinstance(entries, list):
            raise RosterUnavailable(
                f"mRoster scoring period {scoring_period}, team {team['id']} "
                "has no roster entries list")
        for entry in entries:
            if not isinstance(entry, dict):
                raise RosterUnavailable(
                    f"mRoster scoring period {scoring_period} contains a "
                    "non-object roster entry")
            player = ((entry.get("playerPoolEntry") or {}).get("player") or {})
            player_id = entry.get("playerId", player.get("id"))
            if player_id is None:
                continue
            rostered_ids.add(player_id)

            slot_id = entry.get("lineupSlotId")
            if slot_id not in LINEUP_SLOT_MAP:
                raise unmapped_lineup_slot_error(
                    KeyError(slot_id), reporting_period, scoring_period)

            raw = all_player_stats.get(player_id)
            if raw is not None:
                breakdown = raw["breakdown"]
                points = raw["points"]
                games_played = raw["games_played"]
                club_of_game = raw["club_of_game"]
                kona_count += 1
            else:
                breakdown, points, games_played = _roster_period_stats(
                    player, scoring_period)
                club_of_game = None
                if games_played:
                    fallback_count += 1
                else:
                    empty_count += 1

            lineup.append({
                "name": player.get("fullName") or str(player_id),
                "playerId": player_id,
                "position": DEFAULT_POSITION_MAP.get(
                    player.get("defaultPositionId"), "UNK"),
                "lineupSlot": LINEUP_SLOT_MAP[slot_id],
                "proTeam": PRO_TEAM_MAP.get(player.get("proTeamId"), "FA"),
                "clubOfGame": club_of_game,
                "points": points,
                "breakdown": breakdown,
                "games_played": games_played,
                "eligibleSlots": [
                    LINEUP_SLOT_MAP.get(slot, str(slot))
                    for slot in player.get("eligibleSlots", []) or []
                ],
            })

        team_rosters.append({
            "team_name": _team_display_name(team, team_identity),
            "team_id": team["id"],
            "team_abbrev": _team_display_abbrev(team, team_identity),
            "owner": _owner_display(team.get("owners"), members),
            "lineup": lineup,
        })

    free_agents = []
    for player_id, raw in all_player_stats.items():
        if player_id in rostered_ids:
            continue
        free_agents.append({
            "name": raw["name"],
            "playerId": player_id,
            "position": DEFAULT_POSITION_MAP.get(
                raw["default_position_id"], "UNK"),
            "lineupSlot": "FA",
            "proTeam": raw["pro_team"],
            "clubOfGame": raw["club_of_game"],
            "points": raw["points"],
            "breakdown": raw["breakdown"],
            "games_played": raw["games_played"],
            "eligibleSlots": raw["eligible_slots"],
        })

    print(f"    season-points rosters: {len(team_rosters)} team(s), "
          f"{len(rostered_ids)} rostered player(s) | {kona_count} tracked by "
          f"kona | fallbacks: {fallback_count} | inactive/no-game: "
          f"{empty_count}")
    return {"matchups": [], "team_rosters": team_rosters,
            "free_agents": free_agents}


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


def settled_loaded_periods(sink, year, league_key, periods, parse, today=None):
    """
    MLB-188 guard. Of `periods`, return the ones a re-extract would damage:
    already loaded, and no longer inside the live-capture window.

    Returns [(matchup_period, last_scoring_period, last_loaded_at)] sorted by
    period. Empty means the requested set is safe to extract.

    IT NO LONGER READS A CALENDAR (MLB-235 rung 4B-1). "Ended more than
    LIVE_CAPTURE_WINDOW_DAYS ago" used to mean an end date out of
    matchup_schedule.csv against `today`; it now means a last scoring-period
    id out of `parse` -- ESPN's own membership -- against that season's
    `latestScoringPeriod`. `classify_recency` is the one place that
    arithmetic lives, shared with the weekly default so the guard's exemption
    and the default's reach cannot drift apart. An ESPN scoring period is one
    day, so this is the same set the dates produced, without requiring a
    stranger to have typed them.

    `today` still decides ONE thing: whether `year` is the current season.
    An earlier season is settled outright, however close a period sits to
    that season's own final scoring period -- kona answers about today's
    player universe, and last year's last week is as unreachable as its
    first.

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

    Fails closed, in two senses. A period the membership cannot place -- not
    in the closed set at all, or in a season whose `latestScoringPeriod` ESPN
    did not send -- has no knowable age, so it counts as settled rather than
    being waved through. That is the same rule the seed version applied to a
    period with no schedule row, restated over the platform's evidence.
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
    verdicts = classify_recency(
        parse,
        window=LIVE_CAPTURE_WINDOW_DAYS,
        is_current_season=year >= today.year,
    )
    last_scoring_period = {p.matchup_period: p.scoring_periods[-1]
                           for p in parse.closed if p.scoring_periods}

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
        # RECENT is the ONLY verdict that exempts. UNKNOWN does not, which is
        # the fail-closed half: a period the payload could not place is
        # protected, not waived.
        if verdicts.get(mp) == RECENT:
            continue
        settled.append((mp, last_scoring_period.get(mp), last_loaded[mp]))
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
        "These periods already hold RAW rows and closed more than "
        f"{LIVE_CAPTURE_WINDOW_DAYS} scoring periods ago (an ESPN scoring "
        "period is one day):",
        "",
        f"  {'period':>7}  {'last day':<28} {'last loaded':<20}",
    ]
    for mp, last_scoring_period, loaded_at in settled:
        # The id, not a date: this refusal no longer consults a calendar, and
        # printing one would be inventing the very thing the seed used to
        # supply. UNKNOWN is a real verdict here -- see the fail-closed note
        # on settled_loaded_periods.
        closed_on = (f"scoring period {last_scoring_period}"
                     if last_scoring_period is not None
                     else "UNKNOWN (not in ESPN's membership)")
        lines.append(f"  {mp:>7}  {closed_on:<28} {str(loaded_at)[:19]:<20}")
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


def extract_matchup_period(sink, league, matchup_period, year, league_key,
                           scoring_periods, serializer=None):
    """
    Extract all scoring periods for a matchup period and load them to the sink.

    `scoring_periods` IS THE PLATFORM'S ANSWER and is passed in rather than
    looked up (MLB-235 rung 4B-1). This line used to read
    `get_scoring_periods(matchup_period, year)`, i.e. the seed's start/end
    dates -- so the `matchup_period` stamped onto every RAW.BOX_SCORES row
    below originated in a CSV a human maintained, and the warehouse could not
    independently prove the mapping it had been handed. It now comes from the
    keys of `pointsByScoringPeriod` in ESPN's own mMatchupScore document.

    W-03 / MLB-199 FAIL-CLOSED, AND IT IS STRUCTURAL RATHER THAN CHECKED:
    every scoring period is serialized into `records` BEFORE the single write
    below. A KonaUnavailable anywhere in the loop propagates out of this
    function with nothing written for the period -- so a failed fetch cannot
    produce a period stored without its free agents and with every club label
    null, which the delete-then-insert would have committed over a good one.
    The ordering is the guarantee; keep the write after the loop.
    """
    print(f"  Matchup period {matchup_period} spans {len(scoring_periods)} days "
          f"(scoring periods {scoring_periods[0]}-{scoring_periods[-1]})")

    serializer = serializer or (
        lambda scoring_period, period: serialize_box_scores(
            league, scoring_period, period))
    records = []
    for sp in scoring_periods:
        print(f"  Pulling scoring period {sp}...")
        sp_data = serializer(sp, matchup_period)
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
# Every one of them is an append-only snapshot with the shape the other
# snapshot tables use, so staging picks the latest per (league_key,
# season_year) by extracted_at with no new convention to learn.
SNAPSHOT_TABLES = (
    "SCHEDULE_SETTINGS",
    "DRAFT_SETTINGS",
    "ACQUISITION_SETTINGS",
    "TRADE_SETTINGS",
    "TEAM_STANDINGS",
    # MLB-235. Its own table for the same readability reason, and one more:
    # SCHEDULE_SETTINGS is a once-a-season block, while this changes every
    # week a matchup period closes. Sharing a table would mean one cadence
    # overwriting the other's freshness story.
    "MATCHUP_SCHEDULE",
    # MLB-235 rung 4B-2. The season's first scoring DATE, from MLB's own
    # public season record -- the anchor that turns ESPN's daily
    # scoring-period ids into a calendar. Deliberately its own narrow table
    # rather than fields grafted onto MATCHUP_SCHEDULE: that snapshot is
    # ESPN's three blocks verbatim, and mixing a second vendor's measurement
    # into it would make "what did ESPN serve" unanswerable from the row.
    #
    # It carries a league_key like every other snapshot because the shape is
    # uniform and both sinks already write it, but the CALENDAR IS NOT
    # LEAGUE-SCOPED -- MLB's season is a fact about baseball. Staging keys it
    # on season_year alone; see stg_mlb__season_calendar.
    "MLB_SEASON_CALENDAR",
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
# They all share the snapshot shape, which is why one DDL covers them.
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
    {team_id, owner_id, first_name, last_name, display_name}.

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
                # Public leagues can expose the stable owner id and ESPN
                # display name while withholding first/last name. Preserve
                # that supported privacy shape instead of turning it into a
                # downstream not-null failure.
                "display_name": owner.get("displayName"),
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
    Returns None when ESPN refuses this otherwise-authenticated member access
    to the communications feed. None means unavailable, not empty; callers
    must preserve any older good snapshot and omit dependent claims.
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
        if response.status_code in (401, 403):
            print(
                f"  [warn] ESPN did not authorize the transaction board for "
                f"{year} (HTTP {response.status_code}). Other league data may "
                "still be available; transaction-dependent output will be "
                "omitted rather than reported as empty."
            )
            return None
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

    # -- MLB-235 -----------------------------------------------------------
    def write_matchup_schedule(self, payload, year, league_key):
        load_snapshot_to_snowflake(self.conn, "MATCHUP_SCHEDULE", payload,
                                   year, league_key, "matchup schedule")

    def write_season_calendar(self, payload, year, league_key):
        load_snapshot_to_snowflake(self.conn, "MLB_SEASON_CALENDAR", payload,
                                   year, league_key, "MLB season calendar")

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
    if topics is None:
        print("  Transaction board unavailable -- preserving any prior "
              "snapshot and continuing.")
        return
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


def _write_team_standings(sink, payload, year, league_key):
    """Write the mTeam standings out of an already-fetched league payload.

    Team-season standings: divisions, records, playoff seeds and final
    ranks, verbatim. `playoffSeed` is a full standings rank (1..N, assigned
    to non-qualifiers too), and it is NOT the same number as
    `rankCalculatedFinal` -- 2025's champion was the 7 seed. Both are kept
    because they answer different questions, and neither is reconstructable
    from a record sort: ESPN seeds division winners first.
    """
    teams = payload.get("teams")
    if teams:
        print(f"  Retrieved standings for {len(teams)} teams in {year}")
        sink.write_team_standings(teams, year, league_key)
    else:
        print(f"  No teams in the {year} payload -- skipping standings")


def extract_team_standings(sink, year, league_key):
    """Standings ALONE, on their own mTeam request.

    Split out of extract_league_settings because the two have opposite
    refresh needs (Kyle 2026-08-09). Settings change once a season and are
    opt-in for that reason; the standings change every week and now ORDER
    the almanac's standings tables, so a box-score pull that advanced the
    W-L column while leaving the row order frozen at the last settings
    capture rendered a table that disagreed with itself.

    mTeam without mSettings is a smaller response and skips the settings
    parsing entirely, so making this the default costs one cheap request
    per run. When settings ARE being extracted they carry mTeam already and
    this is skipped rather than duplicated.
    """
    print(f"\nTeam standings for {year}:")
    payload = fetch_league_payload(year, ["mTeam"])
    _write_team_standings(sink, payload, year, league_key)


def fetch_matchup_schedule(year):
    """Pull the mMatchupScore document for one season.

    ONE REQUEST, and it is the whole acquisition half of MLB-235. The view
    carries `schedule[]` -- whose `home/away.pointsByScoringPeriod` KEYS are
    each matchup period's scoring periods -- alongside the base document's
    `status` and `seasonId`, so a single unfiltered call returns everything
    the derivation needs for the season. Period-filtered requests are not
    required; the evidence returned all 26 periods of one season and all 18
    of another in one response each.

    NOTHING IN THIS PATH READS THE SEED, which is the entire point. The old
    schedule chain was circular -- load_schedule() read matchup_schedule.csv,
    get_scoring_periods() turned its dates into scoring periods, and the
    extract stamped that answer onto RAW.BOX_SCORES -- so the warehouse could
    not independently prove the mapping it had been handed. This request takes
    a season and a league id and nothing else, and as of rung 4B-1 its answer
    is what the extract actually pulls.
    """
    return fetch_league_payload(year, ["mMatchupScore"])


def fetch_season_calendar(year):
    """Pull MLB's own season record for one year.

    PUBLIC, KEY-FREE AND NOT ESPN. statsapi.mlb.com is the same host the
    project's baseball layer already sources player production from
    (extract/mlb_stats.py), so this adds no vendor, no credential and no
    cookie. It is deliberately a separate request from the ESPN document
    rather than something inferred out of it -- see the module docstring in
    extract/season_calendar.py for why `latestScoringPeriod` paired with the
    capture date was rejected as the anchor.

    BOUNDED, because this now runs on EVERY ordinary box-score extract. A
    `requests.get` with no timeout waits forever by default, so a third-party
    host that accepts the connection and then stalls would hang the weekly
    run indefinitely -- on a request whose whole failure story is "warn and
    carry on". The timeout turns that into the warning it was designed to be.
    Deliberately no retry machinery: this is enrichment, the next run picks
    it up, and one request per season stays one request per season.

    The User-Agent identifies the project to a free public API that owes
    nobody anything, which is the same courtesy extract/mlb_stats.py already
    extends to the same host.
    """
    response = requests.get(
        season_calendar_url(year),
        timeout=SEASON_CALENDAR_TIMEOUT_SECONDS,
        headers={"User-Agent": PUBLIC_API_USER_AGENT},
    )
    response.raise_for_status()
    return response.json()


def capture_season_calendar(sink, year, league_key, payload=None,
                            required=False):
    """Store the season's opener anchor. Warns rather than stops on failure.

    NOT FATAL, and that is the deliberate half. Rung 4B-1 made box-score
    extraction need no dates at all, so a public API being briefly unreachable
    must not block the weekly pull -- the run proceeds and the season simply
    has no derived calendar until a later run lands one. What it must NOT do
    is store an anchor it cannot vouch for: a wrong opener does not produce
    missing dates, it produces confident wrong ones for every period in the
    season, so the snapshot refuses on shape and writes nothing.

    The consequence of no anchor is visible rather than silent: dates stay
    NULL in dim_matchup_period, and points-since-trade renders unavailable
    instead of quietly reporting whole-season production.
    """
    try:
        payload = fetch_season_calendar(year) if payload is None else payload
        snapshot = season_calendar_snapshot(payload, season_year=year)
    except SeasonCalendarError as exc:
        if required:
            raise SystemExit(
                f"[season calendar] REFUSING complete-history extraction for "
                f"{year}: MLB's season calendar response was not usable "
                f"({exc}). Membership was preserved for diagnosis, but no "
                f"settings, transactions, box scores, or workbook will be "
                f"produced. Retry when the calendar source is available."
            ) from exc
        print(f"  [warn] MLB's season calendar for {year} was not usable, so "
              f"no opener was stored: {exc}")
        return None
    except Exception as exc:
        if required:
            raise SystemExit(
                f"[season calendar] REFUSING complete-history extraction for "
                f"{year}: could not retrieve a verifiable MLB season calendar "
                f"({type(exc).__name__}: {exc}). Membership was preserved for "
                f"diagnosis, but no settings, transactions, box scores, or "
                f"workbook will be produced. Retry when the calendar source "
                f"is available."
            ) from exc
        print(f"  [warn] could not reach MLB's public season calendar for "
              f"{year} ({type(exc).__name__}: {exc}); dates for this season "
              f"stay unresolved until a later run.")
        return None

    print(f"  Season calendar for {year}: scoring period 1 = "
          f"{snapshot['regularSeasonStartDate']} "
          f"(MLB {snapshot['anchor_field']})")
    sink.write_season_calendar(snapshot, year, league_key)
    return snapshot


def refuse_matchup_schedule_capture(year, why):
    """The message for a membership capture that must not be written.

    A refusal rather than a skip, and the distinction is the point: a skip
    says "there was nothing here", which is a claim about the league. Every
    condition that reaches this function says something different -- the
    document is not the one we asked for, or is not shaped like the thing
    this capture stores. Writing it anyway files evidence that cannot be
    derived from, or worse, files the wrong season's periods under a
    season_year the loader stamped itself, with nothing on the row to
    contradict it.

    Stops the run rather than continuing to the next step for the same
    reason `refuse_settled_overwrite` does: the run's remaining writes are
    stamped with the same `year`, and a season identity that just failed a
    check is not a good foundation for them.
    """
    return (
        f"\n[matchup schedule] REFUSING to capture {year}: {why}.\n"
        f"\nNothing was written. The capture stores "
        f"{{{', '.join(SNAPSHOT_KEYS)}}} from ESPN's mMatchupScore view and "
        f"requires all three -- status carries currentMatchupPeriod, which "
        f"decides which periods are closed, and seasonId is the only season "
        f"label on the row that this project did not stamp itself.\n"
        f"\nCheck that --year names a season this league played, then re-run. "
        f"If ESPN has genuinely changed the shape of this view, that is a "
        f"finding: the derivation reads these three blocks and nothing else."
    )


@dataclass(frozen=True)
class AcquiredMembership:
    """One mMatchupScore acquisition, as both consumers see it.

    `snapshot` is the {seasonId, status, schedule} object RAW stored, and
    `parse` is the CLOSED H2H membership derived FROM THAT SAME OBJECT.
    `season_points` is a separate live daily reporting window only when ESPN
    explicitly identifies league type 5. It is not folded into `parse.closed`:
    doing that would make an unfinished season-long container look like a
    completed H2H matchup and weaken the rule for every ordinary league.

    Holding both is the whole reason this type exists. A run that fetched the
    document twice -- once to store and once to select -- could store one
    week's schedule and extract against another's, and nothing on either row
    would say so.
    """

    snapshot: dict
    parse: object
    season_points: object = None


def acquire_matchup_membership(sink, year, league_key, payload=None):
    """ONE mMatchupScore document -> the RAW snapshot AND the membership.

    ONE ACQUISITION, TWO CONSUMERS (Kyle's ruling). The fetch happens here,
    exactly once per requested season; the snapshot is written to RAW; and the
    membership is parsed out of THE SAME in-memory object, never re-fetched
    and never read back from the row just written. `payload` exists so a test
    can supply the document without a network, not so a caller can pass a
    second one.

    THE TWO TIERS, kept distinct because they protect different things:

      1. STRUCTURE (`matchup_schedule_snapshot`). All three blocks present and
         shaped right, and ESPN's own seasonId equal to the year about to be
         stamped. Failing this writes NOTHING -- an unstorable document is not
         evidence, and filing it would put the wrong season's periods under a
         label the loader supplied itself.

      2. MEMBERSHIP (`parse_matchup_membership`). Side agreement, closed-run
         contiguity, scoring-period key validity, completion proof. Failing
         this KEEPS the snapshot -- it is structurally valid, so it is real
         diagnostic evidence of what ESPN served, and ESPN does not re-serve
         what it has moved on from -- and THEN REFUSES.

    THE REFUSAL IS UNCONDITIONAL AND LIVES HERE, in every acquisition mode
    (Kyle's ruling, rung 4B-1). It was briefly the caller's decision, so
    --matchup-schedule-only stored the unusable document and exited 0. That
    is wrong: capturing usable matchup membership is what the command is FOR,
    and preserving evidence of an unusable response is not the same as
    succeeding at it. A zero-status exit there would tell a script, a cron
    job and a stranger alike that the season's membership had been captured.
    Refusing from inside the acquisition means no caller can forget, and no
    two modes can disagree about it.

    Zero CLOSED periods is a different fact and is NOT this. That parses
    cleanly, returns normally, and only an ordinary box-score run refuses on
    it -- see refuse_no_closed_periods.

    The order is the guarantee: the write happens between the two checks, so
    "preserved but unusable" cannot be reached by a document that failed the
    storage contract, and the refusal cannot be reached before the preserve.
    Both sinks make that write in one shot (Snowflake INSERT + commit, local
    atomic parquet rename), so the behaviour is identical on either engine
    rather than engine-dependent. And because this runs FIRST in `run()`, the
    snapshot really is the only thing written when it refuses, whatever else
    the invocation asked for.
    """
    print(f"\nMatchup schedule for {year}:")
    payload = fetch_matchup_schedule(year) if payload is None else payload
    try:
        snapshot = matchup_schedule_snapshot(payload, season_year=year)
    except MatchupMembershipError as exc:
        raise SystemExit(refuse_matchup_schedule_capture(year, str(exc)))

    periods = {entry.get("matchupPeriodId")
               for entry in snapshot["schedule"]
               if isinstance(entry, dict)}
    current = snapshot["status"].get("currentMatchupPeriod")
    print(f"  Retrieved {len(snapshot['schedule'])} scheduled matchup(s) "
          f"across {len(periods)} matchup period(s) for {year} "
          f"(current period {current})")
    sink.write_matchup_schedule(snapshot, year, league_key)

    try:
        parse = parse_matchup_membership(
            snapshot, league_key=league_key, season_year=year)
        season_points = season_long_points_window(snapshot)
    except MatchupMembershipError as exc:
        raise SystemExit(refuse_membership_unusable(year, str(exc)))

    print(f"  {len(parse.closed)} closed matchup period(s) in {year} "
          f"(latest scoring period "
          f"{parse.latest_scoring_period if parse.latest_scoring_period is not None else 'unknown'})")
    if season_points is not None:
        state = "complete" if season_points.is_complete else "active"
        print(f"  ESPN season-long points: {state} reporting window "
              f"1-{season_points.scoring_periods[-1]} (not classified as a "
              "closed H2H matchup)")
    return AcquiredMembership(snapshot, parse, season_points)


# ---------------------------------------------------------------------------
# Which periods this invocation extracts (MLB-235 rung 4B-1)
# ---------------------------------------------------------------------------
def refuse_membership_unusable(year, why):
    """The snapshot was storable but not derivable-from.

    The middle case, and the one that must not produce a half-run OR a
    successful-looking one: a document shaped like the thing RAW stores but
    that the membership parser cannot read leaves the extract with no
    non-circular answer to "which scoring periods are in period N".

    Raised in EVERY acquisition mode, including --matchup-schedule-only. See
    the ruling recorded on acquire_matchup_membership.

    Says what WAS written, because "nothing was written" would be false here
    and a refusal that misreports its own side effects is worse than none.
    """
    return "\n".join([
        "",
        "=" * 72,
        f"REFUSING -- {year} matchup membership could not be derived",
        "=" * 72,
        "",
        f"  {why}",
        "",
        "  The schedule snapshot WAS preserved in RAW.MATCHUP_SCHEDULE: it",
        "  passed the storage contract, so it is real evidence of what ESPN",
        "  served, and ESPN does not re-serve a document it has moved on",
        "  from. Keeping it is how this becomes diagnosable later.",
        "",
        "  THAT SNAPSHOT IS THE ONLY THING THIS RUN WROTE. No box scores, no",
        "  settings, no standings, no transactions -- whatever else was asked",
        "  for, membership is acquired first and this refusal lands before",
        "  any of it.",
        "",
        "  This exits non-zero even when the schedule was all you asked for.",
        "  Capturing USABLE matchup membership is the point of the capture,",
        "  and storing evidence of an unusable response is not the same as",
        "  succeeding at it. Which scoring periods belong to a matchup period",
        "  is read out of this document; nothing that cannot read it has a",
        "  non-circular answer, and falling back to matchup_schedule.csv is",
        "  the dependency MLB-235 removed.",
        "",
        "  A season with ZERO closed matchup periods is a different thing and",
        "  does not reach here -- that parses cleanly and is reported as zero.",
        "",
        "  If ESPN has genuinely changed this view's shape, that is a",
        "  finding: the stored snapshot is the evidence for it.",
        "",
        "=" * 72,
        "",
    ])


# The status fields that identify format evidence. Type 5 now has one narrow,
# measured season-long-points acquisition path. Other numeric values remain
# diagnostic: the two H2H seasons on file read current=0 / created=2, but that
# does not establish a complete ESPN type map.
FORMAT_EVIDENCE_FIELDS = ("currentLeagueType", "createdAsLeagueType")


def refuse_no_closed_periods(year, snapshot, current_matchup_period):
    """A box-score run that found zero closed matchup periods.

    ZERO IS A VALID CARDINALITY, not malformed data (Kyle's ruling). A
    rotisserie league may plausibly expose no H2H schedule at all, and a
    season asked about before its first period closes has none yet. A measured
    ESPN season-long points league follows its separate daily roster path and
    therefore never reaches this refusal.
    Neither is a reason to fabricate matchup period 0 or 1.

    But it IS a reason to refuse rather than exit 0. A run that selects
    nothing, writes no player rows and prints "Done." is indistinguishable
    from a successful weekly pull, and that is how an unsupported league shape
    becomes a silent empty warehouse.

    Identity-free: no team, owner or league name appears here, only the
    season and the measured status fields.
    """
    status = snapshot.get("status") or {}
    evidence = [f"    status.{field} = {status.get(field)!r}"
                for field in FORMAT_EVIDENCE_FIELDS if field in status]

    lines = [
        "",
        "=" * 72,
        f"REFUSING TO EXTRACT -- no closed matchup periods in {year}",
        "=" * 72,
        "",
        f"  ESPN's schedule for {year} yielded zero CLOSED matchup periods",
        f"  (current matchup period: {current_matchup_period}).",
        "",
        "  The schedule snapshot WAS preserved in RAW.MATCHUP_SCHEDULE.",
        "  Nothing else was written, and nothing was fabricated: no matchup",
        "  period 0 or 1 was invented to make the H2H-shaped models",
        "  non-empty.",
        "",
        "WHAT THIS USUALLY MEANS",
        "",
        "  * the season has not finished its first matchup period yet, so",
        "    there is genuinely nothing settled to pull; or",
        "  * this league does not play head-to-head and is not the measured",
        "    ESPN season-long points shape. Rotisserie and other formats may",
        "    expose no matchup schedule; their acquisition remains unproven,",
        "    so the extractor refuses rather than guessing.",
    ]
    if evidence:
        lines += [
            "",
            "  Measured from this season's own status block and reported",
            "  verbatim. Only type 5 has a measured non-H2H acquisition path;",
            "  the two H2H seasons on file read 0 and 2, but other meanings",
            "  have not been guessed:",
            "",
        ] + evidence
    lines += [
        "",
        "WHAT YOU CAN STILL DO",
        "",
        "  --matchup-schedule-only   capture the membership snapshot alone;",
        "                            it succeeds and reports 0 closed periods",
        "  --settings-only           settings without box scores",
        "",
        "=" * 72,
        "",
    ]
    return "\n".join(lines)


def refuse_unextractable_periods(year, parse, offenders):
    """Explicitly requested periods that ESPN has not closed.

    Named individually and classified, because "period 19 is not available"
    and "period 40 was never scheduled" send a user to different places. The
    classification is read off the parse, never guessed.
    """
    lines = [
        "",
        "=" * 72,
        f"REFUSING TO EXTRACT -- {len(offenders)} requested matchup period(s) "
        f"are not closed in {year}",
        "=" * 72,
        "",
        f"  ESPN's current matchup period for {year} is "
        f"{parse.current_matchup_period}.",
        "",
    ]
    for mp in offenders:
        if mp == parse.current_matchup_period:
            # Deliberately does not claim the season is live: a finished
            # season whose closing period failed the completion proofs lands
            # here too. Both mean the same thing to the caller -- ESPN has
            # not shown this period closed -- and only one of them is
            # "in flight", so the message says neither.
            why = ("is the CURRENT matchup period, and ESPN has not shown it "
                   "CLOSED. A period still filling in stores a short week "
                   "that reads as a real one")
        elif mp in parse.excluded:
            why = "has not been played yet"
        else:
            why = ("carries no membership in ESPN's schedule for this season "
                   "-- it was never scheduled")
        lines.append(f"  period {mp} {why}.")

    closed = parse.closed_periods
    lines += [
        "",
        f"  Closed and extractable: "
        f"{list(closed) if closed else 'none'}",
        "",
        "  Nothing was written. Which periods are closed is ESPN's answer,",
        "  read from the mMatchupScore document this run already captured --",
        "  not from matchup_schedule.csv, and not from the wall clock.",
        "",
        "=" * 72,
        "",
    ]
    return "\n".join(lines)


def refuse_history_calendar_unavailable(year, why):
    """An `--all-seasons` run whose calendar anchor could not be captured.

    THE ASYMMETRY WITH THE ORDINARY RUN IS DELIBERATE, and it is about what
    the command PROMISED. A weekly box-score extract does not need dates at
    all, so a briefly unreachable public API there is a warning: the box
    scores land, the calendar arrives on a later run, and nothing claimed
    otherwise. `--all-seasons` promises a season range, and a range delivered
    with some anchors missing is a half-answer that reports success -- the
    next reader has no way to tell which seasons are dated because they were
    captured and which are undated because a request timed out.

    So this refuses, and nothing is written. Re-running is cheap and
    idempotent; the whole command is one small request per season.
    """
    return "\n".join([
        "",
        "=" * 72,
        f"REFUSING -- {year}'s season calendar could not be captured",
        "=" * 72,
        "",
        f"  {why}",
        "",
        "  NOTHING WAS WRITTEN. No schedule history, no calendars: this is a",
        "  history command, and a range delivered with some anchors missing",
        "  reports success while leaving no way to tell a season that has no",
        "  dates from one whose request happened to fail.",
        "",
        "  The anchor is MLB's own published regular-season start, from the",
        "  free public MLB Stats API. It is what turns ESPN's daily",
        "  scoring-period ids into calendar dates.",
        "",
        "  Re-run when it answers again. One small request per season, and",
        "  re-running costs nothing -- every write here is append-only.",
        "",
        "  An ordinary box-score run is NOT blocked by this: it needs no",
        "  dates, so it warns and carries on.",
        "",
        "=" * 72,
        "",
    ])


@dataclass(frozen=True)
class MatchupHistoryPlan:
    """Every season of a history capture, fully validated and not yet written.

    `seasons` is ((year, schedule_snapshot, calendar_snapshot), ...) ascending
    when the whole set passed. `failure` is (year, snapshot, reason) for the
    ONE season that was storable but not derivable-from -- the only case where
    anything is written at all, and then only that snapshot.

    The two are mutually exclusive by construction: a failure stops the plan.
    """

    seasons: tuple = ()
    failure: object = None


def plan_matchup_history(seasons, league_key, fetch=None, fetch_calendar=None):
    """Fetch and validate an entire history range, WITHOUT WRITING ANYTHING.

    NO SINK, and that is the correction this function exists for. The first
    version validated every ESPN document up front and then wrote season by
    season, parsing membership during each write -- so an underivable 2025
    left 2023 and 2024 successfully written, which contradicted the CLI's own
    promise, the no-partial-history ruling, and the refusal message's claim
    that the diagnostic snapshot was the only thing written. Taking the sink
    away is what makes those three true rather than aspirational: this
    function cannot write, so a caller cannot half-write.

    FOUR GATES, ALL OF THEM BEFORE ANY WRITE:

      1. every ESPN document fetched, exactly once, ascending;
      2. every one structurally valid (all three blocks, right shapes,
         seasonId agreeing with the year) -- a failure raises, nothing is
         written, and no sink is ever opened;
      3. every one parsed into membership -- the FIRST failure returns a plan
         carrying only that season's snapshot, so the caller preserves that
         one diagnostic row and refuses;
      4. every season's MLB calendar fetched, exactly once, and validated --
         a failure raises, nothing is written.

    The calendars are fetched only after membership has passed for the whole
    range, so a range that is going to refuse never touches MLB's API at all.
    """
    fetch = fetch or fetch_matchup_schedule
    fetch_calendar = fetch_calendar or fetch_season_calendar

    print(f"\nMatchup schedule history: {len(seasons)} season(s) "
          f"{seasons[0]}-{seasons[-1]}")
    print("  validating the whole range before writing any of it")

    # 1 + 2. Structure.
    snapshots = []
    for year in seasons:
        try:
            snapshot = matchup_schedule_snapshot(fetch(year), season_year=year)
        except MatchupMembershipError as exc:
            raise SystemExit(refuse_matchup_schedule_capture(year, str(exc)))
        snapshots.append((year, snapshot))
        print(f"  {year}: {len(snapshot['schedule'])} scheduled matchup(s), "
              f"structurally valid")

    # 3. Membership. The first failure is the whole answer: one diagnostic
    # snapshot is preserved by the caller and the range is abandoned.
    for year, snapshot in snapshots:
        try:
            parse_matchup_membership(snapshot, league_key=league_key,
                                     season_year=year)
        except MatchupMembershipError as exc:
            return MatchupHistoryPlan(failure=(year, snapshot, str(exc)))

    # 4. Calendars.
    planned = []
    for year, snapshot in snapshots:
        try:
            calendar = season_calendar_snapshot(fetch_calendar(year),
                                                season_year=year)
        except SeasonCalendarError as exc:
            raise SystemExit(refuse_history_calendar_unavailable(year, str(exc)))
        except Exception as exc:
            raise SystemExit(refuse_history_calendar_unavailable(
                year, f"{type(exc).__name__}: {exc}"))
        planned.append((year, snapshot, calendar))

    print(f"  all {len(planned)} season(s) validated, schedule and calendar")
    return MatchupHistoryPlan(seasons=tuple(planned))


def write_matchup_history(sink, plan, league_key):
    """Write a validated plan, or preserve the one diagnostic snapshot.

    Nothing is fetched here and nothing is re-derived: every snapshot in the
    plan already passed every gate, so this is the only place a history
    capture touches the sink and it either writes the whole range or writes
    exactly one row and refuses.
    """
    if plan.failure:
        year, snapshot, reason = plan.failure
        # The single exception to "no sink until the preflight passes": a
        # structurally valid document is real evidence of what ESPN served,
        # and ESPN does not re-serve what it has moved on from.
        sink.write_matchup_schedule(snapshot, year, league_key)
        raise SystemExit(refuse_membership_unusable(year, reason))

    for year, snapshot, calendar in plan.seasons:
        print(f"\nMatchup schedule for {year}:")
        sink.write_matchup_schedule(snapshot, year, league_key)
        print(f"  Season calendar for {year}: scoring period 1 = "
              f"{calendar['regularSeasonStartDate']} "
              f"(MLB {calendar['anchor_field']})")
        sink.write_season_calendar(calendar, year, league_key)
    return plan.seasons


def select_matchup_periods(parse, *, requested, want_all, year, today=None):
    """The matchup periods to extract, entirely from platform membership.

    THE THREE SPELLINGS, and none of them opens the seed:

      --all            every closed period, exactly as ESPN closed them. A
                       final period promoted by the completion proof is in;
                       the live current period is not, because the parser
                       already excluded it.
      explicit ids     checked against the closed set. Anything not in it
                       refuses and is named -- silently dropping a requested
                       period would look like a successful narrower run.
      default          the closed periods still inside the live-capture
                       window, via the shared `recent_periods` policy the
                       settled-history guard also uses.

    Returns (periods, description). An empty default selection is a normal
    answer -- nothing new has closed -- and is NOT the zero-closed-periods
    shape, which the caller has already refused on by this point.
    """
    today = today or date.today()

    if want_all:
        return list(parse.closed_periods), "all closed matchup periods"

    if requested:
        closed = set(parse.closed_periods)
        offenders = [mp for mp in requested if mp not in closed]
        if offenders:
            raise SystemExit(
                refuse_unextractable_periods(year, parse, offenders))
        return list(requested), "specified matchup periods"

    periods = list(recent_periods(parse,
                                  window=LIVE_CAPTURE_WINDOW_DAYS,
                                  is_current_season=year >= today.year))
    return periods, "recent matchup periods"


def select_season_points_window(window, *, requested, want_all, year):
    """Select the one season-long reporting container without calling it H2H."""
    period = window.reporting_period
    if requested:
        offenders = [value for value in requested if value != period]
        if offenders:
            raise SystemExit(
                f"Season-long points uses reporting period {period}; requested "
                f"period(s) {offenders} do not exist. Nothing was written.")
        return list(requested), "specified season-long reporting period"
    if want_all:
        return [period], "the season-long points reporting window"
    return [period], "the current season-long points reporting window"


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

    # The settings response already carries mTeam, so the standings ride it
    # rather than costing a second request on this path.
    _write_team_standings(sink, payload, year, league_key)

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
# THE ARGUMENT PARSING, THE FLAG RESOLUTION AND THE RUN ARE THREE FUNCTIONS
# rather than one `if __name__` block (MLB-235 rung 4B-1). They were inline,
# and that made the most important thing this ticket changed -- which matchup
# periods a given invocation selects, and from what evidence -- the one part
# of the extract no test could reach. Every selection test drives `run()`.
def build_parser():
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
        help="Extract every CLOSED matchup period for the year (full "
             "backfill). Overrides positional periods and the recent-only "
             "default. Closed is ESPN's own answer, read from the "
             "mMatchupScore document this run captures: the period in flight "
             "and everything after it are skipped, because their membership "
             "is still filling in and a short in-progress period is "
             "indistinguishable from a genuinely short one.",
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
        "--no-standings", action="store_true",
        help="Skip the team-season standings capture. Standings run on "
             "EVERY extract by default (one cheap mTeam request) because "
             "they order the almanac's standings tables and go stale "
             "weekly, unlike the settings they used to ride along with.",
    )
    parser.add_argument(
        "--include-matchup-schedule", action="store_true",
        help="ACCEPTED AND NO LONGER NEEDED (MLB-235). Capturing the "
             "season's matchup-period membership from ESPN's mMatchupScore "
             "view is now automatic on any box-score run, because the "
             "extract selects periods and scoring periods FROM it -- "
             "matchup_schedule.csv is no longer read. Kept so existing "
             "runbooks and scripts keep working; on a box-score run it "
             "changes nothing. Combined with --settings-only or "
             "--transactions-only it still adds the capture to a run that "
             "would not otherwise pay for it.",
    )
    parser.add_argument(
        "--matchup-schedule-only", action="store_true",
        help="Capture the matchup-period membership only (skip box scores, "
             "settings and standings). The cheapest way to backfill a season "
             "of membership -- ONE request for the season named by --year, "
             "and no box-score work. --year Y means Y only; it is not a "
             "history backfill. Use --all-seasons for that.",
    )
    parser.add_argument(
        "--all-seasons", action="store_true",
        help="With --matchup-schedule-only: capture membership for EVERY "
             "season the league registry bounds, ascending, one request each "
             "(config/leagues.yml first_season through final_season, capped "
             "at --year). This is the schedule history and NOTHING ELSE -- it "
             "downloads no historical box scores. The whole set is fetched "
             "and validated before any of it is written, so a failure part "
             "way through cannot leave what looks like a complete backfill.",
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
             "already loaded and closed more than "
             f"{LIVE_CAPTURE_WINDOW_DAYS} scoring periods ago (an ESPN "
             "scoring period is one day). Their stored club-of-game "
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
    parser.add_argument(
        "--require-season-calendar", action="store_true",
        help="Fail closed when the season's MLB calendar cannot be verified. "
             "Used by the complete-history public orchestration; ordinary "
             "weekly extraction retains its warn-and-continue behavior.",
    )
    return parser


def run(args):
    """Do what `args` asks for. Returns the process exit code.

    THE ORDER OF THIS FUNCTION IS PART OF THE CONTRACT (rung 4B-1). Matchup
    membership is acquired FIRST, before settings, standings, transactions or
    any box score, because two refusals have to happen before any of those
    writes: a structurally valid document the membership parser cannot read,
    and an H2H season with zero closed matchup periods. Both mean the ordinary
    box-score route has no non-circular answer. The explicit ESPN type-5 path
    instead derives its one live reporting window before any write. Letting
    settings land first would produce exactly the partial run the two-tier
    rule exists to prevent.
    """
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
    do_box_scores = (not args.settings_only and not args.transactions_only
                     and not args.matchup_schedule_only)
    do_settings = ((args.settings_only or args.include_settings)
                   and not args.matchup_schedule_only)
    do_transactions = ((args.transactions_only or args.include_transactions)
                       and not args.matchup_schedule_only)
    # MLB-235 rung 4B-1. AUTOMATIC ON ANY BOX-SCORE RUN, and no longer opt-in:
    # the capture is what the extract now SELECTS FROM, so a box-score run
    # without it has no non-circular answer to which scoring periods a matchup
    # period contains. It moved to the default exactly the way standings did
    # -- when something downstream stopped working without it.
    #
    # --settings-only and --transactions-only still do not pay for it. The
    # automatic behaviour is load-bearing for box scores specifically, not a
    # licence to add a request to every invocation; the compatibility flag is
    # how those two modes opt in.
    do_matchup_schedule = (args.matchup_schedule_only
                           or args.include_matchup_schedule
                           or do_box_scores)
    # Standings run on every invocation, including --transactions-only:
    # "as up to date as possible" is the whole point, and one mTeam request
    # is cheap enough that gating it on what else the run is doing would
    # only recreate the staleness this split exists to remove. The settings
    # path already carries mTeam, so it writes them itself and this stands
    # down rather than fetching twice.
    do_standings = (not args.no_standings and not do_settings
                    and not args.matchup_schedule_only)

    # Contradictions refuse rather than being silently dropped. Every flag
    # below asks for box-score work that --matchup-schedule-only exists to
    # skip, so accepting the pair would run something other than what was
    # typed and say nothing about it.
    if args.matchup_schedule_only:
        conflicting = [name for name, given in (
            ("matchup periods as positional arguments", bool(args.periods)),
            ("--all", args.all),
            ("--backfill-club-of-game", args.backfill_club_of_game),
            ("--overwrite-day-accurate-history",
             args.overwrite_day_accurate_history),
        ) if given]
        if conflicting:
            raise SystemExit(
                f"[cli] --matchup-schedule-only captures the season's "
                f"matchup-period membership and writes no box scores, so it "
                f"cannot be combined with {', '.join(conflicting)}. Drop "
                f"--matchup-schedule-only to extract box scores, or drop the "
                f"other flag(s) to capture the schedule alone."
            )

    # --all-seasons is history capture, and history capture is the ONLY thing
    # it does. Requiring the pairing keeps `--year` unsurprising: a season
    # named on an ordinary run still means that season, never a silent
    # backfill of twenty more.
    history_seasons = None
    if args.all_seasons:
        if not args.matchup_schedule_only:
            raise SystemExit(
                "[cli] --all-seasons captures matchup-period membership for "
                "every season the registry bounds and writes nothing else, so "
                "it must be combined with --matchup-schedule-only. It does "
                "NOT download historical box scores; extract those a season "
                "at a time with --year.")
        try:
            history_seasons = seasons_to_request(
                target_league.first_season, target_league.final_season, year)
        except ValueError as exc:
            raise SystemExit(f"[league registry] {exc}")
        if not history_seasons:
            # Exits BEFORE open_sink, so an empty range creates no warehouse
            # connection, no parquet directory and no manifest. "There is
            # nothing to capture" must not leave artifacts behind that suggest
            # otherwise.
            print(f"\nThe registry bounds no seasons at or before {year} for "
                  f"{league_key}, so there is no membership history to "
                  f"capture. Nothing was opened or written.")
            return 0

    # --- Registry-bounded schedule history (MLB-235 rung 4B-2) ---
    # THE WHOLE RANGE IS VALIDATED BEFORE THE SINK IS EVEN OPENED. Every ESPN
    # document and every MLB calendar is fetched once and checked here, with
    # no sink in scope, so a failure part way through cannot leave a warehouse
    # holding what looks like a complete backfill. The one exception is a
    # season that is storable but not derivable-from: that snapshot is real
    # evidence and is preserved, which is the only reason the sink opens on a
    # failing plan at all.
    if history_seasons:
        plan = plan_matchup_history(history_seasons, league_key)
        with open_sink(raw_target, args.parquet_dir) as sink:
            print(f"RAW target: {sink.describe()}")
            write_matchup_history(sink, plan, league_key)
        print("\nDone.")
        return 0

    with open_sink(raw_target, args.parquet_dir) as sink:
        print(f"RAW target: {sink.describe()}")

        # --- Matchup-period membership (MLB-235) ---
        # FIRST, and the position is load-bearing. This is the only
        # acquisition of the season's mMatchupScore document, and it refuses
        # on an underivable one from inside itself -- so that refusal lands
        # before any other surface is written, in every mode, and the
        # message's "the snapshot is the only thing this run wrote" is true
        # by construction rather than by each caller remembering.
        membership = None
        if do_matchup_schedule:
            membership = acquire_matchup_membership(sink, year, league_key)
            # The opener anchor rides the same run: one cheap public MLB
            # request per season, no credentials, and it is what turns the
            # membership just captured into a calendar. Non-fatal by design --
            # see capture_season_calendar.
            capture_season_calendar(
                sink, year, league_key,
                required=getattr(args, "require_season_calendar", False),
            )

        # Zero closed periods parsed CLEANLY and is a valid cardinality, so
        # it is not the refusal above. Only a run that owes the user player
        # data refuses on it -- see refuse_no_closed_periods.
        if (do_box_scores and not membership.parse.closed
                and membership.season_points is None):
            raise SystemExit(refuse_no_closed_periods(
                year, membership.snapshot,
                membership.parse.current_matchup_period))

        # --- League settings ---
        if do_settings:
            extract_league_settings(sink, year, league_key)

        # --- Team standings (MLB-227 capture, split out 2026-08-09) ---
        if do_standings:
            extract_team_standings(sink, year, league_key)

        # --- Transactions (MLB-16) ---
        if do_transactions:
            extract_transactions(sink, year, league_key)

        # --- Box scores ---
        if do_box_scores:
            parse = membership.parse
            season_points = membership.season_points
            if season_points is not None:
                periods, described = select_season_points_window(
                    season_points, requested=args.periods,
                    want_all=args.all, year=year)
            else:
                periods, described = select_matchup_periods(
                    parse, requested=args.periods, want_all=args.all, year=year)
            print(f"\nExtracting {described} for {year}: {periods}")

            if not periods:
                # A normal answer, and distinct from the zero-closed-periods
                # refusal above: this season HAS closed periods, none of them
                # is inside the live-capture window. Nothing new to pull.
                print(f"\nNo closed matchup periods inside the last "
                      f"{LIVE_CAPTURE_WINDOW_DAYS} scoring periods for {year}.")
                print("\nDone.")
                return 0

            if args.backfill_club_of_game:
                # Enrichment, not extraction: updates in place, deletes
                # nothing, so it is not what the guard below is guarding.
                #
                # THE SPLIT HERE IS A RULING (Kyle, rung 4B-1), not an
                # oversight. The PERIODS come from ESPN's derived membership,
                # which is what took the seed off this path. The SCORING
                # PERIODS visited inside each one are still the rows actually
                # stored, and that is what keeps the enrichment additive: it
                # can only add a key to a row that already exists. Narrowing
                # it to the derived scoring-period ids instead would silently
                # skip any stored row the payload does not list, turning an
                # additive pass into one that quietly covers less. Do not
                # "fix" this to match the selection.
                print(f"\nBackfilling club-of-game for {year}: {periods}")
                try:
                    sink.backfill_club_of_game(year, league_key, periods)
                except NotImplementedError as exc:
                    raise SystemExit(f"[raw target] {exc}")
                print("\nDone.")
                return 0

            # MLB-188: decide on the whole requested set before touching any
            # of it. A per-period check would half-finish — three periods
            # overwritten, the fourth refused — which is a worse state to be
            # handed than a clean refusal.
            guard_parse = (replace(parse, closed=(season_points,))
                           if season_points is not None else parse)
            if not args.overwrite_day_accurate_history:
                settled = settled_loaded_periods(
                    sink, year, league_key, periods, guard_parse)
                if settled:
                    raise SystemExit(refuse_settled_overwrite(
                        settled, year, "--overwrite-day-accurate-history"))
            else:
                print("\n!! --overwrite-day-accurate-history: for already-loaded "
                      "settled periods, the stored club-of-game labels and the "
                      "free-agent rows will be replaced by whatever kona still "
                      "returns today, and ESPN will not serve the originals "
                      "again.")

            league = None if season_points is not None else connect_espn(year)
            serializer = None
            if season_points is not None:
                # Once per season, not once per scoring day: the labels do
                # not vary by day, and this loop runs ~142 times.
                team_identity = fetch_team_identity(year)
                serializer = (
                    lambda scoring_period, reporting_period:
                    serialize_season_points_rosters(
                        year, scoring_period, reporting_period,
                        team_identity=team_identity))

            for mp in periods:
                label = ("Season-long reporting period" if season_points
                         is not None else "Matchup period")
                print(f"\n{label} {mp}:")
                try:
                    scoring_periods = (
                        season_points.scoring_periods
                        if season_points is not None
                        else parse.scoring_periods_for(mp))
                    extract_matchup_period(
                        sink, league, mp, year, league_key,
                        scoring_periods, serializer=serializer)
                except KonaUnavailable as exc:
                    raise SystemExit(refuse_extract_without_stats(year, mp, str(exc)))

    print("\nDone.")
    return 0


def main(argv=None):
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
