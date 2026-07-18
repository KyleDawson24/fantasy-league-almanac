"""output/almanac_write.py

Tier 2c.4 (v1.1.1): Google Sheets orchestration for the league almanac.

Owns the gspread / Sheets API surface: opening the spreadsheet, replacing
tab contents, applying cell formatting (color scales, frozen rows,
column widths, conditional record highlighting), and the retry wrapper
that handles transient Sheets quota errors during a full almanac write.

Dependencies (downward only): almanac_data, almanac_render, almanac_logic.
Write is the consumer of everything else -- it gathers all the row data
through the other three modules and pushes it to the spreadsheet.
"""

import math
import os
import re
import time

import gspread

import almanac_data
import almanac_logic
import almanac_render
import records
from almanac_data import (
    get_almanac_records,
    get_draft_board,
    get_draft_history_boards,
    get_season_scoring_periods,
    get_team_standings,
    get_team_slot_points,
    get_team_slot_points_alltime,
    get_team_acquisition_channels,
    get_team_acquisition_channels_alltime,
    get_team_affinity_weights,
    get_team_rank_arc,
    get_espn_season_finishes,
    get_team_standings_alltime,
    get_current_team_roster_stats,
    get_latest_matchup_period,
    get_roster_slot_capacities,
    get_team_roster_history_stats,
    get_team_week_record_marks,
    get_team_week_stat_specs,
    get_team_weeks,
    _fact_stat_column_name,
)
from almanac_logic import (
    SCORE_RECORD_SPECS,
    build_advanced_standings_tab_rows,
    build_draft_board_color_grid,
    build_draft_tab_rows,
    build_home_tab_rows,
    build_records_tab_rows,
    build_team_history_tabs,
    build_team_weeks_tab_rows,
    expand_team_roster_rows,
)
from almanac_render import (
    ADVANCED_STANDINGS_TAB,
    DRAFT_TAB,
    HOME_TAB,
    RECORDS_TAB,
    RECORDS_MATRIX_DETAIL_HEADER,
    RECORDS_MATRIX_WIDTH,
    TEAM_HISTORY_DETAIL_HEADER,
    TEAM_HISTORY_HITTER_HEADER,
    TEAM_HISTORY_PITCHER_HEADER,
    TEAM_HISTORY_MIXED_HEADER,
    TEAM_HISTORY_HITTER_STATS,
    TEAM_HISTORY_PITCHER_STATS,
    TEAM_HISTORY_MIXED_STATS,
    TEAM_ROSTER_HEADER,
    TEAM_ROSTER_MATRIX_WIDTH,
    TEAM_WEEKS_BASE_HEADER,
    TEAM_WEEKS_RARE_STATS,
    TEAM_WEEKS_SCORE_HEADER,
    TEAM_WEEKS_TAB,
    TEAM_WEEKS_WHITE_TO_GREEN_STATS,
    TEAM_WEEKS_WHITE_TO_RED_STATS,
    _is_active_display_slot,
    _is_hitter_display_slot,
    _is_pitcher_display_slot,
    _is_rare_team_week_stat,
    _safe_sheet_title,
    _team_week_specs_for_category,
    team_tab_banner_merges,
    team_tab_format_specs,
    team_tab_merge_ranges,
)
from sheets_writer import _get_authorized_client


# PITCHING_STAT_ORDER moved to almanac_data.py (Tier 2c.1). Re-exported
# below for backward compat.


def write_almanac(sheet_id, season_year=None, matchup_period=None):
    """Write the v1.1 almanac Home tab.

    If `season_year` / `matchup_period` are omitted, the latest loaded
    matchup_period is used. The Sheets writer is intentionally separate
    from `sheets_writer.write_records()` while the almanac surface is
    being built out, so the legacy records sink remains stable.
    """
    if season_year is None or matchup_period is None:
        season_year, matchup_period = get_latest_matchup_period()

    league_id = os.getenv('LEAGUE_ID')
    # Tier 2c.5 (v1.1.1): season-to-date is get_all_league_team(matchup_
    # period=None). v1.2 (#23): all Home datasets come from one fetch so
    # this live path can't drift from the preview path.
    home_data = almanac_data.get_home_tab_data(season_year, matchup_period)
    schedule_lookup = records.load_schedule_lookup()
    records_rows = build_records_tab_rows(
        all_time_records=get_almanac_records('all_time'),
        current_season_records=get_almanac_records('current_season'),
        league_id=league_id,
        schedule_lookup=schedule_lookup,
    )
    team_week_stat_specs = get_team_week_stat_specs()
    team_week_rows = get_team_weeks(team_week_stat_specs)
    team_week_record_marks = get_team_week_record_marks(team_week_stat_specs)
    team_weeks_tab_rows = build_team_weeks_tab_rows(
        team_week_rows,
        team_week_stat_specs,
        league_id=league_id,
        schedule_lookup=schedule_lookup,
    )
    team_pages = build_team_history_tabs(
        get_team_roster_history_stats(season_year),
        season_year=season_year,
        league_id=league_id,
        slot_caps=get_roster_slot_capacities(season_year, include_inactive=True),
        best_seasons_fn=almanac_data.team_best_seasons_fn(),
    )
    draft_board = get_draft_board(season_year)
    draft_tab_rows = build_draft_tab_rows(
        draft_board, season_year, league_id=league_id,
        history_rows=get_draft_history_boards(season_year),
        season_clocks={r['season_year']: r['clock']
                       for r in get_season_scoring_periods()})
    draft_color_grid = build_draft_board_color_grid(draft_board)
    standings_tab_rows = build_advanced_standings_tab_rows(
        get_team_standings(season_year, team_week_stat_specs),
        get_team_slot_points(season_year),
        team_week_stat_specs,
        season_year,
        # acquisition_rows was preview-only until 2026-07-17 -- the writer
        # always knew how to paint the blocks; the assembly never passed
        # the rows, so the real sheet silently lacked them.
        acquisition_rows=get_team_acquisition_channels(season_year),
        slot_rows_alltime=get_team_slot_points_alltime(),
        affinity_rows=get_team_affinity_weights(season_year),
        rank_arc_rows=get_team_rank_arc(season_year),
        finishes_rows=get_espn_season_finishes(),
        standings_rows_alltime=get_team_standings_alltime(
            team_week_stat_specs),
        acquisition_rows_alltime=get_team_acquisition_channels_alltime(),
    )
    client = _get_authorized_client()
    spreadsheet = client.open_by_key(sheet_id)

    # Two-pass write (#25). Pass 1: create/write every non-Home tab so
    # their gids exist; capture each worksheet. Pass 2: read the gids
    # straight off those worksheets, render the Home nav as in-sheet
    # =HYPERLINK("#gid=...&range=A1") formulas, and write Home last.
    # Reading gids at write time -- never hardcoding tab URLs -- keeps this
    # portable: it works on a brand-new sheet for any league, no manual
    # copy-paste of tab links.
    records_ws = _replace_records_tab(spreadsheet, records_rows)
    matchup_ws = _replace_team_weeks_tab(
        spreadsheet,
        team_weeks_tab_rows,
        team_week_stat_specs,
        source_rows=team_week_rows,
        record_marks=team_week_record_marks,
    )
    _delete_prefixed_team_tabs(spreadsheet, {title for title, _ in team_pages})
    team_worksheets = [
        _replace_team_tab(spreadsheet, title, team_page_rows)
        for title, team_page_rows in team_pages
    ]
    draft_ws = _replace_draft_tab(spreadsheet, draft_tab_rows, color_grid=draft_color_grid)
    standings_ws = _replace_advanced_standings_tab(
        spreadsheet, standings_tab_rows, team_week_stat_specs,
    )

    nav_targets = {
        ws.title: ws.id
        for ws in (records_ws, matchup_ws, draft_ws, standings_ws, *team_worksheets)
        if ws is not None
    }
    rows = build_home_tab_rows(
        **home_data,
        season_year=season_year,
        matchup_period=matchup_period,
        team_titles=[title for title, _ in team_pages],
        league_id=league_id,
        nav_targets=nav_targets,
    )
    _replace_home_tab(spreadsheet, rows)

    _sort_almanac_tabs(spreadsheet, [
        HOME_TAB, RECORDS_TAB, TEAM_WEEKS_TAB, ADVANCED_STANDINGS_TAB,
        *[title for title, _ in team_pages], DRAFT_TAB,
    ])

    print(
        f"[almanac] wrote {len(home_data['weekly_rows'])} weekly + "
        f"{len(home_data['season_rows'])} season-to-date all-league rows "
        f"{len(records_rows)} records-tab rows, {len(team_week_rows)} team-week rows "
        f"and {len(team_pages)} team roster tabs "
        f"for {season_year} MP{matchup_period} to sheet {sheet_id}"
    )


_HOME_LEFT_SECTION_LABELS = {
    'Navigate', 'Points Glossary', 'All-League Team: All-Time',
}


def _replace_home_tab(spreadsheet, rows):
    """Clear/create Home and write the two-band almanac front page (#23)."""
    width = max((len(row) for row in rows), default=20)
    try:
        worksheet = spreadsheet.worksheet(HOME_TAB)
    except gspread.WorksheetNotFound:
        worksheet = _sheets_call(
            f'create {HOME_TAB}',
            lambda: spreadsheet.add_worksheet(
                title=HOME_TAB, rows=max(len(rows) + 10, 50), cols=max(width, 20),
            ),
        )

    _sheets_call(f'clear {HOME_TAB}', worksheet.clear)
    _sheets_call(
        f'update {HOME_TAB}',
        lambda: worksheet.update(rows, 'A1', value_input_option='USER_ENTERED'),
    )

    # First-pass polish for the two-band layout. Deliberately restrained --
    # the live Sheet gets a hand pass (merges, widths, color). Byte-diff
    # doesn't cover formatting, so keep this defensive.
    try:
        last_col = _a1_col(width)
        _sheets_call(f'freeze {HOME_TAB}', lambda: worksheet.freeze(rows=3))
        _apply_home_tab_dimensions(spreadsheet, worksheet)
        formats = [
            {  # title banner
                'range': f'A1:{last_col}1',
                'format': {'textFormat': {'bold': True, 'fontSize': 14}},
            },
            {  # scoring callout
                'range': f'A2:{last_col}2',
                'format': {
                    'textFormat': {'italic': True},
                    'backgroundColor': {'red': 0.90, 'green': 0.94, 'blue': 0.98},
                },
            },
            # Points number formats. Left all-time Points (C) is whole --
            # 1-decimal is overkill at the all-time scale; right Points (K)
            # and deviation total pts (O) stay one decimal. Number format
            # only touches numeric cells, harmless on the text/hyperlink
            # cells those columns also contain.
            {'range': 'C:C', 'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0'}}},
            # ppg (D) -- 2 decimals. The value is written as a string but
            # USER_ENTERED coerces it to a number and would drop a trailing
            # zero ("4.60" -> 4.6); the format pins it back to 2 places.
            {'range': 'D:D', 'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0.00'}}},
            {'range': 'K:K', 'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0.0'}}},
            {'range': 'O:O', 'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0.0'}}},
        ]
        formats.extend(_home_label_formats(rows, last_col))
        _batch_format(worksheet, formats)
    except Exception as exc:
        print(f"[almanac] formatting skipped: {exc}")


