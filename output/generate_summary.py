"""
Generate the weekly front-page summary from the mart tables.

Reads fct_weekly_team_performance and fct_weekly_player_performance (the wide
convergence facts shipped in Phase 3.1) to produce a BBCode-formatted
summary for the ESPN league front page.
"""

import os

from dotenv import load_dotenv
import snowflake.connector

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

    Returns counting + rate columns alongside scoring totals so the Top
    Hitter / Top Pitcher callouts can render their stat lines without a
    second query.
    """
    return query_snowflake("""
        SELECT team_name, team_id, player_id, display_name,
               platform_points, platform_hitting_pts, platform_pitching_pts,
               -- Hitting counting + rates for Top Hitter callout
               h, ab, hr, rbi, sb,
               avg, obp, slg,
               -- Pitching counting + rates for Top Pitcher callout
               w, sv, k, p_bb, outs,
               era, whip
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


# ---------- Top Hitter / Top Pitcher callouts ----------

def fmt_avg(x):
    """Baseball-style rate formatting (.350, not 0.350). NULL → .000."""
    if x is None:
        return ".000"
    s = f"{x:.3f}"
    return s.lstrip("0") if s.startswith("0.") else s


def fmt_ip(outs):
    """Innings pitched in baseball notation: 9.0, 9.1, 9.2 (one out = .1, NOT decimal .333)."""
    if outs is None or outs == 0:
        return "0.0"
    outs = int(outs)
    return f"{outs // 3}.{outs % 3}"


def find_top_hitter(players):
    """Player with the highest platform_hitting_pts (>0). None if no qualifying player."""
    hitters = [p for p in players if (p['platform_hitting_pts'] or 0) > 0]
    return max(hitters, key=lambda p: p['platform_hitting_pts']) if hitters else None


def find_top_pitcher(players):
    """Player with the highest platform_pitching_pts (>0). None if no qualifying player."""
    pitchers = [p for p in players if (p['platform_pitching_pts'] or 0) > 0]
    return max(pitchers, key=lambda p: p['platform_pitching_pts']) if pitchers else None


def format_hitter_line(player):
    """Top Hitter callout: pts by Player (Team) -- avg/obp/slg over AB. HR, RBI[, SB]"""
    rate = f"{fmt_avg(player['avg'])}/{fmt_avg(player['obp'])}/{fmt_avg(player['slg'])}"
    counting = [
        f"{int(player['hr'] or 0)} HR",
        f"{int(player['rbi'] or 0)} RBI",
    ]
    if (player['sb'] or 0) > 0:
        counting.append(f"{int(player['sb'])} SB")

    return (
        f"{player['platform_hitting_pts']:.1f} pts by {player['display_name']} "
        f"({player['team_name']}) -- "
        f"{rate} over {int(player['ab'] or 0)} AB. "
        f"{', '.join(counting)}"
    )


def format_pitcher_line(player):
    """Top Pitcher callout: pts by Player (Team) -- [Wins, ][Saves, ]ERA, WHIP. K : BB over IP"""
    leading = []
    if (player['w'] or 0) > 0:
        wins = int(player['w'])
        leading.append(f"{wins} {'Win' if wins == 1 else 'Wins'}")
    if (player['sv'] or 0) > 0:
        saves = int(player['sv'])
        leading.append(f"{saves} {'Save' if saves == 1 else 'Saves'}")

    era = player['era']
    whip = player['whip']
    leading.append(f"{era:.2f} ERA" if era is not None else "— ERA")
    leading.append(f"{whip:.2f} WHIP" if whip is not None else "— WHIP")

    k = int(player['k'] or 0)
    bb = int(player['p_bb'] or 0)
    ip = fmt_ip(player['outs'])

    return (
        f"{player['platform_pitching_pts']:.1f} pts by {player['display_name']} "
        f"({player['team_name']}) -- "
        f"{', '.join(leading)}. "
        f"{k} K : {bb} BB over {ip} IP"
    )


