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


def mismatch(ctx):
    """The league's top-scoring team was matched up against the league's
    bottom-scoring team. Pattern 2 (max 1 line per MP -- only one top
    and one bottom). Ordinal references the margin's rank among all
    non-abnormal matchups in league history (descending).
    """
    scored = [s for s in ctx['scores']
              if s.get('platform_points') is not None
              and s.get('opponent_id') is not None]
    if len(scored) < 2:
        return []
    ranked = sorted(scored, key=lambda s: s['platform_points'], reverse=True)
    top, bottom = ranked[0], ranked[-1]
    if top['opponent_id'] != bottom['team_id']:
        return []
    margin = top['platform_points'] - bottom['platform_points']
    # team_id < opponent_id dedupes matchup pairings (each appears twice
    # in the fct, once per team).
    hist = records.query_snowflake("""
        WITH all_margins AS (
          SELECT ABS(t.platform_points - t.opponent_points) AS m
          FROM fct_weekly_team_active_performance t
          JOIN matchup_schedule s
            ON t.season_year = s.season_year
           AND t.matchup_period = s.matchup_period
          WHERE s.is_abnormal = false
            AND t.opponent_id IS NOT NULL
            AND t.team_id < t.opponent_id
        )
        SELECT
          SUM(CASE WHEN m >= %s THEN 1 ELSE 0 END) AS rank_desc,
          COUNT(*) AS total
        FROM all_margins
    """, (margin,))[0]
    return [
        f"[b]Mismatch![/b] Our golden boys on {top['team_name']} led the "
        f"league with {top['platform_points']:.1f} points while the "
        f"shameful scum on {bottom['team_name']} put up "
        f"{bottom['platform_points']:.1f}, worst in the league. Wouldn't "
        f"ya know it, they did so in the same matchup? The {margin:.1f} "
        f"margin is the {records.ordinal(hist['rank_desc'])} largest in "
        f"the {hist['total']} matchups in the history of our beautiful "
        f"league."
    ]

_HERO_TEMPLATES = [
    "[b]Hero:[/b] {team_name} beat {opp_name} "
    "({opp_pts:.1f}) by {margin:.1f}, because {hero_name} went off for {hero_pts:.1f} -- {gap:.1f} more than {team_name}'s #2 scorer. "
    "Baseball is a game of inches and {team_name} needed every inch of {hero_name}'s swinging hog to win this week.",

    "[b]Hero:[/b] {hero_name} put up {hero_pts:.1f} for {team_name} this week, "
    "who only beat {opp_name} by {margin:.1f}. "
    "'Oh so surely {team_name} would have still won even if {hero_name} had been average' you might say "
    "but that's a stupid thing to have thought and the science refutes you, fool! {team_name}'s second best player only produced {second_pts:.1f} points! "
    "had {hero_name} only done what his team's number two had managed, {team_name} would have lost, and we'd all be speaking German!",

    "[b]Hero:[/b] {hero_name} put up {hero_pts:.1f} for {team_name} this week, {gap:.1f} more than {team_name}'s #2 scorer "
    "'kind of an arbitrary thing to call out' you might find yourself thinking, especially if you're a stupid guy'; "
    "{team_name} only beat {opp_name} by {margin:.1f}; if {hero_name} had merely been excellent instead of a star, his guys would have lost",

    "[b]Hero:[/b] {hero_name} put up {hero_pts:.1f} for {team_name} this week, {gap:.1f} more than {team_name}'s #2 scorer, single handedly accounting for the {margin:.1f} point gap by which {team_name} defeated {opp_name}"
]


