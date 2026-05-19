"""
output/records_data.py

Snowflake data-access layer for the records pipeline.

Phase 7 Step 3 split: extracted from records.py. All SQL lives here.
No consumer-side filter rules, no presentation, no tie-collapse. The
pure consumer-side rules live in records_logic.py; the workflow
orchestrators (and backward-compat re-exports) live in records.py.

Public API:
- get_all_time_records()                -- rank-1 records for all-time scope
- get_current_season_records()          -- rank-1 records for current_season
- get_record_top_n(stat, ...)           -- top-N rank rows for one stat
- get_tracked_team_stats()              -- distinct team-grain stat_names
- get_team_contributors(...)            -- per-player contribution list
- get_team_contributors_bulk(...)       -- batched team contributor fetch
- get_player_contributors_bulk(...)     -- batched player contributor fetch
- count_value_occurrences(...)          -- tie-count helper
- league_history_count(...)             -- generalized fct comparison count
- load_schedule_lookup()                -- (season, mp) -> playoff metadata
"""

from db import query_snowflake


# ---------- Rank-1 record fetches ----------

def get_all_time_records():
    """Rank-1 leaderboard rows for the all_time scope.

    Returns a list of dicts (raw leaderboard rows). Includes both
    entity_grain values (team, player), all stat_names the leaderboard
    tracks, and both record_directions (most, fewest). Consumers key by
    (entity_grain, stat_name, record_direction) to extract what they need.

    The polarity filter is NOT applied here -- consumers filter for their
    use case (the records sections want most+fewest for team scores but
    only most for player; the records report wants only 'most' direction).
    """
    return _fetch_rank1_records('all_time')


def get_current_season_records():
    """Rank-1 leaderboard rows for the current_season scope. Same shape
    as get_all_time_records()."""
    return _fetch_rank1_records('current_season')


def _fetch_rank1_records(scope):
    return query_snowflake("""
        SELECT entity_grain, stat_name, record_direction,
               team_id, team_name, team_abbrev, owner_name,
               player_id, player_name, display_name,
               season_year, matchup_period, stat_value
        FROM mart_stat_leaderboard
        WHERE record_scope = %s
          AND rank = 1
          AND performance_status = 'active'
    """, (scope,))


# ---------- Records report: top-N for one stat ----------

def get_record_top_n(stat_name, grain='team', direction='most',
                     scope='all_time', limit=5):
    """Top-N rank rows for one specific stat. Used by generate_records_report
    to render the multi-rank holders + tie tiers per stat.

    Phase 6.3.3: direction values are 'most' / 'fewest' (renamed from
    'best' / 'worst' at the mart layer). Default limit dropped to 5 to
    match the mart's new top-5 cap.
    """
    return query_snowflake("""
        SELECT rank, season_year, matchup_period, team_id, team_name,
               owner_name, stat_value
        FROM mart_stat_leaderboard
        WHERE entity_grain = %s
          AND record_scope = %s
          AND record_direction = %s
          AND stat_name = %s
          AND rank <= %s
          AND performance_status = 'active'
        ORDER BY rank
    """, (grain, scope, direction, stat_name, limit))


def get_tracked_team_stats():
    """Distinct stat_names present in the team-grain all-time 'most' leaderboard.
    Used by the records report to discover what stats to iterate."""
    rows = query_snowflake("""
        SELECT DISTINCT stat_name
        FROM mart_stat_leaderboard
        WHERE entity_grain = 'team'
          AND record_scope = 'all_time'
          AND record_direction = 'most'
          AND performance_status = 'active'
    """)
    return [r['stat_name'] for r in rows]


# ---------- Contributors ----------

