"""
output/records.py

Workflow orchestrators for the records pipeline, plus backward-compat
re-exports so the consumer scripts (generate_summary.py,
generate_records_report.py, sheets_writer.py, league_notes.py) can keep
using `records.foo` / `from records import foo` patterns unchanged.

Phase 7 Step 3 split:
- output/records_data.py    -- Snowflake queries (10 fns + helpers)
- output/records_logic.py   -- pure consumer-side rules + presentation
- output/records.py (this)  -- orchestrators that compose the two

Historical context preserved:

Phase 6.2 extraction: data access for league records previously lived
split across the two consumer scripts. Team records used a direct
fct_team_weekly_active_performance query with Python max/min; player
records used the leaderboard. New-record detection had its own
leaderboard reads + filter logic. This module unified all data access
on mart_stat_leaderboard as the single source of truth.

Phase 6.3.3 chunk 3: bulk contributor fetches + leaderboard-dump
orchestrator. The records sections and Sheets Tab 3 (Leaderboard Dump)
need contributor lists for many records per script run; the bulk
helpers issue a single batched query rather than N round-trips.

Phase 7 G2: polarity + auto-tracked maps moved to stat_catalog
(stat_catalog.get_polarity_map / get_auto_tracked); records.py was left
as consumer-side filter + presentation logic plus orchestrators.

Phase 7 Step 3: the consumer-side filter + presentation logic moved
to records_logic.py and the data-access functions moved to
records_data.py. This module now holds only the two workflow
orchestrators (get_records_set_this_week, get_records_with_contributors)
plus the re-exports below.

Public orchestrator API:
- get_records_set_this_week(s, mp)    -- new/tied records in just-recapped MP
- get_records_with_contributors(...)  -- leaderboard-dump orchestrator
"""

# Backward-compat re-export so league_notes.py (records.query_snowflake)
# keeps working without consumer-side import changes.
from db import league_predicate, query_snowflake

import stat_catalog

# Backward-compat re-exports from the data layer. Underscored names are
# kept on this module because tests/test_records_pure.py references them
# as records._player_stat_value et al.
from records_data import (
    get_all_time_records,
    get_current_season_records,
    get_record_top_n,
    get_tracked_team_stats,
    get_team_contributors,
    get_team_contributors_bulk,
    get_player_contributors_bulk,
    count_value_occurrences,
    league_history_count,
    load_schedule_lookup,
    _player_stat_value,
)

# Backward-compat re-exports from the logic layer.
from records_logic import (
    SCORE_STAT_NAMES,
    INLINE_COLLAPSE_THRESHOLD,
    should_track_record,
    best_or_worst_label,
    format_week_label,
    ordinal,
    collapse_ties,
    _orchestrator_filter,
    _collapse_one_group,
    _sort_new_records,
)


# ---------- New-record detection ----------

def get_records_set_this_week(season_year, matchup_period):
    """Records broken or tied in this matchup_period. Returns list of dicts:
      {
        'grain': 'team' | 'player',
        'stat_name': uppercase leaderboard name,
        'direction': 'most' | 'fewest',
        'new': leaderboard row holding rank 1,
        'prior': leaderboard row at rank 2 (None if no rank-2 row OR if tied),
        'is_tie': bool (True when rank-2 stat_value equals rank-1),
        'tie_count': int (only present when is_tie; total entities ever at this value),
        'is_new_record': bool (only when is_tie; True when this value was never
            reached before this week -- a brand-new record N entities set at
            once, vs a match of a standing mark),
        'fresh_holders': list of leaderboard rows at the value (only when
            is_new_record) -- the simultaneous setters,
      }
    Polarity filter applied. Floor-noise filter: tied records at value=0
    for individual stats (HBP, QS, SV, HLD, CG, etc. perfect-zero ties)
    are skipped.
    """
    polarity = stat_catalog.get_polarity_map()
    auto_tracked = stat_catalog.get_auto_tracked()

    candidates = query_snowflake(f"""
        SELECT *
        FROM mart_stat_leaderboard
        WHERE rank = 1
          AND record_scope = 'all_time'
          AND season_year = %s
          AND matchup_period = %s
          AND performance_status = 'active'
          AND {league_predicate()}
    """, (season_year, matchup_period))

    out = []
    for cand in candidates:
        grain = cand['entity_grain']
        stat = cand['stat_name']
        direction = cand['record_direction']
        if not should_track_record(grain, stat, direction, polarity, auto_tracked):
            continue

        # Rank 2 = prior holder. With recency tiebreak, also tells us
        # whether we tied (rank-2 stat_value == rank-1).
        prior_rows = query_snowflake(f"""
            SELECT *
            FROM mart_stat_leaderboard
            WHERE entity_grain = %s
              AND stat_name = %s
              AND record_scope = 'all_time'
              AND record_direction = %s
              AND rank = 2
              AND performance_status = 'active'
              AND {league_predicate()}
        """, (grain, stat, direction))
        prior = prior_rows[0] if prior_rows else None

        is_tie = prior is not None and prior['stat_value'] == cand['stat_value']

        # Floor-noise filter: tied at value=0 for individual stats
        # (CG/HLD/SV/QS perfect-zero ties happen weekly with N in the
        # hundreds). Strict breaks at 0 can't happen for these (count
        # can't go negative), so this only affects ties.
        if is_tie and stat not in SCORE_STAT_NAMES and cand['stat_value'] == 0:
            continue

        rec = {
            'grain': grain,
            'stat_name': stat,
            'direction': direction,
            'new': cand,
            'prior': prior if not is_tie else None,
            'is_tie': is_tie,
        }
        if is_tie:
            rec['tie_count'] = count_value_occurrences(grain, stat, cand['stat_value'])
            # A tie at the top only MATCHES a standing record if this value was
            # already reached in an earlier week. If every all-time holder at
            # this value is from THIS week, it's a brand-new record that N
            # entities set simultaneously (e.g. the first-ever cycle, by two
            # teams at once) -- flag it so the recap frames it as a new record
            # rather than "the Nth to do so."
            tied = query_snowflake(f"""
                SELECT season_year, matchup_period, team_abbrev, team_name,
                       owner_name, display_name, player_name
                FROM mart_stat_leaderboard
                WHERE entity_grain = %s AND stat_name = %s
                  AND record_direction = %s AND record_scope = 'all_time'
                  AND performance_status = 'active'
                  AND stat_value = %s
                  AND {league_predicate()}
                ORDER BY rank
            """, (grain, stat, direction, cand['stat_value']))
            this_week = (season_year, matchup_period)
            rec['is_new_record'] = bool(tied) and all(
                (r['season_year'], r['matchup_period']) == this_week for r in tied
            )
            if rec['is_new_record']:
                rec['fresh_holders'] = tied
        out.append(rec)

    return _sort_new_records(out)


