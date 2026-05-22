"""
output/almanac_sheets.py

Google Sheets writer for the v1.1 league almanac surface.

The legacy `sheets_writer.py` module still owns the three records tabs.
This module starts the new browsable almanac surface, beginning with the
Home tab's "All-League Team of the Week" block.
"""

import math
import os
import re
import time
from collections import defaultdict

import gspread

from db import query_snowflake
import records
import stat_catalog
from formatters import fmt_avg, fmt_ip, fmt_record_value, format_top_scorer_stats_line
from sheets_writer import _get_authorized_client


HOME_TAB = 'Home'
RECORDS_TAB = 'Records'
TEAM_WEEKS_TAB = 'Team Weeks'

HOME_HEADER = [
    'Slot', 'MLB Team', 'Player', 'Fantasy Team', 'Owner',
    'Points', 'Stat Line', 'Boxscore',
]

RECORDS_HEADER = [
    'Scope', 'Record', 'Holder', 'Fantasy Team', 'Owner',
    'Value', 'Season', 'Period', 'Details', 'Boxscore',
]

HITTING_RATE_MIN_AB = 225
PITCHING_RATE_MIN_IP = 50
PITCHING_RATE_MIN_OUTS = PITCHING_RATE_MIN_IP * 3

RECORDS_MATRIX_WIDTH = 12
RECORDS_MATRIX_DETAIL_HEADER = [
    'Record',
    'Holder', 'Owner', 'Value', 'Period', 'Details',
    '',
    'Holder', 'Owner', 'Value', 'Period', 'Details',
]

TEAM_HISTORY_DETAIL_HEADER = [
    'Tm', 'Slot', 'Player', 'Team',
    'RosterDays', 'Games', 'Active Points', 'Bench/IL Points', 'ppg',
    'Avg|W-L-Sv', 'OBP|ERA', 'Slg|WHIP', 'HR|K', 'SB|BB',
]

TEAM_ROSTER_HEADER = [
    *TEAM_HISTORY_DETAIL_HEADER,
    '',
    *TEAM_HISTORY_DETAIL_HEADER,
]

TEAM_ROSTER_MATRIX_WIDTH = len(TEAM_ROSTER_HEADER)
TEAM_HISTORY_HITTER_HEADER = '__hitter_header__'
TEAM_HISTORY_PITCHER_HEADER = '__pitcher_header__'
TEAM_HISTORY_MIXED_HEADER = '__mixed_header__'
TEAM_HISTORY_HITTER_STATS = ['Avg', 'OBP', 'Slg', 'HR', 'SB']
TEAM_HISTORY_PITCHER_STATS = ['W-L (Sv)', 'ERA', 'WHIP', 'K', 'BB']
TEAM_HISTORY_MIXED_STATS = ['Avg|W-L-Sv', 'OBP|ERA', 'Slg|WHIP', 'HR|K', 'SB|BB']

TEAM_WEEKS_BASE_HEADER = [
    'Sort Key', 'Season', 'Matchup', 'Team',
]
TEAM_WEEKS_SCORE_HEADER = [
    'Hitting Points', 'Pitching Points', 'Total Points', 'Margin', 'W', 'L',
    '', 'Matchup Hit', 'Matchup Pitch', 'Matchup Total',
    '', 'Lg Avg Hit', 'Lg Avg Pitch', 'Lg Avg Total',
]
TEAM_WEEKS_RARE_STATS = {'CYC', 'NH', 'PG', 'SHO'}
TEAM_WEEKS_WHITE_TO_GREEN_STATS = {'TRIPLES', 'B_IBB', 'CYC', 'CG', 'SHO', 'PG', 'PK'}
TEAM_WEEKS_WHITE_TO_RED_STATS = {'BLK'}

SCORE_RECORD_SPECS = [
    {
        'section': 'Score Records',
        'label': 'Best Team Total Points',
        'grain': 'team',
        'stat_name': 'CALCULATED_POINTS',
        'direction': 'most',
    },
    {
        'section': 'Score Records',
        'label': 'Best Team Hitting Points',
        'grain': 'team',
        'stat_name': 'CALCULATED_HITTING_PTS',
        'direction': 'most',
    },
    {
        'section': 'Score Records',
        'label': 'Best Team Pitching Points',
        'grain': 'team',
        'stat_name': 'CALCULATED_PITCHING_PTS',
        'direction': 'most',
    },
    {
        'section': 'Score Records',
        'label': 'Best Player Total Points',
        'grain': 'player',
        'stat_name': 'CALCULATED_POINTS',
        'direction': 'most',
    },
    {
        'section': 'Score Records',
        'label': 'Best Player Hitting Points',
        'grain': 'player',
        'stat_name': 'CALCULATED_HITTING_PTS',
        'direction': 'most',
    },
    {
        'section': 'Score Records',
        'label': 'Best Player Pitching Points',
        'grain': 'player',
        'stat_name': 'CALCULATED_PITCHING_PTS',
        'direction': 'most',
    },
    {
        'section': 'Score Records',
        'spacer': True,
    },
    {
        'section': 'Score Records',
        'label': 'Worst Team Total Points',
        'grain': 'team',
        'stat_name': 'CALCULATED_POINTS',
        'direction': 'fewest',
    },
    {
        'section': 'Score Records',
        'label': 'Worst Team Hitting Points',
        'grain': 'team',
        'stat_name': 'CALCULATED_HITTING_PTS',
        'direction': 'fewest',
    },
    {
        'section': 'Score Records',
        'label': 'Worst Team Pitching Points',
        'grain': 'team',
        'stat_name': 'CALCULATED_PITCHING_PTS',
        'direction': 'fewest',
    },
    {
        'section': 'Score Records',
        'label': 'Worst Player Total Points',
        'grain': 'player',
        'stat_name': 'CALCULATED_POINTS',
        'direction': 'fewest',
    },
    {
        'section': 'Score Records',
        'label': 'Worst Player Hitting Points',
        'grain': 'player',
        'stat_name': 'CALCULATED_HITTING_PTS',
        'direction': 'fewest',
    },
    {
        'section': 'Score Records',
        'label': 'Worst Player Pitching Points',
        'grain': 'player',
        'stat_name': 'CALCULATED_PITCHING_PTS',
        'direction': 'fewest',
    },
]

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

SLOT_ORDER = {
    'C': 10,
    '1B': 20,
    '2B': 30,
    '3B': 40,
    'SS': 50,
    'IF': 60,
    'LF': 70,
    'CF': 80,
    'RF': 90,
    'OF': 100,
    'DH': 110,
    'UTIL': 120,
    'SP': 130,
    'RP': 140,
    'P': 150,
}

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


