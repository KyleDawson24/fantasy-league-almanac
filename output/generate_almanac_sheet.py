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

import almanac_data
import almanac_sheets
import cbs_almanac_sheets
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
    parser.add_argument(
        '--league', default=None, metavar='LEAGUE_KEY',
        help='League registry key to render (config/leagues.yml). '
             'Default: the registry\'s default_league (the ESPN league).',
    )
    parser.add_argument(
        '--include-trades',
        action='store_true',
        help='Include the live-API Trades tab in previews. Off by default so '
             'preview runs (and the pinned byte-diff anchor) stay '
             'network-free; the Sheets write always includes the tab.',
    )
    parser.add_argument(
        '--duckdb', nargs='?', const=True, default=None, metavar='PATH',
        help='Read from a local DuckDB file instead of Snowflake. PATH '
             'defaults to DBT_DUCKDB_PATH, then to the location the dbt '
             'profile writes. No Snowflake account or driver is needed.',
    )
    args = parser.parse_args()
    if args.duckdb:
        db.use_duckdb(None if args.duckdb is True else args.duckdb)
    db.set_league(args.league)

    # Format dispatch by DATA PRESENCE (the format-modularity rule): a
    # league with delivered period standings is a points league -- no
    # matchups exist, so the H2H almanac shape below cannot apply. The
    # ESPN league has no period-standings rows and flows on unchanged.
    if cbs_almanac_sheets.is_points_league():
        _run_points_league_almanac(args, parser)
        return

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
        hall_of_fame=almanac_sheets.get_franchise_hall_of_fame(),
        hall_of_shame=almanac_sheets.get_wasted_hall_of_shame(),
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
        best_seasons_fn=almanac_data.team_best_seasons_fn(),
    )
    draft_rows = almanac_sheets.build_draft_tab_rows(
        almanac_sheets.get_draft_board(season_year), season_year, league_id=league_id,
        history_rows=almanac_sheets.get_draft_history_boards(season_year),
        season_clocks={r['season_year']: r['clock']
                       for r in almanac_sheets.get_season_scoring_periods()},
    )
    advanced_standings_rows = almanac_sheets.build_advanced_standings_tab_rows(
        almanac_sheets.get_team_standings(season_year, team_week_stat_specs),
        almanac_sheets.get_team_slot_points(season_year),
        team_week_stat_specs,
        season_year,
        acquisition_rows=almanac_sheets.get_team_acquisition_channels(season_year),
        slot_rows_alltime=almanac_sheets.get_team_slot_points_alltime(),
        affinity_rows=almanac_sheets.get_team_affinity_weights(season_year),
        rank_arc_rows=almanac_sheets.get_team_rank_arc(season_year),
        finishes_rows=almanac_sheets.get_espn_season_finishes(),
        standings_rows_alltime=almanac_sheets.get_team_standings_alltime(
            team_week_stat_specs),
        acquisition_rows_alltime=(
            almanac_sheets.get_team_acquisition_channels_alltime()),
    )
    trades_rows = None
    if args.include_trades:
        trades_rows = almanac_sheets.build_trades_tab_rows(
            almanac_sheets.get_trades_tab_data(season_year), season_year,
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
        *([(almanac_sheets.TRADES_TAB, trades_rows)]
          if trades_rows is not None else []),
        *team_tabs,
        (almanac_sheets.DRAFT_TAB, draft_rows),
    ]

    if args.preview_dir:
        _write_preview_dir(preview_tabs, args.preview_dir)

    if args.no_sheets:
        sheet_id, target_label = None, None
    else:
        try:
            sheet_id, target_label = sheets_target.resolve_sheets_target(
                args.prod, db.league())
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


def _run_points_league_almanac(args, parser):
    """The points-league almanac path (MLB-66 v2): the ESPN-architecture
    workbook (nav-first Home + Records + Standings + Best-Lineup team
    pages) on the unified fact family. Same preview / dev-default /
    explicit-prod UX as the H2H path; the sheet resolves from the
    league's registry sinks (MLB-58). --season-year / --matchup-period
    are H2H concepts and are ignored here (the points almanac's horizon
    comes from the data)."""
    # Resolve the sink FIRST, then decide what to build (MLB-201).
    #
    # This used to build preview tabs under `--preview-dir or --no-sheets`
    # and then fall back to printing them whenever no sheet id resolved --
    # so a run with NEITHER flag and no dev sheet configured reached
    # `_print_preview(preview_tabs)` with preview_tabs unbound and died on
    # an UnboundLocalError. That is a stranger's literal first render: no
    # SHEETS_DEV_ID in .env, no flags typed. The two decisions have to
    # happen in this order, because whether a preview is needed depends on
    # where the output is going.
    if args.no_sheets:
        sheet_id, target_label = None, None
    else:
        try:
            sheet_id, target_label = sheets_target.resolve_sheets_target(
                args.prod, db.league())
        except RuntimeError as exc:
            parser.error(str(exc))

    # Built whenever the preview path is reachable: explicitly asked for, or
    # implied because there is no sheet to write to.
    if args.preview_dir or not sheet_id:
        tabs, _, _ = cbs_almanac_sheets.build_all_tabs()
        preview_tabs = [(title, rows) for title, rows, _ in tabs]
        if args.preview_dir:
            _write_preview_dir(preview_tabs, args.preview_dir)

    if not sheet_id:
        print(
            f"[almanac] preview only; not writing Sheets (--no-sheets set, "
            f"or no dev sheet configured for league '{db.league_key()}')"
        )
        _print_preview(preview_tabs, print_all=args.print_all)
        return

    if target_label == 'PROD':
        print(f"[almanac] >>> writing to PRODUCTION sheet: {sheet_id}")
    else:
        print(f"[almanac] writing to dev sheet: {sheet_id}")

    # write_cbs_almanac builds its own tabs: the two-pass nav-link write
    # needs the real sheet gids before Home's rows exist.
    cbs_almanac_sheets.write_cbs_almanac(sheet_id)


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
