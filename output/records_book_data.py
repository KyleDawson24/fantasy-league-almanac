"""Data for the redesigned Records surfaces (MLB-212): the platform adapters.

PLATFORM DECIDES WHERE THE NUMBERS COME FROM; FORMAT DECIDES THE PAGE. This
module turns each platform's facts into the unit rows the shared engine
(records_book_logic) understands, and nothing else -- no layout, no
record-keeping rules.

  ESPN  weekly facts for weeks, the daily fact for days, the season facts
        for seasons and lifetimes. Every unit is complete by construction:
        the closed-period gate (is_record_eligible / the loaded periods)
        decides what is in, and this module does not second-guess it.
  CBS   no weekly or season facts exist; everything is aggregated from the
        daily union fact, ACTIVE-WEIGHTED (the CBS book's standing lens:
        2004-2020 start shares are estimates). Weeks are Monday-Sunday
        calendar bins numbered from the first Monday on or before the
        season's first game -- verified against the 2026 period standings
        (period 23 = Aug 24-30 on both sides). Player-weeks are too many to
        hold, so their candidates come from a top-K UNPIVOT query.

Completeness (spec section 2.14): a period is complete when it is loaded
(today's gate); a season is complete when it is not the league's current
season. Standard length: periods carry is_record_eligible; seasons whose
best team total sits outside 60%-150% of the median are non-standard (the
2020 60-gamer and the 2001-02 coin-flip era on CBS).
"""

import os
from collections import defaultdict

import db
from db import league_predicate, query_for_presentation
import records
from almanac_render import _boxscore_url, disambiguated_abbrev_map
from records_book_logic import (
    AB_FLOOR, OUTS_FLOOR, CandidatePool, Context, Pool, POINTS_METRICS,
    RATE_METRICS, WASTED_METRICS, add_rates, add_wasted, aggregate,
    counting_metrics, derive_tabs, order_slots, stat_line, SUM_COLS,
)

COUNT_COLS = ['h', 'ab', 'b_bb', 'b_so', 'hbp', 'sf', 'hr', 'r', 'rbi', 'sb', 'cs',
              'tb', 'singles', 'doubles', 'triples', 'xbh', 'gdp', 'b_ibb', 'cyc',
              'w', 'l', 'k', 'er', 'outs', 'qs', 'sv', 'hld', 'p_h', 'p_bb', 'p_hr',
              'p_r', 'cg', 'blk', 'wp', 'hbp_p', 'blsv', 'nh', 'pg', 'pk', 'sho']

SENTINEL_TEAM = '9999'


# ---------------------------------------------------------------------------
# Shared lookups
# ---------------------------------------------------------------------------

def _catalog(platform):
    """The league's scored + auto-tracked counting stats, keyed by the
    fact column each lives in (spec section 3: settings-driven rows)."""
    if platform == 'cbs':
        join = (f"LEFT JOIN stg_cbs__scoring_settings s ON s.canonical_key = d.canonical_key "
                f"AND {league_predicate('s')}")
        scored = "s.canonical_key IS NOT NULL"
    else:
        join = (f"LEFT JOIN stg_scoring_settings s ON s.stat_name = d.stat_name "
                f"AND {league_predicate('s')}")
        scored = "s.stat_name IS NOT NULL"
    rows = query_for_presentation(f"""
        SELECT DISTINCT d.leaderboard_name, d.display_name, d.stat_category,
               d.polarity, d.derivation_expr, d.is_counting
        FROM dim_stat d
        {join}
        WHERE d.is_record_candidate
          AND d.stat_category IN ('hitting', 'pitching')
          AND ({scored} OR d.auto_tracked)
        ORDER BY d.stat_category, d.display_name
    """)
    out = []
    for r in rows:
        key = str(r['leaderboard_name']).lower()
        if key in COUNT_COLS or r.get('derivation_expr'):
            out.append({'key': key, 'display_name': r['display_name'],
                        'stat_category': r['stat_category'], 'polarity': r['polarity'],
                        'derivation_expr': r.get('derivation_expr')})
    return out


def _ppu(platform):
    if platform == 'cbs':
        rows = query_for_presentation(f"""
            SELECT d.leaderboard_name, s.points_per_unit
            FROM dim_stat d JOIN stg_cbs__scoring_settings s
              ON s.canonical_key = d.canonical_key AND {league_predicate('s')}""")
    else:
        rows = query_for_presentation(f"""
            SELECT d.leaderboard_name, s.points_per_unit
            FROM dim_stat d JOIN stg_scoring_settings s
              ON s.stat_name = d.stat_name AND {league_predicate('s')}""")
    return {str(r['leaderboard_name']).lower(): float(r['points_per_unit'])
            for r in rows if r.get('points_per_unit') is not None}


def _owner_maps():
    rows = query_for_presentation(f"""
        SELECT season_year, team_id, owner_display FROM dim_team_owner
        WHERE {league_predicate()}""")
    by_season, latest = {}, {}
    for r in sorted(rows, key=lambda r: int(r['season_year'])):
        key = (int(r['season_year']), str(int(r['team_id'])))
        label = (r.get('owner_display') or '').replace(', ', ' & ')
        by_season[key] = label
        latest[str(int(r['team_id']))] = label
    return by_season, latest


def _franchise_maps():
    rows = query_for_presentation(f"""
        SELECT franchise_id, canonical_franchise_id, canonical_name, canonical_abbrev
        FROM dim_franchise WHERE {league_predicate()}""")
    canon_of = {str(r['franchise_id']): str(r['canonical_franchise_id']) for r in rows}
    names = {str(r['canonical_franchise_id']): r['canonical_name'] for r in rows}
    abbrevs = {str(r['canonical_franchise_id']): r['canonical_abbrev'] for r in rows}
    # MLB-279: colliding abbreviations gain an id-style suffix (BENT 14 / BENT 17).
    labels = disambiguated_abbrev_map(abbrevs)
    season_rows = query_for_presentation(f"""
        SELECT season_year, franchise_id, canonical_franchise_id
        FROM dim_franchise_season WHERE {league_predicate()}""")
    canon_season = {(int(r['season_year']), str(r['franchise_id'])): str(r['canonical_franchise_id'])
                    for r in season_rows}

    def canon_fn(row):
        tid = row.get('team_id')
        if tid is None:
            return None
        return canon_season.get((row.get('season'), tid)) or canon_of.get(tid, tid)
    return canon_of, names, labels, canon_fn


def _slots(platform):
    if platform == 'cbs':
        caps = {'C': 1, '1B': 1, '2B': 1, '3B': 1, 'SS': 1, 'OF': 3, 'DH': 1, 'U': 1, 'P': 9}
        rows = query_for_presentation(
            "SELECT lineup_slot, display_slot, sort_order, slot_category FROM dim_lineup_slot "
            "WHERE platform = 'cbs'")
        meta = {r['lineup_slot']: r for r in rows}
        return order_slots([{'label': meta.get(s, {}).get('display_slot') or s, 'count': n,
                             'category': meta.get(s, {}).get('slot_category') or 'hitting',
                             'sort_order': meta.get(s, {}).get('sort_order'),
                             'source': s} for s, n in caps.items()])
    rows = query_for_presentation(f"""
        SELECT c.lineup_slot, c.starter_count, c.sort_order, l.display_slot, l.slot_category
        FROM dim_roster_slot_counts c
        LEFT JOIN dim_lineup_slot l ON l.platform = 'espn' AND l.lineup_slot = c.lineup_slot
        WHERE {league_predicate('c')}
          AND c.season_year = (SELECT MAX(season_year) FROM dim_roster_slot_counts
                               WHERE {league_predicate()})
          AND c.is_active_lineup_slot AND c.starter_count > 0
        ORDER BY c.sort_order""")
    return order_slots([{'label': r.get('display_slot') or r['lineup_slot'],
                         'count': int(r['starter_count']),
                         'category': r.get('slot_category') or 'hitting',
                         'sort_order': r.get('sort_order'), 'source': r['lineup_slot']}
                        for r in rows])