def get_team_contributors(season_year, matchup_period, team_id, stat_column):
    """Per-player contribution to a specific stat for one team in one matchup.

    `stat_column` is interpolated directly into SQL. Safe ONLY because it
    comes from our own leaderboard's stat_name (an enumerated set of
    column names), NOT user input.
    """
    # Phase 7 D2: display_name added as tiebreak token. Without it, the
    # row order for tied stat_value entries is whatever Snowflake's scan
    # returns -- which flips when the fct gets re-materialized (e.g. on
    # --full-refresh). Same flake mode as the mart's ROW_NUMBER fix in
    # B1; deterministic by display_name keeps golden output stable AND
    # reads as "alphabetical within a tie" to a human.
    return query_snowflake(f"""
        SELECT display_name, {stat_column} AS stat_value
        FROM fct_weekly_player_active_performance
        WHERE season_year = %s
          AND matchup_period = %s
          AND team_id = %s
        ORDER BY {stat_column} DESC NULLS LAST, display_name
    """, (season_year, matchup_period, team_id))


# Stats with no per-player breakdown story. Team-level rates aren't
# additive across players; WASTED_POINTS lives in a separate mart at
# different grain. get_team_contributors_bulk returns [] for these, and
# _player_stat_value returns None.
#
# Phase 7 Step 3: moved here from records.py alongside _player_stat_value
# (its sole consumer is get_team_contributors_bulk below).
_NO_PLAYER_BREAKDOWN_STATS = frozenset({
    'ERA', 'WHIP', 'K_PER_9', 'K_PER_BB', 'HR_PER_9', 'BB_PER_9',
    'WASTED_POINTS',
})


def _player_stat_value(row, stat_name):
    """Compute the player's value for a leaderboard stat_name from their
    fct_weekly_player_active_performance row. Most stats are direct columns
    (lowercased); derived counting stats are inline expressions. Returns
    None for stats with no per-player contribution story."""
    if stat_name in _NO_PLAYER_BREAKDOWN_STATS:
        return None
    if stat_name == 'PA':
        return ((row.get('ab')  or 0) + (row.get('b_bb') or 0)
                + (row.get('hbp') or 0) + (row.get('sf')  or 0))
    if stat_name == 'SB_CS':
        return (row.get('sb') or 0) - (row.get('cs')   or 0)
    if stat_name == 'W_L':
        return (row.get('w')  or 0) - (row.get('l')    or 0)
    if stat_name == 'SV_BLSV':
        return (row.get('sv') or 0) - (row.get('blsv') or 0)
    return row.get(stat_name.lower()) or 0


# Leaderboard stat_name -> uppercase column-key list whose *_pts is the
# point contribution for player-grain top-N. Mirrors the wide *_pts
# columns on fct_weekly_player_active_performance. Hitter + pitcher pools both
# included since player records are typically score-level (calculated_*)
# where contributions can come from either side.
_PLAYER_CONTRIB_STATS = (
    # Hitting
    'H', 'AB', 'B_BB', 'B_SO', 'HBP', 'SF',
    'HR', 'R', 'RBI', 'SB', 'CS', 'TB',
    'SINGLES', 'DOUBLES', 'TRIPLES', 'XBH',
    'GDP', 'B_IBB',
    # Pitching
    'W', 'L', 'K', 'ER', 'OUTS', 'QS', 'SV', 'HLD',
    'P_H', 'P_BB', 'P_HR', 'P_R', 'CG', 'BLK', 'WP',
    'HBP_P', 'BLSV', 'NH', 'PG', 'PK', 'SHO',
)