def _apply_home_tab_dimensions(spreadsheet, worksheet):
    """Set Home column widths (#23 live polish). Cols A-K + N-O are sized
    to the two-band content; L/M (right Slash / Stat Line) and P+ keep the
    default. Indices are 0-based: A=0 ... O=14."""
    sheet_id = worksheet.id
    widths = [
        (0, 100), (1, 125), (2, 100), (3, 50), (4, 100), (5, 40), (6, 40),
        (7, 150), (8, 100), (9, 125), (10, 50), (13, 150), (14, 50),
    ]
    requests = [
        _column_width_request(sheet_id, idx, idx + 1, px) for idx, px in widths
    ]
    _sheets_batch_update(spreadsheet, f'home dimensions {worksheet.title}', requests)


def _home_label_formats(rows, last_col):
    """Bold the two-band Home section labels + table headers. Positions are
    dynamic, so scan by marker (#23). Best-effort: keep these marker
    strings in sync with build_home_tab_rows' left-band labels."""
    formats = []
    for row_number, row in enumerate(rows, 1):
        first = row[0] if len(row) > 0 else ''
        right = row[5] if len(row) > 5 else ''
        if first in _HOME_LEFT_SECTION_LABELS or (
            first == 'Slot' and len(row) > 1 and row[1] == 'Player'
        ):
            formats.append({
                'range': f'A{row_number}:D{row_number}',
                'format': {'textFormat': {'bold': True}},
            })
        if isinstance(right, str) and right.startswith('All-League Team'):
            formats.append({
                'range': f'F{row_number}:{last_col}{row_number}',
                'format': {'textFormat': {'bold': True}},
            })
        elif right == 'Slot':
            formats.append({
                'range': f'F{row_number}:{last_col}{row_number}',
                'format': {
                    'textFormat': {
                        'bold': True,
                        'foregroundColor': {'red': 1, 'green': 1, 'blue': 1},
                    },
                    'backgroundColor': {'red': 0.12, 'green': 0.20, 'blue': 0.30},
                },
            })
    return formats


# Board team/slot columns start at index 6 (after Rd / Pick|Year / Team /
# Player / Max / Med -- Kyle's 2026-07-18 board).
_DRAFT_BOARD_TEAM_START = 6
_DRAFT_HEADER_BG = {'red': 0.90, 'green': 0.94, 'blue': 0.98}
_DRAFT_BOARD_HEADER_BG = {'red': 0.12, 'green': 0.20, 'blue': 0.30}   # navy
_DRAFT_WHITE = {'red': 1, 'green': 1, 'blue': 1}
# Powder blue for the Pick/Team/Player (Top Pick) header trio, against the
# navy band (Kyle 2026-07-18).
_DRAFT_POWDER_BG = {'red': 0.72, 'green': 0.85, 'blue': 0.92}


def _reapply_formula_cells(worksheet, rows):
    """Re-send any formula cells (values starting with '=') with USER_ENTERED.

    The team and draft tabs are bulk-written RAW so signed / zero-padded value
    strings ("+253", "040", "3.00") survive verbatim -- but RAW also keeps
    Sheets from parsing a formula, so the bref HYPERLINK player links would
    show up as literal text. This second pass re-coerces only the '=' cells to
    parsed formulas in one batched call, leaving every value string untouched."""
    formula_cells = [
        {'range': f'{_a1_col(col)}{row_number}', 'values': [[value]]}
        for row_number, row in enumerate(rows, start=1)
        for col, value in enumerate(row, start=1)
        if isinstance(value, str) and value.startswith('=')
    ]
    if not formula_cells:
        return
    _sheets_call(
        f'reapply {len(formula_cells)} formula cells {worksheet.title}',
        # Fresh dicts on every attempt: gspread's Worksheet.batch_update
        # rewrites each entry's 'range' IN PLACE to "'<title>'!<range>"
        # before posting, so retrying the same list after a quota hit would
        # prefix the title a second time ("'HH'!'HH'!C7" -> 400 Unable to
        # parse range) and turn a recoverable 429 into a hard failure.
        lambda: worksheet.batch_update(
            [dict(cell) for cell in formula_cells],
            value_input_option='USER_ENTERED',
        ),
    )


def _replace_draft_tab(spreadsheet, rows, color_grid=None):
    """Clear/create the Draft Recap tab and write it. Returns the worksheet
    so the two-pass write can read its gid for the Home nav link.

    color_grid (from build_draft_board_color_grid) drives the per-cell
    red->white->green color scale on the board; None skips it."""
    width = max((len(row) for row in rows), default=20)
    try:
        worksheet = spreadsheet.worksheet(DRAFT_TAB)
    except gspread.WorksheetNotFound:
        worksheet = _sheets_call(
            f'create {DRAFT_TAB}',
            lambda: spreadsheet.add_worksheet(
                title=DRAFT_TAB, rows=max(len(rows) + 10, 50), cols=max(width, 20),
            ),
        )

    _sheets_call(f'clear {DRAFT_TAB}', worksheet.clear)
    # RAW so the signed value strings ("+253") keep their '+' -- USER_ENTERED
    # would coerce them to plain numbers and drop the sign.
    _sheets_call(
        f'update {DRAFT_TAB}',
        lambda: worksheet.update(rows, 'A1', value_input_option='RAW'),
    )
    _reapply_formula_cells(worksheet, rows)

    try:
        last_col = _a1_col(width)
        _apply_draft_tab_dimensions(spreadsheet, worksheet, width)
        formats = [
            {'range': 'A1', 'format': {'textFormat': {'bold': True, 'fontSize': 14}}},
        ]
        formats.extend(_draft_label_formats(rows, last_col))
        _batch_format(worksheet, formats)
        _sheets_batch_update(spreadsheet, f'draft merges {DRAFT_TAB}',
                             _draft_merge_requests(rows, worksheet.id))
        _apply_draft_board_colors(spreadsheet, worksheet, rows, color_grid)
    except Exception as exc:
        print(f"[almanac] formatting skipped for {DRAFT_TAB}: {exc}")

    return worksheet


def _draft_label_formats(rows, last_col):
    """Format the Draft Recap tab (Kyle 2026-07-18): italic helper notes,
    bold leaderboard bands + headers with the reordered Pts one-decimal
    number format, the navy 'Top Pick' super-header band, and the navy
    header row with its Pick/Team/Player trio in powder blue."""
    formats = []
    navy_white = {'textFormat': {'bold': True, 'foregroundColor': _DRAFT_WHITE},
                  'backgroundColor': _DRAFT_BOARD_HEADER_BG}
    for row_number, row in enumerate(rows, 1):
        first = row[0] if row else ''
        second = row[1] if len(row) > 1 else ''
        # Helper-note row (Delta at A, keeper at F).
        if isinstance(first, str) and first.startswith('Δ ='):
            formats.append({'range': f'A{row_number}:F{row_number}',
                            'format': {'textFormat': {'italic': True}}})
        # Leaderboard band + column headers (value B-F, busts G-K).
        if second == 'Best Value Picks':
            for rng in (f'B{row_number}:F{row_number}', f'G{row_number}:K{row_number}'):
                formats.append({'range': rng,
                                'format': {'textFormat': {'bold': True, 'fontSize': 12}}})
        if second == 'Pts' and len(row) > 3 and row[3] == 'Player':
            for rng in (f'B{row_number}:F{row_number}', f'G{row_number}:K{row_number}'):
                formats.append({'range': rng,
                                'format': {'textFormat': {'bold': True},
                                           'backgroundColor': _DRAFT_HEADER_BG}})
            # One-decimal Pts (B / G) for the 10 rows of each block.
            for col in ('B', 'G'):
                formats.append({
                    'range': f'{col}{row_number + 1}:{col}{row_number + 10}',
                    'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0.0'}}})
        # Section titles.
        if isinstance(first, str) and (first.startswith('Draft Board')
                                       or first.startswith('All-Time Draft Board')):
            formats.append({'range': f'A{row_number}:{last_col}{row_number}',
                            'format': {'textFormat': {'bold': True, 'fontSize': 12}}})
        if isinstance(first, str) and first.startswith('Team-agnostic'):
            formats.append({'range': f'A{row_number}:{last_col}{row_number}',
                            'format': {'textFormat': {'italic': True}}})
        # Navy 'Top Pick' super-header band (the merge is applied separately).
        if first == '' and second == 'Top Pick':
            formats.append({'range': f'A{row_number}:{last_col}{row_number}',
                            'format': {**navy_white,
                                       'horizontalAlignment': 'CENTER'}})
        # Board header row: navy + white, Pick/Team/Player (B:D) in powder.
        if first == 'Rd':
            formats.append({'range': f'A{row_number}:{last_col}{row_number}',
                            'format': navy_white})
            formats.append({'range': f'B{row_number}:D{row_number}',
                            'format': {'textFormat': {'bold': True},
                                       'backgroundColor': _DRAFT_POWDER_BG}})
    return formats


def _draft_merge_requests(rows, sheet_id):
    """Merge each board's 'Top Pick' super-header across B:D (columns 1-3),
    so it centers over the Pick/Team/Player trio."""
    requests = []
    for row_index, row in enumerate(rows):
        if row and row[0] == '' and len(row) > 1 and row[1] == 'Top Pick':
            requests.append({'mergeCells': {
                'range': {'sheetId': sheet_id,
                          'startRowIndex': row_index, 'endRowIndex': row_index + 1,
                          'startColumnIndex': 1, 'endColumnIndex': 4},
                'mergeType': 'MERGE_ALL'}})
    return requests