# ---------- Leaderboard-dump orchestrator ----------

# Mart caps each (grain, stat, direction) at this many ranks. The
# orchestrator pulls the full buffer (not just top_n) so collapse_ties
# can see whether a tier extends past the display cap and call
# count_value_occurrences() for accurate "N teams tied" totals.
_MART_TOP_N_BUFFER = 10


def get_records_with_contributors(scope, top_n=5):
    """Leaderboard-dump orchestrator. Pulls the mart's top-10 buffer per
    (entity_grain, stat_name, record_direction) for the given scope,
    applies the chunk-3 layered filter, runs tie-collapse trimmed to
    `top_n` display rows, then attaches contributor lists via the bulk
    helpers (one batched SELECT per grain). Contributors are fetched
    AFTER collapse so we don't pay round-trip cost for rows the collapse
    pass discards.

    `scope`: 'all_time' or 'current_season'.
    `top_n`: display cap per partition. Default 5 -- the mart's top-10
    cap is the visibility buffer so chunk-4 collapse detection can see
    tier extension past the display cap.

    Returns a list of dicts. Real leaderboard rows are annotated with a
    'contributors' key (team-grain: list of {display_name, stat_value};
    player-grain: list of {stat_name, count_value, point_value}).
    Synthetic collapsed rows carry 'is_collapsed': True with cleared
    identity fields and an empty contributors list.
    """
    polarity = stat_catalog.get_polarity_map()
    auto_tracked = stat_catalog.get_auto_tracked()

    rows = query_snowflake(f"""
        SELECT entity_grain, stat_name, record_direction, rank,
               season_year, matchup_period,
               team_id, team_name, team_abbrev, owner_name,
               player_id, player_name, display_name, stat_value
        FROM mart_stat_leaderboard
        WHERE record_scope = %s
          AND rank <= %s
          AND performance_status = 'active'
          AND {league_predicate()}
        ORDER BY entity_grain, stat_name, record_direction, rank
    """, (scope, _MART_TOP_N_BUFFER))

    surviving = [
        r for r in rows
        if _orchestrator_filter(
            r['entity_grain'], r['stat_name'],
            r['record_direction'], polarity, auto_tracked,
        )
    ]

    # Phase 6.3.3 chunk 4: collapse first, contributor-stitch second.
    # count_fn=count_value_occurrences (v1.x DI cleanup) lets the saturated-
    # tier branch backfill accurate counts when the visible tier hits the
    # mart's top-10 cap; records_logic stays import-pure (no records_data
    # dependency) so the algorithm can be unit-tested with a mock counter.
    collapsed = collapse_ties(surviving, max_n=top_n, count_fn=count_value_occurrences)

    real_rows = [r for r in collapsed if not r.get('is_collapsed')]
    team_tuples = [
        (r['season_year'], r['matchup_period'], r['team_id'], r['stat_name'])
        for r in real_rows
        if r['entity_grain'] == 'team' and r['team_id'] is not None
    ]
    player_tuples = [
        (r['season_year'], r['matchup_period'], r['player_id'])
        for r in real_rows
        if r['entity_grain'] == 'player' and r['player_id'] is not None
    ]

    team_contribs   = get_team_contributors_bulk(team_tuples)     if team_tuples   else {}
    player_contribs = get_player_contributors_bulk(player_tuples) if player_tuples else {}

    for r in collapsed:
        if r.get('is_collapsed'):
            r['contributors'] = []
        elif r['entity_grain'] == 'team':
            key = (r['season_year'], r['matchup_period'],
                   r['team_id'], r['stat_name'])
            r['contributors'] = team_contribs.get(key, [])
        else:
            key = (r['season_year'], r['matchup_period'], r['player_id'])
            r['contributors'] = player_contribs.get(key, [])

    return collapsed
