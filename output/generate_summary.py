"""
Generate the weekly front-page summary from the mart tables.

Reads fct_weekly_team_scores and int_weekly_matchups to produce
a BBCode-formatted summary for the ESPN league front page.
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
            FROM fct_weekly_team_scores
            WHERE season_year = %s
        """, (season_year,))
        matchup_period = result[0]['mp']

    scores = query_snowflake("""
        SELECT season_year, matchup_period, team_name, total_points,
               hitting_points, pitching_points, days_in_period
        FROM fct_weekly_team_scores
        WHERE matchup_period = %s
        AND season_year = %s
        ORDER BY total_points DESC
    """, (matchup_period, season_year))

    return matchup_period, scores


def get_matchups(season_year, matchup_period):
    return query_snowflake("""
        SELECT team_name, team_points, opponent_name,
               opponent_points, result
        FROM int_weekly_matchups
        WHERE matchup_period = %s
        AND season_year = %s
    """, (matchup_period, season_year))

def get_player_contributions(season_year, matchup_period):
    return query_snowflake("""
        SELECT
            p.team_name,
            p.player_id,
            COALESCE(n.nickname, p.player_name) as display_name,
            p.position,
            p.player_type,
            p.total_points
        FROM fct_weekly_player_scores p
        LEFT JOIN player_nicknames n ON p.player_id = n.player_id
        WHERE p.matchup_period = %s
        AND p.season_year = %s
        ORDER BY p.total_points DESC
    """, (matchup_period, season_year))

def get_contribution_callouts(scores, players):
    """
    Returns top 5 scorers from the best overall team,
    top 3 hitters from the best hitting team,
    top 3 pitchers from the best pitching team.
    """
    best_overall_team  = scores[0]['team_name']
    best_hitting_team  = sorted(scores, key=lambda x: x['hitting_points'], reverse=True)[0]['team_name']
    best_pitching_team = sorted(scores, key=lambda x: x['pitching_points'], reverse=True)[0]['team_name']

    top_overall = [
        p for p in players if p['team_name'] == best_overall_team
    ][:5]

    top_hitters = [
        p for p in players
        if p['team_name'] == best_hitting_team
        and p['player_type'] == 'hitting'
    ][:3]

    top_pitchers = [
        p for p in players
        if p['team_name'] == best_pitching_team
        and p['player_type'] == 'pitching'
    ][:3]

    return {
        'best_overall_team':  best_overall_team,
        'best_hitting_team':  best_hitting_team,
        'best_pitching_team': best_pitching_team,
        'top_overall':        top_overall,
        'top_hitters':        top_hitters,
        'top_pitchers':       top_pitchers,
    }

def find_tough_luck(scores, matchups):
    """
    Tough Luck: only triggers if the #2 overall scorer lost.
    """
    ranked = sorted(scores, key=lambda x: x['total_points'], reverse=True)
    second_place = ranked[1]

    matchup = next(
        (m for m in matchups if m['team_name'] == second_place['team_name']),
        None
    )
    if matchup and matchup['result'] == 'L':
        return {
            'team': second_place['team_name'],
            'points': second_place['total_points'],
            'opponent': matchup['opponent_name'],
            'opponent_points': matchup['opponent_points'],
        }
    return None


def find_lucky_bastard(scores, matchups):
    """
    Lucky Bastard: only triggers if the #13-of-14 scorer (second lowest) won.
    """
    ranked = sorted(scores, key=lambda x: x['total_points'], reverse=True)
    second_worst = ranked[-2]

    matchup = next(
        (m for m in matchups if m['team_name'] == second_worst['team_name']),
        None
    )
    if matchup and matchup['result'] == 'W':
        return {
            'team': second_worst['team_name'],
            'points': second_worst['total_points'],
            'opponent': matchup['opponent_name'],
            'opponent_points': matchup['opponent_points'],
        }
    return None


