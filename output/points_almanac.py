"""The points-format almanac for an ESPN league (MLB-243).

WHAT THIS IS. The shared points-format WORKBOOK SHAPE -- Team of the
Month / Season / All-Time on Home, a points record book, points-based
Advanced Standings, no Matchup History -- assembled over ESPN's data via
`espn_points_data`. It is the sibling of the H2H assembly in
`almanac_write.write_almanac`, and it deliberately shares that module's
tab writers, row formatters and two-pass nav mechanics: the format
decides which sections exist, not how a row is drawn.

WHY IT IS NOT THE CBS RENDERER. `cbs_almanac_sheets` renders the same
FORMAT from a different PLATFORM's feeds -- `mart_period_standings`,
`stg_cbs__rosters`, `stg_cbs__ui_standings`. An ESPN league lands none of
those and needs none of them: it has the shared facts. Calling CBS
queries against an ESPN league would repeat, in the other direction, the
exact confusion this ticket exists to remove.

WHAT A FIRST-YEAR LEAGUE STILL IN FLIGHT GETS. Everything that accrues:
the boards, the record book, the standings, the draft recap, the team
pages. Nothing here waits for a completed matchup or a completed season,
because in this format neither is what makes a number meaningful. The one
section that does wait is the Rivalry Matrix, which compares COMPLETED
seasons -- it says so plainly rather than reinterpreting an unfinished
season as a finished result.
"""

import os

import almanac_data
import almanac_write
import espn_points_data
from almanac_logic import (
    build_advanced_standings_tab_rows,
    build_draft_tab_rows,
    build_points_home_tab_rows,
    build_records_tab_rows,
    build_team_history_tabs,
    build_trades_tab_rows,
)
from almanac_render import (
    ADVANCED_STANDINGS_TAB,
    DRAFT_TAB,
    HOME_TAB,
    RECORDS_TAB,
    TRADES_TAB,
)


# Said in place of the acquisition tables when the transaction board was
# never successfully read. A chart of zeroes would assert that nobody made a
# move all year; this asserts only what we actually know.
ACQUISITION_UNAVAILABLE_NOTE = (
    "Unavailable -- this league's transaction log could not be read, so how "
    'each player was acquired is unknown. This does NOT mean there were no '
    'adds, drops or trades. It fills in once a capture succeeds.'
)

# Said on Home when the transactions feed could not be read at all. The
# distinction it protects is the whole point: an accessible feed with no
# trades is a fact about the league, an inaccessible one is a fact about
# our access, and collapsing them publishes the wrong claim.
TRADES_UNAVAILABLE_NOTE = (
    'Unavailable -- ESPN did not authorize this league\'s transactions '
    'feed, so trades could not be read. This does NOT mean no trades '
    'happened.'
)


def build_all_tabs(nav_targets=None, include_trades=True, context=None):
    """Assemble every tab of the points workbook.

    Returns (tabs, context, team_titles) where tabs is
    [(title, rows)] with Home FIRST for preview ordering. The live write
    below rebuilds ONLY Home a second time, once the other tabs' gids
    exist, so its nav cells can be real in-sheet links -- see
    `_home_builder`, which closes over the data already fetched rather
    than re-running every query for one tab.
    """
    tabs, context, team_titles, _ = _build(
        nav_targets=nav_targets, include_trades=include_trades,
        context=context,
    )
    return tabs, context, team_titles


