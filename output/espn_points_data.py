"""ESPN season-points data adapter (MLB-243).

THE SPLIT THIS MODULE IMPLEMENTS: format decides the workbook's SHAPE,
platform decides which DATA is available to fill it. `points_almanac`
owns the shape -- Team of the Month / Season / All-Time, a points record
book, points-based Advanced Standings -- and asks an adapter for the
numbers. This is that adapter for ESPN. CBS has its own
(`cbs_almanac_sheets`), because the two platforms deliver genuinely
different feeds; what they must never do is disagree about the shape.

WHY ESPN NEEDED A NEW ONE. The CBS points renderer reads CBS feeds --
`mart_period_standings`, `stg_cbs__rosters`, `stg_cbs__ui_standings`.
An ESPN league has none of those and does not need them: it lands the
same SHARED facts the H2H almanac already reads
(`fct_team_season_performance`, `mart_team_slot_production`,
`fct_player_daily_performance`, `mart_stat_leaderboard`). Pointing CBS
queries at an ESPN league would have been the mirror image of the bug
this ticket fixes -- platform-shaped code deciding a format-shaped
question.

TWO ESPN-SPECIFIC FACTS THIS MODULE ENCODES:

1. THE SCORING PERIOD IS THE CALENDAR. `game_date` is a CBS union
   passthrough and is NULL on every ESPN row, so a date window cannot be
   filtered directly. ESPN numbers scoring periods as contiguous days
   from the season opener, which `stg_mlb__season_calendar` supplies from
   MLB's own StatsAPI -- so period N is opener + (N-1) days. Verified
   against this league's captured span: opener 2026-03-25, period 142 =
   2026-08-13, and the draft stamp in DRAFT_SETTINGS (2026-07-31) lands
   on period 129 exactly.

2. THERE IS ONE MATCHUP PERIOD. A `currentLeagueType = 5` league is one
   season-spanning scoring container, so `matchup_period` is 1 for
   everything and carries no information. Every sub-season window here is
   expressed in scoring periods instead.
"""

from datetime import date, timedelta

from db import (
    flatten_array,
    json_text,
    league_predicate,
    query_snowflake,
)


# Slots that are not lineup positions. Mirrors the CBS window query's
# exclusion: an eligibility array carries bench/IL membership too, and
# neither is a position anybody can be "the best" at.
_NON_POSITION_SLOTS = ("'BE'", "'IL'")


def season_context():
    """The league's data horizon, from the data rather than the clock.

    Returns season_year, the scoring-period span actually captured, the
    MLB season opener that anchors periods to dates, and the latest date
    the capture reaches.
    """
    row = query_snowflake(f"""
        SELECT
            MAX(season_year)     AS season_year,
            MIN(scoring_period)  AS first_period,
            MAX(scoring_period)  AS last_period
        FROM fct_player_daily_performance
        WHERE {league_predicate()}
    """)[0]
    season_year = row['season_year']
    if season_year is None:
        return {
            'season_year': None, 'first_period': None, 'last_period': None,
            'season_opener': None, 'latest_date': None, 'first_season': None,
        }
    season_year = int(season_year)

    opener = query_snowflake(f"""
        SELECT season_opener
        FROM stg_mlb__season_calendar
        WHERE season_year = {season_year}
    """)
    opener = opener[0]['season_opener'] if opener else None

    first_season = query_snowflake(f"""
        SELECT MIN(season_year) AS lo
        FROM fct_team_season_performance
        WHERE {league_predicate()}
    """)[0]['lo']

    first_period = int(row['first_period']) if row['first_period'] else None
    last_period = int(row['last_period']) if row['last_period'] else None
    return {
        'season_year': season_year,
        'first_period': first_period,
        'last_period': last_period,
        'season_opener': opener,
        'latest_date': period_to_date(last_period, opener),
        'first_season': int(first_season) if first_season else season_year,
    }


def period_to_date(scoring_period, season_opener):
    """Scoring period -> calendar date. None when either input is absent,
    which is the honest answer: without the opener there is no anchor, and
    inventing one would put every date-labelled board a few days off."""
    if scoring_period is None or season_opener is None:
        return None
    return season_opener + timedelta(days=int(scoring_period) - 1)


def date_to_period(day, season_opener):
    """Calendar date -> scoring period (the inverse of period_to_date)."""
    if day is None or season_opener is None:
        return None
    return (day - season_opener).days + 1


