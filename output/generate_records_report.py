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

import argparse
import os
from datetime import datetime

# Phase 7: shared script startup (utf-8 stdout reconfig, load_dotenv,
# Snowflake config) lives in db.py.
import db
db.init()

from formatters import (
    fmt_value, fmt_ip, fmt_record_value,
    format_contributors,
)
import records
import sheets_target
import stat_catalog


# When the rank-1 tier has more than this many members, collapse the
# tier display to "X across N team-weeks" rather than enumerating every
# tied team. Mirrors the floor-noise pattern handled by the records-with-
# contributors orchestrator on the Sheets side; this is the BBCode-side
# equivalent. Keep low (3) to match formatters.format_contributors's
# own inline-collapse threshold so reads consistent across outputs.
INLINE_TIER_LIMIT = 3


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
# interpolates the stat_name as a fct_player_weekly_active_performance column
# and assumes a per-player breakdown story exists -- neither holds for
# rates / wasted / derived. The new Sheets dump (chunks 5-6) handles
# these via get_records_with_contributors() instead.
_REPORT_EXCLUDED_STATS = frozenset({
    # Pitching rate stats -- this report's per-stat contributor query
    # interpolates stat_name as a player-fact column and assumes a per-
    # player breakdown story exists; rates don't.
    'ERA', 'WHIP', 'K_PER_9', 'K_PER_BB', 'HR_PER_9', 'BB_PER_9',
    # Hitting rate stats -- v1.1.1 promoted these to is_record_candidate=
    # true so they enter mart_stat_leaderboard (with the qualifier_min
    # filter applied) for the almanac. Same per-player-breakdown
    # limitation applies to this report; exclude from display.
    'AVG', 'OBP', 'SLG', 'OPS',
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

    display = stat_catalog.get_display_map().get(stat_name, stat_name)
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
    elif len(top_tier) <= INLINE_TIER_LIMIT:
        # Small tied tier -- list every team, drop contributor breakout.
        team_descs = ", ".join(fmt_team_in_week(t, schedule_lookup) for t in top_tier)
        lines.append(f"[b]{display}[/b]: {record_str} by {team_descs}")

        if len(tiers) > 1:
            second_tier = tiers[1]
            second_value = second_tier[0]['stat_value']
            second_teams = ", ".join(fmt_team_in_week(t, schedule_lookup) for t in second_tier)
            lines.append(
                f"Second place: {fmt_record_value(stat_name, second_value)} held by {second_teams}"
            )
    else:
        # Long-tail tie -- typically value=0 stats (NH/PG/SHO/etc. that no
        # team has ever recorded). Collapse to a count instead of listing
        # 10+ identical entries. Use league_history_count for the accurate
        # team-week count; fall back to the visible tier size + suffix when
        # the stat isn't fct-countable (rate stats / WASTED_POINTS — these
        # don't reach this report anyway via _REPORT_EXCLUDED_STATS, but
        # the fallback keeps the renderer total-function).
        count = records.count_value_occurrences('team', stat_name, record_value)
        count_str = f"{count}" if count is not None else f"{len(top_tier)}+"

        # v1.x: floor-zero rendering. When the rank-1 value is 0 on a
        # positive-polarity stat, "0 across N team-weeks" reads as if a
        # record exists when actually no team has ever recorded the stat
        # at all (NH/PG/CYC are the typical cases). Render explicitly as
        # "None recorded yet" instead. Negative-polarity stats with
        # rank-1=0 in the 'most' direction (e.g., "team with the most
        # errors had 0 errors") are legitimate good-record cases and
        # keep the count rendering.
        polarity = stat_catalog.get_polarity_map().get(stat_name, 'positive')
        if record_value == 0 and polarity == 'positive':
            lines.append(
                f"[b]{display}[/b]: None recorded yet (across {count_str} team-weeks)"
            )
        else:
            lines.append(
                f"[b]{display}[/b]: {record_str} across {count_str} team-weeks"
            )
        # Skip second-place tier when collapsing -- tier 2 at a long-tail
        # floor is usually also a tied long tail (e.g., 0 in tier 1, 1 in
        # tier 2 with another 200 ties), and adds noise rather than insight.

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description='Generate the all-time team records BBCode report. '
                    'Optionally writes to a Google Sheet -- the dev sheet '
                    '(SHEETS_DEV_ID) by default, or production with --prod.',
    )
    parser.add_argument(
        '--no-sheets', action='store_true',
        help='Suppress the Sheets sink entirely. Useful for verification '
             'runs that should not touch any sheet. (load_dotenv '
             'repopulates env-var-unsetting attempts, so this flag is the '
             'canonical suppression mechanism.)',
    )
    parser.add_argument(
        '--prod', action='store_true',
        help='Write to the PRODUCTION sheet (SHEETS_PROD_ID). Default '
             'writes to the dev/testing sheet (SHEETS_DEV_ID).',
    )
    parser.add_argument(
        '--league', default=None, metavar='LEAGUE_KEY',
        help='League registry key to render (config/leagues.yml). '
             'Default: the registry\'s default_league (the ESPN league).',
    )
    args = parser.parse_args()
    db.set_league(args.league)

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

    # Phase 6.3 / v1.3: optional Sheets sink. Default target is the dev
    # sheet (SHEETS_DEV_ID); --prod selects production (SHEETS_PROD_ID, or
    # the legacy SHEETS_OUTPUT_ID). Skipped when no target is configured
    # or --no-sheets is passed. The writer imports lazily so its
    # dependency footprint only loads when actually writing.
    if args.no_sheets:
        print("[sheets] --no-sheets flag set; suppressing Sheets sink")
    else:
        try:
            sheets_id, target_label = sheets_target.resolve_sheets_target(args.prod)
        except RuntimeError as exc:
            parser.error(str(exc))
        if not sheets_id:
            print("[sheets] no dev sheet configured (set SHEETS_DEV_ID in "
                  ".env); skipping Sheets sink")
        else:
            if target_label == 'PROD':
                print(f"[sheets] >>> writing records to PRODUCTION sheet: {sheets_id}")
            else:
                print(f"[sheets] writing records to dev sheet: {sheets_id}")
            import sheets_writer
            try:
                sheets_writer.write_records(sheets_id)
            except Exception as e:
                print(f"[sheets] write failed: {e}")


if __name__ == "__main__":
    main()