def _build(nav_targets=None, include_trades=True, context=None):
    """The assembly, also handing back a Home rebuilder bound to the data
    it already fetched. Returns (tabs, context, team_titles, home_builder)."""
    context = context or espn_points_data.season_context()
    season_year = context['season_year']
    league_id = os.getenv('LEAGUE_ID')

    if season_year is None:
        # Nothing has been extracted. Say that once, rather than emitting a
        # book of empty tables that reads like a broken league.
        empty = [(HOME_TAB, [['Fantasy League Almanac'],
                             ['No season data has been extracted for this '
                              'league yet. Run the extract, then rebuild.']])]
        return (empty, context, [], lambda _targets: empty[0][1])

    records_rows = build_records_tab_rows(
        all_time_records=almanac_data.get_almanac_records('all_time'),
        current_season_records=almanac_data.get_almanac_records(
            'current_season'),
        league_id=league_id,
        hall_of_fame=almanac_data.get_franchise_hall_of_fame(),
        hall_of_shame=almanac_data.get_wasted_hall_of_shame(),
        # The SAME shared record book, told in this format's language: no
        # "standard-length matchups", no "Week 1" period cells, no boxscore
        # links to a matchup view this league has never had.
        season_long=True,
    )

    # Detected, stated, and NOT corrected (MLB-243): a league that drafted
    # months into the season carries pre-league production in every total.
    # None for an ordinary league, so nothing is said where nothing is
    # wrong.
    caveat = espn_points_data.late_draft_note(context)

    # THE RICH BUILDER, not a simplified twin (MLB-243). Every section the
    # established books carry -- the standing-by-period chart, historic
    # finishes, the per-stat detailed standings with its colour grading, the
    # slot grids, acquisition channels and the MLB affinity matrix -- already
    # exists here. What a points league needed was points-shaped INPUTS, not
    # a second renderer: the earlier two-table stand-in reproduced almost
    # none of it.
    stat_specs = almanac_data.get_team_week_stat_specs()
    acquisition = espn_points_data.acquisition_channels(season_year)
    standings_rows = build_advanced_standings_tab_rows(
        espn_points_data.standings_rows(season_year, stat_specs),
        almanac_data.get_team_slot_points(season_year),
        stat_specs,
        season_year,
        acquisition_rows=acquisition,
        slot_rows_alltime=almanac_data.get_team_slot_points_alltime(),
        affinity_rows=almanac_data.get_team_affinity_weights(season_year),
        rank_arc_rows=espn_points_data.rank_arc(season_year),
        finishes_rows=espn_points_data.season_finishes(),
        rivalry_axes=almanac_data.get_rivalry_axes(),
        rivalry_pairs=almanac_data.get_rivalry_matrix(),
        season_long=True,
        acquisition_unavailable=(
            None if acquisition is not None else ACQUISITION_UNAVAILABLE_NOTE),
        caveat=caveat,
    )

    team_tabs = build_team_history_tabs(
        almanac_data.get_team_roster_history_stats(season_year),
        season_year=season_year,
        league_id=league_id,
        slot_caps=almanac_data.get_roster_slot_capacities(
            season_year, include_inactive=True),
        best_seasons_fn=almanac_data.team_best_seasons_fn(),
    )
    team_titles = [title for title, _ in team_tabs]

    draft_rows = build_draft_tab_rows(
        almanac_data.get_draft_board(season_year), season_year,
        league_id=league_id,
        history_rows=almanac_data.get_draft_history_boards(season_year),
        season_clocks={r['season_year']: r['clock']
                       for r in almanac_data.get_season_scoring_periods()},
    )

    trades_rows, trades_note = (None, None)
    if include_trades:
        trades_rows, trades_note = _trades_section(season_year)

    boards = espn_points_data.home_boards(context)
    era = _era_label(context)

    def _home_builder(targets):
        """Home from the ALREADY-FETCHED boards. The live write needs Home
        twice -- once for its shape, once with real gids -- and the second
        pass must not re-query the warehouse for three optimal lineups."""
        return build_points_home_tab_rows(
            boards,
            season_year=season_year,
            month_window=boards.get('month_window'),
            era_label=era,
            team_titles=team_titles,
            league_id=league_id,
            nav_targets=targets,
            trades_note=trades_note,
            caveat=caveat,
        )

    home_rows = _home_builder(nav_targets)

    tabs = [
        (HOME_TAB, home_rows),
        (RECORDS_TAB, records_rows),
        (ADVANCED_STANDINGS_TAB, standings_rows),
        *([(TRADES_TAB, trades_rows)] if trades_rows is not None else []),
        (DRAFT_TAB, draft_rows),
        *team_tabs,
    ]
    # NO Matchup History: it is a week-by-week matchup archive, and this
    # league has no matchups. Omitted rather than rendered empty.
    return tabs, context, team_titles, _home_builder


