"""
output/league_notes.py

League-flavor "color" callouts for the weekly summary. The single home
for conditional fun-facts the recap surfaces alongside the structural
sections (matchup recap, new records, season+all-time records). Each
callout is a function returning 0+ BBCode lines; the registry below
controls render order and lets the rule list grow without touching
generate_summary.py.

How to add a callout
--------------------
1. Define a function that takes `ctx` (the context dict assembled by
   build_ctx below) and returns a LIST of BBCode-ready lines (empty list
   if the rule didn't fire, length-N for N matching occurrences).
2. Append your function to the CALLOUTS list at the bottom of this file.
   Comment out lines you want to disable without deleting them.

Patterns
--------
Three template patterns covered by the examples below; copy whichever
fits your rule:

  Pattern 1 (regular occurrence, single inline line):
    Multiple matching teams collapsed into one line with comma-joined
    abbrevs and an ordinal range. See `zero_steals` below.

  Pattern 2 (rare occurrence, multi-line):
    One line per matching player/team. See `no_hitters` below. Useful
    for events that should each get their own announcement (e.g., two
    no-hitters in one week == two lines).

  Pattern 3 (regular occurrence, varied template):
    Random-pick from a small list of phrasings to keep weekly recaps
    from feeling repetitive. See `hr_drought` below.

Available helpers (import from records / formatters as needed)
--------------------------------------------------------------
  records.ordinal(n)
      1 -> '1st', 2 -> '2nd', 11 -> '11th', etc.

  records.league_history_count(grain, stat_name, value, op='=')
      Count of entity-weeks where `stat_name` compares with `value` per
      `op` (=, !=, <, <=, >, >=) across all-time fct rows. Excludes
      abnormal weeks. Returns None for stats with no fct counterpart
      (rate stats / WASTED_POINTS).

  records.format_week_label(season_year, matchup_period, ctx['schedule_lookup'])
      'Week N' for regular weeks, playoff round name for playoffs.

  formatters.fmt_ip(outs)
      Convert outs to baseball IP notation (51 outs -> '17.0').

  formatters.fmt_value(v)
      Int-or-1-decimal float formatter.

The ctx dict
------------
Built by build_ctx() and passed into every callout. Keys:
  ctx['season_year']     int    Active season
  ctx['matchup_period']  int    Active matchup period
  ctx['scope_label']     str    "Week 5" or "Round 1" (already format_week_label-d)
  ctx['scores']          list   One row per team this week. Wide -- has every
                                counting and pts column from
                                fct_weekly_team_active_performance, plus team_abbrev,
                                team_name, owner_name, opponent_*, etc.
  ctx['players']         list   One row per active player this week (slot not in
                                BE/IL/FA). Wide -- every counting and pts column
                                from fct_weekly_player_active_performance plus
                                display_name, team_abbrev, etc.
  ctx['schedule_lookup'] dict   Pass to format_week_label if needed.
"""
import random

import records
from formatters import fmt_ip, fmt_value


# ============================================================
# Matchup-outcome callouts (W/L lens; second-place / second-worst / sweep)
# ============================================================

def tough_luck(ctx):
    """Second-highest-scoring team in the league lost. The 'unlucky' end
    of the high-score-but-lost spectrum."""
    ranked = sorted(ctx['scores'], key=lambda x: x['platform_points'], reverse=True)
    if len(ranked) < 2:
        return []
    second_place = ranked[1]
    if second_place['result'] != 'L':
        return []
    return [
        f"[b]Tough Luck[/b]: {second_place['team_name']} scored "
        f"{second_place['platform_points']:.1f} pts, second most in the "
        f"league, but lost to {second_place['opponent_name']}'s "
        f"{second_place['opponent_points']:.1f}"
    ]


def lucky_bastard(ctx):
    """Second-lowest-scoring team won. The 'lucky' end of the
    low-score-but-won spectrum."""
    ranked = sorted(ctx['scores'], key=lambda x: x['platform_points'], reverse=True)
    if len(ranked) < 2:
        return []
    second_worst = ranked[-2]
    if second_worst['result'] != 'W':
        return []
    return [
        f"[b]Lucky Bastard[/b]: {second_worst['team_name']} scored just "
        f"{second_worst['platform_points']:.1f} pts, second worst in the "
        f"league, but beat {second_worst['opponent_name']}'s "
        f"{second_worst['opponent_points']:.1f}"
    ]