def month_window(context, today=None):
    """The running Team-of-the-Month window, 8th-of-month rollover.

    Same rule as the CBS board (Kyle, 2026-07-13) so the two points
    workbooks agree on what "this month" means: from the 8th onward the
    CURRENT month accrues; in the first week of a new month the board
    retrospects on the previous, completed one. Capped at the latest day
    captured, and stepped back if the chosen month has no data yet.

    Returns (first_day, last_day, first_period, last_period), or a tuple
    of Nones when the league has no date anchor to reason from.
    """
    opener = context.get('season_opener')
    latest = context.get('latest_date')
    if opener is None or latest is None:
        return (None, None, None, None)

    today = today or date.today()
    anchor = today if today.day >= 8 else (today.replace(day=1)
                                           - timedelta(days=1))
    first = anchor.replace(day=1)
    hi = min(_month_last_day(first), latest)
    # Extraction lag, or a board asked for before the season reaches it:
    # walk back to the most recent month that actually has data.
    while hi < first:
        first = (first - timedelta(days=1)).replace(day=1)
        hi = min(_month_last_day(first), latest)

    lo = max(first, opener)
    return (lo, hi, date_to_period(lo, opener), date_to_period(hi, opener))


def _month_last_day(day):
    nxt = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    return nxt - timedelta(days=1)


def window_lineup(first_period, last_period, points_type='active'):
    """Best lineup over a scoring-period window.

    `fct_player_position_pts` aggregates to season/matchup-period grain,
    and a type-5 league has exactly one matchup period -- so a sub-season
    window has to come off the daily fact, the same way the CBS month
    board does.

    points_type 'active' is the display lens (fantasy-credited production
    only); 'all' is the everything-while-rostered lens that drives the
    Total-Pts Best deviation columns.
    """
    if first_period is None or last_period is None:
        return []

    weight = ('COALESCE(active_weight, 0)' if points_type == 'active' else '1')
    slot = json_text('slot.value')
    candidates = query_snowflake(f"""
        WITH exploded AS (
            SELECT
                player_key, player_id, player_name, display_name, pro_team,
                {slot} AS position,
                CASE WHEN {slot} = 'P'
                     THEN total_pitching_stat_pts
                     ELSE total_hitting_stat_pts END
                    * {weight} AS pos_pts
            FROM fct_player_daily_performance,
                 {flatten_array('eligible_slots', 'slot')}
            WHERE {league_predicate()}
              AND scoring_period BETWEEN {int(first_period)}
                                     AND {int(last_period)}
              AND {slot} NOT IN ({', '.join(_NON_POSITION_SLOTS)})
        )
        SELECT
            player_key,
            MAX(player_id)    AS player_id,
            MAX(player_name)  AS player_name,
            MAX(display_name) AS display_name,
            MAX(pro_team)     AS pro_team,
            position,
            ROUND(CAST(SUM(CAST(pos_pts AS DECIMAL(18, 6))) AS DOUBLE), 1)
                AS position_pts
        FROM exploded
        GROUP BY player_key, position
        HAVING CAST(SUM(CAST(pos_pts AS DECIMAL(18, 6))) AS DOUBLE) > 0
        ORDER BY position, position_pts DESC, player_id, player_key
    """)
    if not candidates:
        return []

    # Lazy imports: almanac_data <-> almanac_logic already carry a
    # module-load cycle (documented there), and this module sits on top of
    # both.
    import almanac_data
    from almanac_logic import get_optimal_team_selections

    caps_year = _caps_year()
    slot_caps = almanac_data.get_slot_capacities(caps_year, matchup_period=None)
    selected = get_optimal_team_selections(candidates, slot_caps)
    _enrich_window_lineup(selected, first_period, last_period, points_type)
    for row in selected:
        # No single boxscore stands behind a multi-day window, so the
        # renderer must not offer a link to one.
        row['period_label'] = 'Season'
    return selected


def _caps_year():
    row = query_snowflake(f"""
        SELECT MAX(season_year) AS sy
        FROM fct_player_daily_performance
        WHERE {league_predicate()}
    """)[0]['sy']
    return int(row) if row is not None else None


