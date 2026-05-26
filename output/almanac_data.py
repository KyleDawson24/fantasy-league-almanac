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
    """Fetch slot candidates and pick the all-league roster.

    Two modes:
      - ``matchup_period`` provided: returns the All-League Team for that
        specific week (rates and points scoped to that single matchup).
      - ``matchup_period=None``: returns the season-to-date all-league
        roster using slot stats accumulated through whatever data has
        been loaded for ``season_year``.

    v1.1.1 Tier 2c.5: merged the previous two public functions
    (``get_all_league_team`` and ``get_all_league_team_season_to_date``)
    into a single dispatcher. The week-scoped and season-to-date SQL
    paths are different enough to stay as private helpers below.
    """
    if matchup_period is not None:
        return _get_all_league_team_for_week(season_year, matchup_period)
    return _get_all_league_team_season_to_date(season_year)


def _get_all_league_team_for_week(season_year, matchup_period):
    """Fetch candidates and pick the all-league roster for one week."""
    candidates = query_snowflake("""
        WITH weekly AS (
            SELECT
                season_year,
                matchup_period,
                lineup_slot,
                team_id,
                team_name,
                team_abbrev,
                owner_name,
                player_id,
                player_name,
                display_name,
                platform_points,

                h, ab, b_bb, b_so, hbp, sf, hr, r, rbi, sb, cs, tb,
                singles, doubles, triples, xbh,
                w, l, k, er, outs, qs, sv, hld,
                p_h, p_bb, p_hr, p_r, cg, blk, wp,

                h_pts, ab_pts, b_bb_pts, b_so_pts, hbp_pts, sf_pts,
                hr_pts, r_pts, rbi_pts, sb_pts, cs_pts, tb_pts,
                singles_pts, doubles_pts, triples_pts, xbh_pts,
                w_pts, l_pts, k_pts, er_pts, outs_pts, qs_pts,
                sv_pts, hld_pts, p_h_pts, p_bb_pts, p_hr_pts, p_r_pts,
                cg_pts, blk_pts, wp_pts
            FROM fct_weekly_player_performance
            WHERE season_year = %s
              AND matchup_period = %s
              AND performance_status = 'active'
              AND team_id IS NOT NULL
        ),

        latest_slot_context AS (
            SELECT
                season_year,
                matchup_period,
                team_id,
                player_id,
                lineup_slot,
                pro_team
            FROM mart_daily_roster_snapshot
            WHERE season_year = %s
              AND matchup_period = %s
              AND roster_status = 'active'
              AND team_id IS NOT NULL
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY season_year, matchup_period, team_id, player_id, lineup_slot
                ORDER BY scoring_period DESC
            ) = 1
        )

        SELECT
            w.*,
            c.pro_team
        FROM weekly w
        LEFT JOIN latest_slot_context c
            ON w.season_year = c.season_year
            AND w.matchup_period = c.matchup_period
            AND w.team_id = c.team_id
            AND w.player_id = c.player_id
            AND w.lineup_slot = c.lineup_slot
    """, (season_year, matchup_period, season_year, matchup_period))

    # v1.1.1 Tier 2c.1 note: select_all_league_team lives in
    # almanac_sheets.py (will move to almanac_logic.py in 2c.3). Lazy
    # import here to avoid the data <-> logic circular at module load.
    # The data/logic separation cleans up in Tier 2c.5 alongside the
    # all-league-team consolidation.
    from almanac_sheets import select_all_league_team
    slot_caps = get_slot_capacities(season_year, matchup_period)
    return select_all_league_team(candidates, slot_caps)



