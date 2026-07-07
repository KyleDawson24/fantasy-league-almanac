"""
Generate the season-to-date BBCode report (milestone summary).

Deliberately calendar-agnostic: run it any week and it reports the
season through the latest loaded matchup period ("Through Week N").
The intended uses are the milestone posts -- the All-Star break
first-half review now, an extended end-of-season edition later -- with
occasion-specific flavor supplied by the optional leagueNoteHeader.txt /
leagueNoteFooter.txt files (see output/note_files.py), not by any
break-aware logic here.

Structure mirrors the weekly recap (generate_summary.py) since the ESPN
front-page renderer is simpler than most BBCode targets: bold-label
callouts and short lines, no tables. Season-scope replaces week-scope:
team callouts show (W-L, pts/wk) on the same per-gameplay-week lens the
almanac's Advanced Standings uses, superlatives are the season's best
single weeks, and the All-League Team is the season-to-date optimal
lineup the almanac Home tab already computes.

MLB-37 (2026-07-07) reshaped the presentation: contributor lines carry
per-week averages, the superlatives block groups top-level records /
matchup oddities / draft value with the Game- and Loss-of-the-Week
tallies closing the section, the records list splits positive and
negative blocks sorted by point impact, and the season wasted list is
bench/IL + active-negative only (unowned points are a player-pool fact,
not a manager's shame).
"""

import db
db.init()
from db import query_snowflake

import almanac_data
import note_files
import records
import stat_catalog
from almanac_render import _all_league_slash_line, _per_week_value
from formatters import format_top_scorer_stats_line
from generate_summary import (
    _join_names,
    _recap_record_label,
    fmt_stat_value_with_unit,
    format_hitter_line,
    format_pitcher_line,
    format_records,
    format_player_records,
    format_top_scorer_line,
)


# ---------- data ----------


def get_active_season():
    return query_snowflake(
        "SELECT MAX(season_year) AS sy FROM fct_team_weekly_active_performance"
    )[0]['sy']


def get_latest_matchup_period(season_year):
    return query_snowflake("""
        SELECT MAX(matchup_period) AS mp
        FROM fct_team_weekly_active_performance
        WHERE season_year = %s
    """, (season_year,))[0]['mp']


def get_season_standings(season_year):
    """One row per team from mart_team_season_standings (regular season,
    abnormal weeks in -- the gameplay-day denominators normalize them),
    ordered by season calculated points."""
    return query_snowflake("""
        SELECT team_id, team_name, team_abbrev, owner_display,
               wins, losses, ties,
               scoring_days_played, standard_matchup_days,
               calculated_hitting_pts, calculated_pitching_pts,
               calculated_points, against_calculated_points
        FROM mart_team_season_standings
        WHERE season_year = %s
        ORDER BY calculated_points DESC
    """, (season_year,))


# Rate columns must not be summed across weeks; identity/label fields
# aren't numeric aggregates. Everything else on the player weekly fact
# (counting stats + per-stat _pts + score totals) sums cleanly.
_NO_SUM_KEYS = {
    'season_year', 'matchup_period', 'team_id', 'player_id',
    'avg', 'obp', 'slg', 'ops', 'era', 'whip',
    'k_per_9', 'k_per_bb', 'hr_per_9', 'bb_per_9',
}


def _rate(numerator, denominator):
    return numerator / denominator if denominator else None


def _recompute_season_rates(agg):
    """MLB-39: the aggregation loop sums counting columns, but rates
    can't be summed -- without this step each player's rate keys keep
    the FIRST aggregated week's values, and the shared card formatters
    read those keys verbatim (Yordan .435/.462/.957, Sanchez 0.00 ERA).
    Recompute every displayed rate from the season counting sums,
    mirroring macros/rate_stats.sql exactly."""
    h = agg.get('h') or 0
    ab = agg.get('ab') or 0
    b_bb = agg.get('b_bb') or 0
    hbp = agg.get('hbp') or 0
    sf = agg.get('sf') or 0
    tb = agg.get('tb') or 0
    er = agg.get('er') or 0
    outs = agg.get('outs') or 0
    p_h = agg.get('p_h') or 0
    p_bb = agg.get('p_bb') or 0
    p_hr = agg.get('p_hr') or 0
    k = agg.get('k') or 0
    ip = outs / 3.0

    agg['avg'] = _rate(h, ab)
    agg['obp'] = _rate(h + b_bb + hbp, ab + b_bb + hbp + sf)
    agg['slg'] = _rate(tb, ab)
    obp, slg = agg['obp'], agg['slg']
    agg['ops'] = (obp + slg) if (obp is not None and slg is not None) else None
    agg['era'] = _rate(er * 9.0, ip)
    agg['whip'] = _rate(p_h + p_bb, ip)
    agg['k_per_9'] = _rate(k * 9.0, ip)
    agg['k_per_bb'] = _rate(k, p_bb)
    agg['hr_per_9'] = _rate(p_hr * 9.0, ip)
    agg['bb_per_9'] = _rate(p_bb * 9.0, ip)
    return agg