def get_wasted_points(season_year, matchup_period, limit=5):
    """
    Top N wasted-points performers for a matchup period (Phase 4).

    A player who was both ROSTERED_INACTIVE and FA in the same matchup
    period (e.g., dropped mid-week) gets their wasted_points summed
    across both buckets — one row per player. The two source buckets are
    surfaced separately (fa_wasted_pts, bench_wasted_pts) so the formatter
    can attribute "X unowned, Y benched" in the parenthetical.

    Joins stg_box_scores for MLB pro_team and primary position metadata,
    and fct_weekly_player_performance to detect partial-active weeks
    (player who also had active days during the same matchup period).

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
            -- for pro_team / position. Handles mid-period trades by picking
            -- the latest snapshot.
            SELECT player_id, pro_team, position
            FROM (
                SELECT player_id, pro_team, position,
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
            COALESCE(a.active_team_name, w.bench_team_name, 'Free Agent')
                AS fantasy_team,
            w.fa_wasted_pts,
            w.bench_wasted_pts,
            w.wasted_points_total AS wasted_points,
            a.platform_points AS active_points
        FROM wasted_combined w
        LEFT JOIN player_meta m ON w.player_id = m.player_id
        LEFT JOIN active_points a ON w.player_id = a.player_id
        ORDER BY w.wasted_points_total DESC
        LIMIT %s
    """, (season_year, matchup_period, season_year, matchup_period,
          season_year, matchup_period, limit))


def format_wasted_points(wasted):
    """
    Top Wasted Performances callout. Combined across FA and ROSTERED_INACTIVE
    buckets per player; parenthetical attributes points to their source.

    Format per row:
        N. Player (MLB Team, Pos) -- Fantasy Team -- TOTAL [(BREAKDOWN)]

    TOTAL takes one of two forms:
      - "X+Y waste pts"    when wasted points came from BOTH FA and bench
                           (the addition signals the split; sum is X+Y)
      - "X.X pts"          when wasted points came from a single bucket
                           (no ambiguity, just the value)

    BREAKDOWN parenthetical lists each non-zero of (unowned, benched,
    active). It appears when there's something to attribute beyond the
    main line — i.e., either:
      - waste came from both buckets (already shown via X+Y above; the
        parenthetical names them), OR
      - active points are non-zero (need to show the "and also X active"
        context). Threshold is != 0 not > 0 so a net-negative active
        stretch still appears (informative — they were "doubly wasted":
        held but lost ground when started).
    Single-source-no-active rows (the common pure-FA case) get no
    parenthetical since the row already says all there is to say.
    """
    if not wasted:
        return []

    lines = ["", f"[u][b]Top {len(wasted)} Wasted Performances[/b][/u]"]
    for i, p in enumerate(wasted, 1):
        fa_pts     = p['fa_wasted_pts']     or 0
        bench_pts  = p['bench_wasted_pts']  or 0
        active_pts = p['active_points']     or 0

        # Total format
        if fa_pts and bench_pts:
            total_str = f"{fa_pts:.1f}+{bench_pts:.1f} waste pts"
        else:
            total_str = f"{p['wasted_points']:.1f} pts"

        # Breakdown parenthetical
        parts = []
        if (fa_pts and bench_pts) or active_pts != 0:
            if fa_pts:
                parts.append(f"{fa_pts:.1f} unowned")
            if bench_pts:
                parts.append(f"{bench_pts:.1f} benched")
            if active_pts != 0:
                parts.append(f"{active_pts:.1f} active")
        paren = f" ({', '.join(parts)})" if parts else ""

        lines.append(
            f"{i}. {p['display_name']} ({p['pro_team']}, {p['position']}) "
            f"-- {p['fantasy_team']} -- {total_str}{paren}"
        )
    return lines


def get_records(active_season, season_only=False):
    """
    Fetch all matchup scores for records calculation.
    Excludes abnormal weeks. If season_only=True, filters to active_season.
    """
    season_filter = f"AND f.season_year = {active_season}" if season_only else ""

    return query_snowflake(f"""
        SELECT
            f.season_year,
            f.matchup_period,
            f.team_name,
            f.owner_name,
            f.platform_points,
            f.platform_hitting_pts,
            f.platform_pitching_pts
        FROM fct_weekly_team_performance f
        LEFT JOIN MATCHUP_SCHEDULE s
            ON f.season_year = s.season_year
            AND f.matchup_period = s.matchup_period
        WHERE s.is_abnormal = false
        {season_filter}
    """)


