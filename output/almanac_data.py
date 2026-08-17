"""output/almanac_data.py

Tier 2c.1 (v1.1.1): SQL data-access surface of the league almanac.

Every function in this module issues at least one Snowflake query via
`query_for_presentation` -- the raw seam PLUS the owner-label fallback,
because everything here is read to be rendered (MLB-243; see db.py for why
that is opt-in) -- or wraps a project-level data module like `records`,
with one exception: the MLB-103 Trades-tab fetches read the live ESPN
league API at build time -- the trading block is ephemeral current-state
and the executed-trade ledger lives only in the communication feed, so
neither has a warehouse landing.
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

import json
import os
import re
from collections import defaultdict
from datetime import datetime

import requests

from db import latest_by, league_predicate, listagg, query_for_presentation
from formatters import TOP_SCORER_STAT_DISPLAY
import records
import slot_catalog


# Affinity-chart club sentinel (MLB-159). ESPN records only a player's
# CURRENT club, so involvement from a backfilled season carries no usable
# club-of-game for anyone who has since moved -- it used to be dropped from
# the chart, and now buckets here. Deliberately NOT an ESPN abbrev (those
# are short and mixed-case: 'Ari', 'ChC', 'KC'), so it cannot collide with
# a real club, and if it ever reached a surface unmapped it would read as
# an obvious sentinel rather than as a team. The display name lives with
# the render vocabulary; see ESPN_UNATTRIBUTED_CLUB in almanac_render.
AFFINITY_UNATTRIBUTED = 'UNATTRIBUTED'

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
    rows = query_for_presentation(f"""
        SELECT season_year, matchup_period
        FROM fct_team_weekly_active_performance
        WHERE {league_predicate()}
        QUALIFY ROW_NUMBER() OVER (
            ORDER BY season_year DESC, matchup_period DESC
        ) = 1
    """)
    if not rows:
        raise RuntimeError("No team-week rows found; run dbt build first.")
    return rows[0]['season_year'], rows[0]['matchup_period']



def get_team_week_stat_specs():
    """Return scored hitting/pitching stat columns for the Team Weeks tab."""
    rows = query_for_presentation(f"""
        SELECT
            d.leaderboard_name AS stat_name,
            d.display_name,
            d.abbrev,
            d.stat_category,
            s.points_per_unit
        FROM dim_stat d
        -- stg_scoring_settings is per-league (each league's own weights);
        -- dim_stat is the platform stat vocabulary and stays unscoped.
        INNER JOIN stg_scoring_settings s
            ON s.stat_name = d.stat_name
            AND {league_predicate('s')}
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
    rows = query_for_presentation(f"""
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
            -- MLB-235: the gate the record/format decisions read.
            -- is_abnormal rides along for display only.
            is_record_eligible,
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
        WHERE {league_predicate()}
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
                FROM fct_team_weekly_active_performance
                WHERE opponent_id IS NOT NULL
                  AND is_record_eligible
                  AND {column} IS NOT NULL
                  AND {league_predicate()}
            )
            WHERE stat_value = record_value
            GROUP BY record_value
        """)

    rows = query_for_presentation("\nUNION ALL\n".join(selects))
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
    candidates from fct_player_weekly_slot_performance + a simpler fill-by-
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


_VALID_POINTS_TYPES = ('active', 'inactive', 'all', 'weighted_active', 'rostered')


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
    elif points_type == 'weighted_active':
        # The MLB-72 cross-era active lens: identical to 'active' where
        # the state is known (ESPN; CBS captured/reconstructed eras),
        # start-share-weighted on CBS's estimated era. The CBS almanac's
        # Best Lineup axis.
        points_expr = 'sum(weighted_active_pts)'
    elif points_type == 'rostered':
        # The weight-independent total: everything produced while
        # rostered. For ESPN rows this equals 'all' exactly (active +
        # inactive); for CBS it additionally counts the estimated era's
        # production, which the binary lenses can't see -- the honest
        # "Total-Pts Best" comparison lens across the union.
        points_expr = 'sum(rostered_pts)'
    else:  # 'all'
        points_expr = 'sum(active_pts + inactive_pts)'

    where_clauses = [league_predicate()]
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
    where_sql = ' AND '.join(where_clauses)

    rows = query_for_presentation(f"""
        SELECT
            -- player_key is the grain (MLB-72): 1:1 with player_id on ESPN
            -- rows; the only identity on CBS ui-only synthetics (whose
            -- player_id is NULL and would otherwise merge distinct players).
            player_key,
            MAX(player_id)    AS player_id,
            MAX(player_name)  AS player_name,
            MAX(display_name) AS display_name,
            -- Club label: LATEST in whatever scope the WHERE clause
            -- pinned, not the alphabetical maximum (MLB-168). This
            -- group spans every season and period the filters allow,
            -- so once the club label is game-accurate a traded player
            -- carries several -- and MAX() would have picked between
            -- them by collation. The composite sort key is safe
            -- because matchup_period tops out at 26 on ESPN and is
            -- NULL on CBS (one season-grain row there, so season_year
            -- alone orders it).
            {latest_by('pro_team',
                       'season_year * 1000 + COALESCE(matchup_period, 0)')}
                              AS pro_team,
            position,
            ROUND({points_expr}, 1) AS position_pts
        FROM fct_player_position_pts
        WHERE {where_sql}
        GROUP BY player_key, position
        HAVING {points_expr} > 0
        -- player_id first keeps the ESPN tie-break byte-identical (numeric
        -- order, not the stringified key's); player_key settles CBS rows
        -- whose player_id is NULL.
        ORDER BY position, position_pts DESC, player_id, player_key
    """, params)

    return rows


def get_optimal_season_candidates(team_id):
    """Per-(player, SEASON, position) candidate pool for the Best
    Individual Seasons lineup (Kyle 2026-07-17): position-eligible
    active points per season for players on this team that season.
    weighted_active is the cross-era lens -- identical to 'active'
    where lineup states are known (all ESPN rows), start-share-weighted
    on CBS's estimated era -- so one query serves both leagues. The
    block builder synthesizes key|season candidate ids so the shared
    selector can reuse a player across slots while burning each
    player-season once."""
    return query_for_presentation(f"""
        SELECT
            player_key,
            MAX(player_id)    AS player_id,
            MAX(player_name)  AS player_name,
            MAX(display_name) AS display_name,
            -- Season-grain group, so the club label is the latest one
            -- WITHIN that season (MLB-168). CBS aggregates to a single
            -- season row with matchup_period NULL, which the COALESCE
            -- keeps orderable.
            {latest_by('pro_team', 'COALESCE(matchup_period, 0)')}
                              AS pro_team,
            position,
            season_year,
            ROUND(CAST(SUM(CAST(weighted_active_pts AS DECIMAL(18, 6))) AS DOUBLE), 1) AS position_pts
        FROM fct_player_position_pts
        WHERE {league_predicate()} AND team_id = %s
        GROUP BY player_key, position, season_year
        HAVING CAST(SUM(CAST(weighted_active_pts AS DECIMAL(18, 6))) AS DOUBLE) > 0
        ORDER BY position, position_pts DESC, player_id, player_key,
                 season_year
    """, (team_id,))


def get_team_player_season_stats():
    """ESPN display stats at (team, player, SEASON) grain for the Best
    Individual Seasons block -- the same fields the team-history rows
    carry (active/inactive points, hitting/pitching split, games, days,
    stat tail, current fantasy team), one query for all teams. The CBS
    almanac builds its equivalent in get_cbs_team_history_data."""
    return query_for_presentation(f"""
        WITH latest_day AS (
            SELECT season_year, MAX(scoring_period) AS scoring_period
            FROM mart_daily_roster_snapshot
            WHERE team_id IS NOT NULL AND {league_predicate()}
            GROUP BY 1
            QUALIFY ROW_NUMBER() OVER (ORDER BY season_year DESC) = 1
        ),

        roster AS (
            SELECT
                team_id,
                player_id,
                season_year,
                COUNT(DISTINCT scoring_period) AS rostered_days,
                SUM(CASE WHEN roster_status = 'active'
                        THEN COALESCE(games_played, 0) ELSE 0
                END) AS active_games
            FROM mart_daily_roster_snapshot
            WHERE team_id IS NOT NULL AND {league_predicate()}
            GROUP BY 1, 2, 3
        ),

        player_context AS (
            -- pro_team comes off the latest day that HAS a club, not off
            -- the picked row (MLB-159 Exit 1, swept MLB-168). The label
            -- is game-accurate now, so it is NULL on every day a player
            -- did not appear; reading it from the rn=1 row blanks the
            -- club for anyone whose last rostered day was a rest day.
            -- The row choice is deliberately left alone so name and
            -- position keep coming from that same latest row -- only the
            -- label moves. `season_year * 1000 + scoring_period` is safe
            -- as one ordering key because this mart is ESPN-only and its
            -- scoring_period tops out at 195.
            SELECT player_id, player_name, display_name, position, pro_team
            FROM (
                SELECT player_id, player_name, display_name, position,
                       {latest_by('pro_team',
                                  'season_year * 1000 + scoring_period',
                                  'player_id')} AS pro_team,
                       ROW_NUMBER() OVER (
                           PARTITION BY player_id
                           ORDER BY season_year DESC, scoring_period DESC
                       ) AS rn
                FROM mart_daily_roster_snapshot
                WHERE team_id IS NOT NULL AND {league_predicate()}
            )
            WHERE rn = 1
        ),

        current_player_team AS (
            SELECT d.player_id, d.team_id AS current_fantasy_team_id,
                   d.team_abbrev AS current_fantasy_team
            FROM mart_daily_roster_snapshot d
            INNER JOIN latest_day ld
                ON d.season_year = ld.season_year
                AND d.scoring_period = ld.scoring_period
            WHERE d.team_id IS NOT NULL AND {league_predicate('d')}
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY d.player_id ORDER BY d.team_name
            ) = 1
        ),

        season_active AS (
            SELECT
                team_id, player_id, season_year,
                -- MLB-123: 6dp, round once at display (mirrors MLB-121 and the
                -- team-history query). These season rows feed the same shared
                -- renderer that sums active+bench and re-rounds for display.
                ROUND(CAST(SUM(CAST(calculated_points AS DECIMAL(18, 6))) AS DOUBLE), 6)      AS active_points,
                ROUND(CAST(SUM(CAST(calculated_hitting_pts AS DECIMAL(18, 6))) AS DOUBLE), 6) AS active_hitting_points,
                ROUND(CAST(SUM(CAST(calculated_pitching_pts AS DECIMAL(18, 6))) AS DOUBLE), 6) AS active_pitching_points,
                SUM(h) AS h, SUM(ab) AS ab, SUM(b_bb) AS b_bb,
                SUM(hbp) AS hbp, SUM(sf) AS sf, SUM(tb) AS tb,
                SUM(hr) AS hr, SUM(sb) AS sb, SUM(w) AS w, SUM(l) AS l,
                SUM(sv) AS sv, SUM(er) AS er, SUM(outs) AS outs,
                SUM(k) AS k, SUM(p_bb) AS p_bb, SUM(p_h) AS p_h
            FROM fct_player_season_performance
            WHERE team_id IS NOT NULL AND {league_predicate()}
              AND performance_status = 'active'
            GROUP BY 1, 2, 3
        ),

        season_inactive AS (
            SELECT team_id, player_id, season_year,
                   ROUND(CAST(SUM(CAST(calculated_points AS DECIMAL(18, 6))) AS DOUBLE), 6) AS bench_il_points
            FROM fct_player_season_performance
            WHERE team_id IS NOT NULL AND {league_predicate()}
              AND performance_status = 'inactive'
            GROUP BY 1, 2, 3
        )

        SELECT
            r.team_id,
            r.player_id,
            r.season_year,
            pl.player_name,
            pl.display_name,
            pl.position,
            pl.pro_team,
            CASE
                WHEN cpt.current_fantasy_team_id = r.team_id THEN '*'
                ELSE COALESCE(cpt.current_fantasy_team, '')
            END AS current_fantasy_team,
            r.rostered_days,
            r.active_games,
            COALESCE(sa.active_points, 0)           AS active_points,
            COALESCE(sa.active_hitting_points, 0)   AS active_hitting_points,
            COALESCE(sa.active_pitching_points, 0)  AS active_pitching_points,
            COALESCE(si.bench_il_points, 0)         AS bench_il_points,
            COALESCE(sa.h, 0) AS h, COALESCE(sa.ab, 0) AS ab,
            COALESCE(sa.b_bb, 0) AS b_bb, COALESCE(sa.hbp, 0) AS hbp,
            COALESCE(sa.sf, 0) AS sf, COALESCE(sa.tb, 0) AS tb,
            COALESCE(sa.hr, 0) AS hr, COALESCE(sa.sb, 0) AS sb,
            COALESCE(sa.w, 0) AS w, COALESCE(sa.l, 0) AS l,
            COALESCE(sa.sv, 0) AS sv, COALESCE(sa.er, 0) AS er,
            COALESCE(sa.outs, 0) AS outs, COALESCE(sa.k, 0) AS k,
            COALESCE(sa.p_bb, 0) AS p_bb, COALESCE(sa.p_h, 0) AS p_h
        FROM roster r
        LEFT JOIN player_context pl ON r.player_id = pl.player_id
        LEFT JOIN current_player_team cpt ON r.player_id = cpt.player_id
        LEFT JOIN season_active sa
            ON r.team_id = sa.team_id AND r.player_id = sa.player_id
            AND r.season_year = sa.season_year
        LEFT JOIN season_inactive si
            ON r.team_id = si.team_id AND r.player_id = si.player_id
            AND r.season_year = si.season_year
        ORDER BY r.team_id, r.season_year, r.player_id
    """)


def team_best_seasons_fn():
    """Factory for build_team_history_tabs' best_seasons_fn (ESPN): the
    season-grain display stats fetch once for all teams, per-team
    candidates lazily."""
    from collections import defaultdict
    by_team = defaultdict(list)
    for row in get_team_player_season_stats():
        by_team[row['team_id']].append(row)

    def fn(team_id):
        return {
            'candidates': get_optimal_season_candidates(team_id),
            'seasons': by_team.get(team_id, []),
        }
    return fn


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

    Runs a single SUM aggregation over fct_player_weekly_slot_performance
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

    where_clauses = [league_predicate()]
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
        f'SUM({col}) AS {col}, CAST(SUM(CAST({col}_pts AS DECIMAL(18, 6))) AS DOUBLE) AS {col}_pts'
        for col in _OPTIMAL_TEAM_STAT_COLUMNS
    )

    rows = query_for_presentation(f"""
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
            FROM fct_player_weekly_slot_performance
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


def get_service_years(player_ids, team_id=None):
    """Per player_id, the seasons with qualifying active production as the
    LISTAGG string "2024,2025,2026" -- the fact-backed Years of Service
    definition (Kyle 2026-07-15/16, mirrored from the CBS
    get_years_of_service and the team-history service_seasons CTE): a
    season counts when the player was actively started and his active
    calculated points for the season are nonzero at 1dp, net-NEGATIVE
    seasons INCLUDED (a bad season is still service). League-wide by
    default; team_id scopes it to one franchise the way the team pages do.
    Empty dict for no ids; a player with no qualifying season is absent.
    """
    player_ids = [pid for pid in (player_ids or []) if pid is not None]
    if not player_ids:
        return {}
    where = [league_predicate(), 'team_id IS NOT NULL',
             "performance_status = 'active'"]
    params = []
    if team_id is not None:
        where.append('team_id = %s')
        params.append(team_id)
    where.append(f"player_id IN ({', '.join(['%s'] * len(player_ids))})")
    params.extend(player_ids)
    rows = query_for_presentation(f"""
        SELECT
            player_id,
            {listagg('CAST(season_year AS VARCHAR)', ',', 'season_year')}
                AS service_years
        FROM (
            SELECT player_id, season_year
            FROM fct_player_season_performance
            WHERE {' AND '.join(where)}
            GROUP BY player_id, season_year
            HAVING ROUND(CAST(SUM(CAST(calculated_points AS DECIMAL(18, 6))) AS DOUBLE), 1) <> 0
        )
        GROUP BY player_id
    """, params)
    return {r['player_id']: r['service_years'] for r in rows}


def get_home_all_time_team():
    """The Home All-Time board's lineup, one definition for every format:
    the all-time active All-League Team (get_optimal_team, latest roster
    shape, most-recent-stint team + canonical owner already attached by
    _enrich_optimal_team_with_stats) with each pick's fact-backed
    `service_years` attached for the Years of Service column. Both the
    H2H get_home_tab_data and the season-points home_boards read this, so
    the two books cannot disagree on what the all-time team is."""
    lineup = get_optimal_team(season_year=None, points_type='active')
    years = get_service_years([row.get('player_id') for row in lineup])
    for row in lineup:
        row['service_years'] = years.get(row.get('player_id'), '')
    return lineup


def get_first_season():
    """The earliest season on file for the league (measured from the team
    season fact, the same source the season-points context reads), or
    None when nothing is loaded. Titles the Home All-Time board's era."""
    rows = query_for_presentation(f"""
        SELECT MIN(season_year) AS first_season
        FROM fct_team_season_performance
        WHERE {league_predicate()}
    """)
    value = rows[0]['first_season'] if rows else None
    return int(value) if value is not None else None


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
      all_time_rows               -- all-time active All-League Team with
                                     service_years (the full-width board
                                     under Season-to-Date)
      first_season                -- earliest season on file, for the
                                     all-time board's measured era
    """
    return {
        'weekly_rows': get_all_league_team(season_year, matchup_period),
        'season_rows': get_all_league_team(season_year),
        'weekly_all_rows': get_optimal_team(
            season_year, matchup_period, points_type='all',
        ),
        'season_all_rows': get_optimal_team(season_year, points_type='all'),
        'all_time_rows': get_home_all_time_team(),
        'first_season': get_first_season(),
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
    return query_for_presentation(f"""
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
          AND {league_predicate()}
        ORDER BY overall_pick
    """, (season_year,))


def get_draft_history_boards(through_season):
    """Every season's draft picks for the all-time board -- one row per
    pick (season_year <= through_season, so the ONGOING season is
    included and paced up by season_pace_factors), same season-points
    lens as get_draft_board. Carries team_abbrev so the re-cut board can
    name each round's straight top pick. Rows ordered (season, pick).
    """
    return query_for_presentation(f"""
        SELECT
            season_year,
            overall_pick,
            round_num,
            round_pick,
            keeper,
            team_id,
            team_abbrev,
            player_name,
            official_player_name,
            season_points
        FROM mart_draft_board
        WHERE season_year <= %s
          AND {league_predicate()}
        ORDER BY season_year, overall_pick
    """, (through_season,))


def get_season_scoring_periods():
    """Per-season 'clock': distinct scoring periods each season reached --
    ESPN's day-grain scoring_period from the union daily fact (~184 in a
    full season, fewer in the year in flight). Feeds season_pace_factors
    so a partial ongoing season is scaled to a full-season equivalent,
    the same standard-season-clock idea as the CBS gameplay-days weight.
    """
    return query_for_presentation(f"""
        SELECT season_year, COUNT(DISTINCT scoring_period) AS clock
        FROM fct_player_daily_performance
        WHERE {league_predicate()}
        GROUP BY season_year
    """)


def get_team_standings(season_year, stat_specs):
    """Season team standings from mart_team_season_standings -- one row per
    team (regular season only; the mart excludes playoff weeks).

    v2.0: the aggregation moved into the mart; this selects the fixed
    standings columns (record, calculated score lenses, points conceded,
    per-week normalization denominators) plus the dynamic per-stat counting
    columns the scored stat specs request -- the same spec-driven column
    pattern get_team_weeks uses.

    ORDERED BY THE PLATFORM'S OWN SEED, NOT BY RECORD (MLB-227). ESPN seeds
    division winners ahead of the field, so no ordering over wins/points
    reproduces its standings -- see stg_team_standings for the worked
    counterexample. The record sort this used to carry disagrees with the
    platform in both captured seasons, at different division counts. The
    record columns survive only as a tiebreak behind the seed, and they
    engage solely for a season the capture does not cover.

    THE SEED IS AS FRESH AS THE LAST SETTINGS EXTRACT. ESPN serves it on the
    mSettings/mTeam payload, which extract.py writes only under
    --include-settings / --settings-only, so a box-score-only weekly pull
    advances the records without advancing the seed. When the two disagree
    this table renders the platform's older order beside the warehouse's
    newer record.
    """
    if not stat_specs:
        raise RuntimeError("No scored standings stat specs found.")

    stat_columns = [_fact_stat_column_name(spec['stat_name']) for spec in stat_specs]
    for column in stat_columns:
        if not re.match(r'^[a-z][a-z0-9_]*$', column):
            raise ValueError(f"Unsafe stat column name: {column!r}")

    stat_select = ',\n            '.join(f'm.{c}' for c in stat_columns)
    return query_for_presentation(f"""
        SELECT
            m.team_id,
            m.team_abbrev,
            m.team_name,
            m.owner_display,
            m.wins,
            m.losses,
            m.ties,
            m.matchup_periods_played,
            m.scoring_days_played,
            m.standard_matchup_days,
            m.calculated_hitting_pts,
            m.calculated_pitching_pts,
            m.calculated_points,
            m.against_calculated_points,
            {stat_select}
        FROM mart_team_season_standings m
        LEFT JOIN dim_team_season_standing s
            ON  s.league_key  = m.league_key
            AND s.season_year = m.season_year
            AND s.team_id     = m.team_id
        WHERE m.season_year = %s
          AND {league_predicate('m')}
        ORDER BY s.playoff_seed NULLS LAST,
                 m.wins DESC, m.ties DESC, m.calculated_points DESC,
                 m.team_id
    """, (season_year,))


def get_team_slot_points(season_year):
    """Season points produced at each ACTIVE lineup slot, per team.

    One row per (team_id, lineup_slot) from mart_team_slot_production: the
    calculated points the team's players generated while deployed in that
    slot, regular season only. Filtered to active lineup slots -- BE / IL
    production is deliberately out of the v2.0 grid (a future bench/IL view
    belongs on the inactive-points lens; the mart keeps those rows for it).
    Ordered by the roster dim's sort_order so consumers can lay columns out
    without a hardcoded slot list.
    """
    return query_for_presentation(f"""
        SELECT
            team_id,
            lineup_slot,
            slot_calculated_points AS slot_pts,
            sort_order
        FROM mart_team_slot_production
        WHERE season_year = %s
          AND is_active_lineup_slot
          AND {league_predicate()}
        ORDER BY sort_order, lineup_slot
    """, (season_year,))


def get_team_slot_points_alltime():
    """All-time points at each ACTIVE lineup slot per team, as the
    per-standard-matchup average: slot production summed across every
    season over regular-season matchups played (mart_team_season_
    standings' denominator, so partial seasons average honestly). Same
    mart the season grid reads -- same scope rules (regular season only,
    active slots only). 'All-time' means every season the warehouse
    holds; slot vocabulary drift across seasons unions in the builder."""
    return query_for_presentation(f"""
        WITH slots AS (
            SELECT team_id, lineup_slot,
                   CAST(SUM(CAST(slot_calculated_points AS DECIMAL(18, 6))) AS DOUBLE) AS pts,
                   MIN(sort_order) AS sort_order
            FROM mart_team_slot_production
            WHERE is_active_lineup_slot AND {league_predicate()}
            GROUP BY team_id, lineup_slot
        ), matchups AS (
            SELECT team_id, SUM(matchup_periods_played) AS mp
            FROM mart_team_season_standings
            WHERE {league_predicate()}
            GROUP BY team_id
        )
        SELECT s.team_id, s.lineup_slot,
               ROUND(s.pts / NULLIF(m.mp, 0), 1) AS slot_pts,
               s.sort_order
        FROM slots s
        JOIN matchups m ON m.team_id = s.team_id
        ORDER BY s.sort_order, s.lineup_slot
    """)


def get_espn_season_finishes():
    """One row per (season, team): the REGULAR-SEASON finish, W/L/T for the
    all-time W%% column, the POST-PLAYOFF finish, and is_champion = the team
    won EVERY playoff week that season (the consolation bracket always
    carries at least one loss; the current season has no playoff rows yet,
    so it crowns nobody). Feeds the finishes-beside-the-chart table (Kyle
    2026-07-17 round 8).

    TWO DIFFERENT FINISHES, AND THEY ROUTINELY DISAGREE (MLB-227):

      finish      -- the platform's own regular-season seed, 1..N, assigned
                     to every team including non-qualifiers. This is the
                     number the table prints.
      final_rank  -- where the team actually ENDED the season once the
                     bracket had run. NULL until the season closes. This is
                     what the runner-up and third-place medals key on.

    In the last closed season the 7 seed won the title, the 1 seed finished
    2nd and the 2 seed finished 6th, so a medal placed off `finish` would
    decorate the wrong teams. is_champion stays derived from the playoff
    sweep rather than from final_rank = 1: it is the older definition, the
    two agree, and the derivation covers a season whose capture predates
    MLB-227.

    finish falls back to the flat record ordering ONLY for a season the
    standings capture does not cover, so the table still renders for one.
    That fallback is the ordering MLB-227 proved wrong; it cannot silently
    mix with real seeds within a season, because ESPN assigns playoffSeed to
    every team in a season or to none of them."""
    return query_for_presentation(f"""
        WITH ranked AS (
            SELECT m.season_year, m.team_id, m.team_abbrev, m.owner_display,
                   m.wins, m.losses, m.ties,
                   COALESCE(
                       s.playoff_seed,
                       ROW_NUMBER() OVER (
                           PARTITION BY m.season_year
                           ORDER BY m.wins DESC, m.ties DESC,
                                    m.calculated_points DESC, m.team_id)
                   ) AS finish,
                   s.final_rank
            FROM mart_team_season_standings m
            LEFT JOIN dim_team_season_standing s
                ON  s.league_key  = m.league_key
                AND s.season_year = m.season_year
                AND s.team_id     = m.team_id
            WHERE {league_predicate('m')}
        ),
        champs AS (
            SELECT season_year, team_id
            FROM fct_team_weekly_active_performance
            WHERE {league_predicate()} AND is_playoff
            GROUP BY season_year, team_id
            HAVING SUM(CASE WHEN result = 'W' THEN 0 ELSE 1 END) = 0
        )
        SELECT r.season_year, r.team_id, r.team_abbrev, r.owner_display,
               r.wins, r.losses, r.ties, r.finish, r.final_rank,
               (c.team_id IS NOT NULL) AS is_champion
        FROM ranked r
        LEFT JOIN champs c
          ON c.season_year = r.season_year AND c.team_id = r.team_id
        ORDER BY r.season_year, r.finish
    """)


def get_team_standings_alltime(stat_specs):
    """All-time Table A: every season's regular-season rows summed per
    team (same mart, same scope rules), shaped exactly like
    get_team_standings so format_standings_row renders it unchanged --
    the per-standard-matchup normalization divides by the SUMMED
    scoring days. Ordered by all-time win rate, points as tiebreak."""
    if not stat_specs:
        raise RuntimeError("No scored standings stat specs found.")
    stat_columns = [_fact_stat_column_name(spec['stat_name']) for spec in stat_specs]
    for column in stat_columns:
        if not re.match(r'^[a-z][a-z0-9_]*$', column):
            raise ValueError(f"Unsafe stat column name: {column!r}")
    stat_select = ',\n            '.join(
        f'SUM({c}) AS {c}' for c in stat_columns)
    return query_for_presentation(f"""
        SELECT * FROM (
        SELECT
            team_id,
            MAX_BY(team_abbrev, season_year) AS team_abbrev,
            MAX_BY(team_name, season_year) AS team_name,
            MAX_BY(owner_display, season_year) AS owner_display,
            SUM(wins) AS wins,
            SUM(losses) AS losses,
            SUM(ties) AS ties,
            SUM(matchup_periods_played) AS matchup_periods_played,
            SUM(scoring_days_played) AS scoring_days_played,
            MAX(standard_matchup_days) AS standard_matchup_days,
            CAST(SUM(CAST(calculated_hitting_pts AS DECIMAL(18, 6))) AS DOUBLE) AS calculated_hitting_pts,
            CAST(SUM(CAST(calculated_pitching_pts AS DECIMAL(18, 6))) AS DOUBLE) AS calculated_pitching_pts,
            CAST(SUM(CAST(calculated_points AS DECIMAL(18, 6))) AS DOUBLE) AS calculated_points,
            CAST(SUM(CAST(against_calculated_points AS DECIMAL(18, 6))) AS DOUBLE) AS against_calculated_points,
            {stat_select}
        FROM mart_team_season_standings
        WHERE {league_predicate()}
        GROUP BY team_id
        ) standings
        -- The aggregate is wrapped and the ORDER BY sits OUTSIDE it,
        -- because the engines disagree about what `wins` MEANS here:
        -- `SUM(wins) AS wins` shadows the base column, and Snowflake
        -- binds the ORDER BY name to the output alias while DuckDB binds
        -- it to the base column and demands a GROUP BY. Neither in-place
        -- spelling is portable. The note this replaces was right that
        -- SUM(alias) nests aggregates -- Snowflake does reject it,
        -- measured -- so that is not the way out either. Ordering
        -- outside the aggregate makes `wins` an ordinary column, which
        -- means the same thing on both engines (MLB-10 phase 5).
        ORDER BY (wins + 0.5 * ties)
                 / NULLIF(wins + losses + ties, 0) DESC,
                 calculated_points DESC
    """)


def get_team_acquisition_channels_alltime():
    """The acquisition mart summed across every season it holds, per
    team -- the all-time half of the acquisition tables. ESPN's logged
    transaction era starts 2026 (the 2025 topics log isn't cleanly
    reachable -- MLB-16), so today this equals the season table and
    deepens as seasons accrue."""
    return query_for_presentation(f"""
        SELECT
            team_id,
            MAX_BY(team_abbrev, season_year) AS team_abbrev,
            MAX_BY(owner_display, season_year) AS owner_display,
            CAST(SUM(CAST(keeper_active_pts AS DECIMAL(18, 6))) AS DOUBLE) AS keeper_active_pts,
            CAST(SUM(CAST(draft_active_pts AS DECIMAL(18, 6))) AS DOUBLE) AS draft_active_pts,
            CAST(SUM(CAST(trade_active_pts AS DECIMAL(18, 6))) AS DOUBLE) AS trade_active_pts,
            CAST(SUM(CAST(fa_add_active_pts AS DECIMAL(18, 6))) AS DOUBLE) AS fa_add_active_pts,
            CAST(SUM(CAST(acquired_active_pts AS DECIMAL(18, 6))) AS DOUBLE) AS acquired_active_pts,
            CAST(SUM(CAST(dropped_active_pts AS DECIMAL(18, 6))) AS DOUBLE) AS dropped_active_pts,
            CAST(SUM(CAST(traded_away_active_pts AS DECIMAL(18, 6))) AS DOUBLE) AS traded_away_active_pts,
            CAST(SUM(CAST(lost_active_pts AS DECIMAL(18, 6))) AS DOUBLE) AS lost_active_pts,
            CAST(SUM(CAST(fa_delta_active_pts AS DECIMAL(18, 6))) AS DOUBLE) AS fa_delta_active_pts,
            CAST(SUM(CAST(trade_delta_active_pts AS DECIMAL(18, 6))) AS DOUBLE) AS trade_delta_active_pts,
            CAST(SUM(CAST(keeper_rostered_pts AS DECIMAL(18, 6))) AS DOUBLE) AS keeper_rostered_pts,
            CAST(SUM(CAST(draft_rostered_pts AS DECIMAL(18, 6))) AS DOUBLE) AS draft_rostered_pts,
            CAST(SUM(CAST(trade_rostered_pts AS DECIMAL(18, 6))) AS DOUBLE) AS trade_rostered_pts,
            CAST(SUM(CAST(fa_add_rostered_pts AS DECIMAL(18, 6))) AS DOUBLE) AS fa_add_rostered_pts,
            CAST(SUM(CAST(acquired_rostered_pts AS DECIMAL(18, 6))) AS DOUBLE) AS acquired_rostered_pts,
            CAST(SUM(CAST(dropped_rostered_pts AS DECIMAL(18, 6))) AS DOUBLE) AS dropped_rostered_pts,
            CAST(SUM(CAST(traded_away_rostered_pts AS DECIMAL(18, 6))) AS DOUBLE) AS traded_away_rostered_pts,
            CAST(SUM(CAST(lost_rostered_pts AS DECIMAL(18, 6))) AS DOUBLE) AS lost_rostered_pts,
            CAST(SUM(CAST(fa_delta_rostered_pts AS DECIMAL(18, 6))) AS DOUBLE) AS fa_delta_rostered_pts,
            CAST(SUM(CAST(trade_delta_rostered_pts AS DECIMAL(18, 6))) AS DOUBLE) AS trade_delta_rostered_pts
        FROM mart_team_acquisition_channels
        WHERE {league_predicate()}
        GROUP BY team_id
    """)


def get_team_rank_arc(season_year):
    """Standings position after every regular-season matchup period,
    reconstructed from the weekly results -- ESPN keeps no intra-season
    standings snapshots, but every week's result is here, so the
    standings after week N recompute exactly. Ranking is the almanac's
    own standings ordering (wins, ties, cumulative calculated points as
    the tiebreak, team_id as the deterministic last resort); mid-season
    the OFFICIAL site tiebreakers could order a tied pair differently.
    Feeds the rank-by-week chart (Kyle 2026-07-17)."""
    return query_for_presentation(f"""
        WITH weekly AS (
            SELECT team_id, team_abbrev, matchup_period,
                   CASE result WHEN 'W' THEN 1 ELSE 0 END AS w,
                   CASE result WHEN 'T' THEN 1 ELSE 0 END AS t,
                   COALESCE(calculated_points, 0) AS pts
            FROM fct_team_weekly_active_performance
            WHERE {league_predicate()} AND season_year = %s
              AND NOT is_playoff
        ),
        cume AS (
            SELECT team_id, team_abbrev, matchup_period,
                   SUM(w) OVER (PARTITION BY team_id
                                ORDER BY matchup_period) AS cume_w,
                   SUM(t) OVER (PARTITION BY team_id
                                ORDER BY matchup_period) AS cume_t,
                   SUM(CAST(pts AS DECIMAL(18, 6))) OVER (PARTITION BY team_id
                                  ORDER BY matchup_period) AS cume_pts
            FROM weekly
        )
        SELECT team_id, team_abbrev, matchup_period AS period,
               ROW_NUMBER() OVER (
                   PARTITION BY matchup_period
                   ORDER BY cume_w DESC, cume_t DESC, cume_pts DESC,
                            team_id) AS standings_rank
        FROM cume
        ORDER BY period, standings_rank
    """, (season_year,))


def get_rivalry_axes():
    """The ACTIVE teams a Rivalry Matrix draws rows and columns for
    (MLB-229), already deduplicated onto one axis per team identity and
    ordered.

    Read straight off mart_franchise_rivalry_axes rather than reconstructed
    from the standings rows every other block on this tab uses, and the
    difference is the point: the standings are keyed by PLATFORM team id,
    while a rivalry axis is keyed by team IDENTITY. Two live platform ids
    that the league has given one configured canonical name are one team
    here and two rows there, and a franchise that changed ids keeps its old
    eras on the axis its current id resolves to."""
    return query_for_presentation(
        f"SELECT identity_key, identity_name, identity_abbrev,"
        f"       identity_source, active_platform_teams, league_format,"
        f"       has_rivalry_evidence, sort_order"
        f" FROM mart_franchise_rivalry_axes"
        f" WHERE {league_predicate()}"
        f" ORDER BY sort_order"
    )


def get_rivalry_matrix():
    """Every ordered pair of team identities with something to say about
    each other (MLB-229): the head-to-head matchup ledger and the
    completed-season points ledger, from the row team's perspective.

    LONG, and densified by the renderer rather than here. The mart is one
    row per ordered pair that HAS a result; the matrix wants a cell for
    every pair of active axes, including the ones that have never met.
    Filling those in is a display decision -- an empty diagonal, a 0-0 for
    two teams that never played -- so build_rivalry_matrix_rows makes it,
    and the query stays a straight read.

    NOT FILTERED TO ACTIVE TEAMS. The axes decide what is drawn; the ledger
    holds everything, including the folded franchises an active team's
    record was built against. Filtering here would make a team's totals
    depend on who else is still playing."""
    return query_for_presentation(
        f"SELECT row_identity_key, opponent_identity_key,"
        f"       row_team_name, opponent_team_name,"
        f"       matchup_meetings, matchup_wins, matchup_losses, matchup_ties,"
        f"       points_for, points_against, points_margin,"
        f"       season_meetings, season_wins, season_losses, season_ties"
        f" FROM mart_franchise_rivalry"
        f" WHERE {league_predicate()}"
    )


def get_team_affinity_weights(season_year):
    """Active-lineup INVOLVEMENT per (team, MLB club) -- the affinity-
    chart substrate, weighted by PA + BF (Kyle 2026-07-17 round 10:
    pure games-played underweights pitchers ~5:1). PA = AB+BB+HBP+SF,
    BF = outs+H+BB+HBP allowed, both straight off the daily fact.

    The club signal is `pro_team`, which since MLB-159 Exit 1 is the club
    of the GAME the production came from -- read per player-day from RAW's
    `clubOfGame` rather than from ESPN's person-level stamp. A player
    traded mid-season is credited to the club he actually played for on
    each day, on both sides of the move.

    What that replaced is worth keeping in view, because it is the failure
    mode to re-check if these numbers ever look too clean. The old signal
    was the person stamp, applied per matchup period from whatever the
    profile said when the period was pulled: FORWARD that is
    period-accurate but not day-accurate (the week a player moves was
    stamped wholly with his new club), and BACKWARD a season pulled in one
    pass gets ONE stamp for the whole year (2025 measured 0 of 1,236
    player-seasons carrying more than one club, against 66 in the live
    season -- every mid-season move mis-filed, invisibly).

    THE ATTRIBUTION RULE IS THE PRODUCING-SPLITS FILTER. Only a split
    carrying a non-empty stats object is club evidence. ESPN emits an
    empty `{}` split for the incoming club during a transition window --
    person-record drift reaching split level -- and that phantom names a
    club that frequently did not even play that day. Requiring production
    removes it before any tie-break sees it. Majority-by-production
    survives upstream as a documented dormant fallback for a genuine
    same-day two-club day, which is unobserved across both seasons here
    (0 of 94 multi-club candidates carry two PRODUCING clubs).

    Three rewrites of this docstring have now over-claimed, each in the
    same direction, so the wording stays deliberately careful. The
    original called the snapshot "day-accurate across trades" -- the
    per-scoring-period part was right and the day part was not. Its
    replacement called 2026 "live-extracted and correct", which the Mead
    reconciliation disproved (18 of his 20 Boston-labelled PA were
    Washington games in the week of the move). So this one does not say
    "correct" either: it says the club is the one the producing split
    names, which is a rule you can check, and Mead now reconciles to
    Baseball Reference on both sides of his move (2 PA Boston, 327
    Washington) without having been tuned to.

    AFFINITY_UNATTRIBUTED is now expected to be EMPTY on this league's
    data -- measured 0.0 across 2025, 2026 and all-time, against 11.73%
    and 0.02% before the flip. The band is deliberately still
    render-capable: zero rows here is a property of THIS data, not deleted
    code, and for a league backfilled years after its seasons were lived
    it is a live diagnostic. The `pro_team = 'FA'` arm of that CASE is
    likewise unreachable now (clubOfGame is one of 30 clubs or NULL, never
    'FA') and is kept as a tripwire -- it is what stops an FA filter being
    silently restored, which is the regression that once deleted 11.7% of
    2025 from the chart. tests/test_almanac_sheets.py pins the text.

    Note the two different FAs. `lineup_slot = 'FA'` means nobody had him
    rostered that day and stays excluded; `pro_team = 'FA'` was the
    extract-day stamp described above. Bench/IL and free-agent-SLOT rows
    stay out; playoff weeks count (affinity is a roster-identity lens, not
    a standings metric).

    The one measured gap is outside this query's scope: 476 FA-slot
    player-days (all 2026) where ESPN no longer serves the player and the
    club cannot be reconstructed. They carry zero active weight, so no
    chart row moves; MLB-193 keeps them NULL rather than guessing."""
    involvement = ('(COALESCE(ab, 0) + COALESCE(b_bb, 0) + COALESCE(hbp, 0)'
                   ' + COALESCE(sf, 0) + COALESCE(outs, 0)'
                   ' + COALESCE(p_h, 0) + COALESCE(p_bb, 0)'
                   ' + COALESCE(hbp_p, 0))')
    # "Which slots are not a deployment" comes from the slot_classification
    # seed rather than a literal repeated here (MLB-222 F-1). Resolves to
    # the same 'BE', 'FA', 'IL' this line used to spell out, sorted so the
    # generated SQL is stable run to run.
    inactive_slots = slot_catalog.sql_in_list(slot_catalog.get_inactive_slots())
    return query_for_presentation(f"""
        SELECT team_id,
               CASE WHEN pro_team IS NULL OR pro_team = 'FA'
                    THEN '{AFFINITY_UNATTRIBUTED}'
                    ELSE pro_team END AS pro_team,
               ROUND(CAST(SUM(CAST(CASE WHEN season_year = %s
                              THEN {involvement}
                                   * COALESCE(active_weight, 0)
                              ELSE 0 END AS DECIMAL(18, 6))) AS DOUBLE), 1) AS season_wt,
               ROUND(CAST(SUM(CAST({involvement}
                         * COALESCE(active_weight, 0) AS DECIMAL(18, 6))) AS DOUBLE), 1) AS alltime_wt
        FROM fct_player_daily_performance
        WHERE {league_predicate()}
          AND lineup_slot NOT IN ({inactive_slots})
        GROUP BY 1, 2
    """, (season_year,))


def get_team_acquisition_channels(season_year):
    """Per-team production by acquisition channel and departure type, both
    lenses, from mart_team_acquisition_channels (MLB-17). One row per team;
    the builder orders each lens block by its own Acquired total. Feeds the two
    transaction blocks stacked under the Advanced Standings weekly grid.
    """
    return query_for_presentation(f"""
        SELECT
            team_id,
            team_abbrev,
            owner_display,
            keeper_active_pts,   draft_active_pts,   trade_active_pts,
            fa_add_active_pts,   acquired_active_pts,
            dropped_active_pts,  traded_away_active_pts, lost_active_pts,
            fa_delta_active_pts, trade_delta_active_pts,
            keeper_rostered_pts,   draft_rostered_pts,   trade_rostered_pts,
            fa_add_rostered_pts,   acquired_rostered_pts,
            dropped_rostered_pts,  traded_away_rostered_pts, lost_rostered_pts,
            fa_delta_rostered_pts, trade_delta_rostered_pts
        FROM mart_team_acquisition_channels
        WHERE season_year = %s
          AND {league_predicate()}
    """, (season_year,))


# ---------------------------------------------------------------------------
# MLB-103 Trades tab: live ESPN league-API reads + warehouse points joins.
# The trading block is ephemeral current-state (marks appear and vanish as
# managers toggle them) and the executed-trade ledger only exists in the
# communication feed, so both are fetched at build time rather than landed
# through the warehouse; the points columns join from the season and daily
# facts. Same cookie auth as extract/extract.py.
# ---------------------------------------------------------------------------

# Slot-id and pro-team-id decoders from the espn_api wrapper, mirroring
# extract/extract.py's defensive getattr pattern.
try:
    from espn_api.baseball import constant as _espn_constant
except ImportError:
    _espn_constant = None
_LINEUP_SLOT_MAP = getattr(_espn_constant, 'POSITION_MAP', {})
_PRO_TEAM_MAP = getattr(_espn_constant, 'PRO_TEAM_MAP', {})

_TRADE_PLAYERCARD_CHUNK = 60

# Communication-feed message types (MLB-16 + MLB-103 spikes): 178 add,
# 179 drop, 188 lineup noise, 224 trade ACCEPTED (member author), 239
# trade-block marks, 244 trade EXECUTED (TradeTaskProcessor, ~a day after
# its accepted-224 twin), 245 a drop-to-waivers baked into an executing
# trade. The Trade Record keys off 244, so vetoed / still-pending trades
# never appear; to=0 legs are the baked drops, received by nobody.
_TRADE_EXECUTED_MSG_TYPE = 244


def _espn_league_get(season_year, view, extra_headers=None, path=''):
    """One cookie-authed GET against the league read API; returns JSON."""
    league_id = os.getenv('LEAGUE_ID')
    resp = requests.get(
        'https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/seasons/'
        f'{season_year}/segments/0/leagues/{league_id}{path}',
        params={'view': view},
        cookies={'swid': os.getenv('SWID'), 'espn_s2': os.getenv('ESPN_S2')},
        headers=extra_headers or {},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _espn_communication_topics(season_year):
    """Every ACTIVITY_TRANSACTIONS topic for the season, paginated by
    offset until a short page (the MLB-16 contract)."""
    topics = []
    offset = 0
    while True:
        page_filter = {
            'topics': {
                'filterType': {'value': ['ACTIVITY_TRANSACTIONS']},
                'limit': 200,
                'offset': offset,
                'sortMessageDate': {'sortPriority': 1, 'sortAsc': False},
            }
        }
        try:
            payload = _espn_league_get(
                season_year,
                'kona_league_communication',
                extra_headers={'x-fantasy-filter': json.dumps(page_filter)},
                path='/communication/',
            )
        except requests.HTTPError as exc:
            status = getattr(exc.response, 'status_code', None)
            if status in (401, 403):
                print(
                    '[warn] ESPN did not authorize the communications feed; '
                    'the Trade Record is unavailable, not empty.'
                )
                return None
            raise
        page = payload.get('topics') or []
        topics.extend(page)
        if len(page) < 200:
            break
        offset += 200
    return topics


def _executed_trades(season_year):
    """Executed trades from the communication feed, newest first:
    [{'executed_ms', 'legs': [{'player_id', 'sending_team_id',
    'receiving_team_id'}]}]. One topic per transaction event; only the
    msgType-244 executed movements count, and to=0 legs (trade-baked
    drops to waivers) are excluded."""
    topics = _espn_communication_topics(season_year)
    if topics is None:
        return None
    trades = []
    for topic in topics:
        legs = [
            {
                'player_id': m.get('targetId'),
                'sending_team_id': m.get('from'),
                'receiving_team_id': m.get('to'),
            }
            for m in topic.get('messages') or []
            if m.get('messageTypeId') == _TRADE_EXECUTED_MSG_TYPE
            and m.get('to')
        ]
        if legs:
            trades.append({'executed_ms': topic.get('date') or 0, 'legs': legs})
    trades.sort(key=lambda t: -t['executed_ms'])
    return trades


def get_player_season_points(season_year):
    """Season Total / Active points per player, league-wide, one query.

    Home-glossary semantics, unscoped by team: Total = every point the
    player produced all season (active + bench / IL + FA time); Active =
    fantasy-credited production (performance_status = 'active').
    Returns {player_id: {'total_pts', 'active_pts'}}.
    """
    rows = query_for_presentation(f"""
        SELECT
            player_id,
            ROUND(CAST(SUM(CAST(calculated_points AS DECIMAL(18, 6))) AS DOUBLE), 1) AS total_pts,
            ROUND(CAST(SUM(CAST(CASE WHEN performance_status = 'active'
                           THEN calculated_points ELSE 0 END AS DECIMAL(18, 6))) AS DOUBLE), 1) AS active_pts
        FROM fct_player_season_performance
        WHERE season_year = %s
          AND {league_predicate()}
        GROUP BY player_id
    """, (season_year,))
    return {
        r['player_id']: {
            'total_pts': r['total_pts'] or 0,
            'active_pts': r['active_pts'] or 0,
        }
        for r in rows
    }


def _get_season_opener(season_year):
    """The season's first scoring-period date, for converting a trade's
    execution date to an ESPN scoring period.

    STILL dim_matchup_period, and that is the point of MLB-235 rung 4B-2
    rather than an omission: the dimension's start_date is now DERIVED --
    ESPN's daily scoring-period ids anchored to MLB's published
    regular-season start -- and falls back to the hand-maintained seed only
    where the platform has not answered. So this reads the automatic opener
    without knowing that it is one, and a stranger with a blank
    matchup_schedule.csv gets a real answer here for the first time.

    Returns None when NOTHING resolved: no captured anchor and no seed. That
    is a genuine state and the caller must treat it as one -- see the note in
    get_trades_tab_data on what used to happen instead.
    """
    rows = query_for_presentation(f"""
        SELECT MIN(start_date) AS opener
        FROM dim_matchup_period
        WHERE season_year = %s
          -- MLB-235: league-scoped dimension. Without this the opener could
          -- come from another league's calendar entirely.
          AND {league_predicate()}
    """, (season_year,))
    return rows[0]['opener'] if rows else None


def since_trade_cutoff(exec_date, opener):
    """The first scoring period whose production counts as "since the trade".

    PURE, AND PUBLIC, so the rule is reachable by a test. It used to be an
    inline expression inside `get_trades_tab_data` -- which is a live ESPN +
    warehouse read and therefore excluded from the pure suite -- so the one
    place the wrong answer was born was the one place nothing could exercise.

    THE FALLBACK THAT IS NOT HERE. This read `... if opener else 1`, and a
    floor of 1 admits EVERY scoring period of the season, so a season whose
    opener could not be resolved published each player's whole-season
    production under a column headed "since the trade". That is not a
    slightly-wrong number, it is a different statistic wearing the right
    label, and it looked entirely plausible.

    None means "cannot be computed", and every caller must render it as
    unavailable rather than as a total.

    `max(1, ...)` is retained for a trade executed BEFORE the season opened:
    a draft-day deal has no pre-trade production to exclude, and a zero or
    negative floor would be a scoring period that does not exist.
    """
    if opener is None:
        return None
    return max(1, (exec_date - opener).days + 1)


def _get_since_trade_points(season_year, player_ids):
    """Per (player, team, scoring_period) point sums for trade-leg
    players, one query; the caller slices per leg (team = receiver,
    scoring_period >= the execution day's). player_name rides along as a
    fallback label for players who have since left the ESPN pool."""
    if not player_ids:
        return []
    placeholders = ', '.join(['%s'] * len(player_ids))
    return query_for_presentation(f"""
        SELECT
            player_id,
            team_id,
            scoring_period,
            MAX(player_name) AS player_name,
            CAST(SUM(CAST(total_stat_pts AS DECIMAL(18, 6))) AS DOUBLE) AS total_pts,
            CAST(SUM(CAST(CASE WHEN performance_status = 'active'
                     THEN total_stat_pts ELSE 0 END AS DECIMAL(18, 6))) AS DOUBLE) AS active_pts
        FROM fct_player_daily_performance
        WHERE season_year = %s
          AND {league_predicate()}
          AND player_id IN ({placeholders})
        GROUP BY player_id, team_id, scoring_period
    """, (season_year, *player_ids))


def get_trades_tab_data(season_year):
    """Everything the Trades tab renders: the live trading block plus the
    season's executed Trade Record (MLB-103).

    Live reads: mTeam (team names + abbrevs), mRoster (the rostered
    universe -- dropped players with stale block marks fall out here),
    kona_playercard in chunks (tradeBlock status + teamsWatching, plus
    name / position metadata that covers traded players no longer
    rostered), and the communication feed's transaction topics (executed
    trades). Warehouse reads: season Total / Active points per player and
    per-day points for the since-trade sums.

    Returns {'as_of', 'players', 'trades'}:
      players -- one dict per ROSTERED player: fantasy_team, player_name,
        pro_team, eligible_slots (name list), availability (raw status or
        None), interest, total_pts, active_pts.
      trades -- newest first, one dict per EXECUTED trade: date_display
        plus legs [{receiving_team, receiving_team_id, sending_abbrev,
        player_name, pro_team, eligible_slots, total_pts, active_pts}],
        the points scoped to production for the receiving team since the
        execution day.

    The playercard's teamIdsWatching (WHICH teams marked interest --
    populated for your own roster only) is deliberately never read: the
    tab publishes counts, not identities, matching what ESPN shows every
    manager.
    """
    mteam = _espn_league_get(season_year, 'mTeam')
    team_names, team_abbrevs = {}, {}
    for t in mteam.get('teams', []):
        team_names[t['id']] = t.get('name') or f"Team {t['id']}"
        team_abbrevs[t['id']] = t.get('abbrev') or str(t['id'])

    season_points = get_player_season_points(season_year)
    mroster = _espn_league_get(season_year, 'mRoster')
    players = {}
    for team in mroster.get('teams', []):
        for entry in (team.get('roster') or {}).get('entries', []):
            player = (entry.get('playerPoolEntry') or {}).get('player') or {}
            pid = entry.get('playerId')
            pts = season_points.get(pid) or {}
            players[pid] = {
                'fantasy_team': team_names.get(team['id'], f"Team {team['id']}"),
                'player_name': player.get('fullName') or str(pid),
                'pro_team': _PRO_TEAM_MAP.get(player.get('proTeamId'), 'FA'),
                'eligible_slots': [
                    _LINEUP_SLOT_MAP.get(slot_id, str(slot_id))
                    for slot_id in player.get('eligibleSlots') or []
                ],
                'availability': None,
                'interest': 0,
                'total_pts': pts.get('total_pts', 0),
                'active_pts': pts.get('active_pts', 0),
            }

    trades = _executed_trades(season_year)
    trade_record_available = trades is not None
    trades = trades or []
    leg_pids = sorted({
        leg['player_id'] for t in trades for leg in t['legs']
        if leg.get('player_id')
    })

    cards = {}
    card_pids = sorted(set(players) | set(leg_pids))
    for start in range(0, len(card_pids), _TRADE_PLAYERCARD_CHUNK):
        chunk = card_pids[start:start + _TRADE_PLAYERCARD_CHUNK]
        card = _espn_league_get(
            season_year,
            'kona_playercard',
            extra_headers={
                'X-Fantasy-Filter': json.dumps(
                    {'players': {'filterIds': {'value': chunk}}}
                ),
            },
        )
        for entry in card.get('players') or []:
            cards[entry.get('id')] = entry

    for pid, target in players.items():
        block = (cards.get(pid) or {}).get('tradeBlock') or {}
        target['availability'] = block.get('status')
        target['interest'] = block.get('teamsWatching') or 0

    opener = _get_season_opener(season_year)
    daily_rows = defaultdict(list)
    daily_names = {}
    for r in _get_since_trade_points(season_year, leg_pids):
        daily_rows[r['player_id']].append(r)
        daily_names.setdefault(r['player_id'], r.get('player_name'))

    for trade in trades:
        exec_date = datetime.fromtimestamp(trade['executed_ms'] / 1000).date()
        trade['date_display'] = (
            f"{exec_date.month}/{exec_date.day}/{exec_date.year}"
        )
        # NO FALLBACK TO SCORING PERIOD 1 (MLB-235 rung 4B-2). This read
        # `if opener else 1`, and a scoring-period floor of 1 admits EVERY
        # day of the season -- so a season whose opener could not be resolved
        # published each player's whole-season production under a column
        # headed "since the trade". That is not a slightly-wrong number; it is
        # a different statistic wearing the right label, and it looked
        # perfectly plausible.
        #
        # An unresolved opener is now unavailable, and says so. The ordinary
        # path is unaffected: the opener is derived automatically, so this
        # arithmetic and its values are exactly what they were.
        cutoff_sp = since_trade_cutoff(exec_date, opener)
        for leg in trade['legs']:
            pid = leg.get('player_id')
            receiver = leg.get('receiving_team_id')
            if cutoff_sp is None:
                # None, not 0. A zero would render as a real total of zero
                # points, which is a claim; None is the absence of one, and
                # the formatter turns it into an explicit unavailable marker.
                total = active = None
            else:
                total = active = 0.0
                for r in daily_rows.get(pid, ()):
                    if (r['team_id'] == receiver
                            and (r['scoring_period'] or 0) >= cutoff_sp):
                        total += r['total_pts'] or 0
                        active += r['active_pts'] or 0
            card_player = (cards.get(pid) or {}).get('player') or {}
            rostered = players.get(pid) or {}
            leg.update({
                'receiving_team': team_names.get(receiver, f"Team {receiver}"),
                'sending_abbrev': team_abbrevs.get(
                    leg.get('sending_team_id'),
                    str(leg.get('sending_team_id') or ''),
                ),
                'player_name': (
                    card_player.get('fullName')
                    or rostered.get('player_name')
                    or daily_names.get(pid)
                    or str(pid)
                ),
                'pro_team': (
                    _PRO_TEAM_MAP.get(card_player.get('proTeamId'))
                    or rostered.get('pro_team')
                    or 'FA'
                ),
                'eligible_slots': (
                    [
                        _LINEUP_SLOT_MAP.get(slot_id, str(slot_id))
                        for slot_id in card_player.get('eligibleSlots') or []
                    ]
                    or rostered.get('eligible_slots')
                    or []
                ),
                'total_pts': round(total, 1),
                'active_pts': round(active, 1),
            })

    return {
        'as_of': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'players': list(players.values()),
        'trades': trades,
        'trade_record_available': trade_record_available,
    }


def get_slot_capacities(season_year, matchup_period):
    """Return configured active roster slot counts for one season."""
    rows = query_for_presentation(f"""
        SELECT
            lineup_slot,
            starter_count AS slots_to_fill
        FROM dim_roster_slot_counts
        WHERE season_year = %s
          AND is_active_lineup_slot
          AND starter_count > 0
          AND {league_predicate()}
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
    rows = query_for_presentation("""
        SELECT
            lineup_slot,
            starter_count AS slots_to_fill
        FROM dim_roster_slot_counts
        WHERE season_year = %s
          AND starter_count > 0
          AND {league_filter}
          {inactive_filter}
        ORDER BY sort_order
    """.format(inactive_filter=inactive_filter,
               league_filter=league_predicate()), (season_year,))

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
    player_rows = query_for_presentation(f"""
        WITH latest_day AS (
            SELECT
                season_year,
                MAX(scoring_period) AS scoring_period
            FROM mart_daily_roster_snapshot
            WHERE season_year = %s
              AND team_id IS NOT NULL
              AND {league_predicate()}
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
                ON d.league_key = m.league_key
                AND d.season_year = m.season_year
                AND d.matchup_period = m.matchup_period
            WHERE d.team_id IS NOT NULL
              AND {league_predicate('d')}
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
              AND {league_predicate('d')}

            UNION ALL

            SELECT 'all_time' AS scope, d.*
            FROM mart_daily_roster_snapshot d
            WHERE d.team_id IS NOT NULL
              AND {league_predicate('d')}
        ),

        player_context AS (
            -- pro_team comes off the latest day that HAS a club, not off
            -- the picked row (MLB-159 Exit 1, swept MLB-168). This is the
            -- site that blanked the club on the team tabs: post-flip the
            -- label is NULL on every day a player did not appear, and
            -- more than half of all players' latest rostered day in scope
            -- is a rest day (measured 476/897 all-time, 326/646
            -- current-season). The row choice is deliberately left alone
            -- so name/display_name/position keep coming from that same
            -- latest row -- only the label moves. Partitioned by scope
            -- too, so the current-season and all-time bands each resolve
            -- their own latest labelled day.
            SELECT
                scope,
                player_id,
                player_name,
                display_name,
                position,
                pro_team
            FROM (
                SELECT
                    scope,
                    player_id,
                    player_name,
                    display_name,
                    position,
                    {latest_by('pro_team',
                               'season_year * 1000 + scoring_period',
                               'scope, player_id')} AS pro_team,
                    ROW_NUMBER() OVER (
                        PARTITION BY scope, player_id
                        ORDER BY season_year DESC, scoring_period DESC
                    ) AS rn
                FROM scoped_daily
            )
            WHERE rn = 1
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
              AND {league_predicate('d')}
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
                AND {league_predicate('r')}
            WHERE d.roster_status = 'active'
        ),

        active_slot_list AS (
            SELECT
                scope,
                team_id,
                player_id,
                {listagg('lineup_slot', ', ', 'sort_order, lineup_slot')}
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
              AND {league_predicate('p')}

            UNION ALL

            SELECT 'all_time' AS scope, p.*
            FROM fct_player_season_performance p
            WHERE p.team_id IS NOT NULL
              AND {league_predicate('p')}
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
                -- MLB-123: carry 6dp and let the display round decide the
                -- digit. These point totals are summed in Python (Total =
                -- active + bench) and handed to the shared team renderer,
                -- which rounds them for display -- rounding to 1dp HERE made
                -- that a double round and lifted boundary cells a full unit.
                -- Mirrors the CBS fix (MLB-121) so both books feed the shared
                -- _team_history_display_row identical precision.
                ROUND(CAST(SUM(CAST(calculated_points AS DECIMAL(18, 6))) AS DOUBLE), 6) AS active_points,
                -- v1.2: per-category active points so the per-team tab can
                -- show slot-decomposed points for two-way players (Ohtani
                -- gets hitting pts at his DH slot, pitching pts at SP).
                -- Single-discipline players: one equals active_points, the
                -- other is 0 -- so their displayed points don't move.
                ROUND(CAST(SUM(CAST(calculated_hitting_pts AS DECIMAL(18, 6))) AS DOUBLE), 6) AS active_hitting_points,
                ROUND(CAST(SUM(CAST(calculated_pitching_pts AS DECIMAL(18, 6))) AS DOUBLE), 6) AS active_pitching_points,
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
                -- MLB-123: 6dp -- summed into Total with active_points, so
                -- pre-rounding to 1dp double-rounds the displayed Total.
                ROUND(CAST(SUM(CAST(calculated_points AS DECIMAL(18, 6))) AS DOUBLE), 6) AS bench_il_points
            FROM scoped_season
            WHERE performance_status = 'inactive'
            GROUP BY 1, 2, 3
        ),

        service_seasons AS (
            -- Distinct seasons a player was actively started for this team with
            -- nonzero active production (net-negative seasons included) -- the
            -- all-time "Years of Service" column (Kyle 2026-07-16). Mirrors the
            -- CBS get_years_of_service definition; only meaningful for the
            -- all_time scope. Rendered as a "count: year-ranges" string.
            SELECT
                scope,
                team_id,
                player_id,
                -- CAST, not TO_VARCHAR: DuckDB has no to_varchar, while
                -- CAST(x AS VARCHAR) is valid on both engines. Verified
                -- equal to TO_VARCHAR over all 141,350 season_year rows
                -- on Snowflake, 0 mismatches (MLB-10 phase 5).
                {listagg('CAST(season_year AS VARCHAR)', ',', 'season_year')}
                    AS service_years
            FROM (
                SELECT scope, team_id, player_id, season_year
                FROM scoped_season
                WHERE performance_status = 'active'
                GROUP BY scope, team_id, player_id, season_year
                HAVING ROUND(CAST(SUM(CAST(calculated_points AS DECIMAL(18, 6))) AS DOUBLE), 1) <> 0
            )
            GROUP BY scope, team_id, player_id
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
            COALESCE(act.active_points, 0) AS active_points,
            COALESCE(act.active_hitting_points, 0) AS active_hitting_points,
            COALESCE(act.active_pitching_points, 0) AS active_pitching_points,
            COALESCE(act.h, 0) AS h,
            COALESCE(act.ab, 0) AS ab,
            COALESCE(act.b_bb, 0) AS b_bb,
            COALESCE(act.hbp, 0) AS hbp,
            COALESCE(act.sf, 0) AS sf,
            COALESCE(act.tb, 0) AS tb,
            COALESCE(act.hr, 0) AS hr,
            COALESCE(act.sb, 0) AS sb,
            COALESCE(act.w, 0) AS w,
            COALESCE(act.l, 0) AS l,
            COALESCE(act.sv, 0) AS sv,
            COALESCE(act.er, 0) AS er,
            COALESCE(act.outs, 0) AS outs,
            COALESCE(act.k, 0) AS k,
            COALESCE(act.p_bb, 0) AS p_bb,
            COALESCE(act.p_h, 0) AS p_h,
            COALESCE(ss.service_years, '') AS service_years
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
        -- Alias is `act`, not `at`: AT is a RESERVED WORD in DuckDB (its
        -- time-travel syntax), so `at.` is a parser error there while
        -- Snowflake accepts it. Purely a naming change -- see MLB-10
        -- phase 5. Don't shorten it back.
        LEFT JOIN active_totals act
            ON rt.scope = act.scope
            AND rt.team_id = act.team_id
            AND rt.player_id = act.player_id
        LEFT JOIN inactive_totals it
            ON rt.scope = it.scope
            AND rt.team_id = it.team_id
            AND rt.player_id = it.player_id
        LEFT JOIN service_seasons ss
            ON rt.scope = ss.scope
            AND rt.team_id = ss.team_id
            AND rt.player_id = ss.player_id
        -- MLB-123: end the order on player_id so this is a TOTAL order. The
        -- shared renderer re-sorts these rows by points (a stable sort), and
        -- exact ties are real -- without a unique final key the tie order is
        -- whatever the warehouse happened to return, which flips run to run.
        ORDER BY ct.team_name, rt.scope, rt.rostered_days DESC,
                 pl.display_name, rt.player_id
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
    rows = query_for_presentation(f"""
        WITH latest_day AS (
            SELECT
                season_year,
                MAX(scoring_period) AS scoring_period
            FROM mart_daily_roster_snapshot
            WHERE season_year = %s
              AND team_id IS NOT NULL
              AND {league_predicate()}
            GROUP BY 1
        ),

        player_club AS (
            -- The club label must NOT come off the pinned latest day.
            -- This CTE's rows are one specific scoring_period, and
            -- post-flip pro_team is NULL on every day a player did not
            -- appear -- so reading `d.pro_team` blanks the club for
            -- everyone who happened to be resting on that day. Same
            -- defect as the two player_context row-picks, reached by a
            -- different shape (a single-day pin rather than a row-pick),
            -- which is why the MLB-168 sweep did not catch it.
            SELECT
                player_id,
                {latest_by('pro_team',
                           'season_year * 1000 + scoring_period')} AS pro_team
            FROM mart_daily_roster_snapshot
            WHERE season_year = %s
              AND team_id IS NOT NULL
              AND {league_predicate()}
            GROUP BY player_id
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
                pc.pro_team,
                d.lineup_slot,
                d.slots_to_fill,
                d.slot_sort_order
            FROM mart_daily_roster_snapshot d
            INNER JOIN latest_day ld
                ON d.season_year = ld.season_year
                AND d.scoring_period = ld.scoring_period
            LEFT JOIN player_club pc
                ON pc.player_id = d.player_id
            WHERE d.team_id IS NOT NULL
              AND d.lineup_slot <> 'FA'
              AND {league_predicate('d')}
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
                ROUND(CAST(SUM(CAST(CASE
                    WHEN roster_status = 'inactive'
                        THEN COALESCE(platform_points, 0)
                    ELSE 0
                END AS DECIMAL(18, 6))) AS DOUBLE), 1) AS inactive_points
            FROM mart_daily_roster_snapshot
            WHERE season_year = %s
              AND team_id IS NOT NULL
              AND lineup_slot <> 'FA'
              AND {league_predicate()}
            GROUP BY 1, 2, 3
        ),

        active AS (
            SELECT
                season_year,
                team_id,
                player_id,
                COUNT(DISTINCT matchup_period) AS active_weeks,
                ROUND(CAST(SUM(CAST(platform_points AS DECIMAL(18, 6))) AS DOUBLE), 1) AS active_points,
                SUM(hr) AS hr,
                SUM(rbi) AS rbi,
                SUM(r) AS r,
                SUM(sb) AS sb,
                SUM(w) AS w,
                SUM(sv) AS sv,
                SUM(hld) AS hld,
                SUM(k) AS k,
                SUM(outs) AS outs
            FROM fct_player_weekly_active_performance
            WHERE season_year = %s
              AND {league_predicate()}
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
    """, (season_year, season_year, season_year, season_year))

    if not rows:
        raise RuntimeError(f"No current roster rows found for {season_year}.")
    return rows



def get_almanac_records(scope):
    """Return rank-one record rows for the almanac Records tab."""
    rows = query_for_presentation(f"""
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
          AND {league_predicate()}
        ORDER BY entity_grain, stat_name, record_direction, rank
    """, (scope,))

    collapsed = records.collapse_ties(
        rows,
        max_n=1,
        count_fn=_almanac_tie_counter(scope),
    )
    collapsed.extend(get_rate_records(scope))
    collapsed.extend(get_lineup_slot_records(scope))
    # MLB-135: wasted lives at performance_status='inactive', so the main
    # query above (which filters to 'active') can never return it -- that is
    # why the export existed but nothing rendered. Fetched separately, like
    # rate and lineup-slot records. Safe to extend BEFORE the contributor
    # pass: WASTED_POINTS is in records_data._NO_PLAYER_BREAKDOWN_STATS, so
    # the bulk fetch returns [] for it rather than interpolating the stat
    # name as a fct column that does not exist.
    collapsed.extend(get_wasted_points_records(scope))
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
    fct = ('fct_team_weekly_active_performance' if grain == 'team'
           else 'fct_player_weekly_active_performance')
    rows = query_for_presentation(f"""
        SELECT COUNT(*) AS n
        FROM {fct}
        WHERE is_record_eligible
          AND {league_predicate()}
          AND season_year = (
              SELECT MAX(season_year)
              FROM fct_team_weekly_active_performance
              WHERE {league_predicate()}
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
        season_filter = f"""
          AND p.season_year = (
              SELECT MAX(season_year)
              FROM fct_team_weekly_active_performance
              WHERE {league_predicate()}
          )
        """
    elif scope != 'all_time':
        raise ValueError(f"Unsupported record scope: {scope!r}")

    rows = query_for_presentation(f"""
        WITH current_slots AS (
            SELECT lineup_slot, starter_count
            FROM dim_roster_slot_counts
            WHERE {league_predicate()}
              AND season_year = (
                SELECT MAX(season_year)
                FROM dim_roster_slot_counts
                WHERE {league_predicate()}
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
                -- upstream in fct_player_weekly_slot_performance) -- no COALESCE.
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
            FROM fct_player_weekly_slot_performance p
            LEFT JOIN dim_matchup_period m
                ON p.league_key = m.league_key
                AND p.season_year = m.season_year
                AND p.matchup_period = m.matchup_period
            INNER JOIN current_slots c
                ON p.lineup_slot = c.lineup_slot
            WHERE p.performance_status = 'active'
              AND p.team_id IS NOT NULL
              AND m.is_record_eligible
              AND p.total_stat_pts IS NOT NULL
              AND {league_predicate('p')}
              {season_filter}
        )
        WHERE rank <= slots_to_fill
        ORDER BY lineup_slot, rank
    """)

    return rows



def _get_rate_record_rows(scope, spec):
    """Read rate-stat records from mart_stat_leaderboard.

    v1.1.1: previously bypassed the mart and re-ranked off
    fct_team_weekly_active_performance with a Python-constant threshold
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

    rows = query_for_presentation(f"""
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
        LEFT JOIN fct_team_weekly_active_performance t
            ON l.league_key      = t.league_key
            AND l.season_year    = t.season_year
            AND l.matchup_period = t.matchup_period
            AND l.team_id        = t.team_id
        WHERE l.entity_grain       = 'team'
          AND l.performance_status = 'active'
          AND l.stat_name          = %s
          AND l.record_direction   = %s
          AND l.record_scope       = %s
          AND l.rank <= 10
          AND {league_predicate('l')}
        ORDER BY l.rank
    """, (spec['stat_name'], spec['direction'], scope))

    return records.collapse_ties(rows, max_n=1, count_fn=None)



def get_wasted_points_records(scope):
    """Return wasted-points records, which live at inactive-team grain."""
    rows = query_for_presentation(f"""
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
          AND {league_predicate()}
    """, (scope,))

    for row in rows:
        row['contributors'] = []
    return rows


# ---------------------------------------------------------------------------
# MLB-164: the two Halls. ESPN's answer to the CBS book's Franchise Hall of
# Fame and Wasted Hall of Shame -- an MLB-160 D-class missing-surface
# asymmetry. Both are ALL-TIME only, as on CBS: they are career/history
# blocks, not a current-vs-all-time matrix, so they sit below the record
# matrix rather than inside it.
# ---------------------------------------------------------------------------


def get_franchise_hall_of_fame(limit=25):
    """Top player-x-franchise careers by ACTIVE points -- a player's run
    WITH one team, not his whole league career.

    Substrate is fct_player_season_performance, the same career brick the
    per-team history tabs read (get_team_roster_history_stats' active_
    totals), so a Hall row equals that team page's number for the same
    player. Deliberately NOT eligibility-filtered: the Records matrix
    filters short weeks because a PEAK mark from a 10-day week isn't
    comparable to one from a 7-day week, but a career total has no such
    problem -- every player-franchise pair is summed over the same
    calendar, and dropping weeks would just under-report the run.

    Franchise identity is team_id, labelled with MAX_BY(team_abbrev,
    season_year) -- the house all-time convention (get_team_standings_
    alltime, and the per-team tabs group the same way). That folds 2025's
    '####' sentinel team 7 into its current label rather than printing the
    holding-pen token, which is why CBS's explicit '####' skip has no
    counterpart here: on ESPN the canonical-label rule already resolves it.

    MLB-128: 6dp + ORDER BY the grain. These totals are re-rounded for
    display, so rounding to 1dp here would be a double round, and the
    tie-break spelled out below only settles exact ties -- points decide
    everything else. Without it Python's stable sort falls back to the
    warehouse's row order, which has no guarantee and changes on rebuild,
    so two level players would swap between renders and one could fall off
    the [:limit] cut entirely.
    """
    # Stat columns are driven off the shared top-scorer display map rather
    # than hand-listed, so the Hall's stat line can never quietly fall
    # behind a stat the rest of the book already surfaces. Both the count
    # and its *_pts sibling are carried: format_top_scorer_stats_line ranks
    # by POINT CONTRIBUTION, not raw magnitude, and the rate helpers read
    # the counts (ab/h/b_bb/hbp/sf/tb, outs/er/p_h/p_bb -- all present in
    # the map already). Names are house constants, but they are being
    # interpolated into SQL, so they get the same guard as the standings
    # stat columns.
    stat_keys = sorted(TOP_SCORER_STAT_DISPLAY)
    for column in stat_keys:
        if not re.match(r'^[a-z][a-z0-9_]*$', column):
            raise ValueError(f"Unsafe stat column name: {column!r}")
    stat_select = ',\n                '.join(
        f'SUM({c}) AS {c}, SUM({c}_pts) AS {c}_pts' for c in stat_keys)
    return query_for_presentation(f"""
        WITH totals AS (
            SELECT
                player_id,
                team_id,
                MAX(player_name)  AS player_name,
                MAX(display_name) AS display_name,
                COUNT(DISTINCT season_year) AS service_years,
                ROUND(CAST(SUM(CAST(calculated_points AS DECIMAL(18, 6)))
                      AS DOUBLE), 6) AS active_points,
                {stat_select}
            FROM fct_player_season_performance
            WHERE {league_predicate()}
              AND performance_status = 'active'
              AND team_id IS NOT NULL
            GROUP BY player_id, team_id
        ),

        -- Canonical franchise label: the most recent season's abbrev for
        -- that team_id, so a renamed franchise reads under one banner.
        labels AS (
            SELECT
                team_id,
                MAX_BY(team_abbrev, season_year) AS team_abbrev,
                MAX_BY(team_name, season_year)   AS team_name
            FROM fct_player_season_performance
            WHERE {league_predicate()}
              AND team_id IS NOT NULL
            GROUP BY team_id
        )

        SELECT t.*, l.team_abbrev, l.team_name
        FROM totals t
        JOIN labels l ON l.team_id = t.team_id
        WHERE t.active_points > 0
        ORDER BY t.active_points DESC, t.player_id, t.team_id
        LIMIT {int(limit)}
    """)


def get_wasted_hall_of_shame(limit=25):
    """Career wasted points per player, SPLIT BY PRODUCTION TYPE, for the
    two Hall of Shame boards (pitching waste | hitting waste).

    NOT built from mart_stat_leaderboard. That mart is rank-capped at 10 per
    scope, so summing its rows to player grain would yield "the total of each
    player's ten worst weeks" -- a number that looks plausible, is wrong, and
    reconciles against nothing. This computes the same canonical three-term
    sum the mart's player_wasted_inactive_parts CTE performs, but aggregated
    to player BEFORE any ranking and with no cap.

    The split is by production type, not by player (Kyle 2026-08-03): a
    player's career waste is divided into its pitching share and its hitting
    share, each board ranks its own share independently, and a two-way player
    legitimately appears on BOTH with different totals. That is the correct
    output, not an edge case.

    Why the split partitions exactly, term by term:
      * unrostered and bench/IL come off the inactive fact, where
        calculated_hitting_pts + calculated_pitching_pts reconstructs
        calculated_points on every row (verified: 0 mismatches in 18,935).
      * negative-active does NOT split at the source. It is a per-DAY net
        measure -- the magnitude of a day whose whole-player total went
        negative -- so it has no hitting or pitching half to read. It is
        allocated here in proportion to each discipline's share of that
        week's active production magnitude. For a single-discipline player
        that puts 100% on his one side, which is every player in the league
        but one; the proration only ever engages for a genuine two-way
        week. Measured exposure is therefore the negative points on weeks
        carrying BOTH kinds of production: 17.1 of 13,140.6 career
        negative-active points, over 6 player-weeks, all Ohtani. (The
        other 13,123.5 split exactly, 0 or 1, with nothing to decide.)
        Chosen over inventing a day-grain
        re-definition because it partitions the canonical total EXACTLY,
        which is what lets the caller assert the boards reconcile.

    ⚠️ A two-way player's PITCHING side is understated at the source --
    ESPN's deployed-slot convention drops roughly half of Ohtani's real
    pitching days (MLB-174). Not corrected here and not caveated on the
    sheet; it is upstream of this block.
    """
    return query_for_presentation(f"""
        WITH labels AS (
            SELECT
                team_id,
                MAX_BY(team_abbrev, season_year) AS team_abbrev
            FROM fct_player_season_performance
            WHERE {league_predicate()}
              AND team_id IS NOT NULL
            GROUP BY team_id
        ),

        -- Terms 1 and 2, split by discipline straight off the fact.
        inactive_parts AS (
            SELECT
                player_id,
                -- The inactive fact carries player_name only; the mart's
                -- own CTE reads it the same way and lets display_name fall
                -- back to it.
                MAX(player_name)  AS player_name,
                ROUND(CAST(SUM(CAST(CASE WHEN wasted_bucket = 'FA'
                        THEN calculated_pitching_pts ELSE 0 END
                    AS DECIMAL(18, 6))) AS DOUBLE), 6) AS unrostered_pitching,
                ROUND(CAST(SUM(CAST(CASE WHEN wasted_bucket = 'FA'
                        THEN calculated_hitting_pts ELSE 0 END
                    AS DECIMAL(18, 6))) AS DOUBLE), 6) AS unrostered_hitting,
                ROUND(CAST(SUM(CAST(CASE WHEN wasted_bucket = 'ROSTERED_INACTIVE'
                        THEN calculated_pitching_pts ELSE 0 END
                    AS DECIMAL(18, 6))) AS DOUBLE), 6) AS benched_pitching,
                ROUND(CAST(SUM(CAST(CASE WHEN wasted_bucket = 'ROSTERED_INACTIVE'
                        THEN calculated_hitting_pts ELSE 0 END
                    AS DECIMAL(18, 6))) AS DOUBLE), 6) AS benched_hitting
            FROM fct_player_weekly_inactive_performance
            WHERE {league_predicate()}
              AND is_record_eligible
            GROUP BY player_id
        ),

        -- Term 3, prorated per week by discipline magnitude before summing.
        active_weeks AS (
            SELECT
                player_id,
                player_name,
                negative_points,
                calculated_pitching_pts,
                calculated_hitting_pts,
                ABS(COALESCE(calculated_pitching_pts, 0))
                    + ABS(COALESCE(calculated_hitting_pts, 0)) AS magnitude
            FROM fct_player_weekly_active_performance
            WHERE {league_predicate()}
              AND is_record_eligible
        ),

        active_parts AS (
            SELECT
                player_id,
                MAX(player_name) AS player_name,
                -- A week with production splits by magnitude share. A week
                -- with NO production either way cannot have given points
                -- back, so its share is 0/0 -- pinned to hitting so the
                -- proration always sums back to negative_points instead of
                -- silently dropping the row's contribution.
                ROUND(CAST(SUM(CAST(negative_points * CASE WHEN magnitude = 0 THEN 0
                        ELSE ABS(COALESCE(calculated_pitching_pts, 0)) / magnitude END
                    AS DECIMAL(18, 6))) AS DOUBLE), 6) AS negative_pitching,
                ROUND(CAST(SUM(CAST(negative_points * CASE WHEN magnitude = 0 THEN 1
                        ELSE ABS(COALESCE(calculated_hitting_pts, 0)) / magnitude END
                    AS DECIMAL(18, 6))) AS DOUBLE), 6) AS negative_hitting,
                ROUND(CAST(SUM(CAST(calculated_pitching_pts AS DECIMAL(18, 6)))
                    AS DOUBLE), 6) AS active_pitching,
                ROUND(CAST(SUM(CAST(calculated_hitting_pts AS DECIMAL(18, 6)))
                    AS DOUBLE), 6) AS active_hitting
            FROM active_weeks
            GROUP BY player_id
        ),

        -- "Benched Most By": the franchise that sat the most of this
        -- player's production, per discipline. Unrostered weeks have no
        -- team by construction, so only the ROSTERED_INACTIVE half votes.
        bench_by AS (
            SELECT
                player_id,
                team_id,
                ROUND(CAST(SUM(CAST(calculated_pitching_pts AS DECIMAL(18, 6)))
                    AS DOUBLE), 6) AS pitching_benched,
                ROUND(CAST(SUM(CAST(calculated_hitting_pts AS DECIMAL(18, 6)))
                    AS DOUBLE), 6) AS hitting_benched
            FROM fct_player_weekly_inactive_performance
            WHERE {league_predicate()}
              AND is_record_eligible
              AND wasted_bucket = 'ROSTERED_INACTIVE'
              AND team_id IS NOT NULL
            GROUP BY player_id, team_id
        ),

        -- The abbrev AND how much it sat, so the cell can read "AAA (233)"
        -- the way CBS's does. Without the number, "benched most by" says
        -- who without saying whether it was 8 points or 800.
        bench_pitching AS (
            SELECT b.player_id, l.team_abbrev, b.pitching_benched AS pts
            FROM bench_by b
            JOIN labels l ON l.team_id = b.team_id
            WHERE b.pitching_benched > 0
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY b.player_id
                ORDER BY b.pitching_benched DESC, b.team_id
            ) = 1
        ),

        bench_hitting AS (
            SELECT b.player_id, l.team_abbrev, b.hitting_benched AS pts
            FROM bench_by b
            JOIN labels l ON l.team_id = b.team_id
            WHERE b.hitting_benched > 0
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY b.player_id
                ORDER BY b.hitting_benched DESC, b.team_id
            ) = 1
        )

        SELECT
            COALESCE(i.player_id, a.player_id)     AS player_id,
            COALESCE(i.player_name, a.player_name)  AS player_name,
            COALESCE(i.player_name, a.player_name)  AS display_name,
            COALESCE(i.unrostered_pitching, 0)     AS unrostered_pitching,
            COALESCE(i.unrostered_hitting, 0)      AS unrostered_hitting,
            COALESCE(i.benched_pitching, 0)        AS benched_pitching,
            COALESCE(i.benched_hitting, 0)         AS benched_hitting,
            COALESCE(a.negative_pitching, 0)       AS negative_pitching,
            COALESCE(a.negative_hitting, 0)        AS negative_hitting,
            COALESCE(a.active_pitching, 0)         AS active_pitching,
            COALESCE(a.active_hitting, 0)          AS active_hitting,
            bp.team_abbrev                         AS bench_team_pitching,
            bh.team_abbrev                         AS bench_team_hitting,
            bp.pts                                 AS bench_points_pitching,
            bh.pts                                 AS bench_points_hitting
        FROM inactive_parts i
        FULL OUTER JOIN active_parts a ON a.player_id = i.player_id
        LEFT JOIN bench_pitching bp ON bp.player_id = COALESCE(i.player_id, a.player_id)
        LEFT JOIN bench_hitting  bh ON bh.player_id = COALESCE(i.player_id, a.player_id)
        ORDER BY COALESCE(i.player_id, a.player_id)
    """)


def get_wasted_career_total():
    """League-wide career wasted total from the UNCAPPED source, as the
    reconciliation target for the two Hall of Shame boards.

    One query, and it is the invariant that catches a bad join or a
    double-count in the discipline split -- the two boards' full (not
    top-N) totals must add up to this.
    """
    rows = query_for_presentation(f"""
        WITH inactive_total AS (
            SELECT ROUND(CAST(SUM(CAST(calculated_points AS DECIMAL(18, 6)))
                AS DOUBLE), 6) AS pts
            FROM fct_player_weekly_inactive_performance
            WHERE {league_predicate()} AND is_record_eligible
        ),
        negative_total AS (
            SELECT ROUND(CAST(SUM(CAST(negative_points AS DECIMAL(18, 6)))
                AS DOUBLE), 6) AS pts
            FROM fct_player_weekly_active_performance
            WHERE {league_predicate()} AND is_record_eligible
        )
        SELECT i.pts AS inactive_points,
               n.pts AS negative_points,
               i.pts + n.pts AS total_wasted
        FROM inactive_total i CROSS JOIN negative_total n
    """)
    return rows[0] if rows else {}



def get_scored_record_specs():
    """Return best-only team specs for scored and auto-tracked stats."""
    rows = query_for_presentation(f"""
        SELECT DISTINCT
            d.leaderboard_name AS stat_name,
            d.display_name,
            d.stat_category,
            d.polarity,
            d.auto_tracked
        FROM dim_stat d
        LEFT JOIN stg_scoring_settings s
            ON s.stat_name = d.stat_name
            AND {league_predicate('s')}
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



