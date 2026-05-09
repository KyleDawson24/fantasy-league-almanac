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
import sys
from datetime import datetime

from dotenv import load_dotenv

from formatters import (
    fmt_value, fmt_ip, fmt_record_value,
    format_contributors, STAT_DISPLAY,
)
import records

load_dotenv()

# Phase 6.3.3 chunk 4.5: force utf-8 stdout. Windows defaults to cp1252
# which crashes on team names with emoji (e.g. "Team Hybrid<emoji>").
# When the script crashes mid-print, the Sheets sink never fires and
# the Sheet stays stale -- a silent failure mode that masquerades as
# "the new stats aren't showing up". Idempotent; safe to repeat.
try:
    sys.stdout.reconfigure(encoding='utf-8')
except (AttributeError, OSError):
    pass


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
    'GDP', 'B_IBB',
    # Pitching
    'W', 'L', 'SV', 'HLD', 'QS', 'CG',
    'K', 'OUTS', 'ER', 'P_H', 'P_BB', 'P_HR', 'P_R',
    'BLK', 'WP',
    'HBP_P', 'BLSV', 'NH', 'PG', 'PK', 'SHO',
]

# Stats this BBCode report doesn't render. Phase 6.3.3 chunk 2 added
# rate stats / wasted_points / derived counting stats to the mart, but
# this report's per-stat contributor query (get_team_contributors)
# interpolates the stat_name as a fct_weekly_player_performance column
# and assumes a per-player breakdown story exists -- neither holds for
# rates / wasted / derived. The new Sheets dump (chunks 5-6) handles
# these via get_records_with_contributors() instead.
_REPORT_EXCLUDED_STATS = frozenset({
    'ERA', 'WHIP', 'K_PER_9', 'K_PER_BB', 'HR_PER_9', 'BB_PER_9',
    'WASTED_POINTS',
    'PA', 'SB_CS', 'W_L', 'SV_BLSV',
})


def order_stats_for_display(stat_names):
    """STAT_ORDER first (preserving its order), then anything else
    alphabetically. Phase 6.2: extracted from get_tracked_team_stats so
    the ordering rule lives in this script (records.py is data-only).
    Phase 6.3.3: chunk 2's mart-layer additions (rate stats / wasted /
    derived) are dropped here since this report can't render them; the
    Sheets dump pipeline handles those via the new orchestrator."""
    filtered = [s for s in stat_names if s not in _REPORT_EXCLUDED_STATS]
    ordered = [s for s in STAT_ORDER if s in filtered]
    leftover = sorted(set(filtered) - set(STAT_ORDER))
    return ordered + leftover


# ---------- formatting helpers ----------
# fmt_value and format_contributors moved to output/formatters.py (Phase 5)
# so the new-record callouts in generate_summary.py can share them.


def fmt_team_in_week(row, schedule_lookup):
    """e.g., 'Island Daddys in Week 2 of 2025' or 'The Hosston Hosstros
    in Round 1 of 2025' for playoff weeks. Phase 6.3.3 chunk 6: switched
    from 'Matchup #N' to format_week_label() output (Week N for regular
    weeks, playoff round name for playoff weeks)."""
    week = records.format_week_label(
        row['season_year'], row['matchup_period'], schedule_lookup,
    )
    return f"{row['team_name']} in {week} of {row['season_year']}"


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


def format_record(stat_name, holders, schedule_lookup):
    """Format the full block (1 or 2 lines) for a single stat record."""
    if not holders:
        return None

    display = STAT_DISPLAY.get(stat_name, stat_name)
    tiers = split_tiers(holders)
    top_tier = tiers[0]
    record_value = top_tier[0]['stat_value']
    # Phase 6.3.3 chunk 6.5: stat-aware value rendering. OUTS records
    # display as baseball IP (88.1) instead of raw outs (265). Contributor
    # lists below pass value_fmt=fmt_ip for the same reason.
    record_str = fmt_record_value(stat_name, record_value)
    contrib_value_fmt = fmt_ip if stat_name == 'OUTS' else None

    lines = []

    if len(top_tier) == 1:
        # Single record holder -- include contributor breakout
        holder = top_tier[0]
        lines.append(
            f"[b]{display}[/b]: {record_str} by {fmt_team_in_week(holder, schedule_lookup)}"
        )
        contributors = records.get_team_contributors(
            holder['season_year'], holder['matchup_period'],
            holder['team_id'], stat_name,
        )
        contrib_str = format_contributors(contributors, value_fmt=contrib_value_fmt)
        if contrib_str:
            lines.append(contrib_str)
    else:
        # Multi-team tie at the record -- list all, point to runner-up tier
        team_descs = ", ".join(fmt_team_in_week(t, schedule_lookup) for t in top_tier)
        lines.append(f"[b]{display}[/b]: {record_str} by {team_descs}")

        if len(tiers) > 1:
            second_tier = tiers[1]
            second_value = second_tier[0]['stat_value']
            second_teams = ", ".join(fmt_team_in_week(t, schedule_lookup) for t in second_tier)
            lines.append(
                f"Second place: {fmt_record_value(stat_name, second_value)} held by {second_teams}"
            )

    return "\n".join(lines)


def main():
    tracked = records.get_tracked_team_stats()
    stats = order_stats_for_display(tracked)
    schedule_lookup = records.load_schedule_lookup()

    output_lines = ["[u][b]All-Time Team Records[/b][/u]", ""]

    for stat_name in stats:
        holders = records.get_record_top_n(stat_name, grain='team',
                                           direction='most', scope='all_time',
                                           limit=10)
        block = format_record(stat_name, holders, schedule_lookup)
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

    # Phase 6.3: optional Sheets sink. Opt-in via SHEETS_OUTPUT_ID env var.
    # Skipped silently (with an info log) when not configured. The Sheets
    # writer is its own module so the import only fires when actually used
    # -- keeps the dependency footprint of the records report minimal for
    # users who don't enable Sheets.
    sheets_id = os.getenv("SHEETS_OUTPUT_ID")
    if sheets_id:
        import sheets_writer
        try:
            sheets_writer.write_records(sheets_id)
        except Exception as e:
            print(f"[sheets] write failed: {e}")
    else:
        print("[sheets] SHEETS_OUTPUT_ID not set; skipping Sheets sink")


if __name__ == "__main__":
    main()