def _apply_draft_tab_dimensions(spreadsheet, worksheet, width):
    """Kyle's 2026-07-18 house grid: 25 buffer / 40 short / 40 short / 125
    player / 75 longer-number / 40 short, then 100 for every board and
    leaderboard column after."""
    sheet_id = worksheet.id
    requests = [
        _column_width_request(sheet_id, 0, 1, 25),
        _column_width_request(sheet_id, 1, 3, 40),
        _column_width_request(sheet_id, 3, 4, 125),
        _column_width_request(sheet_id, 4, 5, 75),
        _column_width_request(sheet_id, 5, 6, 40),
        _column_width_request(sheet_id, 6, max(width, 7), 100),
    ]
    _sheets_batch_update(spreadsheet, f'format dimensions {worksheet.title}', requests)


def _apply_draft_board_colors(spreadsheet, worksheet, rows, color_grid):
    """Red->white->green per-cell background on BOTH boards (Kyle
    2026-07-18). The current board grades player-NAME cells by the passed
    color_grid (invisible season points); the all-time board grades its
    own visible paced numbers. Backgrounds are set directly (a numeric
    gradient rule can't touch the current board's text cells), one
    updateCells request each, backgroundColor-only so values survive."""
    requests = []

    # Current-season board: header row[1]=='Pick', colored by color_grid.
    current_hdr = next((i for i, row in enumerate(rows)
                        if row and row[0] == 'Rd' and len(row) > 1
                        and row[1] == 'Pick'), None)
    if color_grid and current_hdr is not None:
        pts = [p for grid_row in color_grid for p in grid_row if p is not None]
        if pts:
            requests.append(_draft_color_request(
                worksheet.id, current_hdr + 1, color_grid, min(pts), max(pts)))

    # All-time board: header row[1]=='Year', colored by the cells' own
    # numbers (paced medians in the slot columns).
    alltime_hdr = next((i for i, row in enumerate(rows)
                        if row and row[0] == 'Rd' and len(row) > 1
                        and row[1] == 'Year'), None)
    if alltime_hdr is not None:
        num_rows = rows[alltime_hdr + 1:]
        grid = [[(float(c) if isinstance(c, (int, float)) else None)
                 for c in row[_DRAFT_BOARD_TEAM_START:]] for row in num_rows]
        vals = [c for grid_row in grid for c in grid_row if c is not None]
        if vals:
            requests.append(_draft_color_request(
                worksheet.id, alltime_hdr + 1, grid, min(vals), max(vals)))

    _sheets_batch_update(spreadsheet, f'draft board colors {worksheet.title}',
                         requests)


def _draft_color_request(sheet_id, start_row, grid, low, high):
    """One updateCells background request for a board grid starting at
    _DRAFT_BOARD_TEAM_START; None cells paint white."""
    width = max((len(g) for g in grid), default=0)
    cell_rows = [{'values': [
        {'userEnteredFormat': {'backgroundColor': (
            _draft_gradient_color(g[c], low, high)
            if c < len(g) and g[c] is not None else {'red': 1, 'green': 1, 'blue': 1})}}
        for c in range(width)]} for g in grid]
    return {'updateCells': {
        'range': {'sheetId': sheet_id, 'startRowIndex': start_row,
                  'endRowIndex': start_row + len(grid),
                  'startColumnIndex': _DRAFT_BOARD_TEAM_START,
                  'endColumnIndex': _DRAFT_BOARD_TEAM_START + width},
        'rows': cell_rows, 'fields': 'userEnteredFormat.backgroundColor'}}


def _draft_gradient_color(value, low, high):
    """Map a season-points value to a red (low) -> white (mid) -> green (high)
    background, matching the team-weeks palette."""
    if high <= low:
        t = 0.5
    else:
        t = max(0.0, min(1.0, (value - low) / (high - low)))
    red = {'red': 0.96, 'green': 0.62, 'blue': 0.60}
    white = {'red': 1.0, 'green': 1.0, 'blue': 1.0}
    green = {'red': 0.67, 'green': 0.86, 'blue': 0.64}
    if t < 0.5:
        return _lerp_color(red, white, t / 0.5)
    return _lerp_color(white, green, (t - 0.5) / 0.5)


def _lerp_color(c1, c2, t):
    return {channel: c1[channel] + (c2[channel] - c1[channel]) * t
            for channel in ('red', 'green', 'blue')}


def _standings_table_bounds(rows):
    """Locate every Table-A-shaped standings table (the season one and
    its all-time twin): a list of (header_idx, end_idx), 0-based, end
    exclusive. The stacked tables below have their own locators
    (_slot_grid_bounds, _acquisition_table_bounds, _affinity_bounds,
    _espn_finishes_bounds)."""
    bounds = []
    for i, r in enumerate(rows):
        if r and r[0] == 'Rank' and 'Offense' in r:
            end = i + 1
            while (end < len(rows) and rows[end]
                   and rows[end][0] not in ('', None)):
                end += 1
            bounds.append((i, end))
    return bounds


def _espn_finishes_bounds(rows):
    """Locate the finishes-beside-the-chart table (anchored at col V,
    hugging the top since round 12: explainer row 1, header row 2): None
    or a dict with note/header/data geometry."""
    for i, r in enumerate(rows):
        if len(r) > 25 and r[21] == 'Team' and r[25] == 'Titles':
            end = i + 1
            while (end < len(rows) and len(rows[end]) > 21
                   and rows[end][21] not in ('', None)):
                end += 1
            return {'col0': 21, 'note': i - 1, 'hdr': i, 'end': end,
                    'n_cols': len(r) - 21}
    return None


def _slot_grid_bounds(rows):
    """Locate every slot-points grid (the season grid + its all-time
    twin): indented headers with Team in column 1 and NO 'Keeper' column
    (that's what distinguishes the acquisition blocks). Returns a list of
    (header_idx, end_idx); data rows key off column 1, matching the
    grids' one-cell indent."""
    bounds = []
    for i, r in enumerate(rows):
        if len(r) > 1 and r[0] == '' and r[1] == 'Team' and 'Keeper' not in r:
            end = i + 1
            while (end < len(rows) and len(rows[end]) > 1
                   and rows[end][1] not in ('', None)):
                end += 1
            bounds.append((i, end))
    return bounds


def _affinity_bounds(rows):
    """Locate the roster-affinity matrices: header rows starting 'MLB
    Team'. Returns a list of dicts -- header/end row indices plus the
    two half-block column spans (the season half starts at column C, the
    all-time half past the shared U divider; both read off the header's
    abbrev runs, so the geometry survives pad-width changes)."""
    bounds = []
    for i, r in enumerate(rows):
        spine0 = next((j for j in range(0, 5)
                       if len(r) > j and r[j] == 'MLB Team'), None)
        if spine0 is None:
            continue
        end = i + 1
        while (end < len(rows) and len(rows[end]) > spine0
               and rows[end][spine0] not in ('', None)):
            end += 1
        left0, n_t = spine0 + 2, 0
        while left0 + n_t < len(r) and r[left0 + n_t] not in ('', None):
            n_t += 1
        right0 = next((j for j in range(left0 + n_t, len(r))
                       if r[j] not in ('', None)), None)
        bounds.append({'hdr': i, 'end': end, 'spine0': spine0,
                       'left0': left0, 'right0': right0, 'n_t': n_t})
    return bounds


def _stale_conditional_rule_requests(spreadsheet, worksheet, rows=None):
    """Wipe requests for state that ACCUMULATES across reruns on this
    worksheet: conditional-format rules (each render adds at index 0;
    clear() only drops values) and -- when the tab carries the rank-chart
    apparatus -- embedded charts, checkbox validations, and hidden helper
    columns. Scoped to one sheetId; other tabs untouched."""
    meta = _sheets_call(
        f'meta {worksheet.title}',
        lambda: spreadsheet.fetch_sheet_metadata({
            'fields': 'sheets(properties(sheetId),conditionalFormats,'
                      'charts(chartId))',
        }),
    )
    sheet = next(
        (s for s in meta.get('sheets', [])
         if s.get('properties', {}).get('sheetId') == worksheet.id),
        {},
    )
    requests = [{'deleteConditionalFormatRule':
                 {'sheetId': worksheet.id, 'index': 0}}
                for _ in sheet.get('conditionalFormats', ())]
    for chart in sheet.get('charts', ()):
        requests.append({'deleteEmbeddedObject':
                         {'objectId': chart['chartId']}})
    if rows is not None and _rank_chart_bounds(rows):
        requests.append({'setDataValidation':
                         {'range': {'sheetId': worksheet.id}, 'rule': None}})
        requests.append({'updateDimensionProperties': {
            'range': {'sheetId': worksheet.id, 'dimension': 'COLUMNS'},
            'properties': {'hiddenByUser': False},
            'fields': 'hiddenByUser',
        }})
    return requests


def _rank_chart_bounds(rows):
    """Locate the rank-by-week chart apparatus the builder emits with
    rank_arc_rows: the '(check to plot)' toggle row and the hidden
    helper block headed 'Week' at column AK. Returns None when absent,
    else the geometry the writer needs to arm the checkboxes, hide the
    helper columns, and add the chart."""
    chk_idx = next((i for i, r in enumerate(rows)
                    if r and r[0] == '(check to plot)'), None)
    if chk_idx is None:
        return None
    n_teams = sum(1 for c in rows[chk_idx][1:] if isinstance(c, bool)) - 1
    # The helper block's column is dynamic (past the widest table); find
    # it from the 'Week' header the builder stamps at its first cell.
    helper_first = helper_col0 = None
    for i, r in enumerate(rows[chk_idx:], start=chk_idx):
        idx = next((j for j, c in enumerate(r)
                    if c == 'Week' and j >= 30), None)
        if idx is not None:
            helper_first, helper_col0 = i, idx
            break
    if helper_first is None or n_teams < 1:
        return None
    end = helper_first + 1
    while (end < len(rows) and len(rows[end]) > helper_col0
           and rows[end][helper_col0] not in ('', None)):
        end += 1
    return {
        'checkbox_row0': chk_idx,
        'n_teams': n_teams,
        'helper_col0': helper_col0,
        'first_row': helper_first,
        'last_row': end,
        'raw_end_col0': helper_col0 + 1 + 2 * n_teams,
        'series_cols': [helper_col0 + 1 + t for t in range(n_teams)],
    }