# This section's name, in BOTH books. Pinned as a constant because the
# write layer keys formatting off it AND the golden corpus contains it
# verbatim -- renaming it is a byte change to a pinned fixture.
#
# THE POINTS BOOK USED TO RENAME IT (MLB-243, "Production by Actual Lineup
# Slot", with "(as started)" on every row label). That was an overreach and
# is reverted: one section of a shared record book should not be titled two
# different ways depending on which league is reading it, and a
# parenthetical on eighteen row labels shouts a caveat the reader needs
# once. The distinction it was protecting is real, so it survives as a
# single caption -- see LINEUP_SLOT_LENS_CAPTION.
LINEUP_SLOT_SECTION = 'Lineup Slot Records'

# The one sentence that survives the rename. Kyle's wording, 2026-08-14.
#
# WHAT IT PROTECTS. This section ranks points a player scored WHILE
# ACTUALLY DEPLOYED in a slot; Home's boards rank the best player ELIGIBLE
# at a position. Those are different questions and they can name different
# players -- in the rehearsal league the best C-eligible bat was started at
# 1B all season, so Home called him the catcher and this section called
# someone else. Both numbers were right and nothing said so.
#
# The PRODUCT RULING (Kyle 2026-08-14) is that position-eligible active
# points is the lens for Home, Records and ordinary by-position
# leaderboards, and deployed slot belongs only to explicitly slot-based
# analysis. Unifying the data path is 2.0 work
# (docs/decisions/POSITION_ELIGIBLE_LENS.md); for v1.9 the caption states
# the difference and marks itself temporary. Carried by the points book
# only, so the H2H section keeps its pinned shape byte for byte.
LINEUP_SLOT_LENS_CAPTION = (
    'Actual-slot production: ranks points scored while a player was '
    'started in each lineup slot. Home boards rank position-eligible '
    'players, so the leaders may differ. Likely to align in future '
    'releases, let me know which you prefer.'
)


def get_lineup_slot_record_specs(season_long=False):
    """Return active lineup-slot point record specs for the current league.

    season_long is accepted and no longer changes the section title or the
    row labels (MLB-243 correction) -- both books call this section
    "Lineup Slot Records" and label its rows with the bare slot. Kept on
    the signature because the caller passes it positionally alongside the
    rest of the record book's format switches, and because the points
    book's caption is still selected by it one level up.
    """
    del season_long
    section = LINEUP_SLOT_SECTION
    rows = query_for_presentation(f"""
        SELECT lineup_slot, starter_count AS slots_to_fill
        FROM dim_roster_slot_counts
        WHERE {league_predicate()}
          AND season_year = (
            SELECT MAX(season_year)
            FROM dim_roster_slot_counts
            WHERE {league_predicate()}
        )
          AND is_active_lineup_slot
          AND starter_count > 0
        ORDER BY sort_order
    """)
    return [
        {
            'section': section,
            # The bare slot, in both books. A "(as started)" suffix here
            # repeated the caption's point on every row and made a
            # normal-looking table read like a disclaimer (MLB-243
            # correction).
            'label': slot_label(row['lineup_slot'], slot_rank,
                                int(row['slots_to_fill'])),
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