def fair_and_just(ctx):
    """Top half of the scoring all won AND bottom half all lost --
    every score-rank-vs-result pair lined up the 'fair' way."""
    ranked = sorted(ctx['scores'], key=lambda x: x['platform_points'], reverse=True)
    num_matchups = len([s for s in ctx['scores'] if s.get('opponent_name') is not None]) // 2
    if num_matchups == 0:
        return []
    for i, team in enumerate(ranked):
        if team['result'] is None:
            return []  # bye-week team breaks the rule
        if i < num_matchups and team['result'] != 'W':
            return []
        if i >= num_matchups and team['result'] != 'L':
            return []
    return [
        f"[b]A FAIR AND JUST LEAGUE![/b] The top {num_matchups} scoring "
        f"teams all won this week, and the bottom {num_matchups} all lost."
    ]


# ============================================================
# Pattern 1: regular occurrence, list inline
# ============================================================

def zero_steals(ctx):
    """Teams that put up 0 SB this week. One line listing all teams +
    their cumulative ordinal range in league history."""
    teams = [s for s in ctx['scores'] if (s.get('sb') or 0) == 0]
    if not teams:
        return []
    abbrevs = ", ".join(t['team_abbrev'] for t in teams)

    total_through_now = records.league_history_count('team', 'SB', 0, op='=')
    if total_through_now is None:
        # Stat has no fct counterpart; degrade gracefully without ordinals.
        return [f"[b]Bases Untouched:[/b] {abbrevs} put up 0 stolen bases this week."]

    end = total_through_now
    start = end - len(teams) + 1
    range_str = records.ordinal(end) if start == end else (
        f"{records.ordinal(start)}-{records.ordinal(end)}"
    )
    plural = 's' if len(teams) > 1 else ''
    return [
        f"[b]Bases Untouched:[/b] {abbrevs} gave us our "
        f"{range_str} zero-steal team-week{plural}."
    ]


# ============================================================
# Pattern 2: rare occurrence, multi-line
# ============================================================

def no_hitters(ctx):
    """One line per pitcher who threw an NH this week. Cites the
    cumulative league-history NH count so each gets its own ordinal."""
    pitchers = [p for p in ctx['players'] if (p.get('nh') or 0) > 0]
    if not pitchers:
        return []

    total_through_now = records.league_history_count(
        'player', 'NH', 1, op='>=',
    )
    if total_through_now is None:
        n = None
    else:
        n = total_through_now - len(pitchers) + 1

    out = []
    for p in sorted(pitchers, key=lambda p: p['display_name']):
        ip      = fmt_ip(p.get('outs') or 0)
        ks      = int(p.get('k')   or 0)
        bbs     = int(p.get('p_bb') or 0)
        ordinal_str = (
            f"our {records.ordinal(n)} no-hitter in league history"
            if n is not None else 'a no-hitter'
        )
        out.append(
            f"[b]No-Hitter![/b] {p['display_name']} ({p['team_abbrev']}) "
            f"tossed {ordinal_str}. "
            f"Line: {ip} IP, {ks} K, {bbs} BB."
        )
        if n is not None:
            n += 1
    return out


def cycles(ctx):
    """One line per player who hit for the cycle this week. Same shape
    as no_hitters: rare-event with cumulative league-history ordinal.
    Adds a 'first of season' note when this week's cycle is the first
    of the current season."""
    hitters = [p for p in ctx['players'] if (p.get('cyc') or 0) > 0]
    if not hitters:
        return []

    cumulative = records.league_history_count(
        'player', 'CYC', 1, op='>=',
    )
    season_total = records.query_snowflake("""
        SELECT COALESCE(SUM(cyc), 0) AS n
        FROM fct_weekly_player_active_performance
        WHERE season_year = %s
    """, (ctx['season_year'],))[0]['n']

    if cumulative is None:
        next_ord = None
    else:
        next_ord = cumulative - len(hitters) + 1

    # season_total counts cycles through this matchup (inclusive); the
    # first cycle of the season is the one where season_total - i + 1 == 1.
    season_count_after_this_week = season_total
    season_starts_at = season_count_after_this_week - len(hitters) + 1

    out = []
    for i, p in enumerate(sorted(hitters, key=lambda p: p['display_name'])):
        hits  = int(p.get('h')   or 0)
        hr    = int(p.get('hr')  or 0)
        rbi   = int(p.get('rbi') or 0)
        if next_ord is not None:
            ordinal_str = f"the {records.ordinal(next_ord)} cycle in league history"
            next_ord += 1
        else:
            ordinal_str = "a cycle"
        line = (
            f"[b]Cycle![/b] {p['display_name']} ({p['team_abbrev']}) "
            f"hit for {ordinal_str}. Line: {hits} H, {hr} HR, {rbi} RBI."
        )
        # First cycle of the season gets an extra flourish.
        if season_starts_at + i == 1:
            line = line + " The first of the season!"
        out.append(line)
    return out