def write_almanac(sheet_id, season_year=None, matchup_period=None):
    """Write the v1.1 almanac Home tab.

    If `season_year` / `matchup_period` are omitted, the latest loaded
    matchup_period is used. The Sheets writer is intentionally separate
    from `sheets_writer.write_records()` while the almanac surface is
    being built out, so the legacy records sink remains stable.
    """
    if season_year is None or matchup_period is None:
        season_year, matchup_period = get_latest_matchup_period()

    league_id = os.getenv('LEAGUE_ID')
    weekly_team_rows = get_all_league_team(season_year, matchup_period)
    season_team_rows = get_all_league_team_season_to_date(season_year)
    rows = build_home_tab_rows(
        weekly_team_rows,
        season_team_rows,
        season_year,
        matchup_period,
        league_id=league_id,
    )
    schedule_lookup = records.load_schedule_lookup()
    records_rows = build_records_tab_rows(
        all_time_records=get_almanac_records('all_time'),
        current_season_records=get_almanac_records('current_season'),
        league_id=league_id,
        schedule_lookup=schedule_lookup,
    )
    team_week_stat_specs = get_team_week_stat_specs()
    team_week_rows = get_team_weeks(team_week_stat_specs)
    team_week_record_marks = get_team_week_record_marks(team_week_stat_specs)
    team_weeks_tab_rows = build_team_weeks_tab_rows(
        team_week_rows,
        team_week_stat_specs,
        league_id=league_id,
        schedule_lookup=schedule_lookup,
    )
    team_pages = build_team_history_tabs(
        get_team_roster_history_stats(season_year),
        season_year=season_year,
        league_id=league_id,
        slot_caps=get_roster_slot_capacities(season_year, include_inactive=True),
    )

    client = _get_authorized_client()
    spreadsheet = client.open_by_key(sheet_id)
    _replace_home_tab(spreadsheet, rows)
    _replace_records_tab(spreadsheet, records_rows)
    _replace_team_weeks_tab(
        spreadsheet,
        team_weeks_tab_rows,
        team_week_stat_specs,
        source_rows=team_week_rows,
        record_marks=team_week_record_marks,
    )
    _delete_prefixed_team_tabs(spreadsheet, {title for title, _ in team_pages})
    for title, team_page_rows in team_pages:
        _replace_team_tab(spreadsheet, title, team_page_rows)
    _sort_almanac_tabs(spreadsheet, [
        HOME_TAB, RECORDS_TAB, TEAM_WEEKS_TAB, *[title for title, _ in team_pages],
    ])

    print(
        f"[almanac] wrote {len(weekly_team_rows)} weekly + "
        f"{len(season_team_rows)} season-to-date all-league rows "
        f"{len(records_rows)} records-tab rows, {len(team_week_rows)} team-week rows "
        f"and {len(team_pages)} team roster tabs "
        f"for {season_year} MP{matchup_period} to sheet {sheet_id}"
    )


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
    """Fetch one row per team-week for the matchup archive tab."""
    if not stat_specs:
        raise RuntimeError("No scored team-week stat specs found.")

    stat_columns = [_fact_stat_column_name(spec['stat_name']) for spec in stat_specs]
    for column in stat_columns:
        if not re.match(r'^[a-z][a-z0-9_]*$', column):
            raise ValueError(f"Unsafe stat column name: {column!r}")

    stat_select = ',\n            '.join(f't.{column}' for column in stat_columns)
    rows = query_snowflake(f"""
        SELECT
            t.season_year,
            t.matchup_period,
            (t.season_year * 100 + t.matchup_period) AS sort_key,
            t.team_id,
            t.team_name,
            t.team_abbrev,
            t.opponent_id,
            t.opponent_name,
            t.result,
            t.is_abnormal,
            t.calculated_hitting_pts,
            t.calculated_pitching_pts,
            t.calculated_points,
            opp.calculated_hitting_pts AS opponent_calculated_hitting_pts,
            opp.calculated_pitching_pts AS opponent_calculated_pitching_pts,
            opp.calculated_points AS opponent_calculated_points,
            t.calculated_points - opp.calculated_points AS calculated_margin,
            t.calculated_hitting_pts + opp.calculated_hitting_pts AS matchup_calculated_hitting_pts,
            t.calculated_pitching_pts + opp.calculated_pitching_pts AS matchup_calculated_pitching_pts,
            t.calculated_points + opp.calculated_points AS matchup_calculated_points,
            AVG(t.calculated_hitting_pts) OVER (
                PARTITION BY t.season_year, t.matchup_period
            ) AS league_avg_hitting_points,
            AVG(t.calculated_pitching_pts) OVER (
                PARTITION BY t.season_year, t.matchup_period
            ) AS league_avg_pitching_points,
            AVG(t.calculated_points) OVER (
                PARTITION BY t.season_year, t.matchup_period
            ) AS league_avg_total_points,
            {stat_select}
        FROM fct_weekly_team_active_performance t
        LEFT JOIN fct_weekly_team_active_performance opp
            ON t.season_year = opp.season_year
            AND t.matchup_period = opp.matchup_period
            AND t.opponent_id = opp.team_id
            AND t.team_id = opp.opponent_id
        WHERE t.opponent_id IS NOT NULL
        ORDER BY sort_key DESC, t.calculated_points DESC, t.team_name
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


def get_all_league_team(season_year, matchup_period):
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

    slot_caps = get_slot_capacities(season_year, matchup_period)
    return select_all_league_team(candidates, slot_caps)


def get_all_league_team_season_to_date(season_year):
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

        scoped_weekly AS (
            SELECT 'current_season' AS scope, p.*
            FROM fct_weekly_player_performance p
            WHERE p.season_year = %s
              AND p.team_id IS NOT NULL
              AND p.performance_status = 'active'

            UNION ALL

            SELECT 'all_time' AS scope, p.*
            FROM fct_weekly_player_performance p
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
            FROM scoped_weekly
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

        scoped_weekly AS (
            SELECT 'current_season' AS scope, p.*
            FROM fct_weekly_player_performance p
            WHERE p.season_year = %s
              AND p.team_id IS NOT NULL
              AND p.performance_status = 'active'

            UNION ALL

            SELECT 'all_time' AS scope, p.*
            FROM fct_weekly_player_performance p
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
            FROM scoped_weekly
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


def _attach_almanac_contributors(record_rows):
    """Attach contributor details after tie-collapse trims visible rows."""
    real_rows = [r for r in record_rows if not r.get('is_collapsed')]
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
    positive_player_tuples = [
        (r['season_year'], r['matchup_period'], r['player_id'])
        for r in real_rows
        if (
            r['entity_grain'] == 'player'
            and r['player_id'] is not None
            and r.get('record_direction') != 'fewest'
        )
    ]
    balanced_player_tuples = [
        (r['season_year'], r['matchup_period'], r['player_id'])
        for r in real_rows
        if (
            r['entity_grain'] == 'player'
            and r['player_id'] is not None
            and r.get('record_direction') == 'fewest'
        )
    ]

    team_contribs = records.get_team_contributors_bulk(team_tuples) if team_tuples else {}
    player_contribs = {}
    if positive_player_tuples:
        player_contribs.update(records.get_player_contributors_bulk(positive_player_tuples))
    if balanced_player_tuples:
        player_contribs.update(records.get_player_contributors_bulk(
            balanced_player_tuples,
            positives_only=False,
        ))

    for row in record_rows:
        if row.get('is_collapsed'):
            row['contributors'] = []
        elif row['entity_grain'] == 'team':
            key = (
                row['season_year'],
                row['matchup_period'],
                row['team_id'],
                row['stat_name'],
            )
            row['contributors'] = team_contribs.get(key, [])
        else:
            key = (row['season_year'], row['matchup_period'], row['player_id'])
            row['contributors'] = player_contribs.get(key, [])


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
    source_column = spec['source_column']
    min_column = spec['min_column']
    sort_direction = 'ASC' if spec['direction'] == 'fewest' else 'DESC'
    if min_column == 'outs':
        min_filter = f"{min_column} >= {PITCHING_RATE_MIN_OUTS}"
    else:
        min_filter = f"{min_column} >= {HITTING_RATE_MIN_AB}"
    season_filter = ""
    if scope == 'current_season':
        season_filter = """
          AND season_year = (
              SELECT MAX(season_year)
              FROM fct_weekly_team_active_performance
          )
        """
    elif scope != 'all_time':
        raise ValueError(f"Unsupported record scope: {scope!r}")

    rows = query_snowflake(f"""
        SELECT
            'team' AS entity_grain,
            %s AS stat_name,
            %s AS record_direction,
            rank,
            season_year,
            matchup_period,
            team_id,
            team_name,
            team_abbrev,
            owner_name,
            NULL::integer AS player_id,
            NULL::varchar AS player_name,
            NULL::varchar AS display_name,
            stat_value,
            qualifier_value
        FROM (
            SELECT
                season_year,
                matchup_period,
                team_id,
                team_name,
                team_abbrev,
                owner_name,
                {source_column} AS stat_value,
                {min_column} AS qualifier_value,
                ROW_NUMBER() OVER (
                    ORDER BY {source_column} {sort_direction},
                             season_year DESC,
                             matchup_period DESC,
                             team_id
                ) AS rank
            FROM fct_weekly_team_active_performance
            WHERE is_abnormal = false
              AND {source_column} IS NOT NULL
              AND {min_filter}
              {season_filter}
        )
        WHERE rank <= 10
        ORDER BY rank
    """, (spec['stat_name'], spec['direction']))

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


def select_all_league_team(candidates, slot_caps):
    """Pick top active performers by actual lineup slot.

    The comprehensive weekly fact preserves `lineup_slot`, so this uses
    the same roster-slot lens ESPN users recognize. If a player appears
    in multiple active slots during a week, keep only their highest-
    scoring slot row so the all-league roster never selects the same
    player twice.
    """
    best_by_player = {}
    for row in candidates:
        player_id = row.get('player_id')
        if player_id is None:
            continue
        current = best_by_player.get(player_id)
        if current is None or _candidate_sort_key(row) < _candidate_sort_key(current):
            best_by_player[player_id] = row

    by_slot = defaultdict(list)
    for row in best_by_player.values():
        slot = row.get('lineup_slot')
        if slot in slot_caps:
            by_slot[slot].append(row)

    selected = []
    for slot in sorted(slot_caps, key=_slot_sort_key):
        capacity = slot_caps[slot]
        rows = sorted(by_slot.get(slot, []), key=_candidate_sort_key)
        for slot_rank, row in enumerate(rows[:capacity], 1):
            out = dict(row)
            out['slot_rank'] = slot_rank
            out['slots_to_fill'] = capacity
            out['slot_label'] = slot_label(slot, slot_rank, capacity)
            selected.append(out)

    return selected


def build_home_tab_rows(weekly_rows, season_rows, season_year, matchup_period,
                        league_id=None):
    """Build the Home tab value matrix."""
    rows = [
        ['Fantasy Beat Reporter Almanac'],
        [f'All-League Team of the Week: {season_year} Week {matchup_period}'],
        [],
        HOME_HEADER,
    ]
    rows.extend([
        format_all_league_team_row(row, league_id=league_id)
        for row in weekly_rows
    ])
    rows.extend([
        [],
        [f'All-League Team Season-to-Date: {season_year}'],
        [],
        HOME_HEADER,
    ])
    rows.extend([
        format_all_league_team_row(row, league_id=league_id)
        for row in season_rows
    ])
    return rows


def build_team_weeks_tab_rows(team_week_rows, stat_specs, league_id=None,
                              schedule_lookup=None):
    """Build the team-week matchup archive tab."""
    schedule_lookup = schedule_lookup or {}
    hitting_specs = _team_week_specs_for_category(stat_specs, 'hitting')
    pitching_specs = _team_week_specs_for_category(stat_specs, 'pitching')
    header = [
        *TEAM_WEEKS_BASE_HEADER,
        *_team_week_stat_headers(hitting_specs),
        '',
        *_team_week_stat_headers(pitching_specs),
        '',
        *TEAM_WEEKS_SCORE_HEADER,
    ]
    rows = [header]
    for row in team_week_rows:
        rows.append(format_team_week_row(
            row,
            hitting_specs,
            pitching_specs,
            league_id=league_id,
            schedule_lookup=schedule_lookup,
        ))
    return rows


def format_team_week_row(row, hitting_specs, pitching_specs, league_id=None,
                         schedule_lookup=None):
    """Project one team-week fact row into the archive tab layout."""
    schedule_lookup = schedule_lookup or {}
    result = row.get('result') or ''
    matchup_label = records.format_week_label(
        row.get('season_year'),
        row.get('matchup_period'),
        schedule_lookup,
    )
    return [
        row.get('sort_key') or '',
        row.get('season_year') or '',
        _period_boxscore_formula(
            matchup_label,
            league_id,
            row.get('season_year'),
            row.get('matchup_period'),
            row.get('team_id'),
        ),
        row.get('team_name') or '',
        *[_format_team_week_stat(row, spec) for spec in hitting_specs],
        '',
        *[_format_team_week_stat(row, spec) for spec in pitching_specs],
        '',
        _one_decimal(row.get('calculated_hitting_pts')),
        _one_decimal(row.get('calculated_pitching_pts')),
        _one_decimal(row.get('calculated_points')),
        _one_decimal(row.get('calculated_margin')),
        1 if result == 'W' else '',
        1 if result == 'L' else '',
        '',
        _one_decimal(row.get('matchup_calculated_hitting_pts')),
        _one_decimal(row.get('matchup_calculated_pitching_pts')),
        _one_decimal(row.get('matchup_calculated_points')),
        '',
        _one_decimal(row.get('league_avg_hitting_points')),
        _one_decimal(row.get('league_avg_pitching_points')),
        _one_decimal(row.get('league_avg_total_points')),
    ]


def build_records_tab_rows(all_time_records, current_season_records, league_id=None,
                           display_map=None, schedule_lookup=None, record_specs=None):
    """Build the almanac Records tab as a side-by-side record book."""
    display_map = display_map or stat_catalog.get_display_map()
    schedule_lookup = schedule_lookup or records.load_schedule_lookup()
    record_specs = record_specs or [
        *SCORE_RECORD_SPECS,
        *get_scored_record_specs(),
        *RATE_RECORD_SPECS,
        *get_lineup_slot_record_specs(),
    ]

    all_time_index = _index_records(all_time_records)
    current_index = _index_records(current_season_records)

    rows = [
        ['League Records'],
        [
            'Counting Stats only look at standard-length matchups. '
            f'Pitching Rate stats require min {PITCHING_RATE_MIN_IP} IP, '
            f'Hitting Rate stats require min {HITTING_RATE_MIN_AB} AB. '
            'Boxscore links go to the most recent instance of the record.'
        ],
        [],
    ]

    for section_title, specs in _group_record_specs(record_specs):
        section_rows = []
        for spec in specs:
            if spec.get('spacer'):
                if section_rows and section_rows[-1] != []:
                    section_rows.append([])
                continue
            current_record = current_index.get(_spec_key(spec))
            all_time_record = all_time_index.get(_spec_key(spec))
            if _record_never_occurred(current_record):
                current_record = None
            if _record_never_occurred(all_time_record):
                all_time_record = None
            if current_record or all_time_record:
                section_rows.append(format_record_matrix_row(
                    spec,
                    current_record=current_record,
                    all_time_record=all_time_record,
                    league_id=league_id,
                    display_map=display_map,
                    schedule_lookup=schedule_lookup,
                ))

        if section_rows:
            rows.extend([
                _records_matrix_scope_header(section_title),
                RECORDS_MATRIX_DETAIL_HEADER,
            ])
            rows.extend(section_rows)
            rows.append([])

    if rows and rows[-1] == []:
        rows.pop()
    return rows


def _group_record_specs(record_specs):
    """Group record specs by section while preserving first-seen order."""
    grouped = []
    by_section = {}
    for spec in record_specs:
        section = spec.get('section') or 'Records'
        if section not in by_section:
            by_section[section] = []
            grouped.append((section, by_section[section]))
        by_section[section].append(spec)
    return grouped


def _records_matrix_scope_header(section_title):
    return [
        section_title,
        'Current Season', '', '', '', '',
        '',
        'All-Time', '', '', '', '',
    ]


def _spec_key(spec):
    if spec.get('spacer'):
        return None
    return (
        spec.get('grain'),
        spec.get('stat_name'),
        spec.get('direction'),
    )


def _record_never_occurred(record):
    """Suppress positive-event records whose top value is still zero."""
    if not record:
        return False
    return (
        record.get('record_direction') == 'most'
        and (record.get('stat_value') or 0) == 0
        and record.get('stat_name') not in {
            'CALCULATED_POINTS',
            'CALCULATED_HITTING_PTS',
            'CALCULATED_PITCHING_PTS',
            'PLATFORM_POINTS',
            'PLATFORM_HITTING_PTS',
            'PLATFORM_PITCHING_PTS',
        }
        and not str(record.get('stat_name') or '').startswith('LINEUP_SLOT_POINTS__')
    )


def _index_records(record_rows):
    """Index records by (grain, stat, direction) for curated lookup."""
    return {
        (
            row.get('entity_grain'),
            row.get('stat_name'),
            row.get('record_direction'),
        ): row
        for row in record_rows
    }


def build_team_roster_tabs(roster_rows, season_year, league_id=None, slot_caps=None):
    """Build one team active-stat roster tab per fantasy team."""
    grouped = defaultdict(list)
    for row in roster_rows:
        grouped[row.get('team_id')].append(row)

    tabs = []
    for team_id in sorted(grouped):
        team_rows = expand_team_roster_rows(grouped[team_id], slot_caps)
        first = team_rows[0]
        title = team_tab_title(first)
        scoring_period = first.get('latest_scoring_period')
        rows = [
            [first.get('team_name') or f'Team {team_id}'],
            [
                f"{season_year} roster snapshot"
                + (f" through scoring period {scoring_period}" if scoring_period else "")
            ],
            [],
            TEAM_ROSTER_HEADER,
        ]
        rows.extend([
            format_team_roster_row(row, league_id=league_id)
            for row in team_rows
        ])
        tabs.append((title, rows))

    return tabs


def expand_team_roster_rows(team_rows, slot_caps=None):
    """Add blank rows for configured roster slots with no current player."""
    if not slot_caps:
        return team_rows

    expanded = list(team_rows)
    by_slot = defaultdict(list)
    for row in team_rows:
        by_slot[row.get('lineup_slot')].append(row)

    template = team_rows[0] if team_rows else {}
    for slot in sorted(slot_caps, key=_slot_sort_key):
        capacity = slot_caps[slot]
        existing = len(by_slot.get(slot, []))
        for slot_rank in range(existing + 1, capacity + 1):
            expanded.append(_blank_roster_row(template, slot, slot_rank, capacity))

    return sorted(
        expanded,
        key=lambda r: (
            _slot_sort_key(r.get('lineup_slot')),
            int(r.get('slot_rank') or 1),
            r.get('display_name') or r.get('player_name') or '',
        ),
    )


def _blank_roster_row(template, slot, slot_rank, slots_to_fill):
    """Build an empty roster-slot placeholder row."""
    return {
        'season_year': template.get('season_year'),
        'latest_matchup_period': template.get('latest_matchup_period'),
        'latest_scoring_period': template.get('latest_scoring_period'),
        'latest_matchup_end_date': template.get('latest_matchup_end_date'),
        'team_id': template.get('team_id'),
        'team_name': template.get('team_name'),
        'team_abbrev': template.get('team_abbrev'),
        'owner_name': template.get('owner_name'),
        'lineup_slot': slot,
        'slot_rank': slot_rank,
        'slots_to_fill': slots_to_fill,
        'is_empty_slot': True,
    }


def build_team_history_tabs(history_data, season_year, league_id=None, slot_caps=None):
    """Build side-by-side current-season/all-time roster history tabs."""
    del league_id
    slot_caps = slot_caps or {}
    players = history_data.get('players') or []
    active_slots = history_data.get('active_slots') or []

    teams = {}
    players_by_team_scope = defaultdict(list)
    for row in players:
        team_id = row.get('team_id')
        scope = row.get('scope')
        if team_id is None or not scope:
            continue
        teams.setdefault(team_id, row)
        players_by_team_scope[(team_id, scope)].append(row)

    active_by_team_scope = defaultdict(list)
    for row in active_slots:
        team_id = row.get('team_id')
        scope = row.get('scope')
        if team_id is None or not scope:
            continue
        active_by_team_scope[(team_id, scope)].append(row)

    tabs = []
    for team_id in sorted(teams, key=lambda tid: _team_sort_key(teams[tid])):
        team_meta = teams[team_id]
        current_rows = build_team_history_side(
            players_by_team_scope[(team_id, 'current_season')],
            active_by_team_scope[(team_id, 'current_season')],
            slot_caps,
        )
        all_time_rows = build_team_history_side(
            players_by_team_scope[(team_id, 'all_time')],
            active_by_team_scope[(team_id, 'all_time')],
            slot_caps,
        )
        row_labels = _team_history_row_labels(current_rows, all_time_rows)
        period_end_date = _format_sheet_date(team_meta.get('latest_matchup_end_date'))
        rows = [
            [team_meta.get('team_name') or f'Team {team_id}'],
            [
                f"{season_year} roster history"
                + (f" through {period_end_date}" if period_end_date else "")
            ],
            [],
            _team_history_scope_header(),
            TEAM_ROSTER_HEADER,
        ]
        for label in row_labels:
            rows.append(format_team_history_matrix_row(
                label,
                current_rows.get(label),
                all_time_rows.get(label),
            ))
        tabs.append((team_tab_title(team_meta), rows))

    return tabs


def build_team_history_side(player_rows, active_slot_rows, slot_caps):
    """Arrange one scope of team/player history into roster-shaped rows."""
    players = {
        row.get('player_id'): row
        for row in player_rows
        if row.get('player_id') is not None
    }

    active_by_slot = defaultdict(list)
    for row in active_slot_rows:
        player = players.get(row.get('player_id'))
        if not player:
            continue
        candidate = dict(player)
        candidate.update(row)
        active_by_slot[row.get('lineup_slot')].append(candidate)

    active_caps = {
        slot: count
        for slot, count in slot_caps.items()
        if slot not in {'BE', 'IL'}
    }
    selected_ids = set()
    output = {}

    for slot in sorted(active_caps, key=_slot_sort_key):
        capacity = active_caps[slot]
        candidates = sorted(
            active_by_slot.get(slot, []),
            key=lambda r: (
                -int(r.get('active_days_in_slot') or 0),
                -float(r.get('active_points_in_slot') or 0),
                r.get('display_name') or r.get('player_name') or '',
            ),
        )
        chosen = []
        for candidate in candidates:
            player_id = candidate.get('player_id')
            if player_id in selected_ids:
                continue
            chosen.append(candidate)
            selected_ids.add(player_id)
            if len(chosen) == capacity:
                break
        for slot_rank, row in enumerate(chosen, 1):
            label = slot_label(slot, slot_rank, capacity)
            output[label] = _team_history_display_row(
                row,
                label,
                display_slot=label,
            )

    remaining = [
        row for row in players.values()
        if row.get('player_id') not in selected_ids
    ]

    bench_count = int(slot_caps.get('BE') or 0)
    bench_candidates = sorted(
        remaining,
        key=lambda r: (
            -float(r.get('active_points') or 0),
            -int(r.get('rostered_days') or 0),
            r.get('display_name') or r.get('player_name') or '',
        ),
    )
    for slot_rank, row in enumerate(bench_candidates[:bench_count], 1):
        label = slot_label('BE', slot_rank, bench_count)
        position = _inactive_position_display(row)
        output[label] = _team_history_display_row(
            row,
            label,
            display_slot=_compact_inactive_slot('BE', position),
        )
        selected_ids.add(row.get('player_id'))

    remaining = [
        row for row in players.values()
        if row.get('player_id') not in selected_ids
    ]

    il_count = int(slot_caps.get('IL') or 0)
    il_candidates = [
        row for row in remaining
        if int(row.get('il_days') or 0) > 0
    ]
    il_candidates.sort(
        key=lambda r: (
            -int(r.get('il_days') or 0),
            -int(r.get('rostered_days') or 0),
            r.get('display_name') or r.get('player_name') or '',
        ),
    )
    for slot_rank in range(1, il_count + 1):
        label = slot_label('IL', slot_rank, il_count)
        if slot_rank <= len(il_candidates):
            row = il_candidates[slot_rank - 1]
            position = _inactive_position_display(row)
            output[label] = _team_history_display_row(
                row,
                label,
                display_slot=_compact_inactive_slot('IL', position),
            )
            selected_ids.add(row.get('player_id'))
        else:
            output[label] = _empty_team_history_display_row()

    remaining = [
        row for row in players.values()
        if row.get('player_id') not in selected_ids
    ]
    remaining.sort(
        key=lambda r: (
            -float(r.get('active_points') or 0),
            -int(r.get('rostered_days') or 0),
            r.get('display_name') or r.get('player_name') or '',
        ),
    )
    for row_number, row in enumerate(remaining, 1):
        label = f'Other {row_number}'
        position = _inactive_position_display(row)
        output[label] = _team_history_display_row(
            row,
            label,
            display_slot=_compact_inactive_slot('Other', position),
        )

    return output


def _team_history_row_labels(current_rows, all_time_rows):
    base_labels = [label for label in current_rows if not label.startswith('Other ')]
    labels = list(base_labels)
    for label in all_time_rows:
        if not label.startswith('Other ') and label not in labels:
            labels.append(label)
    labels = _insert_before_first(
        labels,
        TEAM_HISTORY_HITTER_HEADER,
        _is_hitter_team_history_label,
    )
    labels = _insert_before_first(
        labels,
        TEAM_HISTORY_PITCHER_HEADER,
        _is_pitcher_team_history_label,
    )
    labels = _insert_before_first(
        labels,
        TEAM_HISTORY_MIXED_HEADER,
        _is_mixed_team_history_label,
    )
    other_count = max(
        _max_other_index(current_rows),
        _max_other_index(all_time_rows),
    )
    labels.extend(f'Other {i}' for i in range(1, other_count + 1))
    if other_count:
        labels.insert(len(labels) - other_count, '')
    return labels


def _insert_before_first(labels, marker, predicate):
    for index, label in enumerate(labels):
        if predicate(label):
            return [*labels[:index], marker, *labels[index:]]
    return labels


def _is_hitter_team_history_label(label):
    return label and not _is_pitcher_team_history_label(label) and not _is_mixed_team_history_label(label)


def _is_pitcher_team_history_label(label):
    return str(label).startswith(('SP', 'RP', 'P '))


def _is_mixed_team_history_label(label):
    return str(label).startswith(('BE', 'IL'))


def _max_other_index(rows):
    max_index = 0
    for label in rows:
        if label.startswith('Other '):
            try:
                max_index = max(max_index, int(label.split(' ', 1)[1]))
            except (IndexError, ValueError):
                pass
    return max_index


def _team_history_scope_header():
    left_span = len(TEAM_HISTORY_DETAIL_HEADER)
    return [
        'Current Season',
        *([''] * (left_span - 1)),
        '',
        'All-Time',
        *([''] * (left_span - 1)),
    ]


def format_team_history_matrix_row(label, current_row=None, all_time_row=None):
    if label == TEAM_HISTORY_HITTER_HEADER:
        return _team_history_section_header_row(TEAM_HISTORY_HITTER_STATS)
    if label == TEAM_HISTORY_PITCHER_HEADER:
        return _team_history_section_header_row(TEAM_HISTORY_PITCHER_STATS)
    if label == TEAM_HISTORY_MIXED_HEADER:
        return _team_history_section_header_row(TEAM_HISTORY_MIXED_STATS)
    return [
        *_team_history_side_cells(current_row),
        '',
        *_team_history_side_cells(all_time_row),
    ]


def _team_history_section_header_row(stat_labels):
    side = [''] * len(TEAM_HISTORY_DETAIL_HEADER)
    side[9:] = stat_labels
    return [*side, '', *side]


def _team_history_display_row(row, label, display_slot=None, active_games=None,
                              active_points=None):
    active_games = int(active_games if active_games is not None else row.get('active_games') or 0)
    active_points = _one_decimal(
        active_points if active_points is not None else row.get('active_points')
    )
    stat_line = _team_history_stat_line(row, display_slot or label)
    return {
        'slot_label': label,
        'display_slot': display_slot or label,
        'player': row.get('display_name') or row.get('player_name') or '',
        'pro_team': row.get('pro_team') or '',
        'current_fantasy_team': row.get('current_fantasy_team') or '',
        'rostered_days': int(row.get('rostered_days') or 0),
        'active_games': active_games,
        'active_points': _round_half_up(active_points),
        'bench_il_points': _round_half_up(float(row.get('bench_il_points') or 0)),
        'points_per_active_game': (
            active_points / active_games if active_games else ''
        ),
        **stat_line,
    }


def _empty_team_history_display_row():
    return {
        'display_slot': '',
        'player': '',
        'pro_team': '',
        'current_fantasy_team': '',
        'rostered_days': '',
        'active_games': '',
        'active_points': '',
        'bench_il_points': '',
        'points_per_active_game': '',
        'stat_1': '',
        'stat_2': '',
        'stat_3': '',
        'stat_4': '',
        'stat_5': '',
    }


def _team_history_side_cells(row):
    row = row or _empty_team_history_display_row()
    return [
        row.get('current_fantasy_team') or '',
        row.get('display_slot') or '',
        row.get('player') or '',
        row.get('pro_team') or '',
        row.get('rostered_days'),
        row.get('active_games'),
        row.get('active_points'),
        row.get('bench_il_points'),
        row.get('points_per_active_game'),
        row.get('stat_1'),
        row.get('stat_2'),
        row.get('stat_3'),
        row.get('stat_4'),
        row.get('stat_5'),
    ]


def _team_history_stat_line(row, display_slot):
    if _team_history_is_pitcher(row, display_slot):
        return {
            'stat_1': _pitching_decision_display(row, display_slot),
            'stat_2': _pitching_rate(row, 'era'),
            'stat_3': _pitching_rate(row, 'whip'),
            'stat_4': int(row.get('k') or 0),
            'stat_5': int(row.get('p_bb') or 0),
        }
    return {
        'stat_1': _hitting_rate(row, 'avg'),
        'stat_2': _hitting_rate(row, 'obp'),
        'stat_3': _hitting_rate(row, 'slg'),
        'stat_4': int(row.get('hr') or 0),
        'stat_5': int(row.get('sb') or 0),
    }


def _team_history_is_pitcher(row, display_slot):
    slots = _display_slot_tokens(display_slot)
    if not slots:
        slots = _display_slot_tokens(row.get('active_slots_played'))
    if 'SP' in slots or 'RP' in slots:
        return True
    if 'P' in slots:
        return True
    pitching_volume = sum(row.get(k) or 0 for k in ('outs', 'k', 'sv', 'w', 'l'))
    hitting_volume = sum(row.get(k) or 0 for k in ('ab', 'h', 'hr', 'sb'))
    return pitching_volume > hitting_volume


def _display_slot_tokens(value):
    cleaned = str(value or '').replace('-', ',').replace(' ', ',')
    return {part.strip() for part in cleaned.split(',') if part.strip()}


def _pitching_decision_display(row, display_slot):
    wins = int(row.get('w') or 0)
    losses = int(row.get('l') or 0)
    saves = int(row.get('sv') or 0)
    return f"{wins}-{losses}" if wins + losses >= saves else saves


def _hitting_rate(row, stat_name):
    at_bats = row.get('ab') or 0
    if not at_bats:
        return ''
    hits = row.get('h') or 0
    if stat_name == 'avg':
        return _rate_as_whole_number(hits / at_bats)
    if stat_name == 'obp':
        denominator = at_bats + (row.get('b_bb') or 0) + (row.get('hbp') or 0) + (row.get('sf') or 0)
        return _rate_as_whole_number(
            (hits + (row.get('b_bb') or 0) + (row.get('hbp') or 0)) / denominator
        ) if denominator else ''
    if stat_name == 'slg':
        return _rate_as_whole_number((row.get('tb') or 0) / at_bats)
    return ''


def _rate_as_whole_number(value):
    return f"{int(round(value * 1000)):03d}"


def _round_half_up(value):
    if value < 0:
        return math.ceil(value - 0.5)
    return math.floor(value + 0.5)


def _pitching_rate(row, stat_name):
    outs = row.get('outs') or 0
    if not outs:
        return ''
    innings = outs / 3
    if stat_name == 'era':
        return f"{((row.get('er') or 0) * 9 / innings):.2f}"
    if stat_name == 'whip':
        return f"{(((row.get('p_bb') or 0) + (row.get('p_h') or 0)) / innings):.2f}"
    return ''


def _inactive_position_display(row):
    return row.get('active_slots_played') or row.get('position') or ''


def _compact_inactive_slot(slot, position):
    clean_position = ','.join(part.strip() for part in str(position or '').split(',') if part.strip())
    if clean_position:
        return f'{slot} - {clean_position}'
    return slot


def format_all_league_team_row(row, league_id=None):
    """Project one selected slot row into the Home tab table shape."""
    season = row.get('season_year')
    matchup_period = row.get('matchup_period')
    team_id = row.get('team_id')
    is_season_row = row.get('period_label') == 'Season'
    boxscore = '' if is_season_row else boxscore_formula(
        league_id, season, matchup_period, team_id,
    )
    return [
        row.get('slot_label') or row.get('lineup_slot') or '',
        row.get('pro_team') or '',
        row.get('display_name') or row.get('player_name') or '',
        row.get('team_name') or '',
        row.get('owner_name') or '',
        _one_decimal(row.get('platform_points')),
        format_top_scorer_stats_line(row),
        boxscore,
    ]


def format_record_matrix_row(spec, current_record=None, all_time_record=None,
                             league_id=None, display_map=None, schedule_lookup=None):
    """Project current/all-time holders into one side-by-side record row."""
    display_map = display_map or stat_catalog.get_display_map()
    schedule_lookup = schedule_lookup or records.load_schedule_lookup()
    return [
        spec.get('label') or display_map.get(spec.get('stat_name'), spec.get('stat_name')),
        *_format_record_side(
            current_record,
            scope='current_season',
            league_id=league_id,
            display_map=display_map,
            schedule_lookup=schedule_lookup,
        ),
        '',
        *_format_record_side(
            all_time_record,
            scope='all_time',
            league_id=league_id,
            display_map=display_map,
            schedule_lookup=schedule_lookup,
        ),
    ]


def _format_record_side(record, scope, league_id=None, display_map=None, schedule_lookup=None):
    """Format one current-season or all-time side of a matrix row."""
    if not record:
        return ['', '', '', '', '']

    display_map = display_map or stat_catalog.get_display_map()
    schedule_lookup = schedule_lookup or records.load_schedule_lookup()

    season = record.get('season_year')
    matchup_period = record.get('matchup_period')
    if record.get('is_collapsed'):
        holder = _collapsed_holder(record)
        owner = _collapsed_owner(record)
        period = _collapsed_period(record, schedule_lookup, scope=scope)
    elif record.get('entity_grain') == 'player':
        holder = record.get('display_name') or record.get('player_name') or ''
        owner = record.get('owner_name') or ''
        period = (
            records.format_week_label(season, matchup_period, schedule_lookup)
            if season is not None and matchup_period is not None
            else ''
        )
    else:
        holder = record.get('team_abbrev') or record.get('team_name') or ''
        owner = record.get('owner_name') or ''
        period = (
            records.format_week_label(season, matchup_period, schedule_lookup)
            if season is not None and matchup_period is not None
            else ''
        )
    if scope == 'all_time' and not record.get('is_collapsed') and period and season:
        period = f"{period}: {season}"
    period = _period_boxscore_formula(
        period, league_id, season, matchup_period, record.get('team_id')
    )
    return [
        holder,
        owner,
        _format_record_value(record.get('stat_name'), record.get('stat_value')),
        period,
        _record_details(record, display_map),
    ]


def format_record_row(record, scope_label, league_id=None, display_map=None,
                      polarity_map=None, schedule_lookup=None):
    """Project one record holder into the curated Records tab shape."""
    display_map = display_map or stat_catalog.get_display_map()
    polarity_map = polarity_map or stat_catalog.get_polarity_map()
    schedule_lookup = schedule_lookup or records.load_schedule_lookup()

    grain = record.get('entity_grain')
    stat_name = record.get('stat_name')
    direction = record.get('record_direction')
    season = record.get('season_year')
    matchup_period = record.get('matchup_period')
    record_label = _record_label(record, display_map, polarity_map)

    if record.get('is_collapsed'):
        holder = _collapsed_holder(record)
        fantasy_team = ''
        owner = ''
        boxscore = ''
    elif grain == 'player':
        holder = record.get('display_name') or record.get('player_name') or ''
        fantasy_team = record.get('team_name') or ''
        owner = record.get('owner_name') or ''
        boxscore = boxscore_formula(league_id, season, matchup_period, record.get('team_id'))
    else:
        holder = record.get('team_name') or ''
        fantasy_team = record.get('team_abbrev') or ''
        owner = record.get('owner_name') or ''
        boxscore = boxscore_formula(league_id, season, matchup_period, record.get('team_id'))

    period = (
        records.format_week_label(season, matchup_period, schedule_lookup)
        if season is not None and matchup_period is not None
        else ''
    )

    return [
        scope_label,
        record_label,
        holder,
        fantasy_team,
        owner,
        _format_record_value(stat_name, record.get('stat_value')),
        season or '',
        period,
        _record_details(record, display_map),
        boxscore,
    ]


def _record_label(record, display_map, polarity_map):
    """Build a compact label like 'Best Team Total Points'."""
    grain = (record.get('entity_grain') or '').title()
    stat_name = record.get('stat_name')
    direction = record.get('record_direction')
    outcome = records.best_or_worst_label(stat_name, direction, polarity_map)
    stat_label = display_map.get(stat_name, stat_name)
    return f"{outcome} {grain} {stat_label}".strip()


def _collapsed_holder(record):
    """Render a collapsed top tied tier."""
    holders = record.get('holders') or []
    grain = record.get('entity_grain')
    if holders:
        if grain == 'player':
            return ', '.join(h.get('display_name') or '' for h in holders)
        return ', '.join(h.get('team_abbrev') or h.get('team_name') or '' for h in holders)
    unit = 'players' if grain == 'player' else 'teams'
    return f"{record.get('tie_count', 0)} {unit} tied"


def _collapsed_owner(record):
    """Render owner names for a small collapsed tied tier."""
    holders = record.get('holders') or []
    if not holders:
        return ''
    owners = [h.get('owner_name') or '' for h in holders]
    return ', '.join(owners)


def _collapsed_season(record):
    """Render seasons for collapsed tiers, compacting single-season ties."""
    holders = record.get('holders') or []
    if not holders:
        return record.get('season_year') or ''
    seasons = [str(h.get('season_year')) for h in holders if h.get('season_year')]
    unique = []
    for season in seasons:
        if season not in unique:
            unique.append(season)
    return unique[0] if len(unique) == 1 else ', '.join(unique)


def _collapsed_period(record, schedule_lookup, scope='current_season'):
    """Render holder-period pairs for small collapsed tied tiers."""
    holders = record.get('holders') or []
    if not holders:
        season = record.get('season_year')
        matchup_period = record.get('matchup_period')
        return (
            _period_label(season, matchup_period, schedule_lookup, scope)
            if season is not None and matchup_period is not None
            else ''
        )

    parts = []
    for holder in holders:
        season = holder.get('season_year')
        matchup_period = holder.get('matchup_period')
        if season is None or matchup_period is None:
            continue
        name = (
            holder.get('team_abbrev')
            or holder.get('display_name')
            or holder.get('team_name')
            or holder.get('player_name')
            or ''
        )
        period = _period_label(season, matchup_period, schedule_lookup, scope)
        parts.append(f"{name} {period}".strip())
    return '; '.join(parts)


def _period_label(season, matchup_period, schedule_lookup, scope):
    label = records.format_week_label(season, matchup_period, schedule_lookup)
    if scope == 'all_time' and season:
        return f"{label}: {season}"
    return label


def _record_details(record, display_map):
    """Render contributor context for a record row."""
    rate_detail = _rate_qualifier_detail(record)
    if record.get('is_collapsed'):
        value = _format_record_value(record.get('stat_name'), record.get('stat_value'))
        detail = f"Top tier tied at {value}"
        return f"{detail}; {rate_detail}" if rate_detail else detail

    if rate_detail:
        return rate_detail

    contributors = record.get('contributors') or []
    if not contributors:
        return ''

    parts = []
    if record.get('entity_grain') == 'team':
        stat_name = record.get('stat_name')
        for item in contributors[:3]:
            value = fmt_record_value(stat_name, item.get('stat_value'))
            parts.append(f"{item.get('display_name')}: {value}")
    else:
        for item in contributors[:3]:
            stat_name = item.get('stat_name')
            label = _detail_stat_label(stat_name, display_map)
            value = fmt_record_value(stat_name, item.get('count_value'))
            parts.append(f"{value} {label}")
    return ', '.join(parts)


def _detail_stat_label(stat_name, display_map):
    label = display_map.get(stat_name, stat_name)
    replacements = {
        'Innings Pitched': 'IP',
        'Strikeouts (Pitcher)': 'K',
        'Strikeouts (Batter)': 'K',
        'Home Runs': 'HR',
        'Quality Starts': 'Quality Starts',
        'RBIs': 'RBI',
    }
    return replacements.get(label, label)


def _rate_qualifier_detail(record):
    """Render AB/IP qualification context for team rate records."""
    stat_name = record.get('stat_name')
    qualifier = record.get('qualifier_value')
    if qualifier is None:
        return ''
    if stat_name in {'AVG', 'OBP', 'SLG'}:
        return f"{int(qualifier)} AB"
    if stat_name in {'ERA', 'WHIP', 'K_PER_9', 'K_PER_BB', 'HR_PER_9', 'BB_PER_9'}:
        return f"{fmt_ip(qualifier)} IP"
    return ''


def _format_record_value(stat_name, value):
    """Almanac-specific record value rendering."""
    if value is None:
        return ''
    if stat_name in {'AVG', 'OBP', 'SLG'}:
        return fmt_avg(value)
    if stat_name in {'ERA', 'WHIP', 'K_PER_9', 'K_PER_BB', 'HR_PER_9', 'BB_PER_9'}:
        return f"{value:.2f}"
    if str(stat_name or '').startswith('LINEUP_SLOT_POINTS__'):
        return f"{value:.1f}"
    return fmt_record_value(stat_name, value)


def format_team_roster_row(row, league_id=None):
    """Project one current roster row into a team active-stat table row."""
    slot = slot_label(
        row.get('lineup_slot'),
        int(row.get('slot_rank') or 1),
        int(row.get('slots_to_fill') or 1),
    )
    if row.get('is_empty_slot'):
        return [slot, *([''] * (len(TEAM_ROSTER_HEADER) - 1))]

    return [
        slot,
        row.get('pro_team') or '',
        row.get('display_name') or row.get('player_name') or '',
        row.get('position') or '',
        _one_decimal(row.get('active_points')),
        int(row.get('active_weeks') or 0),
        int(row.get('active_days') or 0),
        int(row.get('rostered_days') or 0),
        _one_decimal(row.get('inactive_points')),
        int(row.get('hr') or 0),
        int(row.get('rbi') or 0),
        int(row.get('r') or 0),
        int(row.get('sb') or 0),
        int(row.get('w') or 0),
        int(row.get('sv') or 0),
        int(row.get('hld') or 0),
        int(row.get('k') or 0),
        fmt_ip(row.get('outs')),
    ]


def team_tab_title(row):
    """Return a compact worksheet title for a team roster page."""
    team_id = row.get('team_id')
    name = row.get('team_abbrev') or row.get('team_name') or str(team_id or '')
    return _safe_sheet_title(name)


def _team_sort_key(row):
    title = team_tab_title(row)
    try:
        team_id = int(row.get('team_id'))
    except (TypeError, ValueError):
        team_id = 9999
    return (title.casefold(), team_id)


def boxscore_formula(league_id, season_year, matchup_period, team_id):
    """Return a Google Sheets HYPERLINK formula for an ESPN boxscore."""
    if not (league_id and season_year and matchup_period and team_id):
        return ''
    url = _boxscore_url(league_id, season_year, matchup_period, team_id)
    return f'=HYPERLINK("{url}", "boxscore")'


def _period_boxscore_formula(label, league_id, season_year, matchup_period, team_id):
    """Return a HYPERLINK formula whose visible text is the period label."""
    if not label:
        return ''
    if not (league_id and season_year and matchup_period and team_id):
        return label
    url = _boxscore_url(league_id, season_year, matchup_period, team_id)
    safe_label = str(label).replace('"', '""')
    return f'=HYPERLINK("{url}", "{safe_label}")'


def _boxscore_url(league_id, season_year, matchup_period, team_id):
    return (
        'https://fantasy.espn.com/baseball/boxscore?'
        f'leagueId={league_id}&matchupPeriodId={matchup_period}'
        f'&seasonId={season_year}&teamId={team_id}&view=matchup'
    )


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


def _team_week_specs_for_category(stat_specs, category):
    return [spec for spec in stat_specs if spec.get('stat_category') == category]


def _team_week_stat_headers(stat_specs):
    return [_team_week_stat_header(spec) for spec in stat_specs]


def _team_week_stat_header(spec):
    stat_name = spec.get('stat_name')
    if stat_name == 'OUTS':
        return 'IP'
    return spec.get('abbrev') or spec.get('display_name') or stat_name


def _format_team_week_stat(row, spec):
    stat_name = spec.get('stat_name')
    value = row.get(_fact_stat_column_name(stat_name))
    if stat_name == 'OUTS':
        return fmt_ip(value)
    if value is None:
        return ''
    if float(value).is_integer():
        return int(value)
    return round(value, 3)


def _fact_stat_column_name(stat_name):
    special = {
        'OUTS': 'outs',
    }
    return special.get(stat_name, str(stat_name or '').lower())


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


def _is_rare_team_week_stat(stat_name):
    return stat_name in TEAM_WEEKS_RARE_STATS


def slot_label(slot, slot_rank, slots_to_fill):
    """Display repeated roster slots as OF 1 / OF 2, etc."""
    if slots_to_fill and slots_to_fill > 1:
        return f"{slot} {slot_rank}"
    return slot


def _slot_sort_key(slot):
    return (SLOT_ORDER.get(slot, 999), slot)


def _candidate_sort_key(row):
    points = row.get('platform_points') or 0
    display_name = row.get('display_name') or row.get('player_name') or ''
    slot = row.get('lineup_slot') or ''
    return (-points, _slot_sort_key(slot), display_name)


def _one_decimal(value):
    return round(value or 0, 1)


def _safe_sheet_title(title):
    bad_chars = set('[]:*?/\\')
    cleaned = ''.join('-' if c in bad_chars else c for c in str(title))
    cleaned = cleaned.strip("'").strip()
    return cleaned[:100] or 'Sheet'


def _format_sheet_date(value):
    if not value:
        return ''
    if hasattr(value, 'strftime'):
        return value.strftime('%b %-d, %Y') if os.name != 'nt' else value.strftime('%b %#d, %Y')
    text = str(value)
    try:
        from datetime import date
        return date.fromisoformat(text[:10]).strftime('%b %#d, %Y' if os.name == 'nt' else '%b %-d, %Y')
    except ValueError:
        return text


def _replace_home_tab(spreadsheet, rows):
    """Clear/create Home and write the almanac front page."""
    try:
        worksheet = spreadsheet.worksheet(HOME_TAB)
    except gspread.WorksheetNotFound:
        worksheet = _sheets_call(
            f'create {HOME_TAB}',
            lambda: spreadsheet.add_worksheet(
                title=HOME_TAB, rows=max(len(rows) + 10, 50), cols=len(HOME_HEADER),
            ),
        )

    _sheets_call(f'clear {HOME_TAB}', worksheet.clear)
    _sheets_call(
        f'update {HOME_TAB}',
        lambda: worksheet.update(rows, 'A1', value_input_option='USER_ENTERED'),
    )

    # Basic first-pass polish. This is deliberately restrained; visual
    # iteration will happen once the live Sheet can be eyeballed.
    try:
        _sheets_call(f'freeze {HOME_TAB}', lambda: worksheet.freeze(rows=4))
        _batch_format(worksheet, [
            {
                'range': 'A1:H1',
                'format': {'textFormat': {'bold': True, 'fontSize': 14}},
            },
            {
                'range': 'A2:H2',
                'format': {
                    'textFormat': {'bold': True},
                    'backgroundColor': {'red': 0.90, 'green': 0.94, 'blue': 0.98},
                },
            },
            {
                'range': 'A4:H4',
                'format': {
                    'textFormat': {
                        'bold': True,
                        'foregroundColor': {'red': 1, 'green': 1, 'blue': 1},
                    },
                    'backgroundColor': {'red': 0.12, 'green': 0.20, 'blue': 0.30},
                },
            },
            {
                'range': 'F:F',
                'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0.0'}},
            },
        ])
    except Exception as exc:
        print(f"[almanac] formatting skipped: {exc}")


def _replace_team_weeks_tab(spreadsheet, rows, stat_specs, source_rows=None, record_marks=None):
    """Recreate and write the team-week matchup archive tab."""
    try:
        worksheet = spreadsheet.worksheet(TEAM_WEEKS_TAB)
        _sheets_call(
            f'delete {TEAM_WEEKS_TAB}',
            lambda: spreadsheet.del_worksheet(worksheet),
        )
    except gspread.WorksheetNotFound:
        pass

    worksheet = _sheets_call(
        f'create {TEAM_WEEKS_TAB}',
        lambda: spreadsheet.add_worksheet(
            title=TEAM_WEEKS_TAB,
            rows=max(len(rows) + 10, 50),
            cols=max(len(rows[0]) if rows else 1, 20),
        ),
    )
    _sheets_call(
        f'update {TEAM_WEEKS_TAB}',
        lambda: worksheet.update(rows, 'A1', value_input_option='USER_ENTERED'),
    )

    try:
        _sheets_call(f'freeze {TEAM_WEEKS_TAB}', lambda: worksheet.freeze(rows=1))
        _apply_team_weeks_tab_dimensions(spreadsheet, worksheet, rows, stat_specs)
        _apply_team_weeks_conditional_formats(
            spreadsheet,
            worksheet,
            rows,
            stat_specs,
            source_rows=source_rows,
        )
        _apply_team_weeks_record_formats(
            spreadsheet,
            worksheet,
            stat_specs,
            source_rows=source_rows,
            record_marks=record_marks,
        )
        layout = _team_weeks_layout(stat_specs)
        score_start_col = _a1_col(layout['score_start'] + 1)
        score_end_col = _a1_col(layout['score_start'] + 4)
        matchup_start_col = _a1_col(layout['score_start'] + 8)
        matchup_end_col = _a1_col(layout['score_start'] + 10)
        avg_start_col = _a1_col(layout['score_start'] + 12)
        avg_end_col = _a1_col(layout['score_start'] + 14)
        _batch_format(worksheet, [
            {
                'range': f'A1:{_a1_col(len(rows[0]))}1',
                'format': {
                    'textFormat': {
                        'bold': True,
                        'foregroundColor': {'red': 1, 'green': 1, 'blue': 1},
                    },
                    'backgroundColor': {'red': 0.12, 'green': 0.20, 'blue': 0.30},
                },
            },
            {
                'range': f'A:{_a1_col(len(rows[0]))}',
                'format': {'wrapStrategy': 'OVERFLOW_CELL'},
            },
            {
                'range': f'{score_start_col}:{score_end_col}',
                'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0.0'}},
            },
            {
                'range': f'{matchup_start_col}:{matchup_end_col}',
                'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0.0'}},
            },
            {
                'range': f'{avg_start_col}:{avg_end_col}',
                'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0.0'}},
            },
        ])
    except Exception as exc:
        print(f"[almanac] formatting skipped for {TEAM_WEEKS_TAB}: {exc}")


def _replace_records_tab(spreadsheet, rows):
    """Clear/create Records and write the curated record-book tab."""
    try:
        worksheet = spreadsheet.worksheet(RECORDS_TAB)
    except gspread.WorksheetNotFound:
        worksheet = _sheets_call(
            f'create {RECORDS_TAB}',
            lambda: spreadsheet.add_worksheet(
                title=RECORDS_TAB,
                rows=max(len(rows) + 10, 50),
                cols=RECORDS_MATRIX_WIDTH,
            ),
        )

    _sheets_call(f'clear {RECORDS_TAB}', worksheet.clear)
    _sheets_call(
        f'update {RECORDS_TAB}',
        lambda: worksheet.update(rows, 'A1', value_input_option='USER_ENTERED'),
    )

    try:
        _sheets_call(f'freeze {RECORDS_TAB}', lambda: worksheet.freeze(rows=3))
        _apply_records_tab_dimensions(spreadsheet, worksheet)
        _merge_records_scope_headers(spreadsheet, worksheet, rows)
        formats = [
            {
                'range': 'A:L',
                'format': {
                    'textFormat': {'bold': False, 'italic': False},
                    'backgroundColor': {'red': 1, 'green': 1, 'blue': 1},
                    'wrapStrategy': 'OVERFLOW_CELL',
                },
            },
            {
                'range': 'A1:L1',
                'format': {'textFormat': {'bold': True, 'fontSize': 14}},
            },
            {
                'range': 'A2:L2',
                'format': {
                    'textFormat': {'italic': True},
                    'backgroundColor': {'red': 0.95, 'green': 0.97, 'blue': 0.99},
                },
            },
        ]
        formats.extend(_records_header_formats(rows))
        formats.extend(_fresh_record_formats(rows))
        formats.extend(_records_score_value_formats(rows))
        _batch_format(worksheet, formats)
    except Exception as exc:
        print(f"[almanac] formatting skipped for {RECORDS_TAB}: {exc}")


def _records_header_formats(rows):
    """Return batched formats for section labels and matrix headers."""
    formats = []
    for row_number, row in enumerate(rows, 1):
        if _is_records_scope_header(row) or row == RECORDS_MATRIX_DETAIL_HEADER:
            formats.append({
                'range': f'A{row_number}:L{row_number}',
                'format': {
                    'textFormat': {'bold': True},
                    'backgroundColor': {'red': 0.95, 'green': 0.97, 'blue': 0.99},
                },
            })
    return formats


def _is_records_scope_header(row):
    return (
        len(row) == RECORDS_MATRIX_WIDTH
        and row[1] == 'Current Season'
        and row[7] == 'All-Time'
    )


def _fresh_record_formats(rows):
    """Return batched formats for fresh current-season/all-time records."""
    formats = []
    season_year, matchup_period = get_latest_matchup_period()
    schedule_lookup = records.load_schedule_lookup()
    latest_period = records.format_week_label(season_year, matchup_period, schedule_lookup)

    for row_number, row in enumerate(rows, 1):
        if len(row) < RECORDS_MATRIX_WIDTH:
            continue
        current_period = row[4]
        all_time_period = row[10]
        if (
            isinstance(current_period, str)
            and latest_period in current_period
            and _record_side_is_small_tie(row[1])
        ):
            formats.append({
                'range': f'A{row_number}:F{row_number}',
                'format': {'textFormat': {'italic': True}},
            })
        if (
            isinstance(all_time_period, str)
            and f': {season_year}' in all_time_period
            and _record_side_is_small_tie(row[7])
        ):
            formats.append({
                'range': f'H{row_number}:L{row_number}',
                'format': {'textFormat': {'italic': True}},
            })
    return formats


def _record_side_is_small_tie(holder):
    """Return true when a side is a single holder or a listed tie of 3 or fewer."""
    holder_text = str(holder or '').strip()
    if not holder_text:
        return False
    match = re.match(r'^(\d+)\s+\w+\s+tied$', holder_text)
    if match:
        return int(match.group(1)) < 4
    return True


def _records_score_value_formats(rows):
    """Force score/points values to one decimal without affecting count stats."""
    formats = []
    active_section = ''
    for row_number, row in enumerate(rows, 1):
        if _is_records_scope_header(row):
            active_section = row[0]
            continue
        if len(row) < RECORDS_MATRIX_WIDTH:
            continue
        if active_section == 'Score Records' or active_section == 'Lineup Slot Records':
            formats.extend([
                {
                    'range': f'D{row_number}:D{row_number}',
                    'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0.0'}},
                },
                {
                    'range': f'J{row_number}:J{row_number}',
                    'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0.0'}},
                },
            ])
    return formats


def _replace_team_tab(spreadsheet, title, rows):
    """Clear/create one fantasy team roster tab and write rows."""
    try:
        worksheet = spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        worksheet = _sheets_call(
            f'create {title}',
            lambda: spreadsheet.add_worksheet(
                title=title,
                rows=max(len(rows) + 10, 50),
                cols=TEAM_ROSTER_MATRIX_WIDTH,
            ),
        )

    _sheets_call(f'clear {title}', worksheet.clear)
    _sheets_call(
        f'update {title}',
        lambda: worksheet.update(rows, 'A1', value_input_option='RAW'),
    )

    try:
        _sheets_call(f'freeze {title}', lambda: worksheet.freeze(rows=5))
        _apply_team_tab_dimensions(spreadsheet, worksheet)
        formats = [
            {
                'range': 'A1:AC1',
                'format': {'textFormat': {'bold': True, 'fontSize': 13}},
            },
            {
                'range': 'A2:AC2',
                'format': {
                    'textFormat': {'italic': True},
                    'backgroundColor': {'red': 0.95, 'green': 0.97, 'blue': 0.99},
                },
            },
            {
                'range': 'A4:AC5',
                'format': {
                    'textFormat': {
                        'bold': True,
                        'foregroundColor': {'red': 1, 'green': 1, 'blue': 1},
                    },
                    'backgroundColor': {'red': 0.12, 'green': 0.20, 'blue': 0.30},
                },
            },
            {
                'range': 'A5:A',
                'format': {'textFormat': {'fontSize': 5}},
            },
            {
                'range': 'P5:P',
                'format': {'textFormat': {'fontSize': 5}},
            },
            {
                'range': 'G:H',
                'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0'}},
            },
            {
                'range': 'V:W',
                'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0'}},
            },
            {
                'range': 'I:I',
                'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0.0'}},
            },
            {
                'range': 'X:X',
                'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0.0'}},
            },
            {
                'range': 'E:F',
                'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0'}},
            },
            {
                'range': 'T:U',
                'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0'}},
            },
        ]
        for row_number, row in enumerate(rows, 1):
            if len(row) > 9 and row[9] in {'Avg', 'W-L (Sv)', 'Avg|W-L-Sv'}:
                text_format = {'bold': True}
                if row_number <= 5:
                    text_format['foregroundColor'] = {'red': 1, 'green': 1, 'blue': 1}
                formats.extend([
                    {
                        'range': f'J{row_number}:N{row_number}',
                        'format': {'textFormat': text_format},
                    },
                    {
                        'range': f'Y{row_number}:AC{row_number}',
                        'format': {'textFormat': text_format},
                    },
                ])
            if _is_active_display_slot(row[1] if len(row) > 1 else ''):
                formats.append({
                    'range': f'B{row_number}:B{row_number}',
                    'format': {'horizontalAlignment': 'RIGHT'},
                })
            if _is_active_display_slot(row[16] if len(row) > 16 else ''):
                formats.append({
                    'range': f'Q{row_number}:Q{row_number}',
                    'format': {'horizontalAlignment': 'RIGHT'},
                })
            if _is_pitcher_display_slot(row[1] if len(row) > 1 else ''):
                formats.extend([
                    {
                        'range': f'J{row_number}:N{row_number}',
                        'format': {'horizontalAlignment': 'LEFT'},
                    },
                ])
            elif _is_hitter_display_slot(row[1] if len(row) > 1 else ''):
                formats.extend([
                    {
                        'range': f'J{row_number}:N{row_number}',
                        'format': {'horizontalAlignment': 'RIGHT'},
                    },
                ])
            if _is_pitcher_display_slot(row[16] if len(row) > 16 else ''):
                formats.extend([
                    {
                        'range': f'Y{row_number}:AC{row_number}',
                        'format': {'horizontalAlignment': 'LEFT'},
                    },
                ])
            elif _is_hitter_display_slot(row[16] if len(row) > 16 else ''):
                formats.extend([
                    {
                        'range': f'Y{row_number}:AC{row_number}',
                        'format': {'horizontalAlignment': 'RIGHT'},
                    },
                ])
        _batch_format(worksheet, formats)
    except Exception as exc:
        print(f"[almanac] formatting skipped for {title}: {exc}")


def _apply_records_tab_dimensions(spreadsheet, worksheet):
    sheet_id = worksheet.id
    requests = [
        _column_width_request(sheet_id, 0, 1, 175),
        _column_width_request(sheet_id, 5, 6, 400),
        _column_width_request(sheet_id, 11, 12, 400),
    ]
    _sheets_batch_update(spreadsheet, f'format dimensions {worksheet.title}', requests)


def _apply_team_weeks_tab_dimensions(spreadsheet, worksheet, rows, stat_specs):
    sheet_id = worksheet.id
    layout = _team_weeks_layout(stat_specs)
    total_cols = len(rows[0]) if rows else 1
    requests = [
        _column_width_request(sheet_id, 0, 1, 70),
        _hidden_columns_request(sheet_id, 0, 1, hidden=True),
        _column_width_request(sheet_id, 1, 2, 70),
        _column_width_request(sheet_id, 2, 3, 90),
        _column_width_request(sheet_id, 3, 4, 175),
        _column_width_request(sheet_id, layout['hitting_start'], layout['hitting_end'], 42),
        _column_width_request(sheet_id, layout['hitting_spacer'], layout['hitting_spacer'] + 1, 12),
        _column_width_request(sheet_id, layout['pitching_start'], layout['pitching_end'], 42),
        _column_width_request(sheet_id, layout['pitching_spacer'], layout['pitching_spacer'] + 1, 12),
        _column_width_request(sheet_id, layout['score_start'], layout['score_start'] + 4, 85),
        _column_width_request(sheet_id, layout['score_start'] + 4, layout['score_start'] + 6, 38),
        _column_width_request(sheet_id, layout['score_start'] + 6, layout['score_start'] + 7, 32),
        _column_width_request(sheet_id, layout['score_start'] + 7, layout['score_start'] + 10, 95),
        _column_width_request(sheet_id, layout['score_start'] + 10, layout['score_start'] + 11, 32),
        _column_width_request(sheet_id, layout['score_start'] + 11, total_cols, 90),
    ]
    for column in _team_weeks_rare_column_indices(stat_specs):
        requests.append(_hidden_columns_request(sheet_id, column, column + 1, hidden=True))
    _sheets_batch_update(spreadsheet, f'format dimensions {worksheet.title}', requests)


def _apply_team_weeks_conditional_formats(spreadsheet, worksheet, rows, stat_specs,
                                          source_rows=None):
    if len(rows) <= 1:
        return
    sheet_id = worksheet.id
    layout = _team_weeks_layout(stat_specs)
    row_ranges = _team_weeks_standard_data_ranges(sheet_id, len(rows), source_rows)
    requests = []

    for column, spec in _team_weeks_stat_column_pairs(stat_specs):
        stat_name = spec.get('stat_name')
        if stat_name in TEAM_WEEKS_WHITE_TO_GREEN_STATS:
            requests.append(_color_scale_request(
                sheet_id, column, len(rows), scale='two_good', row_ranges=row_ranges,
            ))
        elif stat_name in TEAM_WEEKS_WHITE_TO_RED_STATS:
            requests.append(_color_scale_request(
                sheet_id, column, len(rows), scale='two_bad', row_ranges=row_ranges,
            ))
        else:
            scale = 'three_good_high'
            if (spec.get('points_per_unit') or 0) < 0:
                scale = 'three_good_low'
            requests.append(_color_scale_request(
                sheet_id, column, len(rows), scale=scale, row_ranges=row_ranges,
            ))

    for column in [
        layout['score_start'],
        layout['score_start'] + 1,
        layout['score_start'] + 2,
        layout['score_start'] + 3,
        layout['score_start'] + 7,
        layout['score_start'] + 8,
        layout['score_start'] + 9,
        layout['score_start'] + 11,
        layout['score_start'] + 12,
        layout['score_start'] + 13,
    ]:
        requests.append(_color_scale_request(
            sheet_id, column, len(rows), scale='three_good_high', row_ranges=row_ranges,
        ))

    _sheets_batch_update(spreadsheet, f'conditional formats {worksheet.title}', requests)


def _apply_team_weeks_record_formats(spreadsheet, worksheet, stat_specs,
                                     source_rows=None, record_marks=None):
    if not source_rows or not record_marks:
        return
    sheet_id = worksheet.id
    requests = []
    for column_index, spec in _team_weeks_stat_column_pairs(stat_specs):
        mark = record_marks.get(spec.get('stat_name'))
        if not mark or _is_zeroish(mark.get('value')):
            continue
        for source_index, source_row in enumerate(source_rows):
            if source_row.get('is_abnormal'):
                continue
            if not _numeric_values_equal(source_row.get(_fact_stat_column_name(spec.get('stat_name'))), mark.get('value')):
                continue
            text_format = {'bold': True}
            if int(mark.get('holder_count') or 0) == 1:
                text_format['foregroundColor'] = {'red': 0.72, 'green': 0.48, 'blue': 0.00}
            requests.append(_cell_format_request(
                sheet_id,
                row_index=source_index + 1,
                column_index=column_index,
                cell_format={'textFormat': text_format},
            ))
    _sheets_batch_update(spreadsheet, f'record formats {worksheet.title}', requests)


def _team_weeks_standard_data_ranges(sheet_id, row_count, source_rows=None):
    """Return contiguous non-abnormal data-row ranges for conditional formatting."""
    if not source_rows:
        return [{'sheetId': sheet_id, 'startRowIndex': 1, 'endRowIndex': row_count}]

    ranges = []
    start = None
    for source_index, source_row in enumerate(source_rows):
        row_index = source_index + 1
        is_standard = not source_row.get('is_abnormal')
        if is_standard and start is None:
            start = row_index
        elif not is_standard and start is not None:
            ranges.append({'sheetId': sheet_id, 'startRowIndex': start, 'endRowIndex': row_index})
            start = None
    if start is not None:
        ranges.append({
            'sheetId': sheet_id,
            'startRowIndex': start,
            'endRowIndex': min(len(source_rows) + 1, row_count),
        })
    return ranges


def _numeric_values_equal(left, right):
    if left is None or right is None:
        return False
    try:
        return math.isclose(float(left), float(right), rel_tol=0, abs_tol=0.000001)
    except (TypeError, ValueError):
        return str(left) == str(right)


def _is_zeroish(value):
    if value is None:
        return False
    try:
        return math.isclose(float(value), 0.0, rel_tol=0, abs_tol=0.000001)
    except (TypeError, ValueError):
        return False


def _team_weeks_layout(stat_specs):
    hitting_count = len(_team_week_specs_for_category(stat_specs, 'hitting'))
    pitching_count = len(_team_week_specs_for_category(stat_specs, 'pitching'))
    hitting_start = len(TEAM_WEEKS_BASE_HEADER)
    hitting_end = hitting_start + hitting_count
    hitting_spacer = hitting_end
    pitching_start = hitting_spacer + 1
    pitching_end = pitching_start + pitching_count
    pitching_spacer = pitching_end
    score_start = pitching_spacer + 1
    return {
        'hitting_start': hitting_start,
        'hitting_end': hitting_end,
        'hitting_spacer': hitting_spacer,
        'pitching_start': pitching_start,
        'pitching_end': pitching_end,
        'pitching_spacer': pitching_spacer,
        'score_start': score_start,
    }


def _team_weeks_stat_column_pairs(stat_specs):
    layout = _team_weeks_layout(stat_specs)
    pairs = []
    hitting_specs = _team_week_specs_for_category(stat_specs, 'hitting')
    pitching_specs = _team_week_specs_for_category(stat_specs, 'pitching')
    pairs.extend((layout['hitting_start'] + index, spec) for index, spec in enumerate(hitting_specs))
    pairs.extend((layout['pitching_start'] + index, spec) for index, spec in enumerate(pitching_specs))
    return pairs


def _team_weeks_rare_column_indices(stat_specs):
    return [
        column
        for column, spec in _team_weeks_stat_column_pairs(stat_specs)
        if _is_rare_team_week_stat(spec.get('stat_name'))
    ]


def _merge_records_scope_headers(spreadsheet, worksheet, rows):
    sheet_id = worksheet.id
    requests = []
    for row_index, row in enumerate(rows):
        if not _is_records_scope_header(row):
            continue
        for start_col, end_col in [(1, 6), (7, 12)]:
            grid_range = {
                'sheetId': sheet_id,
                'startRowIndex': row_index,
                'endRowIndex': row_index + 1,
                'startColumnIndex': start_col,
                'endColumnIndex': end_col,
            }
            requests.append({'unmergeCells': {'range': grid_range}})
            requests.append({'mergeCells': {'range': grid_range, 'mergeType': 'MERGE_ALL'}})
    _sheets_batch_update(spreadsheet, f'merge scope headers {worksheet.title}', requests)


def _delete_prefixed_team_tabs(spreadsheet, current_titles):
    """Remove obsolete team tabs from the old T## naming scheme."""
    for worksheet in spreadsheet.worksheets():
        title = worksheet.title
        if title in current_titles:
            continue
        if re.match(r'^T\d{2}\s+', title):
            _sheets_call(
                f'delete old team tab {title}',
                lambda worksheet=worksheet: spreadsheet.del_worksheet(worksheet),
            )


def _sort_almanac_tabs(spreadsheet, ordered_titles):
    worksheets_by_title = {worksheet.title: worksheet for worksheet in spreadsheet.worksheets()}
    requests = []
    for index, title in enumerate(ordered_titles):
        worksheet = worksheets_by_title.get(title)
        if not worksheet:
            continue
        requests.append({
            'updateSheetProperties': {
                'properties': {
                    'sheetId': worksheet.id,
                    'index': index,
                },
                'fields': 'index',
            },
        })
    _sheets_batch_update(spreadsheet, 'sort almanac tabs', requests)


def _apply_team_tab_dimensions(spreadsheet, worksheet):
    sheet_id = worksheet.id
    requests = []
    for start_index, width in [
        (0, 25),    # Tm
        (1, 75),    # Slot
        (3, 40),    # Team
        (4, 75),    # RosterDays
        (5, 75),    # Games
        (6, 75),    # Active Points
        (7, 75),    # Bench/IL Points
        (8, 40),    # ppg
        (14, 15),   # spacer
        (15, 25),   # Tm
        (16, 75),   # Slot
        (18, 40),   # Team
        (19, 75),   # RosterDays
        (20, 75),   # Games
        (21, 75),   # Active Points
        (22, 75),   # Bench/IL Points
        (23, 40),   # ppg
    ]:
        requests.append(_column_width_request(sheet_id, start_index, start_index + 1, width))
    requests.extend([
        _column_width_request(sheet_id, 9, 14, 80),
        _column_width_request(sheet_id, 24, 29, 80),
        _auto_resize_columns_request(sheet_id, 2, 3),
        _auto_resize_columns_request(sheet_id, 17, 18),
    ])
    _sheets_batch_update(spreadsheet, f'format dimensions {worksheet.title}', requests)


def _column_width_request(sheet_id, start_index, end_index, pixel_size):
    return {
        'updateDimensionProperties': {
            'range': {
                'sheetId': sheet_id,
                'dimension': 'COLUMNS',
                'startIndex': start_index,
                'endIndex': end_index,
            },
            'properties': {'pixelSize': pixel_size},
            'fields': 'pixelSize',
        },
    }


def _hidden_columns_request(sheet_id, start_index, end_index, hidden=True):
    return {
        'updateDimensionProperties': {
            'range': {
                'sheetId': sheet_id,
                'dimension': 'COLUMNS',
                'startIndex': start_index,
                'endIndex': end_index,
            },
            'properties': {'hiddenByUser': hidden},
            'fields': 'hiddenByUser',
        },
    }


def _color_scale_request(sheet_id, column_index, row_count, scale='three_good_high',
                         row_ranges=None):
    if scale in {'two_good', 'two_bad'}:
        max_color = {'red': 0.67, 'green': 0.86, 'blue': 0.64}
        if scale == 'two_bad':
            max_color = {'red': 0.96, 'green': 0.62, 'blue': 0.60}
        gradient_rule = {
            'minpoint': {
                'type': 'NUMBER',
                'value': '0',
                'color': {'red': 1, 'green': 1, 'blue': 1},
            },
            'maxpoint': {
                'type': 'MAX',
                'color': max_color,
            },
        }
    else:
        low_color = {'red': 0.96, 'green': 0.62, 'blue': 0.60}
        high_color = {'red': 0.67, 'green': 0.86, 'blue': 0.64}
        if scale == 'three_good_low':
            low_color, high_color = high_color, low_color
        gradient_rule = {
            'minpoint': {'type': 'MIN', 'color': low_color},
            'midpoint': {
                'type': 'PERCENTILE',
                'value': '50',
                'color': {'red': 1, 'green': 1, 'blue': 1},
            },
            'maxpoint': {'type': 'MAX', 'color': high_color},
        }

    return {
        'addConditionalFormatRule': {
            'rule': {
                'ranges': _color_scale_ranges(
                    sheet_id,
                    column_index,
                    row_count,
                    row_ranges=row_ranges,
                ),
                'gradientRule': gradient_rule,
            },
            'index': 0,
        },
    }


def _color_scale_ranges(sheet_id, column_index, row_count, row_ranges=None):
    ranges = row_ranges or [{'startRowIndex': 1, 'endRowIndex': row_count}]
    return [
        {
            'sheetId': sheet_id,
            'startRowIndex': row_range['startRowIndex'],
            'endRowIndex': row_range['endRowIndex'],
            'startColumnIndex': column_index,
            'endColumnIndex': column_index + 1,
        }
        for row_range in ranges
        if row_range['startRowIndex'] < row_range['endRowIndex']
    ]


def _cell_format_request(sheet_id, row_index, column_index, cell_format):
    return {
        'repeatCell': {
            'range': {
                'sheetId': sheet_id,
                'startRowIndex': row_index,
                'endRowIndex': row_index + 1,
                'startColumnIndex': column_index,
                'endColumnIndex': column_index + 1,
            },
            'cell': {'userEnteredFormat': cell_format},
            'fields': 'userEnteredFormat.textFormat',
        },
    }


def _auto_resize_columns_request(sheet_id, start_index, end_index):
    return {
        'autoResizeDimensions': {
            'dimensions': {
                'sheetId': sheet_id,
                'dimension': 'COLUMNS',
                'startIndex': start_index,
                'endIndex': end_index,
            },
        },
    }


def _a1_col(index_1_based):
    letters = ''
    index = index_1_based
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _is_pitcher_display_slot(slot):
    slot_text = str(slot or '')
    if not slot_text:
        return False
    if slot_text.startswith(('SP', 'RP', 'P ')):
        return True
    return any(token in slot_text for token in ('- SP', '- RP', '- P'))


def _is_hitter_display_slot(slot):
    slot_text = str(slot or '')
    if not slot_text:
        return False
    if slot_text in {'Slot', 'Avg', 'W-L (Sv)', 'Avg|W-L-Sv'}:
        return False
    return not _is_pitcher_display_slot(slot_text)


def _is_active_display_slot(slot):
    slot_text = str(slot or '')
    return bool(slot_text) and not slot_text.startswith(('BE', 'IL', 'Other'))


def _batch_format(worksheet, formats):
    if not formats:
        return
    _sheets_call(
        f'format {worksheet.title}',
        lambda: worksheet.batch_format(formats),
    )


def _sheets_batch_update(spreadsheet, label, requests):
    if not requests:
        return
    _sheets_call(label, lambda: spreadsheet.batch_update({'requests': requests}))


def _sheets_call(label, func, attempts=3, delay_seconds=70):
    """Run a Sheets mutation, backing off when the API write quota resets."""
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except gspread.exceptions.APIError as exc:
            if attempt == attempts or not _is_quota_error(exc):
                raise
            print(
                f"[almanac] Sheets quota hit during {label}; "
                f"retrying in {delay_seconds}s"
            )
            time.sleep(delay_seconds)


def _is_quota_error(exc):
    message = str(exc).lower()
    return '[429]' in message or 'quota exceeded' in message or 'rate limit' in message