def _rank_chart_requests(sheet_id, rows, season_year=None):
    """setDataValidation + hide-columns + addChart for the rank-by-week
    apparatus; empty when the tab doesn't carry one."""
    b = _rank_chart_bounds(rows)
    if not b:
        return []
    checkbox_range = {
        'sheetId': sheet_id,
        'startRowIndex': b['checkbox_row0'],
        'endRowIndex': b['checkbox_row0'] + 1,
        'startColumnIndex': 1,
        'endColumnIndex': 2 + b['n_teams'],
    }
    title = (f'{season_year} standings position by week (top = 1st)'
             if season_year else 'Standings position by week (top = 1st)')
    return [
        {'setDataValidation': {
            'range': checkbox_range,
            'rule': {'condition': {'type': 'BOOLEAN'},
                     'strict': True, 'showCustomUi': True},
        }},
        {'updateDimensionProperties': {
            'range': {'sheetId': sheet_id, 'dimension': 'COLUMNS',
                      'startIndex': b['helper_col0'],
                      'endIndex': b['raw_end_col0']},
            'properties': {'hiddenByUser': True},
            'fields': 'hiddenByUser',
        }},
        {'addChart': {'chart': {
            'spec': {
                'title': title,
                'basicChart': {
                    'chartType': 'LINE',
                    'legendPosition': 'RIGHT_LEGEND',
                    'headerCount': 1,
                    'domains': [{'domain': {'sourceRange': {'sources': [{
                        'sheetId': sheet_id,
                        'startRowIndex': b['first_row'],
                        'endRowIndex': b['last_row'],
                        'startColumnIndex': b['helper_col0'],
                        'endColumnIndex': b['helper_col0'] + 1,
                    }]}}}],
                    'series': [{'series': {'sourceRange': {'sources': [{
                        'sheetId': sheet_id,
                        'startRowIndex': b['first_row'],
                        'endRowIndex': b['last_row'],
                        'startColumnIndex': col,
                        'endColumnIndex': col + 1,
                    }]}}, 'targetAxis': 'LEFT_AXIS'}
                        for col in b['series_cols']],
                    'axis': [
                        {'position': 'BOTTOM_AXIS', 'title': 'Week'},
                        {'position': 'LEFT_AXIS',
                         'title': 'Position (top = 1st)',
                         'viewWindowOptions': {
                             'viewWindowMode': 'EXPLICIT',
                             'viewWindowMin': 0,
                             'viewWindowMax': b['n_teams'] + 1}},
                    ],
                },
                'hiddenDimensionStrategy': 'SHOW_ALL',
            },
            'position': {'overlayPosition': {
                'anchorCell': {'sheetId': sheet_id,
                               'rowIndex': b['first_row'],
                               'columnIndex': 0},
                'widthPixels': 940,
                'heightPixels': 360,
            }},
        }}},
    ]


def _acquisition_table_bounds(rows):
    """Locate the acquisition-channel blocks (Active + Rostered lenses) stacked
    below the slot grid. Returns a list of (header_idx, end_idx). The headers
    are indented like Table B (Team in column 1) but carry a 'Keeper' column,
    which is what tells them apart from the slot grid."""
    bounds = []
    for i, r in enumerate(rows):
        if len(r) > 3 and r[1] == 'Team' and 'Keeper' in r:
            end = i + 1
            while (end < len(rows) and len(rows[end]) > 1
                   and rows[end][1] not in ('', None)):
                end += 1
            bounds.append((i, end))
    return bounds


def _apply_standings_gradients(spreadsheet, worksheet, rows, stat_specs):
    """Red->white->green column gradients. Table A: every stat and points
    column, polarity-aware -- positive-weighted stats and the three score
    totals paint green-high, negative-weighted stats (L / ER / BLSV / ...)
    and Against paint green-low, zero-weighted stats get no gradient.
    Column positions come from standings_gradient_columns (positional, not
    label lookup -- K / BB / H / HR / R appear in both stat blocks). The
    slot grids (season + all-time): every lineup-slot column, green-high.
    Affinity matrices: one white->green rule per half-block (0 stays
    white), so a shade means the same share in every team column. The
    batch opens by deleting whatever rules the last render left --
    addConditionalFormatRule stacks; clear() only drops values."""
    sheet_id = worksheet.id
    requests = _stale_conditional_rule_requests(spreadsheet, worksheet, rows)
    # Rank-by-week chart apparatus (checkboxes + hidden helper + chart),
    # when the builder emitted one; rides the same batch as the gradients.
    _title = rows[0][0] if rows and rows[0] else ''
    _season = _title.rsplit(': ', 1)[-1] if ': ' in _title else None
    requests.extend(_rank_chart_requests(sheet_id, rows, _season))
    gradient_columns = almanac_render.standings_gradient_columns(
        _team_week_specs_for_category(stat_specs, 'hitting'),
        _team_week_specs_for_category(stat_specs, 'pitching'),
    )
    for a_hdr, a_end in _standings_table_bounds(rows):
        a_range = [{'startRowIndex': a_hdr + 1, 'endRowIndex': a_end}]
        for col, direction in gradient_columns:
            if direction is None:
                continue
            scale = 'three_good_high' if direction == 'most' else 'three_good_low'
            requests.append(_color_scale_request(
                sheet_id, col, a_end, scale=scale, row_ranges=a_range,
            ))
    # Finishes-beside-the-chart: CBS's auto-scaled finish gradient per
    # year column (green best -> red worst within the year's own field).
    fin = _espn_finishes_bounds(rows)
    if fin:
        for col in range(fin['col0'] + 7, fin['col0'] + fin['n_cols']):
            requests.append({'addConditionalFormatRule': {'rule': {
                'ranges': [{'sheetId': sheet_id,
                            'startRowIndex': fin['hdr'] + 1,
                            'endRowIndex': fin['end'],
                            'startColumnIndex': col,
                            'endColumnIndex': col + 1}],
                'gradientRule': {
                    'minpoint': {'type': 'MIN',
                                 'color': {'red': 0.341, 'green': 0.733,
                                           'blue': 0.541}},
                    'midpoint': {'type': 'PERCENTILE', 'value': '50',
                                 'color': {'red': 1.0, 'green': 0.839,
                                           'blue': 0.4}},
                    'maxpoint': {'type': 'MAX',
                                 'color': {'red': 0.902, 'green': 0.486,
                                           'blue': 0.451}},
                },
            }, 'index': 0}})
    # Acquisition band groups merge (Kyle round 8, the CBS convention);
    # unmerge the sheet first so reruns never re-merge a merged range.
    band_rows = [i for i, r in enumerate(rows)
                 if len(r) > 3 and r[3] == 'Points Acquired Via']
    if band_rows:
        requests.append({'unmergeCells': {'range': {'sheetId': sheet_id}}})
        for i in band_rows:
            for c0, c1 in ((3, 8), (9, 12), (13, 15),
                           (21, 26), (27, 30), (31, 33)):
                requests.append({'mergeCells': {
                    'range': {'sheetId': sheet_id,
                              'startRowIndex': i, 'endRowIndex': i + 1,
                              'startColumnIndex': c0, 'endColumnIndex': c1},
                    'mergeType': 'MERGE_ALL'}})
    # De-italicize the trophy glyph inside the finishes explainer (Kyle
    # round 12: an italic 🏆 'looks quite bad'): a textFormatRuns pass on
    # that one cell -- the emoji is 2 UTF-16 units, so the italic run
    # starts at index 2.
    fin_note = _espn_finishes_bounds(rows)
    if fin_note:
        requests.append({'updateCells': {
            'range': {'sheetId': sheet_id,
                      'startRowIndex': fin_note['note'],
                      'endRowIndex': fin_note['note'] + 1,
                      'startColumnIndex': fin_note['col0'],
                      'endColumnIndex': fin_note['col0'] + 1},
            'rows': [{'values': [{'textFormatRuns': [
                {'startIndex': 0, 'format': {'italic': False}},
                {'startIndex': 2, 'format': {'italic': True}},
            ]}]}],
            'fields': 'textFormatRuns',
        }})
    for b_hdr, b_end in _slot_grid_bounds(rows):
        b_range = [{'startRowIndex': b_hdr + 1, 'endRowIndex': b_end}]
        # Slot values start after the indent + Team + Owner cells.
        for col in range(3, len(rows[b_hdr])):
            requests.append(_color_scale_request(
                sheet_id, col, b_end, scale='three_good_high', row_ranges=b_range,
            ))
    # Acquisition blocks: acquired columns green-high, lost columns green-low,
    # the two Net deltas on a zero-centered diverging scale (polarity-aware).
    _acq_scale = {'most': 'three_good_high', 'fewest': 'three_good_low',
                  'diverging': 'diverging_zero'}
    for hdr, end in _acquisition_table_bounds(rows):
        acq_range = [{'startRowIndex': hdr + 1, 'endRowIndex': end}]
        for col, direction in almanac_render.acquisition_gradient_columns():
            requests.append(_color_scale_request(
                sheet_id, col, end, scale=_acq_scale[direction], row_ranges=acq_range,
            ))
    for aff in _affinity_bounds(rows):
        # Per-BLOCK red -> white -> green (Kyle 2026-07-17 round 4 -- the
        # shared yellow-mid scale was an eyesore): each matrix scales to
        # its own spread, on the same palette as the slot grids. Blanks
        # can't take a gradient -- the static light-gray base laid down
        # by the format pass is what marks a true zero/null.
        for sc in (aff['left0'], aff['right0']):
            if sc is None:
                continue
            requests.append({'addConditionalFormatRule': {
                'rule': {
                    'ranges': [{'sheetId': sheet_id,
                                'startRowIndex': aff['hdr'] + 1,
                                'endRowIndex': aff['end'],
                                'startColumnIndex': sc,
                                'endColumnIndex': sc + aff['n_t']}],
                    'gradientRule': {
                        'minpoint': {'type': 'NUMBER', 'value': '0',
                                     'color': {'red': 0.96, 'green': 0.62,
                                               'blue': 0.60}},
                        'midpoint': {'type': 'PERCENTILE', 'value': '50',
                                     'color': {'red': 1, 'green': 1,
                                               'blue': 1}},
                        'maxpoint': {'type': 'MAX',
                                     'color': {'red': 0.67, 'green': 0.86,
                                               'blue': 0.64}},
                    },
                },
                'index': 0,
            }})
    if requests:
        _sheets_batch_update(
            spreadsheet, f'standings gradients {worksheet.title}', requests,
        )