def hero(ctx):
    """Winning team where (top scorer pts - 2nd scorer pts) > margin of
    victory -- i.e., if the top scorer had only matched the team's #2
    scorer, the team would have lost. Pattern 3-style (varied templates)
    since multiple wins per week can fire.
    """
    wins = [s for s in ctx['scores'] if s.get('result') == 'W']
    if not wins:
        return []
    players_by_team = {}
    for p in ctx['players']:
        players_by_team.setdefault(p['team_id'], []).append(p)
    qualifying = []
    for w in wins:
        team_players = players_by_team.get(w['team_id'], [])
        ranked = sorted(team_players,
                        key=lambda p: p.get('platform_points') or 0,
                        reverse=True)
        if len(ranked) < 2:
            continue
        top, second = ranked[0], ranked[1]
        top_pts = top.get('platform_points') or 0
        second_pts = second.get('platform_points') or 0
        gap = top_pts - second_pts
        margin = w['platform_points'] - w['opponent_points']
        if gap > margin:
            qualifying.append({
                'team_name':   w['team_name'],
                'team_pts':    w['platform_points'],
                'opp_name':    w['opponent_name'],
                'opp_pts':     w['opponent_points'],
                'margin':      margin,
                'hero_name':   top['display_name'],
                'hero_pts':    top_pts,
                'gap':         gap,
                'second_name': second['display_name'],
                'second_pts':  second_pts,
            })
    if not qualifying:
        return []
    qualifying.sort(key=lambda d: d['team_name'])
    # Shuffle the template list each recap so each fire after the first
    # gets a *different* template than its predecessors -- avoids the
    # weird-feeling repeat when multiple heroes fire in the same week.
    # Cycles back to the start if there are more fires than templates.
    shuffled = list(_HERO_TEMPLATES)
    random.shuffle(shuffled)
    return [
        shuffled[i % len(shuffled)].format(**d)
        for i, d in enumerate(qualifying)
    ]


def _losing_team_scapegoats(ctx):
    """Shared helper for scapegoat_net_negative + scapegoat_gross_negative.
    Returns dict {team_id: {...}} for each team that lost this MP,
    including pre-computed flags for both net and gross qualification.
    Players come from pre-loaded ctx['players'] -- no extra Snowflake
    roundtrip.

    Trigger logic:
      net_qualifies   = worst_net_p.platform_points < 0
                        AND -worst_net_p.platform_points >= margin
      gross_qualifies = worst_gross_p.negative_points >= margin
                        AND worst_gross_p.negative_points > 0

    Net is a strict subset of gross (since |net_pts| <= negative_points
    when net is negative), so the two callouts coordinate via
    net_qualifies taking priority and gross firing only when net didn't.
    """
    losses = [s for s in ctx['scores'] if s.get('result') == 'L']
    if not losses:
        return {}
    players_by_team = {}
    for p in ctx['players']:
        players_by_team.setdefault(p['team_id'], []).append(p)
    out = {}
    for loss in losses:
        team_players = players_by_team.get(loss['team_id'], [])
        if not team_players:
            continue
        margin = loss['opponent_points'] - loss['platform_points']
        worst_net_p   = min(team_players,
                            key=lambda p: p.get('platform_points') or 0)
        worst_gross_p = max(team_players,
                            key=lambda p: p.get('negative_points') or 0)
        net_pts = worst_net_p.get('platform_points') or 0
        neg_pts = worst_gross_p.get('negative_points') or 0
        out[loss['team_id']] = {
            'team_name':       loss['team_name'],
            'opp_name':        loss['opponent_name'],
            'team_pts':        loss['platform_points'],
            'opp_pts':         loss['opponent_points'],
            'margin':          margin,
            'worst_net_p':     worst_net_p,
            'worst_gross_p':   worst_gross_p,
            'net_qualifies':   net_pts < 0 and -net_pts >= margin,
            'gross_qualifies': neg_pts > 0 and neg_pts >= margin,
        }
    return out


