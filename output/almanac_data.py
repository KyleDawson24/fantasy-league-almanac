"""output/almanac_data.py

Tier 2c.1 (v1.1.1): SQL data-access surface of the league almanac.

Every function in this module issues at least one Snowflake query via
`query_snowflake` (or wraps a project-level data module like `records`).
The module also carries the spec constructors and a small set of helpers
that participate in the data shape -- they belong here, not in
almanac_render.py, because render consumes finished spec dicts rather
than building them.

Imports are one-way: almanac_data.py is the foundational layer of the
almanac module split (data -> logic -> render -> write). No imports from
sibling almanac_* modules.

The previous monolith (output/almanac_sheets.py, ~3700 lines as of v1.1.0)
re-exports every public name here for backward compatibility, so existing
`import almanac_sheets` call sites continue to resolve unchanged.
"""

import re

from db import query_snowflake
import records


RATE_RECORD_SPECS = [
    {
        'section': 'Team Hitting Records',
        'label': 'Batting Average',
        'grain': 'team',
        'stat_name': 'AVG',
        'direction': 'most',
        'source_column': 'avg',
        'min_column': 'ab',
    },
    {
        'section': 'Team Hitting Records',
        'label': 'On-Base Percentage',
        'grain': 'team',
        'stat_name': 'OBP',
        'direction': 'most',
        'source_column': 'obp',
        'min_column': 'ab',
    },
    {
        'section': 'Team Hitting Records',
        'label': 'Slugging Percentage',
        'grain': 'team',
        'stat_name': 'SLG',
        'direction': 'most',
        'source_column': 'slg',
        'min_column': 'ab',
    },
    {
        'section': 'Team Pitching Records',
        'label': 'ERA',
        'grain': 'team',
        'stat_name': 'ERA',
        'direction': 'fewest',
        'source_column': 'era',
        'min_column': 'outs',
    },
    {
        'section': 'Team Pitching Records',
        'label': 'K/9',
        'grain': 'team',
        'stat_name': 'K_PER_9',
        'direction': 'most',
        'source_column': 'k_per_9',
        'min_column': 'outs',
    },
    {
        'section': 'Team Pitching Records',
        'label': 'WHIP',
        'grain': 'team',
        'stat_name': 'WHIP',
        'direction': 'fewest',
        'source_column': 'whip',
        'min_column': 'outs',
    },
]


HITTING_RECORD_LABELS = {
    'AB': 'At Bats',
    'CS': 'Caught Stealing',
    'XBH': 'Extra Base Hits',
    'GDP': 'GIDP (Batter)',
    'GIDP': 'GIDP (Batter)',
    'HBP': 'Hit by Pitch (Batter)',
    'H': 'Hits',
    'SINGLES': '1B',
    'DOUBLES': '2B',
    'TRIPLES': '3B',
    'HR': 'HR',
    'B_IBB': 'Intentional Walks (Batter)',
    'IBB': 'Intentional Walks (Batter)',
    'PA': 'Plate Appearances',
    'RBI': 'RBI',
    'R': 'Runs',
    'SF': 'Sacrifice Flies',
    'SB': 'Stolen Bases',
    'B_SO': 'Strikeouts (Batter)',
    'B_BB': 'Walks (Batter)',
}


HITTING_RECORD_ORDER = {
    stat_name: index
    for index, stat_name in enumerate([
        'AB',
        'CS',
        'XBH',
        'GDP',
        'HBP',
        'H',
        'SINGLES',
        'DOUBLES',
        'TRIPLES',
        'HR',
        'B_IBB',
        'PA',
        'RBI',
        'R',
        'SF',
        'SB',
        'B_SO',
        'TB',
        'B_BB',
    ])
}


def get_latest_matchup_period():
    """Return latest loaded (season_year, matchup_period)."""
    rows = query_snowflake("""
        SELECT season_year, matchup_period
        FROM fct_weekly_team_active_performance
        QUALIFY ROW_NUMBER() OVER (
            ORDER BY season_year DESC, matchup_period DESC
        ) = 1
    """)
    if not rows:
        raise RuntimeError("No team-week rows found; run dbt build first.")
    return rows[0]['season_year'], rows[0]['matchup_period']



def get_team_week_stat_specs():
    """Return scored hitting/pitching stat columns for the Team Weeks tab."""
    rows = query_snowflake("""
        SELECT
            d.leaderboard_name AS stat_name,
            d.display_name,
            d.abbrev,
            d.stat_category,
            s.points_per_unit
        FROM dim_stat d
        INNER JOIN stg_scoring_settings s
            ON s.stat_name = d.stat_name
        WHERE d.stat_category IN ('hitting', 'pitching')
          AND d.is_counting
        ORDER BY
            CASE d.stat_category
                WHEN 'hitting' THEN 1
                WHEN 'pitching' THEN 2
                ELSE 3
            END,
            d.display_name
    """)
    return sorted(rows, key=_team_week_stat_sort_key)



def get_team_weeks(stat_specs):
    """Fetch one row per team-week for the matchup archive tab.

    v1.1.1: the matchup self-join, computed deltas, and league-average
    windows now live in mart_team_matchup. This function selects the
    fixed columns plus the dynamic per-stat columns the active stat
    list requests; the view does the rest.
    """
    if not stat_specs:
        raise RuntimeError("No scored team-week stat specs found.")

    stat_columns = [_fact_stat_column_name(spec['stat_name']) for spec in stat_specs]
    for column in stat_columns:
        if not re.match(r'^[a-z][a-z0-9_]*$', column):
            raise ValueError(f"Unsafe stat column name: {column!r}")

    stat_select = ',\n            '.join(stat_columns)
    rows = query_snowflake(f"""
        SELECT
            season_year,
            matchup_period,
            sort_key,
            team_id,
            team_name,
            team_abbrev,
            opponent_id,
            opponent_name,
            result,
            is_abnormal,
            calculated_hitting_pts,
            calculated_pitching_pts,
            calculated_points,
            opponent_calculated_hitting_pts,
            opponent_calculated_pitching_pts,
            opponent_calculated_points,
            calculated_margin,
            matchup_calculated_hitting_pts,
            matchup_calculated_pitching_pts,
            matchup_calculated_points,
            league_avg_hitting_points,
            league_avg_pitching_points,
            league_avg_total_points,
            {stat_select}
        FROM mart_team_matchup
        ORDER BY sort_key DESC, calculated_points DESC, team_name
    """)
    return rows