def _enrich_window_lineup(lineup, first_period, last_period, points_type):
    """Attach the stat tail + fantasy-team attribution the board's Stat
    Line / Fantasy Team / Owner columns read.

    Attribution is the team that held the player for the most days IN THE
    WINDOW, which is the only honest answer for a board about a window: a
    player traded mid-month produced for whoever rostered him at the time,
    and his current team did not earn the earlier half.
    """
    if not lineup:
        return
    keys = {row.get('player_key') for row in lineup if row.get('player_key')}
    if not keys:
        return
    key_list = ', '.join("'" + str(k).replace("'", "''") + "'" for k in keys)
    weight = ('COALESCE(active_weight, 0)' if points_type == 'active' else '1')

    stats = query_snowflake(f"""
        WITH scoped AS (
            SELECT *
            FROM fct_player_daily_performance
            WHERE {league_predicate()}
              AND scoring_period BETWEEN {int(first_period)}
                                     AND {int(last_period)}
              AND player_key IN ({key_list})
        ),
        holding AS (
            SELECT player_key, team_id, team_name, team_abbrev, owner_name,
                   COUNT(*) AS days,
                   ROW_NUMBER() OVER (
                       PARTITION BY player_key
                       ORDER BY COUNT(*) DESC, team_id
                   ) AS rn
            FROM scoped
            WHERE team_id IS NOT NULL
            GROUP BY player_key, team_id, team_name, team_abbrev, owner_name
        ),
        totals AS (
            SELECT
                player_key,
                SUM(games_played * {weight})                 AS games_played,
                SUM(h * {weight})   AS h,   SUM(ab * {weight})  AS ab,
                SUM(b_bb * {weight}) AS b_bb, SUM(hbp * {weight}) AS hbp,
                SUM(sf * {weight})  AS sf,  SUM(tb * {weight})  AS tb,
                SUM(hr * {weight})  AS hr,  SUM(r * {weight})   AS r,
                SUM(rbi * {weight}) AS rbi, SUM(sb * {weight})  AS sb,
                SUM(w * {weight})   AS w,   SUM(l * {weight})   AS l,
                SUM(sv * {weight})  AS sv,  SUM(k * {weight})   AS k,
                SUM(er * {weight})  AS er,  SUM(outs * {weight}) AS outs,
                SUM(qs * {weight})  AS qs,  SUM(hld * {weight}) AS hld,
                SUM(p_h * {weight}) AS p_h, SUM(p_bb * {weight}) AS p_bb
            FROM scoped
            GROUP BY player_key
        )
        SELECT t.*, h.team_id, h.team_name, h.team_abbrev, h.owner_name
        FROM totals t
        LEFT JOIN holding h
            ON t.player_key = h.player_key AND h.rn = 1
    """)
    by_key = {row['player_key']: row for row in stats}
    for row in lineup:
        extra = by_key.get(row.get('player_key'))
        if extra:
            for column, value in extra.items():
                if column != 'player_key':
                    row.setdefault(column, value)


def home_boards(context, today=None):
    """The three Home boards a points workbook shows, plus the
    total-points partner lineup each deviation column is read against.

    Month is the live one (8th-of-month rollover); Season is the whole
    captured season to date; All-Time spans every season on file.
    """
    import almanac_data

    season_year = context['season_year']
    lo, hi, sp_lo, sp_hi = month_window(context, today=today)
    return {
        'month_window': (lo, hi),
        'month_rows': window_lineup(sp_lo, sp_hi, points_type='active'),
        'month_all_rows': window_lineup(sp_lo, sp_hi, points_type='all'),
        'season_rows': almanac_data.get_optimal_team(
            season_year, points_type='active'),
        'season_all_rows': almanac_data.get_optimal_team(
            season_year, points_type='all'),
        'alltime_rows': almanac_data.get_optimal_team(
            season_year=None, points_type='active'),
        'alltime_all_rows': almanac_data.get_optimal_team(
            season_year=None, points_type='all'),
    }


def season_totals(season_year):
    """Per-team season production -- the points league's standings.

    `mart_team_season_standings` is deliberately NOT the source: that mart
    aggregates matchup results, and a season-points league has none, so it
    is correctly empty. The team season fact carries the totals that
    actually decide this format's standings, and rank comes from points.
    """
    return query_snowflake(f"""
        SELECT
            team_id,
            team_name,
            team_abbrev,
            owner_display,
            calculated_points,
            calculated_hitting_pts,
            calculated_pitching_pts,
            negative_points,
            platform_points,
            RANK() OVER (ORDER BY calculated_points DESC) AS points_rank
        FROM fct_team_season_performance
        WHERE {league_predicate()}
          AND season_year = {int(season_year)}
        ORDER BY calculated_points DESC, team_id
    """)