def scapegoat_net_negative(ctx):
    """Team lost and the team's worst player (by net platform_points) was
    net-negative enough that removing him entirely would have flipped
    the result. ~9 historical occurrences. When this fires for a team,
    scapegoat_gross_negative defers and does NOT fire for that same
    team (net is the harsher framing)."""
    by_team = _losing_team_scapegoats(ctx)
    rows = [(tid, d) for tid, d in by_team.items() if d['net_qualifies']]
    if not rows:
        return []
    rows.sort(key=lambda r: r[1]['team_name'])
    # Games-played per qualifying player (active-slot rows only)
    pids = [d['worst_net_p']['player_id'] for _, d in rows]
    placeholders = ','.join(['%s'] * len(pids))
    games = records.query_snowflake(
        f"""SELECT player_id, SUM(games_played) AS games
            FROM int_player_daily
            WHERE season_year = %s AND matchup_period = %s
              AND is_active_slot = true
              AND player_id IN ({placeholders})
            GROUP BY 1""",
        (ctx['season_year'], ctx['matchup_period'], *pids),
    )
    games_by_pid = {r['player_id']: r['games'] for r in games}
    # Historical: team-MPs where worst player's net flipped the loss.
    hist_n = records.query_snowflake("""
        WITH p AS (
          SELECT season_year, matchup_period, team_id,
                 MIN(platform_points) AS worst_net
          FROM fct_weekly_player_active_performance
          GROUP BY 1, 2, 3
        ),
        t AS (
          SELECT t.season_year, t.matchup_period, t.team_id,
                 (t.opponent_points - t.platform_points) AS margin
          FROM fct_weekly_team_active_performance t
          JOIN matchup_schedule s
            ON t.season_year = s.season_year
           AND t.matchup_period = s.matchup_period
          WHERE t.result = 'L' AND s.is_abnormal = false
        )
        SELECT COUNT(*) AS n
        FROM t JOIN p USING (season_year, matchup_period, team_id)
        WHERE p.worst_net < 0 AND -p.worst_net >= t.margin
    """)[0]['n']
    base_ord = hist_n - len(rows) + 1
    out = []
    for i, (tid, d) in enumerate(rows):
        p = d['worst_net_p']
        nth = records.ordinal(base_ord + i)
        net_pts = p.get('platform_points') or 0
        games = games_by_pid.get(p['player_id'], '?')
        game_word = 'game' if games == 1 else 'games'
        out.append(
            f"[b]Scapegoat:[/b] {d['team_name']} lost to {d['opp_name']} "
            f"by {d['margin']:.1f} points. {p['display_name']} put up "
            f"{net_pts:.1f} points total, over {games} {game_word}. If "
            f"{d['team_name']} had just left his lineup slots blank "
            f"instead of playing his sorry ass, {d['team_name']} would "
            f"have won and {p['display_name']} would not be the most "
            f"pathetic loser on the face of the earth. The {nth} such "
            f"performance in league history."
        )
    return out


def scapegoat_gross_negative(ctx):
    """Team lost and a player's negative-day point magnitude was >=
    margin of defeat, BUT that team's loss does NOT also qualify for
    scapegoat_net_negative (which takes priority).

    Net is a strict subset of gross, so the disjoint cut is
    'gross_qualifies AND NOT net_qualifies'. The historical ordinal
    counts gross-only events (excluding gross-AND-net overlaps), so
    each callout's 'Mth such performance' is self-consistent.
    ~17 gross historically; ~9 of those also met net; ~8 gross-only.
    """
    by_team = _losing_team_scapegoats(ctx)
    rows = [(tid, d) for tid, d in by_team.items()
            if d['gross_qualifies'] and not d['net_qualifies']]
    if not rows:
        return []
    rows.sort(key=lambda r: r[1]['team_name'])
    # Negative-day count per qualifying player
    pids = [d['worst_gross_p']['player_id'] for _, d in rows]
    placeholders = ','.join(['%s'] * len(pids))
    neg = records.query_snowflake(
        f"""SELECT player_id, COUNT(*) AS neg_days
            FROM int_player_daily
            WHERE season_year = %s AND matchup_period = %s
              AND is_active_slot = true
              AND platform_points < 0
              AND player_id IN ({placeholders})
            GROUP BY 1""",
        (ctx['season_year'], ctx['matchup_period'], *pids),
    )
    neg_days_by_pid = {r['player_id']: r['neg_days'] for r in neg}
    # Historical: gross-qualifying losses that did NOT also meet net.
    hist_n = records.query_snowflake("""
        WITH p AS (
          SELECT season_year, matchup_period, team_id,
                 MAX(negative_points) AS worst_neg,
                 MIN(platform_points) AS worst_net
          FROM fct_weekly_player_active_performance
          GROUP BY 1, 2, 3
        ),
        t AS (
          SELECT t.season_year, t.matchup_period, t.team_id,
                 (t.opponent_points - t.platform_points) AS margin
          FROM fct_weekly_team_active_performance t
          JOIN matchup_schedule s
            ON t.season_year = s.season_year
           AND t.matchup_period = s.matchup_period
          WHERE t.result = 'L' AND s.is_abnormal = false
        )
        SELECT COUNT(*) AS n
        FROM t JOIN p USING (season_year, matchup_period, team_id)
        WHERE p.worst_neg >= t.margin
          AND NOT (p.worst_net < 0 AND -p.worst_net >= t.margin)
    """)[0]['n']
    base_ord = hist_n - len(rows) + 1
    out = []
    for i, (tid, d) in enumerate(rows):
        p = d['worst_gross_p']
        nth = records.ordinal(base_ord + i)
        total_pts = p.get('platform_points') or 0
        neg_pts   = p.get('negative_points') or 0
        n_games   = neg_days_by_pid.get(p['player_id'], '?')
        game_word = 'game' if n_games == 1 else 'games'
        out.append(
            f"[b]Scapegoat:[/b] {d['team_name']} lost to {d['opp_name']} "
            f"by {d['margin']:.1f} points. {p['display_name']} put up "
            f"{total_pts:.1f} points total, including {neg_pts:.1f} "
            f"[i]negative[/i] points across {n_games} {game_word}. If he had "
            f"just put up 0 in those outings, {d['team_name']} would "
            f"have won, the {nth} such performance in league history."
        )
    return out


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


