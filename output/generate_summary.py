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


def generate_summary(matchup_period, scores, contributions, season_records, alltime_records):
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

    season_raw      = get_records(active_season, season_only=True)
    alltime_raw     = get_records(active_season, season_only=False)
    season_records  = format_records(season_raw)
    alltime_records = format_records(alltime_raw)

    summary = generate_summary(matchup_period, scores, contributions, season_records, alltime_records)
    print(summary)