def get_season_player_totals(season_year):
    """Season-total active production per (team, player) stint.

    Wide rows (SELECT * for the same reason the weekly recap uses it:
    the shared stat-line formatters consume a broad swath of counting +
    *_pts columns), aggregated in Python at (team_id, player_id) grain
    so team-contributor lists attribute a traded player's production to
    the right stint. League-wide superlatives treat each stint as the
    entity -- same identity the lines print ("Player (ABBR)"). Rates are
    recomputed from the summed counting stats (see MLB-39).
    """
    rows = query_snowflake("""
        SELECT *
        FROM fct_player_weekly_active_performance
        WHERE season_year = %s
    """, (season_year,))

    totals = {}
    for row in rows:
        key = (row['team_id'], row['player_id'])
        agg = totals.get(key)
        if agg is None:
            totals[key] = dict(row)
            continue
        for col, value in row.items():
            if col.lower() in _NO_SUM_KEYS:
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                agg[col] = (agg.get(col) or 0) + value
    return sorted(
        (_recompute_season_rates(agg) for agg in totals.values()),
        key=lambda r: r.get('calculated_points') or 0,
        reverse=True,
    )


def get_biggest_hero(season_year):
    """The season's highest-scoring hero week: a win where the top
    scorer's gap over his own team's #2 exceeded the margin of victory.
    Mirrors the league_notes.hero convention exactly -- platform lens
    (the lens that decides W/L), abnormal weeks excluded. The
    "most-heroic" (largest would-have-been deficit) variant is MLB-40."""
    rows = query_snowflake("""
        WITH wins AS (
            SELECT t.matchup_period, t.team_id, t.team_name, t.opponent_name,
                   (t.platform_points - t.opponent_points) AS margin
            FROM fct_team_weekly_active_performance t
            WHERE t.season_year = %s AND t.result = 'W'
              AND t.is_abnormal = false
        ),
        ranked AS (
            SELECT p.matchup_period, p.team_id, p.platform_points,
                   p.display_name, p.team_abbrev,
                   ROW_NUMBER() OVER (
                       PARTITION BY p.matchup_period, p.team_id
                       ORDER BY p.platform_points DESC NULLS LAST
                   ) AS rk
            FROM fct_player_weekly_active_performance p
            WHERE p.season_year = %s
        ),
        top2 AS (
            SELECT matchup_period, team_id,
                   MAX(CASE WHEN rk = 1 THEN platform_points END) AS top_pts,
                   MAX(CASE WHEN rk = 1 THEN display_name END)    AS hero_name,
                   MAX(CASE WHEN rk = 1 THEN team_abbrev END)     AS team_abbrev,
                   MAX(CASE WHEN rk = 2 THEN platform_points END) AS second_pts
            FROM ranked
            WHERE rk <= 2
            GROUP BY 1, 2
        )
        SELECT w.matchup_period, w.team_name, w.opponent_name, w.margin,
               t.hero_name, t.team_abbrev, t.top_pts, t.second_pts,
               (t.top_pts - t.second_pts) AS gap
        FROM wins w
        JOIN top2 t
          ON w.matchup_period = t.matchup_period AND w.team_id = t.team_id
        WHERE (t.top_pts - t.second_pts) > w.margin
        ORDER BY t.top_pts DESC
        LIMIT 1
    """, (season_year, season_year))
    return rows[0] if rows else None


def get_biggest_blowout(season_year):
    """Largest calculated-lens margin of the season (winner's row).
    Abnormal weeks excluded, matching the records convention."""
    rows = query_snowflake("""
        SELECT matchup_period, team_name, opponent_name,
               calculated_points, opponent_calculated_points, calculated_margin
        FROM mart_team_matchup
        WHERE season_year = %s AND is_abnormal = false AND NOT is_playoff
          AND calculated_margin > 0
        ORDER BY calculated_margin DESC
        LIMIT 1
    """, (season_year,))
    return rows[0] if rows else None