def no_negative_days(ctx):
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


def baseblunders(ctx):
    """Teams with more CS than SB in the MP. Per-team line. Special
    add-on for the rarer sub-case where SB = 0 (caught stealing >= 1
    AND zero successful steals).

    Two cumulative ordinals tracked:
      main: count of team-MPs ever where cs > sb
      sub:  count of team-MPs ever where cs >= 1 AND sb = 0
    """
    teams = [s for s in ctx['scores']
             if (s.get('cs') or 0) > (s.get('sb') or 0)]
    if not teams:
        return []
    teams.sort(key=lambda s: s.get('team_abbrev') or '')
    hist = records.query_snowflake("""
        SELECT
          SUM(CASE WHEN t.cs > t.sb THEN 1 ELSE 0 END) AS cs_gt_sb_n,
          SUM(CASE WHEN t.cs >= 1 AND t.sb = 0 THEN 1 ELSE 0 END) AS zero_sb_n
        FROM fct_weekly_team_active_performance t
        JOIN matchup_schedule s
          ON t.season_year = s.season_year
         AND t.matchup_period = s.matchup_period
        WHERE s.is_abnormal = false
    """)[0]
    n_today         = len(teams)
    n_today_zero_sb = sum(1 for t in teams if (t.get('sb') or 0) == 0)
    base_ord         = hist['cs_gt_sb_n'] - n_today + 1
    base_ord_zero_sb = hist['zero_sb_n']  - n_today_zero_sb + 1
    out = []
    zero_sb_seen = 0
    for i, t in enumerate(teams):
        cs = int(t.get('cs') or 0)
        sb = int(t.get('sb') or 0)
        nth = records.ordinal(base_ord + i)
        line = (
            f"[b]Baserunners? More like baseblunders![/b] The clumsy "
            f"fuckers on {t['team_name']} became the {nth} team to have "
            f"more CS ({cs}) than SB ({sb}) in a matchup"
        )
        if sb == 0:
            sb_zero_nth = records.ordinal(base_ord_zero_sb + zero_sb_seen)
            line += (
                f", and only the {sb_zero_nth} team to do so with no "
                f"steals at all. Idk if cool kids still say \"secure the "
                f"bag\" but it's clear the idiots on {t['team_name']} "
                f"don't."
            )
            zero_sb_seen += 1
        else:
            line += "."
        out.append(line)
    return out