def clean_slate(ctx):
    """Teams where every active-slot player-day in the MP had
    platform_points >= 0 — among players who appeared in at least one
    real-world game. Functionally equivalent to fct.negative_points = 0
    at the team-MP rollup, with a "fielded > 0 players" guard so an
    all-rest-day team doesn't qualify vacuously.

    Frequency context: zero hits in 454 historical team-MPs at probe
    time, so each fire is genuinely first-time-ever (or nearly so).
    Worth a per-team line."""
    candidates = [
        s for s in ctx['scores'] if (s.get('negative_points') or 0) == 0
    ]
    if not candidates:
        return []

    # Per-team distinct-player count restricted to days the player
    # actually appeared in a game. games_played lives only on
    # int_player_daily; the team/player active facts don't carry it.
    fielded_rows = records.query_snowflake("""
        SELECT team_id,
               COUNT(DISTINCT player_id) AS fielded_players
        FROM int_player_daily
        WHERE season_year = %s
          AND matchup_period = %s
          AND is_active_slot = true
          AND games_played >= 1
        GROUP BY team_id
    """, (ctx['season_year'], ctx['matchup_period']))
    fielded_by_team = {r['team_id']: r['fielded_players'] for r in fielded_rows}

    qualifying = [
        c for c in candidates if fielded_by_team.get(c['team_id'], 0) > 0
    ]
    if not qualifying:
        return []

    # League-history count of qualifying team-MPs through the current
    # MP. Mirrors the trigger criteria exactly (neg_days = 0 AND fielded
    # > 0) so the count and the trigger never disagree. Abnormal MPs
    # excluded to match records.league_history_count's convention.
    hist = records.query_snowflake("""
        WITH team_mp AS (
          SELECT
            d.season_year,
            d.matchup_period,
            d.team_id,
            SUM(CASE WHEN d.platform_points < 0 THEN 1 ELSE 0 END) AS neg_days,
            COUNT(DISTINCT CASE WHEN d.games_played >= 1 THEN d.player_id END) AS fielded
          FROM int_player_daily d
          JOIN matchup_schedule s
            ON d.season_year = s.season_year
           AND d.matchup_period = s.matchup_period
          WHERE s.is_abnormal = false
            AND d.is_active_slot = true
          GROUP BY 1, 2, 3
        )
        SELECT COUNT(*) AS n
        FROM team_mp
        WHERE neg_days = 0 AND fielded > 0
    """)
    n_total = hist[0]['n'] if hist else len(qualifying)

    qualifying.sort(key=lambda s: s.get('team_abbrev') or '')
    plural = '' if n_total == 1 else 's'
    return [
        f"[b]I'm Doing My Part![/b] {s['team_name']} fielded "
        f"{fielded_by_team[s['team_id']]} players, and not a one of those "
        f"patriots sabotaged the cause -- each put up at least 0 points. "
        f"This has happened {n_total} time{plural} in league history."
        for s in qualifying
    ]


# ============================================================
# League-wide benchmark callouts (mart_league_weekly_benchmarks)
# ============================================================

# Threshold bands for hot/cold callouts. Percentile of the week's
# league-mean within league history (PERCENT_RANK so 0..1, where 1.0 =
# highest ever). Tiered framing: extreme (== 1.0 / == 0.0) gets the
# "highest/lowest in league history" superlative; otherwise the
# top/bottom 15% bucket fires with an Nth-percentile rendering.
_HOT_PCTILE_THRESHOLD  = 0.85
_COLD_PCTILE_THRESHOLD = 0.15

# Per-lens phrasing. Each entry: (mart column prefix, hot lead-in,
# cold lead-in, lens label for "in league history" suffix). The
# function below picks the appropriate template per fired lens.
_BENCHMARK_LENSES = [
    ('calculated_points',
     'Hot Week',  'Slow Week',          'calculated points'),
    ('calculated_hitting_pts',
     'Bats Were Loud', 'Bats Were Quiet', 'hitting points'),
    ('calculated_pitching_pts',
     'Arms Day',  'Rough Day for Arms', 'pitching points'),
]