def get_result_extreme(season_year, result, best):
    """Most calculated points in a loss (result='L', best=True) or fewest
    in a win (result='W', best=False). W/L is the official platform
    outcome; the displayed points stay on the calculated lens like every
    other score in the report. Abnormal weeks excluded."""
    rows = query_snowflake(f"""
        SELECT t.matchup_period, t.team_name,
               COALESCE(tod.owner_display, t.owner_name) AS owner_name,
               t.calculated_points
        FROM fct_team_weekly_active_performance t
        LEFT JOIN dim_team_owner tod
            ON t.season_year = tod.season_year AND t.team_id = tod.team_id
        WHERE t.season_year = %s AND t.result = %s AND t.is_abnormal = false
        ORDER BY t.calculated_points {'DESC' if best else 'ASC'}
        LIMIT 1
    """, (season_year, result))
    return rows[0] if rows else None


def get_gotw_lotw_counts(season_year):
    """Per-team tallies of the weekly awards: Game of the Week goes to the
    single team with the league's highest score that week, Loss of the
    Week to the lowest -- on the calculated lens, matching the recap's
    weekly Best/Worst Overall awards. Abnormal weeks count (each week
    still produces exactly one GotW; within a week every team plays the
    same length). DENSE_RANK so a dead-heat week credits both teams."""
    return query_snowflake("""
        SELECT team_abbrev,
               SUM(CASE WHEN hi_rk = 1 THEN 1 ELSE 0 END) AS gotw_n,
               SUM(CASE WHEN lo_rk = 1 THEN 1 ELSE 0 END) AS lotw_n
        FROM (
            SELECT team_abbrev,
                   DENSE_RANK() OVER (
                       PARTITION BY matchup_period
                       ORDER BY calculated_points DESC
                   ) AS hi_rk,
                   DENSE_RANK() OVER (
                       PARTITION BY matchup_period
                       ORDER BY calculated_points ASC
                   ) AS lo_rk
            FROM fct_team_weekly_active_performance
            WHERE season_year = %s AND NOT is_playoff
        )
        GROUP BY team_abbrev
        HAVING gotw_n > 0 OR lotw_n > 0
        ORDER BY gotw_n DESC, lotw_n DESC, team_abbrev
    """, (season_year,))


def get_draft_extremes(season_year):
    """(best_value, biggest_bust) rows from the draft board, on the
    almanac Draft Recap leaderboards' basis: keepers included at fair
    effective picks via the shared _draft_with_effective_picks helper.
    Per MLB-37: keepers are EXCLUDED from the bust side -- a keeper slot
    isn't a real "selection" to be embarrassed by -- but remain eligible
    as the best value."""
    from almanac_logic import _draft_with_effective_picks
    ranked = [r for r in _draft_with_effective_picks(
                  almanac_data.get_draft_board(season_year))
              if r.get('value_delta') is not None]
    if not ranked:
        return None, None
    best_value = max(ranked, key=lambda r: (r['value_delta'], -r['overall_pick']))
    non_keepers = [r for r in ranked if not r.get('keeper')]
    biggest_bust = min(
        non_keepers, key=lambda r: (r['value_delta'], -r['overall_pick']),
    ) if non_keepers else None
    return best_value, biggest_bust


def get_alltime_record_buffer():
    """Top-10 all-time leaderboard rows per (grain, stat, direction) --
    the visibility buffer the records section needs to distinguish a
    brand-new record (possibly shared by several teams this season) from
    a tie of an older standing mark."""
    return query_snowflake("""
        SELECT l.entity_grain, l.stat_name, l.record_direction, l.rank,
               l.season_year, l.matchup_period,
               l.team_id, l.team_name, l.team_abbrev,
               -- Canonical owner label; the leaderboard's owner_name is the
               -- raw per-row fact value and its casing drifts with ESPN
               -- profile edits (Mcginley vs McAvery on different weeks).
               COALESCE(tod.owner_display, l.owner_name) AS owner_name,
               l.player_id, l.player_name, l.display_name, l.stat_value
        FROM mart_stat_leaderboard l
        LEFT JOIN dim_team_owner tod
            ON l.season_year = tod.season_year AND l.team_id = tod.team_id
        WHERE l.record_scope = 'all_time'
          AND l.performance_status = 'active'
          AND l.rank <= 10
        ORDER BY l.entity_grain, l.stat_name, l.record_direction, l.rank
    """)