def get_team_week_record_marks(stat_specs):
    """Return all-time team-active record values for Team Weeks cell emphasis."""
    record_specs = [
        spec for spec in stat_specs
        if _team_week_good_record_direction(spec)
    ]
    if not record_specs:
        return {}

    selects = []
    for spec in record_specs:
        stat_name = spec.get('stat_name')
        column = _fact_stat_column_name(stat_name)
        if not re.match(r'^[a-z][a-z0-9_]*$', column):
            raise ValueError(f"Unsafe stat column name: {column!r}")
        direction = _team_week_good_record_direction(spec)
        aggregate = 'MAX' if direction == 'most' else 'MIN'
        safe_stat = str(stat_name).replace("'", "''")
        selects.append(f"""
            SELECT
                '{safe_stat}' AS stat_name,
                '{direction}' AS record_direction,
                record_value,
                COUNT(*) AS holder_count
            FROM (
                SELECT
                    {column} AS stat_value,
                    {aggregate}({column}) OVER () AS record_value
                FROM fct_weekly_team_active_performance
                WHERE opponent_id IS NOT NULL
                  AND is_abnormal = false
                  AND {column} IS NOT NULL
            )
            WHERE stat_value = record_value
            GROUP BY record_value
        """)

    rows = query_snowflake("\nUNION ALL\n".join(selects))
    return {
        row['stat_name']: {
            'direction': row.get('record_direction'),
            'value': row.get('record_value'),
            'holder_count': int(row.get('holder_count') or 0),
        }
        for row in rows
    }



def get_all_league_team(season_year, matchup_period=None):
    """Thin wrapper around the generalized get_optimal_team primitive.

    Pre-v1.1.1 this was its own bespoke pipeline (USED-SLOT-based
    candidates from fct_weekly_player_performance + a simpler fill-by-
    slot selection). v1.1.1 reframed All-League Team as a special case
    of "pick the highest-scoring possible lineup given filters" --
    league-wide pool (team_id=None), active-fantasy-credited points
    (points_type='active'), Approach 1 eligibility (per BRAINTHOUGHTS
    [ARCH]), gap-based selection (almanac_logic.get_optimal_team_selections).

    The week-specific vs season-to-date split is now just the
    matchup_period argument; the underlying SQL handles either.
    """
    return get_optimal_team(
        season_year=season_year,
        matchup_period=matchup_period,
        team_id=None,
        points_type='active',
    )


# -------------------------------------------------------------------------
# v1.1.1: generalized optimal-team primitive (Approach 1 per
# BRAINTHOUGHTS [ARCH] -- per-position points by daily eligibility set).
#
# get_optimal_team_candidates: read fct_player_position_pts with the
# right filters, return the (player, position, points) pool.
#
# Pairs with get_optimal_team_selections in almanac_logic.py (gap-based
# selection + disjoint-stat-categories rule for two-way players) and
# get_optimal_team in almanac_logic.py (thin chaining convenience).
#
# This subsumes get_all_league_team* once the wrappers stabilize.
# -------------------------------------------------------------------------


_VALID_POINTS_TYPES = ('active', 'inactive', 'all')


def get_optimal_team_candidates(season_year=None, matchup_period=None,
                                team_id=None, points_type='active'):
    """Read fct_player_position_pts and return one row per
    (player_id, position) with summed points across the filter window.

    All four parameters are filters; pass None to leave that dimension
    unfiltered.

    Args:
      season_year:    Specific season to scope to. None = all-time.
      matchup_period: Specific matchup_period within season_year.
                      None = the full season (or all-time if
                      season_year is also None).
      team_id:        Specific fantasy team_id to scope to. None =
                      league-wide pool, including FA-time rows
                      (team_id IS NULL).
      points_type:    'active', 'inactive', or 'all'. Picks which
                      column to sum -- active_pts (fantasy-credited),
                      inactive_pts (BE/IL/FA), or both. Defaults to
                      'active' since that's by far the most common
                      ask.

    Returns: list of dict rows
      {player_id, player_name, display_name, pro_team, position, position_pts}
      sorted by (position, position_pts DESC). HAVING-filtered to drop
      zero-or-negative rows so the candidate pool stays focused on
      meaningful production. The column is `position_pts` (not
      `platform_points`) because it's position-category-conditional --
      pitching positions hold pitching points, hitting positions hold
      hitting points. The wrapper translates this to `platform_points`
      for the renderer's standard field expectations.
    """
    if points_type not in _VALID_POINTS_TYPES:
        raise ValueError(
            f"points_type must be one of {_VALID_POINTS_TYPES}; got {points_type!r}"
        )

    if points_type == 'active':
        points_expr = 'sum(active_pts)'
    elif points_type == 'inactive':
        points_expr = 'sum(inactive_pts)'
    else:  # 'all'
        points_expr = 'sum(active_pts + inactive_pts)'

    where_clauses = []
    params = []
    if season_year is not None:
        where_clauses.append('season_year = %s')
        params.append(season_year)
    if matchup_period is not None:
        where_clauses.append('matchup_period = %s')
        params.append(matchup_period)
    if team_id is not None:
        where_clauses.append('team_id = %s')
        params.append(team_id)
    where_sql = ' AND '.join(where_clauses) if where_clauses else '1 = 1'

    rows = query_snowflake(f"""
        SELECT
            player_id,
            MAX(player_name)  AS player_name,
            MAX(display_name) AS display_name,
            MAX(pro_team)     AS pro_team,
            position,
            ROUND({points_expr}, 1) AS position_pts
        FROM fct_player_position_pts
        WHERE {where_sql}
        GROUP BY player_id, position
        HAVING {points_expr} > 0
        ORDER BY position, position_pts DESC, player_id
    """, params)

    return rows


# Stat columns the rendered All-League Team row needs (per format_top_
# scorer_stats_line + the TOP_SCORER_STAT_DISPLAY map in formatters.py).
# Kept aligned with the original _get_all_league_team_for_week query so
# the rendered stat-line tail matches the pre-v1.1.1 output for any
# player who happens to be picked by both the old and new selection
# algorithms. v1.1.0 callers can refine.
_OPTIMAL_TEAM_STAT_COLUMNS = (
    # Hitting counting + per-stat _pts
    'h', 'ab', 'b_bb', 'b_so', 'hbp', 'sf', 'hr', 'r', 'rbi', 'sb', 'cs', 'tb',
    'singles', 'doubles', 'triples', 'xbh',
    # Pitching counting + per-stat _pts
    'w', 'l', 'k', 'er', 'outs', 'qs', 'sv', 'hld',
    'p_h', 'p_bb', 'p_hr', 'p_r', 'cg', 'blk', 'wp',
)