def no_quality_starts(ctx):
    """Teams with 0 QS in the MP, with a count of the starts the staff
    actually trotted out. Per-team line. Skips teams that started zero
    pitchers entirely (no SP-slot games_played>=1 rows) -- if you
    didn't even take the mound, you didn't fart, you abstained.

    Each line includes the cumulative ordinal of 0-QS team-MPs in
    league history (~17 through this MP). Multiple-teams-firing-today
    gets sequential ordinals, alphabetical-by-team_abbrev.
    """
    candidates = [s for s in ctx['scores'] if (s.get('qs') or 0) == 0]
    if not candidates:
        return []
    team_ids = [t['team_id'] for t in candidates]
    placeholders = ','.join(['%s'] * len(team_ids))
    gs_rows = records.query_snowflake(
        f"""SELECT team_id, COUNT(*) AS gs_count
            FROM int_player_daily
            WHERE season_year = %s AND matchup_period = %s
              AND lineup_slot = 'SP'
              AND games_played >= 1
              AND team_id IN ({placeholders})
            GROUP BY 1""",
        (ctx['season_year'], ctx['matchup_period'], *team_ids),
    )
    gs_by_team = {r['team_id']: r['gs_count'] for r in gs_rows}
    teams = [t for t in candidates if gs_by_team.get(t['team_id'], 0) >= 1]
    if not teams:
        return []
    teams.sort(key=lambda s: s.get('team_abbrev') or '')
    # Historical count mirrors the trigger criteria exactly: at least one
    # SP start AND zero quality starts in the MP, abnormal weeks excluded.
    # league_history_count('team','QS',0) would include team-MPs where no
    # SP started at all (which the trigger excludes), drifting the ordinal
    # upward from the actual trigger definition.
    hist_rows = records.query_snowflake("""
        WITH team_mp_starts AS (
            SELECT
                season_year, matchup_period, team_id,
                SUM(CASE WHEN lineup_slot = 'SP' AND games_played >= 1
                         THEN 1 ELSE 0 END) AS sp_starts
            FROM int_player_daily
            GROUP BY 1, 2, 3
        )
        SELECT COUNT(*) AS n
        FROM fct_weekly_team_active_performance t
        JOIN matchup_schedule s
            ON t.season_year = s.season_year
           AND t.matchup_period = s.matchup_period
        JOIN team_mp_starts tms
            ON t.season_year = tms.season_year
           AND t.matchup_period = tms.matchup_period
           AND t.team_id = tms.team_id
        WHERE s.is_abnormal = false
          AND tms.sp_starts >= 1
          AND t.qs = 0
    """)
    total_through_now = hist_rows[0]['n'] if hist_rows else None
    # Defensive None-guard: if the query ever fails to populate, fall back
    # to dropping the ordinal clause rather than crashing the recap.
    base_ord = (None if total_through_now is None
                else total_through_now - len(teams) + 1)
    out = []
    for i, t in enumerate(teams):
        if base_ord is not None:
            ord_clause = (
                f" The {records.ordinal(base_ord + i)} 0-QS matchup in "
                f"league history."
            )
        else:
            ord_clause = ""
        out.append(
            f"[b]No Quality Starts, just Low Quality Farts:[/b] The boys on "
            f"{t['team_name']} failed to produce even one quality start, "
            f"trotting out {gs_by_team[t['team_id']]} low-grade, bootleg-ass "
            f"starts that no self respecting starlet would be caught dead "
            f"wearing on the red carpet.{ord_clause} Hack shit, shame!"
        )
    return out


# ============================================================
# HR streak callouts (consecutive AB-played days with >= 1 HR)
# ============================================================

# Module-level cache so hr_streak_active and hr_streak_ended share the
# per-recap data without re-running the (~half-million-row) team-day
# query twice. Keyed by (season_year, matchup_period). Stale-on-rerun
# is acceptable since the data is point-in-time for each recap.
_HR_STREAK_CACHE = {}

_HR_STREAK_ACTIVE_THRESHOLD = 7
_HR_STREAK_ENDED_THRESHOLD  = 10


