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
import league_format
import points_almanac
import sheets_auth
import sheets_target
import sheets_workbook


# The title an app-created workbook gets when the caller names none.
# Deliberately generic: it is the first thing a stranger sees in their
# own Drive, and a default carrying a league or owner name would publish
# an identity nobody asked to publish.
DEFAULT_PUBLIC_WORKBOOK_TITLE = 'Fantasy League Almanac'


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
        '--new-public-workbook', action='store_true',
        help='STRANGER PATH (MLB-209). Create a BRAND-NEW Google workbook '
             'under the public drive.file OAuth profile, render the almanac '
             'into it, set it to anyone-with-the-link VIEWER, and print the '
             'share URL. Does not read or touch any configured dev/prod '
             'sheet, and cannot be combined with --prod.',
    )
    parser.add_argument(
        '--public-workbook-title', default=None, metavar='TITLE',
        help='Title for the workbook --new-public-workbook creates. '
             'Defaults to a generic almanac title carrying no league or '
             'owner identity.',
    )
    parser.add_argument(
        '--new-public-workbook-force', action='store_true',
        help='With --new-public-workbook, create a fresh workbook even if a '
             'previous run left an unfinished one with the same title. '
             'Default is to RESUME that one, so a retry after a partial '
             'failure does not pile up spreadsheets.',
    )
    parser.add_argument(
        '--confirm-link-sharing', action='store_true',
        help='With --new-public-workbook, affirm the anyone-with-the-link '
             'disclosure non-interactively. Intended for automation; without '
             'it the app prompts immediately before changing sharing.',
    )
    parser.add_argument(
        '--advanced-snowflake', action='store_true',
        help='With --new-public-workbook, deliberately use the advanced '
             'Snowflake source. The public path otherwise forces local '
             'DuckDB; this flag cannot be combined with --duckdb.',
    )
    parser.add_argument(
        '--duckdb', nargs='?', const=True, default=None, metavar='PATH',
        help='Read from a local DuckDB file instead of Snowflake. PATH '
             'defaults to DBT_DUCKDB_PATH, then to the location the dbt '
             'profile writes. No Snowflake account or driver is needed.',
    )
    args = parser.parse_args()
    _validate_public_workbook_args(args, parser)
    _configure_data_source(args)
    db.set_league(args.league)
    _generate(args, parser)


def _configure_data_source(args):
    """Keep the stranger default local; Snowflake requires an opt-in."""
    if args.new_public_workbook and not args.advanced_snowflake:
        db.use_duckdb(None if args.duckdb in (None, True) else args.duckdb)
    elif args.duckdb:
        db.use_duckdb(None if args.duckdb is True else args.duckdb)


def _generate(args, parser):
    """Generate after argument validation and data-source selection."""

    # FORMAT DECIDES THE WORKBOOK; PLATFORM DECIDES THE DATA (MLB-243).
    #
    # This used to dispatch on whether `mart_period_standings` had rows --
    # a CBS-shaped feed standing in for "is this a points league". An ESPN
    # season-long points league (currentLeagueType = 5) delivers no period
    # standings, so it read as H2H and the first stranger rehearsal was
    # handed a matchup almanac over a league that has never played a
    # matchup. The canonical answer lives in dim_league_format and is read
    # in exactly one place now (output/league_format.py), which also means
    # an UNKNOWN format raises instead of quietly selecting H2H.
    try:
        fmt = league_format.resolve()
    except league_format.LeagueFormatError as exc:
        parser.error(str(exc))
        return                                     # unreachable; parser exits

    if fmt == league_format.POINTS:
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
        rivalry_axes=almanac_sheets.get_rivalry_axes(),
        rivalry_pairs=almanac_sheets.get_rivalry_matrix(),
        eligibility_rows=almanac_sheets.get_team_position_eligibility(
            season_year),
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

    # The stranger path owns its own destination: it CREATES one. It
    # deliberately returns before target resolution, so no configured
    # dev/prod workbook is read, opened, or written on this path.
    if args.new_public_workbook:
        publish_new_public_workbook(args, season_year, matchup_period)
        return

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