def _replace_advanced_standings_tab(spreadsheet, rows, stat_specs):
    """Clear / create the Advanced Standings tab and write it. Returns the
    worksheet so write_almanac can read its gid for the Home nav band.

    Written RAW so the W-L record strings ("11-2") aren't parsed as dates; the
    numeric point cells stay numeric, so the column gradients still apply."""
    width = max((len(row) for row in rows), default=20)
    try:
        worksheet = spreadsheet.worksheet(ADVANCED_STANDINGS_TAB)
        # The tab GREW with the rank-chart helper block (hidden cols to
        # ~BM); an existing grid from an earlier render may be too small
        # for the values write.
        if (worksheet.col_count < width
                or worksheet.row_count < len(rows) + 10):
            _sheets_call(
                f'resize {ADVANCED_STANDINGS_TAB}',
                lambda ws=worksheet: ws.resize(
                    rows=max(ws.row_count, len(rows) + 10),
                    cols=max(ws.col_count, width)),
            )
    except gspread.WorksheetNotFound:
        worksheet = _sheets_call(
            f'create {ADVANCED_STANDINGS_TAB}',
            lambda: spreadsheet.add_worksheet(
                title=ADVANCED_STANDINGS_TAB,
                rows=max(len(rows) + 10, 50),
                cols=max(width, 20),
            ),
        )

    _sheets_call(f'clear {ADVANCED_STANDINGS_TAB}', worksheet.clear)
    _sheets_call(
        f'update {ADVANCED_STANDINGS_TAB}',
        lambda: worksheet.update(rows, 'A1', value_input_option='RAW'),
    )
    # The rank-chart helper formulas arrive as literal '=' strings under
    # RAW (which the W-L cells need); re-coerce just those cells.
    _reapply_formula_cells(worksheet, rows)

    try:
        sheet_id = worksheet.id
        last_col = _a1_col(width)
        a_tables = _standings_table_bounds(rows)
        a_hdr = a_tables[0][0] if a_tables else None
        slot_grids = _slot_grid_bounds(rows)
        affinity = _affinity_bounds(rows)
        _apply_standings_gradients(spreadsheet, worksheet, rows, stat_specs)
        # Widths by column TYPE, derived from the Table A header layout
        # rather than hardcoded letters, so other leagues' stat counts get
        # the right shape: identity columns wide, value columns narrow,
        # buffers narrower still. The blanket 40px request lands first;
        # the per-buffer 25px overrides follow (later requests win within
        # one batchUpdate). None of these touch hiddenByUser, so manually
        # hidden columns (e.g. a stat the league never records) stay
        # hidden across reruns.
        width_requests = [
            _column_width_request(sheet_id, 0, 2, 52),      # Rank, Team
            _column_width_request(sheet_id, 2, 3, 125),     # Owner
            _column_width_request(sheet_id, 3, width, 40),  # W-L + every value column
            # Freeze the title + subtitle band (the CBS convention).
            {'updateSheetProperties': {
                'properties': {'sheetId': sheet_id,
                               'gridProperties': {'frozenRowCount': 2}},
                'fields': 'gridProperties.frozenRowCount',
            }},
        ]
        if a_hdr is not None:
            width_requests.extend(
                _column_width_request(sheet_id, col, col + 1, 25)
                for col, cell in enumerate(rows[a_hdr])
                if col >= 4 and cell == ''
            )
        _sheets_batch_update(
            spreadsheet, f'standings widths {worksheet.title}', width_requests,
        )
        # CBS conventions (Kyle 2026-07-18): the NAVY lives on the section
        # bands, table header rows are plain bold, explainer rows italic,
        # the subtitle pale blue -- one visual system across both books.
        formats = [
            {'range': 'A1', 'format': {'textFormat': {'bold': True, 'fontSize': 14}}},
            {'range': f'A2:{last_col}2',
             'format': {'textFormat': {'italic': True},
                        'backgroundColor': {'red': 0.95, 'green': 0.97,
                                            'blue': 0.99}}},
        ]
        navy_fmt = {
            'textFormat': {'bold': True,
                           'foregroundColor': {'red': 1, 'green': 1,
                                               'blue': 1}},
            'backgroundColor': {'red': 0.12, 'green': 0.20, 'blue': 0.30},
        }
        section_titles = {'Detailed Standings (Weekly Averages)',
                          'Detailed Standings (Weekly Averages, All-Time)',
                          'Points by Lineup Slot',
                          'Points by Lineup Slot (Season Totals)',
                          'Production by Acquisition Channel',
                          'Roster Affinity by MLB Team'}
        # Two passes so every navy band runs as far as the WIDEST one
        # (Kyle round 12: unified band width).
        band_specs = []
        for i, row in enumerate(rows):
            title = row[0] if row else None
            if not (title in section_titles
                    or (isinstance(title, str)
                        and title.endswith('Rank by Week'))):
                continue
            # Band width = the widest nearby row that isn't the hidden
            # helper block (helper rows park past column 45).
            w = max((len(r) for r in rows[i:i + 6]
                     if r and len(r) <= 45), default=20)
            band_specs.append((i, w))
            if title in ('Production by Acquisition Channel',
                         'Roster Affinity by MLB Team'):
                # The explainer row directly underneath, CBS-note style.
                formats.append({
                    'range': f'A{i + 2}:{last_col}{i + 2}',
                    'format': {'textFormat': {'italic': True,
                                              'fontSize': 9}},
                })
        if band_specs:
            band_col = _a1_col(max(w for _, w in band_specs))
            for i, _w in band_specs:
                formats.append({'range': f'A{i + 1}:{band_col}{i + 1}',
                                'format': dict(navy_fmt)})
        header_indices = [h for h, _ in a_tables]
        header_indices += [h for h, _ in slot_grids]
        header_indices += [h for h, _ in _acquisition_table_bounds(rows)]
        header_indices += [a['hdr'] for a in affinity]
        for header_idx in header_indices:
            r = header_idx + 1
            formats.append({
                'range': f'A{r}:{last_col}{r}',
                'format': {'textFormat': {'bold': True}},
            })

        # Decimal rule (Kyle round 12): per value column, 0 decimals
        # unless the column's AVERAGE is under 10, then 1 -- so CYC keeps
        # its decimal while HR doesn't wobble between 51.9 and 52.
        def _decimal_rule(hdr, end, first_col):
            for col in range(first_col, len(rows[hdr])):
                vals = [r[col] for r in rows[hdr + 1:end]
                        if len(r) > col and isinstance(r[col], (int, float))]
                if not vals:
                    continue
                pattern = '0.0' if sum(vals) / len(vals) < 10 else '0'
                a1 = _a1_col(col + 1)
                formats.append({
                    'range': f'{a1}{hdr + 2}:{a1}{end}',
                    'format': {'numberFormat': {'type': 'NUMBER',
                                                'pattern': pattern}},
                })

        for hdr, end in a_tables:
            _decimal_rule(hdr, end, 4)
        for hdr, end in slot_grids:
            _decimal_rule(hdr, end, 3)
        # Acquisition totals stay whole-point (all columns are large).
        for hdr, end in _acquisition_table_bounds(rows):
            formats.append({
                'range': f'{_a1_col(4)}{hdr + 2}:'
                         f'{_a1_col(len(rows[hdr]))}{end}',
                'format': {'numberFormat': {'type': 'NUMBER',
                                            'pattern': '0'}},
            })
        # The acquisition group-band rows bold + center over their merges;
        # every indented sub-label row ('<season> to date' / 'All-Time'
        # ...) bolds full-width.
        for i, row in enumerate(rows):
            if len(row) > 3 and row[3] == 'Points Acquired Via':
                formats.append({
                    'range': f'A{i + 1}:{last_col}{i + 1}',
                    'format': {'textFormat': {'bold': True},
                               'horizontalAlignment': 'CENTER'},
                })
            elif (len(row) > 3 and row[0] == '' and row[1] == ''
                    and row[2] == '' and isinstance(row[3], str) and row[3]):
                formats.append({
                    'range': f'A{i + 1}:{last_col}{i + 1}',
                    'format': {'textFormat': {'bold': True}},
                })
        # Finishes-beside-the-chart dressing (top-hugging since round 12:
        # explainer row 1, header on the frozen subtitle row): italic
        # note (the trophy glyph de-italicizes via a runs pass in the
        # gradients batch), bold header, centered values, W% / Avg number
        # formats, and the champion trophies' static green fill.
        fin = _espn_finishes_bounds(rows)
        if fin:
            f0 = fin['col0']
            first_col = _a1_col(f0 + 1)
            last_fin_col = _a1_col(f0 + fin['n_cols'])
            note_r = fin['note'] + 1
            hdr_r = fin['hdr'] + 1
            end_r = fin['end']
            formats.append({
                'range': f'{first_col}{note_r}:{last_fin_col}{note_r}',
                'format': {'textFormat': {'italic': True, 'fontSize': 9}},
            })
            formats.append({
                'range': f'{first_col}{hdr_r}:{last_fin_col}{hdr_r}',
                'format': {'textFormat': {'bold': True}},
            })
            formats.append({
                'range': f'{_a1_col(f0 + 5)}{hdr_r}:{last_fin_col}{end_r}',
                'format': {'horizontalAlignment': 'CENTER'},
            })
            formats.append({
                'range': f'{_a1_col(f0 + 6)}{hdr_r + 1}:'
                         f'{_a1_col(f0 + 6)}{end_r}',
                'format': {'numberFormat':
                           {'type': 'PERCENT', 'pattern': '0.0%'}},
            })
            formats.append({
                'range': f'{_a1_col(f0 + 7)}{hdr_r + 1}:'
                         f'{_a1_col(f0 + 7)}{end_r}',
                'format': {'numberFormat':
                           {'type': 'NUMBER', 'pattern': '0.0'}},
            })
            for i in range(fin['hdr'] + 1, fin['end']):
                for j, cell in enumerate(rows[i]):
                    if isinstance(cell, str) and cell.startswith('🏆'):
                        a1 = f'{_a1_col(j + 1)}{i + 1}'
                        formats.append({
                            'range': f'{a1}:{a1}',
                            'format': {'backgroundColor':
                                       {'red': 0.341, 'green': 0.733,
                                        'blue': 0.541},
                                       'textFormat': {'bold': True}},
                        })
        # The affinity sub-label row ('<season> to date' / 'All-Time') sits
        # directly above its header with labels mid-row; bold it full-width
        # (the "row above" pass only bolds column A).
        for aff in affinity:
            formats.append({
                'range': f'A{aff["hdr"]}:{last_col}{aff["hdr"]}',
                'format': {'textFormat': {'bold': True}},
            })
        # Affinity half-blocks (CBS conventions + the round-12 geometry):
        # light-gray base for true zero/null cells (the gradient only
        # paints numeric cells over it), whole-percent centered display,
        # RIGHT-aligned abbrev headers, and a bold on each MLB club's
        # biggest devotee per block (ties all bold).
        for aff in affinity:
            hdr, end, n_t = aff['hdr'], aff['end'], aff['n_t']
            for start_col in (aff['left0'], aff['right0']):
                if start_col is None:
                    continue
                first_a1 = _a1_col(start_col + 1)
                last_a1 = _a1_col(start_col + n_t)
                formats.append({
                    'range': f'{first_a1}{hdr + 1}:{last_a1}{hdr + 1}',
                    'format': {'horizontalAlignment': 'RIGHT'},
                })
                formats.append({
                    'range': f'{first_a1}{hdr + 2}:{last_a1}{end}',
                    'format': {
                        'backgroundColor': {'red': 0.937, 'green': 0.937,
                                            'blue': 0.937},
                        'horizontalAlignment': 'CENTER',
                        'numberFormat': {'type': 'PERCENT',
                                         'pattern': '0%'},
                    }})
            for r_idx in range(hdr + 1, end):
                for start_col in (aff['left0'], aff['right0']):
                    if start_col is None:
                        continue
                    vals = rows[r_idx][start_col:start_col + n_t]
                    numeric = [v for v in vals
                               if isinstance(v, (int, float))]
                    if not numeric:
                        continue
                    peak = max(numeric)
                    for k, v in enumerate(vals):
                        if isinstance(v, (int, float)) and v == peak:
                            cell = f'{_a1_col(start_col + k + 1)}{r_idx + 1}'
                            formats.append({
                                'range': f'{cell}:{cell}',
                                'format': {'textFormat': {'bold': True}},
                            })
        _batch_format(worksheet, formats)
    except Exception as exc:
        print(f"[almanac] standings formatting skipped: {exc}")

    return worksheet