def get_season_wasted_players(season_year, limit=5):
    """Season shame list: bench/IL production plus active-negative
    magnitude per player. Unowned (FA) points are deliberately EXCLUDED
    per MLB-37 -- "Kyle Karros is having a good year and is available"
    is player-pool trivia, not a manager's waste. FULL OUTER between the
    bench and active legs so a pure self-harm starter (never benched,
    plenty of negative days) still appears."""
    return query_snowflake("""
        WITH bench AS (
            SELECT player_id,
                   MAX(player_name) AS player_name,
                   SUM(calculated_points) AS bench_wasted_pts,
                   MAX(team_name) AS bench_team_name
            FROM fct_player_weekly_inactive_performance
            WHERE season_year = %s AND wasted_bucket = 'ROSTERED_INACTIVE'
            GROUP BY player_id
        ),
        active AS (
            SELECT player_id,
                   MAX_BY(team_name, matchup_period) AS active_team_name,
                   SUM(GREATEST(0, -platform_points)) AS negative_active_pts,
                   SUM(platform_points) AS active_points
            FROM fct_player_weekly_active_performance
            WHERE season_year = %s
            GROUP BY player_id
        ),
        player_meta AS (
            SELECT player_id, display_name, pro_team, position, eligible_slots
            FROM (
                SELECT player_id, display_name, pro_team, position, eligible_slots,
                       ROW_NUMBER() OVER (
                           PARTITION BY player_id
                           ORDER BY scoring_period DESC
                       ) AS rn
                FROM fct_player_daily_performance
                WHERE season_year = %s
            )
            WHERE rn = 1
        )
        SELECT COALESCE(m.display_name, b.player_name) AS display_name,
               m.pro_team,
               m.position,
               m.eligible_slots,
               COALESCE(a.active_team_name, b.bench_team_name, 'Free Agent')
                   AS fantasy_team,
               COALESCE(b.bench_wasted_pts, 0) AS bench_wasted_pts,
               COALESCE(a.negative_active_pts, 0) AS negative_active_pts,
               COALESCE(b.bench_wasted_pts, 0)
                   + COALESCE(a.negative_active_pts, 0) AS wasted_points,
               a.active_points
        FROM bench b
        FULL OUTER JOIN active a USING (player_id)
        LEFT JOIN player_meta m USING (player_id)
        WHERE COALESCE(b.bench_wasted_pts, 0)
                  + COALESCE(a.negative_active_pts, 0) > 0
        ORDER BY wasted_points DESC
        LIMIT %s
    """, (season_year, season_year, season_year, limit))


def get_team_wasted_totals(season_year):
    """Season wasted points per team: bench/IL production plus the
    negative-active magnitude, on the same player-week net convention the
    player list uses. FA-pool waste belongs to no roster and is absent
    here too."""
    return query_snowflake("""
        WITH bench AS (
            SELECT team_id, SUM(calculated_points) AS bench_pts
            FROM fct_team_weekly_inactive_performance
            WHERE season_year = %s AND team_id IS NOT NULL
            GROUP BY team_id
        ),
        neg AS (
            SELECT team_id, SUM(GREATEST(0, -platform_points)) AS neg_pts
            FROM fct_player_weekly_active_performance
            WHERE season_year = %s
            GROUP BY team_id
        ),
        labels AS (
            SELECT team_id, MAX_BY(team_abbrev, matchup_period) AS team_abbrev
            FROM fct_team_weekly_active_performance
            WHERE season_year = %s
            GROUP BY team_id
        )
        SELECT l.team_abbrev,
               ROUND(COALESCE(b.bench_pts, 0) + COALESCE(n.neg_pts, 0), 1)
                   AS wasted_pts
        FROM labels l
        LEFT JOIN bench b USING (team_id)
        LEFT JOIN neg n USING (team_id)
        ORDER BY wasted_pts DESC
    """, (season_year, season_year, season_year))


# ---------- formatting ----------


def _per_week(row, value):
    """Season total -> per-standard-matchup average, on the same
    gameplay-day lens as the almanac's Advanced Standings."""
    return _per_week_value(row, value)


def _record_str(wins, losses, ties):
    return f"{wins}-{losses}-{ties}" if ties else f"{wins}-{losses}"


def _fmt_players(player_list, team_row, score_key='calculated_points'):
    """Contributor line: per-week averages (MLB-37), normalized with the
    TEAM's gameplay-day denominators -- 'what does this player do in a
    typical week' rather than a season total."""
    return ", ".join(
        f"{p['display_name']}: {_per_week(team_row, p[score_key])}"
        for p in player_list
    )


def _team_top_players(players, team_id, score_key, n=3):
    ranked = sorted(
        [p for p in players
         if p['team_id'] == team_id and (p.get(score_key) or 0) > 0],
        key=lambda p: p[score_key],
        reverse=True,
    )
    return ranked[:n]