def _get_all_league_team_season_to_date(season_year):
    """Fetch season-to-date slot performers and pick the all-league roster."""
    candidates = query_snowflake("""
        WITH slot_stats AS (
            SELECT
            season_year,
            lineup_slot,
            player_id,
            SUM(platform_points) AS platform_points,

            SUM(h) AS h,
            SUM(ab) AS ab,
            SUM(b_bb) AS b_bb,
            SUM(b_so) AS b_so,
            SUM(hbp) AS hbp,
            SUM(sf) AS sf,
            SUM(hr) AS hr,
            SUM(r) AS r,
            SUM(rbi) AS rbi,
            SUM(sb) AS sb,
            SUM(cs) AS cs,
            SUM(tb) AS tb,
            SUM(singles) AS singles,
            SUM(doubles) AS doubles,
            SUM(triples) AS triples,
            SUM(xbh) AS xbh,
            SUM(w) AS w,
            SUM(l) AS l,
            SUM(k) AS k,
            SUM(er) AS er,
            SUM(outs) AS outs,
            SUM(qs) AS qs,
            SUM(sv) AS sv,
            SUM(hld) AS hld,
            SUM(p_h) AS p_h,
            SUM(p_bb) AS p_bb,
            SUM(p_hr) AS p_hr,
            SUM(p_r) AS p_r,
            SUM(cg) AS cg,
            SUM(blk) AS blk,
            SUM(wp) AS wp,

            SUM(h_pts) AS h_pts,
            SUM(ab_pts) AS ab_pts,
            SUM(b_bb_pts) AS b_bb_pts,
            SUM(b_so_pts) AS b_so_pts,
            SUM(hbp_pts) AS hbp_pts,
            SUM(sf_pts) AS sf_pts,
            SUM(hr_pts) AS hr_pts,
            SUM(r_pts) AS r_pts,
            SUM(rbi_pts) AS rbi_pts,
            SUM(sb_pts) AS sb_pts,
            SUM(cs_pts) AS cs_pts,
            SUM(tb_pts) AS tb_pts,
            SUM(singles_pts) AS singles_pts,
            SUM(doubles_pts) AS doubles_pts,
            SUM(triples_pts) AS triples_pts,
            SUM(xbh_pts) AS xbh_pts,
            SUM(w_pts) AS w_pts,
            SUM(l_pts) AS l_pts,
            SUM(k_pts) AS k_pts,
            SUM(er_pts) AS er_pts,
            SUM(outs_pts) AS outs_pts,
            SUM(qs_pts) AS qs_pts,
            SUM(sv_pts) AS sv_pts,
            SUM(hld_pts) AS hld_pts,
            SUM(p_h_pts) AS p_h_pts,
            SUM(p_bb_pts) AS p_bb_pts,
            SUM(p_hr_pts) AS p_hr_pts,
            SUM(p_r_pts) AS p_r_pts,
            SUM(cg_pts) AS cg_pts,
            SUM(blk_pts) AS blk_pts,
            SUM(wp_pts) AS wp_pts
            FROM fct_weekly_player_performance
            WHERE season_year = %s
              AND performance_status = 'active'
              AND team_id IS NOT NULL
            GROUP BY 1, 2, 3
        ),

        latest_context AS (
            SELECT
                season_year,
                matchup_period,
                player_id,
                team_id,
                team_name,
                team_abbrev,
                owner_name,
                player_name,
                display_name
            FROM fct_weekly_player_performance
            WHERE season_year = %s
              AND performance_status = 'active'
              AND team_id IS NOT NULL
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY player_id
                ORDER BY matchup_period DESC
            ) = 1
        ),

        latest_player_context AS (
            SELECT
                season_year,
                player_id,
                pro_team
            FROM mart_daily_roster_snapshot
            WHERE season_year = %s
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY season_year, player_id
                ORDER BY scoring_period DESC
            ) = 1
        )

        SELECT
            s.season_year,
            c.matchup_period,
            s.lineup_slot,
            c.team_id,
            c.team_name,
            c.team_abbrev,
            c.owner_name,
            s.player_id,
            c.player_name,
            c.display_name,
            pc.pro_team,
            s.platform_points,
            'Season' AS period_label,

            s.h, s.ab, s.b_bb, s.b_so, s.hbp, s.sf, s.hr, s.r, s.rbi,
            s.sb, s.cs, s.tb, s.singles, s.doubles, s.triples, s.xbh,
            s.w, s.l, s.k, s.er, s.outs, s.qs, s.sv, s.hld,
            s.p_h, s.p_bb, s.p_hr, s.p_r, s.cg, s.blk, s.wp,

            s.h_pts, s.ab_pts, s.b_bb_pts, s.b_so_pts, s.hbp_pts, s.sf_pts,
            s.hr_pts, s.r_pts, s.rbi_pts, s.sb_pts, s.cs_pts, s.tb_pts,
            s.singles_pts, s.doubles_pts, s.triples_pts, s.xbh_pts,
            s.w_pts, s.l_pts, s.k_pts, s.er_pts, s.outs_pts, s.qs_pts,
            s.sv_pts, s.hld_pts, s.p_h_pts, s.p_bb_pts, s.p_hr_pts,
            s.p_r_pts, s.cg_pts, s.blk_pts, s.wp_pts
        FROM slot_stats s
        INNER JOIN latest_context c
            ON s.season_year = c.season_year
            AND s.player_id = c.player_id
        LEFT JOIN latest_player_context pc
            ON s.season_year = pc.season_year
            AND s.player_id = pc.player_id
    """, (season_year, season_year, season_year))

    # Same lazy-import pattern as get_all_league_team above.
    from almanac_sheets import select_all_league_team
    slot_caps = get_slot_capacities(season_year, matchup_period=None)
    return select_all_league_team(candidates, slot_caps)



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
                d.owner_name,
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
                END) AS il_days,
                ROUND(SUM(CASE
                    WHEN roster_status = 'inactive'
                        THEN COALESCE(platform_points, 0)
                    ELSE 0
                END), 1) AS bench_il_points
            FROM scoped_daily
            GROUP BY 1, 2, 3
        ),

        scoped_season AS (
            -- v1.1.1: read season-grain rollup from fct_player_season_performance
            -- (matchup_period already collapsed); the consumer GROUP BY below
            -- collapses lineup_slot to player-grain.
            SELECT 'current_season' AS scope, p.*
            FROM fct_player_season_performance p
            WHERE p.season_year = %s
              AND p.team_id IS NOT NULL
              AND p.performance_status = 'active'

            UNION ALL

            SELECT 'all_time' AS scope, p.*
            FROM fct_player_season_performance p
            WHERE p.team_id IS NOT NULL
              AND p.performance_status = 'active'
        ),

        active_totals AS (
            SELECT
                scope,
                team_id,
                player_id,
                ROUND(SUM(platform_points), 1) AS active_points,
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
            CASE
                WHEN cpt.current_fantasy_team_id = ct.team_id THEN ''
                ELSE COALESCE(cpt.current_fantasy_team, '')
            END AS current_fantasy_team,
            COALESCE(asl.active_slots_played, '') AS active_slots_played,
            rt.rostered_days,
            rt.active_days,
            rt.active_games,
            rt.bench_days,
            rt.il_days,
            rt.bench_il_points,
            COALESCE(at.active_points, 0) AS active_points,
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
        ORDER BY ct.team_name, rt.scope, rt.rostered_days DESC, pl.display_name
    """, (season_year, season_year, season_year, season_year))

    active_slot_rows = query_snowflake("""
        WITH scoped_daily AS (
            SELECT 'current_season' AS scope, d.*
            FROM mart_daily_roster_snapshot d
            WHERE d.season_year = %s
              AND d.team_id IS NOT NULL
              AND d.roster_status = 'active'

            UNION ALL

            SELECT 'all_time' AS scope, d.*
            FROM mart_daily_roster_snapshot d
            WHERE d.team_id IS NOT NULL
              AND d.roster_status = 'active'
        ),

        active_days AS (
            SELECT
                scope,
                team_id,
                player_id,
                lineup_slot,
                COUNT(DISTINCT season_year || '-' || scoring_period) AS active_days_in_slot
            FROM scoped_daily
            GROUP BY 1, 2, 3, 4
        ),

        scoped_season AS (
            -- v1.1.1: read season-grain rollup from fct_player_season_performance.
            -- Brick is already at (season, team, player, slot) grain, so the
            -- consumer-side GROUP BY just dedupes the row format below.
            SELECT 'current_season' AS scope, p.*
            FROM fct_player_season_performance p
            WHERE p.season_year = %s
              AND p.team_id IS NOT NULL
              AND p.performance_status = 'active'

            UNION ALL

            SELECT 'all_time' AS scope, p.*
            FROM fct_player_season_performance p
            WHERE p.team_id IS NOT NULL
              AND p.performance_status = 'active'
        ),

        active_points AS (
            SELECT
                scope,
                team_id,
                player_id,
                lineup_slot,
                ROUND(SUM(platform_points), 1) AS active_points_in_slot
            FROM scoped_season
            GROUP BY 1, 2, 3, 4
        )

        SELECT
            ad.scope,
            ad.team_id,
            ad.player_id,
            ad.lineup_slot,
            ad.active_days_in_slot,
            COALESCE(ap.active_points_in_slot, 0) AS active_points_in_slot
        FROM active_days ad
        LEFT JOIN active_points ap
            ON ad.scope = ap.scope
            AND ad.team_id = ap.team_id
            AND ad.player_id = ap.player_id
            AND ad.lineup_slot = ap.lineup_slot
    """, (season_year, season_year))

    if not player_rows:
        raise RuntimeError(f"No team roster history rows found for {season_year}.")

    return {
        'players': player_rows,
        'active_slots': active_slot_rows,
    }



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
                d.owner_name,
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
            owner_name,
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
    # Same lazy-import pattern as get_all_league_team: orchestrates
    # records.py-driven contributor lookups; lives in almanac_sheets.py
    # until 2c.3 moves it to almanac_logic.py.
    from almanac_sheets import _attach_almanac_contributors
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
                p.owner_name,
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
            l.owner_name,
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
            owner_name,
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