def _replace_team_weeks_tab(spreadsheet, rows, stat_specs, source_rows=None, record_marks=None):
    """Recreate and write the team-week matchup archive tab."""
    try:
        worksheet = spreadsheet.worksheet(TEAM_WEEKS_TAB)
        _sheets_call(
            f'delete {TEAM_WEEKS_TAB}',
            lambda: spreadsheet.del_worksheet(worksheet),
        )
    except gspread.WorksheetNotFound:
        pass

    worksheet = _sheets_call(
        f'create {TEAM_WEEKS_TAB}',
        lambda: spreadsheet.add_worksheet(
            title=TEAM_WEEKS_TAB,
            rows=max(len(rows) + 10, 50),
            cols=max(len(rows[0]) if rows else 1, 20),
        ),
    )
    _sheets_call(
        f'update {TEAM_WEEKS_TAB}',
        lambda: worksheet.update(rows, 'A1', value_input_option='USER_ENTERED'),
    )

    try:
        _sheets_call(f'freeze {TEAM_WEEKS_TAB}', lambda: worksheet.freeze(rows=1))
        _apply_team_weeks_tab_dimensions(spreadsheet, worksheet, rows, stat_specs)
        _apply_team_weeks_conditional_formats(
            spreadsheet,
            worksheet,
            rows,
            stat_specs,
            source_rows=source_rows,
        )
        _apply_team_weeks_record_formats(
            spreadsheet,
            worksheet,
            stat_specs,
            source_rows=source_rows,
            record_marks=record_marks,
        )
        layout = _team_weeks_layout(stat_specs)
        score_start_col = _a1_col(layout['score_start'] + 1)
        score_end_col = _a1_col(layout['score_start'] + 4)
        matchup_start_col = _a1_col(layout['score_start'] + 8)
        matchup_end_col = _a1_col(layout['score_start'] + 10)
        avg_start_col = _a1_col(layout['score_start'] + 12)
        avg_end_col = _a1_col(layout['score_start'] + 14)
        _batch_format(worksheet, [
            {
                'range': f'A1:{_a1_col(len(rows[0]))}1',
                'format': {
                    'textFormat': {
                        'bold': True,
                        'foregroundColor': {'red': 1, 'green': 1, 'blue': 1},
                    },
                    'backgroundColor': {'red': 0.12, 'green': 0.20, 'blue': 0.30},
                },
            },
            {
                'range': f'A:{_a1_col(len(rows[0]))}',
                'format': {'wrapStrategy': 'OVERFLOW_CELL'},
            },
            {
                'range': f'{score_start_col}:{score_end_col}',
                'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0.0'}},
            },
            {
                'range': f'{matchup_start_col}:{matchup_end_col}',
                'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0.0'}},
            },
            {
                'range': f'{avg_start_col}:{avg_end_col}',
                'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0.0'}},
            },
        ])
    except Exception as exc:
        print(f"[almanac] formatting skipped for {TEAM_WEEKS_TAB}: {exc}")

    return worksheet


def _replace_records_tab(spreadsheet, rows):
    """Clear/create Records and write the curated record-book tab."""
    try:
        worksheet = spreadsheet.worksheet(RECORDS_TAB)
    except gspread.WorksheetNotFound:
        worksheet = _sheets_call(
            f'create {RECORDS_TAB}',
            lambda: spreadsheet.add_worksheet(
                title=RECORDS_TAB,
                rows=max(len(rows) + 10, 50),
                cols=RECORDS_MATRIX_WIDTH,
            ),
        )

    _sheets_call(f'clear {RECORDS_TAB}', worksheet.clear)
    _sheets_call(
        f'update {RECORDS_TAB}',
        lambda: worksheet.update(rows, 'A1', value_input_option='USER_ENTERED'),
    )

    try:
        _sheets_call(f'freeze {RECORDS_TAB}', lambda: worksheet.freeze(rows=3))
        _apply_records_tab_dimensions(spreadsheet, worksheet)
        _merge_records_scope_headers(spreadsheet, worksheet, rows)
        formats = [
            {
                'range': 'A:L',
                'format': {
                    'textFormat': {'bold': False, 'italic': False},
                    'backgroundColor': {'red': 1, 'green': 1, 'blue': 1},
                    'wrapStrategy': 'OVERFLOW_CELL',
                },
            },
            {
                'range': 'A1:L1',
                'format': {'textFormat': {'bold': True, 'fontSize': 14}},
            },
            {
                'range': 'A2:L2',
                'format': {
                    'textFormat': {'italic': True},
                    'backgroundColor': {'red': 0.95, 'green': 0.97, 'blue': 0.99},
                },
            },
            {
                # Row 3: formatting legend (italic + small, matching the note row).
                'range': 'A3:L3',
                'format': {
                    'textFormat': {'italic': True, 'fontSize': 9},
                    'backgroundColor': {'red': 0.95, 'green': 0.97, 'blue': 0.99},
                },
            },
        ]
        formats.extend(_records_header_formats(rows))
        formats.extend(_fresh_record_formats(rows))
        formats.extend(_records_score_value_formats(rows))
        _batch_format(worksheet, formats)
    except Exception as exc:
        print(f"[almanac] formatting skipped for {RECORDS_TAB}: {exc}")

    return worksheet


def _records_header_formats(rows):
    """Return batched formats for section labels and matrix headers."""
    formats = []
    for row_number, row in enumerate(rows, 1):
        if _is_records_scope_header(row) or row == RECORDS_MATRIX_DETAIL_HEADER:
            formats.append({
                'range': f'A{row_number}:L{row_number}',
                'format': {
                    'textFormat': {'bold': True},
                    'backgroundColor': {'red': 0.95, 'green': 0.97, 'blue': 0.99},
                },
            })
    return formats


def _is_records_scope_header(row):
    return (
        len(row) == RECORDS_MATRIX_WIDTH
        and row[1] == 'Current Season'
        and row[7] == 'All-Time'
    )


# Burnt orange / gold background for a brand-new ALL-TIME record set in the
# MOST RECENT week -- the strongest "you just made league history this week"
# cue, layered over the italic this-season marker. Tunable.
_FRESH_ALL_TIME_RECORD_BG = {'red': 0.92, 'green': 0.60, 'blue': 0.20}


def _fresh_record_formats(rows):
    """Return batched formats for fresh current-season/all-time records."""
    formats = []
    season_year, matchup_period = get_latest_matchup_period()
    schedule_lookup = records.load_schedule_lookup()
    latest_period = records.format_week_label(season_year, matchup_period, schedule_lookup)

    for row_number, row in enumerate(rows, 1):
        if len(row) < RECORDS_MATRIX_WIDTH:
            continue
        current_period = row[4]
        all_time_period = row[10]
        if (
            isinstance(current_period, str)
            and latest_period in current_period
            and _record_side_is_small_tie(row[1])
        ):
            formats.append({
                'range': f'A{row_number}:F{row_number}',
                'format': {'textFormat': {'italic': True}},
            })
        if (
            isinstance(all_time_period, str)
            and f': {season_year}' in all_time_period
            and _record_side_is_small_tie(row[7])
        ):
            formats.append({
                'range': f'H{row_number}:L{row_number}',
                'format': {'textFormat': {'italic': True}},
            })
        # v1.3: a brand-new all-time record set in the MOST RECENT week (not
        # just somewhere this season) gets a burnt-orange/gold background --
        # the "you just set a league all-time record this week" highlight.
        # latest_period already carries the ": {season}" suffix via the cell,
        # so match the full "<week>: <season>" to avoid week-substring hits
        # (e.g. "Week 1" inside "Week 11").
        if (
            isinstance(all_time_period, str)
            and f'{latest_period}: {season_year}' in all_time_period
            and _record_side_is_small_tie(row[7])
        ):
            formats.append({
                'range': f'H{row_number}:L{row_number}',
                'format': {'backgroundColor': _FRESH_ALL_TIME_RECORD_BG},
            })
            # A fresh all-time record IS also the current-season record, so it
            # appears on the left panel too -- mirror the highlight onto A:F.
            # (Its italic already fires via the current-season branch above.)
            if _record_side_is_small_tie(row[1]):
                formats.append({
                    'range': f'A{row_number}:F{row_number}',
                    'format': {'backgroundColor': _FRESH_ALL_TIME_RECORD_BG},
                })
    return formats


def _record_side_is_small_tie(holder):
    """Return true when a side is a single holder or a listed tie of 3 or fewer."""
    holder_text = str(holder or '').strip()
    if not holder_text:
        return False
    match = re.match(r'^(\d+)\s+\w+\s+tied$', holder_text)
    if match:
        return int(match.group(1)) < 4
    return True