def _validate_public_workbook_args(args, parser):
    """Reject the flag combinations that would mean two different things
    at once, before any work happens.

    --prod and --new-public-workbook are opposite answers to the same
    question (which workbook?), and letting one silently win is how a
    stranger-path run ends up rewriting the live league book.
    """
    if args.new_public_workbook:
        if args.prod:
            parser.error(
                '--new-public-workbook creates a brand-new workbook and '
                '--prod writes to the configured production sheet; pick one.'
            )
        if args.no_sheets:
            parser.error(
                '--new-public-workbook writes to Sheets by definition; it '
                'cannot be combined with --no-sheets.'
            )
        if args.advanced_snowflake and args.duckdb:
            parser.error(
                '--advanced-snowflake deliberately selects Snowflake and '
                '--duckdb selects local DuckDB; pick one.'
            )
        return

    if args.public_workbook_title is not None:
        parser.error(
            '--public-workbook-title only applies with --new-public-workbook.'
        )
    if args.new_public_workbook_force:
        parser.error(
            '--new-public-workbook-force only applies with '
            '--new-public-workbook.'
        )
    if args.confirm_link_sharing:
        parser.error(
            '--confirm-link-sharing only applies with --new-public-workbook.'
        )
    if args.advanced_snowflake:
        parser.error(
            '--advanced-snowflake only applies with --new-public-workbook.'
        )


def safe_workbook_title(raw):
    """Normalize a caller-supplied workbook title.

    Drive accepts almost anything, which is the problem: a title is the
    one piece of this workbook visible before it is opened. Control
    characters are stripped, whitespace collapsed, length capped, and an
    empty result falls back to the generic default rather than creating
    an untitled file.
    """
    if not raw:
        return DEFAULT_PUBLIC_WORKBOOK_TITLE
    cleaned = ' '.join(
        ''.join(c for c in str(raw) if c.isprintable()).split()
    )
    return cleaned[:120] or DEFAULT_PUBLIC_WORKBOOK_TITLE


def publish_new_public_workbook(args, season_year, matchup_period,
                                client=None, publish=None, input_fn=None,
                                render=None):
    """The stranger path (MLB-209): create a workbook, render into it,
    share it, announce it.

    Authorization comes from the PUBLIC profile -- `drive.file` only --
    and the same client both creates the workbook and renders into it,
    because under that scope no other client may open the file.

    `client` / `publish` exist so the sequencing can be tested without
    Google. Nothing here reads a configured sheet id.

    The OAuth-scope disclosure is printed before anything can open a browser.
    The separate data-sharing disclosure is printed after rendering and
    immediately before the permission call, where an affirmative answer is
    required.

    `render` (MLB-243) supplies the FORMAT'S renderer, called with
    (spreadsheet_id, client). It changes what is drawn inside the
    workbook and nothing about how the workbook is obtained: the same
    public profile, the same Credential Locker token, the same
    create -> render -> confirm -> share -> read-back sequence, in the
    same order. A points league needed a different renderer, never a
    different security path -- which is precisely why the wiring goes here
    rather than a parallel publish function. Defaults to the H2H almanac
    so every existing caller is unchanged.
    """
    title = safe_workbook_title(args.public_workbook_title)
    input_fn = input_fn or input
    print(f'[almanac] {sheets_auth.consent_disclosure(sheets_auth.PUBLIC)}')
    if client is None:
        client = _authorize_public_client()
    if publish is None:
        publish = sheets_workbook.publish_workbook

    def _render(spreadsheet_id):
        if render is not None:
            render(spreadsheet_id, client)
            return
        almanac_sheets.write_almanac(
            spreadsheet_id,
            season_year=season_year,
            matchup_period=matchup_period,
            client=client,
        )

    def _confirm_share():
        print(f'[almanac] {sheets_workbook.LINK_SHARING_DISCLOSURE}')
        if args.confirm_link_sharing:
            print('[almanac] link sharing affirmed by '
                  '--confirm-link-sharing')
            return True
        answer = input_fn(
            '[almanac] Set this workbook to anyone-with-the-link VIEWER? '
            'Type YES to continue: '
        )
        return answer.strip().casefold() == 'yes'

    print(f"[almanac] creating a new workbook titled {title!r}")
    result = publish(
        client, title, _render,
        resume=not args.new_public_workbook_force,
        confirm_share=_confirm_share,
    )
    report_publish_result(result)
    return result


def _authorize_public_client():
    """Authorize as the published app, turning an auth failure into an
    exit code and one sentence.

    Without this the two ways authorization can fail both surface as a
    raw traceback, which is a bad way to tell somebody their install is
    incomplete. `sheets_auth` raises `RuntimeError` for exactly two
    things and both are the user's business rather than a bug:

      - no usable identity (the descriptor did not ship, or an explicit
        GOOGLE_PUBLIC_OAUTH_CLIENT_PATH points nowhere) -- raised by the
        resolver, so no browser has opened;
      - a consent that granted the wrong scopes -- raised after the
        browser, with its own instructions.

    Exit 2 rather than 1: argparse's usage-error code, which is what this
    is from the outside -- the command as invoked cannot run.
    """
    try:
        return sheets_auth.authorized_client(sheets_auth.PUBLIC)
    except RuntimeError as exc:
        print(f'[almanac] {exc}')
        raise SystemExit(2)