def _enrich_optimal_team_with_stats(selected_rows, season_year, matchup_period,
                                    team_id, points_type):
    """Merge stat columns into selected rows so the renderer's
    format_top_scorer_stats_line tail renders correctly.

    Runs a single SUM aggregation over fct_weekly_player_performance
    for the selected player ids, scoped to the same filter window as
    the candidates query. Mutates ``selected_rows`` in place AND
    returns it (convenience).

    For week-specific queries: the sum is over a single (player,
    matchup) row, so it's a no-op aggregation -- the stat-line tail
    matches today's pre-v1.1.1 output for that player.

    For season-to-date / all-time / team-specific queries: the stat-
    line tail represents accumulated production across the filter
    window. Different framing than today's single-week tail, but the
    natural extension of "stats during the same window the points
    came from."
    """
    if not selected_rows:
        return selected_rows
    player_ids = [r['player_id'] for r in selected_rows if r.get('player_id')]
    if not player_ids:
        return selected_rows

    where_clauses = []
    params = []
    if season_year is not None:
        where_clauses.append('season_year = %s')
        params.append(season_year)
    if matchup_period is not None:
        where_clauses.append('matchup_period = %s')
        params.append(matchup_period)
    if team_id is not None:
        where_clauses.append('team_id = %s')
        params.append(team_id)
    if points_type == 'active':
        where_clauses.append("performance_status = 'active'")
    elif points_type == 'inactive':
        where_clauses.append("performance_status = 'inactive'")
    # 'all' -> no performance_status filter

    placeholders = ', '.join(['%s'] * len(player_ids))
    where_clauses.append(f'player_id IN ({placeholders})')
    params.extend(player_ids)
    where_sql = ' AND '.join(where_clauses)

    stat_select = ',\n        '.join(
        f'SUM({col}) AS {col}, SUM({col}_pts) AS {col}_pts'
        for col in _OPTIMAL_TEAM_STAT_COLUMNS
    )

    rows = query_snowflake(f"""
        SELECT
            player_id,
            -- Roster-context fields the renderer reads, all taken from the
            -- player's single most-recent stint via MAX_BY on the shared
            -- recency_key built below -- so a player who changed fantasy
            -- teams mid-window can't get team_abbrev / owner / team_id from
            -- different stints. (These used to be independent column-wise
            -- MAXes -- alphabetical, not chronological, and not co-varying --
            -- which mislabeled traded players, e.g. a GPGP pick showing
            -- CYCL's owner.) For week-specific queries there is one stint, so
            -- this matches the old per-column MAX.
            MAX_BY(season_year,    recency_key) AS season_year,
            MAX_BY(matchup_period, recency_key) AS matchup_period,
            MAX_BY(team_id,        recency_key) AS team_id,
            MAX_BY(team_name,      recency_key) AS team_name,
            MAX_BY(team_abbrev,    recency_key) AS team_abbrev,
            -- canonical owner_display (resolved upstream), taken from the
            -- same most-recent stint.
            MAX_BY(owner_display, recency_key) AS owner_name,
            -- v1.2 (#22): games_played drives the all-time team's ppg
            -- column. Scoped by the same performance_status filter as the
            -- points, so for points_type='active' this is active games and
            -- ppg reads "points per active game" (per-team-tab convention).
            SUM(games_played) AS games_played,
            {stat_select}
        FROM (
            SELECT
                *,
                -- One monotonic recency key per row: latest season, then
                -- matchup_period, then team_id to break a same-period trade
                -- (the data does carry intra-period two-team rows). Lets the
                -- MAX_BY calls above all resolve to the SAME latest stint.
                (season_year * 100 + matchup_period) * 100 + team_id
                    AS recency_key
            FROM fct_weekly_player_performance
            WHERE {where_sql}
        )
        GROUP BY player_id
    """, params)

    by_id = {r['player_id']: r for r in rows}
    for sel in selected_rows:
        pid = sel.get('player_id')
        if pid is not None and pid in by_id:
            # Don't overwrite display fields the selector already set
            # (display_name, pro_team, slot_label, etc.). Only add
            # what's missing.
            for k, v in by_id[pid].items():
                sel.setdefault(k, v)
    return selected_rows


def get_optimal_team(season_year=None, matchup_period=None,
                    team_id=None, points_type='active'):
    """One-stop call: get the optimal lineup for any (timespan, scope,
    points_type) combination.

    Chains the three primitives:
      1. get_optimal_team_candidates -- per-position candidate pool
      2. almanac_logic.get_optimal_team_selections -- gap-based fill
      3. _enrich_optimal_team_with_stats -- merge stat columns for
         the renderer's top-N stat-line tail

    Returns the selected lineup as a list of dicts ready for
    format_all_league_team_row, sorted in canonical baseball-card
    SLOT_ORDER (C, 1B, 2B, ..., SP*, RP*) by the selector. For
    non-matchup-specific queries (season-to-date or all-time), each
    row gets period_label='Season' so the renderer suppresses the
    per-row boxscore URL (today's season-to-date behavior; carried
    forward).

    See get_optimal_team_candidates for parameter semantics.
    """
    candidates = get_optimal_team_candidates(
        season_year=season_year,
        matchup_period=matchup_period,
        team_id=team_id,
        points_type=points_type,
    )

    # Roster shape: use the specified season's config, or the latest
    # season when None (per the v1.1.1 architectural call for all-time
    # views -- current league shape, not historical-per-season).
    if season_year is not None:
        caps_year = season_year
    else:
        caps_year, _ = get_latest_matchup_period()
    slot_caps = get_slot_capacities(caps_year, matchup_period=None)

    # Lazy import to dodge module-load circular (data <-> logic;
    # cleanup target tracked in v1.x Handoff carry-overs).
    from almanac_logic import get_optimal_team_selections
    selected = get_optimal_team_selections(candidates, slot_caps)

    _enrich_optimal_team_with_stats(
        selected, season_year, matchup_period, team_id, points_type,
    )

    # Mark non-matchup-specific rows so the renderer suppresses the
    # boxscore URL (boxscore is only meaningful for a single matchup).
    if matchup_period is None:
        for row in selected:
            row['period_label'] = 'Season'

    return selected


def get_home_tab_data(season_year, matchup_period):
    """Fetch every dataset the Home tab needs, in one place (#23).

    Centralizing the queries here is the fix for the recurring "preview
    path vs live-write path drift" bug class: both generate_almanac_sheet
    (preview) and write_almanac (live) call this, so they can't disagree
    on what the Home tab is built from. Keys match build_home_tab_rows'
    data params.

      weekly_rows / season_rows   -- active-lens All-League Team (week, season)
      weekly_all_rows / season_all_rows
                                  -- points_type='all' lineups (active +
                                     inactive + FA) driving the Total-Pts
                                     deviation columns
      all_time_rows               -- all-time active All-League Team (left band)
    """
    return {
        'weekly_rows': get_all_league_team(season_year, matchup_period),
        'season_rows': get_all_league_team(season_year),
        'weekly_all_rows': get_optimal_team(
            season_year, matchup_period, points_type='all',
        ),
        'season_all_rows': get_optimal_team(season_year, points_type='all'),
        'all_time_rows': get_optimal_team(season_year=None, points_type='active'),
    }