def format_records(records):
    best_total    = max(records, key=lambda x: x['platform_points'])
    best_hitting  = max(records, key=lambda x: x['platform_hitting_pts'])
    best_pitching = max(records, key=lambda x: x['platform_pitching_pts'])

    worst_total    = min(records, key=lambda x: x['platform_points'])
    worst_hitting  = min(records, key=lambda x: x['platform_hitting_pts'])
    worst_pitching = min(records, key=lambda x: x['platform_pitching_pts'])

    def fmt(row, score_key):
        return (
            f"{row['team_name']} ({row['owner_name']}) -- "
            f"{row[score_key]:.1f} pts, "
            f"{row['season_year']} Matchup #{row['matchup_period']}"
        )

    return {
        'best_total':     fmt(best_total,    'platform_points'),
        'best_hitting':   fmt(best_hitting,  'platform_hitting_pts'),
        'best_pitching':  fmt(best_pitching, 'platform_pitching_pts'),
        'worst_total':    fmt(worst_total,   'platform_points'),
        'worst_hitting':  fmt(worst_hitting, 'platform_hitting_pts'),
        'worst_pitching': fmt(worst_pitching,'platform_pitching_pts'),
    }


def generate_summary(matchup_period, scores, contributions, wasted_points,
                     season_records, alltime_records):
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
        f"[u][b]Matchup #{matchup_period} Recap[/b][/u]",
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

    # Player-level superlatives across the whole league (top hitter / top pitcher
    # by platform_hitting_pts and platform_pitching_pts respectively). Stashed in the
    # contributions dict by get_contribution_callouts.
    top_hitter = contributions.get('top_hitter')
    top_pitcher = contributions.get('top_pitcher')
    if top_hitter:
        lines.extend([
            f"",
            f"[b]Top Hitter[/b]: {format_hitter_line(top_hitter)}",
        ])
    if top_pitcher:
        lines.append(f"[b]Top Pitcher[/b]: {format_pitcher_line(top_pitcher)}")

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

    # Top Wasted Points (Phase 4) — last item in the weekly recap, before records.
    lines.extend(format_wasted_points(wasted_points))

    # Records
    lines.extend([
        f"",
        f"[u][b]Current Season Records[/b][/u]",
        f"[b]Best Matchup Total[/b]: {season_records['best_total']}",
        f"[b]Best Matchup Hitting[/b]: {season_records['best_hitting']}",
        f"[b]Best Matchup Pitching[/b]: {season_records['best_pitching']}",
        f"[b]Worst Matchup Total[/b]: {season_records['worst_total']}",
        f"[b]Worst Matchup Hitting[/b]: {season_records['worst_hitting']}",
        f"[b]Worst Matchup Pitching[/b]: {season_records['worst_pitching']}",
        f"",
        f"[u][b]All-Time League Records[/b][/u]",
        f"[b]Best Matchup Total[/b]: {alltime_records['best_total']}",
        f"[b]Best Matchup Hitting[/b]: {alltime_records['best_hitting']}",
        f"[b]Best Matchup Pitching[/b]: {alltime_records['best_pitching']}",
        f"[b]Worst Matchup Total[/b]: {alltime_records['worst_total']}",
        f"[b]Worst Matchup Hitting[/b]: {alltime_records['worst_hitting']}",
        f"[b]Worst Matchup Pitching[/b]: {alltime_records['worst_pitching']}",
    ])

    # Optional league note from output/LeagueNote.txt -- print contents verbatim
    # if the file exists and is non-empty. Lets the commissioner add ad-hoc
    # commentary, scoring change notes, etc., without code changes.
    note_path = os.path.join(os.path.dirname(__file__), "LeagueNote.txt")
    if os.path.exists(note_path):
        with open(note_path, "r", encoding="utf-8") as f:
            note_content = f.read().strip()
        if note_content:
            lines.extend([
                f"",
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

    season_raw      = get_records(active_season, season_only=True)
    alltime_raw     = get_records(active_season, season_only=False)
    season_records  = format_records(season_raw)
    alltime_records = format_records(alltime_raw)

    summary = generate_summary(matchup_period, scores, contributions,
                               wasted_points, season_records, alltime_records)
    print(summary)