def league_benchmarks(ctx):
    """Collective-mood callouts driven by mart_league_weekly_benchmarks.
    Emits 0-3 lines depending on whether the current MP's league means
    sit in the extreme percentile bands for overall / hitting / pitching.

    Renders three tiers per lens:
      - pctile == 1.0  -> "highest in league history"
      - pctile >= 0.85 -> "<N>th percentile in league history"
      - pctile == 0.0  -> "lowest in league history"
      - pctile <= 0.15 -> "<N>th percentile in league history"
      - middling       -> silent (no line)
    """
    rows = records.query_snowflake("""
        SELECT calculated_points_mean,         calculated_points_pctile,
               calculated_hitting_pts_mean,    calculated_hitting_pts_pctile,
               calculated_pitching_pts_mean,   calculated_pitching_pts_pctile
        FROM mart_league_weekly_benchmarks
        WHERE season_year = %s AND matchup_period = %s
    """, (ctx['season_year'], ctx['matchup_period']))
    if not rows:
        return []
    bench = rows[0]

    out = []
    for prefix, hot_lead, cold_lead, lens_label in _BENCHMARK_LENSES:
        mean   = bench.get(f'{prefix}_mean')
        pctile = bench.get(f'{prefix}_pctile')
        if mean is None or pctile is None:
            continue

        # Tier the framing.
        if pctile >= _HOT_PCTILE_THRESHOLD:
            lead = hot_lead
            if pctile == 1.0:
                rank_str = f'highest in league history'
            else:
                rank_str = f'{int(round(pctile * 100))}th percentile in league history'
            out.append(
                f"[b]{lead}:[/b] League averaged {mean} {lens_label} "
                f"this week -- {rank_str}."
            )
        elif pctile <= _COLD_PCTILE_THRESHOLD:
            lead = cold_lead
            if pctile == 0.0:
                rank_str = f'lowest in league history'
            else:
                rank_str = f'{int(round(pctile * 100))}th percentile in league history'
            out.append(
                f"[b]{lead}:[/b] League averaged {mean} {lens_label} "
                f"this week -- {rank_str}."
            )
    return out


# ============================================================
# Pattern 3: regular occurrence, varied template
# ============================================================

_HR_DROUGHT_TEMPLATES = [
    "[b]Power Outage:[/b] {team} swung hard, connected on nothing. 0 HR this week.",
    "[b]Where's the Beef?[/b] {team}'s lineup mustered 0 HR this week.",
    "[b]Pitcher's Friend:[/b] {team} couldn't push one over the fence. 0 HR.",
]


def hr_drought(ctx):
    """Teams with 0 HR this week, each with a randomly-picked phrasing."""
    teams = [s for s in ctx['scores'] if (s.get('hr') or 0) == 0]
    return [
        random.choice(_HR_DROUGHT_TEMPLATES).format(team=t['team_name'])
        for t in teams
    ]


# ============================================================
# Registry: order matters. Top of list prints first in the recap.
# Comment out a line to disable that callout without deleting code.
# ============================================================

CALLOUTS = [
    # Matchup-outcome callouts first -- they're the most structurally
    # tied to the W/L results and read first in the recap by convention.
    tough_luck,
    lucky_bastard,
    fair_and_just,
    # League-wide collective-mood callouts (top/bottom percentile bands
    # via mart_league_weekly_benchmarks).
    league_benchmarks,
    # Rare events
    no_hitters,
    cycles,
    clean_slate,
    # Recurring oddities
    zero_steals,
    hr_drought,
    # ... add 5-10 of your own here
]


def render_callouts(ctx):
    """Returns a list of fired callout lines, with a single blank-line
    separator between callouts that fire. Empty when no rules fired this
    week. Caller (generate_summary.py) wraps the section break around
    this output and skips the section entirely if the list is empty."""
    lines = []
    for fn in CALLOUTS:
        try:
            new_lines = fn(ctx)
        except Exception as e:
            # A buggy callout shouldn't kill the weekly recap. Log and
            # continue so the rest of the rules still fire.
            print(f"[league_notes] callout {fn.__name__} crashed: {e}")
            continue
        if not new_lines:
            continue
        if lines:
            lines.append("")
        lines.extend(new_lines)
    return lines


def build_ctx(season_year, matchup_period, schedule_lookup):
    """Assemble the ctx dict consumed by every callout. One Snowflake
    call per grain (team-week + active-player-week wide rows)."""
    scores = records.query_snowflake("""
        SELECT *
        FROM fct_weekly_team_active_performance
        WHERE season_year = %s AND matchup_period = %s
    """, (season_year, matchup_period))

    # Active players only (slot != BE/IL/FA). Mirrors fct_weekly_player_active_performance's
    # active-only filter applied during the int -> fct rollup, so this
    # query returns the same player set generate_summary already uses for
    # top-N callouts.
    players = records.query_snowflake("""
        SELECT *
        FROM fct_weekly_player_active_performance
        WHERE season_year = %s AND matchup_period = %s
    """, (season_year, matchup_period))

    return {
        'season_year':     season_year,
        'matchup_period':  matchup_period,
        'scope_label':     records.format_week_label(
                                season_year, matchup_period, schedule_lookup),
        'scores':          scores,
        'players':         players,
        'schedule_lookup': schedule_lookup,
    }