def get_draft_board(season_year):
    """Return the season's draft board from mart_draft_board -- one row per
    pick -- with the value metric attached (draft tab).

    value_delta = overall_pick - points_rank, where points_rank is the
    player's rank by season_points within the season's drafted pool. A
    large positive value_delta is a steal (drafted late, produced like an
    early pick); a large negative is a bust (drafted early, underproduced).
    Rows ordered by overall_pick.
    """
    return query_snowflake("""
        SELECT
            overall_pick,
            round_num,
            round_pick,
            keeper,
            team_id,
            team_name,
            team_abbrev,
            owner_display,
            player_id,
            player_name,
            official_player_name,
            season_points,
            games_played,
            RANK() OVER (ORDER BY season_points DESC)               AS points_rank,
            overall_pick - RANK() OVER (ORDER BY season_points DESC) AS value_delta
        FROM mart_draft_board
        WHERE season_year = %s
        ORDER BY overall_pick
    """, (season_year,))


def get_team_standings(season_year):
    """Season-to-date team standings -- one row per team.

    Offense / defense / total are the calculated hitting / pitching / total
    points; `against_pts` is the calculated points conceded (the opponent's
    calculated total, summed). The record is the official platform `result`
    (W-L-T). Team labels come from the team's most-recent week so a mid-season
    rename can't surface a stale abbrev, and owner_display is the canonical
    nickname-resolved label. Ordered as a standings: record first, total
    points as the tiebreak.
    """
    return query_snowflake("""
        SELECT
            m.team_id,
            MAX_BY(m.team_abbrev, m.matchup_period)  AS team_abbrev,
            MAX_BY(m.team_name,   m.matchup_period)  AS team_name,
            MAX(tod.owner_display)                   AS owner_display,
            ROUND(SUM(m.calculated_hitting_pts),     1) AS offense_pts,
            ROUND(SUM(m.calculated_pitching_pts),    1) AS defense_pts,
            ROUND(SUM(m.calculated_points),          1) AS total_pts,
            ROUND(SUM(m.opponent_calculated_points), 1) AS against_pts,
            SUM(CASE WHEN m.result = 'W' THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN m.result = 'L' THEN 1 ELSE 0 END) AS losses,
            SUM(CASE WHEN m.result = 'T' THEN 1 ELSE 0 END) AS ties
        FROM mart_team_matchup m
        LEFT JOIN dim_team_owner tod
            ON m.season_year = tod.season_year
            AND m.team_id    = tod.team_id
        WHERE m.season_year = %s
        GROUP BY m.team_id
        ORDER BY wins DESC, ties DESC, total_pts DESC
    """, (season_year,))


def get_team_slot_points(season_year):
    """Season-to-date points produced at each lineup slot, per team.

    One row per (team_id, lineup_slot): the summed calculated points
    (total_stat_pts) the team's players generated while occupying that slot --
    every slot including BE / IL, so bench and injured production surface as
    their own slots ("how does this part of my lineup stack up to the league?").
    """
    return query_snowflake("""
        SELECT
            team_id,
            lineup_slot,
            ROUND(SUM(total_stat_pts), 1) AS slot_pts
        FROM fct_weekly_player_performance
        WHERE season_year = %s
        GROUP BY team_id, lineup_slot
    """, (season_year,))


def get_slot_capacities(season_year, matchup_period):
    """Return configured active roster slot counts for one season."""
    rows = query_snowflake("""
        SELECT
            lineup_slot,
            starter_count AS slots_to_fill
        FROM dim_roster_slot_counts
        WHERE season_year = %s
          AND is_active_lineup_slot
          AND starter_count > 0
        ORDER BY sort_order
    """, (season_year,))

    if not rows:
        raise RuntimeError(
            f"No roster slot counts found for {season_year}. "
            "Run extract.py --settings-only and dbt build first."
        )

    return {
        r['lineup_slot']: int(r['slots_to_fill'])
        for r in rows
        if r.get('lineup_slot') and r.get('slots_to_fill')
    }



def get_roster_slot_capacities(season_year, include_inactive=False):
    """Return configured roster slot counts."""
    inactive_filter = "" if include_inactive else "AND is_active_lineup_slot"
    rows = query_snowflake("""
        SELECT
            lineup_slot,
            starter_count AS slots_to_fill
        FROM dim_roster_slot_counts
        WHERE season_year = %s
          AND starter_count > 0
          {inactive_filter}
        ORDER BY sort_order
    """.format(inactive_filter=inactive_filter), (season_year,))

    if not rows:
        raise RuntimeError(
            f"No roster slot counts found for {season_year}. "
            "Run extract.py --settings-only and dbt build first."
        )

    return {
        r['lineup_slot']: int(r['slots_to_fill'])
        for r in rows
        if r.get('lineup_slot') and r.get('slots_to_fill')
    }