def format_team_callouts(standings, players):
    """The recap-mirroring season callout block: best overall (W-L,
    pts/wk) / hitting / pitching with top-3 per-week contributor
    averages, then the worsts without contributors. Ranked by per-week
    production (every team shares the denominator, so the order matches
    season totals)."""
    by_hitting = sorted(standings, key=lambda r: r['calculated_hitting_pts'], reverse=True)
    by_pitching = sorted(standings, key=lambda r: r['calculated_pitching_pts'], reverse=True)
    best, worst = standings[0], standings[-1]
    best_hit, worst_hit = by_hitting[0], by_hitting[-1]
    best_pit, worst_pit = by_pitching[0], by_pitching[-1]

    def wk(row, col):
        return _per_week(row, row[col])

    return [
        f"[b]Best Overall Team[/b]: {best['team_name']} "
        f"({_record_str(best['wins'], best['losses'], best['ties'])}, "
        f"{wk(best, 'calculated_points')} pts/wk)",
        _fmt_players(
            _team_top_players(players, best['team_id'], 'calculated_points'),
            best,
        ),
        f"[b]Best Hitting Team[/b]: {best_hit['team_name']} "
        f"({wk(best_hit, 'calculated_hitting_pts')} offensive pts/wk)",
        _fmt_players(
            _team_top_players(players, best_hit['team_id'], 'calculated_hitting_pts'),
            best_hit,
            'calculated_hitting_pts',
        ),
        f"[b]Best Pitching Team[/b]: {best_pit['team_name']} "
        f"({wk(best_pit, 'calculated_pitching_pts')} pitching pts/wk)",
        _fmt_players(
            _team_top_players(players, best_pit['team_id'], 'calculated_pitching_pts'),
            best_pit,
            'calculated_pitching_pts',
        ),
        "",
        f"[b]Worst Overall Team[/b]: {worst['team_name']} "
        f"({_record_str(worst['wins'], worst['losses'], worst['ties'])}, "
        f"{wk(worst, 'calculated_points')} pts/wk)",
        f"[b]Worst Hitting Team[/b]: {worst_hit['team_name']} "
        f"({wk(worst_hit, 'calculated_hitting_pts')} offensive pts/wk)",
        f"[b]Worst Pitching Team[/b]: {worst_pit['team_name']} "
        f"({wk(worst_pit, 'calculated_pitching_pts')} pitching pts/wk)",
    ]


def format_top_player_callouts(players):
    """Season Top Scorer / Top Hitter / Top Pitcher, reusing the weekly
    recap's card lines over season-total rows. Same two-way gate on Top
    Scorer: it renders only when the leader produced non-zero hitting AND
    pitching points (otherwise it duplicates one of the other two)."""
    top_scorer = next(
        (p for p in players if (p.get('calculated_points') or 0) > 0), None)
    hitters = [p for p in players if (p.get('calculated_hitting_pts') or 0) > 0]
    pitchers = [p for p in players if (p.get('calculated_pitching_pts') or 0) > 0]
    top_hitter = max(hitters, key=lambda p: p['calculated_hitting_pts']) if hitters else None
    top_pitcher = max(pitchers, key=lambda p: p['calculated_pitching_pts']) if pitchers else None

    two_way = (
        top_scorer is not None
        and (top_scorer.get('calculated_hitting_pts') or 0) > 0
        and (top_scorer.get('calculated_pitching_pts') or 0) > 0
    )
    lines = []
    if two_way:
        lines.append(f"[b]Top Scorer[/b]: {format_top_scorer_line(top_scorer)}")
    if top_hitter:
        lines.append(f"[b]Top Hitter[/b]: {format_hitter_line(top_hitter)}")
    if top_pitcher:
        lines.append(f"[b]Top Pitcher[/b]: {format_pitcher_line(top_pitcher)}")
    return lines


def _format_award_counts(label, counts, key):
    """Grouped-by-count award line (MLB-37 format):
    '[b]Games of the Week[/b]: 4: SMEL. 3: NPNP. 2: WALK. 1: LAW, GPGP'."""
    by_count = {}
    for c in counts:
        n = int(c[key] or 0)
        if n > 0:
            by_count.setdefault(n, []).append(c['team_abbrev'])
    if not by_count:
        return None
    groups = [
        f"{n}: {', '.join(sorted(by_count[n]))}"
        for n in sorted(by_count, reverse=True)
    ]
    return f"[b]{label}[/b]: " + ". ".join(groups)


