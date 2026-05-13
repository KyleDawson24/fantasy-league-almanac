"""
output/league_notes.py

League-flavor "color" callouts for the weekly summary. Same shape as
the existing find_tough_luck / check_fair_and_just inline functions in
generate_summary.py, but factored into its own module so the rule list
is easy to read, edit, and extend without touching the main script.

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
                                fct_weekly_team_performance, plus team_abbrev,
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
    no_hitters,
    zero_steals,
    hr_drought,
    # ... add 5-10 of your own here
]


def render_callouts(ctx):
    """Returns a list of fired callout lines. Empty when no rules fired
    this week. Caller (generate_summary.py) wraps the section header
    around this output and skips the section entirely if the list is
    empty."""
    lines = []
    for fn in CALLOUTS:
        try:
            lines.extend(fn(ctx))
        except Exception as e:
            # A buggy callout shouldn't kill the weekly recap. Log and
            # continue so the rest of the rules still fire.
            print(f"[league_notes] callout {fn.__name__} crashed: {e}")
    return lines


def build_ctx(season_year, matchup_period, schedule_lookup):
    """Assemble the ctx dict consumed by every callout. One Snowflake
    call per grain (team-week + active-player-week wide rows)."""
    scores = records.query_snowflake("""
        SELECT *
        FROM fct_weekly_team_performance
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