def get_team_roster_history_stats(season_year):
    """Return team/player roster history for current-season and all-time views."""
    player_rows = query_snowflake("""
        WITH latest_day AS (
            SELECT
                season_year,
                MAX(scoring_period) AS scoring_period
            FROM mart_daily_roster_snapshot
            WHERE season_year = %s
              AND team_id IS NOT NULL
            GROUP BY 1
        ),

        current_teams AS (
            SELECT
                d.team_id,
                d.team_name,
                d.team_abbrev,
                d.owner_display AS owner_name,
                d.matchup_period AS latest_matchup_period,
                d.scoring_period AS latest_scoring_period,
                m.end_date AS latest_matchup_end_date
            FROM mart_daily_roster_snapshot d
            INNER JOIN latest_day ld
                ON d.season_year = ld.season_year
                AND d.scoring_period = ld.scoring_period
            LEFT JOIN dim_matchup_period m
                ON d.season_year = m.season_year
                AND d.matchup_period = m.matchup_period
            WHERE d.team_id IS NOT NULL
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY d.team_id
                ORDER BY d.team_name
            ) = 1
        ),

        scoped_daily AS (
            SELECT 'current_season' AS scope, d.*
            FROM mart_daily_roster_snapshot d
            WHERE d.season_year = %s
              AND d.team_id IS NOT NULL

            UNION ALL

            SELECT 'all_time' AS scope, d.*
            FROM mart_daily_roster_snapshot d
            WHERE d.team_id IS NOT NULL
        ),

        player_context AS (
            SELECT
                scope,
                player_id,
                player_name,
                display_name,
                position,
                pro_team
            FROM scoped_daily
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY scope, player_id
                ORDER BY season_year DESC, scoring_period DESC
            ) = 1
        ),

        current_player_team AS (
            SELECT
                d.player_id,
                d.team_id AS current_fantasy_team_id,
                d.team_abbrev AS current_fantasy_team
            FROM mart_daily_roster_snapshot d
            INNER JOIN latest_day ld
                ON d.season_year = ld.season_year
                AND d.scoring_period = ld.scoring_period
            WHERE d.team_id IS NOT NULL
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY d.player_id
                ORDER BY d.team_name
            ) = 1
        ),

        active_slot_distinct AS (
            SELECT DISTINCT
                d.scope,
                d.team_id,
                d.player_id,
                d.lineup_slot,
                COALESCE(r.sort_order, 999) AS sort_order
            FROM scoped_daily d
            LEFT JOIN dim_roster_slot_counts r
                ON r.season_year = %s
                AND d.lineup_slot = r.lineup_slot
            WHERE d.roster_status = 'active'
        ),

        active_slot_list AS (
            SELECT
                scope,
                team_id,
                player_id,
                LISTAGG(lineup_slot, ', ') WITHIN GROUP (ORDER BY sort_order, lineup_slot)
                    AS active_slots_played
            FROM active_slot_distinct
            GROUP BY 1, 2, 3
        ),

        roster_totals AS (
            -- Roster-status counts only. Point totals (active + bench/IL)
            -- come from active_totals / inactive_totals below, sourced
            -- from fct_player_season_performance so the calculated lens
            -- (v1.1.1) is consistent with the optimal-team selection.
            SELECT
                scope,
                team_id,
                player_id,
                COUNT(DISTINCT season_year || '-' || scoring_period) AS rostered_days,
                COUNT(DISTINCT CASE
                    WHEN roster_status = 'active'
                        THEN season_year || '-' || scoring_period
                END) AS active_days,
                SUM(CASE
                    WHEN roster_status = 'active'
                        THEN COALESCE(games_played, 0)
                    ELSE 0
                END) AS active_games,
                COUNT(DISTINCT CASE
                    WHEN lineup_slot = 'BE'
                        THEN season_year || '-' || scoring_period
                END) AS bench_days,
                COUNT(DISTINCT CASE
                    WHEN lineup_slot = 'IL'
                        THEN season_year || '-' || scoring_period
                END) AS il_days
            FROM scoped_daily
            GROUP BY 1, 2, 3
        ),

        scoped_season AS (
            -- v1.1.1: read season-grain rollup from fct_player_season_performance
            -- (matchup_period already collapsed). performance_status filter
            -- pushed down into active_totals / inactive_totals so both lenses
            -- share one scan of the brick.
            SELECT 'current_season' AS scope, p.*
            FROM fct_player_season_performance p
            WHERE p.season_year = %s
              AND p.team_id IS NOT NULL

            UNION ALL

            SELECT 'all_time' AS scope, p.*
            FROM fct_player_season_performance p
            WHERE p.team_id IS NOT NULL
        ),

        active_totals AS (
            -- Calculated-lens active points (v1.1.1). Was platform_points;
            -- switched for consistency with the optimal-team primitive,
            -- which now answers "who would have done well in this league's
            -- current scoring setting" rather than "who happened to score
            -- well in the historical platform config."
            SELECT
                scope,
                team_id,
                player_id,
                ROUND(SUM(calculated_points), 1) AS active_points,
                -- v1.2: per-category active points so the per-team tab can
                -- show slot-decomposed points for two-way players (Ohtani
                -- gets hitting pts at his DH slot, pitching pts at SP).
                -- Single-discipline players: one equals active_points, the
                -- other is 0 -- so their displayed points don't move.
                ROUND(SUM(calculated_hitting_pts), 1) AS active_hitting_points,
                ROUND(SUM(calculated_pitching_pts), 1) AS active_pitching_points,
                SUM(h) AS h,
                SUM(ab) AS ab,
                SUM(b_bb) AS b_bb,
                SUM(hbp) AS hbp,
                SUM(sf) AS sf,
                SUM(tb) AS tb,
                SUM(hr) AS hr,
                SUM(sb) AS sb,
                SUM(w) AS w,
                SUM(l) AS l,
                SUM(sv) AS sv,
                SUM(er) AS er,
                SUM(outs) AS outs,
                SUM(k) AS k,
                SUM(p_bb) AS p_bb,
                SUM(p_h) AS p_h
            FROM scoped_season
            WHERE performance_status = 'active'
            GROUP BY 1, 2, 3
        ),

        inactive_totals AS (
            -- Calculated-lens bench/IL points (v1.1.1). Was sourced from
            -- mart_daily_roster_snapshot.platform_points in roster_totals;
            -- moved to fct_player_season_performance so both lenses match.
            -- Same Bench-sort framing Approach 1 uses: total rostered
            -- production = active + inactive, both calculated.
            SELECT
                scope,
                team_id,
                player_id,
                ROUND(SUM(calculated_points), 1) AS bench_il_points
            FROM scoped_season
            WHERE performance_status = 'inactive'
            GROUP BY 1, 2, 3
        )

        SELECT
            rt.scope,
            ct.team_id,
            ct.team_name,
            ct.team_abbrev,
            ct.owner_name,
            ct.latest_matchup_period,
            ct.latest_scoring_period,
            ct.latest_matchup_end_date,
            rt.player_id,
            pl.player_name,
            pl.display_name,
            pl.position,
            pl.pro_team,
            -- v1.1.1: asterisk when the player is still on this tab's
            -- team (was blank); abbrev when on a different team; blank
            -- when no longer rostered anywhere. Compact "still here" cue
            -- without restating the tab's own team abbrev.
            CASE
                WHEN cpt.current_fantasy_team_id = ct.team_id THEN '*'
                ELSE COALESCE(cpt.current_fantasy_team, '')
            END AS current_fantasy_team,
            COALESCE(asl.active_slots_played, '') AS active_slots_played,
            rt.rostered_days,
            rt.active_days,
            rt.active_games,
            rt.bench_days,
            rt.il_days,
            COALESCE(it.bench_il_points, 0) AS bench_il_points,
            COALESCE(at.active_points, 0) AS active_points,
            COALESCE(at.active_hitting_points, 0) AS active_hitting_points,
            COALESCE(at.active_pitching_points, 0) AS active_pitching_points,
            COALESCE(at.h, 0) AS h,
            COALESCE(at.ab, 0) AS ab,
            COALESCE(at.b_bb, 0) AS b_bb,
            COALESCE(at.hbp, 0) AS hbp,
            COALESCE(at.sf, 0) AS sf,
            COALESCE(at.tb, 0) AS tb,
            COALESCE(at.hr, 0) AS hr,
            COALESCE(at.sb, 0) AS sb,
            COALESCE(at.w, 0) AS w,
            COALESCE(at.l, 0) AS l,
            COALESCE(at.sv, 0) AS sv,
            COALESCE(at.er, 0) AS er,
            COALESCE(at.outs, 0) AS outs,
            COALESCE(at.k, 0) AS k,
            COALESCE(at.p_bb, 0) AS p_bb,
            COALESCE(at.p_h, 0) AS p_h
        FROM roster_totals rt
        INNER JOIN current_teams ct
            ON rt.team_id = ct.team_id
        LEFT JOIN player_context pl
            ON rt.scope = pl.scope
            AND rt.player_id = pl.player_id
        LEFT JOIN current_player_team cpt
            ON rt.player_id = cpt.player_id
        LEFT JOIN active_slot_list asl
            ON rt.scope = asl.scope
            AND rt.team_id = asl.team_id
            AND rt.player_id = asl.player_id
        LEFT JOIN active_totals at
            ON rt.scope = at.scope
            AND rt.team_id = at.team_id
            AND rt.player_id = at.player_id
        LEFT JOIN inactive_totals it
            ON rt.scope = it.scope
            AND rt.team_id = it.team_id
            AND rt.player_id = it.player_id
        ORDER BY ct.team_name, rt.scope, rt.rostered_days DESC, pl.display_name
    """, (season_year, season_year, season_year, season_year))

    # v1.1.1: the days-active-at-slot Starters selection (and its
    # active_points_in_slot tiebreak query) is gone -- Starters now come
    # from get_optimal_team via the per-team-tab consumer. The roster
    # history rows above carry everything Bench / IL / Other rendering
    # needs (active_points + bench_il_points + il_days + the stat tail),
    # so a single query is all that's left.
    if not player_rows:
        raise RuntimeError(f"No team roster history rows found for {season_year}.")

    return {'players': player_rows}