def _hr_streak_data(ctx):
    """Build per-team HR-day-run history through the recap MP's end.

    A 'run' is a maximal sequence of consecutive AB-played days where
    the team had >= 1 HR. AB-played = sum(ab) >= 1 (active hitter
    rows). No-play days (no active hitters, e.g., ASB) are neutral and
    do NOT break a run -- they're simply not in the played-day series.

    Returns dict:
      recap_year, recap_mp:       int
      recap_first_sp, recap_last_sp: int  (SP boundaries of the recap MP)
      team_data: {team_id: {
        team_name, team_abbrev: str,
        runs: [{start: day_row, end: day_row, length: int,
                broke_on: day_row_or_None, is_active: bool}, ...]
      }}
    """
    key = (ctx['season_year'], ctx['matchup_period'])
    if key in _HR_STREAK_CACHE:
        return _HR_STREAK_CACHE[key]
    recap_year = ctx['season_year']
    recap_mp   = ctx['matchup_period']
    mp_bounds = records.query_snowflake("""
        SELECT MIN(scoring_period) AS first_sp,
               MAX(scoring_period) AS last_sp
        FROM int_player_daily
        WHERE season_year = %s AND matchup_period = %s
    """, (recap_year, recap_mp))[0]
    recap_first_sp = mp_bounds['first_sp']
    recap_last_sp  = mp_bounds['last_sp']
    rows = records.query_snowflake("""
        SELECT season_year, matchup_period, scoring_period,
               team_id, team_abbrev, team_name,
               SUM(ab) AS ab_total, SUM(hr) AS hr_total
        FROM int_player_daily
        WHERE is_active_slot = true
          AND lineup_slot_category = 'hitting'
          AND (season_year < %s
               OR (season_year = %s AND scoring_period <= %s))
        GROUP BY 1, 2, 3, 4, 5, 6
    """, (recap_year, recap_year, recap_last_sp))
    by_team = {}
    for r in rows:
        by_team.setdefault(r['team_id'], []).append(r)
    team_data = {}
    for tid, days in by_team.items():
        played = [d for d in days if (d.get('ab_total') or 0) >= 1]
        played.sort(key=lambda d: (d['season_year'], d['scoring_period']))
        if not played:
            continue
        runs = []
        run_len = 0
        run_start = None
        run_end   = None
        for d in played:
            is_hr = (d.get('hr_total') or 0) >= 1
            if is_hr:
                if run_len == 0:
                    run_start = d
                run_len += 1
                run_end = d
            else:
                if run_len > 0:
                    runs.append({
                        'start':     run_start,
                        'end':       run_end,
                        'length':    run_len,
                        'broke_on':  d,
                        'is_active': False,
                    })
                    run_len = 0
        if run_len > 0:
            runs.append({
                'start':     run_start,
                'end':       run_end,
                'length':    run_len,
                'broke_on':  None,
                'is_active': True,
            })
        team_data[tid] = {
            'team_name':   played[0]['team_name'],
            'team_abbrev': played[0]['team_abbrev'],
            'runs':        runs,
        }
    result = {
        'recap_year':     recap_year,
        'recap_mp':       recap_mp,
        'recap_first_sp': recap_first_sp,
        'recap_last_sp':  recap_last_sp,
        'team_data':      team_data,
    }
    _HR_STREAK_CACHE[key] = result
    return result