def _trades_section(season_year):
    """(rows, unavailable_note).

    A live-API tab, so a pull that fails must not sink the publish. Two
    different failures with two different answers: the feed refused us
    (401/403 -> the data layer already returns "unavailable", and the tab
    renders saying so), or the pull broke outright (no tab, and Home
    carries the reason).
    """
    try:
        data = almanac_data.get_trades_tab_data(season_year)
    except Exception as exc:                       # noqa: BLE001 -- reported
        print(f'[almanac] Trades tab skipped -- live ESPN pull failed: {exc}')
        return (None, TRADES_UNAVAILABLE_NOTE)
    return (build_trades_tab_rows(data, season_year), None)


def _era_label(context):
    first, last = context.get('first_season'), context.get('season_year')
    if first and last and first != last:
        return f'{first}–{last}'
    return str(last or '')


def write_points_almanac(sheet_id, client=None):
    """Two-pass write of the points workbook, mirroring the H2H writer.

    Pass 1 writes every non-Home tab so their gids exist; pass 2 rebuilds
    Home with real =HYPERLINK nav targets and writes it last. `client`
    carries the stranger path's `drive.file` credentials -- under that
    scope only the client that created a workbook may open it -- and
    defaults to the maintainer client for configured dev/prod sheets.
    """
    tabs, context, team_titles, home_builder = _build()
    # The styled Advanced Standings writer paints gradients from the same
    # scored-stat specs the rows were built from, so it needs them here too.
    stat_specs = almanac_data.get_team_week_stat_specs()
    client = client or almanac_write._get_authorized_client()
    spreadsheet = client.open_by_key(sheet_id)

    others = [(title, rows) for title, rows in tabs if title != HOME_TAB]
    worksheets = {}
    for title, rows in others:
        if title in team_titles:
            worksheets[title] = almanac_write._replace_team_tab(
                spreadsheet, title, rows)
        elif title == RECORDS_TAB:
            worksheets[title] = almanac_write._replace_records_tab(
                spreadsheet, rows)
        elif title == TRADES_TAB:
            worksheets[title] = almanac_write._replace_trades_tab(
                spreadsheet, rows)
        elif title == DRAFT_TAB:
            worksheets[title] = almanac_write._replace_draft_tab(
                spreadsheet, rows)
        elif title == ADVANCED_STANDINGS_TAB:
            # The STYLED writer now (MLB-243). It paints gradients by
            # column offset against standings_header's layout, and the
            # points header is the same width as the H2H one, so every
            # offset lands where it always did -- which is what makes the
            # colour grading work here at all.
            worksheets[title] = almanac_write._replace_advanced_standings_tab(
                spreadsheet, rows, stat_specs)
        else:
            worksheets[title] = almanac_write._replace_plain_tab(
                spreadsheet, title, rows)

    nav_targets = {title: ws.id for title, ws in worksheets.items()
                   if ws is not None}
    almanac_write._replace_home_tab(spreadsheet, home_builder(nav_targets))

    almanac_write._sort_almanac_tabs(spreadsheet, [
        HOME_TAB, RECORDS_TAB, ADVANCED_STANDINGS_TAB, TRADES_TAB, DRAFT_TAB,
        *team_titles,
    ])
    print(f'[almanac] wrote the points-format almanac '
          f'({len(tabs)} tabs) for {context["season_year"]} '
          f'to sheet {sheet_id}')