def get_current_team_roster_stats(season_year):
    """Return latest roster snapshot with season-to-date team/player stats."""
    rows = query_snowflake("""
        WITH latest_day AS (
            SELECT
                season_year,
                MAX(scoring_period) AS scoring_period
            FROM mart_daily_roster_snapshot
            WHERE season_year = %s
              AND team_id IS NOT NULL
            GROUP BY 1
        ),

        current_roster AS (
            SELECT
                d.season_year,
                d.matchup_period AS latest_matchup_period,
                d.scoring_period AS latest_scoring_period,
                d.team_id,
                d.team_name,
                d.team_abbrev,
                d.owner_display AS owner_name,
                d.player_id,
                d.player_name,
                d.display_name,
                d.position,
                d.pro_team,
                d.lineup_slot,
                d.slots_to_fill,
                d.slot_sort_order
            FROM mart_daily_roster_snapshot d
            INNER JOIN latest_day ld
                ON d.season_year = ld.season_year
                AND d.scoring_period = ld.scoring_period
            WHERE d.team_id IS NOT NULL
              AND d.lineup_slot <> 'FA'
        ),

        rostered AS (
            SELECT
                season_year,
                team_id,
                player_id,
                COUNT(DISTINCT scoring_period) AS rostered_days,
                COUNT(DISTINCT CASE
                    WHEN roster_status = 'active' THEN scoring_period
                END) AS active_days,
                ROUND(SUM(CASE
                    WHEN roster_status = 'inactive'
                        THEN COALESCE(platform_points, 0)
                    ELSE 0
                END), 1) AS inactive_points
            FROM mart_daily_roster_snapshot
            WHERE season_year = %s
              AND team_id IS NOT NULL
              AND lineup_slot <> 'FA'
            GROUP BY 1, 2, 3
        ),

        active AS (
            SELECT
                season_year,
                team_id,
                player_id,
                COUNT(DISTINCT matchup_period) AS active_weeks,
                ROUND(SUM(platform_points), 1) AS active_points,
                SUM(hr) AS hr,
                SUM(rbi) AS rbi,
                SUM(r) AS r,
                SUM(sb) AS sb,
                SUM(w) AS w,
                SUM(sv) AS sv,
                SUM(hld) AS hld,
                SUM(k) AS k,
                SUM(outs) AS outs
            FROM fct_weekly_player_active_performance
            WHERE season_year = %s
            GROUP BY 1, 2, 3
        )

        SELECT
            cr.*,
            ROW_NUMBER() OVER (
                PARTITION BY cr.team_id, cr.lineup_slot
                ORDER BY COALESCE(a.active_points, 0) DESC, cr.display_name
            ) AS slot_rank,
            COALESCE(r.rostered_days, 0) AS rostered_days,
            COALESCE(r.active_days, 0) AS active_days,
            COALESCE(r.inactive_points, 0) AS inactive_points,
            COALESCE(a.active_weeks, 0) AS active_weeks,
            COALESCE(a.active_points, 0) AS active_points,
            COALESCE(a.hr, 0) AS hr,
            COALESCE(a.rbi, 0) AS rbi,
            COALESCE(a.r, 0) AS r,
            COALESCE(a.sb, 0) AS sb,
            COALESCE(a.w, 0) AS w,
            COALESCE(a.sv, 0) AS sv,
            COALESCE(a.hld, 0) AS hld,
            COALESCE(a.k, 0) AS k,
            COALESCE(a.outs, 0) AS outs
        FROM current_roster cr
        LEFT JOIN rostered r
            ON cr.season_year = r.season_year
            AND cr.team_id = r.team_id
            AND cr.player_id = r.player_id
        LEFT JOIN active a
            ON cr.season_year = a.season_year
            AND cr.team_id = a.team_id
            AND cr.player_id = a.player_id
        ORDER BY cr.team_name, cr.slot_sort_order, slot_rank
    """, (season_year, season_year, season_year))

    if not rows:
        raise RuntimeError(f"No current roster rows found for {season_year}.")
    return rows



