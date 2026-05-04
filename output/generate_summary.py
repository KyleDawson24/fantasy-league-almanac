"""
Generate the weekly front-page summary from the mart tables.

Reads fct_weekly_team_performance and fct_weekly_player_performance (the wide
convergence facts shipped in Phase 3.1) to produce a BBCode-formatted
summary for the ESPN league front page.
"""

import os

from dotenv import load_dotenv
import snowflake.connector

from formatters import (
    format_hitter_stats_line,
    format_pitcher_stats_line,
    format_top_scorer_stats_line,
    format_contributors,
    filter_eligible_slots,
    fmt_ip,
    fmt_value,
    STAT_DISPLAY,
    STAT_ABBREV,
)
import json

load_dotenv()

SNOWFLAKE_CONFIG = {
    "account": os.getenv("SNOWFLAKE_ACCOUNT"),
    "user": os.getenv("SNOWFLAKE_USER"),
    "password": os.getenv("SNOWFLAKE_PASSWORD"),
    "database": os.getenv("SNOWFLAKE_DATABASE"),
    "schema": "ANALYTICS",
    "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE"),
}


def query_snowflake(sql, params=None):
    """Run a query and return results as a list of dicts."""
    conn = snowflake.connector.connect(**SNOWFLAKE_CONFIG)
    cursor = conn.cursor()
    try:
        cursor.execute(sql, params or ())
        columns = [desc[0].lower() for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        cursor.close()
        conn.close()


def get_weekly_scores(season_year, matchup_period=None):
    if matchup_period is None:
        result = query_snowflake("""
            SELECT MAX(matchup_period) as mp
            FROM fct_weekly_team_performance
            WHERE season_year = %s
        """, (season_year,))
        matchup_period = result[0]['mp']

    scores = query_snowflake("""
        SELECT season_year, matchup_period, team_name, team_id,
               platform_points, platform_hitting_pts, platform_pitching_pts,
               owner_name, opponent_name,
               opponent_owner, opponent_points, result
        FROM fct_weekly_team_performance
        WHERE matchup_period = %s
        AND season_year = %s
        ORDER BY platform_points DESC
    """, (matchup_period, season_year))

    return matchup_period, scores

def get_player_contributions(season_year, matchup_period):
    """Fetch weekly player stats for contributor callouts.

    Sources from fct_weekly_player_performance (the wide convergence fact) for
    architectural consistency with team queries -- both go through the
    convergence facts, not the legacy *_scores facts.

    SELECT * here is deliberate: the shared formatter (output/formatters.py)
    consumes a wide swath of counting + *_pts columns to pick the top-N
    point contributors per player. Enumerating ~50 columns by hand is
    tedious and error-prone (missing column -> formatter silently drops
    that stat from consideration). Per-row data volume is small.
    """
    return query_snowflake("""
        SELECT *
        FROM fct_weekly_player_performance
        WHERE matchup_period = %s
        AND season_year = %s
        ORDER BY platform_points DESC
    """, (matchup_period, season_year))

def get_contribution_callouts(scores, players):
    best_overall_team  = scores[0]['team_name']
    best_hitting_team  = sorted(scores, key=lambda x: x['platform_hitting_pts'], reverse=True)[0]['team_name']
    best_pitching_team = sorted(scores, key=lambda x: x['platform_pitching_pts'], reverse=True)[0]['team_name']

    top_overall = [
        p for p in players if p['team_name'] == best_overall_team
    ][:5]

    top_hitters = sorted(
        [p for p in players if p['team_name'] == best_hitting_team and p['platform_hitting_pts'] > 0],
        key=lambda x: x['platform_hitting_pts'],
        reverse=True
    )[:3]

    top_pitchers = sorted(
        [p for p in players if p['team_name'] == best_pitching_team and p['platform_pitching_pts'] > 0],
        key=lambda x: x['platform_pitching_pts'],
        reverse=True
    )[:3]

    return {
        'best_overall_team':  best_overall_team,
        'best_hitting_team':  best_hitting_team,
        'best_pitching_team': best_pitching_team,
        'top_overall':        top_overall,
        'top_hitters':        top_hitters,
        'top_pitchers':       top_pitchers,
        # Player-level superlatives across the whole league (not scoped to a team)
        'top_scorer':         find_top_scorer(players),
        'top_hitter':         find_top_hitter(players),
        'top_pitcher':        find_top_pitcher(players),
    }

def find_tough_luck(scores):
    ranked = sorted(scores, key=lambda x: x['platform_points'], reverse=True)
    second_place = ranked[1]
    if second_place['result'] == 'L':
        return {
            'team': second_place['team_name'],
            'points': second_place['platform_points'],
            'opponent': second_place['opponent_name'],
            'opponent_points': second_place['opponent_points'],
        }
    return None


def find_lucky_bastard(scores):
    ranked = sorted(scores, key=lambda x: x['platform_points'], reverse=True)
    second_worst = ranked[-2]
    if second_worst['result'] == 'W':
        return {
            'team': second_worst['team_name'],
            'points': second_worst['platform_points'],
            'opponent': second_worst['opponent_name'],
            'opponent_points': second_worst['opponent_points'],
        }
    return None


def check_fair_and_just(scores):
    ranked = sorted(scores, key=lambda x: x['platform_points'], reverse=True)
    # Count active matchups from scores that have an opponent
    num_matchups = len([s for s in scores if s['opponent_name'] is not None]) // 2
    for i, team in enumerate(ranked):
        if team['result'] is None:
            return False  # bye week team
        if i < num_matchups and team['result'] != 'W':
            return False
        if i >= num_matchups and team['result'] != 'L':
            return False
    return True


# ---------- Top Scorer / Top Hitter / Top Pitcher callouts ----------
#
# Phase 5: stat-line rendering lives in output/formatters.py so the
# records report can share the same format. The wrappers below add the
# recap prefix "Player (TeamAbbr), X.X pts -- "; everything after the
# first " -- " comes from the shared utility.
#
# Top Scorer (new in Phase 5) is always rendered alongside Top Hitter and
# Top Pitcher even though for typical weeks it duplicates one of them.
# The structure is intentional: it covers the rare two-way case (Ohtani
# topping the league overall while not topping either category alone) and
# keeps the three-callout shape consistent week-to-week.


def find_top_scorer(players):
    """Player with the highest platform_points (>0). None if no qualifying player."""
    scorers = [p for p in players if (p['platform_points'] or 0) > 0]
    return max(scorers, key=lambda p: p['platform_points']) if scorers else None


def find_top_hitter(players):
    """Player with the highest platform_hitting_pts (>0). None if no qualifying player."""
    hitters = [p for p in players if (p['platform_hitting_pts'] or 0) > 0]
    return max(hitters, key=lambda p: p['platform_hitting_pts']) if hitters else None


def find_top_pitcher(players):
    """Player with the highest platform_pitching_pts (>0). None if no qualifying player."""
    pitchers = [p for p in players if (p['platform_pitching_pts'] or 0) > 0]
    return max(pitchers, key=lambda p: p['platform_pitching_pts']) if pitchers else None


def format_top_scorer_line(player):
    """Top Scorer recap callout: total platform_points + top-5 across both pools."""
    return (
        f"{player['display_name']} ({player['team_abbrev']}), "
        f"{player['platform_points']:.1f} pts -- "
        f"{format_top_scorer_stats_line(player)}"
    )


def format_hitter_line(player):
    """Top Hitter recap callout: hitting pts + shared hitter stat line."""
    return (
        f"{player['display_name']} ({player['team_abbrev']}), "
        f"{player['platform_hitting_pts']:.1f} pts -- "
        f"{format_hitter_stats_line(player)}"
    )


def format_pitcher_line(player):
    """Top Pitcher recap callout: pitching pts + shared pitcher stat line."""
    return (
        f"{player['display_name']} ({player['team_abbrev']}), "
        f"{player['platform_pitching_pts']:.1f} pts -- "
        f"{format_pitcher_stats_line(player)}"
    )


def get_wasted_points(season_year, matchup_period, limit=5):
    """
    Top N wasted-points performers for a matchup period (Phase 4).

    A player who was both ROSTERED_INACTIVE and FA in the same matchup
    period (e.g., dropped mid-week) gets their wasted_points summed
    across both buckets — one row per player. The two source buckets are
    surfaced separately (fa_wasted_pts, bench_wasted_pts) so the formatter
    can attribute "X unowned, Y benched" in the parenthetical.

    Joins stg_box_scores for MLB pro_team and eligible-slots metadata
    (Phase 5: position display now uses filtered eligibleSlots so
    multi-position players like Sanoja show as "2B, RP" instead of just
    a primary position), and fct_weekly_player_performance to detect
    partial-active weeks (player who also had active days during the
    same matchup period).

    Team-label priority (in COALESCE order):
      1. Active team (from fct_weekly_player_performance) — captures the
         FA-then-rostered case ("they have since been picked up")
      2. Bench team (from mart_wasted_points ROSTERED_INACTIVE row)
      3. 'Free Agent' fallback when neither active nor bench association
    """
    return query_snowflake("""
        WITH wasted_combined AS (
            SELECT
                player_id,
                MAX(display_name) AS display_name,
                MAX(CASE WHEN wasted_bucket = 'FA'
                         THEN wasted_points END) AS fa_wasted_pts,
                MAX(CASE WHEN wasted_bucket = 'ROSTERED_INACTIVE'
                         THEN wasted_points END) AS bench_wasted_pts,
                SUM(wasted_points) AS wasted_points_total,
                MAX(CASE WHEN wasted_bucket = 'ROSTERED_INACTIVE'
                         THEN team_name END) AS bench_team_name
            FROM mart_wasted_points
            WHERE season_year = %s AND matchup_period = %s
            GROUP BY player_id
        ),
        player_meta AS (
            -- Most-recent (player, scoring_period) row in this matchup_period
            -- for pro_team / position / eligible_slots. Handles mid-period
            -- trades by picking the latest snapshot. eligible_slots is
            -- VARIANT-typed; comes back as a JSON string the formatter
            -- parses with json.loads().
            SELECT player_id, pro_team, position, eligible_slots
            FROM (
                SELECT player_id, pro_team, position, eligible_slots,
                       ROW_NUMBER() OVER (
                           PARTITION BY player_id
                           ORDER BY scoring_period DESC
                       ) AS rn
                FROM stg_box_scores
                WHERE season_year = %s AND matchup_period = %s
            )
            WHERE rn = 1
        ),
        active_points AS (
            SELECT player_id,
                   team_name AS active_team_name,
                   platform_points
            FROM fct_weekly_player_performance
            WHERE season_year = %s AND matchup_period = %s
        )
        SELECT
            w.display_name,
            m.pro_team,
            m.position,
            m.eligible_slots,
            COALESCE(a.active_team_name, w.bench_team_name, 'Free Agent')
                AS fantasy_team,
            w.fa_wasted_pts,
            w.bench_wasted_pts,
            -- Phase 5 (#5): negative-active-as-waste. Days when the
            -- player was started and produced net-negative points are
            -- doubly wasteful -- benching them would have netted 0
            -- instead of a loss. Add the absolute negative-active
            -- portion to the total wasted, and expose separately so
            -- the formatter can attribute it as "doubly wasted" in
            -- the breakdown.
            GREATEST(0, -COALESCE(a.platform_points, 0)) AS doubly_wasted_pts,
            w.wasted_points_total
                + GREATEST(0, -COALESCE(a.platform_points, 0)) AS wasted_points,
            a.platform_points AS active_points
        FROM wasted_combined w
        LEFT JOIN player_meta m ON w.player_id = m.player_id
        LEFT JOIN active_points a ON w.player_id = a.player_id
        ORDER BY w.wasted_points_total
                 + GREATEST(0, -COALESCE(a.platform_points, 0)) DESC
        LIMIT %s
    """, (season_year, matchup_period, season_year, matchup_period,
          season_year, matchup_period, limit))


def format_wasted_points(wasted):
    """
    Top Wasted Performances callout. Combined across FA and ROSTERED_INACTIVE
    buckets per player; parenthetical attributes points to their source.

    Format per row:
        N. Player (MLB Team, Slot1, Slot2, ...) -- Fantasy Team -- TOTAL [(BREAKDOWN)]

    Phase 5: position is now a comma-joined list of filtered eligible
    slots (specific positions only -- BE/IL/UTIL/IF/flex-shapes dropped,
    generic OF/P collapsed when a more specific outfield/pitching slot
    is present). Falls back to primary `position` when eligible_slots
    is empty (legacy raw rows).

    TOTAL takes one of two forms:
      - "X+Y waste pts" / "X+Y+Z waste pts"   when the waste decomposes
                                              into 2+ non-zero components
                                              of (unowned, benched, doubly)
      - "X.X pts"                             when only one component
                                              contributed waste

    BREAKDOWN parenthetical lists non-zero of (unowned, benched, doubly
    wasted, active). It appears when there's anything to attribute beyond
    the main line — multiple waste components, or non-zero active context.
    Phase 5 (#5): negative-active production now adds to the waste total
    as a "doubly wasted" component (benching the player for 0 pts would
    have done strictly better than the negative active line). Positive
    active still appears as context (informative: they did contribute on
    other days) but is NOT added to the waste total.
    """
    if not wasted:
        return []

    lines = ["", f"[u][b]Top {len(wasted)} Wasted Performances[/b][/u]"]
    for i, p in enumerate(wasted, 1):
        fa_pts      = p['fa_wasted_pts']     or 0
        bench_pts   = p['bench_wasted_pts']  or 0
        doubly_pts  = p['doubly_wasted_pts'] or 0   # Phase 5 #5: max(0, -active)
        active_pts  = p['active_points']     or 0
        total_pts   = p['wasted_points']

        # Headline: additive shape when multiple waste components fired.
        components = [c for c in (fa_pts, bench_pts, doubly_pts) if c > 0]
        if len(components) > 1:
            total_str = "+".join(f"{c:.1f}" for c in components) + " waste pts"
        else:
            total_str = f"{total_pts:.1f} pts"

        # Breakdown parenthetical
        parts = []
        if fa_pts:     parts.append(f"{fa_pts:.1f} unowned")
        if bench_pts:  parts.append(f"{bench_pts:.1f} benched")
        if doubly_pts: parts.append(f"{doubly_pts:.1f} doubly wasted")
        if active_pts > 0: parts.append(f"{active_pts:.1f} active")
        # Show the breakdown when it adds info beyond the headline.
        show_paren = len(components) > 1 or active_pts > 0
        paren = f" ({', '.join(parts)})" if (show_paren and parts) else ""

        # eligible_slots VARIANT comes back from snowflake-connector as a
        # JSON string (or already-parsed list, defensively); filter to
        # specific positions and fall back to primary position when empty.
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
    return lines


def get_records(active_season, season_only=False):
    """
    Fetch all matchup scores for records calculation.
    Excludes abnormal weeks. If season_only=True, filters to active_season.

    Phase 5: ranks on calculated_* (rules-normalized under current weights)
    rather than platform_* (ESPN's official tally at the time). Records
    we care about are "what's the best week under today's scoring," so
    historical seasons get re-evaluated under the current rule set rather
    than locked to whatever weights were live then.
    """
    season_filter = f"AND f.season_year = {active_season}" if season_only else ""

    return query_snowflake(f"""
        SELECT
            f.season_year,
            f.matchup_period,
            f.team_name,
            f.owner_name,
            f.calculated_points,
            f.calculated_hitting_pts,
            f.calculated_pitching_pts
        FROM fct_weekly_team_performance f
        LEFT JOIN MATCHUP_SCHEDULE s
            ON f.season_year = s.season_year
            AND f.matchup_period = s.matchup_period
        WHERE s.is_abnormal = false
        {season_filter}
    """)


def format_records(records, season_only):
    """Format the 6 team records (best/worst total/hitting/pitching).

    season_only=True drops the year from the week label since it's
    implied (current season). False keeps "YYYY Week N" for all-time.
    """
    best_total    = max(records, key=lambda x: x['calculated_points'])
    best_hitting  = max(records, key=lambda x: x['calculated_hitting_pts'])
    best_pitching = max(records, key=lambda x: x['calculated_pitching_pts'])

    worst_total    = min(records, key=lambda x: x['calculated_points'])
    worst_hitting  = min(records, key=lambda x: x['calculated_hitting_pts'])
    worst_pitching = min(records, key=lambda x: x['calculated_pitching_pts'])

    def fmt(row, score_key):
        if season_only:
            week_str = f"Week {row['matchup_period']}"
        else:
            week_str = f"{row['season_year']} Week {row['matchup_period']}"
        return (
            f"{row['team_name']} ({row['owner_name']}) -- "
            f"{row[score_key]:.1f} pts, {week_str}"
        )

    return {
        'best_total':     fmt(best_total,    'calculated_points'),
        'best_hitting':   fmt(best_hitting,  'calculated_hitting_pts'),
        'best_pitching':  fmt(best_pitching, 'calculated_pitching_pts'),
        'worst_total':    fmt(worst_total,   'calculated_points'),
        'worst_hitting':  fmt(worst_hitting, 'calculated_hitting_pts'),
        'worst_pitching': fmt(worst_pitching,'calculated_pitching_pts'),
    }


def get_player_records():
    """Fetch rank-1 player records (Top Scorer / Hitter / Pitcher) for both
    all-time and current-season scopes, in the 'best' direction.

    Reads from mart_stat_leaderboard which already pre-computes these.
    Returns a list of up to 6 rows (3 stats x 2 scopes); consumer keys by
    record_scope + stat_name.
    """
    return query_snowflake("""
        SELECT entity_grain, stat_name, record_scope,
               team_abbrev, owner_name, display_name,
               season_year, matchup_period, stat_value
        FROM mart_stat_leaderboard
        WHERE entity_grain = 'player'
          AND stat_name IN ('CALCULATED_POINTS',
                            'CALCULATED_HITTING_PTS',
                            'CALCULATED_PITCHING_PTS')
          AND record_direction = 'best'
          AND rank = 1
    """)


def format_player_records(player_records, season_only):
    """Format the 3 player records (Top Scorer / Hitter / Pitcher) for one
    scope. Returns a dict with keys 'top_scorer', 'top_hitter',
    'top_pitcher'; each value is a formatted string or None when no rank-1
    row exists for that (stat, scope) tuple.
    """
    scope = 'current_season' if season_only else 'all_time'
    by_stat = {r['stat_name']: r for r in player_records
               if r['record_scope'] == scope}

    def fmt(row):
        if row is None:
            return None
        if season_only:
            week_str = f"Week {row['matchup_period']}"
        else:
            week_str = f"{row['season_year']} Week {row['matchup_period']}"
        return (
            f"{row['display_name']} ({row['team_abbrev']}, {row['owner_name']}) -- "
            f"{row['stat_value']:.1f} pts, {week_str}"
        )

    return {
        'top_scorer':  fmt(by_stat.get('CALCULATED_POINTS')),
        'top_hitter':  fmt(by_stat.get('CALCULATED_HITTING_PTS')),
        'top_pitcher': fmt(by_stat.get('CALCULATED_PITCHING_PTS')),
    }


# ---------- New record callouts (Phase 5) ----------
#
# Detects records broken in the just-recapped matchup_period by querying
# mart_stat_leaderboard for rank-1 rows whose (season, MP) matches the
# current week. Then applies polarity-aware filter rules from the Phase 5
# design:
#   - Player grain: only score-level (CALCULATED_*) records, only Most.
#   - Team grain, score-level (CALCULATED_*): both Most and Least.
#   - Team grain, individual stat with positive scoring weight: Most + Least.
#   - Team grain, individual stat with negative scoring weight: Most only
#     ("most of the bad thing" -- "fewest strikeouts" type records would
#     just reward empty lineups, so we skip those for now).
#   - Zero-weighted stats (H, TB, SF, XBH): skipped entirely.
# Ties surface as "{entity} became the Nth team/player to ..." instead of
# the full broken-record block. Worst-direction records render without a
# contributor list (deferred to a future "least of negatives" handling).

SCORE_STAT_NAMES = {
    'CALCULATED_POINTS', 'CALCULATED_HITTING_PTS', 'CALCULATED_PITCHING_PTS',
}

# The seed uses '1B' / '2B' / '3B' but the wide fct + leaderboard call those
# columns 'SINGLES' / 'DOUBLES' / 'TRIPLES'. Translate seed names to
# leaderboard column names so polarity lookups land correctly.
_SEED_TO_LEADERBOARD = {
    '1B': 'SINGLES',
    '2B': 'DOUBLES',
    '3B': 'TRIPLES',
}


def get_stat_polarity():
    """Map of leaderboard-stat_name -> 'positive' | 'negative' | 'neutral'.
    Derived from sign of points_per_unit in stg_scoring_settings.
    Stats without a row in the seed are 'neutral'. Seed names like '1B'
    are translated to their leaderboard equivalents ('SINGLES') so the
    consumer's stat_name lookup succeeds.
    """
    rows = query_snowflake("""
        SELECT UPPER(stat_name) AS stat_name, points_per_unit
        FROM stg_scoring_settings
    """)
    polarity = {}
    for r in rows:
        name = _SEED_TO_LEADERBOARD.get(r['stat_name'], r['stat_name'])
        ppu = r['points_per_unit'] or 0
        if ppu > 0:
            polarity[name] = 'positive'
        elif ppu < 0:
            polarity[name] = 'negative'
        else:
            polarity[name] = 'neutral'
    return polarity


def should_track_record(grain, stat_name, direction, polarity):
    """Apply Phase 5 record-eligibility rules."""
    if grain == 'player':
        return stat_name in SCORE_STAT_NAMES and direction == 'best'
    # team grain
    if stat_name in SCORE_STAT_NAMES:
        return True
    pol = polarity.get(stat_name)
    if pol is None or pol == 'neutral':
        return False
    if pol == 'positive':
        return True
    return direction == 'best'  # negative-stat: most-of only


def find_new_records(season_year, matchup_period):
    """Records broken or tied in this MP. Returns list of dicts with keys:
    grain, stat_name, direction, new (leaderboard row), prior (rank-2 row
    or None), is_tie (bool), tie_count (int, only when is_tie).
    """
    polarity = get_stat_polarity()

    # All rank-1 leaderboard rows whose holder is the just-recapped MP --
    # for every (grain, stat_name, record_direction) combination at once.
    candidates = query_snowflake("""
        SELECT *
        FROM mart_stat_leaderboard
        WHERE rank = 1
          AND record_scope = 'all_time'
          AND season_year = %s
          AND matchup_period = %s
    """, (season_year, matchup_period))

    out = []
    for cand in candidates:
        grain = cand['entity_grain']
        stat = cand['stat_name']
        direction = cand['record_direction']
        if not should_track_record(grain, stat, direction, polarity):
            continue

        # Rank 2 = prior holder. With our recency tiebreak, this also tells
        # us if we tied (rank-2 stat_value equals rank-1 stat_value).
        prior_rows = query_snowflake("""
            SELECT *
            FROM mart_stat_leaderboard
            WHERE entity_grain = %s
              AND stat_name = %s
              AND record_scope = 'all_time'
              AND record_direction = %s
              AND rank = 2
        """, (grain, stat, direction))
        prior = prior_rows[0] if prior_rows else None

        is_tie = prior is not None and prior['stat_value'] == cand['stat_value']

        # Phase 5 (#3) noise filter: skip tied records at value=0 for
        # individual stats (CG/HLD/SV/QS/HBP/etc. floor every week and
        # the "Nth team to record 0 X" phrasing got noisy fast). Strict
        # breaks at 0 can't happen for these (count can't go negative),
        # so this filter only affects ties.
        if (is_tie
                and stat not in SCORE_STAT_NAMES
                and cand['stat_value'] == 0):
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
        out.append(rec)

    return _sort_records(out)


def count_value_occurrences(grain, stat_name, value):
    """How many (entity, MP) tuples in fct_weekly_*_performance have this
    exact stat_value, excluding abnormal weeks. Used for 'Nth team/player'
    framing on tied records.
    """
    fct = ('fct_weekly_team_performance' if grain == 'team'
           else 'fct_weekly_player_performance')
    col = stat_name.lower()
    rows = query_snowflake(f"""
        SELECT COUNT(*) AS n
        FROM {fct} f
        JOIN matchup_schedule s
          ON f.season_year = s.season_year
         AND f.matchup_period = s.matchup_period
        WHERE s.is_abnormal = false
          AND {col} = %s
    """, (value,))
    return rows[0]['n'] if rows else 0


def _sort_records(records):
    """Stable visual order: player records first, then team score, then
    team stat. Within each grouping, Best before Worst, score columns in
    Total->Hitting->Pitching order, individual stats by display order."""
    score_order = {'CALCULATED_POINTS': 0, 'CALCULATED_HITTING_PTS': 1,
                   'CALCULATED_PITCHING_PTS': 2}
    stat_display_order = list(STAT_DISPLAY.keys())

    def sort_key(rec):
        grain_rank = 0 if rec['grain'] == 'player' else (1 if rec['stat_name'] in SCORE_STAT_NAMES else 2)
        if rec['stat_name'] in SCORE_STAT_NAMES:
            stat_rank = score_order.get(rec['stat_name'], 99)
        else:
            try:
                stat_rank = stat_display_order.index(rec['stat_name'])
            except ValueError:
                stat_rank = 99
        direction_rank = 0 if rec['direction'] == 'best' else 1
        return (grain_rank, stat_rank, direction_rank)

    return sorted(records, key=sort_key)


# ---------- Formatting ----------

def ordinal(n):
    """1 -> '1st', 2 -> '2nd', 3 -> '3rd', 11 -> '11th', etc."""
    if 11 <= n % 100 <= 13:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return f"{n}{suffix}"


def fmt_stat_value_with_unit(stat_name, value):
    """Stat value with unit suffix for inline display.
    Score columns: '37.3 pts'. OUTS: '8.0 IP'. Other counting stats: '5 HR'.
    """
    if stat_name in SCORE_STAT_NAMES:
        return f"{value:.1f} pts"
    if stat_name == 'OUTS':
        return f"{fmt_ip(value)} IP"
    return f"{fmt_value(value)} {STAT_ABBREV.get(stat_name, stat_name)}"


def make_record_label(grain, stat_name, direction):
    """Bolded heading text for a new-record block (Phase 5 design):
      Score columns -> '{Best|Worst} {Player|Team} {Total|Hitting|Pitching} Points'
      Individual stats -> '{Most|Fewest} {Display Name}'
    """
    if stat_name in SCORE_STAT_NAMES:
        prefix = 'Best' if direction == 'best' else 'Worst'
        scope = 'Player' if grain == 'player' else 'Team'
        return f"{prefix} {scope} {STAT_DISPLAY[stat_name]}"
    prefix = 'Most' if direction == 'best' else 'Fewest'
    return f"{prefix} {STAT_DISPLAY.get(stat_name, stat_name)}"


def _team_contributors(players, team_id, stat_col):
    """Build contributor list for a team's record-setting MP. Each entry
    is {display_name, stat_value} for format_contributors() consumption."""
    return [
        {'display_name': p['display_name'], 'stat_value': p.get(stat_col)}
        for p in players if p['team_id'] == team_id
    ]


def _format_player_score_record(rec, players):
    """Player-grain record line + prior + inline stat line via #2 formatter."""
    new = rec['new']
    label = make_record_label(rec['grain'], rec['stat_name'], rec['direction'])
    new_value = new['stat_value']

    # Look up the player's full row in the existing players list to render
    # the position-appropriate stat line.
    player_row = next((p for p in players if p['player_id'] == new['player_id']), None)
    if rec['stat_name'] == 'CALCULATED_HITTING_PTS':
        stat_line = format_hitter_stats_line(player_row) if player_row else ''
    elif rec['stat_name'] == 'CALCULATED_PITCHING_PTS':
        stat_line = format_pitcher_stats_line(player_row) if player_row else ''
    else:
        stat_line = format_top_scorer_stats_line(player_row) if player_row else ''

    new_line = (
        f"[b]New {label}[/b]: {new['display_name']} ({new['team_abbrev']}), "
        f"{new_value:.1f} pts"
    )
    if stat_line:
        new_line += f" -- {stat_line}"

    lines = [new_line]
    prior = rec['prior']
    if prior:
        lines.append(
            f"(Prior: {prior['stat_value']:.1f} pts by "
            f"{prior['display_name']} ({prior['team_abbrev']}) "
            f"in Week {prior['matchup_period']} of {prior['season_year']})"
        )
    return lines


def _format_team_record(rec, players):
    """Team-grain record block. Score column or individual stat both use
    this shape; contributor list only renders for 'best' direction (per
    user spec: worsts don't get contributors yet).
    """
    new = rec['new']
    label = make_record_label(rec['grain'], rec['stat_name'], rec['direction'])
    new_value_str = fmt_stat_value_with_unit(rec['stat_name'], new['stat_value'])

    new_line = f"[b]New {label}[/b]: {new['team_name']}, {new_value_str}"
    lines = [new_line]

    prior = rec['prior']
    if prior:
        prior_value_str = fmt_stat_value_with_unit(rec['stat_name'], prior['stat_value'])
        lines.append(
            f"(Prior: {prior_value_str} by "
            f"{prior['owner_name']} ({prior['team_abbrev']}) "
            f"in Week {prior['matchup_period']} of {prior['season_year']})"
        )

    if rec['direction'] == 'best':
        contribs = _team_contributors(players, new['team_id'], rec['stat_name'].lower())
        contrib_str = format_contributors(contribs, max_n=5)
        if contrib_str:
            lines.append(f"Contributors: {contrib_str}")

    return lines


def _format_tied_record(rec):
    """Compact tied-record line: '[Tied Record for {label}]: {entity} at
    {value}, the {Nth} {team|player} to do so.' No prior block, no
    contributors. The matchup-period context is implicit (this section
    is about what just happened)."""
    new = rec['new']
    label = make_record_label(rec['grain'], rec['stat_name'], rec['direction'])
    value_str = fmt_stat_value_with_unit(rec['stat_name'], new['stat_value'])
    n = rec['tie_count']

    if rec['grain'] == 'team':
        entity = new['team_name']
        kind = 'team'
    else:
        entity = f"{new['display_name']} ({new['team_abbrev']})"
        kind = 'player'

    return [
        f"[b]Tied Record for {label}[/b]: {entity} at {value_str}, "
        f"the {ordinal(n)} {kind} to do so."
    ]


def format_new_records_section(records, players):
    """Full New Records section. Returns list of lines, OR an empty list
    when no records were broken/tied (the section is skipped entirely)."""
    if not records:
        return []

    lines = ["", "[u][b]New Records[/b][/u]"]
    for rec in records:
        lines.append("")
        if rec['is_tie']:
            lines.extend(_format_tied_record(rec))
        elif rec['grain'] == 'player':
            lines.extend(_format_player_score_record(rec, players))
        else:
            lines.extend(_format_team_record(rec, players))
    return lines


def generate_summary(matchup_period, scores, contributions, wasted_points,
                     season_records, alltime_records,
                     season_player_records, alltime_player_records,
                     players, new_records):
    """Build the BBCode-formatted front-page summary."""

    best_overall = scores[0]
    worst_overall = scores[-1]

    by_hitting = sorted(scores, key=lambda x: x['platform_hitting_pts'], reverse=True)
    best_hitting = by_hitting[0]
    worst_hitting = by_hitting[-1]

    by_pitching = sorted(scores, key=lambda x: x['platform_pitching_pts'], reverse=True)
    best_pitching = by_pitching[0]
    worst_pitching = by_pitching[-1]

    def fmt_players(player_list, score_key='platform_points'):
        return ", ".join(
            f"{p['display_name']}: {p[score_key]:.1f}"
            for p in player_list
        )

    lines = [
        f"[u][b]Week {matchup_period} Recap[/b][/u]",
        f"",
        f"[b]Best Overall[/b]: {best_overall['platform_points']:.1f} pts by {best_overall['team_name']}",
        f"{fmt_players(contributions['top_overall'])}",
        f"[b]Best Hitting[/b]: {best_hitting['platform_hitting_pts']:.1f} pts by {best_hitting['team_name']}",
        f"{fmt_players(contributions['top_hitters'], 'platform_hitting_pts')}",
        f"[b]Best Pitching[/b]: {best_pitching['platform_pitching_pts']:.1f} pts by {best_pitching['team_name']}",
        f"{fmt_players(contributions['top_pitchers'], 'platform_pitching_pts')}",
        f"",
        f"[b]Worst Overall[/b]: {worst_overall['platform_points']:.1f} pts by {worst_overall['team_name']}",
        f"[b]Worst Hitting[/b]: {worst_hitting['platform_hitting_pts']:.1f} pts by {worst_hitting['team_name']}",
        f"[b]Worst Pitching[/b]: {worst_pitching['platform_pitching_pts']:.1f} pts by {worst_pitching['team_name']}",
    ]

    # Player-level superlatives across the whole league (top scorer / top hitter
    # / top pitcher by platform_points / platform_hitting_pts / platform_pitching_pts
    # respectively). Stashed in the contributions dict by get_contribution_callouts.
    # Top Scorer is rendered alongside the category leaders even when it
    # duplicates one of them — see comment near find_top_scorer for rationale.
    top_scorer = contributions.get('top_scorer')
    top_hitter = contributions.get('top_hitter')
    top_pitcher = contributions.get('top_pitcher')
    if top_scorer:
        lines.extend([
            f"",
            f"[b]Top Scorer[/b]: {format_top_scorer_line(top_scorer)}",
        ])
    if top_hitter:
        lines.append(f"[b]Top Hitter[/b]: {format_hitter_line(top_hitter)}")
    if top_pitcher:
        lines.append(f"[b]Top Pitcher[/b]: {format_pitcher_line(top_pitcher)}")

    # Top Wasted Performances (Phase 4) — last item in the matchup recap.
    lines.extend(format_wasted_points(wasted_points))

    # New Records (Phase 5 #3) -- skipped entirely when no records were broken
    lines.extend(format_new_records_section(new_records, players))

    # Tough Luck
    tough_luck = find_tough_luck(scores)
    if tough_luck:
        lines.extend([
            f"",
            f"[b]Tough Luck[/b]: {tough_luck['team']} scored {tough_luck['points']:.1f} pts, "
            f"second most in the league, but lost to "
            f"{tough_luck['opponent']}'s {tough_luck['opponent_points']:.1f}",
        ])

    # Lucky Bastard
    lucky = find_lucky_bastard(scores)
    if lucky:
        lines.extend([
            f"",
            f"[b]Lucky Bastard[/b]: {lucky['team']} scored just {lucky['points']:.1f} pts, "
            f"second worst in the league, but beat "
            f"{lucky['opponent']}'s {lucky['opponent_points']:.1f}",
        ])

    # Fair and Just League
    if check_fair_and_just(scores):
        num_matchups = len([s for s in scores if s['opponent_name'] is not None]) // 2
        lines.extend([
            f"",
            f"[b]A FAIR AND JUST LEAGUE![/b] The top {num_matchups} scoring teams "
            f"all won this week, and the bottom {num_matchups} all lost.",
        ])

    # Current Season Records (Phase 5 #6: 6 team + 3 player lines).
    # Player record lines render only when a rank-1 player exists for that
    # (stat, scope) -- typically always present for current season after
    # week 1 and always for all-time, but the None-guard keeps it safe.
    lines.extend([
        f"",
        f"[u][b]Current Season Records[/b][/u]",
        f"[b]Best Matchup Total[/b]: {season_records['best_total']}",
        f"[b]Best Matchup Hitting[/b]: {season_records['best_hitting']}",
        f"[b]Best Matchup Pitching[/b]: {season_records['best_pitching']}",
        f"[b]Worst Matchup Total[/b]: {season_records['worst_total']}",
        f"[b]Worst Matchup Hitting[/b]: {season_records['worst_hitting']}",
        f"[b]Worst Matchup Pitching[/b]: {season_records['worst_pitching']}",
    ])
    if season_player_records['top_scorer']:
        lines.append(f"[b]Top Scorer[/b]: {season_player_records['top_scorer']}")
    if season_player_records['top_hitter']:
        lines.append(f"[b]Top Hitter[/b]: {season_player_records['top_hitter']}")
    if season_player_records['top_pitcher']:
        lines.append(f"[b]Top Pitcher[/b]: {season_player_records['top_pitcher']}")

    # All-Time League Records
    lines.extend([
        f"",
        f"[u][b]All-Time League Records[/b][/u]",
        f"[b]Best Matchup Total[/b]: {alltime_records['best_total']}",
        f"[b]Best Matchup Hitting[/b]: {alltime_records['best_hitting']}",
        f"[b]Best Matchup Pitching[/b]: {alltime_records['best_pitching']}",
        f"[b]Worst Matchup Total[/b]: {alltime_records['worst_total']}",
        f"[b]Worst Matchup Hitting[/b]: {alltime_records['worst_hitting']}",
        f"[b]Worst Matchup Pitching[/b]: {alltime_records['worst_pitching']}",
    ])
    if alltime_player_records['top_scorer']:
        lines.append(f"[b]Top Scorer[/b]: {alltime_player_records['top_scorer']}")
    if alltime_player_records['top_hitter']:
        lines.append(f"[b]Top Hitter[/b]: {alltime_player_records['top_hitter']}")
    if alltime_player_records['top_pitcher']:
        lines.append(f"[b]Top Pitcher[/b]: {alltime_player_records['top_pitcher']}")

    # Additional Notes (optional) -- LeagueNote.txt contents printed verbatim
    # under an [u][b]Additional Notes[/b][/u] header. Skipped entirely (no
    # header either) when the file is missing or empty.
    note_path = os.path.join(os.path.dirname(__file__), "LeagueNote.txt")
    if os.path.exists(note_path):
        with open(note_path, "r", encoding="utf-8") as f:
            note_content = f.read().strip()
        if note_content:
            lines.extend([
                f"",
                f"[u][b]Additional Notes[/b][/u]",
                note_content,
            ])

    # Write to timestamped log file
    from datetime import datetime
    log_dir = os.path.join(os.path.dirname(__file__), "..", "output","logs")
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    log_path = os.path.join(log_dir, f"summary_{matchup_period}_{timestamp}.txt")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\nLog saved to: {log_path}")

    return "\n".join(lines)

if __name__ == "__main__":
    active_season = query_snowflake(
        "SELECT MAX(season_year) as sy FROM fct_weekly_team_performance"
    )[0]['sy']

    matchup_period, scores = get_weekly_scores(active_season)
    players        = get_player_contributions(active_season, matchup_period)
    contributions  = get_contribution_callouts(scores, players)
    wasted_points  = get_wasted_points(active_season, matchup_period)
    new_records    = find_new_records(active_season, matchup_period)

    season_raw      = get_records(active_season, season_only=True)
    alltime_raw     = get_records(active_season, season_only=False)
    season_records  = format_records(season_raw,  season_only=True)
    alltime_records = format_records(alltime_raw, season_only=False)

    # Player records (Phase 5 #6): Top Scorer / Hitter / Pitcher per scope.
    # One leaderboard query covers both scopes; format_player_records splits.
    player_record_rows     = get_player_records()
    season_player_records  = format_player_records(player_record_rows, season_only=True)
    alltime_player_records = format_player_records(player_record_rows, season_only=False)

    summary = generate_summary(matchup_period, scores, contributions,
                               wasted_points, season_records, alltime_records,
                               season_player_records, alltime_player_records,
                               players, new_records)
    print(summary)