def format_superlatives(season_records, season_player_records, hero,
                        blowout, most_in_loss, fewest_in_win,
                        gotw_lotw_counts, draft_extremes,
                        schedule_lookup, season_year):
    """Season Superlatives, grouped per MLB-37: the record-book lines,
    then the matchup oddities, then draft value, with the weekly award
    tallies closing the section."""
    lines = ["", "[u][b]Season Superlatives[/b][/u]"]
    lines.append(f"[b]Best Overall Week[/b]: {season_records['best_total']}")
    lines.append(f"[b]Best Hitting Week[/b]: {season_records['best_hitting']}")
    lines.append(f"[b]Best Pitching Week[/b]: {season_records['best_pitching']}")
    if season_player_records['top_scorer']:
        lines.append(f"[b]Best Individual Week[/b]: {season_player_records['top_scorer']}")
    if season_player_records['top_hitter']:
        lines.append(f"[b]Best Individual Hitting Week[/b]: {season_player_records['top_hitter']}")
    if season_player_records['top_pitcher']:
        lines.append(f"[b]Best Individual Pitching Week[/b]: {season_player_records['top_pitcher']}")

    def week(mp):
        return records.format_week_label(season_year, mp, schedule_lookup)

    lines.append("")
    if blowout:
        lines.append(
            f"[b]Biggest Blowout[/b]: {blowout['team_name']} over "
            f"{blowout['opponent_name']} by {blowout['calculated_margin']:.1f} "
            f"({blowout['calculated_points']:.1f} - "
            f"{blowout['opponent_calculated_points']:.1f}), "
            f"{week(blowout['matchup_period'])}"
        )
    if most_in_loss:
        lines.append(
            f"[b]Most Points in a Loss[/b]: {most_in_loss['team_name']} "
            f"({most_in_loss['owner_name']}) -- "
            f"{most_in_loss['calculated_points']:.1f} pts, "
            f"{week(most_in_loss['matchup_period'])}"
        )
    if fewest_in_win:
        lines.append(
            f"[b]Fewest Points in a Win[/b]: {fewest_in_win['team_name']} "
            f"({fewest_in_win['owner_name']}) -- "
            f"{fewest_in_win['calculated_points']:.1f} pts, "
            f"{week(fewest_in_win['matchup_period'])}"
        )
    if hero:
        lines.append(
            f"[b]Most Points by a Hero[/b]: {hero['hero_name']} "
            f"({hero['team_abbrev']}), {hero['top_pts']:.1f} pts in "
            f"{week(hero['matchup_period'])} -- outscored his #2 by "
            f"{hero['gap']:.1f}, single-handedly covering the "
            f"{hero['margin']:.1f}-pt win over {hero['opponent_name']}"
        )

    # Draft value (MLB-37 copy), then the weekly award tallies close the
    # section.
    best_value, biggest_bust = draft_extremes
    lines.append("")
    if best_value:
        lines.append(
            f"[b]Pick of the Year[/b]: {best_value['team_name']} took "
            f"{best_value['player_name']} with the "
            f"{records.ordinal(best_value['overall_pick'])} pick, and he is "
            f"the {records.ordinal(best_value['points_rank'])} ranked player "
            f"in the league ({int(best_value['value_delta'])} places better "
            f"than his selection)."
        )
    if biggest_bust:
        lines.append(
            f"[b]Bust of the Year[/b]: {biggest_bust['team_name']} took "
            f"{biggest_bust['player_name']} with the "
            f"{records.ordinal(biggest_bust['overall_pick'])} pick, and he is "
            f"the {records.ordinal(biggest_bust['points_rank'])} ranked player "
            f"in the league ({abs(int(biggest_bust['value_delta']))} places "
            f"worse than his selection)."
        )
    gotw_line = _format_award_counts('Games of the Week', gotw_lotw_counts, 'gotw_n')
    lotw_line = _format_award_counts('Losses of the Week', gotw_lotw_counts, 'lotw_n')
    if gotw_line:
        lines.append(gotw_line)
    if lotw_line:
        lines.append(lotw_line)
    return lines


def format_all_league_team(rows):
    """Season-to-date All-League Team (MLB-37 format):
    [b]C[/b]: Ben Rice (FUBB) - 267 pts | .265/.360/.548 | 23 HR, ...
    Rows arrive canonical-slot-ordered and stat-enriched from the
    optimal-team dispatcher (the same rows the almanac Home tab renders)."""
    lines = [
        "",
        "[u][b]All-League Team (Season To Date)[/b][/u]",
        "Optimal lineup according to Active Points produced in the league.",
    ]
    for row in rows:
        slot = row.get('slot_label') or row.get('lineup_slot') or ''
        pts = row.get('platform_points') or 0
        slash = _all_league_slash_line(row)
        stat_line = format_top_scorer_stats_line(row)
        segments = [f"{pts:.0f} pts"]
        if slash and slash != '//':
            segments.append(slash)
        if stat_line:
            segments.append(stat_line)
        lines.append(
            f"[b]{slot}[/b]: {row.get('display_name')} "
            f"({row.get('team_abbrev') or row.get('team_name') or 'FA'}) - "
            + " | ".join(segments)
        )
    return lines