def _fence_teams(rows):
    """The holding-pen sentinel ('####', id 9999 -- or a platform id wearing
    that name for an unmanned season) never holds a TEAM record; its players
    still count in player records."""
    return [r for r in rows
            if r.get('team_id') != SENTINEL_TEAM and (r.get('abbrev') or '') != '####']


def _seasons_from(rows):
    return sorted({int(r['season']) for r in rows if r.get('season') is not None})


def _standard_seasons(team_season_rows):
    """Spec 2.14 rider: 'least' needs standard-length units. A season whose
    best team total sits outside 60%-150% of the median is not one."""
    best = defaultdict(float)
    for r in team_season_rows:
        best[int(r['season'])] = max(best[int(r['season'])], float(r.get('pts') or 0))
    vals = sorted(best.values())
    if not vals:
        return set()
    med = vals[len(vals) // 2]
    return {s for s, v in best.items() if 0.6 * med <= v <= 1.5 * med}


def _flag(rows, current_season, standard):
    for r in rows:
        s = r.get('season')
        r['complete'] = s is not None and int(s) < current_season
        r['standard'] = s is not None and int(s) in standard
    return rows


def _apply_derived(rows, catalog):
    exprs = [(c['key'], c['derivation_expr']) for c in catalog if c.get('derivation_expr')]
    if not exprs:
        return rows
    for r in rows:
        for key, expr in exprs:
            total, sign = 0.0, 1.0
            for tok in expr.replace('-', ' - ').replace('+', ' + ').split():
                if tok == '+':
                    sign = 1.0
                elif tok == '-':
                    sign = -1.0
                else:
                    total += sign * float(r.get(tok) or 0)
            r[key] = total
    return rows


def _split_negative(row):
    """Section 3 breakdown: negative-active points have no discipline at the
    source (a per-day net); prorate by each discipline's share of active
    magnitude, the Hall of Shame's convention."""
    neg = float(row.get('neg') or 0)
    h, p = abs(float(row.get('hit_pts') or 0)), abs(float(row.get('pit_pts') or 0))
    if neg <= 0:
        return 0.0, 0.0
    if h + p == 0:
        return neg, 0.0
    return neg * h / (h + p), neg * p / (h + p)


# ---------------------------------------------------------------------------
# ESPN adapter
# ---------------------------------------------------------------------------

def _espn_counts(alias=''):
    a = f'{alias}.' if alias else ''
    return ', '.join(f'{a}{c}' for c in COUNT_COLS)


def _espn_sum_counts(alias=''):
    a = f'{alias}.' if alias else ''
    return ', '.join(f'SUM({a}{c}) AS {c}' for c in COUNT_COLS)


def _load_espn(current_season, owners, slots):
    P = league_predicate()
    def q(sql):
        return query_for_presentation(sql)

    team_week = q(f"""
        SELECT season_year AS season, matchup_period AS unit, matchup_period AS mp,
               team_id, team_name, team_abbrev AS abbrev, owner_name AS owner_raw,
               {_espn_counts()},
               calculated_points AS pts, calculated_hitting_pts AS hit_pts,
               calculated_pitching_pts AS pit_pts, negative_points AS neg,
               is_playoff, playoff_round
        FROM fct_team_weekly_active_performance
        WHERE {P} AND is_record_eligible""")
    player_week = q(f"""
        SELECT season_year AS season, matchup_period AS unit, matchup_period AS mp,
               team_id, team_name, team_abbrev AS abbrev, owner_name AS owner_raw,
               player_id AS pid, player_name AS pname, display_name AS dname,
               {_espn_counts()},
               calculated_points AS pts, calculated_hitting_pts AS hit_pts,
               calculated_pitching_pts AS pit_pts, negative_points AS neg
        FROM fct_player_weekly_active_performance
        WHERE {P} AND is_record_eligible""")
    team_day = q(f"""
        SELECT season_year AS season, scoring_period AS unit, MAX(matchup_period) AS mp,
               game_date AS date, team_id, MAX(team_name) AS team_name,
               MAX(team_abbrev) AS abbrev, MAX(owner_name) AS owner_raw,
               {_espn_sum_counts()},
               SUM(total_stat_pts) AS pts, SUM(total_hitting_stat_pts) AS hit_pts,
               SUM(total_pitching_stat_pts) AS pit_pts, SUM(negative_points) AS neg,
               SUM(games_played) AS games
        FROM fct_player_daily_performance
        WHERE {P} AND performance_status = 'active' AND team_id IS NOT NULL
          AND game_date IS NOT NULL
        GROUP BY season_year, scoring_period, game_date, team_id
        HAVING SUM(games_played) > 0""")
    player_day = q(f"""
        SELECT season_year AS season, scoring_period AS unit, MAX(matchup_period) AS mp,
               game_date AS date, team_id, MAX(team_name) AS team_name,
               MAX(team_abbrev) AS abbrev, MAX(owner_name) AS owner_raw,
               player_id AS pid, MAX(player_name) AS pname, MAX(display_name) AS dname,
               MAX(lineup_slot) AS slot,
               {_espn_sum_counts()},
               SUM(total_stat_pts) AS pts, SUM(total_hitting_stat_pts) AS hit_pts,
               SUM(total_pitching_stat_pts) AS pit_pts, SUM(negative_points) AS neg,
               SUM(games_played) AS games
        FROM fct_player_daily_performance
        WHERE {P} AND performance_status = 'active' AND team_id IS NOT NULL
          AND game_date IS NOT NULL
        GROUP BY season_year, scoring_period, game_date, team_id, player_id
        HAVING SUM(games_played) > 0""")
    slot_week = q(f"""
        SELECT p.season_year AS season, p.matchup_period AS unit, p.matchup_period AS mp,
               p.team_id, p.team_name, p.team_abbrev AS abbrev, p.owner_display AS owner_raw,
               p.player_id AS pid, p.player_name AS pname, p.display_name AS dname,
               p.lineup_slot AS slot, ROUND(p.total_stat_pts, 1) AS pts, p.games_played AS games
        FROM fct_player_weekly_slot_performance p
        JOIN dim_matchup_period m ON m.league_key = p.league_key
             AND m.season_year = p.season_year AND m.matchup_period = p.matchup_period
        WHERE {league_predicate('p')} AND p.performance_status = 'active'
          AND p.team_id IS NOT NULL AND m.is_record_eligible""")
    inactive_week = q(f"""
        SELECT season_year AS season, matchup_period AS unit, matchup_period AS mp,
               team_id, team_name, team_abbrev AS abbrev, player_id AS pid,
               player_name AS pname, wasted_bucket,
               calculated_hitting_pts AS hit, calculated_pitching_pts AS pit
        FROM fct_player_weekly_inactive_performance
        WHERE {P} AND is_record_eligible""")
    inactive_day = q(f"""
        SELECT season_year AS season, scoring_period AS unit, MAX(matchup_period) AS mp,
               game_date AS date, team_id, MAX(team_name) AS team_name,
               MAX(team_abbrev) AS abbrev, player_id AS pid, MAX(player_name) AS pname,
               wasted_bucket, SUM(total_hitting_stat_pts) AS hit,
               SUM(total_pitching_stat_pts) AS pit
        FROM fct_player_daily_performance
        WHERE {P} AND performance_status = 'inactive' AND game_date IS NOT NULL
        GROUP BY season_year, scoring_period, game_date, team_id, player_id, wasted_bucket""")
    team_season = q(f"""
        SELECT season_year AS season, NULL AS unit, team_id, team_name,
               team_abbrev AS abbrev, owner_display AS owner_raw,
               {_espn_counts()},
               calculated_points AS pts, calculated_hitting_pts AS hit_pts,
               calculated_pitching_pts AS pit_pts, negative_points AS neg,
               periods_played
        FROM fct_team_season_performance WHERE {P}""")
    player_season = q(f"""
        SELECT season_year AS season, NULL AS unit, team_id, MAX(team_name) AS team_name,
               MAX(team_abbrev) AS abbrev, MAX(owner_name) AS owner_raw,
               player_id AS pid, MAX(player_name) AS pname, MAX(display_name) AS dname,
               {_espn_sum_counts()},
               SUM(calculated_points) AS pts, SUM(calculated_hitting_pts) AS hit_pts,
               SUM(calculated_pitching_pts) AS pit_pts, SUM(negative_points) AS neg,
               SUM(games_played) AS games
        FROM fct_player_season_performance
        WHERE {P} AND performance_status = 'active' AND team_id IS NOT NULL
        GROUP BY season_year, team_id, player_id""")
    player_season_inactive = q(f"""
        SELECT season_year AS season, team_id, player_id AS pid, MAX(player_name) AS pname,
               wasted_bucket, SUM(calculated_hitting_pts) AS hit,
               SUM(calculated_pitching_pts) AS pit
        FROM fct_player_season_performance
        WHERE {P} AND performance_status = 'inactive'
        GROUP BY season_year, team_id, player_id, wasted_bucket""")
    slot_season = q(f"""
        SELECT season_year AS season, NULL AS unit, team_id, team_name,
               team_abbrev AS abbrev, owner_name AS owner_raw, player_id AS pid,
               player_name AS pname, display_name AS dname, lineup_slot AS slot,
               calculated_points AS pts, games_played AS games
        FROM fct_player_season_performance
        WHERE {P} AND performance_status = 'active' AND team_id IS NOT NULL""")
    schedule = records.load_schedule_lookup()

    def period_label(row, band):
        if band.grain == 'day':
            d = row.get('date')
            text = f"{d.strftime('%b')} {d.day}" if hasattr(d, 'strftime') else str(d)
            if band.scope == 'all':
                text += f", {row.get('season')}"
            return text
        text = records.format_week_label(row.get('season'), row.get('unit'), schedule)
        if band.scope == 'all':
            text += f", {row.get('season')}"
        return text

    league_id = os.getenv('LEAGUE_ID')

    def period_link(row, band):
        if band.grain == 'season' or not league_id:
            return None
        return _boxscore_url(league_id, row.get('season'), row.get('mp'), row.get('team_id'))

    return dict(team_week=team_week, player_week=player_week, team_day=team_day,
                player_day=player_day, slot_week=slot_week, slot_day=None,
                inactive_week=inactive_week, inactive_day=inactive_day,
                team_season=team_season, player_season=player_season,
                player_season_inactive=player_season_inactive, slot_season=slot_season,
                period_label=period_label, period_link=period_link,
                player_week_pool=None, latest_unit=None)


# ---------------------------------------------------------------------------
# CBS adapter
# ---------------------------------------------------------------------------

def _cbs_wcounts():
    return ', '.join(f'SUM({c} * aw) AS {c}' for c in COUNT_COLS)


_CBS_BASE = """
    SELECT f.*, COALESCE(f.active_weight, 0) AS aw,
           DATEDIFF('day', DATE_TRUNC('week', MIN(f.game_date) OVER (PARTITION BY f.season_year)),
                    DATE_TRUNC('week', f.game_date)) / 7 + 1 AS wk,
           CASE WHEN f.lineup_slot IN ('C','1B','2B','3B','SS','OF','DH','U','P') THEN f.lineup_slot
                WHEN f.position = 'P' THEN 'P' END AS slot_label
    FROM fct_player_daily_performance f
    WHERE {P} AND f.game_date IS NOT NULL AND f.team_id IS NOT NULL
"""


def _load_cbs(current_season, owners, slots):
    P = league_predicate('f')
    base = _CBS_BASE.format(P=P)

    def q(sql):
        return query_for_presentation(sql)

    player_season = q(f"""
        WITH d AS ({base})
        SELECT season_year AS season, NULL AS unit, team_id, MAX(team_name) AS team_name,
               MAX(team_abbrev) AS abbrev, player_key AS pid, MAX(player_name) AS pname,
               MAX(display_name) AS dname,
               {_cbs_wcounts()},
               SUM(total_stat_pts * aw) AS pts, SUM(total_hitting_stat_pts * aw) AS hit_pts,
               SUM(total_pitching_stat_pts * aw) AS pit_pts, SUM(negative_points * aw) AS neg,
               SUM(total_hitting_stat_pts * (1 - aw)) AS benched_hit,
               SUM(total_pitching_stat_pts * (1 - aw)) AS benched_pit,
               SUM(games_played * aw) AS games
        FROM d GROUP BY season_year, team_id, player_key
        ORDER BY season_year, team_id, player_key""")
    season_totals = q(f"""
        SELECT season_year AS season, cbs_player_id AS pid, MAX(cbs_player_name) AS pname,
               SUM(CASE WHEN stat_name = 'CALCULATED_HITTING_PTS' THEN stat_value ELSE 0 END) AS hit,
               SUM(CASE WHEN stat_name = 'CALCULATED_PITCHING_PTS' THEN stat_value ELSE 0 END) AS pit
        FROM int_cbs__player_season_stats
        WHERE {league_predicate()} AND stat_name IN ('CALCULATED_HITTING_PTS', 'CALCULATED_PITCHING_PTS')
        GROUP BY season_year, cbs_player_id""")
    team_week = q(f"""
        WITH d AS ({base})
        SELECT season_year AS season, wk AS unit, wk AS mp, team_id, MAX(team_name) AS team_name,
               MAX(team_abbrev) AS abbrev, MIN(game_date) AS date,
               {_cbs_wcounts()},
               SUM(total_stat_pts * aw) AS pts, SUM(total_hitting_stat_pts * aw) AS hit_pts,
               SUM(total_pitching_stat_pts * aw) AS pit_pts, SUM(negative_points * aw) AS neg,
               SUM(total_hitting_stat_pts * (1 - aw)) AS benched_hit,
               SUM(total_pitching_stat_pts * (1 - aw)) AS benched_pit,
               SUM(games_played * aw) AS games
        FROM d GROUP BY season_year, wk, team_id
        HAVING SUM(games_played * aw) > 0
        ORDER BY season_year, wk, team_id""")
    slot_season = q(f"""
        WITH d AS ({base})
        SELECT season_year AS season, NULL AS unit, team_id, MAX(team_name) AS team_name,
               MAX(team_abbrev) AS abbrev, player_key AS pid, MAX(player_name) AS pname,
               MAX(display_name) AS dname, slot_label AS slot,
               SUM(total_stat_pts * aw) AS pts, SUM(games_played * aw) AS games
        FROM d WHERE slot_label IS NOT NULL
        GROUP BY season_year, team_id, player_key, slot_label
        ORDER BY season_year, team_id, player_key, slot_label""")
    # Week-grain slot rows: top-K per slot and scope, so the 100k-row grain
    # never lands in memory whole.
    slot_week = q(f"""
        WITH d AS ({base}),
        pw AS (
            SELECT season_year AS season, wk AS unit, wk AS mp, team_id, MAX(team_name) AS team_name,
                   MAX(team_abbrev) AS abbrev, player_key AS pid, MAX(player_name) AS pname,
                   MAX(display_name) AS dname, slot_label AS slot, MIN(game_date) AS date,
                   SUM(total_stat_pts * aw) AS pts, SUM(games_played * aw) AS games
            FROM d WHERE slot_label IS NOT NULL
            GROUP BY season_year, wk, team_id, player_key, slot_label
        )
        SELECT * FROM pw
        QUALIFY ROW_NUMBER() OVER (PARTITION BY slot ORDER BY pts DESC, season DESC, unit DESC, team_id, pid) <= 40
             OR ROW_NUMBER() OVER (PARTITION BY slot, (season = {current_season}) ORDER BY pts DESC, season DESC, unit DESC, team_id, pid) <= 40""")
    team_slot_week = q(f"""
        WITH d AS ({base}),
        tw AS (
            SELECT season_year AS season, wk AS unit, wk AS mp, team_id, MAX(team_name) AS team_name,
                   MAX(team_abbrev) AS abbrev, slot_label AS slot, MIN(game_date) AS date,
                   SUM(total_stat_pts * aw) AS pts
            FROM d WHERE slot_label IS NOT NULL
            GROUP BY season_year, wk, team_id, slot_label
        )
        SELECT * FROM tw
        QUALIFY ROW_NUMBER() OVER (PARTITION BY slot ORDER BY pts DESC, season DESC, unit DESC, team_id) <= 40
             OR ROW_NUMBER() OVER (PARTITION BY slot, (season = {current_season}) ORDER BY pts DESC, season DESC, unit DESC, team_id) <= 40""")

    # Player-weeks: candidates per metric from one UNPIVOT top-K query.
    metric_cols = COUNT_COLS + ['pts', 'hit_pts', 'pit_pts', 'wasted', 'wasted_hit', 'wasted_pit']
    unpivot = ', '.join(f'{c}_v' for c in metric_cols)
    passthrough = ', '.join(f'{c} AS {c}' for c in COUNT_COLS + ['pts', 'hit_pts', 'pit_pts'])
    pw_rows = q(f"""
        WITH d AS ({base}),
        pw AS (
            SELECT season_year AS season, wk AS unit, wk AS mp, team_id, MAX(team_name) AS team_name,
                   MAX(team_abbrev) AS abbrev, player_key AS pid, MAX(player_name) AS pname,
                   MAX(display_name) AS dname, MIN(game_date) AS date,
                   {_cbs_wcounts()},
                   SUM(total_stat_pts * aw) AS pts, SUM(total_hitting_stat_pts * aw) AS hit_pts,
                   SUM(total_pitching_stat_pts * aw) AS pit_pts, SUM(negative_points * aw) AS neg,
                   SUM(total_hitting_stat_pts * (1 - aw)) AS benched_hit,
                   SUM(total_pitching_stat_pts * (1 - aw)) AS benched_pit,
                   SUM(games_played * aw) AS games
            FROM d GROUP BY season_year, wk, team_id, player_key
            HAVING SUM(games_played) > 0
        ),
        pw2 AS (
            SELECT season, unit, mp, team_id, team_name, abbrev, pid, pname, dname, date, games,
                   {passthrough}, benched_hit, benched_pit, neg,
                   {', '.join(f'CAST({c} AS DOUBLE) AS {c}_v' for c in COUNT_COLS)},
                   CAST(pts AS DOUBLE) AS pts_v, CAST(hit_pts AS DOUBLE) AS hit_pts_v,
                   CAST(pit_pts AS DOUBLE) AS pit_pts_v,
                   CAST(benched_hit + benched_pit + neg AS DOUBLE) AS wasted_v,
                   CAST(benched_hit + CASE WHEN ABS(hit_pts) + ABS(pit_pts) = 0 THEN neg
                        ELSE neg * ABS(hit_pts) / (ABS(hit_pts) + ABS(pit_pts)) END AS DOUBLE) AS wasted_hit_v,
                   CAST(benched_pit + CASE WHEN ABS(hit_pts) + ABS(pit_pts) = 0 THEN 0
                        ELSE neg * ABS(pit_pts) / (ABS(hit_pts) + ABS(pit_pts)) END AS DOUBLE) AS wasted_pit_v
            FROM pw
        ),
        u AS (
            SELECT * FROM pw2 UNPIVOT (value FOR metric IN ({unpivot}))
            WHERE NOT (metric = 'HIT_PTS_V' AND ab <= 0)
              AND NOT (metric = 'PIT_PTS_V' AND outs <= 0)
        ),
        ranked AS (
            SELECT u.*, LOWER(REGEXP_REPLACE(metric, '_V$', '')) AS m, (season = {current_season}) AS cur,
                   ROW_NUMBER() OVER (PARTITION BY metric ORDER BY value DESC, season DESC, unit DESC, team_id, pid) AS rd_all,
                   ROW_NUMBER() OVER (PARTITION BY metric ORDER BY value ASC, season DESC, unit DESC, team_id, pid) AS ra_all,
                   ROW_NUMBER() OVER (PARTITION BY metric, (season = {current_season}) ORDER BY value DESC, season DESC, unit DESC, team_id, pid) AS rd_cur,
                   ROW_NUMBER() OVER (PARTITION BY metric, (season = {current_season}) ORDER BY value ASC, season DESC, unit DESC, team_id, pid) AS ra_cur,
                   COUNT(*) OVER (PARTITION BY metric, value) AS tie_all,
                   COUNT(*) OVER (PARTITION BY metric, value, (season = {current_season})) AS tie_cur
            FROM u
        )
        SELECT * FROM ranked
        WHERE rd_all <= 12 OR ra_all <= 12 OR rd_cur <= 12 OR ra_cur <= 12""")
    # (the discipline gate for worst hitting / pitching weeks rides `require`
    # on the candidate rows, which carry ab and outs)
    cands = defaultdict(list)
    for r in pw_rows:
        m = r['m']
        row = {k: r.get(k) for k in ('season', 'unit', 'mp', 'team_id', 'team_name', 'abbrev',
                                      'pid', 'pname', 'dname', 'date', 'games', 'benched_hit',
                                      'benched_pit', 'neg') + tuple(COUNT_COLS)
               + ('pts', 'hit_pts', 'pit_pts')}
        row['owner_raw'] = ''
        _finish([row], owners, {}, [])
        nh, np_ = _split_negative(row)
        row.update(unrostered_hit=0.0, unrostered_pit=0.0, neg_hit=nh, neg_pit=np_)
        add_wasted(row)
        row['value'] = float(r['value'])
        row[m] = row['value']
        if r['rd_all'] <= 12:
            cands[(m, 'desc', 'all')].append((row['value'], row, int(r['tie_all']), r['rd_all']))
        if r['ra_all'] <= 12:
            cands[(m, 'asc', 'all')].append((row['value'], row, int(r['tie_all']), r['ra_all']))
        if r['cur'] and r['rd_cur'] <= 12:
            cands[(m, 'desc', current_season)].append((row['value'], row, int(r['tie_cur']), r['rd_cur']))
        if r['cur'] and r['ra_cur'] <= 12:
            cands[(m, 'asc', current_season)].append((row['value'], row, int(r['tie_cur']), r['ra_cur']))
    for key, lst in cands.items():
        lst.sort(key=lambda t: t[3])
        cands[key] = [(v, row, n) for v, row, n, _ in lst]

    def period_label(row, band):
        text = f"Week {row.get('unit')}"
        if band.scope == 'all':
            text += f", {row.get('season')}"
        return text

    latest = max(((int(r['season']), int(r['unit'])) for r in team_week
                  if int(r['season']) == current_season), default=None)

    return dict(team_week=team_week, player_week=None, team_day=None, player_day=None,
                slot_week=slot_week, team_slot_week=team_slot_week,
                inactive_week=None, inactive_day=None,
                team_season=None, player_season=player_season,
                season_totals=season_totals, slot_season=slot_season,
                period_label=period_label, period_link=lambda row, band: None,
                player_week_pool=CandidatePool(dict(cands)), latest_unit=latest,
                base_sql=base)


# ---------------------------------------------------------------------------
# Assembly: unit rows -> pools + the lifetime data.
# ---------------------------------------------------------------------------

_CANON = {'fn': lambda row: row.get('team_id'), 'latest_owner': {}}


def _finish(rows, owners, latest_owner, catalog):
    canon_fn = _CANON['fn']
    latest_by_canon = _CANON['latest_owner']
    for r in rows:
        r['team_id'] = None if r.get('team_id') is None else str(int(float(r['team_id'])))
        r['season'] = int(r['season']) if r.get('season') is not None else None
        if r.get('unit') is not None:
            r['unit'] = int(r['unit'])
        r['cid'] = canon_fn(r)
        for c in COUNT_COLS + ['pts', 'hit_pts', 'pit_pts', 'neg', 'games', 'benched_hit',
                               'benched_pit']:
            if c in r and r[c] is not None:
                r[c] = float(r[c])
        r['owner'] = (owners.get((r['season'], r['team_id']))
                      or latest_by_canon.get(r.get('cid'))
                      or r.get('owner_raw') or '')
        if r['owner'] in ('Unknown', 'Unknown owner', 'Owner unavailable'):
            r['owner'] = ''
        add_rates(r)
    _apply_derived(rows, catalog)
    return rows


def _team_wasted_rows(team_rows, inactive_rows, key_fn):
    benched = defaultdict(lambda: [0.0, 0.0])
    for r in inactive_rows or ():
        if r.get('wasted_bucket') != 'ROSTERED_INACTIVE' or r.get('team_id') is None:
            continue
        b = benched[key_fn(r)]
        b[0] += float(r.get('hit') or 0)
        b[1] += float(r.get('pit') or 0)
    out = []
    for t in team_rows:
        bh, bp = benched.get(key_fn(t), (t.get('benched_hit') or 0.0, t.get('benched_pit') or 0.0))
        nh, np_ = _split_negative(t)
        row = dict(t)
        row.update(benched_hit=bh, benched_pit=bp, unrostered_hit=0.0, unrostered_pit=0.0,
                   neg_hit=nh, neg_pit=np_)
        out.append(add_wasted(row))
    return out


def _player_wasted_rows(player_rows, inactive_rows, key_fn, unit_cols):
    """Player wasted units at (season, unit, player): benched by the team that
    sat him most, unrostered from the FA rows, negative from the active row.
    A week with no team at all shows '(unrostered)' as its owner."""
    parts = {}
    for r in inactive_rows or ():
        k = key_fn(r)
        p = parts.setdefault(k, {'benched': defaultdict(lambda: [0.0, 0.0]),
                                 'unrostered': [0.0, 0.0], 'ident': r, 'by_team': {}})
        if r.get('wasted_bucket') == 'FA' or r.get('team_id') is None:
            p['unrostered'][0] += float(r.get('hit') or 0)
            p['unrostered'][1] += float(r.get('pit') or 0)
        else:
            b = p['benched'][r['team_id']]
            b[0] += float(r.get('hit') or 0)
            b[1] += float(r.get('pit') or 0)
            p['ident'] = r
            p['by_team'][r['team_id']] = r
    active = {}
    for r in player_rows:
        k = key_fn(r)
        a = active.setdefault(k, {'neg_hit': 0.0, 'neg_pit': 0.0, 'row': r})
        nh, np_ = _split_negative(r)
        a['neg_hit'] += nh
        a['neg_pit'] += np_
        if float(r.get('pts') or 0) > float(a['row'].get('pts') or 0):
            a['row'] = r
    out = []
    for k in set(parts) | set(active):
        p = parts.get(k)
        a = active.get(k)
        row = {}
        team_row = None
        bh = bp = 0.0
        if p:
            if p['benched']:
                team, (bh, bp) = max(p['benched'].items(), key=lambda kv: (kv[1][0] + kv[1][1], str(kv[0])))
                team_row = p['by_team'].get(team)
            uh, up = p['unrostered']
        else:
            uh = up = 0.0
        src = team_row or (a['row'] if a else p['ident'])
        for c in unit_cols + ['team_id', 'team_name', 'abbrev', 'owner', 'pid', 'pname', 'dname',
                              'date', 'mp']:
            row[c] = src.get(c)
        if a and not row.get('dname'):
            row['dname'] = a['row'].get('dname')
        if team_row is None and a is None:
            row['owner'] = '(unrostered)'
            row['abbrev'] = ''
            row['team_id'] = None
        row.update(benched_hit=bh, benched_pit=bp, unrostered_hit=uh, unrostered_pit=up,
                   neg_hit=a['neg_hit'] if a else 0.0, neg_pit=a['neg_pit'] if a else 0.0,
                   complete=src.get('complete', True), standard=src.get('standard', True))
        out.append(add_wasted(row))
    return out


def _team_slot_rows(slot_rows, key_fn):
    acc = {}
    for r in slot_rows:
        k = key_fn(r) + (r.get('slot'),)
        a = acc.get(k)
        if a is None:
            a = {c: r.get(c) for c in ('season', 'unit', 'mp', 'date', 'team_id', 'team_name',
                                       'abbrev', 'owner', 'slot', 'complete', 'standard')}
            a['pts'] = 0.0
            a['players'] = []
            acc[k] = a
        a['pts'] += float(r.get('pts') or 0)
        a['players'].append((r.get('dname') or r.get('pname'), float(r.get('pts') or 0)))
    for a in acc.values():
        a['players'].sort(key=lambda t: (-t[1], str(t[0])))
    return list(acc.values())


def _relabel_slots(rows, slots):
    """Slot rows carry the platform's slot spelling; records lump by the
    reader-facing label (MI / CI on ESPN)."""
    label_of = {s['source']: s['label'] for s in slots}
    for r in rows:
        r['slot'] = label_of.get(r.get('slot'), r.get('slot'))
    return rows


def load_book(league_key=None):
    """Everything the presenter needs for one league, in one call."""
    if league_key:
        db.set_league(league_key)
    platform = db.league().platform
    import league_format
    fmt = league_format.resolve()
    catalog = _catalog(platform)
    ppu = _ppu(platform)
    owners, latest_owner = _owner_maps()
    canon_of, canon_names, canon_labels, canon_fn = _franchise_maps()
    latest_by_canon = {}
    for (season, tid), label in sorted(owners.items()):
        cid = canon_fn({'season': season, 'team_id': tid})
        if label:
            latest_by_canon[cid] = label
    _CANON['fn'] = canon_fn
    _CANON['latest_owner'] = latest_by_canon
    slots = _slots(platform)

    if platform == 'cbs':
        current_season = int(query_for_presentation(
            f"SELECT MAX(season_year) AS s FROM fct_player_daily_performance WHERE {league_predicate()}")[0]['s'])
    else:
        current_season = int(query_for_presentation(
            f"SELECT MAX(season_year) AS s FROM fct_team_weekly_active_performance WHERE {league_predicate()}")[0]['s'])

    raw = (_load_cbs if platform == 'cbs' else _load_espn)(current_season, owners, slots)

    # ---- season grain (both platforms) ----
    player_season = _finish(raw['player_season'], owners, latest_owner, catalog)
    if platform == 'cbs':
        team_season = list(aggregate(_fence_teams(player_season),
                                     lambda r: (r['season'], r['team_id']), SUM_COLS).values())
        _apply_derived(team_season, catalog)
        for t in team_season:
            k = (t['season'], t['team_id'])
            t['cid'] = canon_fn(t)
            t['owner'] = owners.get(k) or latest_by_canon.get(t['cid']) or ''
            t['seasons'] = None
        # Unrostered: season total minus everything attributed while rostered.
        attributed = defaultdict(lambda: [0.0, 0.0])
        for r in player_season:
            a = attributed[(r['season'], r['pid'])]
            a[0] += float(r.get('hit_pts') or 0) + float(r.get('benched_hit') or 0)
            a[1] += float(r.get('pit_pts') or 0) + float(r.get('benched_pit') or 0)
        unrostered = {}
        for r in raw['season_totals']:
            k = (int(r['season']), r['pid'])
            a = attributed.get(k, [0.0, 0.0])
            unrostered[k] = (max(0.0, float(r['hit'] or 0) - a[0]), max(0.0, float(r['pit'] or 0) - a[1]))
        inactive_season = []
        for r in player_season:
            inactive_season.append({**r, 'wasted_bucket': 'ROSTERED_INACTIVE',
                                    'hit': r.get('benched_hit') or 0.0, 'pit': r.get('benched_pit') or 0.0})
        names = {r['pid']: (r.get('pname'), r.get('dname')) for r in player_season}
        total_names = {(int(r['season']), r['pid']): r.get('pname') for r in raw['season_totals']}
        for (season, pid), (uh, up) in unrostered.items():
            if (uh + up) > 0:
                pname, dname = names.get(pid, (total_names.get((season, pid)), None))
                inactive_season.append({'season': season, 'unit': None, 'team_id': None, 'pid': pid,
                                        'pname': pname or total_names.get((season, pid)),
                                        'dname': dname or pname or total_names.get((season, pid)),
                                        'wasted_bucket': 'FA', 'hit': uh, 'pit': up})
    else:
        team_season = _fence_teams(_finish(raw['team_season'], owners, latest_owner, catalog))
        inactive_season = _finish([dict(r, unit=None) for r in raw['player_season_inactive']],
                                  owners, latest_owner, catalog)
        for r in inactive_season:
            r['dname'] = r.get('dname') or r.get('pname')
    standard = _standard_seasons(team_season)
    if platform == 'cbs':
        # The no-anchor era (2001-02) is under-attributed: a team total there
        # is not its fewest-points worth (the CBS book's own worst-season gate).
        anchored = {int(r['season_year']) for r in query_for_presentation(
            f"SELECT DISTINCT season_year FROM stg_cbs__ui_rosters WHERE {league_predicate()}")}
        standard &= anchored
    _flag(team_season, current_season, standard)
    _flag(player_season, current_season, standard)
    _flag(inactive_season, current_season, standard)
    seasons = _seasons_from(team_season)
    team_count = len({r['team_id'] for r in team_season if r['season'] == current_season})

    season_key = lambda r: (r['season'], r['team_id'])
    player_season_key = lambda r: (r['season'], r['pid'])
    team_season_wasted = _team_wasted_rows(team_season, inactive_season, season_key)
    player_season_wasted = _player_wasted_rows(player_season, inactive_season, player_season_key,
                                               ['season', 'unit'])
    slot_season = _relabel_slots(_finish(raw['slot_season'], owners, latest_owner, catalog), slots)
    _flag(slot_season, current_season, standard)
    team_slot_season = _fence_teams(_team_slot_rows(slot_season, season_key))

    pools = {}
    for key in ('season_cur', 'season_all'):
        pools[('team', key)] = Pool(team_season)
        pools[('player', key)] = Pool(player_season)
        pools[('team_wasted', key)] = Pool(team_season_wasted)
        pools[('player_wasted', key)] = Pool(player_season_wasted)
        pools[('team_slot', key)] = Pool(team_slot_season)
        pools[('player_slot', key)] = Pool(slot_season)

    # ---- period grains ----
    latest_unit = raw.get('latest_unit')
    contributors_index = {'season': defaultdict(list)}
    for r in player_season:
        contributors_index['season'][season_key(r)].append(r)
    player_lookup = {}
    for r in player_season:
        player_lookup[('season', r['season'], None, r['team_id'], r['pid'])] = r

    if platform == 'espn':
        week_key = lambda r: (r['season'], r['unit'], r['team_id'])
        day_key = lambda r: (r['season'], r['date'], r['team_id'])
        team_week = _fence_teams(_finish(raw['team_week'], owners, latest_owner, catalog))
        player_week = _finish(raw['player_week'], owners, latest_owner, catalog)
        team_day = _fence_teams(_finish(raw['team_day'], owners, latest_owner, catalog))
        player_day = _finish(raw['player_day'], owners, latest_owner, catalog)
        inactive_week = _finish(raw['inactive_week'], owners, latest_owner, catalog)
        inactive_day = _finish(raw['inactive_day'], owners, latest_owner, catalog)
        for rows in (inactive_week, inactive_day):
            for r in rows:
                r['dname'] = r.get('pname')
        slot_week = _relabel_slots(_finish(raw['slot_week'], owners, latest_owner, catalog), slots)
        slot_day = _relabel_slots(
            [dict(r) for r in player_day], slots)
        latest_unit = max((r['season'], r['unit']) for r in team_week)
        contributors_index['week'] = defaultdict(list)
        for r in player_week:
            contributors_index['week'][week_key(r)].append(r)
        contributors_index['day'] = defaultdict(list)
        for r in player_day:
            contributors_index['day'][day_key(r)].append(r)
        for r in player_week:
            player_lookup[('week', r['season'], r['unit'], r['team_id'], r['pid'])] = r
        for r in player_day:
            player_lookup[('day', r['season'], r['date'], r['team_id'], r['pid'])] = r
        team_week_wasted = _team_wasted_rows(team_week, inactive_week, week_key)
        team_day_wasted = _team_wasted_rows(team_day, inactive_day, day_key)
        player_week_wasted = _player_wasted_rows(player_week, inactive_week,
                                                 lambda r: (r['season'], r['unit'], r['pid']),
                                                 ['season', 'unit'])
        player_day_wasted = _player_wasted_rows(player_day, inactive_day,
                                                lambda r: (r['season'], r['date'], r['pid']),
                                                ['season', 'unit'])
        team_slot_week = _fence_teams(_team_slot_rows(slot_week, week_key))
        team_slot_day = _fence_teams(_team_slot_rows(slot_day, day_key))
        for key in ('level_cur', 'level_all'):
            pools[('team', key)] = Pool(team_week)
            pools[('player', key)] = Pool(player_week)
            pools[('team_wasted', key)] = Pool(team_week_wasted)
            pools[('player_wasted', key)] = Pool(player_week_wasted)
            pools[('team_slot', key)] = Pool(team_slot_week)
            pools[('player_slot', key)] = Pool(slot_week)
        pools[('team', 'day_all')] = Pool(team_day)
        pools[('player', 'day_all')] = Pool(player_day)
        pools[('team_wasted', 'day_all')] = Pool(team_day_wasted)
        pools[('player_wasted', 'day_all')] = Pool(player_day_wasted)
        pools[('team_slot', 'day_all')] = Pool(team_slot_day)
        pools[('player_slot', 'day_all')] = Pool(slot_day)
        counts = {'team_week': len(team_week), 'player_week': len(player_week),
                  'team_day': len(team_day), 'player_day': len(player_day)}
    else:
        week_key = lambda r: (r['season'], r['unit'], r['team_id'])
        team_week = _fence_teams(_finish(raw['team_week'], owners, latest_owner, catalog))
        _flag(team_week, current_season, standard)
        # A standard-length week is one a team played in full: partial
        # opening / closing / All-Star weeks fall under half the season's
        # median team-games and never hold a fewest or worst record.
        med = {}
        by_season = defaultdict(list)
        for r in team_week:
            by_season[r['season']].append(float(r.get('games') or 0))
        for s, gs in by_season.items():
            gs.sort()
            med[s] = gs[len(gs) // 2]
        for r in team_week:
            r['complete'] = True          # every loaded week is closed
            r['standard'] = (int(r['season']) in standard
                             and float(r.get('games') or 0) >= 0.5 * med.get(r['season'], 0))
        team_week_wasted = _team_wasted_rows(team_week, None, week_key)
        slot_week = _relabel_slots(_finish(raw['slot_week'], owners, latest_owner, catalog), slots)
        team_slot_week = _fence_teams(_relabel_slots(
            _finish(raw['team_slot_week'], owners, latest_owner, catalog), slots))
        for r in team_slot_week:
            r['players'] = None           # fetched lazily for the winners
        pools[('team', 'week_all')] = Pool(team_week)
        pools[('player', 'week_all')] = raw['player_week_pool']
        pools[('team_wasted', 'week_all')] = Pool(team_week_wasted)
        pools[('player_wasted', 'week_all')] = _cbs_wasted_pool(raw['player_week_pool'])
        pools[('team_slot', 'week_all')] = Pool(team_slot_week)
        pools[('player_slot', 'week_all')] = Pool(slot_week)
        contributors_index['week'] = _CbsWeekIndex(raw['base_sql'], owners, latest_owner, catalog)
        player_lookup = _CbsPlayerLookup(player_lookup, contributors_index['week'])
        counts = {'team_week': len(team_week), 'player_week_candidates': sum(
            len(v) for v in raw['player_week_pool'].cands.values())}

    # ---- lifetime ----
    active_teams = {r['team_id'] for r in team_season if r['season'] == current_season}
    for r in player_season:
        r['team_labels'] = canon_labels
    lifetime = _lifetime(player_season, inactive_season, team_season, slot_season, canon_of,
                         canon_names, canon_labels, latest_owner, active_teams, current_season,
                         seasons, catalog)
    if platform == 'cbs':
        lifetime['legend_extra'] = ('2004-2020 lineups are start-share estimates; hitter lineup '
                                    'slots are logged from the 2026 daily capture only, pitchers '
                                    'count at P in every season.')

    tabs = derive_tabs('points' if fmt == 'points' else 'h2h', seasons_count=len(seasons),
                       level_label='Weeks')

    def contributors(row, metric):
        if metric.kind == 'rate':
            return []
        grain = 'season' if row.get('unit') is None and row.get('date') is None else \
            ('day' if row.get('date') is not None and platform == 'espn' else 'week')
        key = season_key(row) if grain == 'season' else (
            day_key(row) if grain == 'day' else week_key(row))
        players = contributors_index[grain][key] if grain != 'week' or platform == 'espn' \
            else contributors_index['week'].get(key)
        ranked = sorted(((r.get('dname') or r.get('pname'), float(r.get(metric.key) or 0))
                         for r in players if r.get(metric.key) is not None),
                        key=lambda t: (-t[1], str(t[0])))
        return [(n, v) for n, v in ranked if v > 0][:3]

    def slot_details(row):
        if row.get('players') is not None:
            return ', '.join(f'{n}: {v:,.1f}' for n, v in row['players'][:3] if v > 0)
        if 'pid' in row and row.get('pid') is not None:
            grain = 'season' if row.get('unit') is None and row.get('date') is None else \
                ('day' if row.get('date') is not None and platform == 'espn' else 'week')
            unit = row.get('date') if grain == 'day' else row.get('unit')
            src = player_lookup.get((grain, row['season'], unit, row['team_id'], row['pid']))
            if src is None:
                return f"{float(row.get('games') or 0):,.0f} games at slot"
            cat = next((s['category'] for s in slots if s['label'] == row.get('slot')), None)
            return stat_line(src, ppu, category=cat if cat in ('hitting', 'pitching') else None)
        # CBS team slot-week: players at that slot in that week.
        players = contributors_index['week'].slot_players(row) if platform == 'cbs' else []
        return ', '.join(f'{n}: {v:,.1f}' for n, v in players[:3])

    ctx = Context(current_season, latest_unit, team_count, ppu, raw['period_label'],
                  raw['period_link'], contributors, slot_details)
    return {'ctx': ctx, 'tabs': tabs, 'pools': pools, 'lifetime': lifetime,
            'catalog': catalog, 'slots': slots, 'platform': platform, 'format': fmt,
            'seasons': seasons, 'counts': counts, 'current_season': current_season,
            'latest_unit': latest_unit}


def _cbs_wasted_pool(pool):
    """The wasted metrics came through the same UNPIVOT; expose them under
    the engine's wasted keys with the three-term columns for Details."""
    cands = {}
    for (m, d, scope), lst in pool.cands.items():
        if m in ('wasted', 'wasted_hit', 'wasted_pit'):
            cands[(m, d, scope)] = lst
    return CandidatePool(cands)


class _CbsWeekIndex:
    """Player-week rows for one team-week, fetched on demand (the grain is
    too big to hold, and only the record-holding team-weeks are needed)."""

    def __init__(self, base_sql, owners, latest_owner, catalog):
        self.base_sql = base_sql
        self.owners, self.latest_owner, self.catalog = owners, latest_owner, catalog
        self.cache = {}
        self.slot_cache = {}

    def get(self, key):
        season, unit, team_id = key
        if key not in self.cache:
            rows = query_for_presentation(f"""
                WITH d AS ({self.base_sql})
                SELECT season_year AS season, wk AS unit, wk AS mp, team_id, MAX(team_name) AS team_name,
                       MAX(team_abbrev) AS abbrev, player_key AS pid, MAX(player_name) AS pname,
                       MAX(display_name) AS dname, MIN(game_date) AS date,
                       {_cbs_wcounts()},
                       SUM(total_stat_pts * aw) AS pts, SUM(total_hitting_stat_pts * aw) AS hit_pts,
                       SUM(total_pitching_stat_pts * aw) AS pit_pts, SUM(negative_points * aw) AS neg,
                       SUM(games_played * aw) AS games
                FROM d WHERE season_year = {int(season)} AND wk = {int(unit)}
                  AND team_id = {int(team_id)}
                GROUP BY season_year, wk, team_id, player_key""")
            self.cache[key] = _finish(rows, self.owners, self.latest_owner, self.catalog)
        return self.cache[key]

    def player(self, season, unit, team_id, pid):
        return next((r for r in self.get((season, unit, team_id)) if str(r.get('pid')) == str(pid)), None)

    def slot_players(self, row):
        key = (row['season'], row['unit'], row['team_id'], row['slot'])
        if key not in self.slot_cache:
            rows = query_for_presentation(f"""
                WITH d AS ({self.base_sql})
                SELECT player_key AS pid, MAX(display_name) AS dname, MAX(player_name) AS pname,
                       SUM(total_stat_pts * aw) AS pts
                FROM d WHERE season_year = {int(row['season'])} AND wk = {int(row['unit'])}
                  AND team_id = {int(row['team_id'])}
                  AND slot_label = '{str(row['slot']).replace("'", "''")}'
                GROUP BY player_key""")
            self.slot_cache[key] = sorted(((r.get('dname') or r.get('pname'), float(r['pts'] or 0))
                                           for r in rows), key=lambda t: (-t[1], str(t[0])))
        return self.slot_cache[key]


class _CbsPlayerLookup(dict):
    """Season-grain lookups from memory; week-grain from the lazy index."""

    def __init__(self, base, week_index):
        super().__init__(base)
        self.week_index = week_index

    def get(self, key, default=None):
        if key[0] == 'week':
            _, season, unit, team_id, pid = key
            return self.week_index.player(season, unit, team_id, pid) or default
        return super().get(key, default)


def _lifetime(player_season, inactive_season, team_season, slot_season, canon_of, canon_names,
              canon_labels, latest_owner, active_teams, current_season, seasons, catalog):
    """Section 5's data: the two player lenses, the Halls, the shame rows,
    the team block (lifetime totals + per-completed-season averages)."""
    canon = lambda t: canon_of.get(t, t)
    cid_of = lambda r: r.get('cid') or canon(r.get('team_id'))
    for rows in (player_season, inactive_season, team_season, slot_season):
        for r in rows:
            if r.get('team_id') is not None and not r.get('cid'):
                r['cid'] = canon(r['team_id'])
    # By-franchise: player x canonical franchise; league-wide: player.
    by_fr = aggregate(player_season, lambda r: (r['pid'], cid_of(r)), SUM_COLS)
    by_pl = aggregate(player_season, lambda r: r['pid'], SUM_COLS)
    _apply_derived(list(by_fr.values()), catalog)
    _apply_derived(list(by_pl.values()), catalog)
    for (pid, cid), a in by_fr.items():
        a['team_id'] = cid
        a['abbrev'] = canon_labels.get(cid, a.get('abbrev'))
        a['team_name'] = canon_names.get(cid, a.get('team_name'))
        a['owner'] = latest_owner.get(cid) or a.get('owner')
    for a in by_pl.values():
        a['team_labels'] = canon_labels
    # Wasted terms per lens.
    unro = defaultdict(lambda: [0.0, 0.0])
    benched_by = defaultdict(lambda: [0.0, 0.0])
    for r in inactive_season:
        if r.get('wasted_bucket') == 'FA' or r.get('team_id') is None:
            u = unro[r['pid']]
            u[0] += float(r.get('hit') or 0)
            u[1] += float(r.get('pit') or 0)
        else:
            b = benched_by[(r['pid'], cid_of(r))]
            b[0] += float(r.get('hit') or 0)
            b[1] += float(r.get('pit') or 0)
    for (pid, cid), a in by_fr.items():
        bh, bp = benched_by.get((pid, cid), (0.0, 0.0))
        nh, np_ = _split_negative(a)
        a.update(benched_hit=bh, benched_pit=bp, unrostered_hit=0.0, unrostered_pit=0.0,
                 neg_hit=nh, neg_pit=np_)
        add_wasted(a)
    benched_pl = defaultdict(lambda: [0.0, 0.0])
    mine_by = defaultdict(dict)
    for (p, cid), v in benched_by.items():
        benched_pl[p][0] += v[0]
        benched_pl[p][1] += v[1]
        mine_by[p][cid] = v
    shame = []
    for pid, a in by_pl.items():
        bh, bp = benched_pl.get(pid, (0.0, 0.0))
        uh, up = unro.get(pid, (0.0, 0.0))
        nh, np_ = _split_negative(a)
        a.update(benched_hit=bh, benched_pit=bp, unrostered_hit=uh, unrostered_pit=up,
                 neg_hit=nh, neg_pit=np_)
        add_wasted(a)
        mine = mine_by.get(pid, {})
        s = dict(a)
        for i, d in enumerate(('hit', 'pit')):
            top = max(mine.items(), key=lambda kv: (kv[1][i], str(kv[0])), default=None)
            s[f'bench_by_{d}'] = (f"{canon_labels.get(top[0], top[0])} ({top[1][i]:,.0f})"
                                  if top and top[1][i] > 0 else '')
        shame.append(s)
    # Single season by franchise (band 3): the player-season rows with wasted.
    benched_pst = defaultdict(lambda: [0.0, 0.0])
    for i in inactive_season:
        if i.get('wasted_bucket') != 'FA' and i.get('team_id') is not None:
            b = benched_pst[(i.get('pid'), i.get('season'), i.get('team_id'))]
            b[0] += float(i.get('hit') or 0)
            b[1] += float(i.get('pit') or 0)
    ps_rows = []
    for r in player_season:
        row = dict(r)
        bh, bp = benched_pst.get((r['pid'], r['season'], r['team_id']), (0.0, 0.0))
        nh, np_ = _split_negative(row)
        row.update(benched_hit=bh, benched_pit=bp, unrostered_hit=0.0, unrostered_pit=0.0,
                   neg_hit=nh, neg_pit=np_)
        ps_rows.append(add_wasted(row))
    # Slots.
    slot_fr = aggregate(slot_season, lambda r: (r['pid'], cid_of(r), r['slot']),
                        ['pts', 'games'])
    slot_pl = aggregate(slot_season, lambda r: (r['pid'], r['slot']), ['pts', 'games'])
    for (pid, cid, slot), a in slot_fr.items():
        a['slot'] = slot
        a['abbrev'] = canon_labels.get(cid, a.get('abbrev'))
    for (pid, slot), a in slot_pl.items():
        a['slot'] = slot
        a['team_labels'] = canon_labels
    # Team block: active franchises, sentinel fenced.
    completed = [s for s in seasons if s < current_season]
    active_cids = {cid_of(r) for r in team_season if r['season'] == current_season}
    team_rows = [r for r in _fence_teams(team_season) if cid_of(r) in active_cids]
    tw = _team_wasted_rows(team_rows, inactive_season, lambda r: (r['season'], r['team_id']))
    totals = aggregate(tw, cid_of, SUM_COLS)
    avgs = aggregate([r for r in tw if r['season'] in completed], cid_of, SUM_COLS)
    _apply_derived(list(totals.values()), catalog)
    top_players = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    for r in player_season:
        cid = cid_of(r)
        for c in COUNT_COLS + ['pts', 'hit_pts', 'pit_pts']:
            top_players[cid][c][r.get('dname') or r.get('pname')] += float(r.get(c) or 0)
    for cid, a in totals.items():
        a['team_name'] = canon_names.get(cid, a.get('team_name'))
        a['abbrev'] = canon_labels.get(cid, a.get('abbrev'))
        a['owner'] = latest_owner.get(cid) or a.get('owner')
        a['team_id'] = cid
        a['top_players'] = {c: sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
                            for c, d in top_players[cid].items()}
        add_wasted(a)
    avg_rows = []
    for cid, a in avgs.items():
        n = len(a['seasons'])
        if not n:
            continue
        row = {c: (float(a.get(c) or 0) / n) for c in SUM_COLS}
        row.update(team_id=cid, team_name=canon_names.get(cid), abbrev=canon_labels.get(cid),
                   owner=latest_owner.get(cid) or a.get('owner'), completed_seasons=a['seasons'],
                   seasons=a['seasons'], season=None, unit=None,
                   top_players=totals.get(cid, {}).get('top_players', {}))
        # Rates over the completed-season totals, not averaged rates.
        for c in ('h', 'ab', 'b_bb', 'hbp', 'sf', 'tb', 'er', 'outs', 'p_bb', 'p_h', 'k'):
            row['_' + c] = a.get(c)
        rate_src = {c: a.get(c) for c in SUM_COLS}
        add_rates(rate_src)
        for c in ('avg', 'obp', 'slg', 'era', 'whip', 'k9'):
            row[c] = rate_src[c]
        add_wasted(row)
        avg_rows.append(row)
    _apply_derived(avg_rows, catalog)
    return {'player_franchise': by_fr, 'player_league': by_pl, 'player_season': ps_rows,
            'shame': shame, 'team_total': list(totals.values()), 'team_avg': avg_rows,
            'slot_franchise': slot_fr, 'slot_league': slot_pl, 'slot_season': slot_season}


def _merge_by_canon(by_team, canon):
    out = defaultdict(float)
    for t, v in by_team.items():
        out[canon(t)] += v
    return dict(out)