def get_team_contributors_bulk(tuples, top_n=3):
    """Top-N player contributors per (season, mp, team_id, stat_name).

    Single batched fetch from fct_weekly_player_active_performance keyed by the
    distinct team-week tuples; ranking happens in Python. Stats with no
    meaningful per-player breakdown (rate stats, WASTED_POINTS) return
    [] for those input tuples.

    Returns dict[(season, mp, team_id, stat_name)] -> list of
    {display_name, stat_value} (top N by stat value descending,
    zero/None values dropped).
    """
    tuples = list(tuples)
    if not tuples:
        return {}

    team_weeks = sorted({(s, mp, tid) for (s, mp, tid, _) in tuples})
    placeholders = ", ".join(["(%s, %s, %s)"] * len(team_weeks))
    params = [v for tw in team_weeks for v in tw]
    rows = query_snowflake(f"""
        SELECT *
        FROM fct_weekly_player_active_performance
        WHERE (season_year, matchup_period, team_id) IN ({placeholders})
    """, params)

    by_team_week = {}
    for r in rows:
        key = (r['season_year'], r['matchup_period'], r['team_id'])
        by_team_week.setdefault(key, []).append(r)

    out = {}
    for (s, mp, tid, stat) in tuples:
        if stat in _NO_PLAYER_BREAKDOWN_STATS:
            out[(s, mp, tid, stat)] = []
            continue
        scored = []
        for r in by_team_week.get((s, mp, tid), []):
            v = _player_stat_value(r, stat)
            if v is None or v == 0:
                continue
            scored.append({'display_name': r['display_name'], 'stat_value': v})
        # Phase 7 D2: display_name as secondary key keeps tied stat_value
        # entries in deterministic order (alphabetical), same fix pattern
        # as the SQL contributor query above.
        scored.sort(key=lambda x: (-x['stat_value'], x['display_name']))
        out[(s, mp, tid, stat)] = scored[:top_n]
    return out


def get_player_contributors_bulk(tuples, top_n=3, positives_only=True):
    """Top-N stats by point contribution for each (season, mp, player_id).

    For player-grain leaderboard rows (typically score-level records), we
    surface what the player did to earn that score. Counts (not points)
    are the surfaced numbers per the locked v1 spec -- league members
    who know the weights can mentally convert.

    `positives_only=True` (default): rank by signed point_value desc,
    drop pts <= 0. Right for celebratory 'most' direction records.
    `positives_only=False`: rank by abs(point_value) desc. Right when
    surfacing 'fewest' / negative-impact records (not used in the v1
    pipeline since player-grain 'fewest' is filtered out, but the API
    is parameterized in case the rule loosens later).

    Returns dict[(season, mp, player_id)] -> list of
    {stat_name, count_value, point_value} (top N).
    """
    tuples = list(tuples)
    if not tuples:
        return {}

    unique = sorted({t for t in tuples})
    placeholders = ", ".join(["(%s, %s, %s)"] * len(unique))
    params = [v for t in unique for v in t]
    rows = query_snowflake(f"""
        SELECT *
        FROM fct_weekly_player_active_performance
        WHERE (season_year, matchup_period, player_id) IN ({placeholders})
    """, params)

    by_player = {(r['season_year'], r['matchup_period'], r['player_id']): r
                 for r in rows}

    out = {}
    for tup in tuples:
        row = by_player.get(tup)
        if row is None:
            out[tup] = []
            continue
        candidates = []
        for stat in _PLAYER_CONTRIB_STATS:
            col = stat.lower()
            count = row.get(col) or 0
            pts   = row.get(f'{col}_pts') or 0
            if pts == 0:
                continue
            if positives_only and pts <= 0:
                continue
            candidates.append({
                'stat_name':   stat,
                'count_value': count,
                'point_value': pts,
            })
        # Phase 7 D2: stat_name as secondary key keeps tied point_value
        # entries deterministic (alphabetical).
        if positives_only:
            candidates.sort(key=lambda c: (-c['point_value'], c['stat_name']))
        else:
            candidates.sort(key=lambda c: (-abs(c['point_value']), c['stat_name']))
        out[tup] = candidates[:top_n]
    return out


# ---------- League-history count helpers ----------
#
# Phase 6.3.3 chunk 2 derived counting stats live only in
# mart_stat_leaderboard, not on the fcts. count_value_occurrences
# computes them inline at the fct-row level when asked about one.
_DERIVED_STAT_FCT_EXPR = {
    'PA':      'ab + b_bb + hbp + sf',
    'SB_CS':   'sb - cs',
    'W_L':     'w - l',
    'SV_BLSV': 'sv - blsv',
}