def _holder_label(row):
    if row['entity_grain'] == 'player':
        return f"{row['display_name']} ({row['team_abbrev']}, {row['owner_name']})"
    return f"{row['team_name']} ({row['owner_name']})"


# Stats whose value IS points -- no "(X Points)" suffix, sorted by the
# raw value.
_INTRINSIC_POINT_STATS = {'NEGATIVE_POINTS'}


def format_records_set_this_season(buffer_rows, season_year, schedule_lookup):
    """All-time records set this season, per the MLB-37 rework:

    - Tie policy (the maintainer's PREFERRED version): a rank-1 value
      matched at rank-2 is included only when EVERY holder at that value
      is from this season -- a brand-new record several teams set at
      once (two first-ever perfect games -> list both). A tie of an
      older standing mark is dropped.
    - Positive block first, then a break, then the negative block --
      goodness = polarity x direction (Most HR good; Fewest AB, Most
      Balks bad).
    - Within a block, sorted by |stat_value x points_per_unit| descending
      with an "(X Points)" suffix; unweighted stats (rates, auto-tracked
      extras like H) sink to the bottom of their block alphabetically.
    """
    polarity = stat_catalog.get_polarity_map()
    auto_tracked = stat_catalog.get_auto_tracked()
    weight_map = {
        spec['stat_name']: float(spec['points_per_unit'])
        for spec in almanac_data.get_team_week_stat_specs()
        if spec.get('points_per_unit') is not None
    }

    groups = {}
    for row in buffer_rows:
        groups.setdefault(
            (row['entity_grain'], row['stat_name'], row['record_direction']),
            [],
        ).append(row)

    entries = []
    for (grain, stat, direction), rows in groups.items():
        if not records.should_track_record(
                grain, stat, direction, polarity, auto_tracked):
            continue
        top = rows[0]
        value = top['stat_value']
        # Floor-zero noise: a 0 "record" is nobody's achievement.
        if stat not in records.SCORE_STAT_NAMES and (value or 0) == 0:
            continue

        holders = [r for r in rows if r['stat_value'] == value]
        is_tie = len(holders) > 1
        if is_tie:
            # Standing-mark tie -> drop; brand-new shared record -> keep
            # every simultaneous holder.
            if any(h['season_year'] != season_year for h in holders):
                continue
        elif top['season_year'] != season_year:
            continue

        weight = weight_map.get(stat)
        score_like = stat in records.SCORE_STAT_NAMES or stat in _INTRINSIC_POINT_STATS
        if score_like:
            sort_pts = abs(value)
            suffix = ''
        elif weight:
            sort_pts = abs(value * weight)
            suffix = f" ({value * weight:.1f} Points)"
        else:
            sort_pts = None
            suffix = ''

        direction_good = (
            (polarity.get(stat) == 'positive' and direction == 'most')
            or (polarity.get(stat) == 'negative' and direction == 'fewest')
        )
        label = _recap_record_label(grain, stat, direction)
        value_str = fmt_stat_value_with_unit(stat, value)
        weeks = sorted({
            records.format_week_label(h['season_year'], h['matchup_period'],
                                      schedule_lookup)
            for h in holders
        })
        names = _join_names([_holder_label(h) for h in holders])
        entries.append({
            'good': direction_good,
            'sort_pts': sort_pts,
            'label': label,
            'line': f"[b]{label}[/b]: {names} -- {value_str}, "
                    f"{' & '.join(weeks)}{suffix}",
        })

    if not entries:
        return []

    def block(good):
        members = [e for e in entries if e['good'] == good]
        weighted = sorted(
            (e for e in members if e['sort_pts'] is not None),
            key=lambda e: -e['sort_pts'],
        )
        unweighted = sorted(
            (e for e in members if e['sort_pts'] is None),
            key=lambda e: e['label'],
        )
        return [e['line'] for e in weighted + unweighted]

    lines = ["", "[u][b]All-Time Records Set This Season[/b][/u]"]
    good_block = block(True)
    bad_block = block(False)
    lines.extend(good_block)
    if good_block and bad_block:
        lines.append("")
    lines.extend(bad_block)
    return lines