def check_fair_and_just(scores, matchups):
    """
    Fair and Just League: did every top-half team (by score) win,
    and every bottom-half team lose?
    
    Uses active matchup count (not team count) as the cutoff,
    which correctly handles odd-team leagues with a bye week.
    """
    ranked = sorted(scores, key=lambda x: x['total_points'], reverse=True)
    num_matchups = len(matchups) // 2  # <-- was: len(ranked) // 2

    for i, team in enumerate(ranked):
        matchup = next(
            (m for m in matchups if m['team_name'] == team['team_name']),
            None
        )
        if not matchup:
            return False
        if i < num_matchups and matchup['result'] != 'W':
            return False
        if i >= num_matchups and matchup['result'] != 'L':
            return False
    return True

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
            f.total_points,
            f.hitting_points,
            f.pitching_points
        FROM fct_weekly_team_scores f
        LEFT JOIN MATCHUP_SCHEDULE s
            ON f.season_year = s.season_year
            AND f.matchup_period = s.matchup_period
        WHERE s.is_abnormal = false
        {season_filter}
    """)


def format_records(records):
    best_total    = max(records, key=lambda x: x['total_points'])
    best_hitting  = max(records, key=lambda x: x['hitting_points'])
    best_pitching = max(records, key=lambda x: x['pitching_points'])

    worst_total    = min(records, key=lambda x: x['total_points'])
    worst_hitting  = min(records, key=lambda x: x['hitting_points'])
    worst_pitching = min(records, key=lambda x: x['pitching_points'])

    def fmt(row, score_key):
        return (
            f"{row['team_name']} -- "
            f"{row[score_key]:.1f} pts, "
            f"{row['season_year']} Matchup #{row['matchup_period']}"
        )

    return {
        'best_total':     fmt(best_total,    'total_points'),
        'best_hitting':   fmt(best_hitting,  'hitting_points'),
        'best_pitching':  fmt(best_pitching, 'pitching_points'),
        'worst_total':    fmt(worst_total,   'total_points'),
        'worst_hitting':  fmt(worst_hitting, 'hitting_points'),
        'worst_pitching': fmt(worst_pitching,'pitching_points'),
    }


def generate_summary(matchup_period, scores, matchups, contributions, season_records, alltime_records):
    """Build the BBCode-formatted front-page summary."""

    best_overall = scores[0]
    worst_overall = scores[-1]

    by_hitting = sorted(scores, key=lambda x: x['hitting_points'], reverse=True)
    best_hitting = by_hitting[0]
    worst_hitting = by_hitting[-1]

    by_pitching = sorted(scores, key=lambda x: x['pitching_points'], reverse=True)
    best_pitching = by_pitching[0]
    worst_pitching = by_pitching[-1]

    def fmt_players(player_list):
            return ", ".join(
                f"{p['display_name']}: {p['total_points']:.1f}"
                for p in player_list
            )

    lines = [
            f"[u][b]Matchup #{matchup_period} Recap[/b][/u]",
            f"",
            f"[b]Best Overall[/b]: {best_overall['total_points']:.1f} pts by {best_overall['team_name']}",
            f"{fmt_players(contributions['top_overall'])}",
            f"[b]Best Hitting[/b]: {best_hitting['hitting_points']:.1f} pts by {best_hitting['team_name']}",
            f"{fmt_players(contributions['top_hitters'])}",
            f"[b]Best Pitching[/b]: {best_pitching['pitching_points']:.1f} pts by {best_pitching['team_name']}",
            f"{fmt_players(contributions['top_pitchers'])}",
            f"",
            f"[b]Worst Overall[/b]: {worst_overall['total_points']:.1f} pts by {worst_overall['team_name']}",
            f"[b]Worst Hitting[/b]: {worst_hitting['hitting_points']:.1f} pts by {worst_hitting['team_name']}",
            f"[b]Worst Pitching[/b]: {worst_pitching['pitching_points']:.1f} pts by {worst_pitching['team_name']}",
        ]

    # Tough Luck
    tough_luck = find_tough_luck(scores, matchups)
    if tough_luck:
        lines.extend([
            f"",
            f"[b]Tough Luck[/b]: {tough_luck['team']} scored {tough_luck['points']:.1f} pts, "
            f"second most in the league, but lost to "
            f"{tough_luck['opponent']}'s {tough_luck['opponent_points']:.1f}",
        ])

    # Lucky Bastard
    lucky = find_lucky_bastard(scores, matchups)
    if lucky:
        
        lines.extend([
            f"",
            f"[b]Lucky Bastard[/b]: {lucky['team']} scored just {lucky['points']:.1f} pts, "
            f"second worst in the league, but beat "
            f"{lucky['opponent']}'s {lucky['opponent_points']:.1f}",
        ])

    # Fair and Just League
    if check_fair_and_just(scores, matchups):
        num_matchups = len(matchups) // 2
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
        f"",
        f"[i]*All records exclude matchups lasting longer than 7 days.[/i]",
        f"[i]*Scoring settings changed between 2025 and 2026 — all-time records reflect raw scores under each season's settings." 
        f"Future iterations will calculate scores according to current league settings, for now we just have the output as it existed at the time.[/i]",
    ])

    return "\n".join(lines)

if __name__ == "__main__":
    active_season = query_snowflake(
        "SELECT MAX(season_year) as sy FROM fct_weekly_team_scores"
    )[0]['sy']

    matchup_period, scores  = get_weekly_scores(active_season)
    matchups                = get_matchups(active_season, matchup_period)
    players                 = get_player_contributions(active_season, matchup_period)
    contributions           = get_contribution_callouts(scores, players)

    season_raw      = get_records(active_season, season_only=True)
    alltime_raw     = get_records(active_season, season_only=False)
    season_records  = format_records(season_raw)
    alltime_records = format_records(alltime_raw)

    summary = generate_summary(matchup_period, scores, matchups, contributions, season_records, alltime_records)
    print(summary)