# Stats with no fct-layer counterpart (or where exact-equality COUNT
# isn't reliable). count_value_occurrences returns None for these and
# callers fall back to whatever visible-row count they have.
_NON_FCT_COUNTABLE = frozenset({
    'WASTED_POINTS',                      # derived from inactive facts, no direct fct col
    'ERA', 'WHIP', 'K_PER_9', 'K_PER_BB', # floats: exact-equality
    'HR_PER_9', 'BB_PER_9',               # tie counts unreliable
})

_VALID_HISTORY_OPS = frozenset({'=', '!=', '<', '<=', '>', '>='})


def count_value_occurrences(grain, stat_name, value):
    """How many (entity, MP) tuples in fct_weekly_*_performance have this
    exact stat_value, excluding abnormal weeks. Used for 'Nth team/player'
    framing on tied records.

    Returns None if the stat has no fct-layer counterpart or its
    underlying type makes exact-equality COUNT unreliable (rate stats,
    WASTED_POINTS). Callers should fall back to visible tier size in
    that case.
    """
    return league_history_count(grain, stat_name, value, op='=')


def league_history_count(grain, stat_name, value, op='='):
    """Count entity-weeks where stat_name's value compares with `value`
    using `op`, excluding abnormal weeks.

    grain:     'team' or 'player'
    stat_name: leaderboard stat_name (uppercase). Derived stats and
               column-name keys both work via _DERIVED_STAT_FCT_EXPR.
    value:     numeric threshold for the comparison
    op:        one of '=', '!=', '<', '<=', '>', '>='. Default '='.

    Returns int (count). Returns None for stats with no fct counterpart
    (rate stats, WASTED_POINTS). Callers should treat None as "I don't
    know" and not assume zero.
    """
    if op not in _VALID_HISTORY_OPS:
        raise ValueError(f"op must be one of {_VALID_HISTORY_OPS}, got {op!r}")
    if stat_name in _NON_FCT_COUNTABLE:
        return None
    fct = ('fct_weekly_team_active_performance' if grain == 'team'
           else 'fct_weekly_player_active_performance')
    col_expr = _DERIVED_STAT_FCT_EXPR.get(stat_name, stat_name.lower())
    # v1.1.0: is_abnormal is now denormalized onto the weekly facts, so
    # this filter doesn't need the separate dim/seed JOIN anymore.
    rows = query_snowflake(f"""
        SELECT COUNT(*) AS n
        FROM {fct} f
        WHERE f.is_abnormal = false
          AND ({col_expr}) {op} %s
    """, (value,))
    return rows[0]['n'] if rows else 0


# ---------- Schedule lookup ----------

def load_schedule_lookup():
    """Build a (season_year, matchup_period) -> {is_playoff, playoff_round}
    dict from dim_matchup_period. One query per script run; pass the result
    to format_week_label() instead of re-querying for each record.

    v1.1.0: switched from the matchup_schedule seed to dim_matchup_period
    (the consumer-facing contract layer over the seed). is_abnormal /
    is_playoff / playoff_round are also now denormalized onto the four
    weekly facts; consumers reading fact rows directly can skip this
    lookup entirely. Kept for callers that don't have a fact row in hand
    (e.g., historical-record iterators in generate_records_report.py).
    Deletion candidate once all callers migrate.
    """
    rows = query_snowflake("""
        SELECT season_year, matchup_period, is_playoff, playoff_round
        FROM dim_matchup_period
    """)
    return {
        (r['season_year'], r['matchup_period']): {
            'is_playoff':    bool(r['is_playoff']),
            'playoff_round': r['playoff_round'],
        }
        for r in rows
    }