def format_season_wasted_section(wasted_players, team_totals):
    """Season Top Wasted Performances -- bench/IL + active-negative only
    (no unowned component, per MLB-37), with the 'X.X active negative'
    wording, plus the per-team wasted ranking line."""
    import json
    from formatters import filter_eligible_slots

    lines = []
    if wasted_players:
        lines.extend(["", f"[u][b]Top {len(wasted_players)} Wasted Performances (Season)[/b][/u]"])
    for i, p in enumerate(wasted_players, 1):
        bench_pts = p['bench_wasted_pts'] or 0
        neg_pts = p['negative_active_pts'] or 0
        active_pts = p['active_points'] or 0
        total_pts = p['wasted_points']

        components = [c for c in (bench_pts, neg_pts) if c > 0]
        total_str = (f"{total_pts:.1f} wasted pts" if len(components) > 1
                     else f"{total_pts:.1f} pts")

        parts = []
        if bench_pts:
            parts.append(f"{bench_pts:.1f} benched")
        if neg_pts:
            parts.append(f"{neg_pts:.1f} active negative")
        if active_pts > 0:
            parts.append(f"{active_pts:.1f} active")
        show_paren = len(components) > 1 or active_pts > 0
        paren = f" ({', '.join(parts)})" if (show_paren and parts) else ""

        raw_slots = p.get('eligible_slots')
        if isinstance(raw_slots, str):
            try:
                raw_slots = json.loads(raw_slots)
            except (ValueError, TypeError):
                raw_slots = None
        slots = filter_eligible_slots(raw_slots) if raw_slots else []
        positions_str = "/".join(slots) if slots else (p.get('position') or '?')

        lines.append(
            f"{i}. {p['display_name']} ({p['pro_team']}, {positions_str}) "
            f"-- {p['fantasy_team']} -- {total_str}{paren}"
        )
    if team_totals:
        ranking = ", ".join(
            f"{t['team_abbrev']} {t['wasted_pts']:.1f}" for t in team_totals
        )
        lines.extend(["", f"[b]Team Wasted Totals (bench/IL + negative active)[/b]: {ranking}"])
    return lines


def generate_season_report(season_year):
    """Assemble the full season-to-date report as a BBCode string."""
    schedule_lookup = records.load_schedule_lookup()
    latest_mp = get_latest_matchup_period(season_year)
    through_label = records.format_week_label(
        season_year, latest_mp, schedule_lookup)

    standings = get_season_standings(season_year)
    players = get_season_player_totals(season_year)

    current_season_rows = records.get_current_season_records()
    season_records = format_records(
        current_season_rows, season_only=True, schedule_lookup=schedule_lookup)
    season_player_records = format_player_records(
        current_season_rows, season_only=True, schedule_lookup=schedule_lookup)

    lines = [
        *note_files.header_lines(),
        f"[u][b]{season_year} Season Report (Through {through_label})[/b][/u]",
        "",
        *format_team_callouts(standings, players),
        "",
        *format_top_player_callouts(players),
        *format_superlatives(
            season_records, season_player_records,
            get_biggest_hero(season_year),
            get_biggest_blowout(season_year),
            get_result_extreme(season_year, 'L', best=True),
            get_result_extreme(season_year, 'W', best=False),
            get_gotw_lotw_counts(season_year),
            get_draft_extremes(season_year),
            schedule_lookup, season_year,
        ),
        *format_all_league_team(almanac_data.get_all_league_team(season_year)),
        *format_records_set_this_season(
            get_alltime_record_buffer(), season_year, schedule_lookup),
        *format_season_wasted_section(
            get_season_wasted_players(season_year),
            get_team_wasted_totals(season_year),
        ),
        *note_files.footer_lines(),
    ]
    return "\n".join(lines)


def main():
    import argparse
    import os
    from datetime import datetime

    parser = argparse.ArgumentParser(
        description='Generate the season-to-date BBCode report.')
    parser.add_argument('--season-year', type=int, default=None)
    args = parser.parse_args()

    season_year = args.season_year or get_active_season()
    report = generate_season_report(season_year)

    log_dir = os.path.join(os.path.dirname(__file__), '..', 'output', 'logs')
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y-%m-%d_%H%M%S')
    log_path = os.path.join(log_dir, f'season_report_{season_year}_{timestamp}.txt')
    with open(log_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(report)
    print(f"\nLog saved to: {log_path}")


if __name__ == '__main__':
    main()