def report_publish_result(result):
    """Print the outcome.

    Delegates so the share-ready rule has one home (sheets_workbook):
    this generator and the consent probe must not be able to disagree
    about when the line is earned.
    """
    sheets_workbook.report_result(result)


def _points_adapter(parser):
    """Pick the platform's data adapter for the points workbook.

    THIS IS THE ONE PLACE PLATFORM IS ALLOWED TO DECIDE SOMETHING, and it
    decides only where the numbers come from. The SHAPE was already fixed
    by dim_league_format before we got here. Both adapters render the same
    points-format product; they differ because CBS and ESPN deliver
    genuinely different feeds (CBS has period standings and a UI-parsed
    history; ESPN has the shared daily facts and no period standings at
    all). Choosing an adapter by platform is correct. Choosing a WORKBOOK
    by platform is the bug this ticket fixes.

    Returns (build_preview, write, public_render). `public_render` is None
    for an adapter whose writer cannot render into an app-created workbook
    -- it must accept the caller's `drive.file` client, because under that
    scope no other client may open the file.
    """
    platform = db.league().platform
    if platform == 'cbs':
        # CBS has no live-API tab, so the flag is accepted and ignored
        # rather than left off the signature -- normalizing here means the
        # caller never has to know which adapter it got, and no call site
        # needs an exception handler that could swallow a real TypeError.
        def _cbs_preview(include_trades=False):
            del include_trades
            return cbs_almanac_sheets.build_all_tabs()
        # No public renderer: write_cbs_almanac authorizes its own
        # maintainer client and takes none. CBS also has no scripted
        # credential path at all (its token is browser-extracted from
        # behind a reCAPTCHA), so the stranger journey does not reach it.
        return _cbs_preview, cbs_almanac_sheets.write_cbs_almanac, None
    if platform == 'espn':
        # Previews stay network-free unless --include-trades is typed, the
        # same rule the H2H path follows; the Sheets write always includes
        # the live tab.
        def _espn_preview(include_trades=False):
            return points_almanac.build_all_tabs(include_trades=include_trades)
        return (_espn_preview, points_almanac.write_points_almanac,
                points_almanac.write_points_almanac)
    parser.error(
        f"League '{db.league_key()}' is a points-format league on platform "
        f"'{platform}', which has no points-format data adapter yet. Add one "
        f"rather than rendering another platform's queries against it."
    )


def _build_preview(args, build_tabs):
    """Preview tabs from whichever adapter is active. Both accept the
    live-tab flag; only one of them has a live tab to gate."""
    return build_tabs(include_trades=args.include_trades)[0]


def _run_points_league_almanac(args, parser):
    """The points-league almanac path (MLB-66 v2, generalized MLB-243):
    the ESPN-architecture workbook (nav-first Home + Records + Standings +
    Best-Lineup team pages) on the unified fact family. Same preview /
    dev-default / explicit-prod UX as the H2H path; the sheet resolves
    from the league's registry sinks (MLB-58). --season-year /
    --matchup-period are H2H concepts and are ignored here (the points
    almanac's horizon comes from the data)."""
    build_tabs, write_tabs, public_render = _points_adapter(parser)

    # The stranger path owns its own destination: it CREATES one. Handled
    # before target resolution so no configured dev/prod workbook is read,
    # opened or written on this path -- the same ordering the H2H branch
    # relies on, and the reason --new-public-workbook can no longer be
    # refused here (MLB-243). A points league is a workbook shape, not a
    # second-class journey.
    if args.new_public_workbook:
        if public_render is None:
            parser.error(
                f"League '{db.league_key()}' renders the points almanac "
                f"through an adapter that cannot write into an app-created "
                f"workbook: the public path authorizes with drive.file, "
                f"under which only the client that created the file may "
                f"open it, and this adapter authorizes its own. Use the "
                f"configured dev/prod sheet for this league."
            )
        if args.preview_dir:
            preview_tabs = [(title, rows)
                            for title, rows, *_ in _build_preview(args, build_tabs)]
            _write_preview_dir(preview_tabs, args.preview_dir)
        publish_new_public_workbook(
            args, args.season_year, args.matchup_period,
            render=lambda spreadsheet_id, client: public_render(
                spreadsheet_id, client=client),
        )
        return

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
        # CBS hands back (title, rows, formats); the ESPN points assembly
        # has no per-tab format payload and hands back (title, rows). Both
        # previews want the first two.
        preview_tabs = [(title, rows)
                        for title, rows, *_ in _build_preview(args, build_tabs)]
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

    # The writer builds its own tabs: the two-pass nav-link write needs the
    # real sheet gids before Home's rows exist.
    write_tabs(sheet_id)


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