def slot_production(season_year):
    """Points by lineup slot per team -- the second half of the points
    Advanced Standings table (`mart_team_slot_production`)."""
    return query_snowflake(f"""
        SELECT
            team_id, team_name, team_abbrev, owner_display,
            lineup_slot, slot_calculated_points, starter_count,
            is_active_lineup_slot, sort_order
        FROM mart_team_slot_production
        WHERE {league_predicate()}
          AND season_year = {int(season_year)}
        ORDER BY team_id, sort_order, lineup_slot
    """)


def stat_leaders(season_year, limit_per_stat=1):
    """The points record book's backing rows.

    `mart_stat_leaderboard` is the shared record surface both formats
    already use, and it is populated for this league (8,160 rows). Scoped
    to record-eligible rows and ranked within each stat.
    """
    return query_snowflake(f"""
        SELECT
            record_scope, record_direction, entity_grain, performance_status,
            season_year, team_id, team_name, team_abbrev, owner_display,
            player_id, player_name, display_name,
            stat_name, stat_value, rank
        FROM mart_stat_leaderboard
        WHERE {league_predicate()}
          AND rank <= {int(limit_per_stat)}
        ORDER BY entity_grain, record_scope, stat_name, rank
    """)


# How far after the season opener a draft has to fall before the workbook
# says something. Two weeks is comfortably past any ordinary preseason or
# opening-week draft, so an on-time league never sees this line.
LATE_DRAFT_GRACE_DAYS = 14


def late_draft_note(context):
    """A sentence about pre-league production, or None.

    THE LIMITATION. The type-5 extraction walks every scoring day of the
    MLB season. A league that drafted in July therefore carries four months
    of production from before it existed, attributed by the first lineup we
    ever observed -- so season totals, the record book, the boards and the
    draft-value deltas all include days nobody managed.

    NOT CORRECTED HERE, deliberately: clamping the window touches
    extraction, the daily facts and every aggregate at once, and pre-league
    production is properly a third category rather than a filter. What is
    cheap and reliable is DETECTING it -- the draft stamp against the
    season opener -- so the workbook can say so instead of quietly
    overstating everyone.

    Returns None when the draft is absent, on time, or undatable. Silence
    is the right answer for an ordinary league.
    """
    opener = context.get('season_opener')
    season_year = context.get('season_year')
    if opener is None or season_year is None:
        return None

    rows = query_snowflake(f"""
        SELECT drafted_at
        FROM stg_draft_settings
        WHERE {league_predicate()}
          AND season_year = {int(season_year)}
    """)
    drafted_at = rows[0]['drafted_at'] if rows else None
    if drafted_at is None:
        return None

    drafted_on = getattr(drafted_at, 'date', lambda: drafted_at)()
    late_by = (drafted_on - opener).days
    if late_by <= LATE_DRAFT_GRACE_DAYS:
        return None

    # %-d is POSIX-only and raises on Windows, which is the platform the
    # stranger install targets. Compose the day number instead.
    return (
        f'Heads up: this league drafted on {drafted_on:%B} {drafted_on.day}, '
        f'{drafted_on.year} -- {late_by} days after the {season_year} season '
        f'opened on {opener:%B} {opener.day}. Totals on every tab include '
        f'MLB production from before the league existed, credited to '
        f'whoever first rostered each player. Comparisons BETWEEN teams stay '
        f'fair -- they all carry the same pre-league days -- but season and '
        f'career totals are larger than what was actually managed.'
    )


def has_completed_season(season_year):
    """Has any season on file actually finished?

    The Rivalry Matrix keeps its completed-season requirement, and an
    in-progress first year does not satisfy it. Reading an unfinished
    season as a finished rivalry result would publish a standing nobody
    has earned yet, so this stays a real question with a real answer.
    """
    rows = query_snowflake(f"""
        SELECT COUNT(*) AS n
        FROM int_league_season_closure
        WHERE {league_predicate()}
          AND is_season_complete
    """)
    return bool(rows and rows[0]['n'])