def get_almanac_records(scope):
    """Return rank-one record rows for the almanac Records tab."""
    rows = query_snowflake("""
        SELECT
            entity_grain,
            stat_name,
            record_direction,
            rank,
            season_year,
            matchup_period,
            team_id,
            team_name,
            team_abbrev,
            -- v1.3: canonical owner_display off the leaderboard mart
            -- (resolved upstream; no per-query COALESCE).
            owner_display AS owner_name,
            player_id,
            player_name,
            display_name,
            stat_value
        FROM mart_stat_leaderboard
        WHERE record_scope = %s
          AND rank <= 10
          AND performance_status = 'active'
        ORDER BY entity_grain, stat_name, record_direction, rank
    """, (scope,))

    collapsed = records.collapse_ties(
        rows,
        max_n=1,
        count_fn=_almanac_tie_counter(scope),
    )
    collapsed.extend(get_rate_records(scope))
    collapsed.extend(get_lineup_slot_records(scope))
    # Lazy import to dodge module-load circular (data <-> logic).
    # _attach_almanac_contributors lives in almanac_logic.py post-split;
    # retargeted away from the almanac_sheets facade in v1.1.1.
    from almanac_logic import _attach_almanac_contributors
    _attach_almanac_contributors(collapsed)
    return collapsed



def _almanac_tie_counter(scope):
    """Build a scope-aware count function for saturated tied record tiers."""
    def count_fn(grain, stat_name, value):
        return count_value_occurrences_for_scope(scope, grain, stat_name, value)
    return count_fn



def count_value_occurrences_for_scope(scope, grain, stat_name, value):
    """Count tied values in the same scope as the displayed leaderboard side."""
    if scope == 'all_time':
        return records.count_value_occurrences(grain, stat_name, value)
    if scope != 'current_season':
        raise ValueError(f"Unsupported record scope: {scope!r}")
    if stat_name in {'WASTED_POINTS', 'ERA', 'WHIP', 'K_PER_9', 'K_PER_BB',
                     'HR_PER_9', 'BB_PER_9'}:
        return None

    col_expr = {
        'PA': 'ab + b_bb + hbp + sf',
        'SB_CS': 'sb - cs',
        'W_L': 'w - l',
        'SV_BLSV': 'sv - blsv',
    }.get(stat_name, stat_name.lower())
    fct = ('fct_weekly_team_active_performance' if grain == 'team'
           else 'fct_weekly_player_active_performance')
    rows = query_snowflake(f"""
        SELECT COUNT(*) AS n
        FROM {fct}
        WHERE is_abnormal = false
          AND season_year = (
              SELECT MAX(season_year)
              FROM fct_weekly_team_active_performance
          )
          AND ({col_expr}) = %s
    """, (value,))
    return rows[0]['n'] if rows else 0



def get_rate_records(scope):
    """Return almanac-only rate records not fully covered by the mart."""
    rows = []
    for spec in RATE_RECORD_SPECS:
        rows.extend(_get_rate_record_rows(scope, spec))
    return rows



def get_lineup_slot_records(scope):
    """Return best active player-week score records by roster-shaped slot."""
    season_filter = ""
    if scope == 'current_season':
        season_filter = """
          AND p.season_year = (
              SELECT MAX(season_year)
              FROM fct_weekly_team_active_performance
          )
        """
    elif scope != 'all_time':
        raise ValueError(f"Unsupported record scope: {scope!r}")

    rows = query_snowflake(f"""
        WITH current_slots AS (
            SELECT lineup_slot, starter_count
            FROM dim_roster_slot_counts
            WHERE season_year = (
                SELECT MAX(season_year)
                FROM dim_roster_slot_counts
            )
              AND is_active_lineup_slot
              AND starter_count > 0
        )

        SELECT
            'player' AS entity_grain,
            'LINEUP_SLOT_POINTS__' || lineup_slot || '__' || rank AS stat_name,
            'most' AS record_direction,
            rank,
            season_year,
            matchup_period,
            team_id,
            team_name,
            team_abbrev,
            owner_name,
            player_id,
            player_name,
            display_name,
            calculated_points AS stat_value,
            lineup_slot,
            slots_to_fill
        FROM (
            SELECT
                p.season_year,
                p.matchup_period,
                p.team_id,
                p.team_name,
                p.team_abbrev,
                -- v1.3: read the canonical owner_display (resolved once
                -- upstream in fct_weekly_player_performance) -- no COALESCE.
                p.owner_display AS owner_name,
                p.player_id,
                p.player_name,
                p.display_name,
                p.lineup_slot,
                c.starter_count AS slots_to_fill,
                ROUND(p.total_stat_pts, 1) AS calculated_points,
                ROW_NUMBER() OVER (
                    PARTITION BY p.lineup_slot
                    ORDER BY ROUND(p.total_stat_pts, 1) DESC,
                             p.season_year DESC,
                             p.matchup_period DESC,
                             p.team_id,
                             p.player_id
                ) AS rank
            FROM fct_weekly_player_performance p
            LEFT JOIN dim_matchup_period m
                ON p.season_year = m.season_year
                AND p.matchup_period = m.matchup_period
            INNER JOIN current_slots c
                ON p.lineup_slot = c.lineup_slot
            WHERE p.performance_status = 'active'
              AND p.team_id IS NOT NULL
              AND m.is_abnormal = false
              AND p.total_stat_pts IS NOT NULL
              {season_filter}
        )
        WHERE rank <= slots_to_fill
        ORDER BY lineup_slot, rank
    """)

    return rows



def _get_rate_record_rows(scope, spec):
    """Read rate-stat records from mart_stat_leaderboard.

    v1.1.1: previously bypassed the mart and re-ranked off
    fct_weekly_team_active_performance with a Python-constant threshold
    filter. The mart now applies the qualifier_min threshold from dim_stat
    natively, so this function just reads top-10 records and JOINs back
    to the team fact for the qualifier_value display column (AB count,
    OUTS count) which the mart doesn't carry through UNPIVOT.
    """
    if scope not in ('current_season', 'all_time'):
        raise ValueError(f"Unsupported record scope: {scope!r}")

    qualifier_col = spec['min_column']  # 'ab' (hitting) or 'outs' (pitching)
    if qualifier_col not in ('ab', 'outs'):
        raise ValueError(f"Unsupported qualifier column: {qualifier_col!r}")

    rows = query_snowflake(f"""
        SELECT
            l.entity_grain,
            l.stat_name,
            l.record_direction,
            l.rank,
            l.season_year,
            l.matchup_period,
            l.team_id,
            l.team_name,
            l.team_abbrev,
            l.owner_display AS owner_name,
            NULL::integer AS player_id,
            NULL::varchar AS player_name,
            NULL::varchar AS display_name,
            l.stat_value,
            t.{qualifier_col} AS qualifier_value
        FROM mart_stat_leaderboard l
        LEFT JOIN fct_weekly_team_active_performance t
            ON l.season_year    = t.season_year
            AND l.matchup_period = t.matchup_period
            AND l.team_id        = t.team_id
        WHERE l.entity_grain       = 'team'
          AND l.performance_status = 'active'
          AND l.stat_name          = %s
          AND l.record_direction   = %s
          AND l.record_scope       = %s
          AND l.rank <= 10
        ORDER BY l.rank
    """, (spec['stat_name'], spec['direction'], scope))

    return records.collapse_ties(rows, max_n=1, count_fn=None)



