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

import re
from datetime import date, timedelta

import almanac_render
import owner_labels
from db import (
    flatten_array,
    json_text,
    league_predicate,
    query_for_presentation,
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
    row = query_for_presentation(f"""
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

    opener = query_for_presentation(f"""
        SELECT season_opener
        FROM stg_mlb__season_calendar
        WHERE season_year = {season_year}
    """)
    opener = opener[0]['season_opener'] if opener else None

    first_season = query_for_presentation(f"""
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
    candidates = query_for_presentation(f"""
        WITH exploded AS (
            SELECT
                player_key, player_id, player_name, display_name, pro_team,
                {slot} AS position,
                -- MLB-249 X-1. This tested `= 'P'` alone, so a pitcher
                -- reached the board through his SP or RP eligibility --
                -- which is how ESPN spells most pitchers -- and fell into
                -- the ELSE, priced on total_hitting_stat_pts. That is 0
                -- for a pitcher, so every SP/RP row scored zero and the
                -- Team-of-the-Month board simply had no pitchers in the
                -- slots pitchers actually occupy. Only a league using the
                -- generic P slot was unaffected, which is why it survived.
                --
                -- The three-slot set is not a new rule: it is the same
                -- CASE fct_player_position_pts.sql already applies at the
                -- season/matchup grain. The daily-window path was the one
                -- copy that never got it.
                CASE WHEN {slot} IN ('SP', 'RP', 'P')
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
    # THE DAILY FACT'S OWN SENTINEL (MLB-243). Attribution comes straight
    # off `fct_player_daily_performance`, whose `owner_name` predates
    # dim_owner's resolution and falls back to a bare "Unknown" -- so the
    # month board printed it once per row while every other board on the
    # same page carried the team name. Scoped to this read rather than
    # widened at the shared seam, because on a dim_owner-resolved column
    # "Unknown" is the platform's own label for the unmanned sentinel team
    # and is true there.
    for row in selected:
        owner_labels.apply_row(
            row, owner_keys=('owner_name',),
            labels=owner_labels.DAILY_FACT_UNAVAILABLE_LABELS)
    for row in selected:
        # No single boxscore stands behind a multi-day window, so the
        # renderer must not offer a link to one.
        row['period_label'] = 'Season'
    return selected


def _caps_year():
    row = query_for_presentation(f"""
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

    stats = query_for_presentation(f"""
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


def with_unique_team_abbrevs(rows):
    """Give a board's Fantasy Team cells the league's disambiguated labels.

    The Home boards name the rostering team by abbreviation, and this
    league has two teams abbreviated GGT -- so two different franchises
    appeared under one label, and neither matched the team-page tabs, which
    ARE disambiguated (MLB-243). The rule is the shared one; a league whose
    abbreviations are already distinct gets identical strings back.

    Keyed on team_id and applied to the DISPLAY column only: rows that
    carry no team_id, or a team the franchise registry does not know, keep
    whatever they had.
    """
    labels = {fid: meta['abbrev'] for fid, meta in franchise_map().items()
              if meta.get('abbrev')}
    labels = almanac_render.disambiguated_abbrev_map(labels)
    out = []
    for row in rows or ():
        row = dict(row)
        try:
            label = labels.get(int(row.get('team_id')))
        except (TypeError, ValueError):
            label = None
        if label:
            row['team_abbrev'] = label
        out.append(row)
    return out


def home_boards(context, today=None):
    """The three Home boards a points workbook shows, plus the
    total-points partner lineup each in-season deviation column is read
    against.

    Month is the live one (8th-of-month rollover); Season is the whole
    captured season to date; All-Time spans every season on file and is
    the SAME lineup the H2H Home shows -- almanac_data.get_home_all_time_team,
    with each pick's service_years attached for the Years of Service
    column that replaces the deviation pair on that board (Kyle
    2026-08-17). No all-time partner lineup is fetched: nothing reads it.
    """
    import almanac_data

    season_year = context['season_year']
    lo, hi, sp_lo, sp_hi = month_window(context, today=today)
    boards = {
        'month_rows': window_lineup(sp_lo, sp_hi, points_type='active'),
        'month_all_rows': window_lineup(sp_lo, sp_hi, points_type='all'),
        'season_rows': almanac_data.get_optimal_team(
            season_year, points_type='active'),
        'season_all_rows': almanac_data.get_optimal_team(
            season_year, points_type='all'),
        'alltime_rows': almanac_data.get_home_all_time_team(),
    }
    return {'month_window': (lo, hi),
            **{key: with_unique_team_abbrevs(rows)
               for key, rows in boards.items()}}


def season_totals(season_year):
    """Per-team season production -- the points league's standings.

    `mart_team_season_standings` is deliberately NOT the source: that mart
    aggregates matchup results, and a season-points league has none, so it
    is correctly empty. The team season fact carries the totals that
    actually decide this format's standings, and rank comes from points.
    """
    return query_for_presentation(f"""
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
    return query_for_presentation(f"""
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
    return query_for_presentation(f"""
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

    rows = query_for_presentation(f"""
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


def standings_rows(season_year, stat_specs):
    """The per-team standings row the rich Advanced Standings builder eats,
    sourced for a points league (MLB-243).

    SAME CONTRACT, DIFFERENT SOURCE. `almanac_data.get_team_standings` reads
    `mart_team_season_standings`, which aggregates matchup results and is
    correctly EMPTY here -- a season-points league plays none. The identical
    columns, including every scored counting stat, already exist on
    `fct_team_season_performance`, so the tab's detailed-standings table and
    its colour grading work unchanged once fed from there.

    W/L/T come back as NULL rather than 0. There is no record in this
    format, and zeroes would render as a real 0-0-0 result.
    """
    if not stat_specs:
        raise RuntimeError('No scored standings stat specs found.')

    columns = [_fact_stat_column(spec['stat_name']) for spec in stat_specs]
    stat_select = ',\n            '.join(f't.{c}' for c in columns)

    # One scoring day == one period in this format, so the per-period
    # denominators the builder normalizes by are both the day count.
    return query_for_presentation(f"""
        WITH days AS (
            SELECT COUNT(DISTINCT scoring_period) AS n
            FROM fct_player_daily_performance
            WHERE {league_predicate()} AND season_year = {int(season_year)}
        )
        SELECT
            t.team_id,
            t.team_abbrev,
            t.team_name,
            t.owner_display,
            CAST(NULL AS INTEGER) AS wins,
            CAST(NULL AS INTEGER) AS losses,
            CAST(NULL AS INTEGER) AS ties,
            (SELECT n FROM days) AS matchup_periods_played,
            (SELECT n FROM days) AS scoring_days_played,
            -- The builder renders every stat cell as
            -- value * standard_matchup_days / scoring_days_played, which is
            -- a per-matchup average. This format has no matchup to average
            -- over and the table is labelled Season Totals, so the two
            -- denominators are set equal and the multiplier becomes 1 --
            -- the cells are the counts themselves.
            (SELECT n FROM days) AS standard_matchup_days,
            t.calculated_hitting_pts,
            t.calculated_pitching_pts,
            t.calculated_points,
            CAST(NULL AS DOUBLE) AS against_calculated_points,
            {stat_select}
        FROM fct_team_season_performance t
        WHERE {league_predicate('t')}
          AND t.season_year = {int(season_year)}
        ORDER BY t.calculated_points DESC, t.team_id
    """)


def _fact_stat_column(stat_name):
    """Scored-stat name -> its column on the team season fact. Mirrors
    almanac_data._fact_stat_column_name and re-validates, because the value
    is interpolated into SQL."""
    import almanac_data

    column = almanac_data._fact_stat_column_name(stat_name)
    if not re.match(r'^[a-z][a-z0-9_]*$', column):
        raise ValueError(f'Unsafe stat column name: {column!r}')
    return column


def rank_arc(season_year):
    """Cumulative standing by SCORING PERIOD -- the points league's version
    of the rank-by-week arc (MLB-243).

    The H2H arc walks matchup periods and ranks on cumulative wins. This
    format has one matchup period covering the whole season, so that arc is
    a single point; the day is the unit that actually moves here, and the
    standing is cumulative points.

    Same output columns as `almanac_data.get_team_rank_arc`
    (team_id, team_abbrev, period, standings_rank) so the chart machinery
    is untouched.
    """
    return query_for_presentation(f"""
        WITH daily AS (
            SELECT team_id, scoring_period,
                   SUM(CAST(COALESCE(total_stat_pts, 0) AS DECIMAL(18, 6)))
                       AS pts
            FROM fct_player_daily_performance
            WHERE {league_predicate()}
              AND season_year = {int(season_year)}
              AND team_id IS NOT NULL
              AND is_active_slot
            GROUP BY team_id, scoring_period
        ),
        labelled AS (
            SELECT d.team_id, d.scoring_period, d.pts, f.team_abbrev
            FROM daily d
            LEFT JOIN fct_team_season_performance f
                ON  f.team_id     = d.team_id
                AND f.season_year = {int(season_year)}
                AND {league_predicate('f')}
        ),
        cume AS (
            SELECT team_id, team_abbrev, scoring_period,
                   SUM(pts) OVER (PARTITION BY team_id
                                  ORDER BY scoring_period) AS cume_pts
            FROM labelled
        )
        SELECT team_id, team_abbrev, scoring_period AS period,
               ROW_NUMBER() OVER (
                   PARTITION BY scoring_period
                   ORDER BY cume_pts DESC, team_id) AS standings_rank
        FROM cume
        ORDER BY period, standings_rank
    """)


def season_finishes():
    """Season finishes for a points league.

    `almanac_data.get_espn_season_finishes` reads the empty H2H standings
    mart. The platform's own seed and final rank are on `stg_team_standings`,
    which IS populated, so the finishes table renders from there instead.

    `is_champion` stays False while nothing has finished -- the H2H version
    derives it from sweeping the playoff weeks, and this format has none.
    Crowning the points leader mid-season would invent a title.
    """
    return query_for_presentation(f"""
        SELECT
            s.season_year,
            s.team_id,
            COALESCE(f.team_abbrev, s.team_abbrev) AS team_abbrev,
            f.owner_display,
            CAST(NULL AS INTEGER) AS wins,
            CAST(NULL AS INTEGER) AS losses,
            CAST(NULL AS INTEGER) AS ties,
            COALESCE(
                s.playoff_seed,
                ROW_NUMBER() OVER (
                    PARTITION BY s.season_year
                    ORDER BY f.calculated_points DESC NULLS LAST, s.team_id)
            ) AS finish,
            s.final_rank,
            FALSE AS is_champion
        FROM stg_team_standings s
        LEFT JOIN fct_team_season_performance f
            ON  f.league_key  = s.league_key
            AND f.season_year = s.season_year
            AND f.team_id     = s.team_id
        WHERE {league_predicate('s')}
        ORDER BY s.season_year DESC, finish
    """)


# ---------------------------------------------------------------------------
# Inputs for the SHARED season-points standings presenter (MLB-243).
#
# The presenter (cbs_almanac_sheets.build_standings_rows) is pure layout and
# queries nothing. These functions supply its arguments from ESPN's own
# facts, which is what "same tab, different platform" means here: the CBS
# models are never touched for an ESPN league, and the ESPN book is not a
# section-by-section imitation of a head-to-head layout.
# ---------------------------------------------------------------------------

def presenter_context(context):
    """The presenter's context: season, the latest unit of the arc, and the
    first season on file. `latest_period` is a SCORING DAY here."""
    return {
        'season_year': context['season_year'],
        'latest_period': context['last_period'],
        'first_season': context['first_season'],
    }


def franchise_map():
    """{team_id: {'canonical_id', 'abbrev'}} for the canonical roll-up.

    ESPN team ids ARE the franchise ids, and `dim_franchise` already
    resolves the configured-name override, so canonical_id is the
    franchise's own id unless a lineage seed says otherwise.
    """
    rows = query_for_presentation(f"""
        SELECT franchise_id, canonical_franchise_id, canonical_abbrev,
               canonical_name
        FROM dim_franchise
        WHERE {league_predicate()}
    """)
    out = {}
    for row in rows:
        try:
            fid = int(row['franchise_id'])
            cid = int(row['canonical_franchise_id'])
        except (TypeError, ValueError):
            continue
        out[fid] = {'canonical_id': cid,
                    'abbrev': row['canonical_abbrev'],
                    'name': row['canonical_name']}
    return out


def dense_rank_arc(season_year):
    """Team x SCORING DAY cumulative standing, built from first principles.

    FOUR THINGS THIS HAS TO GET RIGHT, and the sparse version got none of
    them:

    1. DENSE SPINE. Every team gets a row for every scoring day through the
       latest captured one -- not only the days it happened to score. A
       cross join against the day list, not a group-by over production.
    2. CARRY FORWARD. Cumulative totals run over the dense spine, so a day
       with no production repeats yesterday's total instead of vanishing.
       A team that goes quiet holds its line; it does not leave a hole in
       the chart.
    3. RANK FROM CUMULATIVE TOTALS, on that day -- the standing as it stood,
       not a re-ranking of that day alone.
    4. RECONCILIATION. The final day's cumulative total is the team's season
       total, and the final day's rank is its place in the standings, so the
       chart and the table below it cannot disagree.

    Rows carry the presenter's arc contract: team_id, team_name, period,
    standings_rank, is_latest_period.
    """
    return query_for_presentation(f"""
        WITH days AS (
            SELECT DISTINCT scoring_period
            FROM fct_player_daily_performance
            WHERE {league_predicate()} AND season_year = {int(season_year)}
        ),
        teams AS (
            SELECT team_id, team_name
            FROM fct_team_season_performance
            WHERE {league_predicate()} AND season_year = {int(season_year)}
        ),
        -- 1. THE DENSE SPINE.
        spine AS (
            SELECT t.team_id, t.team_name, d.scoring_period
            FROM teams t CROSS JOIN days d
        ),
        scored AS (
            SELECT team_id, scoring_period,
                   SUM(CAST(COALESCE(total_stat_pts, 0) AS DECIMAL(18, 6)))
                       AS pts
            FROM fct_player_daily_performance
            WHERE {league_predicate()}
              AND season_year = {int(season_year)}
              AND team_id IS NOT NULL
              AND is_active_slot
            GROUP BY team_id, scoring_period
        ),
        -- 2. CARRY FORWARD: the running sum walks the dense spine, so a
        --    zero-production day inherits the previous total.
        cume AS (
            SELECT
                s.team_id,
                s.team_name,
                s.scoring_period,
                SUM(COALESCE(x.pts, 0)) OVER (
                    PARTITION BY s.team_id
                    ORDER BY s.scoring_period
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ) AS cume_pts
            FROM spine s
            LEFT JOIN scored x
                ON x.team_id = s.team_id
               AND x.scoring_period = s.scoring_period
        )
        -- 3. RANK FROM THE CUMULATIVE TOTAL on each day.
        SELECT
            team_id,
            team_name,
            scoring_period AS period,
            ROUND(CAST(cume_pts AS DOUBLE), 1) AS cume_pts,
            ROW_NUMBER() OVER (
                PARTITION BY scoring_period
                ORDER BY cume_pts DESC, team_id) AS standings_rank,
            scoring_period = (SELECT MAX(scoring_period) FROM days)
                AS is_latest_period
        FROM cume
        ORDER BY period, standings_rank
    """)


def presenter_finishes(season_year):
    """CLOSED seasons only, in the presenter's shape.

    THE PRESENTER APPENDS THE SEASON IN FLIGHT ITSELF -- its year_labels
    are the closed seasons plus `str(season)`. Returning the current season
    here too produced a duplicate 2026 column, and worse: everything in
    this set counts as finished history, so the in-flight year picked up a
    medal, a division title and a closed-season average it has not earned.

    Completion is read from `int_league_season_closure`, the project's own
    closure truth. Being an earlier year than the one requested is not
    evidence a season finished -- a league can be mid-season in its only
    year, which is exactly this case.
    """
    return query_for_presentation(f"""
        SELECT
            f.season_year,
            f.team_id                       AS franchise_id,
            f.team_name,
            s.final_rank                    AS standings_rank,
            s.final_rank = 1                AS is_champion
        FROM fct_team_season_performance f
        JOIN int_league_season_closure c
            ON  c.league_key  = f.league_key
            AND c.season_year = f.season_year
            AND c.is_season_complete
        LEFT JOIN stg_team_standings s
            ON  s.league_key  = f.league_key
            AND s.season_year = f.season_year
            AND s.team_id     = f.team_id
        WHERE {league_predicate('f')}
        ORDER BY f.season_year, standings_rank
    """)


def presenter_active_franchises(season_year):
    return query_for_presentation(f"""
        SELECT team_id, team_name
        FROM fct_team_season_performance
        WHERE {league_predicate()} AND season_year = {int(season_year)}
        ORDER BY calculated_points DESC, team_id
    """)


def presenter_season_days():
    """{season_year, days} -- the standard-season clock. One scoring day is
    one gameplay day in this format."""
    return query_for_presentation(f"""
        SELECT season_year, COUNT(DISTINCT scoring_period) AS days
        FROM fct_player_daily_performance
        WHERE {league_predicate()}
        GROUP BY season_year
        ORDER BY season_year
    """)


def presenter_slot_columns():
    """The league's CONFIGURED active lineup slots, in its own order.

    THE COLUMNS OF POINTS BY LINEUP SLOT ARE A LEAGUE SETTING (MLB-243
    correction), and `dim_roster_slot_counts` is where the platform's own
    roster rules land. Rendering a fixed list instead gave this league a
    blank DH column -- it has no DH slot, so nobody could ever have
    started one -- while its UTIL production had no column to land in,
    because the fixed list spelled that slot U.

    `starter_count > 0` is the test for "a slot this league actually
    rosters": ESPN reports every slot it knows about and zeroes the ones
    the league did not configure, so presence in the feed proves nothing.
    A configured slot that happens to have scored nothing still gets its
    column -- an empty column for a real slot is information, an empty
    column for a slot that does not exist is a defect.
    """
    return [row['lineup_slot'] for row in query_for_presentation(f"""
        SELECT lineup_slot
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
    """) if row.get('lineup_slot')]


def presenter_slot_rows(season_year=None):
    """Points by deployed lineup slot. season_year=None sums every season,
    which is the all-time half of the twin."""
    scope = (f'AND season_year = {int(season_year)}'
             if season_year is not None else '')
    # season_year rides every row: the all-time half weighs each season by
    # the days it played, so the presenter needs to know which seasons the
    # capture actually covers.
    return query_for_presentation(f"""
        SELECT team_id, season_year, lineup_slot,
               ROUND(SUM(CAST(slot_calculated_points AS DECIMAL(18, 6))), 1)
                   AS slot_pts
        FROM mart_team_slot_production
        WHERE {league_predicate()} {scope}
        GROUP BY team_id, season_year, lineup_slot
        ORDER BY team_id, season_year, lineup_slot
    """)


def presenter_alltime_pitching(season_year=None):
    """The P column of the all-time slot grid, which the presenter takes
    separately because CBS's pitching slot spans eras its hitter slots do
    not. ESPN captures every slot in every season, so this is simply the P
    slot summed over all of them."""
    return query_for_presentation(f"""
        SELECT team_id,
               ROUND(SUM(CAST(slot_calculated_points AS DECIMAL(18, 6))), 1)
                   AS p_pts
        FROM mart_team_slot_production
        WHERE {league_predicate()}
          AND lineup_slot IN ('P', 'SP', 'RP')
        GROUP BY team_id
        ORDER BY team_id
    """)


# The presenter's detailed-standings columns, in its own vocabulary. Every
# one of these is a column on the team season fact.
_DETAILED_COLUMNS = (
    'h', 'doubles', 'triples', 'hr', 'xbh', 'tb', 'r', 'rbi', 'sb', 'b_bb',
    'w', 'qs', 'k', 'sv', 'hld', 'cg', 'outs',
)


def presenter_detailed_alltime():
    """All-time counting stats per team, the presenter's Detailed Standings
    substrate. Summed over every season on file; in a first year that is
    this season, which is truthful rather than empty."""
    cols = ',\n            '.join(
        f'ROUND(SUM(CAST({c} AS DECIMAL(18, 6))), 1) AS {c}'
        for c in _DETAILED_COLUMNS)
    return query_for_presentation(f"""
        SELECT
            team_id,
            {cols},
            ROUND(SUM(CAST(calculated_hitting_pts AS DECIMAL(18, 6))), 1)
                AS hit_pts,
            ROUND(SUM(CAST(calculated_pitching_pts AS DECIMAL(18, 6))), 1)
                AS pit_pts,
            ROUND(SUM(CAST(calculated_points AS DECIMAL(18, 6))), 1)
                AS total_pts
        FROM fct_team_season_performance
        WHERE {league_predicate()}
        GROUP BY team_id
        ORDER BY team_id
    """)


def presenter_affinity(season_year):
    """Share of active-lineup games by MLB club, season and all-time on one
    spine -- the presenter's affinity contract."""
    # The presenter keys the shared MLB spine on a NUMERIC club id. ESPN
    # identifies clubs by abbreviation, so one is derived by dense rank over
    # the abbreviations -- stable for a given set of clubs, and only ever
    # used to group and order the spine. The abbreviation stays the label.
    return query_for_presentation(f"""
        SELECT
            team_id,
            DENSE_RANK() OVER (ORDER BY pro_team)      AS mlb_team_id,
            pro_team                                   AS mlb_team_name,
            ROUND(SUM(CASE WHEN season_year = {int(season_year)}
                           THEN CAST(games_played AS DECIMAL(18, 6))
                           ELSE 0 END), 1)             AS season_wt,
            ROUND(SUM(CAST(games_played AS DECIMAL(18, 6))), 1) AS alltime_wt
        FROM fct_player_daily_performance
        WHERE {league_predicate()}
          AND team_id IS NOT NULL
          AND is_active_slot
          AND pro_team IS NOT NULL
        GROUP BY team_id, pro_team
        ORDER BY team_id, pro_team
    """)


def with_owner_fallback(rows, name_key='team_name', owner_key='owner_display'):
    """Replace an inaccessible Owner value with the team's canonical name.

    THE RULE ITSELF NOW LIVES IN `owner_labels` AND RUNS AT THE OUTPUT
    LAYER'S DATA SEAM (MLB-243), so every tab of both workbooks gets it
    without any renderer opting in -- which is what this function used to
    be, and it reached two surfaces out of six. Kept as a named, copying
    entry point for the two call sites that want it stated at the seam
    they own, and for callers holding rows that never came from a query.
    """
    rows = [dict(row) for row in rows or ()]
    return [owner_labels.apply_row(row, owner_keys=(owner_key,),
                                   name_keys=(name_key,))
            for row in rows]


def acquisition_channels(season_year):
    """Production by acquisition channel, or None when the transaction log
    was never read (MLB-243).

    Returns None -- not [] -- when coverage says the board was unauthorized
    or not served. The caller renders an explicit unavailable line rather
    than a chart of zeroes, because "no adds" and "we could not look" are
    different claims and only one of them is measured.
    """
    if not _transaction_log_was_read(season_year):
        return None
    import almanac_data
    return with_owner_fallback(
        almanac_data.get_team_acquisition_channels(season_year))


def _transaction_log_was_read(season_year):
    rows = query_for_presentation(f"""
        SELECT has_transaction_log
        FROM stg_transaction_coverage
        WHERE {league_predicate()}
          AND season_year = {int(season_year)}
    """)
    return bool(rows and rows[0]['has_transaction_log'])


def has_completed_season(season_year):
    """Has any season on file actually finished?

    The Rivalry Matrix keeps its completed-season requirement, and an
    in-progress first year does not satisfy it. Reading an unfinished
    season as a finished rivalry result would publish a standing nobody
    has earned yet, so this stays a real question with a real answer.
    """
    rows = query_for_presentation(f"""
        SELECT COUNT(*) AS n
        FROM int_league_season_closure
        WHERE {league_predicate()}
          AND is_season_complete
    """)
    return bool(rows and rows[0]['n'])
