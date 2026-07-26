"""
Generate the weekly front-page summary from the mart tables.

Reads fct_team_weekly_active_performance and fct_player_weekly_active_performance (the wide
convergence facts shipped in Phase 3.1) to produce a BBCode-formatted
summary for the ESPN league front page.
"""

import os
import json

# Phase 7: shared script startup (utf-8 stdout reconfig, load_dotenv,
# Snowflake config) lives in db.py. init() is idempotent.
import db
db.init()
from db import league_predicate, query_snowflake

from formatters import (
    format_hitter_stats_line,
    format_pitcher_stats_line,
    format_top_scorer_stats_line,
    format_contributors,
    filter_eligible_slots,
    fmt_ip,
    fmt_value,
)
import note_files
import records
import stat_catalog


def get_weekly_scores(season_year, matchup_period=None):
    if matchup_period is None:
        result = query_snowflake(f"""
            SELECT MAX(matchup_period) as mp
            FROM fct_team_weekly_active_performance
            WHERE season_year = %s
            AND {league_predicate()}
        """, (season_year,))
        matchup_period = result[0]['mp']

    scores = query_snowflake(f"""
        SELECT season_year, matchup_period, team_name, team_id,
               calculated_points, calculated_hitting_pts, calculated_pitching_pts,
               owner_name, opponent_name,
               opponent_owner, opponent_points, result
        FROM fct_team_weekly_active_performance
        WHERE matchup_period = %s
        AND season_year = %s
        AND {league_predicate()}
        ORDER BY calculated_points DESC
    """, (matchup_period, season_year))

    return matchup_period, scores

def get_player_contributions(season_year, matchup_period):
    """Fetch weekly player stats for contributor callouts.

    Sources from fct_player_weekly_active_performance (the wide convergence fact) for
    architectural consistency with team queries -- both go through the
    convergence facts, not the legacy *_scores facts.

    SELECT * here is deliberate: the shared formatter (output/formatters.py)
    consumes a wide swath of counting + *_pts columns to pick the top-N
    point contributors per player. Enumerating ~50 columns by hand is
    tedious and error-prone (missing column -> formatter silently drops
    that stat from consideration). Per-row data volume is small.
    """
    return query_snowflake(f"""
        SELECT *
        FROM fct_player_weekly_active_performance
        WHERE matchup_period = %s
        AND season_year = %s
        AND {league_predicate()}
        ORDER BY calculated_points DESC
    """, (matchup_period, season_year))

