"""
Generate the Google Sheets league almanac surface.

This is the v1.1 almanac entry point. It writes Home, Records, Team
Weeks, and one active-stats tab per fantasy team, with optional TSV
previews for review and golden-output regression.
"""

import argparse
import csv
import os
from pathlib import Path

import db
db.init()

import almanac_sheets
import sheets_target


def main():
    parser = argparse.ArgumentParser(
        description='Generate the league almanac Google Sheet.'
    )
    parser.add_argument('--season-year', type=int, default=None)
    parser.add_argument('--matchup-period', type=int, default=None)
    parser.add_argument(
        '--no-sheets', action='store_true',
        help='Print a preview instead of writing to any Sheet.',
    )
    parser.add_argument(
        '--prod', action='store_true',
        help='Write to the PRODUCTION sheet (SHEETS_PROD_ID). Default '
             'writes to the dev/testing sheet (SHEETS_DEV_ID).',
    )
    parser.add_argument(
        '--preview-dir',
        default=None,
        help='Write one TSV preview file per tab to this directory.',
    )
    parser.add_argument(
        '--print-all',
        action='store_true',
        help='With --no-sheets, print every tab instead of Home + first team tab.',
    )
    args = parser.parse_args()

    if (args.season_year is None) != (args.matchup_period is None):
        parser.error('--season-year and --matchup-period must be supplied together')

    season_year = args.season_year
    matchup_period = args.matchup_period
    if season_year is None:
        season_year, matchup_period = almanac_sheets.get_latest_matchup_period()

    league_id = os.getenv('LEAGUE_ID')
    records_rows = almanac_sheets.build_records_tab_rows(
        almanac_sheets.get_almanac_records('all_time'),
        almanac_sheets.get_almanac_records('current_season'),
        league_id=league_id,
    )
    team_week_stat_specs = almanac_sheets.get_team_week_stat_specs()
    team_weeks_rows = almanac_sheets.build_team_weeks_tab_rows(
        almanac_sheets.get_team_weeks(team_week_stat_specs),
        team_week_stat_specs,
        league_id=league_id,
    )
    team_tabs = almanac_sheets.build_team_history_tabs(
        almanac_sheets.get_team_roster_history_stats(season_year),
        season_year=season_year,
        league_id=league_id,
        slot_caps=almanac_sheets.get_roster_slot_capacities(
            season_year, include_inactive=True,
        ),
    )
    draft_rows = almanac_sheets.build_draft_tab_rows(
        almanac_sheets.get_draft_board(season_year), season_year, league_id=league_id,
    )
    advanced_standings_rows = almanac_sheets.build_advanced_standings_tab_rows(
        almanac_sheets.get_team_standings(season_year, team_week_stat_specs),
        almanac_sheets.get_team_slot_points(season_year),
        team_week_stat_specs,
        season_year,
    )
    # Home is built last among the data tabs: its nav band (#23) lists the
    # team tabs + Draft Recap. Preview has no real gids, so nav_targets stays
    # None -> nav cells render as plain tab-name text.
    home_data = almanac_sheets.get_home_tab_data(season_year, matchup_period)
    home_rows = almanac_sheets.build_home_tab_rows(
        **home_data,
        season_year=season_year,
        matchup_period=matchup_period,
        team_titles=[title for title, _ in team_tabs],
        league_id=league_id,
    )
    preview_tabs = [
        ('Home', home_rows),
        ('Records', records_rows),
        (almanac_sheets.TEAM_WEEKS_TAB, team_weeks_rows),
        (almanac_sheets.ADVANCED_STANDINGS_TAB, advanced_standings_rows),
        *team_tabs,
        (almanac_sheets.DRAFT_TAB, draft_rows),
    ]

    if args.preview_dir:
        _write_preview_dir(preview_tabs, args.preview_dir)

    if args.no_sheets:
        sheet_id, target_label = None, None
    else:
        try:
            sheet_id, target_label = sheets_target.resolve_sheets_target(args.prod)
        except RuntimeError as exc:
            parser.error(str(exc))

    if not sheet_id:
        print(
            "[almanac] preview only; not writing Sheets (--no-sheets set, "
            "or no dev sheet configured -- set SHEETS_DEV_ID in .env)"
        )
        _print_preview(preview_tabs, print_all=args.print_all)
        return

    if target_label == 'PROD':
        print(f"[almanac] >>> writing to PRODUCTION sheet: {sheet_id}")
    else:
        print(f"[almanac] writing to dev sheet: {sheet_id}")

    almanac_sheets.write_almanac(
        sheet_id,
        season_year=season_year,
        matchup_period=matchup_period,
    )


def _print_preview(tabs, print_all=False):
    """Print tab rows to stdout for quick PowerShell inspection."""
    tabs_to_print = tabs if print_all else tabs[:2]
    for title, rows in tabs_to_print:
        print(f"\n[{title}]")
        for row in rows:
            print("\t".join(str(cell) for cell in row))

    omitted = len(tabs) - len(tabs_to_print)
    if omitted:
        print(f"\n[almanac] {omitted} more tabs omitted from console preview")
        print("[almanac] use --print-all or --preview-dir to inspect every tab")


def _write_preview_dir(tabs, preview_dir):
    """Write one TSV file per generated tab."""
    out_dir = Path(preview_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for title, rows in tabs:
        path = out_dir / f"{_safe_filename(title)}.tsv"
        with path.open('w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f, delimiter='\t', lineterminator='\n')
            writer.writerows(rows)

    print(f"[almanac] wrote {len(tabs)} preview TSV files to {out_dir}")


def _safe_filename(value):
    safe = ''.join(c if c.isalnum() or c in (' ', '-', '_') else '-' for c in value)
    safe = '-'.join(safe.split())
    return safe[:100] or 'Sheet'


if __name__ == '__main__':
    main()