def _records_score_value_formats(rows):
    """Force score/points values to one decimal without affecting count stats."""
    formats = []
    active_section = ''
    for row_number, row in enumerate(rows, 1):
        if _is_records_scope_header(row):
            active_section = row[0]
            continue
        if len(row) < RECORDS_MATRIX_WIDTH:
            continue
        if active_section == 'Score Records' or active_section == 'Lineup Slot Records':
            formats.extend([
                {
                    'range': f'D{row_number}:D{row_number}',
                    'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0.0'}},
                },
                {
                    'range': f'J{row_number}:J{row_number}',
                    'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0.0'}},
                },
            ])
    return formats


def _title_abbrev_run_request(sheet_id, title_text):
    """Style the trailing ' (ABBREV)' of a team-page A1 title as a size-10,
    non-bold run -- a small parenthetical beside the bold team name (which
    inherits the cell's bold/large default). Returns an updateCells request,
    or None when the title carries no ' (...)' suffix. Applied AFTER the base
    A1 format so the run wins on that cell."""
    if not title_text or not title_text.endswith(')'):
        return None
    split = title_text.rfind(' (')
    if split <= 0:
        return None
    return {
        'updateCells': {
            'range': {
                'sheetId': sheet_id,
                'startRowIndex': 0, 'endRowIndex': 1,
                'startColumnIndex': 0, 'endColumnIndex': 1,
            },
            'rows': [{'values': [{'textFormatRuns': [
                {'startIndex': 0, 'format': {'bold': True}},
                {'startIndex': split, 'format': {'bold': False, 'fontSize': 10}},
            ]}]}],
            'fields': 'textFormatRuns',
        },
    }


def _replace_team_tab(spreadsheet, title, rows):
    """Clear/create one fantasy team roster tab and write rows."""
    # Width is dynamic: the all-time side gains a trailing Years-of-Service
    # column for leagues with history (30 cols vs the base 29).
    width = max((len(r) for r in rows if r), default=TEAM_ROSTER_MATRIX_WIDTH)
    try:
        worksheet = spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        worksheet = _sheets_call(
            f'create {title}',
            lambda: spreadsheet.add_worksheet(
                title=title,
                rows=max(len(rows) + 10, 50),
                cols=max(width, TEAM_ROSTER_MATRIX_WIDTH),
            ),
        )

    _sheets_call(f'clear {title}', worksheet.clear)
    _sheets_call(
        f'update {title}',
        lambda: worksheet.update(rows, 'A1', value_input_option='RAW'),
    )
    _reapply_formula_cells(worksheet, rows)

    try:
        _sheets_call(f'freeze {title}', lambda: worksheet.freeze(rows=5))
        _apply_team_tab_dimensions(spreadsheet, worksheet, width)
        # The entire format spec is shared with the CBS writer via
        # almanac_render.team_tab_format_specs (Kyle 2026-07-16: identical
        # team tabs across leagues, one format source so they can't drift).
        _batch_format(worksheet, team_tab_format_specs(rows))
        # Header merges (Roster Days pairs + the Points banners) plus the
        # dynamic Best Individual Seasons banner rows. Unmerge first:
        # re-merging an already-merged range errors on rerun. ESPN has no
        # Lineup Data block (that's the CBS provenance story).
        banner_ranges = [
            gspread.utils.a1_range_to_grid_range(rng, sheet_id=worksheet.id)
            for rng in team_tab_banner_merges(rows)
        ]
        _sheets_batch_update(spreadsheet, f'merges {title}', [
            {'unmergeCells': {'range': {'sheetId': worksheet.id}}},
            *({'mergeCells': {'range': {'sheetId': worksheet.id, **rng},
                              'mergeType': 'MERGE_ALL'}}
              for rng in team_tab_merge_ranges()),
            *({'mergeCells': {'range': rng, 'mergeType': 'MERGE_ALL'}}
              for rng in banner_ranges),
        ])
        run_req = _title_abbrev_run_request(
            worksheet.id, rows[0][0] if rows and rows[0] else '')
        if run_req:
            _sheets_batch_update(spreadsheet, f'title run {title}', [run_req])
    except Exception as exc:
        print(f"[almanac] formatting skipped for {title}: {exc}")

    return worksheet


def _apply_records_tab_dimensions(spreadsheet, worksheet):
    sheet_id = worksheet.id
    requests = [
        _column_width_request(sheet_id, 0, 1, 175),
        _column_width_request(sheet_id, 1, 2, 150),   # B: Holder
        _column_width_request(sheet_id, 2, 3, 125),   # C: Owner
        _column_width_request(sheet_id, 5, 6, 400),
        _column_width_request(sheet_id, 6, 7, 25),     # G: buffer between panels
        _column_width_request(sheet_id, 11, 12, 400),
    ]
    _sheets_batch_update(spreadsheet, f'format dimensions {worksheet.title}', requests)


def _apply_team_weeks_tab_dimensions(spreadsheet, worksheet, rows, stat_specs):
    sheet_id = worksheet.id
    layout = _team_weeks_layout(stat_specs)
    total_cols = len(rows[0]) if rows else 1
    requests = [
        _column_width_request(sheet_id, 0, 1, 70),
        _hidden_columns_request(sheet_id, 0, 1, hidden=True),
        _column_width_request(sheet_id, 1, 2, 70),
        _column_width_request(sheet_id, 2, 3, 90),
        _column_width_request(sheet_id, 3, 4, 175),
        _column_width_request(sheet_id, layout['hitting_start'], layout['hitting_end'], 42),
        _column_width_request(sheet_id, layout['hitting_spacer'], layout['hitting_spacer'] + 1, 12),
        _column_width_request(sheet_id, layout['pitching_start'], layout['pitching_end'], 42),
        _column_width_request(sheet_id, layout['pitching_spacer'], layout['pitching_spacer'] + 1, 12),
        _column_width_request(sheet_id, layout['score_start'], layout['score_start'] + 4, 85),
        _column_width_request(sheet_id, layout['score_start'] + 4, layout['score_start'] + 6, 38),
        _column_width_request(sheet_id, layout['score_start'] + 6, layout['score_start'] + 7, 32),
        _column_width_request(sheet_id, layout['score_start'] + 7, layout['score_start'] + 10, 95),
        _column_width_request(sheet_id, layout['score_start'] + 10, layout['score_start'] + 11, 32),
        _column_width_request(sheet_id, layout['score_start'] + 11, total_cols, 90),
    ]
    for column in _team_weeks_rare_column_indices(stat_specs):
        requests.append(_hidden_columns_request(sheet_id, column, column + 1, hidden=True))
    _sheets_batch_update(spreadsheet, f'format dimensions {worksheet.title}', requests)


def _apply_team_weeks_conditional_formats(spreadsheet, worksheet, rows, stat_specs,
                                          source_rows=None):
    if len(rows) <= 1:
        return
    sheet_id = worksheet.id
    layout = _team_weeks_layout(stat_specs)
    row_ranges = _team_weeks_standard_data_ranges(sheet_id, len(rows), source_rows)
    requests = []

    for column, spec in _team_weeks_stat_column_pairs(stat_specs):
        stat_name = spec.get('stat_name')
        if stat_name in TEAM_WEEKS_WHITE_TO_GREEN_STATS:
            requests.append(_color_scale_request(
                sheet_id, column, len(rows), scale='two_good', row_ranges=row_ranges,
            ))
        elif stat_name in TEAM_WEEKS_WHITE_TO_RED_STATS:
            requests.append(_color_scale_request(
                sheet_id, column, len(rows), scale='two_bad', row_ranges=row_ranges,
            ))
        else:
            scale = 'three_good_high'
            if (spec.get('points_per_unit') or 0) < 0:
                scale = 'three_good_low'
            requests.append(_color_scale_request(
                sheet_id, column, len(rows), scale=scale, row_ranges=row_ranges,
            ))

    for column in [
        layout['score_start'],
        layout['score_start'] + 1,
        layout['score_start'] + 2,
        layout['score_start'] + 3,
        layout['score_start'] + 7,
        layout['score_start'] + 8,
        layout['score_start'] + 9,
        layout['score_start'] + 11,
        layout['score_start'] + 12,
        layout['score_start'] + 13,
    ]:
        requests.append(_color_scale_request(
            sheet_id, column, len(rows), scale='three_good_high', row_ranges=row_ranges,
        ))

    _sheets_batch_update(spreadsheet, f'conditional formats {worksheet.title}', requests)


def _apply_team_weeks_record_formats(spreadsheet, worksheet, stat_specs,
                                     source_rows=None, record_marks=None):
    if not source_rows or not record_marks:
        return
    sheet_id = worksheet.id
    requests = []
    for column_index, spec in _team_weeks_stat_column_pairs(stat_specs):
        mark = record_marks.get(spec.get('stat_name'))
        if not mark or _is_zeroish(mark.get('value')):
            continue
        for source_index, source_row in enumerate(source_rows):
            if source_row.get('is_abnormal'):
                continue
            if not _numeric_values_equal(source_row.get(_fact_stat_column_name(spec.get('stat_name'))), mark.get('value')):
                continue
            text_format = {'bold': True}
            if int(mark.get('holder_count') or 0) == 1:
                text_format['foregroundColor'] = {'red': 0.72, 'green': 0.48, 'blue': 0.00}
            requests.append(_cell_format_request(
                sheet_id,
                row_index=source_index + 1,
                column_index=column_index,
                cell_format={'textFormat': text_format},
            ))
    _sheets_batch_update(spreadsheet, f'record formats {worksheet.title}', requests)


def _team_weeks_standard_data_ranges(sheet_id, row_count, source_rows=None):
    """Return contiguous non-abnormal data-row ranges for conditional formatting."""
    if not source_rows:
        return [{'sheetId': sheet_id, 'startRowIndex': 1, 'endRowIndex': row_count}]

    ranges = []
    start = None
    for source_index, source_row in enumerate(source_rows):
        row_index = source_index + 1
        is_standard = not source_row.get('is_abnormal')
        if is_standard and start is None:
            start = row_index
        elif not is_standard and start is not None:
            ranges.append({'sheetId': sheet_id, 'startRowIndex': start, 'endRowIndex': row_index})
            start = None
    if start is not None:
        ranges.append({
            'sheetId': sheet_id,
            'startRowIndex': start,
            'endRowIndex': min(len(source_rows) + 1, row_count),
        })
    return ranges


def _numeric_values_equal(left, right):
    if left is None or right is None:
        return False
    try:
        return math.isclose(float(left), float(right), rel_tol=0, abs_tol=0.000001)
    except (TypeError, ValueError):
        return str(left) == str(right)