def hr_streak_active(ctx):
    """Teams whose HR streak (consecutive AB-played days with >= 1 HR)
    is still alive AND >= 7 days at the end of the recap MP. Pattern 1
    (single inline line). Lists qualifying teams, highlights the
    league's longest current streak, and cites the all-time record
    for context.

    Note: in standard 7-day MPs, 'active streak >= 7 days at MP end' is
    equivalent to 'homered every day of this MP'. In the 14-day
    ASB-spanning MP16, a 7-day active streak may not literally cover
    every day of the MP -- accepted edge case (one MP/season).
    """
    data = _hr_streak_data(ctx)
    if not data['team_data']:
        return []
    active = []
    for tid, td in data['team_data'].items():
        if not td['runs']:
            continue
        last_run = td['runs'][-1]
        if last_run['is_active'] and last_run['length'] >= _HR_STREAK_ACTIVE_THRESHOLD:
            active.append({
                'team_name':   td['team_name'],
                'team_abbrev': td['team_abbrev'],
                'streak_len':  last_run['length'],
            })
    if not active:
        return []
    # Longest current = sort by length desc, alphabetical for tie-break.
    longest = sorted(active, key=lambda t: (-t['streak_len'], t['team_abbrev']))[0]
    # Roster the team list alphabetical-by-name.
    active_alpha = sorted(active, key=lambda t: t['team_name'])
    names = [t['team_name'] for t in active_alpha]
    if len(names) == 1:
        teams_phrase = names[0]
    elif len(names) == 2:
        teams_phrase = f"{names[0]} and {names[1]}"
    else:
        teams_phrase = ", ".join(names[:-1]) + f", and {names[-1]}"
    # All-time league record EXCLUDING the current longest active streak
    # (otherwise that streak's own length would pose as a "prior record"
    # against itself, making every record-tying/breaking case indistinguishable).
    # The dataset extends through this MP's end, so without this guard the
    # longest active run would self-equal record_len whenever it's the
    # all-time leader.
    record_len  = 0
    record_team = None
    record_year = None
    for td in data['team_data'].values():
        for r in td['runs']:
            is_the_longest_active = (
                r['is_active']
                and td['team_name'] == longest['team_name']
                and r['length']     == longest['streak_len']
            )
            if is_the_longest_active:
                continue
            if r['length'] > record_len:
                record_len  = r['length']
                record_team = td['team_name']
                record_year = r['end']['season_year']
    if longest['streak_len'] > record_len:
        record_clause = "a new league record."
    elif longest['streak_len'] == record_len:
        record_clause = (
            f"tying the all-time league record of {record_len} "
            f"set by {record_team} in {record_year}."
        )
    else:
        record_clause = (
            f"league record is {record_len} by {record_team} in {record_year}."
        )
    return [
        f"[b]Active Homerun Streak:[/b] Much like your local Fox "
        f"Syndicate, {teams_phrase} all homered every day last week. "
        f"{longest['team_name']}'s got the longest streak in the league "
        f"at {longest['streak_len']} games, {record_clause}"
    ]


def hr_streak_ended(ctx):
    """HR streaks of >= 10 consecutive HR-days that BROKE during the
    recap MP (i.e., a non-HR AB-played day in this MP terminated a
    long run). Pattern 2 (one line per ended streak).

    Note: text is intentionally minimal -- user to revise wording.
    Trigger logic and data wiring are the stable part.
    """
    data = _hr_streak_data(ctx)
    if not data['team_data']:
        return []
    ended = []
    for td in data['team_data'].values():
        for r in td['runs']:
            if r['is_active']:
                continue
            if r['length'] < _HR_STREAK_ENDED_THRESHOLD:
                continue
            bo = r['broke_on']
            if bo is None:
                continue
            if (bo['season_year']    == data['recap_year']
                    and bo['matchup_period'] == data['recap_mp']):
                ended.append({
                    'team_name':   td['team_name'],
                    'team_abbrev': td['team_abbrev'],
                    'streak_len':  r['length'],
                })
    if not ended:
        return []
    ended.sort(key=lambda x: (-x['streak_len'], x['team_abbrev']))
    return [
        f"[b]Streak Snapped:[/b] {e['team_name']}'s {e['streak_len']}-game "
        f"HR streak came to an end this week."
        for e in ended
    ]


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
    mismatch,
    tough_luck,
    lucky_bastard,
    fair_and_just,
    hero,
    scapegoat_net_negative,
    scapegoat_gross_negative,
    # Rare events
    no_hitters,
    cycles,
    no_negative_days,
    hr_streak_ended,
    # Recurring oddities
    zero_steals,
    baseblunders,
    hr_drought,
    hr_streak_active,
    no_quality_starts,
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
    call per grain (team-week + active-player-week wide rows).

    Seeds Python's `random` module with (season_year, matchup_period)
    so callouts that pick from shuffle-cycled template lists
    (`hero`, `hr_drought`, etc.) produce reproducible output per
    recap. Variety is preserved across weeks (different MP -> different
    seed -> different template pick); within a given week any number
    of reruns produce identical BBCode. Required for the byte-diff
    golden-output test to stay deterministic.
    """
    # Python 3.13's random.seed only accepts int/float/str/bytes; pack
    # (season_year, matchup_period) into a single int via the standard
    # pattern used elsewhere in the codebase (e.g. fct incremental filter).
    random.seed(season_year * 100 + matchup_period)

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