def get_wasted_points_records(scope):
    """Return wasted-points records, which live at inactive-team grain."""
    rows = query_snowflake("""
        SELECT
            entity_grain,
            stat_name,
            record_direction,
            rank,
            season_year,
            matchup_period,
            team_id,
            team_name,
            team_abbrev,
            owner_display AS owner_name,
            player_id,
            player_name,
            display_name,
            stat_value
        FROM mart_stat_leaderboard
        WHERE record_scope = %s
          AND entity_grain = 'team'
          AND stat_name = 'WASTED_POINTS'
          AND record_direction = 'most'
          AND rank = 1
          AND performance_status = 'inactive'
    """, (scope,))

    for row in rows:
        row['contributors'] = []
    return rows



def get_scored_record_specs():
    """Return best-only team specs for scored and auto-tracked stats."""
    rows = query_snowflake("""
        SELECT DISTINCT
            d.leaderboard_name AS stat_name,
            d.display_name,
            d.stat_category,
            d.polarity,
            d.auto_tracked
        FROM dim_stat d
        LEFT JOIN stg_scoring_settings s
            ON s.stat_name = d.stat_name
        WHERE d.stat_category IN ('hitting', 'pitching', 'fielding')
          AND d.is_record_candidate
          AND (s.stat_name IS NOT NULL OR d.auto_tracked)
          AND d.leaderboard_name NOT IN ('AVG', 'OBP', 'SLG', 'ERA', 'K_PER_9', 'WHIP')
        ORDER BY
            CASE d.stat_category
                WHEN 'hitting' THEN 1
                WHEN 'pitching' THEN 2
                WHEN 'fielding' THEN 3
                ELSE 4
            END,
            d.display_name
    """)
    return build_scored_record_specs(rows)



def get_lineup_slot_record_specs():
    """Return active lineup-slot point record specs for the current league."""
    rows = query_snowflake("""
        SELECT lineup_slot, starter_count AS slots_to_fill
        FROM dim_roster_slot_counts
        WHERE season_year = (
            SELECT MAX(season_year)
            FROM dim_roster_slot_counts
        )
          AND is_active_lineup_slot
          AND starter_count > 0
        ORDER BY sort_order
    """)
    return [
        {
            'section': 'Lineup Slot Records',
            'label': slot_label(row['lineup_slot'], slot_rank, int(row['slots_to_fill'])),
            'grain': 'player',
            'stat_name': _lineup_slot_stat_name(row['lineup_slot'], slot_rank),
            'direction': 'most',
        }
        for row in rows
        if row.get('lineup_slot')
        for slot_rank in range(1, int(row['slots_to_fill']) + 1)
    ]



def _lineup_slot_stat_name(slot, slot_rank):
    return f"LINEUP_SLOT_POINTS__{slot}__{slot_rank}"


def build_scored_record_specs(scored_stats):
    """Convert scored stat metadata into sectioned best-record specs."""
    specs = []
    for row in sorted(scored_stats, key=_scored_record_sort_key):
        category = row.get('stat_category')
        stat_name = row.get('stat_name')
        if not category or not stat_name:
            continue

        polarity = row.get('polarity')
        direction = 'fewest' if polarity == 'negative' else 'most'
        section = {
            'hitting': 'Team Hitting Records',
            'pitching': 'Team Pitching Records',
            'fielding': 'Team Fielding Records',
        }.get(category, 'Scored Stat Records')
        label = _team_record_label(row)
        specs.append({
            'section': section,
            'label': label,
            'grain': 'team',
            'stat_name': stat_name,
            'direction': direction,
        })
    return specs


def _scored_record_sort_key(row):
    category = row.get('stat_category')
    stat_name = row.get('stat_name')
    category_order = {'hitting': 1, 'pitching': 2, 'fielding': 3}.get(category, 4)
    if category == 'hitting':
        stat_order = HITTING_RECORD_ORDER.get(stat_name, 999)
    else:
        stat_order = 999
    return (
        category_order,
        stat_order,
        row.get('display_name') or stat_name or '',
    )



def _team_record_label(row):
    stat_name = row.get('stat_name')
    if row.get('stat_category') == 'hitting':
        return HITTING_RECORD_LABELS.get(stat_name, row.get('display_name') or stat_name)
    return row.get('display_name') or stat_name


def _fact_stat_column_name(stat_name):
    special = {
        'OUTS': 'outs',
    }
    return special.get(stat_name, str(stat_name or '').lower())


def slot_label(slot, slot_rank, slots_to_fill):
    """Display repeated roster slots as OF 1 / OF 2, etc."""
    if slots_to_fill and slots_to_fill > 1:
        return f"{slot} {slot_rank}"
    return slot


# v1.1.1 Tier 2c.1: spec-sorting helpers + the pitching display order they
# reference are data-shape concerns -- they participate in producing
# spec dicts that downstream consumers read. Moved here so the data
# module is internally complete.

PITCHING_STAT_ORDER = {
    stat_name: index
    for index, stat_name in enumerate([
        'OUTS',
        'K',
        'QS',
        'W',
        'L',
        'SV',
        'HLD',
        'ER',
        'P_H',
        'P_BB',
        'P_HR',
        'P_R',
        'CG',
        'SHO',
        'BLSV',
        'HBP_P',
        'BLK',
        'WP',
        'PK',
        'NH',
        'PG',
    ])
}


def _team_week_stat_sort_key(row):
    category = row.get('stat_category')
    stat_name = row.get('stat_name')
    category_order = {'hitting': 1, 'pitching': 2}.get(category, 3)
    if category == 'hitting':
        stat_order = HITTING_RECORD_ORDER.get(stat_name, 999)
    else:
        stat_order = PITCHING_STAT_ORDER.get(stat_name, 999)
    return (
        category_order,
        stat_order,
        row.get('display_name') or stat_name or '',
    )


def _team_week_good_record_direction(spec):
    points = spec.get('points_per_unit')
    if points is None:
        return None
    try:
        points = float(points)
    except (TypeError, ValueError):
        return None
    if points > 0:
        return 'most'
    if points < 0:
        return 'fewest'
    return None