def _is_zeroish(value):
    if value is None:
        return False
    try:
        return math.isclose(float(value), 0.0, rel_tol=0, abs_tol=0.000001)
    except (TypeError, ValueError):
        return False


def _team_weeks_layout(stat_specs):
    hitting_count = len(_team_week_specs_for_category(stat_specs, 'hitting'))
    pitching_count = len(_team_week_specs_for_category(stat_specs, 'pitching'))
    hitting_start = len(TEAM_WEEKS_BASE_HEADER)
    hitting_end = hitting_start + hitting_count
    hitting_spacer = hitting_end
    pitching_start = hitting_spacer + 1
    pitching_end = pitching_start + pitching_count
    pitching_spacer = pitching_end
    score_start = pitching_spacer + 1
    return {
        'hitting_start': hitting_start,
        'hitting_end': hitting_end,
        'hitting_spacer': hitting_spacer,
        'pitching_start': pitching_start,
        'pitching_end': pitching_end,
        'pitching_spacer': pitching_spacer,
        'score_start': score_start,
    }


def _team_weeks_stat_column_pairs(stat_specs):
    layout = _team_weeks_layout(stat_specs)
    pairs = []
    hitting_specs = _team_week_specs_for_category(stat_specs, 'hitting')
    pitching_specs = _team_week_specs_for_category(stat_specs, 'pitching')
    pairs.extend((layout['hitting_start'] + index, spec) for index, spec in enumerate(hitting_specs))
    pairs.extend((layout['pitching_start'] + index, spec) for index, spec in enumerate(pitching_specs))
    return pairs


def _team_weeks_rare_column_indices(stat_specs):
    return [
        column
        for column, spec in _team_weeks_stat_column_pairs(stat_specs)
        if _is_rare_team_week_stat(spec.get('stat_name'))
    ]


def _merge_records_scope_headers(spreadsheet, worksheet, rows):
    sheet_id = worksheet.id
    requests = []
    for row_index, row in enumerate(rows):
        if not _is_records_scope_header(row):
            continue
        for start_col, end_col in [(1, 6), (7, 12)]:
            grid_range = {
                'sheetId': sheet_id,
                'startRowIndex': row_index,
                'endRowIndex': row_index + 1,
                'startColumnIndex': start_col,
                'endColumnIndex': end_col,
            }
            requests.append({'unmergeCells': {'range': grid_range}})
            requests.append({'mergeCells': {'range': grid_range, 'mergeType': 'MERGE_ALL'}})
    _sheets_batch_update(spreadsheet, f'merge scope headers {worksheet.title}', requests)


def _delete_prefixed_team_tabs(spreadsheet, current_titles):
    """Remove obsolete team tabs from the old T## naming scheme."""
    for worksheet in spreadsheet.worksheets():
        title = worksheet.title
        if title in current_titles:
            continue
        if re.match(r'^T\d{2}\s+', title):
            _sheets_call(
                f'delete old team tab {title}',
                lambda worksheet=worksheet: spreadsheet.del_worksheet(worksheet),
            )


def _sort_almanac_tabs(spreadsheet, ordered_titles):
    worksheets_by_title = {worksheet.title: worksheet for worksheet in spreadsheet.worksheets()}
    requests = []
    for index, title in enumerate(ordered_titles):
        worksheet = worksheets_by_title.get(title)
        if not worksheet:
            continue
        requests.append({
            'updateSheetProperties': {
                'properties': {
                    'sheetId': worksheet.id,
                    'index': index,
                },
                'fields': 'index',
            },
        })
    _sheets_batch_update(spreadsheet, 'sort almanac tabs', requests)


def _apply_team_tab_dimensions(spreadsheet, worksheet, matrix_width=TEAM_ROSTER_MATRIX_WIDTH):
    sheet_id = worksheet.id
    requests = []
    for start_index, width in [
        (0, 25),    # Tm
        (1, 75),    # Slot
        (3, 40),    # Team
        (4, 50),    # Roster Days (merged E4:E5)
        (5, 50),    # Games
        (6, 50),    # Total
        (7, 50),    # Active
        (8, 55),    # Inactive
        (9, 40),    # ppg
        (15, 15),   # spacer
        (16, 25),   # Tm
        (17, 75),   # Slot
        (19, 40),   # Team
        (20, 50),   # Roster Days (merged U4:U5)
        (21, 50),   # Games
        (22, 50),   # Total
        (23, 50),   # Active
        (24, 55),   # Inactive
        (25, 40),   # ppg
    ]:
        requests.append(_column_width_request(sheet_id, start_index, start_index + 1, width))
    requests.extend([
        _column_width_request(sheet_id, 10, 15, 80),
        _column_width_request(sheet_id, 26, 31, 80),
        _auto_resize_columns_request(sheet_id, 2, 3),
        _auto_resize_columns_request(sheet_id, 18, 19),
    ])
    # Trailing Years-of-Service column (idx 31) when the all-time side has
    # it. Full 325px -- it's the last column, the come-and-go year ranges
    # were truncating, and there's nothing to its right (Kyle 2026-07-17).
    if matrix_width > TEAM_ROSTER_MATRIX_WIDTH:
        requests.append(_column_width_request(sheet_id, 31, 32, 325))
    _sheets_batch_update(spreadsheet, f'format dimensions {worksheet.title}', requests)


def _column_width_request(sheet_id, start_index, end_index, pixel_size):
    return {
        'updateDimensionProperties': {
            'range': {
                'sheetId': sheet_id,
                'dimension': 'COLUMNS',
                'startIndex': start_index,
                'endIndex': end_index,
            },
            'properties': {'pixelSize': pixel_size},
            'fields': 'pixelSize',
        },
    }


def _hidden_columns_request(sheet_id, start_index, end_index, hidden=True):
    return {
        'updateDimensionProperties': {
            'range': {
                'sheetId': sheet_id,
                'dimension': 'COLUMNS',
                'startIndex': start_index,
                'endIndex': end_index,
            },
            'properties': {'hiddenByUser': hidden},
            'fields': 'hiddenByUser',
        },
    }


def _color_scale_request(sheet_id, column_index, row_count, scale='three_good_high',
                         row_ranges=None):
    if scale in {'two_good', 'two_bad'}:
        max_color = {'red': 0.67, 'green': 0.86, 'blue': 0.64}
        if scale == 'two_bad':
            max_color = {'red': 0.96, 'green': 0.62, 'blue': 0.60}
        gradient_rule = {
            'minpoint': {
                'type': 'NUMBER',
                'value': '0',
                'color': {'red': 1, 'green': 1, 'blue': 1},
            },
            'maxpoint': {
                'type': 'MAX',
                'color': max_color,
            },
        }
    elif scale == 'diverging_zero':
        # Zero-centered: red for the most negative, white at exactly 0, green
        # for the most positive. For polarity-aware delta columns (FA / Trade
        # Net) where the sign is the story, not the rank.
        gradient_rule = {
            'minpoint': {'type': 'MIN', 'color': {'red': 0.96, 'green': 0.62, 'blue': 0.60}},
            'midpoint': {'type': 'NUMBER', 'value': '0',
                         'color': {'red': 1, 'green': 1, 'blue': 1}},
            'maxpoint': {'type': 'MAX', 'color': {'red': 0.67, 'green': 0.86, 'blue': 0.64}},
        }
    else:
        low_color = {'red': 0.96, 'green': 0.62, 'blue': 0.60}
        high_color = {'red': 0.67, 'green': 0.86, 'blue': 0.64}
        if scale == 'three_good_low':
            low_color, high_color = high_color, low_color
        gradient_rule = {
            'minpoint': {'type': 'MIN', 'color': low_color},
            'midpoint': {
                'type': 'PERCENTILE',
                'value': '50',
                'color': {'red': 1, 'green': 1, 'blue': 1},
            },
            'maxpoint': {'type': 'MAX', 'color': high_color},
        }

    return {
        'addConditionalFormatRule': {
            'rule': {
                'ranges': _color_scale_ranges(
                    sheet_id,
                    column_index,
                    row_count,
                    row_ranges=row_ranges,
                ),
                'gradientRule': gradient_rule,
            },
            'index': 0,
        },
    }


def _color_scale_ranges(sheet_id, column_index, row_count, row_ranges=None):
    ranges = row_ranges or [{'startRowIndex': 1, 'endRowIndex': row_count}]
    return [
        {
            'sheetId': sheet_id,
            'startRowIndex': row_range['startRowIndex'],
            'endRowIndex': row_range['endRowIndex'],
            'startColumnIndex': column_index,
            'endColumnIndex': column_index + 1,
        }
        for row_range in ranges
        if row_range['startRowIndex'] < row_range['endRowIndex']
    ]


def _cell_format_request(sheet_id, row_index, column_index, cell_format):
    return {
        'repeatCell': {
            'range': {
                'sheetId': sheet_id,
                'startRowIndex': row_index,
                'endRowIndex': row_index + 1,
                'startColumnIndex': column_index,
                'endColumnIndex': column_index + 1,
            },
            'cell': {'userEnteredFormat': cell_format},
            'fields': 'userEnteredFormat.textFormat',
        },
    }


def _auto_resize_columns_request(sheet_id, start_index, end_index):
    return {
        'autoResizeDimensions': {
            'dimensions': {
                'sheetId': sheet_id,
                'dimension': 'COLUMNS',
                'startIndex': start_index,
                'endIndex': end_index,
            },
        },
    }


def _a1_col(index_1_based):
    letters = ''
    index = index_1_based
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _batch_format(worksheet, formats):
    if not formats:
        return
    _sheets_call(
        f'format {worksheet.title}',
        lambda: worksheet.batch_format(formats),
    )


def _sheets_batch_update(spreadsheet, label, requests):
    if not requests:
        return
    _sheets_call(label, lambda: spreadsheet.batch_update({'requests': requests}))


def _sheets_call(label, func, attempts=3, delay_seconds=70):
    """Run a Sheets mutation, backing off when the API write quota resets."""
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except gspread.exceptions.APIError as exc:
            if attempt == attempts or not _is_quota_error(exc):
                raise
            print(
                f"[almanac] Sheets quota hit during {label}; "
                f"retrying in {delay_seconds}s"
            )
            time.sleep(delay_seconds)


def _is_quota_error(exc):
    message = str(exc).lower()
    return '[429]' in message or 'quota exceeded' in message or 'rate limit' in message
