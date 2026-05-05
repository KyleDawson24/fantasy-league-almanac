"""
generate_records_report.py

For each team-level stat in the league all-time leaderboard, print:
  - the record value, holder team, and matchup week
  - top 3 contributing players from that team's record-setting matchup

Tie handling:
  - Multiple teams tied at the record value: list all tied teams, skip
    contributor breakout, show "second place" with the next-tier holders
  - Tied contributors that would push the top-3 list past 3: switch to
    count-based formatting (e.g., "5 others with 4")
  - Fewer than 3 non-zero contributors with zero-value teammates: append
    "N others with 0"

Phase 5: score-level records (Total / Hitting / Pitching Points) now use
the calculated_* columns rather than platform_*. Calculated_* applies
the current season's scoring weights to historical stat lines, giving
us an apples-to-apples cross-season comparison. The platform_* columns
still live in mart_stat_leaderboard for diagnostic lookups.

Phase 6.2: data access (leaderboard reads, contributor lookups) moved to
output/records.py. This script keeps formatting and the iteration shape.
"""

import os
from datetime import datetime

from formatters import fmt_value, format_contributors, STAT_DISPLAY
import records


# Display ordering for stat names. Anything not in this list still gets
# reported (with stat_name as the display) but is appended to the end.
#
# Phase 5: score-level records flipped from PLATFORM_* to CALCULATED_*.
# We care about scores under today's settings rather than what a previous
# season's then-current weights happened to call a total. PLATFORM_* still
# lives in mart_stat_leaderboard for cross-season comparison and is just
# omitted from STAT_ORDER (anything not listed gets appended at the end
# with raw stat_name as its display label, which is fine for diagnostic).
STAT_ORDER = [
    'CALCULATED_POINTS', 'CALCULATED_HITTING_PTS', 'CALCULATED_PITCHING_PTS',
    # Hitting
    'HR', 'RBI', 'R', 'H', 'TB', 'XBH',
    'DOUBLES', 'TRIPLES', 'SINGLES',
    'SB', 'CS', 'B_BB', 'B_SO', 'HBP', 'SF', 'AB',
    # Pitching
    'W', 'L', 'SV', 'HLD', 'QS', 'CG',
    'K', 'OUTS', 'ER', 'P_H', 'P_BB', 'P_HR', 'P_R',
    'BLK', 'WP',
]


def order_stats_for_display(stat_names):
    """STAT_ORDER first (preserving its order), then anything else
    alphabetically. Phase 6.2: extracted from get_tracked_team_stats so
    the ordering rule lives in this script (records.py is data-only)."""
    ordered = [s for s in STAT_ORDER if s in stat_names]
    leftover = sorted(set(stat_names) - set(STAT_ORDER))
    return ordered + leftover


# ---------- formatting helpers ----------
# fmt_value and format_contributors moved to output/formatters.py (Phase 5)
# so the new-record callouts in generate_summary.py can share them.


def fmt_team_in_week(row):
    """e.g., 'Island Daddys in Matchup #2 of 2025'."""
    return f"{row['team_name']} in Matchup #{row['matchup_period']} of {row['season_year']}"


def split_tiers(rows):
    """Group consecutive rows with identical stat_value (rows assumed sorted by rank asc)."""
    if not rows:
        return []
    tiers = [[rows[0]]]
    for row in rows[1:]:
        if row['stat_value'] == tiers[-1][0]['stat_value']:
            tiers[-1].append(row)
        else:
            tiers.append([row])
    return tiers


def format_record(stat_name, holders):
    """Format the full block (1 or 2 lines) for a single stat record."""
    if not holders:
        return None

    display = STAT_DISPLAY.get(stat_name, stat_name)
    tiers = split_tiers(holders)
    top_tier = tiers[0]
    record_value = top_tier[0]['stat_value']
    record_str = fmt_value(record_value)

    lines = []

    if len(top_tier) == 1:
        # Single record holder -- include contributor breakout
        holder = top_tier[0]
        lines.append(
            f"[b]{display}[/b]: {record_str} by {fmt_team_in_week(holder)}"
        )
        contributors = records.get_team_contributors(
            holder['season_year'], holder['matchup_period'],
            holder['team_id'], stat_name,
        )
        contrib_str = format_contributors(contributors)
        if contrib_str:
            lines.append(contrib_str)
    else:
        # Multi-team tie at the record -- list all, point to runner-up tier
        team_descs = ", ".join(fmt_team_in_week(t) for t in top_tier)
        lines.append(f"[b]{display}[/b]: {record_str} by {team_descs}")

        if len(tiers) > 1:
            second_tier = tiers[1]
            second_value = second_tier[0]['stat_value']
            second_teams = ", ".join(fmt_team_in_week(t) for t in second_tier)
            lines.append(
                f"Second place: {fmt_value(second_value)} held by {second_teams}"
            )

    return "\n".join(lines)


def main():
    tracked = records.get_tracked_team_stats()
    stats = order_stats_for_display(tracked)

    output_lines = ["[u][b]All-Time Team Records[/b][/u]", ""]

    for stat_name in stats:
        holders = records.get_record_top_n(stat_name, grain='team',
                                           direction='best', scope='all_time',
                                           limit=10)
        block = format_record(stat_name, holders)
        if block:
            output_lines.append(block)
            output_lines.append("")  # blank line between records

    summary = "\n".join(output_lines).rstrip() + "\n"
    print(summary)

    # Write log
    log_dir = os.path.join(os.path.dirname(__file__), "..", "output", "logs")
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    log_path = os.path.join(log_dir, f"records_report_{timestamp}.txt")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(summary)
    print(f"Log saved to: {log_path}")


if __name__ == "__main__":
    main()