def get_contribution_callouts(scores, players):
    best_overall_team  = scores[0]['team_name']
    best_hitting_team  = sorted(scores, key=lambda x: x['calculated_hitting_pts'], reverse=True)[0]['team_name']
    best_pitching_team = sorted(scores, key=lambda x: x['calculated_pitching_pts'], reverse=True)[0]['team_name']

    top_overall = [
        p for p in players if p['team_name'] == best_overall_team
    ][:5]

    top_hitters = sorted(
        [p for p in players if p['team_name'] == best_hitting_team and p['calculated_hitting_pts'] > 0],
        key=lambda x: x['calculated_hitting_pts'],
        reverse=True
    )[:3]

    top_pitchers = sorted(
        [p for p in players if p['team_name'] == best_pitching_team and p['calculated_pitching_pts'] > 0],
        key=lambda x: x['calculated_pitching_pts'],
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

# ---------- Top Scorer / Top Hitter / Top Pitcher callouts ----------
#
# Phase 5: stat-line rendering lives in output/formatters.py so the
# records report can share the same format. The wrappers below add the
# recap prefix "Player (TeamAbbr), X.X pts -- "; everything after the
# first " -- " comes from the shared utility.
#
# Top Scorer renders only on two-way weeks -- when the leading player
# had BOTH non-zero hitting AND non-zero pitching contributions. On
# pure-pitcher or pure-hitter top-scorer weeks (the typical case), the
# Top Scorer line is identical to Top Pitcher or Top Hitter and adds
# noise. v1.x conditional fix; pre-v1.x always rendered.


def find_top_scorer(players):
    """Player with the highest calculated_points (>0). None if no qualifying player."""
    scorers = [p for p in players if (p['calculated_points'] or 0) > 0]
    return max(scorers, key=lambda p: p['calculated_points']) if scorers else None


def find_top_hitter(players):
    """Player with the highest calculated_hitting_pts (>0). None if no qualifying player."""
    hitters = [p for p in players if (p['calculated_hitting_pts'] or 0) > 0]
    return max(hitters, key=lambda p: p['calculated_hitting_pts']) if hitters else None


def find_top_pitcher(players):
    """Player with the highest calculated_pitching_pts (>0). None if no qualifying player."""
    pitchers = [p for p in players if (p['calculated_pitching_pts'] or 0) > 0]
    return max(pitchers, key=lambda p: p['calculated_pitching_pts']) if pitchers else None


# ---------- League This Week (always-on collective lens) ----------
#
# v1.x: list the league's collective averages every week regardless of
# whether anything extreme happened. Foregrounds the league-wide
# context (rank of overall / hitting / pitching means in league
# history) so the per-team superlatives that follow have a frame of
# reference. Sources mart_league_weekly_benchmarks (one row per
# season_year + matchup_period; absolute ranks + total_weeks
# denormalized for single-query reads). A later v1.x pass may add
# conditional "Hot Week / Cold Week" flourishes on top of this; the
# always-on line stays.

def format_league_this_week(season_year, matchup_period):
    """Single BBCode line listing league averages + ranks across the
    three calculated_* lenses. Empty string if the mart has no row
    for (season_year, matchup_period) -- defensive only; the mart
    builds from the same fact the recap reads.
    """
    rows = query_snowflake(f"""
        SELECT calculated_points_mean,        calculated_points_rank,
               calculated_hitting_pts_mean,   calculated_hitting_pts_rank,
               calculated_pitching_pts_mean,  calculated_pitching_pts_rank,
               total_weeks_in_history
        FROM mart_league_weekly_benchmarks
        WHERE season_year = %s AND matchup_period = %s
          AND {league_predicate()}
    """, (season_year, matchup_period))
    if not rows:
        return ''
    b = rows[0]
    n = b['total_weeks_in_history']

    def fmt(mean, rank):
        return f"{mean} ({records.ordinal(rank)} of {n})"

    return (
        f"[b]League This Week:[/b] "
        f"{fmt(b['calculated_points_mean'], b['calculated_points_rank'])} "
        f"points overall, "
        f"{fmt(b['calculated_hitting_pts_mean'], b['calculated_hitting_pts_rank'])} "
        f"points of offense, and "
        f"{fmt(b['calculated_pitching_pts_mean'], b['calculated_pitching_pts_rank'])} "
        f"points of pitching."
    )


def format_top_scorer_line(player):
    """Top Scorer recap callout: total calculated_points + top-5 across both pools."""
    return (
        f"{player['display_name']} ({player['team_abbrev']}), "
        f"{player['calculated_points']:.1f} pts -- "
        f"{format_top_scorer_stats_line(player)}"
    )


def format_hitter_line(player):
    """Top Hitter recap callout: hitting pts + shared hitter stat line."""
    return (
        f"{player['display_name']} ({player['team_abbrev']}), "
        f"{player['calculated_hitting_pts']:.1f} pts -- "
        f"{format_hitter_stats_line(player)}"
    )


def format_pitcher_line(player):
    """Top Pitcher recap callout: pitching pts + shared pitcher stat line."""
    return (
        f"{player['display_name']} ({player['team_abbrev']}), "
        f"{player['calculated_pitching_pts']:.1f} pts -- "
        f"{format_pitcher_stats_line(player)}"
    )


def get_wasted_points(season_year, matchup_period, limit=5):
    """
    Top N wasted-points performers for a matchup period.

    A player who was both ROSTERED_INACTIVE and FA in the same matchup
    period (e.g., dropped mid-week) gets their wasted_points summed
    across both buckets — one row per player. The two source buckets are
    surfaced separately (fa_wasted_pts, bench_wasted_pts) so the formatter
    can attribute "X unowned, Y benched" in the parenthetical.

    Joins fct_player_daily_performance for MLB pro_team and eligible-slots metadata
    (position display uses filtered eligibleSlots so multi-position
    players like Sanoja show as "2B, RP" instead of just a primary
    position), and fct_player_weekly_active_performance to detect
    partial-active weeks (player who also had active days during the
    same matchup period).

    Wasted-performance source: fct_player_weekly_inactive_performance,
    summed across the player's inactive buckets (FA + ROSTERED_INACTIVE)
    within the matchup_period. Values use calculated_points (rules-
    normalized under current-season weights).

    Team-label priority (in COALESCE order):
      1. Active team (from fct_player_weekly_active_performance) -- captures the
         FA-then-rostered case ("they have since been picked up")
      2. Bench team (from fct_player_weekly_inactive_performance ROSTERED_INACTIVE row)
      3. 'Free Agent' fallback when neither active nor bench association
    """
    return query_snowflake(f"""
        WITH wasted_combined AS (
            SELECT
                player_id,
                MAX(player_name) AS display_name,
                MAX(CASE WHEN wasted_bucket = 'FA'
                         THEN calculated_points END) AS fa_wasted_pts,
                MAX(CASE WHEN wasted_bucket = 'ROSTERED_INACTIVE'
                         THEN calculated_points END) AS bench_wasted_pts,
                CAST(SUM(CAST(calculated_points AS DECIMAL(18, 6))) AS FLOAT) AS wasted_points_total,
                MAX(CASE WHEN wasted_bucket = 'ROSTERED_INACTIVE'
                         THEN team_name END) AS bench_team_name
            FROM fct_player_weekly_inactive_performance
            WHERE season_year = %s AND matchup_period = %s
              AND {league_predicate()}
            GROUP BY player_id
        ),
        player_meta AS (
            -- Most-recent (player, scoring_period) row in this matchup_period
            -- for pro_team / position / eligible_slots. Handles mid-period
            -- trades by picking the latest snapshot. eligible_slots is
            -- VARIANT-typed; comes back as a JSON string the formatter
            -- parses with json.loads().
            --
            -- Sourced from fct_player_daily_performance (the v1.1.0 mart-
            -- layer daily contract). DISTINCT in the outer projection
            -- because the fact's grain includes lineup_slot; a player in
            -- multiple slots on the same scoring_period produces duplicate
            -- rows here, but the player-level columns (pro_team, position,
            -- eligible_slots, display_name) are identical across them.
            SELECT DISTINCT player_id, display_name, pro_team, position, eligible_slots
            FROM (
                SELECT player_id, display_name, pro_team, position, eligible_slots,
                       ROW_NUMBER() OVER (
                           PARTITION BY player_id
                           ORDER BY scoring_period DESC
                       ) AS rn
                FROM fct_player_daily_performance
                WHERE season_year = %s AND matchup_period = %s
                  AND {league_predicate()}
            )
            WHERE rn = 1
        ),
        active_points AS (
            SELECT player_id,
                   team_name AS active_team_name,
                   platform_points
            FROM fct_player_weekly_active_performance
            WHERE season_year = %s AND matchup_period = %s
              AND {league_predicate()}
        )
        SELECT
            -- display_name sourced from player_meta (the daily fact's
            -- nickname-resolved value). Fallback to wasted_combined's
            -- player_name handles the rare case where a player has stats
            -- but no box_scores row (shouldn't happen in practice).
            COALESCE(m.display_name, w.display_name) AS display_name,
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
            -- the formatter can surface it in the breakdown as
            -- "negative X.X active". Column kept its original name
            -- for backward compat with row-dict consumers; the
            -- external label changed in Phase 7.
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
      - "X.X wasted pts"  total of all waste components, when 2+
                          non-zero components contributed (unowned,
                          benched, negative-active). The breakdown
                          parenthetical attributes the components.
      - "X.X pts"         single-component case; section header
                          implicitly carries the "wasted" label.

    BREAKDOWN parenthetical lists non-zero of (unowned, benched, negative
    active, active). It appears when there's anything to attribute beyond
    the main line — multiple waste components, or non-zero active context.
    Phase 5 (#5): negative-active production adds to the waste total
    (benching the player for 0 pts would have done strictly better than
    the negative active line). Positive active still appears as context
    (informative: they did contribute on other days) but is NOT added to
    the waste total.
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

        # Headline: total wasted pts when multiple components fired (the
        # breakdown parenthetical attributes the components). Single-
        # component case stays bare since the section header already
        # says "Wasted Performances".
        components = [c for c in (fa_pts, bench_pts, doubly_pts) if c > 0]
        if len(components) > 1:
            total_str = f"{total_pts:.1f} wasted pts"
        else:
            total_str = f"{total_pts:.1f} pts"

        # Breakdown parenthetical
        parts = []
        if fa_pts:     parts.append(f"{fa_pts:.1f} unowned")
        if bench_pts:  parts.append(f"{bench_pts:.1f} benched")
        if doubly_pts: parts.append(f"negative {doubly_pts:.1f} active")
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


# ---------- Records section formatting ----------
# Phase 6.2: data access (get_records / get_player_records / get_stat_polarity
# / find_new_records / count_value_occurrences) moved to output/records.py.
# This section only does formatting now -- it consumes the leaderboard rows
# the records module returns. The team-records pattern also migrated from a
# direct fct_team_weekly_active_performance query to leaderboard rank-1 reads (the
# "migrate team records to leaderboard" backlog item folded in here).


def format_records(records_rows, season_only, schedule_lookup):
    """Format the 6 team score-level records (best/worst total/hitting/
    pitching) from leaderboard rows.

    `records_rows` is the output of records.get_all_time_records() or
    records.get_current_season_records() -- a list of rank-1 rows across
    all grains/stats/directions. We filter to team-grain score columns and
    key by (stat_name, direction).

    season_only=True drops the year from the week label since it's
    implied (current season). False keeps "YYYY Week N" for all-time.
    """
    by_key = {
        (r['stat_name'], r['record_direction']): r
        for r in records_rows
        if r['entity_grain'] == 'team'
        and r['stat_name'] in records.SCORE_STAT_NAMES
    }

    def fmt(row):
        if row is None:
            return None
        # Phase 6.3.3 chunk 6: format_week_label substitutes "Round 1" /
        # "Semi-Finals" / "Finals" for "Week N" on playoff matchup_periods.
        week_label = records.format_week_label(
            row['season_year'], row['matchup_period'], schedule_lookup,
        )
        week_str = week_label if season_only else f"{row['season_year']} {week_label}"
        return (
            f"{row['team_name']} ({row['owner_name']}) -- "
            f"{row['stat_value']:.1f} pts, {week_str}"
        )

    return {
        # Phase 6.3.3: mart's record_direction values are 'most' / 'fewest'
        # (rename from 'best' / 'worst'); the dict keys here keep best/worst
        # semantics because for score columns 'most' pts == "best" outcome.
        'best_total':     fmt(by_key.get(('CALCULATED_POINTS',         'most'))),
        'best_hitting':   fmt(by_key.get(('CALCULATED_HITTING_PTS',    'most'))),
        'best_pitching':  fmt(by_key.get(('CALCULATED_PITCHING_PTS',   'most'))),
        'worst_total':    fmt(by_key.get(('CALCULATED_POINTS',         'fewest'))),
        'worst_hitting':  fmt(by_key.get(('CALCULATED_HITTING_PTS',    'fewest'))),
        'worst_pitching': fmt(by_key.get(('CALCULATED_PITCHING_PTS',   'fewest'))),
    }


def format_player_records(records_rows, season_only, schedule_lookup):
    """Format the 3 player records (Top Scorer / Hitter / Pitcher) from
    leaderboard rows. Filter to player grain + best direction + score
    columns (player records are best-only per the polarity filter).

    Returns a dict with keys 'top_scorer', 'top_hitter', 'top_pitcher';
    each value is a formatted string or None when no rank-1 row exists.
    """
    by_stat = {
        r['stat_name']: r
        for r in records_rows
        if r['entity_grain'] == 'player'
        and r['record_direction'] == 'most'
        and r['stat_name'] in records.SCORE_STAT_NAMES
    }

    def fmt(row):
        if row is None:
            return None
        # Phase 6.3.3 chunk 6: playoff round names sub for "Week N".
        week_label = records.format_week_label(
            row['season_year'], row['matchup_period'], schedule_lookup,
        )
        week_str = week_label if season_only else f"{row['season_year']} {week_label}"
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
# Detection + filter rules + tie counting moved to output/records.py in
# Phase 6.2. This section now only formats the records that records.py
# returns.


# Phase 6.3.3 chunk 6.6: ordinal() moved to records.py so league_notes
# can import it without dragging this script in. Re-exported here under
# the same name for backward compatibility with anything in this file
# that still calls ordinal directly.
from records import ordinal


def fmt_stat_value_with_unit(stat_name, value):
    """Stat value with unit suffix for inline display.
    Score columns: '37.3 pts'. OUTS: '8.0 IP'. Other counting stats: '5 HR'.
    """
    if stat_name in records.SCORE_STAT_NAMES:
        return f"{value:.1f} pts"
    if stat_name == 'OUTS':
        return f"{fmt_ip(value)} IP"
    return f"{fmt_value(value)} {stat_catalog.get_abbrev_map().get(stat_name, stat_name)}"


def make_record_label(grain, stat_name, direction):
    """Bolded heading text for a new-record block (Phase 5 design):
      Score columns -> '{Best|Worst} {Player|Team} {Total|Hitting|Pitching} Points'
      Individual stats -> '{Most|Fewest} {Display Name}'
    """
    # Phase 6.3.3: mart direction values are 'most' / 'fewest' now.
    # 'most' -> "Best" for score columns (more pts = better), "Most" for
    # individual stats. 'fewest' -> "Worst" / "Fewest" symmetrically.
    if stat_name in records.SCORE_STAT_NAMES:
        prefix = 'Best' if direction == 'most' else 'Worst'
        scope = 'Player' if grain == 'player' else 'Team'
        return f"{prefix} {scope} {stat_catalog.get_display_map()[stat_name]}"
    prefix = 'Most' if direction == 'most' else 'Fewest'
    return f"{prefix} {stat_catalog.get_display_map().get(stat_name, stat_name)}"


def _recap_record_label(grain, stat_name, direction):
    """Recap variant of make_record_label that drops an all-caps abbreviation
    suffix -- ' (SV-BLSV)', ' (W-L)', ' (SB-CS)', ' (SHO)' -- for a cleaner
    recap line, while keeping lowercase disambiguators like ' (Batter)' /
    ' (Pitcher)' (dropping those would collide batter vs pitcher records)."""
    label = make_record_label(grain, stat_name, direction)
    if label.endswith(')'):
        base, sep, paren = label.rpartition(' (')
        if sep and not any(c.islower() for c in paren[:-1]):
            return base
    return label


def _team_contributors(players, team_id, stat_col):
    """Build contributor list for a team's record-setting MP. Each entry
    is {display_name, stat_value} for format_contributors() consumption."""
    return [
        {'display_name': p['display_name'], 'stat_value': p.get(stat_col)}
        for p in players if p['team_id'] == team_id
    ]


def _format_player_score_record(rec, players, schedule_lookup):
    """Player-grain record line + prior + inline stat line via #2 formatter."""
    new = rec['new']
    label = _recap_record_label(rec['grain'], rec['stat_name'], rec['direction'])
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
        f"[b]{label}[/b]: {new['display_name']} ({new['team_abbrev']}), "
        f"{new_value:.1f} pts"
    )
    if stat_line:
        new_line += f" -- {stat_line}"

    prior = rec['prior']
    if prior:
        prior_week = records.format_week_label(
            prior['season_year'], prior['matchup_period'], schedule_lookup,
        )
        new_line += (
            f" (Prior: {prior['stat_value']:.1f} pts by "
            f"{prior['display_name']} ({prior['team_abbrev']}) "
            f"in {prior_week} of {prior['season_year']})"
        )
    return [new_line]


def _format_team_record(rec, players, schedule_lookup):
    """Team-grain record block. Score column or individual stat both use
    this shape; contributor list only renders for 'most' direction (per
    user spec: worsts don't get contributors yet).
    """
    new = rec['new']
    label = _recap_record_label(rec['grain'], rec['stat_name'], rec['direction'])
    new_value_str = fmt_stat_value_with_unit(rec['stat_name'], new['stat_value'])

    new_line = f"[b]{label}[/b]: {new['team_name']}, {new_value_str}"

    prior = rec['prior']
    if prior:
        prior_value_str = fmt_stat_value_with_unit(rec['stat_name'], prior['stat_value'])
        prior_week = records.format_week_label(
            prior['season_year'], prior['matchup_period'], schedule_lookup,
        )
        new_line += (
            f" (Prior: {prior_value_str} by "
            f"{prior['owner_name']} ({prior['team_abbrev']}) "
            f"in {prior_week} of {prior['season_year']})"
        )

    lines = [new_line]

    if rec['direction'] == 'most':
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
    label = _recap_record_label(rec['grain'], rec['stat_name'], rec['direction'])
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


def _join_names(names):
    """'A' / 'A and B' / 'A, B, and C'."""
    names = [n for n in names if n]
    if len(names) <= 1:
        return names[0] if names else ''
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{', '.join(names[:-1])}, and {names[-1]}"


def _format_shared_new_record(rec):
    """A brand-new record set by 2+ entities in the SAME week -- new, but
    shared. Framed as a record (first in league history), naming the
    simultaneous setters, rather than 'the Nth to do so.'"""
    label = _recap_record_label(rec['grain'], rec['stat_name'], rec['direction'])
    value_str = fmt_stat_value_with_unit(rec['stat_name'], rec['new']['stat_value'])
    holders = rec.get('fresh_holders') or [rec['new']]
    if rec['grain'] == 'team':
        names = [h.get('team_name') or h.get('team_abbrev') or '' for h in holders]
    else:
        names = [
            f"{h.get('display_name') or h.get('player_name')} ({h.get('team_abbrev')})"
            for h in holders
        ]
    return [
        f"[b]New Record for {label}[/b]: {_join_names(names)} at {value_str} "
        f"-- first in league history."
    ]


def format_new_records_section(records, players, schedule_lookup):
    """Full New Records section. Returns list of lines, OR an empty list
    when no records were broken/tied (the section is skipped entirely)."""
    if not records:
        return []

    lines = ["", "[u][b]New Records[/b][/u]"]
    broken = [rec for rec in records if not rec['is_tie']]
    # A tie whose value was never reached before this week is a brand-new
    # record that N entities set at once (e.g. the first-ever cycle, by two
    # teams) -- it belongs with the new records, not the "matched a standing
    # mark" ties.
    shared_new = [rec for rec in records if rec['is_tie'] and rec.get('is_new_record')]
    ties = [rec for rec in records if rec['is_tie'] and not rec.get('is_new_record')]
    for rec in broken:
        if rec['grain'] == 'player':
            lines.extend(_format_player_score_record(rec, players, schedule_lookup))
        else:
            lines.extend(_format_team_record(rec, players, schedule_lookup))
    for rec in shared_new:
        lines.extend(_format_shared_new_record(rec))
    for rec in ties:
        lines.extend(_format_tied_record(rec))
    return lines


def generate_summary(matchup_period, scores, contributions, wasted_points,
                     season_records, alltime_records,
                     season_player_records, alltime_player_records,
                     players, new_records, schedule_lookup, active_season):
    """Build the BBCode-formatted front-page summary."""

    best_overall = scores[0]
    worst_overall = scores[-1]

    by_hitting = sorted(scores, key=lambda x: x['calculated_hitting_pts'], reverse=True)
    best_hitting = by_hitting[0]
    worst_hitting = by_hitting[-1]

    by_pitching = sorted(scores, key=lambda x: x['calculated_pitching_pts'], reverse=True)
    best_pitching = by_pitching[0]
    worst_pitching = by_pitching[-1]

    def fmt_players(player_list, score_key='calculated_points'):
        return ", ".join(
            f"{p['display_name']}: {p[score_key]:.1f}"
            for p in player_list
        )

    # Phase 6.3.3 chunk 6: active-week heading uses format_week_label so
    # playoff weeks render as "Round 1 Recap" / "Semi-Finals Recap" /
    # "Finals Recap" instead of "Week 24 Recap".
    active_week_label = records.format_week_label(
        active_season, matchup_period, schedule_lookup,
    )
    lines = [
        # Optional commissioner header note -- verbatim first lines of
        # every summary; contributes nothing when the file is blank or
        # missing (so goldens hold until one is written).
        *note_files.header_lines(),
        f"[u][b]{active_week_label} Recap[/b][/u]",
        f"",
    ]

    # v1.x: League This Week line. Always-on collective lens at the
    # top of the recap so the per-team superlatives below have a
    # frame of reference. Skipped silently if the mart has no row
    # (defensive only).
    league_line = format_league_this_week(active_season, matchup_period)
    if league_line:
        lines.extend([league_line, ""])

    lines.extend([
        f"[b]Best Overall[/b]: {best_overall['calculated_points']:.1f} pts by {best_overall['team_name']}",
        f"{fmt_players(contributions['top_overall'])}",
        f"[b]Best Hitting[/b]: {best_hitting['calculated_hitting_pts']:.1f} pts by {best_hitting['team_name']}",
        f"{fmt_players(contributions['top_hitters'], 'calculated_hitting_pts')}",
        f"[b]Best Pitching[/b]: {best_pitching['calculated_pitching_pts']:.1f} pts by {best_pitching['team_name']}",
        f"{fmt_players(contributions['top_pitchers'], 'calculated_pitching_pts')}",
        f"",
        f"[b]Worst Overall[/b]: {worst_overall['calculated_points']:.1f} pts by {worst_overall['team_name']}",
        f"[b]Worst Hitting[/b]: {worst_hitting['calculated_hitting_pts']:.1f} pts by {worst_hitting['team_name']}",
        f"[b]Worst Pitching[/b]: {worst_pitching['calculated_pitching_pts']:.1f} pts by {worst_pitching['team_name']}",
    ])

    # Player-level superlatives across the whole league (top scorer / top hitter
    # / top pitcher by calculated_points / calculated_hitting_pts / calculated_pitching_pts
    # respectively). Stashed in the contributions dict by get_contribution_callouts.
    # v1.x: Top Scorer renders only on two-way weeks (player with non-zero
    # hitting AND pitching contributions); otherwise it duplicates Top
    # Hitter or Top Pitcher. See comment near find_top_scorer.
    top_scorer = contributions.get('top_scorer')
    top_hitter = contributions.get('top_hitter')
    top_pitcher = contributions.get('top_pitcher')
    top_scorer_is_two_way = (
        top_scorer is not None
        and (top_scorer.get('calculated_hitting_pts') or 0) > 0
        and (top_scorer.get('calculated_pitching_pts') or 0) > 0
    )
    if top_scorer_is_two_way or top_hitter or top_pitcher:
        lines.append("")
    if top_scorer_is_two_way:
        lines.append(f"[b]Top Scorer[/b]: {format_top_scorer_line(top_scorer)}")
    if top_hitter:
        lines.append(f"[b]Top Hitter[/b]: {format_hitter_line(top_hitter)}")
    if top_pitcher:
        lines.append(f"[b]Top Pitcher[/b]: {format_pitcher_line(top_pitcher)}")

    # Top Wasted Performances (Phase 4) — last item in the matchup recap.
    lines.extend(format_wasted_points(wasted_points))

    # New Records (Phase 5 #3) -- skipped entirely when no records were broken
    lines.extend(format_new_records_section(new_records, players, schedule_lookup))

    # League-flavor callouts. Each is a function in output/league_notes.py
    # that returns 0+ BBCode lines. Section is skipped entirely when no
    # rules fired. Includes the matchup-outcome callouts (Tough Luck /
    # Lucky Bastard / Fair-and-Just) that used to render inline here --
    # v1.x migrated them into the registry so all conditional flavor
    # callouts live in one place.
    import league_notes
    callout_ctx = league_notes.build_ctx(
        active_season, matchup_period, schedule_lookup,
    )
    callout_lines = league_notes.render_callouts(callout_ctx)
    if callout_lines:
        lines.append("")
        lines.extend(callout_lines)

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

    # Optional commissioner footer note -- verbatim last lines of every
    # summary (LeagueNote.txt / Additional Notes keeps its locked
    # behavior above; the footer is a separate, also-optional file).
    lines.extend(note_files.footer_lines())

    # Write to timestamped log file
    from datetime import datetime
    log_dir = os.path.join(os.path.dirname(__file__), "..", "output","logs")
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    log_path = os.path.join(log_dir, f"summary_{db.league_file_tag()}{matchup_period}_{timestamp}.txt")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\nLog saved to: {log_path}")

    return "\n".join(lines)

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate the weekly BBCode front-page summary."
    )
    parser.add_argument(
        "--league", default=None, metavar="LEAGUE_KEY",
        help="League registry key to render (config/leagues.yml). "
             "Default: the registry's default_league (the ESPN league).",
    )
    args = parser.parse_args()
    db.set_league(args.league)
    # MLB-58: BBCode is a per-league surface. A league that declares
    # sinks.bbcode: false (or nothing) has no recap to generate -- fail
    # loudly before any query runs.
    if not db.league().sinks.get('bbcode', False):
        raise SystemExit(
            f"League '{db.league_key()}' has no BBCode surface configured "
            f"(sinks.bbcode in config/leagues.yml); nothing to generate."
        )

    active_season = query_snowflake(
        f"SELECT MAX(season_year) as sy FROM fct_team_weekly_active_performance"
        f" WHERE {league_predicate()}"
    )[0]['sy']

    matchup_period, scores = get_weekly_scores(active_season)
    players        = get_player_contributions(active_season, matchup_period)
    contributions  = get_contribution_callouts(scores, players)
    wasted_points  = get_wasted_points(active_season, matchup_period)

    # Phase 6.3.3 chunk 6: load matchup_schedule once and pass the
    # lookup through every formatter that builds week labels. Cheap
    # alternative to re-querying for each record formatted.
    schedule_lookup = records.load_schedule_lookup()

    # Phase 6.2: records data access consolidated in output/records.py.
    # Each scope is a single leaderboard query returning all rank-1 rows
    # across grains, stats, and directions; the format_* functions filter
    # to what each section displays.
    new_records         = records.get_records_set_this_week(active_season, matchup_period)
    all_time_rows       = records.get_all_time_records()
    current_season_rows = records.get_current_season_records()

    season_records         = format_records(current_season_rows, season_only=True,  schedule_lookup=schedule_lookup)
    alltime_records        = format_records(all_time_rows,       season_only=False, schedule_lookup=schedule_lookup)
    season_player_records  = format_player_records(current_season_rows, season_only=True,  schedule_lookup=schedule_lookup)
    alltime_player_records = format_player_records(all_time_rows,       season_only=False, schedule_lookup=schedule_lookup)

    summary = generate_summary(matchup_period, scores, contributions,
                               wasted_points, season_records, alltime_records,
                               season_player_records, alltime_player_records,
                               players, new_records, schedule_lookup, active_season)
    print(summary)